"""Deterministic transcripts and media builders for integration tests.

Everything here is hermetic and reproducible: no network, no real footage, no
real ASR, no clocks and no randomness in the output (the one place an RNG is
used it is a seeded ``random.Random`` so a transcript is the same in every
process and on every host).

Timing model
------------
Words are laid out inside a sentence at a fixed delivery rate
(:data:`WORDS_PER_SECOND`), each word occupying 80% of its slot so natural
micro-gaps appear without any wall-clock measurement. Sentences are packed
into ASR-like segments of roughly :data:`SEGMENT_SECONDS` and stories are
separated by a short pause. The result looks like a real word-timed
transcript (segments of several seconds, words with start/end) while being a
pure function of the input text.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.story_units import split_sentences
from models.core import Segment, Word

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
skip_no_ffmpeg = pytest.mark.skipif(
    not HAVE_FFMPEG, reason="ffmpeg/ffprobe not available"
)

WORDS_PER_SECOND = 2.5
SEGMENT_SECONDS = 7.0
STORY_GAP_SECONDS = 1.5
SILENCE_BETWEEN_SEGMENTS = 0.4


# ── word timing ──────────────────────────────────────────────────────────────


def word(text: str, start: float, end: float, confidence: float = 1.0) -> Word:
    return Word(text, start, end, confidence)


def timed_words(sentence: str, start: float, rate: float = WORDS_PER_SECOND) -> list[Word]:
    """Lay one sentence out at a constant delivery rate.

    Each word takes 80% of its slot, leaving a deterministic micro-gap between
    words so downstream pause detection sees realistic inter-word spacing.
    """
    tokens = sentence.split()
    if not tokens:
        return []
    slot = (len(tokens) / rate) / len(tokens)
    out: list[Word] = []
    t = start
    for tok in tokens:
        out.append(Word(tok, round(t, 3), round(t + 0.8 * slot, 3)))
        t += slot
    return out


def segment(start: float, end: float, text: str, words: list[Word] | None = None) -> Segment:
    return Segment(start, end, text, words if words is not None else [])


# ── transcripts ──────────────────────────────────────────────────────────────


def build_transcript(
    stories: list[str],
    *,
    rate: float = WORDS_PER_SECOND,
    segment_seconds: float = SEGMENT_SECONDS,
    story_gap: float = STORY_GAP_SECONDS,
) -> list[Segment]:
    """Turn story paragraphs into a word-timed list of ASR-like segments.

    Sentences are never split across segments and every story boundary forces
    a new segment plus a pause, so story units are recoverable from timing
    alone even before the lexical segmentation runs.
    """
    segments: list[Segment] = []
    buf_words: list[Word] = []
    buf_sents: list[str] = []
    buf_start = 0.0
    t = 0.0

    def flush(end: float) -> None:
        nonlocal buf_words, buf_sents, buf_start
        if not buf_sents:
            return
        segments.append(
            Segment(
                buf_start,
                round(end, 3),
                " ".join(buf_sents),
                buf_words,
            )
        )
        buf_words, buf_sents = [], []

    for story in stories:
        if buf_sents:
            flush(t)
            t += story_gap
            buf_start = t
        for sentence in split_sentences(story):
            ws = timed_words(sentence, t, rate)
            if not ws:
                continue
            if not buf_sents:
                buf_start = ws[0].start
            buf_words.extend(ws)
            buf_sents.append(sentence)
            t = ws[-1].end
            if t - buf_start >= segment_seconds:
                flush(t)
                t += SILENCE_BETWEEN_SEGMENTS
                buf_start = t
    flush(t)
    return segments


def transcript_json(segments: list[Segment], language: str) -> dict:
    """The transcript.json payload the pipeline reads back."""
    from dataclasses import asdict

    return {
        "language": language,
        "asr": {
            "engine": "fixture",
            "model": "fixture",
            "word_timestamps": "fixture-generated",
            "fallback_reason": "",
        },
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [asdict(w) for w in s.words],
            }
            for s in segments
        ],
    }


def write_transcript_json(path: Path, segments: list[Segment], language: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(transcript_json(segments, language), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ── English content ───────────────────────────────────────────────────────────

ENGLISH_STORIES: list[str] = [
    # digits -> story_type "fact"
    (
        "Hyderabad introduced 40 new electric buses on the city routes this "
        "month. Each vehicle carries a battery pack rated for two hundred "
        "kilometres of continuous service. The depot installed twelve charging "
        "stations along the northern corridor. Drivers reported shorter "
        "turnaround times at the busiest terminals."
    ),
    # question -> story_type "question"
    (
        "Why did the coastal ferry service double its ridership in a single "
        "season? The schedule added two extra crossings during the evening "
        "commute. Ticket prices stayed flat while fuel costs rose elsewhere. "
        "Passengers told the crew that the evening boats fit their working "
        "hours far better."
    ),
    # contrast opener -> story_type "revelation"
    (
        "The district library reopened after eighteen months of renovation "
        "work. However, volunteers sorted nine thousand donated books onto "
        "fresh wooden shelves. The reading room gained better lighting and a "
        "quiet study corner. Children returned for the Saturday story sessions "
        "in large numbers."
    ),
    # plain payoff -> story_type "story"
    (
        "A small bakery in the old town rebuilt its oven from reclaimed "
        "bricks. The owner learned the technique from a retired mason across "
        "the street. Bread output doubled without expanding the shop floor. "
        "Customers line up before sunrise for the first batch of the day."
    ),
]


def english_transcript() -> list[Segment]:
    """Four distinct stories with word timings; every one clears the editorial
    and context gates, so a full pipeline run over it selects several clips."""
    return build_transcript(ENGLISH_STORIES)


# ── Telugu content ────────────────────────────────────────────────────────────

TELUGU_STORIES: list[str] = [
    # Telugu words are long, so sentence word counts are kept well above the
    # 20-word-per-candidate floor rank enforces: two-sentence spans carry
    # roughly 26 words and ten seconds, clearing both the word and the
    # duration floors with margin. Digits make this a "fact" story.
    (
        "హైదరాబాద్ నగరంలో ఈ నెలలో 40 కొత్త ఎలక్ట్రిక్ బస్సులు అన్ని రోడ్డుపైకి "
        "వచ్చాయని అధికారులు ప్రకటించారు. ప్రతి బస్సు ఒకే ఛార్జీతో రెండు వందల "
        "కిలోమీటర్ల దూరం ఎలాంటి ఇబ్బంది లేకుండా ప్రయాణించగలదు. నగరంలోని పదమూడు "
        "ప్రదేశాల్లో కొత్త ఛార్జింగ్ స్టేషన్లు ఏర్పాటయ్యాయని వారు చెప్పారు. డ్రైవర్లు "
        "ఈ కొత్త బస్సులతో తమ రోజువారీ పనిని గణనీయంగా త్వరగా ముగించగలిగారు."
    ),
    # "అయితే" (but) inside the story makes this a "revelation" story; it is
    # never the first token, so no candidate opens mid-conversation.
    (
        "తెలంగాణ రైతులు ఈ సంవత్సరం మిరప పంటను రికార్డు స్థాయిలో పండించారని "
        "చాలామంది ఆశ్చర్యపోయారు. అయితే మార్కెట్లో ధరలు కూడా మంచిగానే ఉన్నాయని "
        "రైతులు స్వయంగా చెప్పారు. ప్రభుత్వం ఈ పంట కోసం అదనపు నిధులను అన్ని "
        "గ్రామాలకు అందించింది. కొత్త గిడ్డంగులు కూడా ఈ ఏడాది నిర్మించబడ్డాయి."
    ),
    # A terminal "?" makes this a "question" story.
    (
        "మన ప్రభుత్వ పాఠశాలల్లో కొత్త డిజిటల్ తరగతులు ఎలా నిర్వహించబడుతున్నాయి "
        "అని తల్లిదండ్రులు ఉపాధ్యాయులను అడిగారు. ప్రతి తరగతి గదికి ఒక పెద్ద "
        "డిజిటల్ స్క్రీన్ అందించబడిందా? ఉపాధ్యాయులు ఈ కొత్త బోధనా పద్ధతులను "
        "ప్రత్యేక శిక్షణ తర్వాత నేర్చుకుంటున్నారు. విద్యార్థులు ఇప్పుడు చాలా "
        "ఇష్టంగా చదువుతున్నారని గమనించారు."
    ),
    (
        "పాత పట్టణంలో ఒక చిన్న బేకరీ తన బట్టిని కొత్త ఇటుకలతో పూర్తిగా "
        "నిర్మించుకుంది. యజమానురాలు ఈ పద్ధతిని ఎదుట ఉన్న వృద్ధ కంట్రాక్టరు నుండి "
        "నేర్చుకుందని చెప్పారు. రొట్టె ఉత్పత్తి రెట్టింపు అయింది కానీ దుకాణం మాత్రం "
        "విస్తరించలేదు. ఉదయం సూర్యోదయానికి ముందే కస్టమర్లు బట్టలో నిలబడతారు."
    ),
]


def telugu_transcript() -> list[Segment]:
    """Telugu-script transcript: four stories, word-timed, script-pure."""
    return build_transcript(TELUGU_STORIES)


# ── long-form content (20-40 minute scale) ───────────────────────────────────

_CITIES = [
    "Hyderabad", "Visakhapatnam", "Warangal", "Vijayawada", "Guntur",
    "Tirupati", "Karimnagar", "Khammam", "Nellore", "Kurnool",
    "Rajahmundry", "Nizamabad",
]

_LONG_TOPICS = [
    ("urban transport", "buses", "bus", "commute"),
    ("agriculture", "paddy farms", "farm", "harvest"),
    ("renewable energy", "solar arrays", "array", "supply"),
    ("school education", "classrooms", "classroom", "lessons"),
    ("primary healthcare", "clinics", "clinic", "checkups"),
    ("handloom crafts", "weaving units", "unit", "orders"),
    ("irrigation", "canal reaches", "reach", "flow"),
    ("fishing harbours", "landing centres", "centre", "landings"),
]

_FRAMES = [
    "{city} commissioned {num} {noun} as part of the {topic} programme.",
    "Each {singular} now serves {num} households across the nearby villages.",
    "Crews trained for {num} weeks before the {noun} opened to the public.",
    "Officials recorded a {num} percent gain over the previous financial year.",
    "Residents say the {noun} changed their daily routine in practical ways.",
]


def long_stories(minutes: float = 25.0, *, episodes: int | None = None) -> list[str]:
    """Programmatic long-form transcript of roughly ``minutes`` length.

    Eight rotating topics fill episodes of five sentences each; every episode
    after the first opens with a ``Part N.`` section marker so the fixture
    exercises boilerplate detection and hard story boundaries, not just
    lexical drift. Vocabulary rotates with the episode index so each episode
    is a distinct story and the segmentation has real work to do.
    """
    if episodes is None:
        words_total = minutes * 60.0 * WORDS_PER_SECOND
        sentences_per_episode = len(_LONG_TOPICS) * len(_FRAMES)
        # ~11 words per generated sentence, plus the section marker.
        episodes = max(2, round(words_total / (sentences_per_episode * 11)))
    stories: list[str] = []
    for ep in range(episodes):
        parts = [f"Part {ep + 1}."] if ep else []
        for ti, (topic, noun, singular, _verb) in enumerate(_LONG_TOPICS):
            for fi in range(len(_FRAMES)):
                city = _CITIES[(ep * 3 + ti + fi) % len(_CITIES)]
                num = 3 + ((ep * 7 + ti * 5 + fi * 2) % 38)
                parts.append(
                    _FRAMES[fi].format(
                        city=city, num=num, noun=noun,
                        singular=singular, topic=topic,
                    )
                )
        stories.append(" ".join(parts))
    return stories


def long_transcript(minutes: float = 25.0) -> list[Segment]:
    return build_transcript(long_stories(minutes))


# ── synthetic media ───────────────────────────────────────────────────────────


def synthetic_media(
    path: Path,
    duration: float,
    *,
    size: tuple[int, int] = (320, 180),
    fps: int = 6,
) -> Path:
    """A landscape 16:9 mp4 with a continuous tone track.

    The QA gate requires landscape video plus an audio stream on every output
    and a non-silent tail, so the fixture media carries tone for its whole
    length. Deterministic: same input path and duration, same bytes modulo
    encoder metadata.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x1b3a6b:s={w}x{h}:r={fps}:d={duration:.2f}",
            "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k",
            "-shortest", "-movflags", "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def media_for(transcript: list[Segment], path: Path, *, margin: float = 8.0, **kw) -> Path:
    """Media long enough to cover every clip in ``transcript``."""
    total = max((s.end for s in transcript), default=0.0)
    return synthetic_media(path, total + margin, **kw)


# ── pipeline runner ──────────────────────────────────────────────────────────


@contextlib.contextmanager
def patched_pipeline(media: Path, transcript: list[Segment], language: str = "en"):
    """Run the real :func:`selekyt.pipeline.run` body against fixture inputs.

    Only the two network boundaries (download + transcription) are replaced;
    every editorial, caption, pack, QA and render stage is the real code.
    Restores the module on exit so a failure cannot leak a stub into another
    test in the same process.
    """
    import selekyt.pipeline as pipeline_mod

    real_download = pipeline_mod.download
    real_transcribe = pipeline_mod.transcribe_auto
    pipeline_mod.download = lambda source, dest, cookies=None: (
        media, {"title": "synthetic fixture clip"},
    )
    pipeline_mod.transcribe_auto = lambda media_arg, **kw: (
        transcript,
        language,
        {
            "engine": "fixture",
            "model": "fixture",
            "word_timestamps": "fixture-generated",
            "fallback_reason": "",
        },
    )
    try:
        yield pipeline_mod
    finally:
        pipeline_mod.download = real_download
        pipeline_mod.transcribe_auto = real_transcribe


def run_fixture_pipeline(
    out_root: Path,
    transcript: list[Segment],
    media: Path,
    *,
    language: str = "en",
    limit: int = 4,
    **kw,
) -> Path:
    """Full pipeline run over fixture inputs; returns the project directory."""
    with patched_pipeline(media, transcript, language) as pipeline_mod:
        return pipeline_mod.run(
            "https://fixture.example.invalid/v", out_root,
            model="fixture", language=language, limit=limit, **kw,
        )


__all__ = [
    "ENGLISH_STORIES",
    "HAVE_FFMPEG",
    "TELUGU_STORIES",
    "build_transcript",
    "english_transcript",
    "long_stories",
    "long_transcript",
    "media_for",
    "patched_pipeline",
    "run_fixture_pipeline",
    "segment",
    "skip_no_ffmpeg",
    "synthetic_media",
    "telugu_transcript",
    "timed_words",
    "transcript_json",
    "word",
    "write_transcript_json",
]
