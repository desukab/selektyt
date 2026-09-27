"""Silero VAD driver tests — the backend that scores learned speech probability.

These are *driver* tests, not model tests: the model is a fixed asset, and what
can regress is the plumbing around it — the left-context construction, the
recurrent state threaded across ORT calls and read windows, the tail rule, and
the PCM conformance that decides what the model actually hears. Each of those is
a place where a silent wrong answer is possible and would never be caught by a
"does it return floats" test.

Everything here needs the vendored model, so the whole module skips when it is
absent rather than reporting a pass that exercised nothing.

Absolute thresholds are deliberately not asserted. Synthetic speech is not
speech — measured on a tone, in-speech frames score 0.20-0.41 and silence
0.04-0.12, well below the 0.5 a real voice reaches. What is stable and what the
editor actually depends on is the *contrast*: speech frames score higher than
silence frames, and that ordering survives levels the energy backend is blind at.
"""
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from engine.vad import FRAME_MS, EnergyVAD, SileroVADBackend, segment_from_scores

ROOT = Path(__file__).resolve().parent.parent
_HAVE_FFMPEG = shutil.which("ffmpeg") is not None

pytest.importorskip("onnxruntime", reason="silero needs onnxruntime to run")
if not SileroVADBackend().is_available():
    pytest.skip(f"silero model {SileroVADBackend._MODEL_NAME!r} not found; "
                "engine/models/ is incomplete in this checkout",
                allow_module_level=True)

STEP = SileroVADBackend._STEP          # 512 samples = 32 ms, one inference
FRAME_MS = FRAME_MS                    # the grid segment_from_scores assumes
SR = 16000


@pytest.fixture(scope="module")
def vad():
    """One backend for the module: loading the session costs ~0.5 s and nothing
    here needs a fresh one."""
    return SileroVADBackend()


def _write(raw: np.ndarray, path: Path, sr: int = SR) -> Path:
    """Write a float signal in [-1, 1] as mono int16 PCM."""
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((np.clip(raw, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    return path


def _write_int16(raw: np.ndarray, path: Path, sr: int = SR) -> Path:
    """Write an int16 array verbatim, with no rescale.

    The parity test needs the reference to see byte-identical PCM: a float
    round trip through ``* 32767 / 32768`` moves every sample by one part in
    32 768, which is enough to shift scores in the fifth decimal and blur the
    line between "the drivers agree" and "the quantization agrees".
    """
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(raw.astype(np.int16).tobytes())
    return path


def _voiced_burst(n: int, f0: float = 120.0, sr: int = SR) -> np.ndarray:
    """A stand-in for voiced speech: a harmonic stack under a syllable-rate
    envelope.

    A pure tone does not work here, and the reason is worth recording because it
    is a property of the model, not of this harness. Silero was trained on
    voices, and scores a 220 Hz sine a mean 0.09 against silence 0.006 — a
    tone-based "speech" segment is indistinguishable from quiet, so no contrast
    can be asserted and any test built on one is vacuous. Adding six harmonics
    raises the active mean to 0.40, and adding a 4 Hz amplitude envelope raises
    it to 0.94 against silence 0.035. The envelope modulation is a genuine cue
    to syllable rate, not a hack to defeat the test.
    """
    tt = np.arange(n, dtype=np.float32) / sr
    stack = sum(np.sin(2 * np.pi * f0 * (k + 1) * tt) / (k + 1)
                for k in range(6))
    stack = stack / np.abs(stack).max()
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * tt)
    return 0.4 * stack * envelope


def _speech_silence(sr: int = SR, dur: float = 9.0) -> np.ndarray:
    """Silence, voiced speech, silence, voiced speech. The active spans are
    2-5 s and 7-9 s, which the segmentation tests depend on."""
    n = int(sr * dur)
    sig = np.zeros(n, dtype=np.float32)
    sig[2 * sr:5 * sr] = _voiced_burst(3 * sr, sr=sr)
    sig[7 * sr:9 * sr] = _voiced_burst(2 * sr, sr=sr)
    return sig


def _active_mask(n_scores: int, frame: float) -> np.ndarray:
    """Which scores cover the two active spans, sized to the array actually
    returned — silero pads a partial final step to one extra score, so a mask
    computed from the sample count alone is off by one on any file whose length
    is not a whole number of steps."""
    mask = np.zeros(n_scores, dtype=bool)
    for lo, hi in ((2.0, 5.0), (7.0, 9.0)):
        mask[int(lo / frame):int(hi / frame)] = True
    return mask


# ── the model distinguishes speech from silence ──

def test_silero_scores_the_active_segments_higher(vad, tmp_path):
    """The whole point of the backend. Frames covering the tones must outrank
    the frames covering silence; asserted as a mean contrast rather than a
    threshold, because a tone is not a voice."""
    path = _write(_speech_silence(), tmp_path / "mix.wav")
    scores = np.asarray(vad.analyze(path).scores, dtype=np.float32)

    frame = FRAME_MS / 1000.0
    active = _active_mask(len(scores), frame)
    assert active.any() and (~active).any(), "the signal has both regimes"

    speech = scores[active].mean()
    quiet = scores[~active].mean()
    assert speech > quiet, "silero does not separate voiced audio from silence"
    # Real speech clears 0.5; the synthetic stand-in reaches it too once the
    # syllable envelope is present, so the segmentation threshold is in play.
    assert speech > 0.5, (
        f"in-speech mean only {speech:.3f}: the synthetic stand-in is not "
        "speech-like enough for this assertion to mean anything")
    print(f"silero: in-speech {speech:.3f} vs silence {quiet:.3f}")


def test_silero_segments_the_active_regions(vad, tmp_path):
    """End to end through ``segment_from_scores``: two active bursts become two
    regions on the same 32 ms grid every other backend uses."""
    path = _write(_speech_silence(), tmp_path / "mix.wav")
    result = vad.analyze(path)
    regions = segment_from_scores(result.scores, sample_rate=result.sample_rate,
                                  frame_ms=result.frame_ms,
                                  threshold=0.5, min_speech_ms=150,
                                  min_silence_ms=100)
    assert len(regions) == 2
    # Each region must sit on its burst, not in the silence around it. The
    # model needs a few frames to commit to speech and releases a few frames
    # early, so the tolerance is what the hysteresis itself spans, not a
    # frame edge.
    for region, (lo, hi) in zip(regions, ((2.0, 5.0), (7.0, 9.0))):
        assert region.start >= lo - 0.3, (region.start, lo)
        assert region.end <= hi + 0.3, (region.end, hi)
    assert regions[0].end < regions[1].start, \
        "the 2 s of silence between the bursts did not survive segmentation"


# ── the read window must be transparent ──

def _scores_at_window(vad, path, window_s, tmp_path):
    """Score ``path`` with a read window of ``window_s`` seconds. Tiny windows
    cross the boundary many times in a short file, which is what reaches the
    state-carry code at all."""
    backend = SileroVADBackend()
    backend._session = vad._session          # reuse the loaded session
    backend._WINDOW_S = window_s
    return backend.analyze(path)


def test_windowed_read_matches_a_single_window(vad, tmp_path):
    """Windowing is a memory change only. Seeded noise rather than a tone: a
    tone scores the same every frame, so a reset or a shifted row at a boundary
    would not show up, while noise differs frame to frame and any misframing or
    dropped recurrent state moves a score.

    This is the regression test for the state-carry bug — an earlier version
    unpacked ``_score_block``'s tuple into three names and discarded the
    returned ``h``/``c``, so the recurrent state reset to zeros at every read
    window while the docstring claimed it threaded. On a 126 s corpus source
    that collapsed speech probability at exactly 60.00 s; here it would make the
    2 s-window scores disagree with the whole-file scores.
    """
    rng = np.random.default_rng(0)
    n = int(SR * 9.5)
    path = _write((rng.random(n, dtype=np.float32) - 0.5) * 0.6,
                  tmp_path / "noise.wav")

    whole = _scores_at_window(vad, path, 600, tmp_path)
    # 2 s windows cross the boundary four times in a 9.5 s file.
    chopped = _scores_at_window(vad, path, 2, tmp_path)

    assert len(chopped.scores) == len(whole.scores), \
        f"{len(chopped.scores)} scores against {len(whole.scores)}"
    diff = np.abs(np.asarray(chopped.scores) - np.asarray(whole.scores))
    assert diff.max() < 1e-5, \
        f"windowing changed scores: max|diff|={diff.max():.6f} " \
        f"at frame {int(diff.argmax())}"


def test_windowed_read_scores_a_speech_file_like_a_single_window(vad, tmp_path):
    """The same contract on structured audio: the tones give the scores dynamic
    range that flat noise does not, so a reset mid-utterance would distort a
    decision the editor sees rather than only a number."""
    path = _write(_speech_silence(), tmp_path / "mix.wav")
    whole = _scores_at_window(vad, path, 600, tmp_path)
    chopped = _scores_at_window(vad, path, 1, tmp_path)
    assert len(chopped.scores) == len(whole.scores)
    diff = np.abs(np.asarray(chopped.scores) - np.asarray(whole.scores))
    assert diff.max() < 1e-5, f"max|diff|={diff.max():.6f}"


# ── tails and sub-frame input ──

def test_a_tail_shorter_than_one_step_is_padded_not_dropped(vad, tmp_path):
    """The energy reader drops a partial final frame; silero pads it, because
    up to 31 ms of the end of every source would otherwise be invisible to a
    cut — the last syllable is exactly where that bites. The padded frame is
    one extra score, and ``duration`` still reports the true file length."""
    n = int(SR * 2.0) + 400        # 400 < STEP, so a partial step is left over
    assert n % STEP, "this test needs a tail that is not a whole number of steps"
    path = _write(np.full(n, 0.0, dtype=np.float32), tmp_path / "tail.wav")
    result = vad.analyze(path)
    assert len(result.scores) == n // STEP + 1
    assert result.duration == pytest.approx(n / SR)


def test_audio_shorter_than_one_step_yields_one_score(vad, tmp_path):
    """Sub-step input is padded to a single score so the array is never empty
    for the divisions downstream — the same rule the energy reader has."""
    path = _write(np.full(STEP // 4, 0.3, dtype=np.float32),
                  tmp_path / "short.wav")
    result = vad.analyze(path)
    assert len(result.scores) == 1
    assert result.duration == pytest.approx((STEP // 4) / SR)


def test_audio_of_exactly_one_step_is_one_score(vad, tmp_path):
    rng = np.random.default_rng(1)
    path = _write((rng.random(STEP, dtype=np.float32) - 0.5) * 0.6,
                  tmp_path / "step.wav")
    assert len(vad.analyze(path).scores) == 1


# ── level invariance: the defect the energy backend has ──

def test_silero_keeps_the_contrast_when_the_source_is_quiet(vad, tmp_path):
    """Scaling the whole source down must not flatten the speech/silence gap.

    The energy scorer compares frame RMS against fixed absolute thresholds
    (0.02 to 0.08), so as the level falls every frame falls below the base and
    the backend scores the whole file silent — silently, with no error. Measured
    on ``corpus/wer/hq_media_hadhazy-protest.wav`` (RMS 0.008) it emits zero
    speech regions and the pipeline reports ``dead_air_ratio`` 1.0. This test
    holds both backends to the contrast they kept at full level and records
    where each one loses it.
    """
    sig = _speech_silence()
    frame = FRAME_MS / 1000.0

    for scale in (0.4, 0.05, 0.01):
        scaled = sig * scale
        silero = np.asarray(
            vad.analyze(_write(scaled, tmp_path / f"silero_{scale}.wav")).scores,
            dtype=np.float32)
        energy = np.asarray(EnergyVAD().analyze(
            _write(scaled, tmp_path / f"energy_{scale}.wav")).scores,
            dtype=np.float32)

        for name, scores in (("silero", silero), ("energy", energy)):
            active = _active_mask(len(scores), frame)
            speech = scores[active].mean()
            quiet = scores[~active].mean()
            print(f"  scale={scale:<5} {name:<7} speech={speech:.3f} "
                  f"silence={quiet:.3f} gap={speech - quiet:+.3f}")

        # Silero keeps the ordering at every level; that is the whole claim.
        active = _active_mask(len(silero), frame)
        assert silero[active].mean() > silero[~active].mean(), \
            f"silero lost the speech/silence ordering at scale {scale}"

    # The energy backend loses it as the level falls. Asserted rather than
    # printed because this is the reason silero is the preferred backend: below
    # its fixed absolute base threshold every frame reads as silence and the
    # file looks like dead air, with no error raised.
    low = sig * 0.01
    energy = np.asarray(EnergyVAD().analyze(
        _write(low, tmp_path / "energy_low.wav")).scores, dtype=np.float32)
    active = _active_mask(len(energy), frame)
    assert energy.max() < 0.5, (
        f"the energy backend was expected to be blind at scale 0.01 but "
        f"reached {energy.max():.3f}; this test's premise is stale")


# ── PCM conformance: the model hears what the reader decodes ──

def test_silero_conforms_stereo_and_a_wrong_sample_rate(vad, tmp_path):
    """A stereo 48 kHz WAV is not a file this backend can read directly — the
    model's contract is 16 kHz mono — but it is what a real container hands over.
    ``_conforming_path`` must resample and downmix so the scores match the mono
    16 kHz version of the same signal, and the frame grid must line up with the
    original duration."""
    if not _HAVE_FFMPEG:
        pytest.skip("ffmpeg is needed to conform a non-16k-mono WAV")

    sig = _speech_silence()
    mono16 = _write(sig, tmp_path / "mono16.wav")
    # The same signal interleaved on two channels at 48 kHz.
    stereo48 = np.repeat(sig, 3)                     # 16k -> 48k
    interleaved = np.empty(len(stereo48) * 2, dtype=np.float32)
    interleaved[0::2] = stereo48
    interleaved[1::2] = stereo48
    stereo_path = tmp_path / "stereo48.wav"
    with wave.open(str(stereo_path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes((interleaved * 32767).astype(np.int16).tobytes())

    base = vad.analyze(mono16)
    conformed = vad.analyze(stereo_path)

    assert conformed.sample_rate == SR
    assert conformed.duration == pytest.approx(base.duration, abs=0.05)
    assert len(conformed.scores) == len(base.scores)
    # ffmpeg's resampler is not bit-identical to a sample-repeat, so this is a
    # shape check, not an equality check: the segments must land in the same
    # places, which is what an editor's cut points depend on.
    seg = dict(sample_rate=SR, frame_ms=FRAME_MS, threshold=0.5,
               min_speech_ms=150, min_silence_ms=100)
    a = segment_from_scores(base.scores, **seg)
    b = segment_from_scores(conformed.scores, **seg)
    assert len(a) == len(b) == 2
    for ra, rb in zip(a, b):
        assert abs(ra.start - rb.start) < 0.2 and abs(ra.end - rb.end) < 0.2


# ── parity with an independent driver of the same model file ──

def test_silero_matches_the_faster_whisper_driver(vad, tmp_path):
    """faster-whisper ships the same ``silero_vad_v6.onnx`` and a driver for it,
    so this is a reference implementation of *this model*, not a description of
    one in a paper. Both drivers must see bit-identical PCM or the comparison
    measures the quantization, not the driver — so the reference gets the same
    int16 bytes this reader decodes, written verbatim, never a rescaled float.

    On a source that fits one read window every frame but the last must agree
    exactly: the context rows then match frame for frame and the recurrent state
    threads through a single ORT batch. The last frame is the one place the two
    legitimately differ, and the reason is a defect in the reference, not here —
    its context array is a *view* of the audio, so ``context[-1] = 0``, which
    exists to give row 0 zero context, also zeroes the final row's own last 64
    samples in the array it then scores. Verified by running the model on the
    reference's block with and without that mutation: it moves exactly one
    score, the last one (0.0327 against 0.0312), and no other. Any divergence
    outside the last frame therefore means *this* driver's context construction
    or state threading has drifted from the shipped one.
    """
    try:
        from faster_whisper.vad import SileroVADModel
    except ImportError:
        pytest.skip("faster-whisper is not installed; the reference driver "
                    "cannot run")

    rng = np.random.default_rng(3)
    # A whole number of steps: the reference asserts its input is a multiple of
    # 512 and drops any tail, where this one pads. Aligning the length keeps the
    # comparison on the frames both drivers score.
    n = STEP * 100
    raw = ((rng.random(n, dtype=np.float32) - 0.5) * 0.6 * 32768).astype(np.int16)
    path = _write_int16(raw, tmp_path / "ref.wav")

    mine = np.asarray(vad.analyze(path).scores, dtype=np.float32)

    ref_model = SileroVADModel(
        str(ROOT / "engine" / "models" / SileroVADBackend._MODEL_NAME))
    ref = np.asarray(ref_model(raw.astype(np.float32) / 32768.0),
                     dtype=np.float32)

    assert len(mine) == len(ref), f"{len(mine)} scores against {len(ref)}"

    # Every frame but the last: bit-identical construction, so bit-identical
    # scores. Measured max|diff| is 0.0; the tolerance admits library float
    # noise without admitting the state-carry bug, which moved scores by 0.75.
    body = np.abs(mine[:-1] - ref[:-1])
    assert body.max() < 1e-6, \
        f"diverged from the reference driver: max|diff|={body.max():.6f} " \
        f"at frame {int(body.argmax())}, mean={body.mean():.6f}"

    # The last frame differs only through the reference's input mutation. It is
    # asserted as a bounded, expected difference rather than compared exactly:
    # a source whose final 32 ms is near the threshold would see it move further.
    last = abs(float(mine[-1] - ref[-1]))
    assert last < 0.05, f"last-frame divergence {last:.4f} exceeds what the " \
                        f"reference's own mutation explains"

    # And the divergence must not be a decision the editor would see.
    flips = int(((mine >= 0.5) != (ref >= 0.5)).sum())
    assert flips == 0, f"{flips} frames cross the 0.5 threshold differently"
