"""Wired cross-language parity: Python core vs the live Rust `selekyt-mech` sidecar.

Spawns the compiled Rust sidecar, speaks its JSON-RPC protocol over stdio, and
asserts the Python intelligence core agrees with the Rust mechanical core on the
contract pinned in `docs/autocut-parity.md`:

  - the 512-sample @16kHz (32ms) scoring cadence and VAD provenance fields
    (`vad_backend`, `vad_model`) on a real audio file
  - speech segmentation for *identical* score arrays (hysteresis, strict
    `< min_silence_ms` merge, `>= min_speech_ms` keep)
  - CutList inversion (pad / clamp / merge / tile) for identical segments

The two energy scorers intentionally use different RMS->probability curves
(see "Intentional deviations" in the parity doc), so parity is asserted on the
cadence and on segmentation given identical scores — never on raw score values.

Skips cleanly when the sidecar binary is absent, so the Python suite still runs
on a host without the Rust toolchain. Set ``SELEKYT_MECH`` to point at a binary
explicitly, and ``SELEKYT_PARITY_MEDIA`` to cross-check a real media file.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import wave
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from engine.cut_algebra import cutlist_from_intervals
from engine.vad import EnergyVAD, segment_with_meta

_REPO = Path(__file__).resolve().parent.parent
_CHUNK = 512  # Silero V5 fixed window @16kHz == 32ms (rust core: CHUNK_SIZE)
_SR = 16000
_FRAME = 0.032

def _mech_binary() -> Path | None:
    """Locate the compiled sidecar: env override, then release, then debug."""
    env = os.environ.get("SELEKYT_MECH")
    if env and Path(env).is_file():
        return Path(env)
    for cand in (
        _REPO / "rust" / "target" / "release" / "selekyt-mech",
        _REPO / "rust" / "target" / "debug" / "selekyt-mech",
    ):
        if cand.is_file():
            return cand
    return None


skip_no_mech = pytest.mark.skipif(
    _mech_binary() is None,
    reason="selekyt-mech not built (run `cargo build --release` in rust/)",
)


class MechError(RuntimeError):
    """The sidecar returned an error envelope."""


class Mech:
    """JSON-RPC client over the sidecar's stdio pipe."""

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    def call(self, op: str, **params) -> dict | list:
        if self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("sidecar pipe is not open")
        self._proc.stdin.write(json.dumps({"op": op, "params": params}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError(f"sidecar closed the pipe after op '{op}'")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise MechError(f"{op}: {resp.get('error')}")
        return resp["data"]

    def close(self) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()


@contextmanager
def mech():
    binary = _mech_binary()
    if binary is None:
        pytest.skip("selekyt-mech not built (run `cargo build --release` in rust/)")
    proc = subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=str(_REPO),
    )
    try:
        yield Mech(proc)
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ── deterministic speech-proxy fixture (no network, no corpus dependency) ──

# Chunk grid for the fixture. Loud bursts saturate both energy curves to 1.0,
# the mid-burst dip stays above BOTH release floors (Rust 0.35 from
# (0.0566-0.0025)/0.0775, Python 0.35 from (0.0566-0.02)/0.06), so hysteresis
# holds in neither scorer and the segmentation is identical across languages.
_FIXTURE = [
    ("silence", 16),   # 0.512s leading silence
    ("loud", 32),      # 1.024s speech
    ("dip", 8),        # 0.256s above release -> must NOT split
    ("loud", 32),      # 1.024s speech
    ("silence", 6),    # 0.192s >= min_silence 100ms -> real gap -> split
    ("loud", 10),      # 0.320s >= min_speech 150ms -> kept
    ("loud", 2),       # 0.064s < min_speech 150ms -> dropped
    ("silence", 22),   # trailing silence
]


def _synth(kind: str, n_chunks: int) -> np.ndarray:
    n = n_chunks * _CHUNK
    if kind == "silence":
        return np.zeros(n, dtype=np.float32)
    amp = 0.4 if kind == "loud" else 0.08
    t = np.arange(n, dtype=np.float32) / _SR
    return (amp * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(_SR)
        f.writeframes((pcm * 32767.0).astype(np.int16).tobytes())
    return path


@pytest.fixture(scope="module")
def proxy_wav(tmp_path_factory) -> Path:
    """Deterministic 16kHz mono speech-proxy WAV built from the chunk grid."""
    out = tmp_path_factory.mktemp("media") / "speech_proxy.wav"
    if out.exists():
        return out
    return _write_wav(out, np.concatenate([_synth(k, n) for k, n in _FIXTURE]))


@pytest.fixture(scope="module")
def fixture_segments() -> list[dict]:
    """Expected segments for the fixture at threshold 0.5 / min_sil 100 / min_speech 150."""
    idx = 0
    runs: list[tuple[int, int]] = []
    cur: list[int] = []
    for kind, n in _FIXTURE:
        voiced = kind in ("loud", "dip")
        for _ in range(n):
            if voiced:
                if not cur:
                    cur = [idx, idx + 1]
                else:
                    cur[1] = idx + 1
            elif cur:
                runs.append(tuple(cur))
                cur = []
            idx += 1
    if cur:
        runs.append(tuple(cur))
    # drop runs shorter than min_speech (150ms = 4.6875 -> 5 chunks)
    kept = [(a, b) for a, b in runs if (b - a) * _FRAME >= 0.150]
    return [{"start": round(a * _FRAME, 6), "end": round(b * _FRAME, 6)} for a, b in kept]


def _norm_segs(segs: list[dict]) -> list[dict]:
    """Strip the Rust sidecar's informational `score` key so boundaries compare
    exactly (it is a placeholder; real scores travel with the `score` op)."""
    return [{"start": s["start"], "end": s["end"]} for s in segs]


# ── protocol ──

@skip_no_mech
def test_ping_reports_backend_and_provenance():
    with mech() as m:
        data = m.call("ping")
    assert data["pong"] is True
    assert data["vad_backend"], "backend must be reported"
    assert data["vad_model"], "model must be reported"


@skip_no_mech
def test_unknown_op_is_a_clean_error():
    with mech() as m:
        with pytest.raises(MechError, match="unknown op"):
            m.call("definitely_not_an_op")


# ── cadence + provenance on a real audio file ──

@skip_no_mech
def test_score_cadence_and_provenance_match_python(proxy_wav):
    """Both scorers must emit one score per 512-sample chunk over the same grid."""
    py = EnergyVAD().analyze(proxy_wav)
    with mech() as m:
        rs = m.call("score", path=str(proxy_wav))

    assert rs["chunk_size"] == _CHUNK
    assert abs(rs["chunk_seconds"] - _FRAME) < 1e-12
    assert rs["sample_rate"] == _SR
    assert rs["vad_backend"] == py.backend == "energy"
    assert rs["vad_model"] == py.vad_model == "energy-rms"

    # Trailing samples short of a full chunk are dropped by BOTH scorers.
    expected = int(proxy_wav.stat().st_size - 44) // (2 * _CHUNK)
    assert len(py.scores) == expected, "python padded or dropped a partial chunk"
    assert len(rs["scores"]) == expected, "rust frame count drifted from python"
    assert len(rs["scores"]) == len(py.scores)


@skip_no_mech
def test_energy_curves_agree_on_the_threshold_decision(proxy_wav):
    """The two RMS->probability curves use different anchors (see the parity
    doc's intentional-deviations table), so raw scores are not compared — but
    the speech/silence decision at the 0.5 threshold must agree on clear
    content, with divergence confined to mid-range transitions."""
    py = EnergyVAD().analyze(proxy_wav)
    with mech() as m:
        rs = m.call("score", path=str(proxy_wav))
    py_scores = np.asarray(py.scores, dtype=np.float64)
    rust_scores = np.asarray(rs["scores"], dtype=np.float64)

    # Saturated content: both scorers land on the same decision.
    decisions = (py_scores >= 0.5) != (rust_scores >= 0.5)
    assert not decisions.any(), f"{int(decisions.sum())} frames disagree"

    # Mid-range divergence is bounded (transition frames only).
    mid = (py_scores > 0.05) & (py_scores < 0.95)
    drift = float(np.abs(py_scores - rust_scores)[mid].max()) if mid.any() else 0.0
    assert drift < 0.15, f"curve drift grew to {drift:.3f}; update the parity doc"


@skip_no_mech
def test_fixture_segmentation_agrees_across_languages(proxy_wav, fixture_segments):
    """Curves agree on saturated bursts/silence, so segmentation must match."""
    with mech() as m:
        rs = m.call("score", path=str(proxy_wav))
        rust_segs = m.call(
            "segment", scores=rs["scores"], threshold=0.5,
            min_silence_ms=100, min_speech_ms=150,
        )
    py_regions, meta = segment_with_meta(
        rs["scores"], frame_ms=32, threshold=0.5,
        min_speech_ms=150, min_silence_ms=100,
    )
    py_segs = [{"start": r.start, "end": r.end} for r in py_regions]

    assert meta["frame_ms"] == 32
    assert meta["hysteresis"]["release"] == 0.35  # max(0.5 - 0.15, 0.05)
    assert _norm_segs(rust_segs) == py_segs, "segmentation diverged across languages"
    assert py_segs == fixture_segments, f"got {py_segs}, want {fixture_segments}"


@skip_no_mech
def test_cutlist_inversion_matches_python_on_fixture(proxy_wav, fixture_segments):
    duration = sum(n for _, n in _FIXTURE) * _FRAME
    with mech() as m:
        rust = m.call(
            "cutlist_from_segments", segments=fixture_segments,
            source_duration=duration, pad=0.3,
        )
    py_spans = cutlist_from_intervals(
        [(s["start"], s["end"]) for s in fixture_segments],
        source_duration=duration, pad=0.3,
    )
    py = [{"kind": s.kind, "start": s.start, "end": s.end} for s in py_spans]
    rust_spans = [
        {"kind": s["kind"], "start": s["start"], "end": s["end"]}
        for s in rust["intervals"]
    ]
    assert rust_spans == py
    assert rust["source_duration"] == duration
    assert any(s["kind"] == "remove" for s in py), "gaps must tile as removes"


# ── segmentation contract on hand-crafted scores (exact reference values) ──

@pytest.mark.parametrize("scores,threshold,min_silence_ms,min_speech_ms,expected", [
    # hysteresis: the 0.4 dip sits above release 0.35 -> one segment, no split.
    ([0.9, 0.4, 0.9], 0.5, 0, 0, [{"start": 0.0, "end": 0.096}]),
    # strict merge: a 96ms gap (3 chunks) is < 100ms -> merged into one segment.
    ([0.9] * 4 + [0.0] * 3 + [0.9] * 4, 0.5, 100, 0,
     [{"start": 0.0, "end": 0.352}]),
    # strict merge: a 128ms gap (4 chunks) is NOT < 100ms -> stays split.
    ([0.9] * 4 + [0.0] * 4 + [0.9] * 4, 0.5, 100, 0,
     [{"start": 0.0, "end": 0.128}, {"start": 0.256, "end": 0.384}]),
    # min_speech: a 96ms burst (3 chunks) is dropped, leaving nothing.
    ([0.0] * 2 + [0.9] * 3 + [0.0] * 2, 0.5, 0, 150, []),
])
@skip_no_mech
def test_segment_contract_matches_across_languages(
    scores, threshold, min_silence_ms, min_speech_ms, expected
):
    with mech() as m:
        rust = m.call(
            "segment", scores=scores, threshold=threshold,
            min_silence_ms=min_silence_ms, min_speech_ms=min_speech_ms,
        )
    py_regions, _ = segment_with_meta(
        scores, frame_ms=32, threshold=threshold,
        min_speech_ms=min_speech_ms, min_silence_ms=min_silence_ms,
    )
    py = [{"start": r.start, "end": r.end} for r in py_regions]
    assert _norm_segs(rust) == py == expected


# ── envelope symmetry: either sidecar can serve the Tauri shell ──

@skip_no_mech
def test_envelope_shape_matches_the_python_sidecar():
    """The Rust and Python sidecars answer the same request with the same
    envelope, so the shell can target either implementation (`server.py` pins
    PROTOCOL_RUST_COMPAT). This is the mitigation for the blocked Tauri build —
    see docs/tauri-termux-blocker.md."""
    from selekyt.server import process_line as python_process_line

    scores = [0.9] * 4 + [0.0] * 4 + [0.9] * 4
    req = {"op": "segment", "params": {
        "scores": scores, "threshold": 0.5,
        "min_silence_ms": 100, "min_speech_ms": 0,
    }}
    with mech() as m:
        rust = m.call("segment", **req["params"])
    py = json.loads(python_process_line(json.dumps(req)))["data"]["segments"]

    assert _norm_segs(rust) == _norm_segs(py)


@skip_no_mech
def test_error_envelope_shape_matches_the_python_sidecar():
    from selekyt.server import process_line as python_process_line

    with mech() as m:
        with pytest.raises(MechError):
            m.call("segment", scores="not-an-array")
    resp = json.loads(python_process_line(
        json.dumps({"op": "segment", "params": {"scores": "not-an-array"}})))
    assert resp["ok"] is False
    assert isinstance(resp["error"], str)


# ── real-media cross-check (SELEKYT_PARITY_MEDIA, or a synthetic stand-in) ──
#
# The contract this asserts — identical score cadence over one file, and
# identical segmentation of identical scores, across the two languages — holds
# for any 16 kHz mono WAV. Real speech is still the harder case, so the
# `parity_media` fixture honours SELEKYT_PARITY_MEDIA when it names a file and
# otherwise generates a deterministic synthetic recording, which keeps this
# check running in CI instead of skipping on every host without a corpus.


@skip_no_mech
def test_parity_on_real_media(parity_media):
    media = parity_media
    py = EnergyVAD().analyze(media)
    with mech() as m:
        rs = m.call("score", path=str(media))
        rust_segs = m.call(
            "segment", scores=rs["scores"], threshold=0.5,
            min_silence_ms=100, min_speech_ms=150,
        )
    # Cadence is the hard contract on real audio (curves may differ).
    assert len(rs["scores"]) == len(py.scores), (
        f"cadence drift: rust={len(rs['scores'])} python={len(py.scores)}"
    )
    # Identical scores must always segment identically: feed Rust's own scores
    # back through the Python segmentation and compare.
    py_regions, meta = segment_with_meta(
        rs["scores"], frame_ms=32, threshold=0.5,
        min_speech_ms=150, min_silence_ms=100,
    )
    assert _norm_segs(rust_segs) == [{"start": r.start, "end": r.end} for r in py_regions]
    assert meta["hysteresis"]["release"] == 0.35


# ── linked-track detection offset (AutoCut DetectParams::source_offset) ──

@skip_no_mech
@pytest.mark.parametrize("time_offset", [0.0, 3.0, 120.5])
def test_segment_time_offset_matches_across_languages(time_offset):
    """A linked track that rolled after the reference is scored in its own
    media time but the cutlist lives in project time, so every boundary is
    shifted by `time_offset`. Both implementations must shift identically."""
    scores = [0.0] * 4 + [0.9] * 6 + [0.0] * 4
    with mech() as m:
        rust = m.call(
            "segment", scores=scores, threshold=0.5,
            min_silence_ms=100, min_speech_ms=0, time_offset=time_offset,
        )
    py_regions, meta = segment_with_meta(
        scores, frame_ms=32, threshold=0.5,
        min_speech_ms=0, min_silence_ms=100, time_offset=time_offset,
    )
    py = [{"start": r.start, "end": r.end} for r in py_regions]
    assert meta["time_offset"] == time_offset
    assert _norm_segs(rust) == py, "time_offset diverged across languages"
    if time_offset:
        # The shift is real, not a no-op: boundaries move by exactly the offset.
        assert all(abs(s["start"] - time_offset - 4 * _FRAME) < 1e-9 for s in py)


# ── SMPTE timecode ops ──

@skip_no_mech
@pytest.mark.parametrize("fps,drop_frame", [
    (29.97, True), (29.97, False), (30000 / 1001, True),
    (25.0, False), (23.976, False), (59.94, True),
])
def test_detect_rate_matches_across_languages(fps, drop_frame):
    from engine.timecode import detect_rate
    with mech() as m:
        rust = m.call("detect_rate", fps=fps, drop_frame=drop_frame)
    py = detect_rate(fps, drop_frame)
    assert rust["nominal_fps"] == py.nominal_fps
    assert rust["is_drop_frame"] == py.is_drop_frame
    assert rust["frame_duration_num"] == py.frame_duration_num
    assert rust["frame_duration_den"] == py.frame_duration_den


@skip_no_mech
@pytest.mark.parametrize("tc,fps", [
    ("00:00:01:00", 25.0),
    ("01:00:00;00", 30000 / 1001),   # DF: exactly one hour
    ("00:01:00;00", 30000 / 1001),   # DF: drops 2 frames in the first minute
    (None, 30.0),
    ("", 30.0),
    ("garbage", 30.0),
    ("1:2", 30.0),                   # too few fields
])
def test_parse_smpte_matches_across_languages(tc, fps):
    from engine.timecode import parse_smpte
    with mech() as m:
        rust = m.call("parse_smpte", tc=tc, fps=fps)
    py = parse_smpte(tc, fps)
    assert rust["seconds"] == pytest.approx(py, abs=1e-9)


@skip_no_mech
@pytest.mark.parametrize("seconds,fps,drop_frame", [
    (1.0, 29.97, True), (1.0, 25.0, False), (0.0, 30.0, False), (2.5, 23.976, False),
])
def test_seconds_to_rational_matches_across_languages(seconds, fps, drop_frame):
    from engine.timecode import detect_rate, seconds_to_rational
    with mech() as m:
        rust = m.call("seconds_to_rational", seconds=seconds, fps=fps, drop_frame=drop_frame)
    py = seconds_to_rational(seconds, detect_rate(fps, drop_frame))
    assert rust["rational"] == py


# ── multi-track FCPXML: byte-identical across languages ──

_FCPXML_CASES = [
    # single track, NDF, integer rate
    {
        "fps": 25.0, "title": "demo",
        "cutlist": {"source_duration": 3.0, "intervals": [
            {"start": 0.0, "end": 1.0, "kind": "keep"},
            {"start": 1.0, "end": 2.0, "kind": "remove"},
            {"start": 2.0, "end": 3.0, "kind": "keep"}]},
        "reference": {"name": "cam", "duration": 3.0, "has_video": True, "has_audio": True, "offset": 0.0},
        "linked": [],
    },
    # drop-frame reference TC + an audio-only linked track below the primary
    {
        "fps": 29.97, "title": "demo",
        "cutlist": {"source_duration": 3.0, "intervals": [
            {"start": 0.0, "end": 1.0, "kind": "keep"},
            {"start": 1.0, "end": 2.0, "kind": "remove"},
            {"start": 2.0, "end": 3.0, "kind": "keep"}]},
        "reference": {"name": "cam", "duration": 3.0, "start_timecode": "01:00:00;00",
                      "has_video": True, "has_audio": True, "offset": 0.0},
        "linked": [{"name": "lav", "duration": 3.0, "has_video": False, "has_audio": True, "offset": 0.0}],
    },
    # late-rolling recorder + partial coverage + its own source TC
    {
        "fps": 25.0, "title": "multi",
        "cutlist": {"source_duration": 10.0, "intervals": [
            {"start": 0.0, "end": 2.0, "kind": "keep"},
            {"start": 2.0, "end": 4.0, "kind": "remove"},
            {"start": 4.0, "end": 6.0, "kind": "keep"},
            {"start": 6.0, "end": 10.0, "kind": "remove"}]},
        "reference": {"name": "cam", "duration": 10.0, "has_video": True, "has_audio": True, "offset": 0.0},
        "linked": [
            {"name": "cam-b", "duration": 5.0, "start_timecode": "00:00:10:00",
             "has_video": True, "has_audio": True, "offset": 0.0},
            {"name": "lav", "duration": 10.0, "has_video": False, "has_audio": True, "offset": 3.0},
        ],
    },
    # keeps that do not land on the frame grid. Rounding each clip's offset and
    # duration independently opened 1-frame gaps at the seams on the real
    # 304-clip cutlist; the record cursor must count frames instead.
    {
        "fps": 30.0, "title": "subframe",
        "cutlist": {"source_duration": 10.0, "intervals": [
            {"start": 0.0, "end": 0.517, "kind": "keep"},
            {"start": 0.517, "end": 0.983, "kind": "remove"},
            {"start": 0.983, "end": 2.011, "kind": "keep"},
            {"start": 2.011, "end": 2.494, "kind": "remove"},
            {"start": 2.494, "end": 3.877, "kind": "keep"},
            {"start": 3.877, "end": 10.0, "kind": "remove"}]},
        "reference": {"name": "cam", "duration": 10.0, "has_video": True, "has_audio": True, "offset": 0.0},
        "linked": [],
    },
]


@skip_no_mech
@pytest.mark.parametrize("case", _FCPXML_CASES, ids=[
    "single-ndf", "drop-frame-audio", "late-partial-tc", "subframe-keeps"])
def test_render_fcpxml_is_byte_identical_across_languages(case):
    """The Python emitter is a port of the Rust one; equal inputs must produce
    equal XML, so the shell can hand a cutlist to either sidecar."""
    from selekyt.server import process_line as python_process_line
    from engine import fcpxml, jumpcut

    with mech() as m:
        rust = m.call("render_fcpxml", **case)
    py_resp = json.loads(python_process_line(json.dumps({"op": "render_fcpxml", "params": case})))
    assert py_resp["ok"], py_resp.get("error")
    assert rust["xml"] == py_resp["data"]["xml"], (
        "FCPXML diverged across languages:\n"
        f"--- rust\n{rust['xml']}\n--- python\n{py_resp['data']['xml']}"
    )

    # And the in-process Python port agrees with the Python sidecar op.
    wire = case["cutlist"]
    direct = fcpxml.render(
        jumpcut.cutlist_from_wire(wire["intervals"], wire["source_duration"]),
        fcpxml.FcpxmlParams(
            fps=case["fps"],
            title=case["title"],
            reference=fcpxml.Track(
                name=case["reference"]["name"],
                duration=case["reference"]["duration"],
                has_video=case["reference"]["has_video"],
                has_audio=case["reference"]["has_audio"],
                start_timecode=case["reference"].get("start_timecode"),
                offset=case["reference"].get("offset", 0.0),
            ),
            linked=[
                fcpxml.Track(
                    name=t["name"], duration=t["duration"],
                    has_video=t["has_video"], has_audio=t["has_audio"],
                    start_timecode=t.get("start_timecode"),
                    offset=t.get("offset", 0.0),
                )
                for t in case["linked"]
            ],
        ),
    )
    assert direct == py_resp["data"]["xml"]


@skip_no_mech
def test_render_fcpxml_refuses_an_empty_timeline_in_both_languages():
    from selekyt.server import process_line as python_process_line

    case = {
        "fps": 25.0, "title": "demo",
        "cutlist": {"source_duration": 3.0, "intervals": [
            {"start": 0.0, "end": 3.0, "kind": "remove"}]},
        "reference": {"name": "cam", "duration": 3.0, "has_video": True, "has_audio": True, "offset": 0.0},
        "linked": [],
    }
    with mech() as m:
        with pytest.raises(MechError, match="nothing to keep"):
            m.call("render_fcpxml", **case)
    resp = json.loads(python_process_line(json.dumps({"op": "render_fcpxml", "params": case})))
    assert resp["ok"] is False
    assert "nothing to keep" in resp["error"]


@skip_no_mech
@pytest.mark.parametrize("case", _FCPXML_CASES, ids=[
    "single-ndf", "drop-frame-audio", "late-partial-tc", "subframe-keeps"])
def test_render_fcpxml_clips_tile_the_timeline_with_no_gaps(case):
    """A timeline whose clips do not abut has a black frame at every seam, and
    an editor's first impression of the export is that it stutters. Offset and
    duration are both rounded to the frame grid, so they must be rounded
    *consistently*: each clip starts exactly where the previous one ended.

    The old code rounded each independently from a float record cursor and
    produced one-frame gaps on real cutlists.
    """
    from selekyt.server import process_line as python_process_line

    for xml in _all_language_xml(case):
        clips = _primary_clips(xml)
        assert len(clips) >= 2, "this case must have more than one keep"
        cursor = Fraction(0)
        for clip in clips:
            offset = Fraction(clip["offset"].rstrip("s"))
            duration = Fraction(clip["duration"].rstrip("s"))
            assert offset == cursor, (
                f"clip does not abut the previous one: offset {offset} != {cursor} "
                f"(gap {float(offset - cursor) * 30:+.2f} frames @30p)"
            )
            assert duration > 0
            cursor = offset + duration


def _all_language_xml(case):
    """The same FCPXML from the Rust sidecar, the Python sidecar op, and the
    in-process Python port — all three must be gap-free."""
    from selekyt.server import process_line as python_process_line
    from engine import fcpxml, jumpcut

    out = []
    with mech() as m:
        out.append(m.call("render_fcpxml", **case)["xml"])
    resp = json.loads(python_process_line(json.dumps({"op": "render_fcpxml", "params": case})))
    assert resp["ok"], resp.get("error")
    out.append(resp["data"]["xml"])
    wire = case["cutlist"]
    out.append(
        fcpxml.render(
            jumpcut.cutlist_from_wire(wire["intervals"], wire["source_duration"]),
            fcpxml.FcpxmlParams(
                fps=case["fps"],
                title=case["title"],
                reference=fcpxml.Track(
                    name=case["reference"]["name"],
                    duration=case["reference"]["duration"],
                    has_video=case["reference"]["has_video"],
                    has_audio=case["reference"]["has_audio"],
                    start_timecode=case["reference"].get("start_timecode"),
                    offset=case["reference"].get("offset", 0.0),
                ),
                linked=[
                    fcpxml.Track(
                        name=t["name"], duration=t["duration"],
                        has_video=t["has_video"], has_audio=t["has_audio"],
                        start_timecode=t.get("start_timecode"),
                        offset=t.get("offset", 0.0),
                    )
                    for t in case["linked"]
                ],
            ),
        )
    )
    # Byte-identity is still the contract; contiguity is a property of it.
    assert len(set(out)) == 1, "FCPXML diverged across languages"
    return out


def _primary_clips(xml):
    """Primary-storyline asset-clips: children of the spine with no `lane`
    attribute. A lane marks a connected clip riding inside a parent, which is
    offset relative to that parent rather than to the timeline."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    spine = root.find("./library/event/project/sequence/spine")
    assert spine is not None, "no spine in the exported FCPXML"
    return [c.attrib for c in spine if c.get("lane") is None]


@skip_no_mech
def test_track_offset_from_timecode_matches_open_track_behavior():
    """AutoCut's `open_track`: when both reference and track carry timecode,
    the track's media begins at project time trackTC - referenceTC."""
    from engine.timecode import track_offset_from_timecode
    from selekyt.server import process_line as python_process_line

    fps = 30000 / 1001
    with mech() as m:
        # The Rust sidecar has no dedicated op; derive via parse_smpte both
        # sides and confirm the Python helper reproduces the arithmetic.
        ref = m.call("parse_smpte", tc="01:00:00;00", fps=fps)["seconds"]
        track = m.call("parse_smpte", tc="01:00:10;00", fps=fps)["seconds"]
    assert track_offset_from_timecode("01:00:00;00", "01:00:10;00", fps) == pytest.approx(track - ref)

    # Missing timecode on either side: nothing to derive from, starts at zero.
    assert track_offset_from_timecode(None, "01:00:10;00", fps) == 0.0
    assert track_offset_from_timecode("01:00:00;00", None, fps) == 0.0

    # The Python sidecar exposes it as an op for the shell.
    resp = json.loads(python_process_line(json.dumps({"op": "track_offset_from_timecode", "params": {
        "reference_timecode": "01:00:00;00", "track_timecode": "01:00:10;00", "fps": fps}})))
    assert resp["ok"], resp.get("error")
    assert resp["data"]["offset"] == pytest.approx(track - ref)


# ── editorial transport algebra (AutoCut src/lib/cuts.ts) ──

def _keeps_wire(keeps) -> list[dict]:
    return [{"start": k.start, "end": k.end, "disabled": k.disabled} for k in keeps]


@skip_no_mech
@pytest.mark.parametrize("keeps_input,duration,expected", [
    # degenerate interval is dropped outright, so only the real keep survives
    ([(0.0, 0.005, False), (1.0, 2.0, False)], 5.0, [(1.0, 2.0)]),
    # touching stays two keeps; only genuine overlap fuses
    ([(0.0, 1.0, False), (1.0, 2.0, False)], 5.0, [(0.0, 1.0), (1.0, 2.0)]),
    ([(0.0, 1.5, False), (1.0, 2.0, False)], 5.0, [(0.0, 2.0)]),
    # clipped to [0, duration]
    ([(-1.0, 1.0, False), (4.0, 9.0, False)], 5.0, [(0.0, 1.0), (4.0, 5.0)]),
    # active content wins over a disabled keep it overlaps
    ([(0.0, 2.0, True), (1.0, 3.0, False)], 5.0, [(0.0, 3.0)]),
])
def test_normalize_keeps_matches_across_languages(keeps_input, duration, expected):
    from engine.cut_algebra import normalize_keeps

    wire = [{"start": s, "end": e, "disabled": d} for s, e, d in keeps_input]
    with mech() as m:
        rust = m.call("normalize_keeps", keeps=wire, duration=duration)
    py = normalize_keeps(keeps_input, duration)
    assert [(k["start"], k["end"]) for k in rust] == expected
    assert [(k.start, k.end) for k in py] == expected
    assert _keeps_wire(py) == rust


@skip_no_mech
@pytest.mark.parametrize("index,edge,proposed,want", [
    (0, "in", -5.0, 0.0),    # floor is 0 for the first keep
    (0, "out", 9.0, 2.5),    # ceil is the next keep's start (touching stays two keeps)
    (1, "in", 0.0, 2.0),     # floor is the previous keep's end
    (1, "out", 99.0, 10.0),  # ceil is duration for the last keep
])
def test_clamp_keep_edge_matches_across_languages(index, edge, proposed, want):
    from engine.cut_algebra import Keep, clamp_keep_edge

    keeps = [Keep(0.0, 2.0), Keep(2.5, 4.0)]
    wire = _keeps_wire(keeps)
    with mech() as m:
        rust = m.call(
            "clamp_keep_edge", keeps=wire, index=index, edge=edge,
            proposed=proposed, duration=10.0,
        )
    py = clamp_keep_edge(keeps, index, edge, proposed, 10.0)
    assert rust["clamped"] == pytest.approx(py)
    assert py == pytest.approx(want)


@skip_no_mech
@pytest.mark.parametrize("time,lookahead,want", [
    (2.2, 0.0, 2.5),     # inside a removed region -> jump to its end
    (1.9, 0.5, 2.5),     # upcoming gap inside the lookahead -> pre-empt it
    (1.0, 0.1, None),    # too far ahead -> keep rolling
    (2.49, 0.0, 2.5),    # 2.49 < 2.495 (end minus settled) -> still skips
    (2.497, 0.0, None),  # settled tail: already leaving, no re-issue
])
def test_skip_target_for_matches_across_languages(time, lookahead, want):
    from engine.cut_algebra import skip_target_for

    omitted = [(2.0, 2.5)]
    with mech() as m:
        rust = m.call("skip_target_for", omitted=omitted, time=time, lookahead=lookahead)
    py = skip_target_for(omitted, time, lookahead)
    assert rust["target"] == py
    assert py == want


@skip_no_mech
@pytest.mark.parametrize("time,want", [
    (0.5, 0.0),   # inside keep 0, under the restart window -> restart it
    (1.5, 0.0),   # past 1s into keep 0 -> restart it (standard player behaviour)
    (3.0, 0.0),   # inside keep 1 -> step back to keep 0's start
    (9.0, 2.5),   # past everything -> last enabled keep's start
])
def test_previous_keep_target_matches_across_languages(time, want):
    from engine.cut_algebra import Keep, previous_keep_target

    keeps = [Keep(0.0, 2.0), Keep(2.5, 4.0), Keep(4.5, 6.0, disabled=True)]
    with mech() as m:
        rust = m.call("previous_keep_target", keeps=_keeps_wire(keeps), time=time)
    py = previous_keep_target(keeps, time)
    assert rust["target"] == py
    assert py == want


@skip_no_mech
@pytest.mark.parametrize("time,want", [
    (0.0, 2.5),   # next enabled keep after the playhead
    (2.4, 2.5),   # 2.5 > 2.4 + 0.05 -> keep 1 is the upcoming one
    (2.51, 4.0),  # inside keep 1 -> its end (the disabled keep is not a target)
    (9.0, 10.0),  # past the last enabled keep -> duration
])
def test_next_keep_target_matches_across_languages(time, want):
    from engine.cut_algebra import Keep, next_keep_target

    keeps = [Keep(0.0, 2.0), Keep(2.5, 4.0), Keep(4.5, 6.0, disabled=True)]
    with mech() as m:
        rust = m.call(
            "next_keep_target", keeps=_keeps_wire(keeps), time=time, duration=10.0
        )
    py = next_keep_target(keeps, time, 10.0)
    assert rust["target"] == py
    assert py == want


@skip_no_mech
def test_omitted_intervals_matches_across_languages():
    from engine.cut_algebra import EditCut, EditCutList, omitted_intervals

    wire = {
        "source_duration": 10.0,
        "intervals": [
            {"start": 0.0, "end": 2.0, "kind": "keep"},
            {"start": 2.0, "end": 2.5, "kind": "remove"},
            {"start": 2.5, "end": 4.0, "kind": "keep", "disabled": True},
            {"start": 4.0, "end": 6.0, "kind": "keep"},
        ],
    }
    with mech() as m:
        rust = m.call("omitted_intervals", cutlist=wire)
    py = omitted_intervals(_edit_cutlist(wire))
    # Both drops: the real remove and the disabled keep.
    assert [(c["start"], c["end"]) for c in rust] == [(2.0, 2.5), (2.5, 4.0)]
    assert [(c.start, c.end) for c in py] == [(2.0, 2.5), (2.5, 4.0)]


def _edit_cutlist(wire: dict):
    from engine.cut_algebra import EditCut, EditCutList

    return EditCutList(
        source_duration=float(wire["source_duration"]),
        intervals=[
            EditCut(
                start=float(c["start"]),
                end=float(c["end"]),
                kind=str(c.get("kind", "remove")),
                disabled=bool(c.get("disabled", False)),
            )
            for c in wire["intervals"]
        ],
    )