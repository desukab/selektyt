"""The audit's own checks, pinned (criterion P1-P4 and the caption checks).

``tools/editorial_audit.py`` is the benchmark the release validation phase asked
for: it scores the concrete properties that decide whether a shipped clip is
publishable, and its exit code composes with CI. Six of its seven checks had no
test at all — only ``check_silence_share`` was pinned — so any of them could
have silently inverted and shipped a wrong PASS verdict. These tests are the
fix for that, and they pin the semantics rather than the implementation, so a
check that drifts from what it claims to measure has to delete a stated
property.

The one case worth reading before changing ``check_cuts_respect_sentences``
is ``test_tightening_a_pause_between_two_kept_words_is_not_a_split``. On
emwcon-panel-2018 clip 01 the audit used to report a cut splitting ' was,'/
' at' even though the cut lands 0.18 s past the end of ' was,' and 1.50 s
before ' at' begins, and no word was lost: the two kept intervals drop only
the 1.44 s gap between two transcript-adjacent words of one utterance, and
the utterance arrives on screen complete. That was a false positive, and its
cause was one level up: the sentence structure came from splitting the
candidate's ``text`` on punctuation, and the transcript segment it was cut
from carries no terminal punctuation, so two utterances six seconds apart
were read as one sentence. The check now takes the transcript's own
utterance spans and fires on the loss of a word, which is what
``test_dropping_a_word_from_the_middle_of_a_sentence_is_flagged`` pins.
``check_cuts_land_between_words`` stays silent on the same boundary because
67.09 s is inside neither word — the two checks are testing different things,
and both are right.
``test_a_clean_cut_between_two_unpunctuated_utterances`` is the regression
test for the source of the sentence structure: it is the pentagon clip 02
shape, and without the transcript spans it fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from tools.editorial_audit import (  # noqa: E402
    _load,
    TOLERANCE_S,
    check_caption_text_is_transcript_text,
    check_cues_fit_the_render,
    check_cuts_land_between_words,
    check_cuts_respect_sentences,
    check_last_word_is_not_shaved,
    check_no_duplicate_sentences,
)


def _cand(text: str, words: list[tuple[float, float, str]]) -> dict:
    """A candidate whose word spans and text are exactly as given."""
    return {
        "text": text,
        "start": words[0][0],
        "end": words[-1][1],
        "words": [{"text": t, "start": s, "end": e} for s, e, t in words],
    }


# --- mid-word cuts ---------------------------------------------------------


def test_a_cut_inside_a_word_is_flagged():
    words = [(0.0, 0.5, "hello"), (0.5, 1.0, "world")]
    ok, bad = check_cuts_land_between_words(_cand("hello world", words), [(0.0, 0.75)])
    assert not ok
    assert any("inside word" in b for b in bad)


def test_a_cut_exactly_at_a_word_edge_is_not_flagged():
    """``st < t < en`` is strict, so a boundary on a word edge is clean."""
    words = [(0.0, 0.5, "hello"), (0.5, 1.0, "world")]
    ok, bad = check_cuts_land_between_words(_cand("hello world", words), [(0.0, 0.5)])
    assert ok and not bad


def test_a_cut_between_distant_words_is_not_inside_either():
    """The gap case: 0.18 s past one word's end and 1.50 s before the next
    starts is not inside a word, so this check must stay quiet."""
    words = [(0.0, 0.42, "was"), (2.10, 2.10, "at"), (2.15, 2.55, "first")]
    ok, bad = check_cuts_land_between_words(_cand("was at first", words), [(0.0, 0.6)])
    assert ok and not bad


def test_no_words_is_not_a_failure():
    ok, bad = check_cuts_land_between_words({"text": "", "words": []}, [(0.0, 1.0)])
    assert ok and not bad


# --- mid-sentence cuts -----------------------------------------------------


def test_a_cut_between_sentences_is_not_flagged():
    words = [(0.0, 0.5, "hello"), (0.6, 1.1, "world"),
             (1.4, 1.9, "goodbye"), (2.0, 2.5, "now")]
    ok, bad = check_cuts_respect_sentences(
        _cand("Hello world. Goodbye now.", words), [(0.0, 1.15)])
    assert ok, f"cut at a sentence edge reported as splitting: {bad}"


def test_a_cut_inside_a_sentence_is_flagged():
    words = [(0.0, 0.5, "hello"), (0.6, 1.1, "cruel"), (1.2, 1.7, "world")]
    ok, bad = check_cuts_respect_sentences(
        _cand("hello cruel world", words), [(0.0, 0.85)])
    assert not ok
    assert any("splits" in b for b in bad)


def test_a_clip_ending_mid_sentence_is_flagged():
    """The clip ends inside a sentence, so the sentence loses its end.

    The boundary is 0.18 s past the end of ' was' and 1.50 s before ' at'
    begins — composed from the real emwcon clip 01 boundary. The words after
    the clip are dropped, so the sentence loses them and the viewer hears a
    thought that never finished.
    """
    words = [(0.0, 0.42, "was"), (2.10, 2.10, "at"), (2.15, 2.55, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.60)])
    assert not ok, "a clip ending inside a sentence was not reported"
    assert any("'was'" in b and "'at'" in b for b in bad)


def test_tightening_a_pause_between_two_kept_words_is_not_a_split():
    """A boundary that shortens only the gap between two kept words is clean.

    Both words are kept and no word between them exists, so the sentence is
    intact on screen and the clip goes on. This is the case the check used to
    flag: all four mid-sentence findings on the committed corpus were it
    (pentagon clip 04, emwcon clip 01, ooi clip 01, royal-society clip 01,
    each removing 0.5-1.4 s of gap between transcript-adjacent words). Firing
    here would make the audit reject every clip that removes a pause.
    """
    words = [(0.0, 0.42, "was"), (2.10, 2.40, "at"), (2.45, 2.85, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.60), (2.00, 2.90)])
    assert ok, f"removing a gap between two kept words was reported: {bad}"


def test_dropping_a_word_from_the_middle_of_a_sentence_is_flagged():
    """The case the criterion exists for: a hole in the sentence itself.

    ' at' is dropped by the gap between the two kept intervals, so the clip
    reads ' was first' and the sentence lost a word it should have kept.
    """
    words = [(0.0, 0.42, "was"), (0.60, 1.00, "at"), (1.10, 1.50, "first")]
    ok, bad = check_cuts_respect_sentences(
        _cand("was at first", words), [(0.0, 0.55), (1.05, 1.60)])
    assert not ok, "a word dropped from inside a sentence was not reported"
    assert any("'was'" in b and "'at'" in b for b in bad) or \
        any("'at'" in b and "'first'" in b for b in bad), bad


def test_the_message_does_not_leak_an_fstring_prefix():
    """The start-boundary message used to print 'was'/f'at' — a literal ``f``
    from a misquoted f-string field. The end-boundary message never had it, so
    a finding from each boundary is needed to cover both formats.

    The interval cuts a sentence at both ends — ' at' is dropped before it and
    ' now' after it — so a start finding and an end finding both come out.
    """
    words = [(0.0, 0.42, "was"), (0.60, 1.00, "at"),
             (1.10, 1.50, "first"), (1.60, 2.00, "now")]
    cand = _cand("was at first now", words)
    _ok, bad = check_cuts_respect_sentences(cand, [(1.05, 1.55)])
    starts = [b for b in bad if b.startswith("start")]
    ends = [b for b in bad if b.startswith("end")]
    assert starts and ends, "need a finding from each boundary to cover both formats"
    for line in starts + ends:
        assert "/f'" not in line, f"literal f-string prefix leaked: {line!r}"


def test_a_clean_cut_between_two_unpunctuated_utterances():
    """The regression test for the pentagon-esper-briefing clip 02 shape.

    The transcript's segments are 'We stay in continuous contact with them' and
    'What was used to destroy the compound', neither punctuated, and the
    candidate's text joins them with a space. The interval ends 0.18 s after
    ' them' and the next starts 0.06 s before ' What', so the cut is between
    two utterances and no word is lost. This used to be reported as a split,
    and five of the seven committed projects failed the audit on findings of
    exactly this shape.
    """
    words = [(0.0, 0.30, "We"), (0.30, 0.50, "stay"), (0.50, 0.80, "contact"),
             (0.80, 1.00, "with"), (1.00, 1.20, "them"),
             (6.00, 6.30, "What"), (6.30, 6.60, "was"), (6.60, 7.00, "used")]
    cand = _cand("contact with them What was used", words)
    intervals = [(0.0, 1.38), (5.94, 7.00)]
    utterances = [(0.0, 1.20), (6.00, 7.00)]

    ok, bad = check_cuts_respect_sentences(cand, intervals, utterances)
    assert ok, f"a clean cut between two utterances was reported: {bad}"


def test_a_whole_utterance_dropped_between_two_kept_ones_is_not_a_split():
    """Where the sentence structure is read from is what decides this one.

    Dropping an utterance wholesale between two kept ones is an edit, not a
    hole: nothing in a kept sentence is lost. The candidate's text has no
    punctuation between the three utterances, so splitting that text reads
    them as one long sentence and the dropped middle one looks like a hole in
    it. The transcript's own utterance spans are the structure that says
    otherwise — which is why ``audit`` passes them and does not rely on text.
    """
    words = [(0.0, 0.30, "We"), (0.30, 0.60, "stay"), (0.60, 0.90, "in"),
             (0.90, 1.20, "contact"),
             (2.00, 2.30, "What"), (2.30, 2.60, "was"),
             (4.00, 4.30, "The"), (4.30, 4.60, "rest")]
    cand = _cand("We stay in contact What was The rest", words)
    intervals = [(0.0, 1.38), (3.94, 4.70)]
    utterances = [(0.0, 1.20), (2.00, 2.60), (4.00, 4.60)]

    ok, bad = check_cuts_respect_sentences(cand, intervals, utterances)
    assert ok, f"a dropped utterance was reported as a hole in a kept one: {bad}"

    # The text-split fallback still merges the three utterances and so still
    # fires here. That is the imprecision the utterances exist to remove, and
    # asserting it keeps the fallback honest rather than quietly dead.
    fallback_ok, _ = check_cuts_respect_sentences(cand, intervals)
    assert not fallback_ok, "the fallback no longer merges unpunctuated utterances"


def test_one_word_cannot_be_mid_sentence():
    ok, bad = check_cuts_respect_sentences(
        _cand("hello", [(0.0, 0.5, "hello")]), [(0.0, 0.5)])
    assert ok and not bad


# --- final word ------------------------------------------------------------


def test_an_interval_ending_mid_word_is_flagged():
    words = [(0.0, 0.5, "hello"), (0.6, 1.2, "world")]
    ok, bad = check_last_word_is_not_shaved(_cand("hello world", words), [(0.0, 1.0)])
    assert not ok
    assert any("final word" in b for b in bad)


def test_a_short_word_fully_inside_the_interval_passes():
    """Unstressed function words genuinely run 90-150 ms, so this check
    deliberately does not threshold on word *duration* — only on whether the
    interval's end is past the word's end. A 120 ms 'to' fully contained is a
    pass."""
    words = [(0.0, 0.12, "to"), (0.2, 0.9, "go")]
    ok, bad = check_last_word_is_not_shaved(_cand("to go", words), [(0.0, 1.0)])
    assert ok and not bad


def test_an_interval_containing_no_words_is_not_a_failure():
    ok, bad = check_last_word_is_not_shaved(
        _cand("hello", [(5.0, 5.5, "hello")]), [(0.0, 1.0)])
    assert ok and not bad


# --- captions --------------------------------------------------------------


def test_a_cue_word_absent_from_the_transcript_is_flagged():
    ok, bad = check_caption_text_is_transcript_text(
        _cand("the quick brown fox", [(0.0, 0.3, "fox")]),
        [{"text": "lazy dog runs", "start": 0.0, "end": 1.0}])
    assert not ok
    assert any("'lazy'" in b for b in bad)


def test_stopwords_are_not_flagged():
    """Common function words are dropped, because they match trivially and
    would flood the findings with noise."""
    ok, bad = check_caption_text_is_transcript_text(
        _cand("the quick brown fox", [(0.0, 0.3, "fox")]),
        [{"text": "the a an of and to in is it that this", "start": 0.0, "end": 1.0}])
    assert ok and not bad


def test_no_cues_is_not_a_failure():
    ok, bad = check_caption_text_is_transcript_text(
        _cand("hello world", [(0.0, 0.5, "hello")]), [])
    assert ok and not bad


def test_a_cue_ending_past_the_render_is_flagged():
    ok, bad = check_cues_fit_the_render(
        [{"start": 0.0, "end": 5.0, "text": "x"}], 4.0)
    assert not ok


def test_a_cue_within_tolerance_of_the_render_passes():
    """ffprobe rounds to the same order as the tolerance, so a cue ending just
    short of the render is a rounding artefact, not an overrun."""
    ok, bad = check_cues_fit_the_render(
        [{"start": 0.0, "end": 4.0 + TOLERANCE_S / 2, "text": "x"}], 4.0)
    assert ok and not bad


def test_no_cues_or_no_render_is_not_a_failure():
    assert check_cues_fit_the_render([], 4.0) == (True, [])
    assert check_cues_fit_the_render([{"start": 0.0, "end": 9.0, "text": "x"}], 0.0) == (True, [])


# --- duplication -----------------------------------------------------------


def test_the_same_long_sentence_in_two_clips_is_flagged():
    sentence = "we were handing out wikis to everyone who walked past the table"
    cands = [{"text": sentence}, {"text": "something else entirely here ok"},
             {"text": sentence}]
    ok, bad = check_no_duplicate_sentences(cands)
    assert not ok
    assert any("clips 1 and 3" in b for b in bad)


def test_short_sentences_are_below_the_bar():
    """Under 24 characters is ignored, so repeated fragments like 'oh yes'
    cannot manufacture a duplication finding."""
    ok, bad = check_no_duplicate_sentences(
        [{"text": "oh yes right"}, {"text": "oh yes right"}])
    assert ok and not bad


def test_two_different_sentences_are_not_duplicates():
    ok, bad = check_no_duplicate_sentences(
        [{"text": "we were handing out wikis to everyone who walked past the table"},
         {"text": "later that evening the table was empty and we went home"}])
    assert ok and not bad


# --- the audit over a whole project ----------------------------------------


def test_a_project_with_nothing_to_audit_fails_with_a_reason(tmp_path):
    """Four of the eleven committed sources have no selections.json — the VAD
    gate suppressed their transcripts — and ``audit`` used to raise KeyError
    on them. A sweep across the corpus has to keep going past the first
    incomplete project, so this is a reported failure, not an exception."""
    from tools.editorial_audit import audit

    ok, rep = audit(tmp_path)
    assert not ok
    findings = rep["findings"]
    assert findings, "the missing artifact must be a finding, not an empty report"
    assert any("no selections.json" in detail for _n, _o, detail in findings)


def test_the_finding_names_the_stage_that_stopped_the_run(tmp_path, ):
    """'No selections' is the symptom, not the cause.

    All four committed sources that land here have a transcript.json with zero
    segments — transcription came back empty, so selection never ran. Telling
    an editor only that selections.json is missing sends them to the wrong
    stage: the fix for an empty transcript (the VAD gate ate the audio) is not
    the fix for a selection stage that rejected everything it saw.
    """
    from tools.editorial_audit import audit

    (tmp_path / "transcript.json").write_text(
        '{"language": "te", "asr": {"engine": "faster-whisper"}, "segments": []}',
        encoding="utf-8",
    )
    ok, rep = audit(tmp_path)
    assert not ok
    detail = rep["findings"][0][2]
    assert "transcription returned no segments" in detail
    assert "te" in detail, "the language the model decoded as belongs in the reason"


def test_a_transcript_that_returned_segments_points_at_selection_not_transcription(tmp_path):
    """The other shape: transcription worked, selection produced nothing. The
    finding must distinguish it, because this one is an editor's tuning problem
    and the empty-transcript one is a transcription bug."""
    from tools.editorial_audit import audit

    (tmp_path / "transcript.json").write_text(
        '{"language": "en", "segments": [{"text": "hello", "start": 0.0, "end": 1.0}]}',
        encoding="utf-8",
    )
    ok, rep = audit(tmp_path)
    assert not ok
    assert "transcription returned 1 segments" in rep["findings"][0][2]


def test_a_project_with_no_clips_reports_why_not_just_that_it_failed(tmp_path):
    """Selections exist but are empty: a run that rejected everything. The
    rejection reasons are the useful part, so they travel with the finding."""
    from tools.editorial_audit import audit

    (tmp_path / "selections.json").write_text("[]", encoding="utf-8")
    (tmp_path / "report.json").write_text(
        '{"rejection_reasons": {"no hook": 3, "too short": 2}}', encoding="utf-8"
    )
    ok, rep = audit(tmp_path)
    assert not ok
    assert any("no clips selected" in d for _n, _o, d in rep["findings"])
    assert any("no hook" in d for _n, _o, d in rep["findings"])


def test_the_whole_corpus_is_auditable_without_keyerror():
    """Every committed source, including the four with no transcript. This is
    the sweep the audit exists to support, and it is what used to stop at
    alice-1903 with a traceback."""
    from tools.editorial_audit import audit

    runs = sorted((TOOLS.parent / "corpus").glob("*_run/project"))
    assert runs, "no corpus runs found; the test below would pass vacuously"
    unreachable = []
    for project in runs:
        try:
            audit(project)
        except Exception as exc:
            unreachable.append(f"{project.parent.name}: {type(exc).__name__}: {exc}")
    assert not unreachable, (
        "the audit raised on a committed project instead of reporting it: "
        + "; ".join(unreachable)
    )


def test_the_audit_verdict_on_the_committed_corpus_is_the_recorded_baseline():
    """The audit's verdict over the eleven committed sources, pinned.

    This tool's purpose is to fail CI on a clip that is not publishable, so a
    corpus leg cannot assert that everything passes: four sources were
    suppressed by the VAD gate before selection ran and two carry a
    silence-share finding whose acoustic measurement contradicts dead air —
    which the tool reports as a hard failure on purpose, because "not digital
    silence" does not establish that the material belongs in the clip and a
    person still has to listen.

    What the pin catches instead is the verdict *changing*: a check that
    silently inverts, a new false positive, or an emitted tree that shifts a
    clip from clean to failing. A change in either direction has to update
    this table and say why, which is the property that kept the audit honest.

    Read alongside ``test_every_word_of_the_committed_corpus_lands_in_exactly_one_utterance``:
    the mid-sentence check used to report failures on five of these seven
    projects, and every one was a pause removed between two transcript-adjacent
    words. None are reported now, and the ones that remain are named below.
    """
    from tools.editorial_audit import audit

    runs = sorted((TOOLS.parent / "corpus").glob("*_run/project"))
    assert runs, "no corpus runs found; the test below would pass vacuously"

    # The recorded verdict: which projects are clean and which are not, with the
    # reason the failing check states. Reasons are matched by prefix so a detail
    # that gains a figure still pins the same finding.
    EXPECTED_CLEAN = {
        "emwcon-panel-2018_run",
        "hadhazy-protest_run",
        "ooi-vertical_run",
        "pentagon-esper-briefing_run",
        "royal-society-cloke_run",
    }
    EXPECTED_FAILURES = {
        "alice-1903_run": "no selections.json",
        "hindi-baiga-interview_run": "no selections.json",
        "nasa360-ep5_run": "silence share",
        "roman-portrait-trailer_run": "no selections.json",
        "telugu-launch_run": "no selections.json",
        "telugu-tutorial_run": "silence share",
    }

    seen = {}
    for project in runs:
        ok, rep = audit(project)
        hard = [f"{name}: {detail}" for name, cok, detail in rep["findings"] if not cok]
        seen[project.parent.name] = (ok, hard)

    unexpected = []
    for name, (ok, hard) in sorted(seen.items()):
        if name in EXPECTED_CLEAN:
            if not ok:
                unexpected.append(f"{name}: expected clean, got {hard}")
        elif name in EXPECTED_FAILURES:
            want = EXPECTED_FAILURES[name]
            if ok:
                unexpected.append(f"{name}: expected a {want!r} failure, got clean")
            elif not any(want in h for h in hard):
                unexpected.append(f"{name}: expected a {want!r} failure, got {hard}")
        else:
            unexpected.append(f"{name}: no recorded verdict for this project")
    for name in sorted(set(EXPECTED_CLEAN) | set(EXPECTED_FAILURES)):
        if name not in seen:
            unexpected.append(f"{name}: recorded but not present in the corpus")
    assert not unexpected, (
        "the audit's verdict on the committed corpus changed; update the table "
        "and say why:\n" + "\n".join(unexpected))


def test_every_word_of_the_committed_corpus_lands_in_exactly_one_utterance():
    """The word-to-sentence join is what the mid-sentence check rests on.

    The check attributes a word to the utterance containing its start, so a
    word the transcript does not cover is a word the check cannot see and a
    word straddling two is a word it may attribute either way. On the seven
    committed projects that produced a timeline every word lands inside exactly
    one segment, so the join is exact rather than approximate — and 155 of the
    1755 words start precisely at a segment boundary, which is the case an
    off-by-one in the join misattributes. This pins both.
    """
    from tools.editorial_audit import _word_sentence_map, _word_spans

    checked = words_at_boundary = 0
    for project in sorted((TOOLS.parent / "corpus").glob("*_run/project")):
        selections = _load(project / "selections.json")
        transcript = _load(project / "transcript.json")
        if selections is None or transcript is None:
            continue
        segments = transcript.get("segments") or []
        if not segments:
            continue
        utterances = sorted((float(s["start"]), float(s["end"])) for s in segments)
        starts = {u[0] for u in utterances}
        for cand in selections:
            spans = _word_spans(cand)
            if not spans:
                continue
            sent_of = _word_sentence_map(cand, utterances)
            assert len(sent_of) == len(spans), "the map must answer one index per word"
            for (st, en, _t), s in zip(spans, sent_of):
                checked += 1
                words_at_boundary += st in starts
                assert s != -1, f"{project.parent.name}: word {st:.3f}s is in no utterance"
                lo, hi = utterances[s]
                assert lo <= st and en <= hi + 0.05, (
                    f"{project.parent.name}: word {st:.3f}-{en:.3f}s is not inside "
                    f"utterance {lo}-{hi}s")
    assert checked > 1000, f"only {checked} words checked; the corpus glob is wrong"
    assert words_at_boundary > 100, (
        f"only {words_at_boundary} words start exactly at a segment boundary; "
        "the off-by-one case this guards is no longer represented")


def test_the_corpus_candidate_words_are_already_the_kept_set():
    """Why the utterance source changes no verdict the corpus can reach.

    A selection's ``words`` list is not the transcript's words for that span —
    it is the words the selection kept. Measured over all seven selected
    projects, 1754 of the 1755 words lie wholly inside a retention interval.
    So at an interval boundary the word on each side is kept, every word
    between them is kept, and the word-loss criterion cannot fire; the
    sentence source is never the thing that decides the outcome. That is a
    property of the data, not of the check: a candidate whose word list
    carried an excised word would still be flagged, and the unit tests build
    exactly those candidates.

    The one word outside every interval is royal-society-cloke clip 00's last
    word, which begins exactly at the final interval's end — the boundary
    excludes it whole rather than shaving it, so it is clean for a different
    reason: the adjacent-word lookup lands on it twice and the check continues.

    Pinned because it is the fact a reader needs in order to trust the corpus
    and e2e legs. If a change makes selections carry their excised words, this
    test fails and the utterance source becomes observable through ``audit`` —
    at which point the corpus baseline above is the leg that moves.
    """
    from engine.edit_plan import retention_intervals
    from models.core import Candidate, Word
    from tools.editorial_audit import _word_spans

    def to_cand(cand: dict) -> Candidate:
        return Candidate(
            start=cand["start"], end=cand["end"], text=cand.get("text", ""),
            story_type=cand.get("story_type", ""), hook=cand.get("hook", ""),
            payoff=cand.get("payoff", ""),
            editorial_score=cand.get("editorial_score", 0.0),
            virality_score=cand.get("virality_score", 0.0),
            context_score=cand.get("context_score", 0.0),
            confidence=cand.get("confidence", 0.0),
            reason=cand.get("reason", ""),
            visual_potential=cand.get("visual_potential", 0.0),
            words=[Word(**w) for w in cand.get("words", [])])

    total = outside = 0
    for project in sorted((TOOLS.parent / "corpus").glob("*_run/project")):
        selections = _load(project / "selections.json")
        if selections is None:
            continue
        for cand in selections:
            spans = _word_spans(cand)
            if not spans:
                continue
            intervals = retention_intervals(to_cand(cand))
            for st, en, _t in spans:
                total += 1
                outside += not any(a <= st and en <= b for a, b in intervals)
    assert total > 1000, f"only {total} words checked; the corpus glob is wrong"
    assert outside <= 1, (
        f"{outside} of {total} candidate words fall outside every retention "
        "interval; the corpus now carries excised words, so the utterance "
        "source is observable through audit() and this test's claim is stale")


# --- the caption parser the audit reads through ----------------------------


def test_the_parser_reads_a_real_committed_caption_file():
    """The audit's cue checks are only as good as its SRT parse, so the parser
    is pinned against a committed artifact rather than a fixture: 20 cues on
    nasa360-ep5 clip 01, with real timestamps."""
    from tools.editorial_audit import _parse_srt_seconds

    srt = TOOLS.parent / "corpus" / "nasa360-ep5_run" / "project" / "captions" / "01.srt"
    if not srt.is_file():
        pytest.skip("nasa360-ep5 captions not present in this checkout")
    cues = _parse_srt_seconds(srt)
    assert len(cues) >= 10, f"a real shipped caption file parsed to {len(cues)} cues"
    # A timestamp that survived the parse has to be a real number, not 0.0 by
    # default — a parse that silently zeroed every cue would pass a length check.
    assert all(c["end"] > c["start"] for c in cues), "a cue does not end after it starts"


@pytest.mark.parametrize("raw", [
    "",
    "1\nhello world\n",
    "1\n00:00:00,000 --> 00:00:01,000\n",
    "1\n00:00:00 --> 00:00:01,000\ntext\n",
    "1\n\n",
], ids=["empty", "no timestamp line", "no text line", "bad timestamp", "blank block"])
def test_a_malformed_caption_file_parses_to_zero_cues_not_an_exception(tmp_path, raw):
    """Every malformed shape degrades to zero cues.

    This is the load-bearing property of the whole caption check family:
    ``check_caption_text_is_transcript_text`` and ``check_cues_fit_the_render``
    both short-circuit on an empty cue list, so a parse that raised — or worse,
    one that invented cues from a malformed block — would turn a caption
    regression into either a crash or a silent PASS. Zero is the honest reading
    of a file with no usable cues in it.
    """
    from tools.editorial_audit import _parse_srt_seconds

    srt = tmp_path / "01.srt"
    srt.write_text(raw, encoding="utf-8")
    assert _parse_srt_seconds(srt) == []
