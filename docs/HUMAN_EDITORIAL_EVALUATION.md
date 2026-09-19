# Human editorial evaluation — the protocol that settles what the audits cannot

**STATUS: DESIGNED, NOT RUN — no participant contacted, founder approval for
external contact not given.**

No editor, rater, transcript checker, coordinator or analyst has been
recruited, contacted, briefed or scheduled. No consent form has been issued.
No session has been run, no rating exists, no score exists, no time has been
measured, and no pilot has been conducted. Every instrument in this document is
written so it can be executed the moment a participant exists; it contains no
number that implies any of these things has happened. Where a quantity appears,
it is either an arithmetic property of the chosen statistical test (stated as
such and checkable) or a threshold set in advance.

This document **supersedes `docs/BLINDED_PROTOCOL.md`**, which remains in the
repository as the earlier, narrower design. `BLINDED_PROTOCOL.md` got the
blinding and the roles right, and those are carried forward here unchanged in
substance. It was incomplete in three ways that the corpus run exposed:

1. Its rubric has seven dimensions and no axis for **story selection** — the
   single most consequential editorial decision the product makes.
2. It has no instrument for the failure the corpus actually found: a transcript
   that is phonetic noise and passes every mechanical check because the check
   compares captions against the transcript, not against reality.
3. It states the mechanical/editorial separation as a reporting convention
   rather than as a structural invariant that cannot be collapsed by a
   well-meaning analyst.

`BLINDED_PROTOCOL.md` is marked superseded at its head with a pointer to this
document. Its §"Falsifier" is retained; this document's §10 generalises it.

---

## 1. Why this protocol exists

`docs/CORPUS_RESULTS.md` (2026-09-19) reports 7 of 11 corpus sources producing
deliverables, with `tools/nle_check.py` at **0 FAIL** and
`tools/editorial_audit.py` passing on all seven. It also reports that **three of
those seven "OK" sources are not usable**, and that no mechanical audit can see
it:

- `telugu-tutorial` shipped one clip whose text is fluent-looking Telugu script
  that is phonetic noise. It passes
  `editorial_audit.py:check_caption_text_is_transcript_text` because the
  captions faithfully reproduce a transcript that is itself wrong.
- `hadhazy-protest` produced 38 individually-plausible Hungarian fragments that
  do not reconstruct what was said.
- `hindi-baiga-interview` produced 3 low-quality Devanagari segments and no
  story passed the editorial gate.

The general failure is not a bug in any check. It is that **every mechanical
audit is self-referential**: it compares the artifact against the transcript
that generated it. There is no ground truth on the video side. Geometry,
timing, EDL/FCPXML agreement and caption-transcript equality can all be perfect
on a clip whose words do not match the speech. A machine audit of a bad
transcript passes because it has nothing true to compare against.

The phase instruction is absolute: **automated metrics must not stand in for
human editorial judgment.** This document is the instrument that makes that
enforceable rather than aspirational.

---

## 2. Design invariant: mechanical and editorial are reported separately, and cannot be averaged

This is the load-bearing rule of the whole protocol. It is stated as an
invariant so that it survives a later analyst who wants one headline number.

**MECHANICAL CORRECTNESS** — does the file open, do the timings match, does the
caption equal the transcript, is the geometry right, is the loudness legal, does
the EDL re-import — is computed by `tools/nle_check.py` and
`tools/editorial_audit.py` on the artifacts. It is reported as a **pass/fail
findings list per clip**, in a separate section of the report, on a separate
page of the form, and in a separate data file (`mechanical_<envelope>.json`).

**EDITORIAL QUALITY** is the raters' anchored scores and nothing else.

Four rules make the separation structural rather than conventional:

1. **Different scales.** Mechanical is binary per check (PASS/FAIL) plus a
   numeric detail. Editorial is 1–5 against anchors. There is no unit in which
   the two can be added, because there is no conversion factor defined anywhere
   in this protocol, and defining one is forbidden by §11.
2. **Different instruments.** Mechanical is computed by a script on files.
   Editorial is recorded by a human on a form. The two never pass through the
   same field.
3. **No composite "quality score."** There is no `total_quality`, no weighted
   blend, no "editorial index." A rater's per-axis scores are reported as a
   profile and, for the falsifier tests only, reduced to a per-axis comparison
   between arms (§10). The summary statistic that carries the study is
   **overall usefulness (§4.11)**, which is itself a human judgement, not a
   mixture.
4. **The mechanical report cannot rescue an editorial failure, and the reverse
   is also true.** A clip that is geometrically perfect and editorially hollow
   is reported as exactly that. A clip with a loudness violation that a rater
   loved is reported as exactly that too. Neither case is averaged, netted, or
   described as "overall good."

The reason is not tidiness. It is that a good frame measurement can launder a
bad cut, and the corpus already contains the proof: a clip with perfect
geometry, perfect timing, a perfect EDL and a transcript that is not what was
said.

---

## 3. The T-gate: transcript against reality

This is the instrument `BLINDED_PROTOCOL.md` lacks and the corpus proved is
required. It runs **before** any editorial rating, and its result is a
prerequisite, not a score.

**What it is.** A language-competent checker, who speaks the source's language
at native or near-native fluency, reads the shipped transcript against the
**source audio** (not the clip — the source, so the checker can hear what was
said before and after the selection) and classifies it.

**The prompt the checker sees:**

> You are given the source media and the transcript the system produced. Listen
> to the source and follow the transcript. Then answer one question with one of
> four labels, and quote up to three examples supporting it.
>
> **TRUE** — The transcript is a faithful rendering of what was said. Names,
> numerals and technical terms are correct. A viewer reading it would understand
> the same thing as a viewer listening.
>
> **PARTLY_TRUE** — Intelligible and mostly correct, but with material errors:
> mangled names or numbers, invented or omitted words, or sentences that say
> something different from the speech. Correctable, but a human must fix it.
>
> **NOISE** — Individually plausible words or characters that do not reconstruct
> what was said. The transcript has the shape of language and is not the
> language spoken.
>
> **WRONG_LANGUAGE / NO_SPEECH** — The transcript is in the wrong language, or
> there is no speech to transcribe.

**Why four labels and not a 1–5.** A transcript is either adequate as a basis
for an edit or it is not. A scale invites averaging; a label forces a decision
that gates everything downstream.

**Consequence table — the gate part:**

| T-gate label | What happens to that source |
|---|---|
| **TRUE** | Source enters the study. Ratings and times are study results. |
| **PARTLY_TRUE** | Source enters, but the transcript errors are recorded as pre-existing defects. Editors in Arm B are told the transcript may contain errors — this is the honest condition, since a real user's transcript is not guaranteed clean. The count of transcript errors the editor corrects is reported separately (§4.10). |
| **NOISE** | **Source does not enter the study.** Any rating or time collected on it is reported as a pipeline defect, not an editorial result, and the failure is filed against the transcription layer. |
| **WRONG_LANGUAGE / NO_SPEECH** | Source does not enter. Recorded as a pipeline defect. |

**Language coverage is a hard prerequisite.** A source cannot enter the study
without a checker for its language. The three non-English corpus sources are
Telugu, Hindi and Hungarian; the study needs one checker per language. If a
language has no checker, that source is dropped and the study is reported as
English-only rather than quietly substituted — this is the same rule
`BLINDED_PROTOCOL.md` applied, and it is non-negotiable because a monolingual
rater scoring a Telugu clip will produce a confidence-rated guess, not a
judgement.

**Status: PENDING EXTERNAL VALIDATION.** No checker has been approached.

---

## 4. Editorial axes — the rating instrument

Eleven axes. The rater's form contains, for each axis, the question, the 1–5
anchors, and a mandatory one-line justification quoting a timestamp or a cue.
An unanchored free-text field is where bias enters, so free text is restricted
to that justification line and to a final open comment.

Axes E2–E8 are the seven-dimension rubric in `docs/EDITORIAL_EVALUATION.md` §1,
carried forward so the two documents stay reconciled; E1, E9, E10 and E11 are
new and are marked as such. The mapping to the P-criteria in
`docs/EVALUATION.md` §4 is given per axis.

Anchors follow one convention throughout: **3 = shippable with a note, 2 = an
assistant would be sent back, 1 = wrong, not just weak, 5 = publish unchanged,
4 = publish with one small change.**

### 4.1 E1 — Story selection *(new axis)*

The rubric in `docs/EDITORIAL_EVALUATION.md` never asks this question, and it is
the decision that determines everything downstream. Which moments were chosen.

**Prompt:**

> You are seeing the delivered set of clips from a tape you have not seen.
> Judging only by what is in front of you: were the right moments chosen?
>
> 5 — The selection is what a good editor would have found. The strongest
> stories in the tape are here and the weak ones are not.
> 4 — Strong selection with one inclusion or omission a human would query.
> 3 — Serviceable. Nothing misleading, but the set reads as *available* rather
> than *found*.
> 2 — The selection misses the obvious stories, or ships material a working
> editor would have discarded as filler.
> 1 — The selection is actively misleading about what the tape contains, or
> every clip is interchangeable filler.

**Pass means:** ≥3 on a majority of an arm's six envelopes. Because each envelope
is rated by exactly one rater (§7), a per-envelope score is a per-envelope
verdict; an arm-level statement aggregates across that arm's six envelopes, so a
majority means **4 of 6**, as in F1a (§10). Maps to P10 (selection overlap) and
D6 in `docs/EVALUATION.md` §3.

### 4.2 E2 — Hook quality

Carried from `docs/EDITORIAL_EVALUATION.md` §1.1. Does the first 3 seconds earn
the next 3 seconds, standing alone, for a cold viewer?

Anchors: 5 = opening sentence complete, self-contained, creates an information
gap. 3 = complete sentence but generic, needs a cold-open graphic. 2 = opens on
a clause or discourse marker depending on speech the viewer never heard.
1 = opens mid-word, or on a name with no antecedent.

**Pass means:** ≥3.

### 4.3 E3 — Context preservation

Carried from §1.3. Can a cold viewer answer *who, what, why* from the clip
alone? Anchors: 5 = every pronoun has an antecedent, every name introduced.
3 = a name used without introduction but the clip tracks. 2 = the referent of
the opening clause is entirely outside the clip. 1 = actively misleading.

**Pass means:** ≥3.

### 4.4 E4 — Payoff completeness

Carried from §1.2. Does the clip land its own point inside its own runtime?
Anchors: 5 = setup and payoff both inside the clip. 3 = ends on a complete
thought that is a restatement. 2 = ends on a question whose answer begins ~2 s
past the out-point. 1 = no payoff; the speaker is still building.

**Pass means:** ≥3.

### 4.5 E5 — Pacing and rhythm *(split from §1.4)*

`BLINDED_PROTOCOL.md`'s rubric merged pacing into cut precision. They are
different failures: a clip can be cut cleanly and still be unwatchable, and
pacing is the axis on which `silence_share` (the one metric the reference run
fails, at 40.8% on clip 05) is supposed to be checked. Splitting them keeps the
human judgement from being absorbed by the machine measurement.

**Prompt:**

> Watch the clip as a viewer. Is the rhythm tolerable?
>
> 5 — Breath at both ends, pauses trimmed, no false starts or repairs left in,
> the energy holds through the whole runtime.
> 4 — One internal imperfection a human would clean.
> 3 — Watchable but draggy — retained dead air, a repetition, or a run of
> low-energy lines.
> 2 — The retained pause pattern creates a rhythm the viewer hears as broken;
> the clip feels longer than it is.
> 1 — Mostly dead air, or so compressed that the speech is unintelligible.

**Pass means:** ≥3. Where a rater scores E5 ≤2 and the mechanical report shows
`silence_share` > 30%, the two are reported side by side as corroborating
evidence — the machine measurement gains its validity from the human judgement,
never the reverse.

### 4.6 E6 — Cut quality

Carried from §1.4, boundaries only. Do the cuts land where a human would put
them? Anchors: 5 = in and out at sentence edges with air at both ends.
3 = word-edge boundaries but no air around the final word, or a visible false
start retained. 2 = boundary clips a phoneme. 1 = boundary inside a word.

Note for the analyst: E6 overlaps `editorial_audit.py`'s
`check_cuts_land_between_words` and `check_last_word_is_not_shaved`. The
machine checks and the human score are **reported side by side and not
combined** (§2). A disagreement between them — machine PASS, human 2 — is a
finding about the audit, and it is exactly the finding the corpus suggests is
available.

**Pass means:** ≥3.

### 4.7 E7 — Caption quality

Carried from §1.5, with one change forced by the corpus finding: the rater for
a non-English source **must be language-competent**, and the rater is explicitly
told the captions may be a faithful rendering of an incorrect transcript, so
the check is against the **speech**, not against the shipped text.

**Prompt:**

> Read the captions against the speech you can hear, and judge them as written
> language. Are they readable and correct?
>
> 5 — Two-line cues, 1.2–2.5 s each, ≤ ~20 chars/sec, numbers and proper nouns
> correct, no orphan words, punctuation respected.
> 4 — Readable and correct but one-line cues, or slightly fast.
> 3 — Correct text but flashing cues or orphan single words as full cues.
> 2 — Verbatim ASR artifacts shipped (mangled numbers, wrong words, lowercased
> acronyms) *and* flash cues.
> 1 — Captions contradict the speech, or cue timing does not match the edit.
>
> **If the captions do not match what the speaker said, score 1 regardless of
> typography, and note "transcript error" in the justification.** A
> well-typeset mistranscription is worse than an ugly correct one.

**Pass means:** ≥3, and any score of 1 with a "transcript error" justification
is routed back through the T-gate (§3) as a suspected NOISE label.

### 4.8 E8 — Reframing

Carried from §1.6. Is the vertical version actually vertical and is the picture
geometry correct? Anchors: 5 = exact target aspect, subject tracked into the
safe area, no distortion. 3 = correct aspect but a static centre crop with no
subject awareness. 2 = the file's own pixel/sample aspect math does not equal
the claimed ratio. 1 = wrong aspect and visibly distorted, so the vertical is
unusable as delivered.

This is the axis where mechanical and editorial most overlap; the invariant in
§2 applies with full force. `nle_check.py` can establish the aspect ratio is
arithmetic-correct; only a human can establish a head was bisected.

**Pass means:** ≥3.

### 4.9 E9 — Timeline usefulness and deliverability *(new axis)*

Carried partly from §1.7, but the question is asked of the editor who used the
package, not the rater who watched the clip. This is the adoption axis.

**Prompt (to the editor, post-session, on the Arm B handover only):**

> You were handed timelines, captions and renders. Rate the package as a handover.
>
> 5 — I imported the timeline, it conformed, and it was the spine of my finished
> edit.
> 4 — I imported it and made targeted edits; the original structure survived.
> 3 — I imported it and used it as a reference/assembly, but rebuilt the real
> structure myself.
> 2 — I opened it, found it faster to start from scratch, and did.
> 1 — It did not import, or imported wrong enough that it cost me time.
>
> Free text (required if you score ≤3): what specifically would have to change
> for you to score 4?

**Pass means:** ≥3. Maps to P1 (NLE interchange) and D3/D6 in
`docs/EVALUATION.md`. Note that `docs/EVALUATION.md` §3-D3 records that **no NLE
has ever imported an exported SelekyT timeline** — a gap E9 is the first
instrument that would close, and one that no unit test can substitute for,
because the question is whether a *different* program's importer accepts the
file.

### 4.10 E10 — Manual corrections required *(new axis, counted not scored)*

The number that distinguishes an assistant from raw material. It is **counted
from the timeline diff between the Arm B handover and what shipped**, on the
scheme already fixed in `docs/TIME_SAVINGS.md` §7 and `BLINDED_PROTOCOL.md`:
clips **kept unmodified**, **trimmed**, **discarded**, **added by hand**, plus
**transcript corrections** (added by the T-gate in §3, since a transcript error
the editor silently fixes is a cost the previous design did not capture).

The rater-facing scale exists only because a count cannot express effort:

> 5 — I changed nothing.
> 4 — Small trims and caption fixes, under a quarter of the delivered runtime.
> 3 — Substantial rework of part of the set; the rest shipped as delivered.
> 2 — I re-cut most of it; only a few selections survived.
> 1 — I discarded everything and started over.

**Pass means:** score ≥3 **and** discard rate ≤50%. The discard threshold is the
`BLINDED_PROTOCOL.md` falsifier and is retained here (§10, F3).

### 4.11 E11 — Overall usefulness *(new axis, the summary judgement)*

This is the only summary number the study reports, and it is a human judgement,
not a composite of E1–E10 (§2, rule 3).

**Prompt:**

> Forget the axes. You are the editor responsible for posting this. What do you
> do with this set?
>
> 5 — **I would post this unchanged.**
> 4 — I would post it after small fixes.
> 3 — **I would keep it and rewrite it.** Shippable only with substantial work.
> 2 — I would delete this and keep the source.
> 1 — **I would delete this and it would change nothing.**
>
> Free text (required): the single change that would move your score up one
> point.

**Pass means:** ≥3, i.e. the set clears "keep and rewrite."

---

## 5. Blinding — what the blind must defeat, concretely

The risk is not that a rater is malicious. It is that a professional recognises
the machine's signature and scores it as a machine — in either direction, since
a rater who sympathises with a tool inflates it as readily as one who resents it
deflates it.

**Named tells, and the neutralising pass applied to both arms identically.**

| Tell | How a rater recognises it | Neutralising measure |
|---|---|---|
| **Timeline signature** | SelekyT cuts on its own frame grid with in/out habits that repeat across sources; one recognised timeline identifies the next. | Raters never see timeline files, EDL, FCPXML, filenames or metadata (§6). The visible artifact is the rendered clip only. |
| **Caption styling** | The preset is visible on screen and is a fingerprint. | All captions from both arms are re-rendered through **one fixed style** — one font, one weight, one size, one position, one cue-duration policy — applied by the coordinator before envelopes are sealed. |
| **Selection style** | Which stories got picked is a style; one sighting primes the next. | Between-rater source assignment (§7): **no rater sees both arms of the same source**, so the complementary content of the other arm cannot be inferred. |
| **Audio / encode fingerprint** | AAC 160k from an ffmpeg chain sounds like an ffmpeg chain. | All audio normalised to one loudness target, one codec, one bitrate, one channel layout, applied to **both** arms. |
| **Container and filename** | `.mkv` vs `.mov`, `shorts/01.mp4` vs an editor's `final_v3.mp4`. | Identical container, identical resolution ladder, identical filename pattern (`sub_NN/clip_NN.mp4`) for every clip in every envelope. |
| **Clip count and runtime profile** | Six clips of 9–25 s each is itself a signature if the human arm ships four clips of 40 s. | The brief fixes clip count and length range for **both** arms, so the delivered profile is a constraint of the task rather than a tell. |
| **Provenance leakage in speech** | An editor in Arm B who was told the product name mentions it in debrief. | Arm B handover is labelled "assistant output, provenance undisclosed" (§8). Debrief is collected **after** all ratings are in. |
| **Content complementarity** | A rater who sees both cuts of one tape recognises the other arm by what it *left out*. | This is the tell a neutralising pass cannot fix. It is defeated structurally, by the between-rater assignment. |

**The neutralising pass is applied to Arm A too.** Normalising only the
machine's output would make Arm B identifiable by the very fact it was
normalised. This is the single most common way a blind quietly becomes open.

**Materials frozen before anyone is recruited**, per `BLINDED_PROTOCOL.md`:
three sources chosen to be maximally unlike each other, drawn only from sources
the corpus has completed successfully (§12); the brief fixed in advance
("deliver 6 clips, 9–25 s each, in 16:9 and 9:16, with captions, plus per-cut
editable timelines"); the rubric printed with anchors visible; coded envelopes
`sub_01/` … `sub_NN/` prepared by the coordinator, with the mapping to
(editor, arm, source) written to one file, `key.csv`, that the analyst never
receives and that is sealed until every rating is in.

A protocol that is still being written while editors are working is not
blinded. Everything in §4, §5 and §10 is frozen at recruitment.

---

## 6. Roles, and the one unblinded person

| Role | Sees arm labels? | Sees source footage? | Count |
|---|---|---|---|
| **Editor** — produces the Arm A baseline and the Arm B finish | no; in Arm B does not know the assistant was SelekyT | yes, that is the job | 6 |
| **Rater** — scores envelopes blind | no | no — finished clips only | 3 |
| **Transcript checker (T-gate)** — reads transcript against source audio | n/a (not an arm judgement) | yes, audio only | 1 per language (≥4) |
| **Coordinator** — the only unblinded person; holds the randomisation and `key.csv` | yes | yes | 1 |
| **Analyst** — computes results | no — coded data only | no | 1 |

**The coordinator must not be the analyst, and neither may be an editor or a
rater.** If one person holds both the key and the conclusion, the study is
unblinded in effect regardless of procedure. If the analyst has ever seen the
unblinded mapping, the report is invalid and must be recomputed by a replacement
analyst from the coded data alone.

The coordinator is a real role, not a formality: someone has to keep the key,
prepare the envelopes, and be the person the editors ask questions of so that
the question itself does not leak the arm. "Nobody needed to know" is how a
blind quietly becomes open.

---

## 7. Design: within-rater is rejected, between-rater is chosen — and why

Two decisions, often conflated. They get opposite answers, and the asymmetry is
the point.

**Editors: WITHIN-subject, one source each.** Each editor does **both arms of
exactly one assigned source**, order counterbalanced (editors 1, 3, 5 do A→B;
2, 4, 6 do B→A). Two editors are assigned to each of the three sources. This
yields 6 editors × 2 sessions = **12 sessions, 6 per arm**, and therefore **six
paired differences** — one per editor, Arm B minus Arm A on *that editor's
source*, so each editor is their own control and individual speed differences,
which vary by an order of magnitude between working editors, cancel out of the
time comparison. A between-editor design at this size would be measuring editor
variance, not the tool.

An earlier draft of this design forbade both-arms-on-one-source, on the
argument that "having seen the machine's picks, an editor cannot unsee them."
That prohibition is dropped, for two reasons. First, it made the primary
outcome uncomputable: splitting each editor's arms across different sources
destroys the pairing, and a cross-source difference is confounded with source
difficulty anyway. Second, the argument it rests on is a *carryover* argument,
and carryover is precisely what a counterbalanced crossover is for — it does
not eliminate carryover, it makes it estimable and directionally balanced
across the sample. Carryover is reported at debrief (§10, F2) rather than
assumed away.

**Raters: BETWEEN-subject on source.** **No rater sees both arms of the same
source.** This is a deliberate departure from the panel design in
`docs/TIME_SAVINGS.md` §7, which did not specify the constraint, and it is the
defence against the one tell a neutralising pass cannot fix (§5, last row): a
rater holding two cuts of the same tape can identify the machine arm by
*complementary content* — the sentences the other arm used.

The cost is that rater-level effects are confounded with arm. That is accepted,
and mitigated two ways: (a) every rater rates envelopes from **both** arms, just
never both arms of the **same** source, so no rater is an "all-machine" or
"all-human" rater; (b) each envelope is rated by exactly one rater, and the
analysis compares arms using the per-axis distributions across raters rather
than paired per-envelope differences.

**Concrete assignment, for 12 envelopes and 3 raters.** Each editor produces one
deliverable set per arm, so 12 sessions yield **12 envelopes** — two per
(source, arm) combination, one from each of the two editors on that source. Each
rater therefore receives **4 envelopes**, assigned so that no rater holds both
arms of one source:

| Rater | Envelopes |
|---|---|
| R1 | source 1 / Arm A (both editors), source 2 / Arm B (both editors) |
| R2 | source 2 / Arm A (both editors), source 3 / Arm B (both editors) |
| R3 | source 3 / Arm A (both editors), source 1 / Arm B (both editors) |

Every envelope is rated exactly once, so every editor's deliverable set receives
exactly one rating and no editor's work is discarded. Each rater sees both arms,
two sources, and never the same source in both arms — the complementarity tell
in §5 cannot fire. A side benefit of the duplication: because each rater scores
two independent cuts of the *same* (source, arm) pair, within-rater consistency
is measurable per rater, which is a check on rater reliability this design gets
for free.

**Order effects.** Envelope presentation order within each rater's set of four
is randomised per rater by the coordinator, with the constraint that the two
envelopes of the same (source, arm) pair are never presented adjacently, so a
rater does not experience them as a comparison exercise. The A-first/B-first
order within each rater's set is balanced across raters so that arm is not
confounded with position-in-session. Raters are told they will see clips from
**several unrelated sources** and are not told how many sources or editors there
are, so the four-envelope structure does not itself reveal the design.

**Source assignment for editors.** Editors are assigned to sources in pairs
against a fixed grid held by the coordinator: editors 1 and 2 on source 1, 3 and
4 on source 2, 5 and 6 on source 3. Within each pair the arm orders are opposed
(one A→B, one B→A), so for each source both orderings are represented and order
is not confounded with source. Each editor therefore sees exactly one source and
both of its arms — 12 sessions, 6 per arm, 6 paired differences.

---

## 8. Baseline construction — Arm A

The machine arm is only as meaningful as its human baseline. Three rules keep
the comparison about the **edit** rather than about tooling access.

1. **Same brief, same source, same target length and platform, same clip count.**
   Both arms receive the identical written brief with the identical deliverable
   list. If the brief asks the human for six clips and the machine for three,
   the comparison is over before it starts.
2. **The editor works in their own NLE, with their own tools, their own way.**
   They are not asked to use a tool they do not use. The baseline is *a
   professional editor doing their job*, not *an editor handicapped for the
   study*.
3. **The clock is the same instrument in both arms.** It runs from
   brief-acknowledged to the editor declaring **"ready to deliver"** — not to
   first pass. An Arm B that is fast and then re-cut from scratch counts as
   slow. This is the definition already fixed in `docs/TIME_SAVINGS.md` §7 and
   `BLINDED_PROTOCOL.md`, and it is not redefined here.

**Arm B handover.** The editor receives the same source, the same brief, and a
folder labelled **"assistant output, provenance undisclosed"**, containing the
renders, per-cut EDL/FCPXML and captions. The editor is free to keep, modify or
discard anything, and is told only that the folder came from "an automated
assistant" — not the product name, not the model, not that selection was
automatic. The clock runs on the same start and end events.

**The editor is a participant, not a reviewer.** Post-session, the editor
completes E9 and E10 on the Arm B handover and gives the free-text response. The
editor never sees ratings, never sees the other arm's deliverables, and is
debriefed only after all ratings are in.

---

## 9. Time measurement — hooked in, not run here

The time instrument is **not duplicated**. `docs/TIME_SAVINGS.md` §7 defines it:
wall-clock to ready-to-deliver as primary outcome, adoption and re-cut rate as
secondary, blind panel as the mandatory quality control, and the cost table
(≈$867 plus recruitment, ASSUMPTION, unspent). That file is the specification of
the clock; this file is the specification of the *judgement* the clock has to be
paired with. **Neither instrument is complete without the other**, and the two
agree by construction on these five definitions:

| Quantity | Definition (fixed in both documents) |
|---|---|
| Session time | brief-acknowledged → editor-declared "ready to deliver" |
| Correction volume | clips kept unmodified / trimmed / discarded / added by hand, from the timeline diff between Arm B handover and shipped edit |
| Transcript corrections | counted separately, from the T-gate (§3) — a category `docs/TIME_SAVINGS.md` does not yet have and which this protocol adds |
| Quality control | the blind rubric in §4, run per §7 |
| Primary claim | Arm B must be faster **at equal-or-better blind quality**; speed alone is a downgrade delivered faster |

The one addition this protocol makes to the time instrument is the
**transcript-correction category**. On a source where the transcript is
PARTLY_TRUE, an editor in Arm B is silently fixing ASR errors, and that time is
currently invisible in the kept/trimmed/discarded/added scheme — it is folded
into "trimming". Counting it separately is what makes the non-English arm
measurable rather than merely runnable.

**Status: PENDING EXTERNAL VALIDATION.** No time has been measured. The 18×–73×
figure in `docs/TIME_SAVINGS.md` §5 remains a model and is labelled one.

---

## 10. Falsifiers, declared in advance

A protocol without a pre-declared falsifier is not an experiment. These are
fixed now, while nothing has been measured, so that a later failure cannot be
redefined as success. All are conditions on the **machine arm**, Arm B.

**F1 — Uselessness.** SelekyT is not useful to a professional editor if
**either** of the two conditions below holds. They are stated separately because
they are measured on different units — one per source, one per editor — and
conflating them would let a strong result on one mask a failure on the other.

- **F1a, judged by raters (per source).** The median E11 (overall usefulness)
  across the Arm B envelopes for a source is **one anchor or more below** the
  median across the Arm A envelopes for the same source. Each source has two
  envelopes per arm, so this is a median of 2 against a median of 2, and it is
  computed per source — a **majority means 2 of 3 sources**. That is thin by
  construction and must be reported as such: if only one source separates, that
  is a signal to run more sources, not a pass.
- **F1b, judged by editors (per editor session).** The Arm B corrections burden
  (E10) exceeds that editor's own Arm A session time. This is a paired
  per-editor comparison, n = 6, and a **majority means 4 of 6**. This is the
  condition that catches "faster but useless": Arm B can win on the clock while
  the editor's correction time alone exceeds what a manual cut would have taken
  end to end.

**F2 — Broken blind.** If raters' accuracy at guessing which arm produced which
envelope is materially above chance, **the study has no results**, regardless of
what the numbers say. Measured at debrief with a per-envelope arm-guess;
reported alongside, never buried. With the §7 design this check has limited
sensitivity by design (§11) — it detects a badly broken blind, which is the
right sensitivity for a blind check.

**F3 — Raw material, not assistant.** If the Arm B discard rate exceeds **50%**,
the tool supplied raw material, not an assistant, and any time saving is in the
*rendering*, not the *editing* — a different and much smaller claim. This is the
`BLINDED_PROTOCOL.md` falsifier, retained unchanged.

**F4 — Speed without quality.** From `docs/TIME_SAVINGS.md` §7: the time-savings
claim is **FALSE** if Arm B is not at least **30% faster** to ready-to-deliver
**at equal-or-better blind quality**. F4 is not satisfied by F1 failing; they
are independent and both must hold for a positive claim.

**F5 — Reality failure.** If any source returns a T-gate label of NOISE or
WRONG_LANGUAGE, that source's ratings and times are **not study results** and
are reported as pipeline defects against the transcription layer. A study that
quietly dropped such a source and reported the rest would be reporting a
survivor set.

**F6 — Circular evidence.** If the mechanical audit and the human rubric
disagree in the machine's favour on a majority of clips (audit PASS, human
score ≤2 on the same axis), the audit is measuring self-consistency rather than
editorial value, and that is itself a reported finding about `tools/editorial_audit.py`
— the specific possibility raised in `docs/EVALUATION.md` §7 as possibility 3.

---

## 11. Minimum sample, and the statistical shape

**Design.** 6 editors, each running both arms of one assigned source = **12
sessions, 6 per arm, 6 paired differences** (n = 6, clearing the n = 5 floor
below with one spare) and **12 envelopes, 4 per rater**, 3 raters.

**Analysis.**

- **Time (primary):** per-editor paired difference in session minutes, Arm B
  minus Arm A, on that editor's own source. Wilcoxon signed-rank, one-sided,
  α = 0.05, **n = 6 pairs**. The signed-rank test is chosen over a paired t-test
  because editing-time distributions are skewed and n is small; a t-test on 6
  pairs of skewed data buys no accuracy and loses robustness. Median and
  interquartile range are reported alongside, never the mean alone.
- **Editorial axes:** per-axis Arm A vs Arm B distributions across the 12
  envelopes (6 per arm). Mann-Whitney U, one-sided, α = 0.05, with the per-axis
  profile reported in full. E11 is the primary editorial endpoint; E1–E10 are
  secondary and are reported as a profile, not as a battery of independent
  confirmatory tests.
- **Blind check:** exact binomial against chance (0.5) on the arm-guesses, one
  guess per envelope, 12 guesses.
- **Correction volume:** reported as counts and proportions with exact
  (Clopper-Pearson) intervals. No inference is claimed at these n.

**What the smallest honest sample is.** A formal power calculation has **not**
been run and no power figure is asserted. What can be stated exactly is the
combinatorial floor of the chosen test, which is checkable arithmetic:

- Wilcoxon signed-rank with **n = 4** pairs all of one sign gives a minimum
  one-sided exact p of 1/2⁴ = **0.0625**, which cannot clear α = 0.05 no matter
  what the effect is. **n = 4 is therefore not a study.**
- **n = 5** pairs all of one sign gives 1/2⁵ = **0.03125**, which does clear it.
  Five pairs is the floor at which a perfectly consistent effect can be
  detected. It detects only a large, perfectly-signed effect, and it should be
  reported at exactly that strength.
- The blind check with 12 arm-guesses: 12 of 12 correct gives p = 1/4096 =
  **0.00024**; 11 or more gives 13/4096 = **0.0032**; 10 or more gives
  79/4096 = **0.0193**; **9 or more gives 299/4096 = 0.0730 and is not
  evidence of a broken blind**. So a rater who calls 9 of 12 right has not
  seen through the blind — near-chance performance on a task a professional
  might reasonably do better than chance at by intuition alone is not a
  failure. The check convicts a badly broken blind, which is the right
  sensitivity for a test whose failure invalidates everything else.

**Smallest honest configuration: 5 editors, 2 sources, 2 raters, 1 coordinator,
1 analyst, 1 checker per language (2 languages if both sources are non-English,
1 if not).** Each editor still runs **both arms of one source**, so the pairing
survives intact and the design yields **five paired differences** — exactly at
the floor, with no margin. That is 10 sessions and 10 envelopes, 5 per rater,
with the between-rater constraint still satisfiable (R1 takes all of source 1's
Arm A plus source 2's Arm B; R2 takes the complement). This configuration buys a
**large-effect-only** claim and should be described at exactly that strength.
Anything smaller is a demonstration, not an experiment, and must be described as
one.

**Realism.** A design that needs 40 raters is a design that never runs. Six
editors, three raters and four language checkers is the configuration this
protocol is written for, and it is the configuration the cost table in
`docs/TIME_SAVINGS.md` §7 is priced for. The study should be run at full design
or not at all; the reduced configuration above exists to state honestly what a
shortfall costs, not to be aimed at.

---

## 12. Ethics, rights and data handling

Proportionate to what is being done: professional adults doing their own job on
rights-permitted public media, for an honorarium. Not a clinical trial, and not
treated as one.

**Sources.** All study sources are drawn from `corpus/manifest.json`, which
records the rights basis for each entry (US-government public domain, or CC
BY/BY-SA as declared on the hosting page). Two caveats carry forward from that
manifest and are not resolved here: Commons licences are **per-upload and must
be re-read at use time**, and `nasa360-ep5` carries no explicit licence at all
(PD status is from the creator). Derivative clips made for evaluation are
internal study artifacts and are not published.

**Participants.** Each participant receives, **before** any session and in
writing: what the study is, what they will be asked to do, how long it will
take, what the honorarium is, that **they may withdraw at any time without
giving a reason and without forfeiting the honorarium**, and that their
individual results will not be identifiable in any report. Consent is recorded
in writing. Withdrawal after the fact removes that participant's data from the
analysis; if the withdrawal happens after unblinding, the affected envelopes are
reported as missing rather than reassigned.

**Anonymisation.** No participant name, email, NLE preference, IP address or
voice appears in any dataset. The only identifier linking a session to a person
is a participant code held by the coordinator; `key.csv` maps envelope codes to
(participant code, arm, source) and **never to a name**. The analyst receives
coded data only.

**What is stored, and for how long.**

| Data | Stored | Retention |
|---|---|---|
| Renders, captions, timelines, transcripts (both arms) | yes, in the study directory | 24 months, then deleted |
| `key.csv` (the blind mapping) | yes, sealed, coordinator only | destroyed once the report is written and ratified |
| Consent forms and participant contact details | yes, held by the coordinator separately from all study data | 24 months, then deleted |
| Ratings and session times | yes, coded only | indefinite, as the study's evidence base |
| Free-text justifications | yes, coded | retained; reviewed before publication for identifying detail |
| Source media | not copied beyond what the pipeline already stages | deleted at study close |

**Coercion and conflict.** Participants are not employees of, or investors in,
SelekyT, and are not told the product name until debrief. Any participant who
has a commercial relationship with the founder is excluded from the rater pool.

**No contact has occurred.** Founder approval for external contact has not been
given, and nothing in this section implies it has.

---

## 13. Prerequisites — what is missing to run this

Stated so the study cannot be started before it is ready. Each item has an
owner and a state, and none is met.

1. **Founder approval to contact participants.** Not requested, not implied.
   This is the hard gate on the whole document; everything below is preparation,
   not progress towards it. **PENDING EXTERNAL VALIDATION.**
2. **A human baseline editor.** Six professional editors for the full design,
   none identified. The baseline is the comparison; without it the study
   measures machine output against nothing. **PENDING EXTERNAL VALIDATION.**
3. **Raters.** Three, blind, not participating as editors. **PENDING EXTERNAL
   VALIDATION.**
4. **Language checkers for the T-gate.** At minimum one each for Telugu, Hindi
   and Hungarian if those sources are used, plus English. This is the
   prerequisite the previous design omitted entirely and the one the corpus
   result makes mandatory. **PENDING EXTERNAL VALIDATION.**
5. **A coordinator and a separate analyst.** Two distinct people, neither an
   editor nor a rater. **PENDING EXTERNAL VALIDATION.**
6. **Source eligibility.** Sources must be chosen only from those the corpus
   has **completed successfully**. As of 2026-09-19 that is seven sources, of
   which three (`telugu-tutorial`, `hadhazy-protest`, and — for a different
   reason — `hindi-baiga-interview`, which produced no stories) are the ones the
   T-gate exists to catch. A study built on sources the pipeline has never
   processed would be measuring pipeline behaviour on unseen input with no
   baseline. Source selection is therefore coupled to the T-gate outcome, and
   if the non-English sources return NOISE the study is **English-only and says
   so** rather than substituting a third English tape.
7. **Funding.** ≈$867 at the rates in `docs/TIME_SAVINGS.md` §7, plus
   recruitment. Not spent, not committed.
8. **A round-trip re-import of an exported timeline.** **Not the `pycmx`
   prerequisite it was drafted as** — that item was based on a claim in
   `docs/EVALUATION.md` §3-D3 and §4-P1 which is stale on this host and needs
   correcting there. Verified on this host today: `pycmx` **is** installed at
   `.venv/lib/python3.12/site-packages/pycmx`, **is** declared in `pyproject.toml`
   in the `nle`, `dev` and `all` extras, and **is** installed by
   `.github/workflows/ci.yml` (which installs `.[dev,llm,nle]`). Separately,
   there are **no skipped EDL-parse checks**: `tools/nle_check.py:21-22` states
   that when `pycmx` is absent the independent-parse check **FAILS rather than
   skips**, and a `grep -rn pycmx tests/` returns no test importing it at all —
   so the check runs only via the audit tool on a real project directory, never
   as a unit test. The actual skips in the suite are environment and opt-in
   fixtures (proxy media, ffmpeg/ffprobe presence, node, an unbuilt Rust binary),
   none of which exercise EDL validity. The genuine remaining gap is the one
   `docs/EVALUATION.md` D3 names alongside it: **no NLE has ever imported an
   exported SelekyT timeline**, and no `pycmx` parse substitutes for that. It is
   mechanical, cheap, independent of item 1, and could be done today — but it is
   a real round-trip in a real NLE, not a package installation.

## 14. What may be said while this is unrun

**May be said:** that the rubric, the blinding procedure, the T-gate, the
falsifiers and the budget exist and are recorded here. That the mechanical and
editorial layers are specified as uncombinable. That a design exists which
would settle editorial quality at a stated cost.

**May not be said:** that any editor has used SelekyT. That any human has judged
any cut. That time savings were measured. That a blind was maintained. That a
transcript was checked against reality. That any of the eleven axes has a score
on any source. Nothing in this document is a result, because nothing has been
run.
