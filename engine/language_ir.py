"""Language IR: what was said, and every derived view that names its own origin.

Why this module exists
----------------------

The pipeline's only per-text language evidence is a script classifier being
used as a language classifier (`engine/context_guard.py`), and on the measured
Telugu corpus that classifier is wrong in a characteristic way: a monolingual
Telugu reference is reported as five languages, because 23.9% of one ASR
record's hypotheses are Devanagari and 8.8% Latin (`docs/telugu-language-ir-
spec.md` §2.2). The model heard the phonetics and wrote them in the wrong
script. Nothing downstream can fix that, and — this is the point — nothing
downstream may *repair* it either, because a repaired transcript no longer
shows what the model did and an auditor can no longer tell a repaired
transcript from a correct one.

So this module does not transcribe, does not detect language, and does not
correct anything. It holds:

* ``verbatim`` — the ASR text, whitespace-trimmed and nothing more. This is the
  only field a caption or display path may read for text. An ASCII-friendly
  path deciding what a viewer sees is the tofu incident
  (`docs/product-gap-map.md:94`), and a canonical retrieval key deciding it is
  the same failure one layer up;
* derived views — an ISO 15919 romanization, its 7-bit fallback, a lossy ASCII
  search alias and a WER-comparison flattening — each accompanied by a
  ``TransformRecord`` naming the transform, its version, and whether it is
  invertible;
* facts and hypotheses kept strictly apart — a script span is a *fact* (the
  script of a code point is certain and costs nothing to compute); a language
  label on that span is a *hypothesis* with a ``basis``, and no hypothesis is
  ever emitted without corroboration. Nothing in this module corroborates one,
  because no code-switching ground truth exists on this corpus: the SLR66
  reference contains zero genuine Latin spans. So ``language_hypothesis`` is
  ``None`` and ``hypothesis_basis`` is ``""`` on every word this module builds,
  and that is the correct answer, not a missing one.

The dialect constraint is enforced by absence rather than by a check: the IR
has no ``register``, no ``is_literary`` and no ``dialect`` field, so a consumer
cannot select a literary Telugu form for a Telangana spoken form, because there
is nowhere to put one. Romanization cannot record a dialect either — it maps
the letter క to ``ka`` whether the speaker realised it as [k], [g] or [x] — so
the dialect guarantee rests entirely on ``verbatim`` and on the absence of any
destructive transform.

Purity
------

Everything here is a value. No I/O, no network, no wall clock, no imports
outside the standard library. Given the same ``(verbatim, words, asr)`` the
record is byte-identical, matching the determinism contract of ``build_corpus``
(`engine/corpus.py:497`). A transform that cannot round-trip fails closed: the
derived field becomes ``None`` and a warning names what blocked it, because a
silent ``None`` and a silent fabrication have the same shape.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Sequence

SCHEMA_VERSION = "0.1.0"

# Bumped only when a stored derived form can disagree with a re-derived one;
# TransformRecord.version carries it so a reader detects the mismatch instead
# of trusting it (spec §5.4, hazard 13).
TRANSFORM_VERSION = SCHEMA_VERSION
_REVERSE_MAP_ID = "iso15919-r1"


# ── Script classification ───────────────────────────────────────────────────
#
# The Unicode *Script* property is what §5.1 asks for, and the stdlib does not
# expose it (no `regex` module on this host, and no pip install is authorised).
# The next-best authoritative source is the character's own Unicode name: a
# Telugu code point is named "TELUGU ...", a Devanagari one "DEVANAGARI ...".
# The spec's own script audit used this same derivation (§2.2), and unlike a
# block table it does not classify the 27 unassigned Telugu code points as
# anything, because `unicodedata.name` raises on them and they land in
# "Unknown" instead. Where the name and the Script property are known to
# differ, the difference is listed below and the name wins by being derivable
# here.

_NAME_SCRIPTS = frozenset({
    "telugu", "devanagari", "bengali", "gurmukhi", "gujarati", "oriya",
    "tamil", "kannada", "malayalam", "sinhala", "latin", "cyrillic", "greek",
    "hebrew", "arabic", "armenian", "tibetan", "thai", "hangul", "hiragana",
    "katakana",
})

# The Devanagari danda is the *conventional Telugu sentence terminator*
# (engine/story_units.py:108, spec §1.5), and its name is "DEVANAGARI DANDI".
# Deriving "Devanagari" from it would manufacture a script switch — and a
# script-substitution warning — inside ordinary Telugu prose, so it and the
# double danda are script-neutral, like every other punctuation mark.
_SCRIPT_NEUTRAL = frozenset({0x0964, 0x0965})  # । ॥


def _script_of(ch: str) -> str:
    """Script of one non-mark character, from its Unicode name.

    Returns ``"Unknown"`` for unassigned code points and ``"Common"`` for
    everything the name does not attribute to a script — digits, punctuation,
    separators, U+FFFD. Marks never reach here: they attach to the preceding
    base (see ``_word_script``), because the Telugu anusvara "ం" is a spacing
    mark (category ``Mc``, combining class 0, engine/caption_director.py:63-68)
    and keying a syllable on it doubled the measured width of Telugu prose.
    """
    if ord(ch) in _SCRIPT_NEUTRAL:
        return "Common"
    try:
        first = unicodedata.name(ch).split(" ", 1)[0].lower()
    except ValueError:
        return "Unknown"
    if first == "cjk":
        return "Han"
    return first.capitalize() if first in _NAME_SCRIPTS else "Common"


def _word_script(text: str) -> str:
    """Script of a word: the script of its first non-mark character, or
    ``"Mixed"`` when it spans more than one.

    ``"Common"`` (digits, punctuation, separators) is script-neutral and does
    not by itself make a word Mixed, so "రూ.500" is Telugu and not Mixed.
    The word is never split to resolve a mix: ASR word timing is atomic and
    splitting it destroys the timing invariants the cut algebra depends on
    (spec §5.1, hazard 15).
    """
    scripts: set[str] = set()
    for ch in text:
        if unicodedata.category(ch)[0] == "M":
            continue
        scripts.add(_script_of(ch))
    if not scripts:
        return "Unknown"
    # "Unknown" is an unassigned or private-use code point: it has no script, so
    # it cannot make a word Mixed. Classifying it as its own script would route
    # a Telugu word containing one through the non-Telugu passthrough branch,
    # where §5.3's null-the-field rule never fires and the unmappable code point
    # reaches the canonical form silently.
    others = scripts - {"Common", "Unknown"}
    if not others:  # digits/punctuation/unassigned only
        return scripts.pop()
    return "Mixed" if len(others) > 1 else others.pop()


def _source_script(text: str) -> str:
    """The script the verbatim is *in*, for the span-divergence check.

    Not ``_word_script``: a Tenglish sentence is Mixed as a string, and calling
    every span in it divergent would emit a warning per span for ordinary
    speech and train an editor to ignore the one that matters — the ASR having
    written Telugu phonetics in Devanagari. The first non-mark, non-Common
    character is the spec's own definition of a word's script (§5.1), so a
    Telugu clip with an English loanword in it has source script Telugu and
    only the Latin span is recorded as a fact.
    """
    for ch in text:
        if unicodedata.category(ch)[0] == "M":
            continue
        script = _script_of(ch)
        if script not in ("Common", "Unknown"):
            return script
    return "Common"


# ── ISO 15919 romanization of Telugu ────────────────────────────────────────
#
# ISO 15919 (2001) rather than Hunterian, because the requirement is a
# retrieval key that loses nothing the verbatim carries, not a form an English
# reader can pronounce. It is one-to-one on graphemes, so script -> Latin ->
# script is deterministic, and it keeps exactly the contrasts Telugu encodes:
# ట ṭ vs త t, శ ś vs ష ṣ vs స s, ళ ḷ, the vowel-length pairs ఎ e / ఏ ē and
# ఒ o / ఓ ō, anusvara ం ṁ and visarga ః ḥ. Hunterian collapses the dental and
# retroflex series and deletes schwas, so two distinct utterances share one key
# and the round trip is not deterministic; it belongs on the query side as an
# alias and never as the canonical form (spec §4.2).
#
# Every diacritic below was resolved through unicodedata.name, not typed from
# memory: ṛ is dot-below, ṟ is line-below, and the two are distinct characters
# in the standard, which is what keeps ఋ and ఱ from colliding.

_MACRON = "̄"     # combining macron above — vowel length: ā ī ū ē ō
_DOT_BELOW = "̣"  # combining dot below — ṛ ṭ ḍ ṇ ḷ ḥ
_DOT_ABOVE = "̇"  # combining dot above — ṅ; anusvara ṁ
_LINE_BELOW = "̲" # combining line below — ṟ (ఱ RRA), ḻ (ఴ LLLA)
_ACUTE = "́"      # combining acute — ś
_CIRCUMFLEX = "̂" # combining circumflex — candrabindu m̂
_TILDE = "̃"      # combining tilde — combining anusvara above ఄ

_TELUGU_START, _TELUGU_END = 0x0C00, 0x0C7F  # Kannada begins at U+0C80; an
# inclusive end here would silently classify Kannada as Telugu (hazard 11).
_VIRAMA = 0x0C4D     # combining class 9, verified against caption_director.

# Independent vowels: code point -> (ISO 15919, 7-bit ASCII fallback).
_INDEPENDENT: dict[int, tuple[str, str]] = {
    0x0C05: ("a", "a"),                             # అ
    0x0C06: ("ā", "aa"),                            # ఆ
    0x0C07: ("i", "i"),                             # ఇ
    0x0C08: ("ī", "ii"),                            # ఈ
    0x0C09: ("u", "u"),                             # ఉ
    0x0C0A: ("ū", "uu"),                            # ఊ
    0x0C0B: ("ṛ", ".r"),                            # ఋ
    0x0C0E: ("e", "e"),                             # ఎ
    0x0C0F: ("ē", "ee"),                            # ఏ
    0x0C10: ("ai", "ai"),                           # ఐ
    0x0C12: ("o", "o"),                             # ఒ
    0x0C13: ("ō", "oo"),                            # ఓ
    0x0C14: ("au", "au"),                           # ఔ
}

# Consonants, each carrying the inherent vowel a: code point -> (ISO, ASCII).
# A consonant immediately followed by the virama (U+0C4D) or a dependent vowel
# sign loses that a, so "ప్ర" is ప + ് + ర -> "pra" and not "para".
_CONSONANT: dict[int, tuple[str, str]] = {
    0x0C15: ("ka", "ka"), 0x0C16: ("kha", "kha"),   # క ఖ
    0x0C17: ("ga", "ga"), 0x0C18: ("gha", "gha"),   # గ ఘ
    0x0C19: ("ṅa", "nga"),                          # ఙ
    0x0C1A: ("ca", "ca"), 0x0C1B: ("cha", "cha"),   # చ ఛ
    0x0C1C: ("ja", "ja"), 0x0C1D: ("jha", "jha"),   # జ ఝ
    0x0C1E: ("ña", "nya"),                          # ఞ
    0x0C1F: ("ṭa", ".ta"), 0x0C20: ("ṭha", ".tha"), # ట ఠ
    0x0C21: ("ḍa", ".da"), 0x0C22: ("ḍha", ".dha"), # డ ఢ
    0x0C23: ("ṇa", ".na"),                          # ణ
    0x0C24: ("ta", "ta"), 0x0C25: ("tha", "tha"),   # త థ
    0x0C26: ("da", "da"), 0x0C27: ("dha", "dha"),   # ద ధ
    0x0C28: ("na", "na"),                           # న
    0x0C2A: ("pa", "pa"), 0x0C2B: ("pha", "pha"),   # ప ఫ
    0x0C2C: ("ba", "ba"), 0x0C2D: ("bha", "bha"),   # బ భ
    0x0C2E: ("ma", "ma"),                           # మ
    0x0C2F: ("ya", "ya"),                           # య
    0x0C30: ("ra", "ra"),                           # ర
    0x0C31: ("ṟa", ".ra"),                          # ఱ
    0x0C32: ("la", "la"),                           # ల
    0x0C33: ("ḷa", ".la"),                          # ళ
    0x0C34: ("ḻa", ".lla"),                         # ఴ
    0x0C35: ("va", "va"),                           # వ
    0x0C36: ("śa", "sha"),                          # శ
    0x0C37: ("ṣa", "sha"),                          # ష
    0x0C38: ("sa", "sa"),                           # స
    0x0C39: ("ha", "ha"),                           # హ
}

# Dependent vowel signs: they replace the consonant's inherent a.
_VOWEL_SIGN: dict[int, tuple[str, str]] = {
    0x0C3E: ("ā", "aa"),   # U+0C3E is the AA sign; U+0C41 is U
    0x0C3F: ("i", "i"),    # ి
    0x0C40: ("ī", "ii"),   # ీ
    0x0C41: ("u", "u"),    # ు
    0x0C42: ("ū", "uu"),   # ూ
    0x0C43: ("ṛ", ".r"),   # ృ
    0x0C46: ("e", "e"),    # ె
    0x0C47: ("ē", "ee"),   # ే
    0x0C48: ("ai", "ai"),  # ై
    0x0C4A: ("o", "o"),    # ొ
    0x0C4B: ("ō", "oo"),   # ో
    0x0C4C: ("au", "au"),  # ౄ
}

# Signs that are units of their own rather than modifiers of a consonant.
_SIGN: dict[int, tuple[str, str]] = {
    0x0C00: (_CIRCUMFLEX, "^"),                       # ఀ combining candrabindu
    0x0C01: ("m" + _CIRCUMFLEX, "m^"),                # ఁ candrabindu
    0x0C02: ("ṁ", ".m"),                              # ం anusvara
    0x0C03: ("ḥ", ".h"),                              # ః visarga
    0x0C04: (_TILDE, "~"),                            # ఄ combining anusvara above
    0x0C3C: (_DOT_BELOW, "."),                        # ఼ nukta
    0x0C3D: ("’", "'"),                               # ఽ avagraha
}

# The numerals pass through rather than romanizing to ASCII digits. ISO 15919
# does map ౦ -> "0", but that mapping is one-to-many across scripts (Devanagari
# ० and Telugu ౦ share it) and applying it would make a span that contains an
# ASCII digit — the common case in real ASR output — fail its own round trip.
# Keeping both digit systems verbatim is what keeps the canonical form lossless
# per span, which is the property the invertibility gate exists to enforce.
_NUMERAL = {cp: (chr(cp), chr(cp)) for cp in range(0x0C66, 0x0C70)}

# Assigned Telugu code points this table deliberately does not cover, because
# ISO 15919 gives them no standalone correspondence and inventing one would
# write fabricated text into a retrieval key (hazard 7):
#   U+0C0C ఌ and U+0C60/0x0C61 (vocalic L / RR / LL letters) — the standard's
#     ḷ is already spent on the retroflex lateral ళ, so mapping ఌ to it too
#     would make two source strings share one key;
#   U+0C44, U+0C55, U+0C56, U+0C62, U+0C63 — the long-vocalic signs and length
#     marks, whose Latin values collide with the signs they lengthen;
#   U+0C58-U+0C5A, U+0C5D — the archaic letters TSA / DZA / RRRA / NAKAARA
#     POLLU;
#   U+0C78-U+0C7F — the traditional fraction signs, siddham and tuumu.
# A span containing any of them nulls the canonical form, and the warning names
# the code point and its offset.


def _in_telugu_block(cp: int) -> bool:
    return _TELUGU_START <= cp <= _TELUGU_END


# Code points the reverse map could echo back verbatim and so appear to pass the
# invertibility gate while carrying no transliteration at all. §5.3 names these
# explicitly: an unassigned code point, U+FFFD, or a private-use code point in a
# Telugu span must null the field, because a passthrough here would let an
# unmappable span through with a canonical form that looks lossless.
_REPLACEMENT = 0xFFFD
_PUA_RANGES = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))


def _unmappable(ch: str) -> bool:
    cp = ord(ch)
    if cp == _REPLACEMENT:
        return True
    if any(lo <= cp <= hi for lo, hi in _PUA_RANGES):
        return True
    # Cn covers reserved (the 27 unassigned Telugu code points) and noncharacters.
    return unicodedata.category(ch) == "Cn"


class _Romanize:
    """Result of romanizing one script span: the canonical form and its 7-bit
    fallback, or the code points that made that impossible."""

    __slots__ = ("iso15919", "iso15919_ascii", "unmapped")

    def __init__(self, iso15919: str | None, iso15919_ascii: str | None,
                 unmapped: tuple[tuple[int, int], ...]):
        self.iso15919 = iso15919
        self.iso15919_ascii = iso15919_ascii
        self.unmapped = unmapped


def _romanize_span(text: str) -> _Romanize:
    """ISO 15919 of one Telugu-script span, with the 7-bit fallback beside it.

    Returns ``unmapped`` (code point, offset) for anything the table above does
    not cover; the caller decides whether that nulls the field. Anything
    outside the Telugu block — a space, a danda, an ASCII digit — passes
    through unchanged, because romanizing it is not this transform's job and
    double-romanizing a Latin span is how garbage reaches an index (hazard 6).
    """
    iso: list[str] = []
    asc: list[str] = []
    unmapped: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        cp = ord(ch)
        if _unmappable(ch):
            unmapped.append((cp, i))
            i += 1
            continue
        if not _in_telugu_block(cp):
            iso.append(ch)
            asc.append(ch)
            i += 1
            continue
        if cp in _SIGN:
            value, fallback = _SIGN[cp]
            iso.append(value)
            asc.append(fallback)
            i += 1
            continue
        if cp in _NUMERAL:
            value, fallback = _NUMERAL[cp]
            iso.append(value)
            asc.append(fallback)
            i += 1
            continue
        if cp in _INDEPENDENT:
            value, fallback = _INDEPENDENT[cp]
            iso.append(value)
            asc.append(fallback)
            i += 1
            continue
        if cp in _CONSONANT:
            value, fallback = _CONSONANT[cp]
            nxt = ord(text[i + 1]) if i + 1 < n else None
            if nxt == _VIRAMA:                # ప + ് -> "p", the a is cancelled
                iso.append(value[:-1])
                asc.append(fallback[:-1])
                i += 2
                continue
            if nxt in _VOWEL_SIGN:            # ప + ా -> "pā", the a is replaced
                v, f = _VOWEL_SIGN[nxt]
                iso.append(value[:-1] + v)
                asc.append(fallback[:-1] + f)
                i += 2
                continue
            iso.append(value)
            asc.append(fallback)
            i += 1
            continue
        if cp == _VIRAMA:
            # A virama without a consonant to cancel from is not Telugu; the
            # invertibility gate below is what says so, by failing the round
            # trip rather than emitting a letter the source never had.
            unmapped.append((cp, i))
            i += 1
            continue
        unmapped.append((cp, i))
        i += 1
    if unmapped:
        return _Romanize(None, None, tuple(unmapped))
    return _Romanize("".join(iso), "".join(asc), ())


def _build_reverse() -> tuple[dict[str, tuple[int, ...]], "re.Pattern[str]"]:
    """The inverse map, and a longest-match matcher over its keys.

    Every key is a whole transliteration unit, including consonant+vowel-sign
    pairs, so "kṛ" is one unit (క + ృ) and is preferred over "k" (క + ്)
    followed by "ṛ" (ఋ). Without the pair units, ordinary Telugu such as
    "కృతి" would parse back as క్ఋతి and the gate would reject real text.

    Two different Telugu sequences mapping to one Latin string is a bug in the
    tables, not a runtime condition, so it fails at import.
    """
    rev: dict[str, tuple[int, ...]] = {}

    def put(key: str, cps: tuple[int, ...]) -> None:
        if rev.setdefault(key, cps) != cps:
            raise AssertionError(
                f"ISO 15919 map is not injective: {key!r} -> {rev[key]!r} "
                f"and {cps!r}"
            )

    for table in (_INDEPENDENT, _SIGN, _NUMERAL):
        for cp, (value, _fallback) in table.items():
            put(value, (cp,))
    for cp, (value, fallback) in _CONSONANT.items():
        put(value, (cp,))                       # క -> "ka"
        put(value[:-1], (cp, _VIRAMA))          # క + ് -> "k"
        for scp, (sv, _sf) in _VOWEL_SIGN.items():
            put(value[:-1] + sv, (cp, scp))     # క + ా -> "kā"
    # Longest keys first so the matcher prefers "kā" to "k" at one position.
    pattern = re.compile(
        "|".join(
            re.escape(k) for k in sorted(rev, key=len, reverse=True)
        )
    )
    return rev, pattern


_REVERSE, _REVERSE_RE = _build_reverse()


def _deromanize(iso: str) -> str | None:
    """Apply the reverse ISO 15919 map. A character no unit covers is emitted
    unchanged: the forward map passes those through, and reproducing them is
    what the caller is about to compare against the source span."""
    out: list[str] = []
    i, n = 0, len(iso)
    while i < n:
        match = _REVERSE_RE.match(iso, i)
        if match is None:
            out.append(iso[i])
            i += 1
            continue
        out.append("".join(chr(cp) for cp in _REVERSE[match.group(0)]))
        i = match.end()
    return "".join(out)


def _ascii_fold(text: str) -> str:
    """NFD, strip every combining mark, lowercase: the query-side alias.

    Lossy by construction — ṛ and ṟ both fold to "r", ś and ṣ both to "s",
    ట/త both to "t — which is why it is derived from the canonical form and
    never stored in its place (hazard 5).
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(ch)
    ).lower()


def _wer_normalise(text: str) -> str:
    """Byte-identical to ``tools/wer_bench._normalize``.

    Copied rather than imported so this module stays pure-values with no
    dependency on the tooling layer, and pinned by a test against the original
    so the two cannot drift: a second, divergent definition of error is how an
    IR-derived comparison stops being comparable to the committed WER records.
    The flattening is script-blind and script-preserving — it does not
    romanize, and it is a comparison aid, never a display or segmentation input
    (hazard 9).
    """
    kept = [
        ch if (ch.isalnum() or ch in "'’-") else " "
        for ch in text.lower()
    ]
    return " ".join("".join(kept).split())


# ── Records (spec §5) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class WordSpan:
    """One ASR word and its classification.

    ``text`` is verbatim from the ASR and is never re-encoded. The word is
    atomic: a mixed-script word is labelled ``script == "Mixed"`` and is never
    split, because ASR word timing is one unit and splitting it destroys the
    timing invariants the cut algebra depends on.
    """

    text: str
    start: float
    end: float
    asr_probability: float | None
    script: str
    span_id: int
    # ISO 639-3, or None. Defaults to None and nothing in this module ever sets
    # it: a script switch is, on the measured corpus, more likely a decoder
    # artifact than a speaker behaviour, and a Latin span is never auto-labelled
    # "eng" because in Tenglish the Latin spans are more often romanized Telugu
    # (spec §4.5, hazards 2-3).
    language_hypothesis: str | None
    hypothesis_basis: str  # "" | "script_range" | "script_range+asr_lang"
                           # | "acoustic_lid" | "manual"


@dataclass(frozen=True)
class ScriptSpan:
    """A maximal run of one script.

    Bounds come from the words, never interpolated: ``start ==
    words[word_start].start`` and ``end == words[word_end - 1].end``. A span
    with no words is dropped rather than given invented times. Spans tile the
    verbatim, so concatenating their text reproduces it exactly; a span whose
    script is not the source script is *recorded* — never repaired, never
    dropped — which is what keeps the ASR's script substitution auditable
    (hazard 1).
    """

    index: int
    char_start: int       # offset into the verbatim string
    char_end: int         # half-open
    word_start: int       # index into the WordSpan list
    word_end: int         # half-open
    start: float          # from word bounds, never synthesized
    end: float
    script: str
    is_switch: bool       # True iff index > 0 and script != the previous span's


@dataclass(frozen=True)
class CanonicalForm:
    """The derived retrieval view of the verbatim.

    ``iso15919`` is lossless and invertible per Telugu span; ``search_alias``
    is derived from it and is lossy. ``iso15919_ascii`` is the standard's own
    7-bit fallback (ṭ -> ".t", ś -> "sh", ē -> "ee"), kept so the ASCII-only
    EDL path has one canonical derivation rather than a second, divergent
    romanization (spec §4.2).
    """

    iso15919: str | None
    iso15919_ascii: str | None
    search_alias: str | None     # LOSSY, non-canonical, query-side only
    wer_normalised: str          # tools/wer_bench._normalize-compatible


@dataclass(frozen=True)
class TransformRecord:
    """Provenance for one derived field: which transform made it, whether the
    verbatim is recoverable from it, and which implementation version.

    A derived field with no record is a schema violation, and the version is
    what makes a stored canonical that disagrees with a re-derived one
    detectable rather than trusted (hazard 13).
    """

    field: str
    transform: str   # "script_segment" | "iso15919" | "iso15919_ascii"
                     # | "ascii_fold" | "wer_normalise" | "label_spans"
    invertible: bool
    lossy: bool
    version: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SegmentDecode:
    """The per-segment decode signals production discards (spec §3.3)."""

    index: int
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    no_speech_prob: float | None = None
    temperature: float | None = None
    seek: int | None = None


@dataclass(frozen=True)
class AsrSignals:
    """The ASR signals the production path keeps and the ones it throws away.

    ``language_probability`` is recorded but carries no weight when
    ``language_forced`` is not None: it is 1.000 on all 175 committed per-clip
    records precisely because ``--language`` was forced, so a consumer that
    routes on it is routing on a flag (hazard 12). ``language_ranking`` is the
    second choice made visible, and a te 0.88 / ta 0.09 ranking is a different
    claim from te 0.99 / ta 0.01.
    """

    engine: str                 # "faster_whisper" | "sherpa_onnx" | ...
    model: str                  # the model that RAN
    requested_model: str | None = None
    language_forced: str | None = None   # the --language code, or None if auto
    detected_language: str | None = None
    language_probability: float | None = None
    language_ranking: list[tuple[str, float]] | None = None
    duration_after_vad: float | None = None
    decode: list[SegmentDecode] = field(default_factory=list)


@dataclass(frozen=True)
class LanguageIR:
    """The record: what was said, plus derived views that name their origin.

    ``verbatim`` is the source of truth and the only field a caption or display
    path may read for text. It is never mutated by anything in this subsystem:
    there is no API that takes a ``LanguageIR`` and returns one with a different
    ``verbatim``, correction is additive (a warning, a hypothesis) and never
    destructive. The record has no field that can hold a "corrected" verbatim
    and none that encodes a language variety, and those absences are the
    dialect constraint (spec §5.6 invariants 1-3).
    """

    verbatim: str                       # ASR text, whitespace-trimmed only
    words: tuple[WordSpan, ...]
    spans: tuple[ScriptSpan, ...]
    canonical: CanonicalForm
    provenance: tuple[TransformRecord, ...]
    asr: AsrSignals
    # The verdict in force when this record was built, carried inside the record
    # so an index built on canonical.iso15919 cannot launder a 91.54%-WER
    # transcript into an authoritative-looking key (hazard 14).
    fitness_verdict: str                # usable | poor | unusable | not_measured
    fitness_detail: str                 # FitnessReport.detail, verbatim
    fitness_record: str | None          # the .json the number came from
    warnings: tuple[str, ...]           # never empty while a derived field is None


# ── Builder ─────────────────────────────────────────────────────────────────


def _word_probability(word: object) -> float | None:
    """Read a probability off any word-like input. faster-whisper's ``Word``
    exposes ``probability``, models.core.Word exposes ``confidence``."""
    for attr in ("probability", "confidence"):
        value = getattr(word, attr, None)
        if value is not None:
            return float(value)
    return None


def build_language_ir(
    verbatim: str,
    words: Sequence[object],
    asr: AsrSignals,
    fitness_verdict: str,
    fitness_detail: str,
    fitness_record: str | None = None,
) -> LanguageIR:
    """Assemble a ``LanguageIR`` from an ASR result.

    ``words`` are any sequence of word-like objects exposing ``text``,
    ``start`` and ``end``; a ``probability``/``confidence`` attribute is read
    if present and otherwise recorded as ``None``. Pure and deterministic: the
    same inputs give the byte-identical record.
    """
    text = verbatim.strip()
    warnings: list[str] = []

    # ── words: clamp and classify, dropping only the empty ones ──
    kept: list[tuple[str, float, float, float | None, str]] = []
    for word in words:
        wtext = (getattr(word, "text", None) or "").strip()
        start = float(getattr(word, "start", 0.0))
        end = float(getattr(word, "end", start))
        if not wtext:
            warnings.append(
                "dropped an ASR word with no text; a word with no text is not "
                "a WordSpan (engine/corpus.py:241-245)"
            )
            continue
        if end < start:                       # corpus.py:232-239's repair
            start, end = end, start
            warnings.append(
                f"word {wtext!r} had end before start; bounds were swapped"
            )
        start = max(0.0, start)
        kept.append(
            (wtext, start, end, _word_probability(word), _word_script(wtext))
        )

    # ── spans: maximal runs of one script over the tiled text ──
    tiled = " ".join(w[0] for w in kept)
    if kept and tiled != text:
        # An empty word list cannot tile anything, and the no-words branch below
        # already says what it did; this warning is about offsets, which exist
        # only when words do.
        warnings.append(
            "the ASR words do not tile the verbatim (joining them with single "
            "spaces gives a different string), so span offsets are into the "
            "tiled text; the verbatim is unchanged"
        )
    # Character offsets of each word inside the tiled string.
    offsets: list[int] = []
    at = 0
    for w in kept:
        offsets.append(at)
        at += len(w[0]) + 1                  # the word plus its separating space

    source_script = _source_script(text)

    spans: list[ScriptSpan] = []
    if kept:
        groups: list[list[int]] = []         # word indices, one group per span
        for idx, w in enumerate(kept):
            if groups and kept[groups[-1][0]][4] == w[4]:
                groups[-1].append(idx)
            else:
                groups.append([idx])
        for gi, group in enumerate(groups):
            script = kept[group[0]][4]
            word_start, word_end = group[0], group[-1] + 1
            # The separators between spans belong to the span before them, so
            # the spans tile the text exactly; leading separators belong to the
            # first span.
            char_start = offsets[group[0]] if gi > 0 else 0
            char_end = (
                offsets[groups[gi + 1][0]] if gi + 1 < len(groups) else len(tiled)
            )
            spans.append(ScriptSpan(
                index=gi,
                char_start=char_start,
                char_end=char_end,
                word_start=word_start,
                word_end=word_end,
                start=kept[group[0]][1],
                end=kept[group[-1]][2],
                script=script,
                is_switch=gi > 0 and script != spans[gi - 1].script,
            ))

    # ── words, now that every span index is known ──
    word_spans = tuple(
        WordSpan(
            text=w[0],
            start=w[1],
            end=w[2],
            asr_probability=w[3],
            script=w[4],
            span_id=next(
                (s.index for s in spans
                 if s.word_start <= i < s.word_end),
                0,
            ),
            language_hypothesis=None,
            hypothesis_basis="",
        )
        for i, w in enumerate(kept)
    )

    # ── canonical form: per span, on script evidence ──
    iso_parts: list[str | None] = []
    asc_parts: list[str | None] = []
    for span in spans:
        span_text = tiled[span.char_start:span.char_end]
        if span.script == "Telugu":
            result = _romanize_span(span_text)
            if result.unmapped:
                for cp, offset in result.unmapped:
                    name = _codepoint_name(cp)
                    warnings.append(
                        f"canonical form unavailable: code point U+{cp:04X} "
                        f"{name} at verbatim offset {span.char_start + offset} "
                        f"in span {span.index} has no ISO 15919 mapping, so "
                        f"the whole iso15919 field is None rather than a "
                        f"best-effort romanization"
                    )
                iso_parts.append(None)
                asc_parts.append(None)
                continue
            # The invertibility gate: one unmappable code point is caught above;
            # this catches a transform that maps something but cannot be
            # undone, and fails closed instead of degrading silently.
            if _deromanize(result.iso15919) != span_text:
                warnings.append(
                    f"canonical form unavailable: span {span.index} "
                    f"romanizes to {result.iso15919!r} which does not "
                    f"round-trip to the verbatim, so iso15919 is None"
                )
                iso_parts.append(None)
                asc_parts.append(None)
                continue
            iso_parts.append(result.iso15919)
            asc_parts.append(result.iso15919_ascii)
        elif span.script == "Latin":
            # Already Latin: applying an Indic transliterator here would
            # double-romanize it (hazard 6). The ASCII fallback still folds
            # accents, so an EDL consumer gets 7 bits.
            iso_parts.append(span_text)
            asc_parts.append(_ascii_fold(span_text))
        else:
            # A script this transform does not cover — on the measured corpus
            # usually the ASR having written Telugu phonetics in Devanagari.
            # Passed through verbatim and named loudly, because romanizing it
            # would be inventing a mapping this module does not have (hazard 1).
            iso_parts.append(span_text)
            asc_parts.append(_ascii_fold(span_text))
            warnings.append(
                f"span {span.index} is {span.script}, for which no ISO 15919 "
                f"transliteration is implemented here; it passes through the "
                f"canonical form unchanged and the verbatim is preserved"
            )

    if not spans:
        # No words (a whitespace-only or empty verbatim): derive from the text
        # itself so the canonical form is still defined.
        if _word_script(text) == "Telugu":
            result = _romanize_span(text)
            iso15919 = result.iso15919
            iso15919_ascii = result.iso15919_ascii
            if result.unmapped or _deromanize(result.iso15919 or "") != text:
                iso15919 = None
                iso15919_ascii = None
                for cp, offset in result.unmapped:
                    warnings.append(
                        f"canonical form unavailable: code point U+{cp:04X} "
                        f"{_codepoint_name(cp)} at offset {offset} has no ISO "
                        f"15919 mapping"
                    )
        else:
            iso15919 = text
            iso15919_ascii = _ascii_fold(text)
        warnings.append(
            "verbatim has no ASR words; the canonical form was derived from "
            "the text directly"
        )
    elif any(part is None for part in iso_parts):
        # The gate nulls the whole field, not just the failing span: a
        # best-effort romanization is how a falsified record enters an index
        # (hazard 7).
        iso15919 = None
        iso15919_ascii = None
        warnings.append(
            "iso15919 and iso15919_ascii are None for the whole record because "
            "at least one span could not be romanized losslessly"
        )
    else:
        iso15919 = "".join(iso_parts) if iso_parts else ""
        iso15919_ascii = "".join(asc_parts) if asc_parts else ""

    # The alias is derived from the canonical form, never from the verbatim, so
    # there is exactly one lossy step and it sits downstream of the lossless
    # one (spec §5.3).
    search_alias = None if iso15919 is None else _ascii_fold(iso15919)
    if search_alias is None:
        warnings.append(
            "search_alias is None because the canonical form it is derived "
            "from is None"
        )

    canonical = CanonicalForm(
        iso15919=iso15919,
        iso15919_ascii=iso15919_ascii,
        search_alias=search_alias,
        wer_normalised=_wer_normalise(text),
    )

    # A span whose script is not the source script is recorded, and so is the
    # fact that it happened — a later stage rewriting it would destroy the
    # evidence (hazard 1). One warning for the record: per-span warnings on a
    # genuinely mixed sentence are noise, and the one that must survive the
    # editor's filter is that the ASR changed scripts at all.
    divergent = [s.index for s in spans if s.script != source_script]
    if divergent:
        warnings.append(
            f"span{'s' if len(divergent) > 1 else ''} "
            f"{', '.join(str(i) for i in divergent)} "
            f"{'are' if len(divergent) > 1 else 'is'} not in the source script "
            f"{source_script}: recorded as a fact and left untouched, because "
            f"repairing it would destroy the evidence of what the ASR did"
        )

    provenance = _provenance(canonical, word_spans)

    return LanguageIR(
        verbatim=text,
        words=word_spans,
        spans=tuple(spans),
        canonical=canonical,
        provenance=provenance,
        asr=asr,
        fitness_verdict=fitness_verdict,
        fitness_detail=fitness_detail,
        fitness_record=fitness_record,
        warnings=tuple(warnings),
    )


def _codepoint_name(cp: int) -> str:
    try:
        return unicodedata.name(chr(cp))
    except ValueError:
        return "<unassigned>"


def _provenance(canonical: CanonicalForm,
                words: tuple[WordSpan, ...]) -> tuple[TransformRecord, ...]:
    """One record per non-None derived field, plus one for the segmentation."""
    records: list[TransformRecord] = [
        TransformRecord(
            field="spans",
            transform="script_segment",
            # The spans tile the verbatim, so it is recoverable from them.
            invertible=True,
            lossy=False,
            version=TRANSFORM_VERSION,
            params={"script_source": "unicode_name"},
        ),
    ]
    if canonical.iso15919 is not None:
        records.append(TransformRecord(
            field="canonical.iso15919",
            transform="iso15919",
            invertible=True,
            lossy=False,
            version=TRANSFORM_VERSION,
            params={"reverse_map": _REVERSE_MAP_ID},
        ))
    if canonical.iso15919_ascii is not None:
        records.append(TransformRecord(
            field="canonical.iso15919_ascii",
            transform="iso15919_ascii",
            # The 7-bit fallback collapses ś/ṣ to "sh" and ṛ/ṟ to ".r", so the
            # verbatim is not recoverable from it. It is the standard's own
            # fallback, not a second romanization.
            invertible=False,
            lossy=True,
            version=TRANSFORM_VERSION,
            params={"reverse_map": _REVERSE_MAP_ID},
        ))
    if canonical.search_alias is not None:
        records.append(TransformRecord(
            field="canonical.search_alias",
            transform="ascii_fold",
            invertible=False,
            lossy=True,
            version=TRANSFORM_VERSION,
            params={"normalization": "NFD", "strip": "combining_marks"},
        ))
    records.append(TransformRecord(
        field="canonical.wer_normalised",
        transform="wer_normalise",
        invertible=False,     # case and punctuation are destroyed
        lossy=True,
        version=TRANSFORM_VERSION,
        params={"comparator": "tools/wer_bench._normalize"},
    ))
    # "label_spans" is emitted only when a language hypothesis is corroborated.
    # Nothing in this module corroborates one, so the record is absent rather
    # than present with an empty claim, and every hypothesis stays None.
    if any(w.language_hypothesis is not None for w in words):
        records.append(TransformRecord(
            field="words.language_hypothesis",
            transform="label_spans",
            invertible=False,
            lossy=False,
            version=TRANSFORM_VERSION,
            params={},
        ))
    return tuple(records)
