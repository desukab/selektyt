# Blinded editorial evaluation — the execution protocol

> **SUPERSEDED by `docs/HUMAN_EDITORIAL_EVALUATION.md`.** That document carries
> this one's blinding design and role separation forward unchanged, and adds
> what the corpus run proved this one lacks: a story-selection axis, a
> transcript-against-reality gate, and a structural rule that mechanical and
> editorial scores can never be combined. Read `HUMAN_EDITORIAL_EVALUATION.md`
> for the executable protocol; this file is kept as the earlier design of
> record.

This is P0 item 4 of the current phase. It exists because the rubric in
`docs/EDITORIAL_EVALUATION.md` and the study design in `docs/TIME_SAVINGS.md`
§7 are both incomplete in the same way: **neither specifies how the blind is
actually maintained.** A rubric that a rater can see through measures nothing,
and a "blinded study" whose blinding is left to good intentions is not one.

**Status: DESIGNED, NOT RUN.** No editor has been recruited, contacted or
briefed. No editor will be, without explicit founder approval — that is a
standing rule and it is not circumvented by anything in this document.

## What the blind has to defeat

A professional editor can identify their own work, and can often identify a
machine's, from cues that have nothing to do with quality:

- **Timeline signature.** SelekyT cuts on its own frame grid with its own
  in/out habits. An editor who has seen one SelekyT timeline recognises the
  next one.
- **Caption styling.** The preset is visible on screen and is a fingerprint.
- **Selection style.** Which stories got picked is a style, and a rater who
  has seen Arm B outputs once is primed for the next source.
- **Audio mix.** AAC 160k from an ffmpeg chain sounds like an ffmpeg chain.

The protocol below removes each one explicitly rather than assuming a rater
"won't notice".

## Roles, and the one person who must exist

| Role | Sees arm labels? | Sees source footage? | Count |
|---|---|---|---|
| **Editor** (produces deliverables) | no — and in Arm B does not know the assistant was SelekyT | yes, that is the job | 6 |
| **Rater** (scores deliverables) | no | no — only the finished clips | 3 |
| **Coordinator** (the only unblinded person) | yes | yes | 1 |
| **Analyst** (computes results) | no — receives coded data only | no | 1 |

The coordinator is a real role, not a formality: someone has to keep the
key. The study is single-blind without them, and "nobody needed to know" is
how a blind quietly becomes open.

**The coordinator must not be the analyst.** If one person holds both the key
and the conclusion, the study is unblinded in effect regardless of procedure.

## Materials, prepared before any editor is recruited

Everything below is fixed before the first session, and frozen. A protocol
that is still being written while editors are working is not blinded.

1. **Three sources** from `corpus/manifest.json`, chosen to be maximally
   unlike each other: one English multi-speaker (`nasa360-ep5`), one
   non-Roman-script (`telugu-tutorial` or `hindi-baiga-interview`), one
   native 9:16 (`ooi-vertical` or `roman-portrait-trailer`). Selection
   **must** be from sources the corpus has actually completed successfully —
   see the status note at the bottom.
2. **The brief**, identical for both arms, fixed in advance: *"deliver 6
   clips, 9–25 s each, in 16:9 and 9:16, with captions, plus per-cut
   editable timelines."* The number 6 matches `--limit` defaults so neither
   arm is asking for something the tool cannot produce.
3. **The rubric** in `docs/EDITORIAL_EVALUATION.md` §1, printed on paper with
   the anchors visible. Raters score 1–5 on all seven dimensions; there is no
   free-text score field, because an unanchored field is where bias enters.
4. **A neutralising pass**, applied to **both** arms identically:
   - Captions re-rendered through one fixed style, so Arm A's hand-set
     captions and Arm B's preset captions look the same on screen.
   - All audio normalised to one loudness target (LUFS), one codec, one
     bitrate. This is the measure that removes the encode fingerprint.
   - Identical container and filename pattern for every clip.
5. **Coded envelopes.** Every deliverable set is copied into a directory
   named `sub_01/` … `sub_NN/` by the coordinator. The mapping from envelope
   to (editor, arm, source) is written to one file, `key.csv`, which the
   analyst never receives and which is sealed until every rating is in.

The neutralising pass is the load-bearing step and it is applied to Arm A
too. Normalising only the machine's output would make Arm B identifiable by
the very fact it was normalised.

## Session procedure

**Arm A (manual).** The editor gets the raw source and the brief. The clock
runs from brief-acknowledged to the editor declaring *ready to deliver*.
Coordinator records wall-clock; the editor works in their own NLE.

**Arm B (assisted).** The editor gets the same source, the same brief, and a
folder labelled "assistant output, provenance undisclosed". It contains
SelekyT's renders, per-cut EDL/FCPXML and captions. The editor is free to
keep, modify or discard anything, and is told only that the folder came from
"an automated assistant" — not the product name, not the model, not that
selection was automatic. The clock runs on the same start and end events.

**Order.** Each editor does both arms, order counterbalanced across the six
(editors 1,3,5 do A→B; 2,4,6 do B→A). Within-subject design so each editor
is their own control and individual speed differences cancel.

**Source assignment.** Each editor sees all three sources, but **no editor
does both arms on the same source** — the risk is that having seen the
machine's picks, they cannot unsee them when cutting manually. Editors are
randomised against a source×arm grid fixed in advance by the coordinator.

**Rating.** Raters receive the envelopes in random order with the neutralised
clips, the rubric, and nothing else. No transcript, no timeline file, no
provenance. Ratings are written into a form whose only identifier is the
envelope code. The analyst computes scores by code and is unblinded only
after the report is written.

## What is measured, and what is not

**Machine mechanical correctness** is measured by `tools/nle_check.py` and
`tools/editorial_audit.py` on the artifacts, and is reported separately from
every human score. It is not part of the rubric and never averaged into it:
a clip can be geometrically perfect and editorially hollow, and mixing the
two would let a good frame measurement launder a bad cut.

**Editorial quality** is the raters' seven scores, and only those.

**Time** is wall-clock to *ready-to-deliver*, not to first pass. Arm B that
is fast and then re-cut from scratch counts as slow.

**Correction volume** is counted from the timeline diff between what Arm B
handed over and what shipped: clips kept unmodified, clips trimmed, clips
discarded, clips added by hand. This is the number that distinguishes
"assistant" from "raw material".

## Falsifier — restated, and tightened

From `docs/TIME_SAVINGS.md` §7, unchanged: the claim is **false** if Arm B is
not at least 30% faster at equal-or-better blind quality.

This protocol adds two falsifiers of its own:

- **The blind failed if** a rater's accuracy at guessing which arm produced
  which set is materially above chance. That is measured at debrief and
  reported alongside the results. A study whose blind was seen through has
  no results, regardless of what the numbers say.
- **The study is not evidence of selection quality if** the discard rate in
  Arm B exceeds 50%: if editors throw out most of the machine's picks, the
  tool supplied raw material, not an assistant, and any time saving is in
  the *rendering*, not the *editing* — a different and much smaller claim.

## Prerequisites that are not yet met

Stated so the study cannot be started before it is ready:

1. **The corpus must have succeeded on the three chosen sources.** As of
   2026-09-19 the corpus has completed one source (`nasa360-ep5`) and is
   running. A study built on sources the pipeline has never processed would
   be measuring the pipeline's behaviour on unseen input with no baseline.
2. **The Telugu/Hindi source must produce a usable transcript.** If the
   non-Roman-script source fails transcription, the study is English-only and
   must say so rather than quietly substituting a third English tape.
3. **Founder approval to contact editors.** Not requested, not implied.
4. **Budget.** ≈$867 at the rates in `docs/TIME_SAVINGS.md` §7, plus
   recruitment. Not spent, not committed.

## What may be said while this is unrun

That the rubric, the blinding procedure, the falsifiers and the budget exist
and are recorded here. Not that editors have used SelekyT, not that time
savings were measured, and not that any human has judged the cuts. The
18×–73× figure in `docs/TIME_SAVINGS.md` remains a model and is labelled one.
