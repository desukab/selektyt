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
import subprocess
import wave
from contextlib import contextmanager
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


# ── optional real-media cross-check (set SELEKYT_PARITY_MEDIA) ──

_REAL = os.environ.get("SELEKYT_PARITY_MEDIA")


@pytest.mark.skipif(
    not (_REAL and Path(_REAL).is_file() and _mech_binary()),
    reason="set SELEKYT_PARITY_MEDIA to a real 16kHz mono WAV to run this",
)
def test_parity_on_real_media():
    media = Path(_REAL)
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
