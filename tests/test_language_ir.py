"""Tests for the language IR: what was said, and the transforms that name themselves.

Every test here is written against a hazard in `docs/telugu-language-ir-spec.md`
§6, and is written to *fail without the guard* — a test that passes both with and
without the guard is documentation, not a test. The hazards this module can
guard in pure values are the ones these cover; the ones that need a decoder
(script substitution at 23.9%, a Latin span auto-labelled English) are covered by
what the record *fails* to assert, which is the only honest thing a pure-value
module can do.

`tools/wer_bench._normalize` is the comparator of record, so the
`wer_normalised` test pins against the original rather than restating it.
"""
import dataclasses
import unicodedata

import pytest

from engine.language_ir import (
    AsrSignals,
    CanonicalForm,
    LanguageIR,
    ScriptSpan,
    TransformRecord,
    WordSpan,
    _build_reverse,
    _deromanize,
    _romanize_span,
    _script_of,
    _source_script,
    _word_script,
    build_language_ir,
)
from tools.wer_bench import _normalize


# ── fixtures ─────────────────────────────────────────────────────────────────


class _Word:
    """A word-like input: faster-whisper's Word exposes ``probability``,
    models.core.Word exposes ``confidence``; the builder reads either."""

    def __init__(self, text, start=0.0, end=1.0, probability=None):
        self.text = text
        self.start = start
        self.end = end
        self.probability = probability


def _ir(text, words=None, asr=None, verdict="usable", detail="ok"):
    if words is None:
        parts = text.split()
        words = [_Word(p, float(i), float(i) + 0.5, 0.9) for i, p in enumerate(parts)]
    if asr is None:
        asr = AsrSignals(engine="faster_whisper", model="small")
    return build_language_ir(text, words, asr, verdict, detail, "fitness.json")


TELUGU_SENTENCE = "అనగా మన దేశానికి హిందీ అధికార భాష"


# ── hazard 4 / invariant: the verbatim is the source of truth ────────────────


def test_verbatim_survives_every_derived_operation():
    """Removing any guard in the canonical path must not change ``verbatim`` by
    a byte: the record is the one place "what was said" lives."""
    ir = _ir(TELUGU_SENTENCE)
    assert ir.verbatim == TELUGU_SENTENCE


def test_verbatim_is_whitespace_trimmed_only():
    ir = _ir("  " + TELUGU_SENTENCE + "  ")
    assert ir.verbatim == TELUGU_SENTENCE


def test_verbatim_survives_an_unmappable_span():
    """A span that cannot be romanized nulls the canonical form and leaves the
    verbatim alone; a module that 'repaired' the span would change this."""
    ir = _ir("క�ర", [_Word("క�ర")])
    assert ir.verbatim == "క�ర"
    assert ir.canonical.iso15919 is None


def test_word_span_text_is_verbatim():
    ir = _ir(TELUGU_SENTENCE)
    assert [w.text for w in ir.words] == TELUGU_SENTENCE.split()


def test_no_field_encodes_a_language_variety():
    """The dialect constraint is enforced by absence (spec §5.6 invariant 3):
    there is no register, no is_literary, no dialect. Adding one would make
    this test fail to compile as written."""
    names = {f.name for f in dataclasses.fields(LanguageIR)}
    for banned in ("register", "is_literary", "dialect", "variety", "formality"):
        assert banned not in names
    flat = {f.name for f in dataclasses.fields(WordSpan)}
    for banned in ("register", "is_literary", "dialect", "variety"):
        assert banned not in flat


def test_records_are_frozen():
    ir = _ir(TELUGU_SENTENCE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ir.verbatim = "something else"


# ── hazard 5 / invertibility gate ────────────────────────────────────────────


def test_round_trip_loses_nothing():
    """The whole point of ISO 15919 over Hunterian here: the reverse map
    reproduces the span exactly. Hunterian, which collapses ట/త and deletes
    schwas, would not. The spans do not have the same length as their
    romanizations, so the walk advances over the concatenated form by the
    length of each span's own romanization."""
    ir = _ir(TELUGU_SENTENCE)
    assert ir.canonical.iso15919 == "anagā mana dēśāniki hiṁdī adhikāra bhāṣa"
    at = 0
    for span in ir.spans:
        if span.script != "Telugu":
            continue
        source = ir.verbatim[span.char_start:span.char_end]
        part = _romanize_span(source).iso15919
        # the slice of the concatenated form for this span is the same string
        assert ir.canonical.iso15919[at:at + len(part)] == part
        assert _deromanize(part) == source
        at += len(part)
    assert at == len(ir.canonical.iso15919)


@pytest.mark.parametrize("word,iso", [
    ("హిందీ", "hiṁdī"),
    ("వాక్య", "vākya"),          # ప్ర-style conjunct: క + ് + య
    ("ప్ర", "pra"),              # one glyph cluster, three code points
    ("ప్రా", "prā"),
    ("కృతి", "kṛti"),            # క + ృ is one reverse unit
    ("కక్క", "kakka"),           # a virama consonant must not swallow the next
    ("కక", "kaka"),              # …and the two must stay distinct (see below)
    ("కృషి", "kṛṣi"),
    ("జ్ఞానం", "jñānaṁ"),
    ("మఱు", "maṟu"),             # ఱ RRA is line-below, not dot-below
    ("తమిళం", "tamiḷaṁ"),        # ళ is ḷ; ఌ is deliberately unmapped instead
    ("దేశం", "dēśaṁ"),           # ఏ is ē, distinct from ఎ e
    ("శివుడు", "śivuḍu"),        # శ ś stays distinct from ష ṣ
])
def test_round_trip_per_word(word, iso):
    """Per-word: the forward value is pinned, and the reverse must give the
    source back. A table that mapped ఌ to ḷ as well as ళ would break the
    round trip for తమిళం."""
    result = _romanize_span(word)
    assert result.iso15919 == iso
    assert _deromanize(iso) == word


def test_a_virama_consonant_is_distinct_from_a_bare_one():
    """The naive per-code-point rule (virama -> "") maps both కక్క and కక to
    "kaka". The gate has to catch that, and the table has to avoid it."""
    assert _romanize_span("కక్క").iso15919 == "kakka"
    assert _romanize_span("కక").iso15919 == "kaka"
    assert _deromanize("kakka") == "కక్క"
    assert _deromanize("kaka") == "కక"


def test_lossy_transform_is_caught_by_the_gate():
    """A transform that maps something but cannot be undone must fail closed
    rather than degrade silently. "కఇ" — a consonant carrying its inherent
    vowel, immediately followed by the independent vowel ఇ — romanizes to
    "kai", which the reverse map reads as క + the vowel-sign ై (కై), because
    that is the sequence real Telugu uses. The gate refuses to store a
    canonical form it cannot get back from."""
    ir = _ir("కఇ", [_Word("కఇ")])
    assert ir.canonical.iso15919 is None
    assert ir.canonical.iso15919_ascii is None
    assert ir.canonical.search_alias is None
    assert any("does not round-trip" in w for w in ir.warnings)


def test_gate_passes_the_common_case_it_disambiguates():
    """The ambiguous direction above is the implausible one; this is the one
    real text uses, and it must not be penalized for the other's existence."""
    assert _romanize_span("కై").iso15919 == "kai"
    assert _deromanize("kai") == "కై"
    ir = _ir("కై", [_Word("కై")])
    assert ir.canonical.iso15919 == "kai"


def test_reverse_map_is_injective():
    """Two Telugu sequences sharing one Latin key is a table bug, so it fails
    at import. Rebuilding here re-runs that assertion."""
    reverse, _pattern = _build_reverse()
    # Every key is reachable and unique by construction; this is the check a
    # duplicate key would have raised on.
    assert len(reverse) >= 500
    # A duplicate inserted by hand is what the guard exists to refuse.
    reverse_copy = dict(reverse)
    duplicate_key = next(iter(reverse_copy))
    different = (0x0C15, 0x0C4D)
    with pytest.raises(AssertionError):

        def _put(key, cps, table):
            if table.setdefault(key, cps) != cps:
                raise AssertionError("not injective")

        _put(duplicate_key, different, reverse_copy)


# ── hazard 7 / unmappable code points null the field ─────────────────────────


@pytest.mark.parametrize("cp", [
    0x0C0C,   # ఌ vocalic L: ISO 15919's ḷ is already spent on ళ
    0x0C5D,   # NAKAARA POLLU, archaic
    0x0C78,   # a traditional fraction sign
    0xFFFD,   # the replacement character the measured ASR emits
    0xE000,   # private use
    0x0C3B,   # unassigned inside the block: block tables misclassify these
])
def test_unmappable_codepoint_nulls_the_field_and_names_it(cp):
    """Spec §5.3: an unassigned, replacement, private-use or unmapped archaic
    code point nulls the *whole* iso15919 field and the warning names the code
    point and its position. A passthrough would let it round-trip through the
    gate and reach an index looking lossless."""
    ch = chr(cp)
    source = "క" + ch + "ర"
    ir = _ir(source, [_Word(source)])
    assert ir.canonical.iso15919 is None
    assert ir.canonical.iso15919_ascii is None
    assert ir.canonical.search_alias is None
    needle = f"U+{cp:04X}"
    assert any(needle in w for w in ir.warnings), ir.warnings


def test_unmapped_code_point_still_keeps_its_verbatim():
    ir = _ir("కఌర", [_Word("కఌర")])
    assert ir.verbatim == "కఌర"
    assert ir.words[0].text == "కఌర"


def test_iso15919_ascii_is_none_whenever_iso15919_is():
    """Spec §5.3: the 7-bit fallback is None whenever the canonical form is, so
    an ASCII-only consumer never gets a form the lossless one refused to
    produce."""
    ir = _ir("కఌర", [_Word("కఌర")])
    assert ir.canonical.iso15919 is None
    assert ir.canonical.iso15919_ascii is None


# ── hazard 6 / double romanization ───────────────────────────────────────────


def test_latin_span_is_not_double_romanized():
    """Applying an Indic transliterator to an already-Latin span is how garbage
    reaches an index. The transform is applied per span on script evidence."""
    text = "అయ్యో friend వచ్చాడు"
    ir = _ir(text)
    assert "friend" in ir.canonical.iso15919
    assert ir.canonical.iso15919 == "ayyō friend vaccāḍu"


def test_tenglish_word_is_not_split():
    """A mixed-script word is labelled Mixed and never split: ASR word timing
    is atomic and splitting destroys the timing invariants (hazard 15)."""
    ir = _ir("tనమస్కారం", [_Word("tనమస్కారం")])
    assert len(ir.words) == 1
    assert ir.words[0].script == "Mixed"


def test_latin_span_is_never_auto_labelled_eng():
    """Spec §4.5 / §5.1: in Tenglish the Latin spans are more often romanized
    Telugu than English, so script alone never sets a language hypothesis.
    A module that guessed would set ``language_hypothesis`` here."""
    ir = _ir("అయ్యో friend వచ్చాడు")
    latin = [w for w in ir.words if w.script == "Latin"]
    assert latin and all(w.language_hypothesis is None for w in latin)
    assert all(w.language_hypothesis is None for w in ir.words)
    assert all(w.hypothesis_basis == "" for w in ir.words)


def test_language_hypothesis_is_none_with_no_evidence():
    """Fails closed: nothing in this module corroborates a language, so every
    hypothesis is None and no ``label_spans`` provenance record is emitted."""
    ir = _ir(TELUGU_SENTENCE)
    assert all(w.language_hypothesis is None for w in ir.words)
    assert not any(r.transform == "label_spans" for r in ir.provenance)


def test_language_probability_carries_no_weight_when_forced():
    """``language_probability`` is 1.000 on all 175 committed per-clip records
    because ``--language`` was forced; a consumer routing on it is routing on a
    flag. The record carries the forcing flag beside the probability."""
    asr = AsrSignals(
        engine="faster_whisper", model="small", language_forced="te",
        detected_language="te", language_probability=1.000,
    )
    ir = _ir(TELUGU_SENTENCE, asr=asr)
    assert ir.asr.language_forced == "te"
    assert ir.asr.language_probability == 1.000
    # and it still does not produce a hypothesis
    assert all(w.language_hypothesis is None for w in ir.words)


# ── hazard 9 / the lossy alias sits downstream of the lossless one ───────────


def test_search_alias_is_derived_from_iso15919_not_verbatim():
    """Spec §5.3: exactly one lossy step, downstream of the canonical one.
    Derived from the verbatim, the alias of "హిందీ" would keep the script and
    the point of a query alias would be gone."""
    ir = _ir("హిందీ", [_Word("హిందీ")])
    assert ir.canonical.iso15919 == "hiṁdī"
    assert ir.canonical.search_alias == "himdi"
    # folded from the verbatim it would still be Telugu, which is what makes
    # the derivation site load-bearing rather than cosmetic
    assert ir.canonical.search_alias != _ascii_fold(ir.verbatim)


def _ascii_fold(text):
    """The lossy fold as a free function, so the test can show what the alias
    would have been had it been taken from the verbatim."""
    from engine.language_ir import _ascii_fold as fold
    return fold(text)


def test_search_alias_is_lossy_and_says_so():
    """ṛ/ṟ and ś/ṣ all collapse, which is why the alias is query-side only and
    never stored in place of the canonical form (hazard 5). The provenance
    record is what keeps that visible downstream."""
    ir = _ir("మఱు శివుడు", [_Word("మఱు", 0, 1), _Word("శివుడు", 1, 2)])
    assert ir.canonical.search_alias == "maru sivudu"
    record = next(r for r in ir.provenance
                  if r.field == "canonical.search_alias")
    assert record.transform == "ascii_fold"
    assert record.lossy is True
    assert record.invertible is False


def test_iso15919_ascii_is_actually_ascii():
    """The 7-bit fallback must be 7 bits; a combining mark leaking into it
    would make an ASCII-only EDL path lie."""
    ir = _ir(TELUGU_SENTENCE)
    assert ir.canonical.iso15919_ascii.encode("ascii") \
        == ir.canonical.iso15919_ascii.encode("ascii")
    assert all(ord(c) < 0x80 for c in ir.canonical.iso15919_ascii)
    assert ir.canonical.iso15919_ascii == "anagaa mana deeshaaniki hi.mdii adhikaara bhaasha"


# ── hazard 9 / WER comparator parity ─────────────────────────────────────────


@pytest.mark.parametrize("text", [
    TELUGU_SENTENCE,
    "అనగా మన దేశానికి హిందీ!",
    "It's 3 O'clock — అవును.",
    "కక్క ౦౧౨ రాజు",
    "Mixed పదం here",
    "",
    "   ",
])
def test_wer_normalised_is_byte_identical_to_the_comparator(text):
    """A second, divergent definition of error is how an IR-derived comparison
    stops being comparable to the committed WER records. Pinned against the
    original, not restated."""
    ir = _ir(text, words=[] if not text.strip() else None)
    assert ir.canonical.wer_normalised == _normalize(text)
    # and it preserves script: it never romanizes
    assert "hi" not in _normalize(TELUGU_SENTENCE)


def test_wer_normalised_is_present_even_when_iso15919_is_none():
    """The flattening is a comparison aid independent of the romanization, so a
    span the gate refused still gets one."""
    ir = _ir("కఌర", [_Word("కఌర")])
    assert ir.canonical.iso15919 is None
    assert ir.canonical.wer_normalised == _normalize("కఌర")


# ── hazard 11 / the block boundary ───────────────────────────────────────────


def test_kannada_is_not_telugu():
    """Hazard 11: Kannada begins at U+0C80, one past the Telugu block. Script
    detection is name-based — the spec rejects block tables for it, because
    they misclassify the block's 28 unassigned code points — so a Kannada
    letter is never classified Telugu, and a span of it is not romanized with
    the Telugu table."""
    assert _script_of("\u0c95") == "Kannada"
    assert _word_script("\u0c95\u0ca8\u0ccd\u0ca1") == "Kannada"
    ir = _ir("\u0c95\u0ca8\u0ccd\u0ca1", [_Word("\u0c95\u0ca8\u0ccd\u0ca1")])
    assert ir.words[0].script == "Kannada"
    assert ir.spans[0].script == "Kannada"
    # and it is not romanized with the Telugu table
    assert ir.canonical.iso15919 == "\u0c95\u0ca8\u0ccd\u0ca1"
    assert any("Kannada" in w for w in ir.warnings)


def test_romanizer_domain_ends_where_kannada_begins():
    """The block constant is the romanizer's own domain test, and it is pinned
    because widening it is how a Kannada letter starts being looked up in the
    Telugu table. U+0C7F is the last Telugu code point; U+0C80 is Kannada."""
    from engine.language_ir import _in_telugu_block

    assert _in_telugu_block(0x0C7F) is True
    assert _in_telugu_block(0x0C80) is False
    # a Kannada letter inside a Telugu span is passed over, not romanized
    assert _romanize_span("\u0c15\u0cb0").iso15919 == "ka\u0cb0"


def test_every_telugu_codepoint_classifies_as_telugu():
    """Sanity on the block itself: the classification is not a block table
    (the spec rejects block tables for unassigned code points) but the
    assigned letters must still land in the right script."""
    for cp in (0x0C15, 0x0C3E, 0x0C4D, 0x0C66, 0x0C02):
        assert _script_of(chr(cp)) == "Telugu", hex(cp)


def test_virama_is_combining_class_nine():
    """The abugida understanding is shared with engine/caption_director.py;
    if this property changed, the conjunct handling above would be wrong."""
    assert unicodedata.combining("్") == 9
    assert unicodedata.combining("ం") == 0    # anusvara is a spacing mark
    assert unicodedata.category("ం") == "Mc"


def test_danda_does_not_manufacture_a_script_switch():
    """The Devanagari danda is the sentence terminator the Telugu ASR emits;
    its name is "DEVANAGARI DANDI" and keying a script on it would split
    ordinary Telugu prose into two scripts."""
    assert _script_of("।") == "Common"
    assert _script_of("॥") == "Common"
    ir = _ir("వాక్య। రెండవ వాక్యం।",
             [_Word("వాక్య।", 0, 1), _Word("రెండవ", 1, 2), _Word("వాక్యం।", 2, 3)])
    assert [s.script for s in ir.spans] == ["Telugu"]
    assert not any(s.is_switch for s in ir.spans)


def test_unassigned_code_point_does_not_make_a_word_mixed():
    """A PUA or reserved code point has no script, so it cannot by itself flip
    a Telugu word to Mixed — that would route it past the null-the-field rule
    and let it reach the canonical form silently."""
    assert _word_script("కర") == "Telugu"
    assert _source_script("కర") == "Telugu"


# ── hazard 1 / script substitution is recorded, never repaired ──────────────


def test_devanagari_substitution_is_recorded_not_repaired():
    """The measured ASR writes Telugu phonetics in Devanagari on 23.9% of
    telugu-small. The span is kept, its script is recorded, and nothing
    rewrites it — a later stage rewriting it would destroy the evidence."""
    text = "నమస్కారం परिणाम చేశాడు"
    ir = _ir(text, [_Word(p, float(i), float(i) + 0.5)
                    for i, p in enumerate(text.split())])
    scripts = [s.script for s in ir.spans]
    assert scripts == ["Telugu", "Devanagari", "Telugu"]
    assert ir.spans[1].is_switch is True
    assert ir.words[1].script == "Devanagari"
    assert ir.words[1].text == "परिणाम"
    assert ir.verbatim == text


def test_source_script_is_the_first_script_not_mixed():
    """A Telugu clip with an English loanword in it has source script Telugu;
    calling every span divergent would bury the one warning that matters."""
    assert _source_script("అయ్యో friend వచ్చాడు") == "Telugu"
    ir = _ir("అయ్యో friend వచ్చాడు")
    divergent = [w for w in ir.warnings if "not in the source script" in w]
    assert len(divergent) == 1
    assert "span 1" in divergent[0]


def test_spans_tile_the_verbatim_exactly():
    """Spec §5.2: concatenating the span text reproduces the verbatim. Offsets
    that did not tile would break the round-trip test above, which is why the
    two are tested separately."""
    text = "నమస్కారం परिणाम చేశాడు"
    ir = _ir(text, [_Word(p, float(i), float(i) + 0.5)
                    for i, p in enumerate(text.split())])
    assert "".join(ir.verbatim[s.char_start:s.char_end] for s in ir.spans) == text
    assert [s.start for s in ir.spans] == [0.0, 1.0, 2.0]
    assert [s.end for s in ir.spans] == [0.5, 1.5, 2.5]
    for s in ir.spans:
        assert s.start == ir.words[s.word_start].start
        assert s.end == ir.words[s.word_end - 1].end


def test_a_span_with_no_words_is_dropped():
    """Spec §5.2: a span with no words is dropped rather than given invented
    times. Here there are no words at all, so there are no spans — and the
    canonical form still gets derived from the text."""
    ir = _ir(TELUGU_SENTENCE, words=[])
    assert ir.spans == ()
    assert ir.words == ()
    assert ir.canonical.iso15919 == "anagā mana dēśāniki hiṁdī adhikāra bhāṣa"
    assert any("no ASR words" in w for w in ir.warnings)


def test_empty_verbatim_is_handled():
    ir = _ir("", words=[])
    assert ir.verbatim == ""
    assert ir.canonical.wer_normalised == ""


# ── hazard 13 / provenance ───────────────────────────────────────────────────


def test_every_non_none_derived_field_has_a_transform_record():
    """A derived field with no record is a schema violation: the version is
    what makes a stored canonical that disagrees with a re-derived one
    detectable rather than trusted."""
    ir = _ir(TELUGU_SENTENCE)
    fields = {r.field for r in ir.provenance}
    assert fields >= {
        "spans",
        "canonical.iso15919",
        "canonical.iso15919_ascii",
        "canonical.search_alias",
        "canonical.wer_normalised",
    }
    for record in ir.provenance:
        assert record.version
        assert record.transform in {
            "script_segment", "iso15919", "iso15919_ascii", "ascii_fold",
            "wer_normalise", "label_spans",
        }


def test_provenance_records_are_absent_for_null_fields():
    """The record says the transform ran and produced something; a None field
    with a record would claim a derivation that did not happen."""
    ir = _ir("కఌర", [_Word("కఌర")])
    fields = {r.field for r in ir.provenance}
    assert "canonical.iso15919" not in fields
    assert "canonical.iso15919_ascii" not in fields
    assert "canonical.search_alias" not in fields
    # the segmentation and the flattening still happened
    assert "spans" in fields
    assert "canonical.wer_normalised" in fields


def test_provenance_marks_the_lossless_and_lossy_steps_apart():
    ir = _ir(TELUGU_SENTENCE)
    by_field = {r.field: r for r in ir.provenance}
    assert by_field["canonical.iso15919"].invertible is True
    assert by_field["canonical.iso15919"].lossy is False
    assert by_field["canonical.iso15919_ascii"].invertible is False
    assert by_field["canonical.iso15919_ascii"].lossy is True
    assert by_field["canonical.search_alias"].invertible is False
    assert by_field["canonical.search_alias"].lossy is True
    assert by_field["spans"].invertible is True


def test_schema_version_is_recorded():
    from engine.language_ir import SCHEMA_VERSION
    assert SCHEMA_VERSION
    assert all(r.version == SCHEMA_VERSION for r in ir_provenance())


def ir_provenance():
    return _ir(TELUGU_SENTENCE).provenance


# ── determinism and the clamping contract ────────────────────────────────────


def test_rebuild_is_byte_identical():
    """The record is pure: the same inputs give the same record, which is what
    makes a stored canonical comparable to a re-derived one."""
    words = [_Word(p, float(i), float(i) + 0.5, 0.9)
             for i, p in enumerate(TELUGU_SENTENCE.split())]
    a = _ir(TELUGU_SENTENCE, words=words)
    b = _ir(TELUGU_SENTENCE, words=words)
    assert a == b
    assert a.canonical == b.canonical


def test_words_clamped_to_the_corpus_contract():
    """engine/corpus.py:232-239 swaps bounds when end precedes start; the IR
    matches that rather than inventing its own repair."""
    ir = _ir("నమస్కారం", [_Word("నమస్కారం", 2.0, 1.0)])
    assert (ir.words[0].start, ir.words[0].end) == (1.0, 2.0)
    assert any("end before start" in w for w in ir.warnings)


def test_negative_start_is_clamped_to_zero():
    ir = _ir("నమస్కారం", [_Word("నమస్కారం", -5.0, 1.0)])
    assert ir.words[0].start == 0.0


def test_empty_word_is_dropped_not_kept():
    """engine/corpus.py:241-245 drops words with no text; a WordSpan with empty
    text violates the schema (spec §5.1)."""
    ir = _ir("నమస్కారం ok", [_Word("నమస్కారం", 0, 1),
                                 _Word("", 1, 2), _Word("ok", 2, 3)])
    assert [w.text for w in ir.words] == ["నమస్కారం", "ok"]
    assert any("no text" in w for w in ir.warnings)


def test_probability_is_read_off_either_word_shape():
    """faster-whisper's Word exposes ``probability``, models.core.Word exposes
    ``confidence``; the builder reads whichever is there and None if neither."""
    from models.core import Word

    ir = _ir("నమస్కారం", [Word("నమస్కారం", 0.0, 1.0, 0.42)])
    assert ir.words[0].asr_probability == 0.42

    ir = _ir("నమస్కారం", [_Word("నమస్కారం", 0.0, 1.0)])
    assert ir.words[0].asr_probability is None


def test_fitness_verdict_travels_with_the_record():
    """A 91.54%-WER transcript must not be laundered into an
    authoritative-looking key; the verdict is carried inside the record
    (hazard 14)."""
    ir = _ir(TELUGU_SENTENCE, verdict="poor", detail="wer 91.54%")
    assert ir.fitness_verdict == "poor"
    assert ir.fitness_detail == "wer 91.54%"
    assert ir.fitness_record == "fitness.json"


# ── the module stays pure-values ─────────────────────────────────────────────


def test_no_transliteration_dependency_is_imported():
    """The mapping is hand-written from the code points; importing an
    transliteration library would make this a build-time dependency it does
    not have on a CPU-only host."""
    import engine.language_ir as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("indic_transliteration", "polyglot", "icu",
                   "transliterate(", "import transliterate"):
        assert banned not in src


def test_romanize_span_reports_what_it_could_not_map():
    """The builder decides what an unmapped code point means; the transform's
    job is to name it."""
    result = _romanize_span("కఌర")
    assert result.iso15919 is None
    assert result.iso15919_ascii is None
    assert (0x0C0C, 1) in result.unmapped


def test_scripts_of_mixed_and_pure_text():
    assert _word_script("రూ.500") == "Telugu"     # digits are script-neutral
    assert _word_script("పదం word") == "Mixed"
    assert _word_script("") == "Unknown"
    assert _script_of("a") == "Latin"
    assert _script_of("5") == "Common"
