"""Story threads, dramatic beats and B-roll opportunity markers.

The question this module answers is the one a professional editor asks first,
before touching a single cut: *what are the stories in this recording, and
where does each one pay off?* A transcript of two hours is not 400 scored
sentences — it is a handful of narrative arcs that are set up, complicated and
resolved, and the editor's job is to preserve the setup-to-payoff shape of each
one, because a clip that contains a payoff without its setup lands flat and a
clip that contains a setup without its payoff is a tease nobody asked for.

What is inferable, and what is not
---------------------------------
This layer works only from evidence the transcript itself carries. It has no
world model and no memory of anything the speaker did not say, so it infers
narrative structure from the *linguistic markers* that actually create it in
speech:

- **question -> answer**: an interrogative passage followed by a passage that
  addresses it (shared subject + no question of its own).
- **problem -> solution**: a passage carrying trouble vocabulary followed by
  one carrying resolution vocabulary on the same subject.
- **cause -> effect**: a later passage opening with an explicit result
  connective ("so", "that's why", "అందుకే", "எனவே") on the same subject. The
  connective is the strongest available evidence for causality; without one,
  temporal order alone is *not* treated as causation.
- **callback**: a subject dropped and picked back up after unrelated material —
  the return is a payoff of the earlier setup, and the gap is what makes it one.
- **contrast turn**: a passage opening with a reversal marker ("but",
  "however", "కానీ") on the same subject — a turn, not a new story.

It does not guess at themes the speaker never named, does not infer a character
arc, and does not rank stories by predicted popularity — that would be
manufacturing context, which the product refuses to do. Every judgement carries
the cue that licensed it, quoted verbatim, and a confidence proportional to how
explicit that cue is.

Dramatic beats
--------------
Each unit is annotated with a beat kind (setup / tension / turn / revelation /
resolution / continuation), a polarity where the lexicon supports one, and an
intensity on a **saturation scale** (`1 - 1/(1+c)`) rather than a per-corpus
normalisation: an intensity of 0.6 must mean the same thing in a 3-minute and a
3-hour transcript, so it is derived from cue counts alone. A within-corpus
percentile is reported alongside it for relative comparison, clearly labelled as
relative. This is a lexical-prosodic estimate, not mind reading — the lexicon is
a working editor's cue list, not a sentiment model, and the reason field says
which cues fired.

B-roll markers
--------------
Only emitted where the transcript itself points at something visual: an
explicit deictic ("as you can see", "right here"), a demonstration verb, a
visualisable analogy ("imagine", "picture this"), or an emphatic pause before a
high-intensity statement (a reaction-shot opportunity). No shot is ever
invented; an empty list is the honest answer for a transcript with none of
these, and each marker quotes the cue it rests on.

Deterministic and pure: identical input -> identical output; no network, no ML,
no wall clock, no global state.

Complexity: linking is O(U * W) with W the forward window (a bounded constant),
beats are O(U), B-roll is O(sentences). Memory is O(U) of index structures; the
only text retained is the bounded evidence quotations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.lexical import (
    containment,
    content_tokens,
    df_table,
    idf_weights,
    quote,
    signature_terms,
)
from engine.story_units import Sentence

# ── cue lexicons ───────────────────────────────────────────────────────────
# A working editor's list of the markers that actually create narrative
# structure in speech, for English and the Indic scripts this product serves.
# These are deliberately *cue* lists, not sentiment lexicons: each entry is a
# word or phrase whose presence in the text is itself the event (a question is
# a question in any script), not a proxy for an inferred emotion.

_RESULT_OPEN = (
    "so\\b", "that'?s why", "that is why", "therefore", "thus", "hence",
    "as a result", "which is why", "consequently",
    # Telugu
    "అందుకే", "అందువల్ల", "కాబట్టి", "అలాగే", "ఫలితంగా",
    # Hindi
    "इसलिए", "तो", "अतः", "परिणामस्वरूप", "अंततः",
    # Tamil
    "எனவே", "அதனால்", "அப்படியால்", "விளைவாக",
    # Kannada
    "ಆದ್ದರಿಂದ", "ಆಮೇಲೆ", "ಪರಿಣಾಮವಾಗಿ",
    # Malayalam
    "അതിനാൽ", "അപ്പോൾ", "ഫലമായി",
    # Bengali
    "তাই", "অতএব", "সুতরাং", "ফলে",
)
_CAUSE_OPEN = (
    "because", "since", "ఎందుకంటే", "కారణంగా", "क्योंकि", "காரணம்",
    "ಕಾರಣ", "കാരണം", "কারণ",
)
_CONTRAST_OPEN = (
    "but\\b", "however", "yet\\b", "actually", "instead", "rather",
    "on the other hand", "nevertheless", "the truth is",
    "కానీ", "అయితే", "మాత్రమే", "నిజానికి",
    "लेकिन", "मगर", "परंतु", "पर\\s", "असल में",
    "ஆனால்", "மாறாக", "உண்மையில்",
    "ಆದರೆ", "ನಿಜವಾಗಲು",
    "എന്നാൽ", "പക്ഷേ", "യഥാർത്ഥത്തിൽ",
    "কিন্তু", "অবশ্য", "সত্যি বলতে",
)
_PROBLEM = (
    "problem", "issue", "failure", "failed", "fail\\b", "crash", "risk",
    "danger", "wrong", "mistake", "crisis", "threat", "broke", "broken",
    "stuck", "struggle", "difficulty", "obstacle", "setback",
    "సమస్య", "లోపం", "ప్రమాదం", "వైఫల్యం", "తప్పు", "ఇబ్బంది",
    "समस्या", "दोष", "खतरा", "असफलता", "गलती", "मुश्किल",
    "சிக்கல்", "தவறு", "ஆபத்து", "தோல்வி", "இடையூறு",
    "ಸಮಸ್ಯೆ", "ದೋಷ", "ಅಪಾಯ", "ವೈಫಲ್ಯ", "ತಪ್ಪು",
    "പ്രശ്നം", "തെറ്റ്", "അപകടം", "പരാജയം", "ബുദ്ധിമുട്ട്",
    "সমস্যা", "বিপদ", "ভুল", "ব্যর্থতা", "অসুবিধা",
)
_SOLUTION = (
    "solution", "solved", "solve", "fixed", "fix\\b", "answer", "resolved",
    "workaround", "result", "recovered", "restored", "repaired",
    "పరిష్కారం", "సమాధానం", "ఫలితం", "సరిచేసాం",
    "हल", "समाधान", "उत्तर", "मरम्मत",
    "தீர்வு", "விடை", "சரிசெய்யப்பட்டது",
    "ಪರಿಹಾರ", "ಉತ್ತರ", "ಸರಿಪಡಿಸಲಾಗಿದೆ",
    "പരിഹാരം", "ഉത്തരം", "ശരിയാക്കി",
    "সমাধান", "উত্তর", "মেরামত",
)
_REVELATION = (
    "it turned out", "turned out", "the truth is", "surprisingly",
    "unexpectedly", "what really happened", "nobody (?:realis|realiz)ed",
    "nobody knew", "the secret", "plot twist", "here'?s the thing",
    "నిజానికి", "అసలు విషయం", "ఆశ్చర్యకరంగా", "ఎవరికీ తెలియదు",
    "सच तो यह है", "असल में", "आश्चर्यजनक रूप से", "किसी को नहीं पता था",
    "உண்மையில்", "அதிர்ச்சியளிக்கும் வகையில்", "யாருக்கும் தெரியாது",
    "ವಾಸ್ತವವಾಗಿ", "ಆಶ್ಚರ್ಯಕರವಾಗಿ",
    "യഥാർത്ഥത്തിൽ", "അതിശയകരമായി",
    "সত্যি বলতে", "অবাক করার বিষয়",
)
_INTENSIFIER = (
    "never", "always", "everything", "nothing", "everyone", "nobody",
    "the most", "the worst", "the best", "completely", "entirely",
    "absolutely", "totally", "forever", "at all costs",
    "ఎప్పటికీ", "ఎల్లప్పుడూ", "అందరూ", "ఏమీ", "ఎవరూ", "పూర్తిగా",
    "हमेशा", "कभी नहीं", "सबसे", "सब कुछ", "कोई नहीं", "पूरी तरह",
    "எப்போதும்", "ஒருபோதும்", "அனைவரும்", "எதுவும்", "யாரும்", "முழுமையாக",
    "ಯಾವಾಗಲೂ", "ಎಂದಿಗೂ", "ಎಲ್ಲರೂ", "ಏನೂ", "ಯಾರೂ", "ಸಂಪೂರ್ಣವಾಗಿ",
    "എപ്പോഴും", "ഒരിക്കലും", "എല്ലാവരും", "ഒന്നുമില്ല", "ആരുമില്ല", "പൂർണ്ണമായും",
    "সবসময়", "কখনোই", "সবাই", "কিছুই", "কেউই", "সম্পূর্ণরূপে",
)
_POSITIVE = (
    "success", "successful", "worked", "launched", "celebrated", "breakthrough",
    "happy", "proud", "amazing", "great", "wonderful", "triumph", "relief",
    "విజయం", "సంతోషం", "గొప్ప", "అద్భుతం", "సంతృప్తి",
    "सफलता", "खुश", "अद्भुत", "गर्व", "महान",
    "வெற்றி", "மகிழ்ச்சி", "அற்புதம்", "பெருமை",
    "ಯಶಸ್ಸು", "ಸಂತೋಷ", "ಅದ್ಭುತ", "ಹೆಮೆ",
    "വിജയം", "സന്തോഷം", "അത്ഭുതം", "അഭിമാനം",
    "সাফল্য", "খুশি", "অদ্ভুত", "গর্ব",
)
_NEGATIVE = (
    "disaster", "terrible", "awful", "tragic", "heartbreaking", "devastating",
    "furious", "angry", "afraid", "scared", "grief", "loss",
    "దారుణం", "భయంకరం", "దుఃఖం", "కోపం", "భయం",
    "भयानक", "त्रासदी", "दुख", "गुस्सा", "डर",
    "பயங்கரமான", "சோகம்", "கோபம்", "பயம்",
    "ಭಯಾನಕ", "ದುಃಖ", "ಕೋಪ", "ಭಯ",
    "ഭയാനകമായ", "ദുഃഖം", "ദേഷ്യം", "ഭയം",
    "ভয়াবহ", "দুঃখজনক", "রাগ", "ভয়",
)
_QUESTION_OPEN = (
    "why", "how", "what", "when", "where", "who", "which", "whose",
    "ఎందుకు", "ఎలా", "ఎప్పుడు", "ఎక్కడ", "ఎవరు", "ఏమిటి",
    "क्यों", "कैसे", "क्या", "कब", "कहाँ", "कौन",
    "ஏன்", "எப்படி", "என்ன", "எப்போது", "எங்கே", "யார்",
    "ಏಕೆ", "ಹೇಗೆ", "ಯಾವಾಗ", "ಎಲ್ಲಿ", "ಯಾರು",
    "എന്തുകൊണ്ട്", "എങ്ങനെ", "എപ്പോൾ", "എവിടെ", "ആരാണ്",
    "কেন", "কীভাবে", "কখন", "কোথায়", "কে",
)
_DEICTIC = (
    "as you can see", "you can see", "look at this", "right here",
    "this one", "see here", "over here", "on the screen", "behind me",
    "ఇలా", "ఇక్కడ", "చూడండి", "ఇదిగో",
    "देखो", "यहाँ", "ऐसे", "सामने",
    "இதைப் பாருங்கள்", "இங்கே", "இப்படி",
    "ಇಲ್ಲಿ ನೋಡಿ", "ಇಲ್ಲೇ", "ಹೀಗೆ",
    "ഇവിടെ നോക്കൂ", "ഇവിടെ", "ഇങ്ങനെ",
    "এখানে দেখুন", "এটা দেখুন", "ঐ যে",
)
_DEMO = (
    "let me show you", "i'?ll show you", "let'?s look at", "pull(?:ing)? up",
    "bring(?:ing)? up", "i want to show", "take a look at", "zoom in",
    "చూపిస్తాను", "చూడండి", "दिखाता हूँ", "देखिए",
    "காட்டுகிறேன்", "பாருங்கள்", "ತೋರಿಸುತ್ತೇನೆ", "കാണിക്കാം",
    "দেখাচ্ছি",
)
_ILLUSTRATION = (
    "for example", "for instance", "imagine", "picture this", "think of it",
    "think about", "consider", "suppose", "to illustrate", "as an example",
    "ఉదాహరణకు", "ఊహించండి", "उदाहरण के लिए", "कल्पना कीजिए",
    "உதாரணமாக", "கற்பனை செய்து பாருங்கள்",
    "ಉದಾಹರಣೆಗೆ", "ಊಹಿಸಿ", "ഉദാഹരണത്തിന്", "സങ്കൽപ്പിക്കൂ",
    "উদাহরণস্বরূপ", "কল্পনা করুন",
)

_TERMINAL = set(".?!।॥？！")


def _compile(phrases: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"(?<![\w])(" + "|".join(phrases) + r")(?![\w])", re.IGNORECASE | re.UNICODE)


RE_RESULT = _compile(_RESULT_OPEN)
RE_CAUSE = _compile(_CAUSE_OPEN)
RE_CONTRAST = _compile(_CONTRAST_OPEN)
RE_PROBLEM = _compile(_PROBLEM)
RE_SOLUTION = _compile(_SOLUTION)
RE_REVELATION = _compile(_REVELATION)
RE_INTENSIFIER = _compile(_INTENSIFIER)
RE_POSITIVE = _compile(_POSITIVE)
RE_NEGATIVE = _compile(_NEGATIVE)
RE_QUESTION_OPEN = _compile(_QUESTION_OPEN)
RE_DEICTIC = _compile(_DEICTIC)
RE_DEMO = _compile(_DEMO)
RE_ILLUSTRATION = _compile(_ILLUSTRATION)

_TERMINAL_PUNCT = set(".?!।॥？。")


def _ends_with_question(text: str) -> bool:
    return text.strip().endswith("?")


def _is_interrogative(text: str) -> bool:
    """A question mark is decisive; a question opener is a strong cue."""
    t = text.strip()
    if not t:
        return False
    if t[-1] == "?":
        return True
    return bool(RE_QUESTION_OPEN.match(t))


def _first_token_lower(text: str) -> str:
    t = text.strip().split()
    if not t:
        return ""
    return "".join(ch for ch in t[0].lower() if ch.isalnum()).strip()


# ── data contract ──────────────────────────────────────────────────────────


@dataclass
class SetupPayoffLink:
    """One setup-to-payoff relation between two units, with its evidence.

    Timecodes are source seconds, so an editor can jump to both halves of the
    relation and verify it holds.
    """

    setup_unit: int
    payoff_unit: int
    kind: str                  # question_answer | problem_solution | causal | callback | contrast_turn
    strength: float            # 0..1, how explicit the evidence is
    setup_start: float
    setup_end: float
    payoff_start: float
    payoff_end: float
    confidence: float
    reason: str
    evidence: list[str] = field(default_factory=list)  # [setup quote, payoff quote]

    @property
    def start(self) -> float:
        """Source-second span covering the whole relation (setup through payoff)."""
        return min(self.setup_start, self.payoff_start)

    @property
    def end(self) -> float:
        return max(self.setup_end, self.payoff_end)


@dataclass
class DramaticBeat:
    """A unit's dramatic function: what it does in the story, and how strongly.

    `intensity` is on a saturation scale (0..1) independent of corpus length;
    `percentile` is its rank *within this corpus*. `cues` are the verbatim
    phrases that fired, so the judgement is auditable.
    """

    unit_index: int
    start: float
    end: float
    kind: str                  # setup | tension | turn | revelation | resolution | continuation
    polarity: str              # positive | negative | neutral
    intensity: float
    percentile: float
    is_thread_peak: bool
    is_corpus_peak: bool
    cues: list[str]
    thread_id: int | None
    confidence: float
    reason: str


@dataclass
class BrollMarker:
    """A point where the transcript itself points at something visual.

    Never invented: the `cue` is quoted verbatim from the source, and the
    marker's span is the cue's own sentence timecode.
    """

    start: float
    end: float
    kind: str                  # demonstration | deictic | illustration | reaction
    cue: str                   # verbatim phrase that licenses the marker
    unit_index: int
    confidence: float
    reason: str


@dataclass
class StoryThread:
    """An ordered narrative chain: setup, complication, payoff."""

    thread_id: int
    title: str                 # signature terms, verbatim — never a synthesised phrase
    terms: list[str]
    unit_indices: list[int]    # chronological chain
    links: list[SetupPayoffLink]
    beat_indices: list[int]
    start: float
    end: float
    arc: str                   # kind sequence, e.g. "setup → tension → resolution"
    span_share: float
    confidence: float
    reason: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class ThreadResult:
    threads: list[StoryThread]
    beats: list[DramaticBeat]
    links: list[SetupPayoffLink]
    broll: list[BrollMarker]
    unit_thread: list[int | None]
    unit_beat: list[int | None]
    note: str


# ── rhythm bridge ──────────────────────────────────────────────────────────


@dataclass
class UnitRhythmRef:
    """Prosodic per-unit signals the beat builder may use, or None throughout.

    Supplied by `engine.rhythm`; kept as a tiny standalone struct so this module
    does not import that one (rhythm has no dependency on narrative structure,
    and the dependency must stay one-way).
    """

    unit_index: int
    rush: float                # 0..1, how far above the corpus median speech rate
    pre_pause: float           # seconds of silence immediately before this unit
    emphatic_pause: float      # longest pause inside this unit, in seconds


# ── beat construction ──────────────────────────────────────────────────────


def _unit_cues(text: str) -> tuple[list[str], dict[str, int]]:
    """Verbatim cues found in a unit, plus a per-category count."""
    cues: list[str] = []
    counts: dict[str, int] = {}
    for name, rx in (
        ("revelation", RE_REVELATION),
        ("problem", RE_PROBLEM),
        ("solution", RE_SOLUTION),
        ("contrast", RE_CONTRAST),
        ("result", RE_RESULT),
        ("intensifier", RE_INTENSIFIER),
        ("positive", RE_POSITIVE),
        ("negative", RE_NEGATIVE),
    ):
        found = rx.findall(text)
        if found:
            counts[name] = len(found)
            cues.extend(str(f) for f in found[:4])  # bounded evidence
    return cues, counts


def _question_signal(unit_sentences: list[list[Sentence]]) -> tuple[bool, str]:
    """Does this unit ask something, and what does it ask (verbatim)?

    Takes the unit's sentence list (a list of lists, one per unit) so the caller
    cannot pass a single unit's sentences by mistake; the flat variant below is
    used where a sentence list is already in hand.
    """
    for sents in unit_sentences:
        for s in sents:
            if _is_interrogative(s.text):
                return True, quote(s.text, 110)
    return False, ""


def _flat_question_signal(sentences: list[Sentence]) -> tuple[bool, str]:
    """Same question over one unit's already-flattened sentence list."""
    for s in sentences:
        if _is_interrogative(s.text):
            return True, quote(s.text, 110)
    return False, ""


def _raw_intensity(counts: dict[str, int], is_question: bool,
                   rhythm: UnitRhythmRef | None) -> float:
    """Unbounded cue weight; saturated by the caller.

    Weights reflect how *decisive* each cue is for dramatic charge, not how
    often it appears: a revelation marker ('it turned out') is worth more than
    a generic intensifier, and an actual question mark is worth more than a
    question-word opener, which can open a statement in some languages.
    """
    c = 0.0
    c += 0.45 * counts.get("revelation", 0)
    c += 0.35 * counts.get("problem", 0)
    c += 0.35 * (1.0 if is_question else 0.0)
    c += 0.30 * counts.get("solution", 0)
    c += 0.25 * counts.get("contrast", 0)
    c += 0.25 * counts.get("result", 0)
    c += 0.15 * min(counts.get("intensifier", 0), 3)
    # Prosody is real evidence for emphasis — a rush of words or a held pause
    # before a statement is how a speaker marks what matters — but it is weaker
    # than the lexical cues and only available with word timestamps.
    if rhythm is not None:
        c += 0.15 * rhythm.rush
        if rhythm.emphatic_pause >= 0.9:
            c += 0.15
    return c


def _polarity(counts: dict[str, int]) -> str:
    pos = counts.get("positive", 0) + (counts.get("solution", 0) > 0)
    neg = counts.get("negative", 0) + (counts.get("problem", 0) > 0)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def build_beats(
    unit_texts: list[str],
    unit_sentences: list[list[Sentence]],
    unit_starts: list[float],
    unit_ends: list[float],
    *,
    rhythm: list[UnitRhythmRef] | None = None,
    thread_of_unit: dict[int, int] | None = None,
) -> list[DramaticBeat]:
    """Annotate every unit with its dramatic function and intensity.

    Complexity O(U). Intensity is corpus-length-independent (saturation scale);
    the within-corpus percentile is computed in one sort.
    """
    n = len(unit_texts)
    if n == 0:
        return []
    rhythm_by_unit = {r.unit_index: r for r in rhythm} if rhythm else {}
    raw: list[float] = []
    meta: list[tuple[list[str], dict[str, int], bool, str]] = []
    for i, text in enumerate(unit_texts):
        cues, counts = _unit_cues(text)
        is_q, q_text = (False, "")
        if i < len(unit_sentences):
            is_q, q_text = _flat_question_signal(unit_sentences[i])
        r = rhythm_by_unit.get(i)
        intensity = _raw_intensity(counts, is_q, r)
        raw.append(intensity)
        meta.append((cues, counts, is_q, q_text))

    order = sorted(range(n), key=lambda i: raw[i])
    percentile = [0.0] * n
    for rank, i in enumerate(order):
        percentile[i] = round(rank / max(1, n - 1), 3) if n > 1 else 1.0

    corpus_peak = max(range(n), key=lambda i: (raw[i], -i)) if n else 0
    beats: list[DramaticBeat] = []
    for i, text in enumerate(unit_texts):
        cues, counts, is_q, q_text = meta[i]
        # Kind is assigned by the most decisive cue present. Revelation beats
        # outrank tension because a revelation *resolves* the tension; a bare
        # resolution cue with nothing before it in the thread is still a
        # resolution, not an anticlimax.
        if counts.get("revelation", 0):
            kind = "revelation"
        elif is_q or counts.get("problem", 0):
            kind = "tension"
        elif counts.get("contrast", 0):
            kind = "turn"
        elif counts.get("solution", 0) or counts.get("result", 0):
            kind = "resolution"
        else:
            kind = "continuation"
        polarity = _polarity(counts)
        tid = thread_of_unit.get(i) if thread_of_unit else None
        cues_out = [c for c in cues][:6]
        beat = DramaticBeat(
            unit_index=i,
            start=unit_starts[i],
            end=unit_ends[i],
            kind=kind,
            polarity=polarity,
            intensity=round(1.0 - 1.0 / (1.0 + raw[i]), 3),
            percentile=percentile[i],
            is_thread_peak=False,   # set by build_threads once chains are known
            is_corpus_peak=(i == corpus_peak),
            cues=cues_out,
            thread_id=tid,
            confidence=round(min(0.95, 0.45 + 0.5 * (1.0 - 1.0 / (1.0 + raw[i]))), 3),
            reason=_beat_reason(kind, polarity, counts, is_q, q_text, cues_out),
        )
        beats.append(beat)
    return beats


def _beat_reason(kind: str, polarity: str, counts: dict[str, int], is_q: bool,
                 q_text: str, cues: list[str]) -> str:
    parts = [f"{kind} beat"]
    if is_q:
        parts.append(f"opens a question (“{q_text}”)" if q_text else "opens a question")
    for name in ("revelation", "problem", "solution", "contrast", "result"):
        if counts.get(name):
            parts.append(f"{counts[name]}× {name} cue")
    if cues:
        parts.append("cues: " + ", ".join(cues[:3]))
    if polarity != "neutral":
        parts.append(f"polarity {polarity} from lexicon matches")
    else:
        parts.append("polarity neutral: no lexicon match, not guessed")
    return "; ".join(parts)


# ── thread construction ────────────────────────────────────────────────────


def _link_candidates(
    i: int, j: int, texts: list[str], bags: list[set[str]], idf: dict[str, float],
    unit_questions: list[bool], unit_openers: dict[str, list[int]],
    topic_of_unit: list[int], min_overlap: float = 0.15,
) -> tuple[str, float, str]:
    """Best setup->payoff relation (i -> j), or ("", 0.0, "").

    Strength encodes how *explicit* the evidence is: a result connective is a
    speaker asserting causation, so it outranks a lexical coincidence across a
    gap, which is at best a plausible callback.
    """
    if i == j:
        return "", 0.0, ""
    # Plain containment: same scale as the story-unit seam decisions, and
    # stable in small corpora where idf compresses every score to noise.
    overlap = containment(bags[i], bags[j])
    ti, tj = topic_of_unit[i], topic_of_unit[j]
    same_topic = ti >= 0 and ti == tj
    separated = abs(j - i) >= 3

    # Explicit cause -> effect: the later unit says "so" / "that's why" about
    # the earlier one's subject.
    if j in unit_openers["result"] and overlap >= min_overlap:
        return "causal", 0.75, (
            f"payoff opens with a result connective on the same subject "
            f"(overlap {overlap:.2f}) — the speaker asserts the causation"
        )
    # Question -> answer: a real interrogative followed by a same-subject
    # passage that asks nothing of its own.
    if unit_questions[i] and not unit_questions[j] and overlap >= min_overlap:
        return "question_answer", 0.65, (
            f"setup asks, payoff answers on the same subject "
            f"(overlap {overlap:.2f})"
        )
    # Problem -> solution on one subject.
    if i in unit_openers["problem"] and j in unit_openers["solution"] and overlap >= min_overlap:
        return "problem_solution", 0.60, (
            f"trouble vocabulary in the setup, resolution vocabulary in the "
            f"payoff, same subject (overlap {overlap:.2f})"
        )
    # Reversal on the same subject: a turn, not a new story.
    if j in unit_openers["contrast"] and overlap >= min_overlap + 0.05:
        return "contrast_turn", 0.55, (
            f"payoff opens with a reversal marker on the same subject "
            f"(overlap {overlap:.2f})"
        )
    # Callback: the subject went away and came back. Same topic across a gap is
    # the corpus-level signal a sentence view cannot see.
    if same_topic and separated:
        return "callback", 0.45, (
            f"subject returns after {abs(j - i) - 1} intervening unit(s) — "
            f"the return pays off the earlier setup"
        )
    return "", 0.0, ""


def build_threads(
    unit_texts: list[str],
    unit_sentences: list[list[Sentence]],
    unit_starts: list[float],
    unit_ends: list[float],
    *,
    duration: float,
    topic_of_unit: list[int] | None = None,
    rhythm: list[UnitRhythmRef] | None = None,
    window_units: int = 6,
    window_seconds: float = 150.0,
    min_strength: float = 0.40,
) -> ThreadResult:
    """Chain units into narrative threads with setup->payoff links.

    Args:
        window_units / window_seconds: how far forward a setup may reach for its
            payoff. Bounded by design — a payoff three hours after its setup is
            not inferable as a relation, and an unbounded search would be O(U²).
        min_strength: the evidence bar below which two same-subject passages are
            treated as unrelated occurrences rather than a setup and payoff.

    Complexity O(U * W) with W = window_units; memory O(U).
    """
    n = len(unit_texts)
    if n == 0:
        return ThreadResult([], [], [], [], [], [], "no units to thread")
    if n == 1:
        beats = build_beats(unit_texts, unit_sentences, unit_starts, unit_ends,
                            rhythm=rhythm)
        return ThreadResult(
            threads=[], beats=beats, links=[], broll=[],
            unit_thread=[None], unit_beat=[0],
            note="a single unit cannot contain a setup-to-payoff relation; "
                 "beat annotation only",
        )

    bags = [content_tokens(t) for t in unit_texts]
    idf = idf_weights(df_table(unit_texts), n)
    topics = list(topic_of_unit) if topic_of_unit is not None else [-1] * n

    unit_questions = [
        any(_is_interrogative(s.text) for s in sents)
        for sents in unit_sentences
    ]
    unit_openers: dict[str, list[int]] = {"result": [], "problem": [],
                                          "solution": [], "contrast": []}
    for i, text in enumerate(unit_texts):
        if RE_RESULT.search(text[:160]):
            unit_openers["result"].append(i)
        # Problem/solution are counted anywhere in the unit, but a *leading*
        # contrast/result is what marks a turn — check the opener separately.
        if RE_CONTRAST.search(text[:160]):
            unit_openers["contrast"].append(i)
        if RE_PROBLEM.search(text):
            unit_openers["problem"].append(i)
        if RE_SOLUTION.search(text):
            unit_openers["solution"].append(i)

    # Each unit claims its best forward link; each target honours only the
    # strongest claim. This keeps chains simple disjoint paths — a unit has at
    # most one predecessor and one successor — which is what an editor can
    # actually verify against the source.
    claims: dict[int, tuple[float, int, str, str]] = {}  # j -> (strength, i, kind, reason)
    for i in range(n):
        best = (0.0, -1, "", "")
        for j in range(i + 1, min(n, i + 1 + window_units)):
            if unit_starts[j] - unit_ends[i] > window_seconds:
                break
            kind, strength, reason = _link_candidates(
                i, j, unit_texts, bags, idf, unit_questions, unit_openers, topics
            )
            if strength > best[0]:
                best = (strength, j, kind, reason)
        if best[0] < min_strength or best[1] < 0:
            continue
        j = best[1]
        prev = claims.get(j)
        if prev is None or best[0] > prev[0]:
            claims[j] = (best[0], i, best[2], best[3])

    links: list[SetupPayoffLink] = []
    successor_of: dict[int, int] = {}
    predecessor_of: dict[int, int] = {}
    for j, (strength, i, kind, reason) in sorted(claims.items()):
        successor_of[i] = j
        predecessor_of[j] = i
        links.append(
            SetupPayoffLink(
                setup_unit=i, payoff_unit=j, kind=kind, strength=round(strength, 3),
                setup_start=unit_starts[i], setup_end=unit_ends[i],
                payoff_start=unit_starts[j], payoff_end=unit_ends[j],
                confidence=round(min(0.95, strength), 3),
                reason=reason,
                evidence=[quote(unit_texts[i], 110), quote(unit_texts[j], 110)],
            )
        )

    # Chains start at units nobody claims.
    starts = [i for i in range(n) if i not in predecessor_of]
    threads: list[StoryThread] = []
    unit_thread: list[int | None] = [None] * n
    for tid, start in enumerate(starts):
        chain = [start]
        cur = start
        while cur in successor_of:
            nxt = successor_of[cur]
            if nxt in chain:      # defensive: never loop, even on hostile input
                break
            chain.append(nxt)
            cur = nxt
        for u in chain:
            unit_thread[u] = tid
        chain_links = [lk for lk in links if lk.setup_unit in set(chain)
                       and lk.payoff_unit in set(chain)]
        lo = unit_starts[chain[0]]
        hi = unit_ends[chain[-1]]
        share = (hi - lo) / duration if duration > 0 else 0.0
        terms = signature_terms([bags[u] for u in chain], idf, limit=4)
        mean_strength = (sum(lk.strength for lk in chain_links) / len(chain_links)
                         if chain_links else 0.0)
        if chain_links:
            confidence = round(min(0.95, 0.45 + 0.5 * mean_strength), 3)
            reason = (
                f"chain of {len(chain)} unit(s) joined by {len(chain_links)} "
                f"setup->payoff link(s) (mean evidence strength {mean_strength:.2f}); "
                f"strongest: {max(chain_links, key=lambda l: l.strength).kind}"
            )
        else:
            # A chain head with no forward link: a passage whose payoff, if any,
            # is not inferable. Reported as such rather than guessed.
            confidence = 0.25
            reason = (
                "no setup->payoff relation could be inferred from this unit "
                "within the evidence window; treated as a standalone passage"
            )
        threads.append(
            StoryThread(
                thread_id=tid,
                title=", ".join(terms[:3]) if terms else "(no signature terms)",
                terms=terms,
                unit_indices=chain,
                links=chain_links,
                beat_indices=[],   # filled after beats are built
                start=lo, end=hi,
                arc="",            # filled after beats are built
                span_share=round(share, 4),
                confidence=confidence,
                reason=reason,
                evidence=[quote(unit_texts[u], 110) for u in chain[:3]],
            )
        )

    thread_of_unit = {
        u: tid for tid, thread in enumerate(threads) for u in thread.unit_indices
    }
    beats = build_beats(
        unit_texts, unit_sentences, unit_starts, unit_ends,
        rhythm=rhythm, thread_of_unit=thread_of_unit,
    )
    unit_beat: list[int | None] = [None] * n
    for bi, b in enumerate(beats):
        unit_beat[b.unit_index] = bi

    # Arc label and thread peaks: the highest-intensity beat in each chain, and
    # the shape of the chain as a sequence of beat kinds.
    for t in threads:
        t.beat_indices = [unit_beat[u] for u in t.unit_indices
                          if unit_beat[u] is not None]
        kinds = []
        for bi in t.beat_indices:
            k = beats[bi].kind
            if not kinds or kinds[-1] != k:
                kinds.append(k)
        t.arc = " → ".join(kinds) or "continuation"
        if t.beat_indices:
            peak_bi = max(t.beat_indices, key=lambda bi: (beats[bi].intensity, -bi))
            beats[peak_bi].is_thread_peak = True

    orphan = sum(1 for x in unit_thread if x is None)
    linked = sum(1 for t in threads if t.links)
    note = (
        f"{len(threads)} chain(s) over {n} unit(s); {linked} carry at least one "
        f"setup->payoff link; {orphan} unit(s) had no inferable relation and are "
        f"reported as standalone passages. {len(links)} link(s) total."
    )
    broll = mark_broll(unit_sentences, beats, rhythm)
    return ThreadResult(threads, beats, links, broll, unit_thread, unit_beat, note)


# ── B-roll markers ─────────────────────────────────────────────────────────


def mark_broll(
    unit_sentences: list[list[Sentence]],
    beats: list[DramaticBeat],
    rhythm: list[UnitRhythmRef] | None = None,
) -> list[BrollMarker]:
    """Mark points where the transcript points at something visual.

    Kinds and the evidence each requires:
      - ``demonstration``: the speaker says they will show something. Strongest
        case — the source is overtly a demo.
      - ``deictic``: a pointing phrase ("right here", "as you can see").
      - ``illustration``: a visualisable analogy ("imagine", "for example") —
        an opportunity for illustrative cutaway, but weaker, since nothing
        guarantees an image exists.
      - ``reaction``: a held pause before a high-intensity statement. This is
        the only timing-derived kind, and its confidence reflects that: a pause
        is suggestive, not a stage direction.

    An empty list is the correct result when none of these appear. No marker is
    ever placed on a passage without a verbatim cue.
    """
    markers: list[BrollMarker] = []
    rhythm_by_unit = {r.unit_index: r for r in rhythm} if rhythm else {}
    beat_by_unit = {b.unit_index: b for b in beats}

    for ui, sents in enumerate(unit_sentences):
        for s in sents:
            if not s.text.strip():
                continue
            demo = RE_DEMO.search(s.text)
            deictic = RE_DEICTIC.search(s.text)
            illus = RE_ILLUSTRATION.search(s.text)
            if demo:
                cue = str(demo.group(0))
                markers.append(BrollMarker(
                    start=s.start, end=s.end, kind="demonstration", cue=cue,
                    unit_index=ui, confidence=0.80,
                    reason=f"speaker announces a demonstration (“{quote(s.text, 80)}”)",
                ))
            elif deictic:
                cue = str(deictic.group(0))
                markers.append(BrollMarker(
                    start=s.start, end=s.end, kind="deictic", cue=cue,
                    unit_index=ui, confidence=0.70,
                    reason=f"pointing phrase in the transcript (“{quote(s.text, 80)}”)",
                ))
            if illus and not demo:
                cue = str(illus.group(0))
                markers.append(BrollMarker(
                    start=s.start, end=s.end, kind="illustration", cue=cue,
                    unit_index=ui, confidence=0.45,
                    reason="visualisable analogy in the transcript; an "
                           "illustrative cutaway is possible, not guaranteed",
                ))

    if rhythm_by_unit and beat_by_unit:
        # A held pause before the strongest statement in the neighbourhood is
        # where a cutaway to a reaction would land in a real edit. Requires word
        # timestamps (rhythm), so it degrades to absent when they are missing.
        for ui, r in rhythm_by_unit.items():
            b = beat_by_unit.get(ui)
            if b is None or b.intensity < 0.55 or r.pre_pause < 0.6:
                continue
            # The cue quotes the sentence that follows the pause when one is
            # available; otherwise the timing itself is the (only) evidence and
            # the cue says so instead of quoting text the speaker did not say.
            cue_text = ""
            if ui < len(unit_sentences):
                for s in unit_sentences[ui]:
                    if s.text.strip():
                        cue_text = quote(s.text, 80)
                        break
            cue = cue_text if cue_text else f"({r.pre_pause:.1f}s silence, no quotation)"
            markers.append(BrollMarker(
                start=max(0.0, b.start - r.pre_pause), end=b.end,
                kind="reaction", cue=cue,
                unit_index=ui, confidence=0.50,
                reason=f"{r.pre_pause:.1f}s held silence precedes a {b.kind} "
                       f"beat at intensity {b.intensity:.2f}; a reaction "
                       f"cutaway would land here — inferred from timing, not "
                       f"stated by the speaker",
            ))

    markers.sort(key=lambda m: (m.start, m.kind))
    return _merge_overlapping(markers)


def _merge_overlapping(markers: list[BrollMarker]) -> list[BrollMarker]:
    """Collapse duplicate markers of one kind over one span.

    A demo sentence carrying both "let me show you" and "right here" is one
    opportunity, not two; the stronger evidence wins and the weaker is noted.
    """
    out: list[BrollMarker] = []
    for m in markers:
        if out:
            last = out[-1]
            same = last.kind == m.kind
            overlap = not (m.start >= last.end or m.end <= last.start)
            if same and overlap:
                continue
        out.append(m)
    return out
