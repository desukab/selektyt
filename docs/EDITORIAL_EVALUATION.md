# Editorial Evaluation — SelekyT run on NASA 360 Episode 5

Evaluated: `out/url-e2e/project/` (primary). Source: `NASA_360_program5_HD.mov`,
720x486 / 29.97fps / 1469.77s (24:29). Evaluator role: professional editor
(documentary / interview / podcast), adversarial by contract. Company rule
applied: **no self-certification** — every score below is tied to a quoted
line and a timestamp that exists in the artifacts.

Evidence base used: `transcript.json` (425 segments, per-word timestamps and
ASR confidence), `selections.json`, `editorial_report.json`, `edit_plan.json`,
`pacing_report.json`, `qa.json`, `content_pack/*.json`, `captions/*.{srt,ass,json}`,
`timeline/*.{edl,fcpxml}`, and `ffprobe`/`ffmpeg` measurements (null-muxer only,
no files written) over `source/`, `facebook/`, `shorts/`.

**What I could not verify:** I did not decode or view any video frames (ffmpeg
was used read-only). Every statement about *subject framing* — are the two
hosts inside the 9:16 crop, are faces bisected, is the lower-third readable —
is flagged as unverified. Statements about geometry, aspect ratios, loudness,
timing, and text are measured, not guessed.

---

## 1. The rubric

Seven dimensions, 1–5 each, designed so a second editor can apply them blind
from the transcript and the renders alone. Anchors are concrete: a 3 is
"shippable with a note", a 2 is "an assistant would be sent back", a 1 is
"wrong, not just weak".

### 1.1 Hook quality

Does the first 3 seconds earn the next 3 seconds, standing alone, for a cold
viewer who knows nothing about the source?

| Score | Anchor |
|---|---|
| 5 | Opening sentence is complete, self-contained, and creates an information gap or a strong image. Would work as the first line of a script. |
| 4 | Complete and clear but lower-energy; a viewer stays but is not pulled. |
| 3 | Complete sentence, but generic ("So today we're going to talk about…"). Needs a cold-open graphic or a B-roll lift to work. |
| 2 | Opens on a clause or a discourse marker that depends on speech the viewer never heard. The meaning arrives but the subject is missing. |
| 1 | Opens mid-sentence or mid-word, or on a name/pronoun with no antecedent. A cold viewer cannot parse frame one. |

### 1.2 Payoff completeness

Does the clip land its own point inside its own runtime?

| Score | Anchor |
|---|---|
| 5 | Setup and payoff both inside the clip; the final line resolves the tension the opening created. |
| 4 | Payoff present but slightly soft (a number without a comparison, an image without a "so what"). |
| 3 | The clip ends on a complete thought that is a restatement rather than a landing. |
| 2 | The clip ends on a question, a handoff, or a name-address whose answer begins in the source within ~2 seconds of the out-point. The viewer gets the setup and the cut. |
| 1 | No payoff at all; the clip stops while the speaker is still building. |

### 1.3 Context preservation

Can a cold viewer answer *who, what, why* from the clip alone? Nothing dangles.

| Score | Anchor |
|---|---|
| 5 | Every pronoun has an antecedent, every name is introduced, the topic is named. |
| 4 | One small dangling reference ("this image") that B-roll covers. |
| 3 | A name or a term is used without introduction, but the clip still tracks. |
| 2 | The referent of the opening clause, or of a key term, is entirely outside the clip; the viewer must guess. |
| 1 | The clip is actively misleading about what is being discussed. |

### 1.4 Cut precision and pacing

Do the boundaries land where a human would put them, and is the internal rhythm
tolerable?

| Score | Anchor |
|---|---|
| 5 | In and out at sentence edges with a breath of air at both ends; pauses trimmed at word boundaries; no false starts or repairs left in. |
| 4 | Boundaries at word edges; one internal imperfection (a repair, a redundancy) a human would clean. |
| 3 | Boundaries at word edges but no air around the final word, or a visible false start retained. |
| 2 | Boundary clips a phoneme, or the retained pause pattern creates a jump the viewer hears. |
| 1 | Boundary inside a word, or the clip is mostly dead air. |

### 1.5 Caption quality

Are the shipped captions readable and correct as *written English*, independent
of what the ASR heard?

| Score | Anchor |
|---|---|
| 5 | Two-line cues, 1.2–2.5s each, ≤ ~20 chars/sec, numbers and proper nouns rendered correctly, no orphan words, terminal punctuation respected. |
| 4 | Readable and correct but one-line cues, or slightly fast. |
| 3 | Correct text but cue durations that flash (sub-0.5s cues) or orphan single words as full cues. |
| 2 | Verbatim ASR artifacts shipped (mangled numbers, wrong words, lowercased acronyms) *and* flash cues. |
| 1 | Captions contradict the speech, or cue timing does not match the edit. |

### 1.6 Reframing

Is the vertical version actually vertical, and is the picture geometry correct?

| Score | Anchor |
|---|---|
| 5 | Exact target aspect ratio, subject tracked into the safe area, no distortion, full-frame use of the canvas. |
| 4 | Correct aspect, canvas under-used (letterbox retained inside a portrait frame). |
| 3 | Correct aspect but a static center crop with no subject awareness; heads may be bisected. |
| 2 | Wrong display aspect ratio — the file's own pixel/sample aspect math does not equal the claimed ratio. |
| 1 | Wrong aspect **and** visibly distorted geometry (squeeze or stretch), so the vertical is unusable as delivered. |

### 1.7 Deliverability

Can the package be handed to a platform or an NLE conform without rework?

| Score | Anchor |
|---|---|
| 5 | Aspect, frame rate, loudness (≈ −14 to −16 LUFS, peak ≤ −1 dBTP), captions, timeline round-trip, titles and metadata all correct. |
| 4 | One cosmetic defect (placeholder format name, a tape-name oddity) that a conform absorbs. |
| 3 | Loudness unnormalized, or a timeline asset declaration that makes the source look truncated in the NLE. |
| 2 | A deliverable that is not the aspect ratio it is labelled, or a timeline whose clip exceeds its declared media range. |
| 1 | Files that fail platform ingest or will not re-link in an NLE. |

---

## 2. Scores, cut by cut

### Cut 01 — 414.30–439.16 (24.86s) — "very much like the electronic camera you have at home."

| Dimension | Score | Basis |
|---|---|---|
| Hook quality | **1** | opens 5.54s into a sentence |
| Payoff completeness | **2** | ends on a handoff question |
| Context preservation | **2** | subject of opening clause is outside the clip |
| Cut precision / pacing | **4** | word-edge boundaries; one unedited self-repair |
| Caption quality | **2** | mangled numbers, 120ms cue |
| Reframing | **1** | not 9:16 (see §3) |
| Deliverability | **2** | "16:9" is 4:3; −31.0 LUFS; FCPXML asset truncated |

**Hook — 1.** The transcript is unambiguous. The sentence runs from 408.76 to
416.16:

> "Hubble, while a very expensive fancy instrument above the Earth orbit, is using a camera that's **very much like the electronic camera you have at home.**"

The cut starts at 414.30, which is the exact end of the word "that's"
(414.06–414.30). So the boundary is clean at the word level but sits **5.54
seconds into a sentence**, and the shipped opening is a dependent clause with
no subject. The clip's declared `hook` field is that clause verbatim. A cold
viewer hears a predicate and no subject — the exact definition of a score-1
hook. The engine's own `reason` string records `opening=0.75`, so the model
that could have caught this scored the opening as three-quarters good.

**Payoff — 2.** The final line is "So, Carrie, looking at space from space is
truly the way to go." ("go." spans 439.04–439.16). That is a host handing the
floor back, not a payoff. The payoff is the expert's answer, which begins
0.32s after the out-point:

> 439.48 "Oh, absolutely. When you're above the atmosphere, you have none of the blurring effects. Everything is crystal clear. The Hubble images of this comet, for example, we can see things as small as 10 meters across." (439.48–447.88)

The clip has a setup and a question and lands neither. The engine scored this
`payoff=1.00`.

**Context — 2.** "Carrie" is addressed at 436.08 (ASR confidence 0.36) and
never introduced in the clip; the referent of "this image" (431.60) and of
"this object" (425.92) is the comet photograph discussed only in the preceding
discarded segment (388.54: "This is a humble image of a comet"). Note also
that the cut opens on the tail of a sentence whose first words are the
lowest-confidence in the region — "Hubble," 0.19, "fancy" 0.44, "expensive"
0.51 — so the excised half is also the half ASR was least sure about.

**Cut precision — 4.** In at 414.30 = end of "that's"; out at 439.16 = end of
"go.". Silence share 14.7% (audit). No pause removal was needed or applied
(`edit_plan.json`: one interval, `removed_seconds: 0.0`). The one thing a
human would still cut is a **self-repair** left in the middle:

> 425.74–427.74 "how far **this object, how far away it is** from us"

and, adjacent, a genuine redundancy:

> 429.88–431.86 "each little box, each little pixel of this image"

**Captions — 2.** `captions/01.srt` cue 4–6 read `This is about the 1 ,024` /
`pixels along this direction,` / `another 1 ,000 along this` — the tokenizer's
comma has been turned into a space-prefixed orphan token ("1 ,024") in the SRT,
the ASS, and the JSON. Cues 17 and 20 are `looking at.` for 0.27s and `go.` for
**0.120s**. Six of 20 cues are under 1.0s; three are under 0.5s. Every cue is a
single line (max 33 chars) so the track flickers a new line every 1.24s on
average.

**Verdict: KEEP WITH EDITS.** The underlying material is a good
explainer-beat and the internal edit is clean. Required human edits:

1. Move the in-point to **408.76** to restore the sentence ("Hubble, while a
   very expensive fancy instrument above the Earth orbit, is using a camera
   that's very much like…"), **or** drop the first sentence and open cold on
   "So think of a megapixel camera." (416.76) — the latter is the better hook
   and costs 2.5s.
2. Extend the out-point to **~448.2** to keep "…we can see things as small as
   10 meters across."
3. Remove the self-repair at 425.74–427.74 (collapse to "how far away it is
   from us") and tighten "each little box, each little pixel" to one.
4. Re-split the caption track so "1,024" and "1,000" render as numbers, and
   merge the sub-second tail cues into the preceding cue.

---

### Cut 02 — 327.86–346.28 (18.42s → 17.08s after pause removal) — "Welcome to the world's largest clean room."

| Dimension | Score | Basis |
|---|---|---|
| Hook quality | **5** | best hook in the run |
| Payoff completeness | **2** | ends on a question whose answer is 0.46s away |
| Context preservation | **3** | "it" = Hubble, unnamed in the clip |
| Cut precision / pacing | **5** | textbook pause removal |
| Caption quality | **3** | timing honours the edit; flash cues |
| Reframing | **1** | not 9:16 |
| Deliverability | **2** | "16:9" is 4:3; −28.0 LUFS |

**Hook — 5.** "Welcome" begins at 327.86, exactly the clip start, after a
12.6s silence in the source (the previous speech ends at 315.27). Complete
sentence, second person, superlative, visual. This is the one opening in the
run that would work as the first frame of a reel.

**Payoff — 2.** The clip closes on the host's handoff:

> 344.52–346.28 "So Mike, Hubble has more than one instrument."

and the answer is 0.46s past the out-point:

> 346.74 "Right, it has actually five instruments, two types of cameras. Imaging cameras which take all those beautiful pretty pictures…" (346.74–357.94)

The clip is a teaser for a segment it does not deliver. The engine scored this
`payoff=0.95` and used the handoff line as the `payoff` field.

**Context — 3.** The middle sentence dangles once:

> 331.54 "The reason we have this exact mechanical replication of **it**…"

"it" is Hubble, named only in the next sentence — which is inside the clip
(338.52 "functions just like Hubble"), so the viewer recovers. "Mike" is
addressed but not introduced. This is the best-contextualised cut of the three.

**Cut precision — 5.** This is the one place the automated edit is genuinely
editor-grade. `edit_plan.json` ships two intervals, [327.86, 343.12] and
[344.46, 346.28], removing 1.34s. Reading the word timings: "it." ends at
342.94, the retained tail runs to 343.12 (+0.18s), and the next interval starts
at 344.46, 0.06s before "So" begins at 344.52. The removed span is exactly the
source pause, and both sides keep air. Silence share 10.3%. No notes.

**Captions — 3.** The caption track correctly follows the *edited* timeline:
cue 11 ends 15.20s, cue 12 starts 15.32s — the 1.34s pause is gone from the
captions too, not just the audio. But `captions/02.srt` cue 2 is `clean room.`
for 0.49s and cue 13 `one instrument.` for 0.42s; five of 13 cues are under
1.0s. Again single-line cues only (max 33 chars).

**Verdict: KEEP WITH EDITS.** This is the strongest clip in the run and the
one needing the least surgery. Required human edits:

1. Extend the out-point to **~358** to keep the five-instruments answer, or
   trim the trailing question and end on "…when we get up here to work on it."
   (342.94) for a pure tour piece.
2. Merge the sub-second tail cues into the preceding cue.
3. Bump loudness to platform target (currently −28.0 LUFS).

---

### Cut 03 — 654.94–671.38 (16.44s) — "Well, we're studying the energy transfer…"

| Dimension | Score | Basis |
|---|---|---|
| Hook quality | **2** | opens on the filler "Well,"; its own question is outside |
| Payoff completeness | **3** | real payoff, but the tag is cut and there is no tail |
| Context preservation | **2** | "this region" never named; TIMED acronym lost |
| Cut precision / pacing | **3** | zero air after the final word |
| Caption quality | **2** | ASR artifact shipped; "timed" lowercased |
| Reframing | **1** | not 9:16 |
| Deliverability | **2** | FCPXML clip out-point exceeds its declared asset duration |

**Hook — 2.** The clip opens on "Well," (654.94–655.12), an answer-marker
responding to a question the viewer never heard:

> 653.88–654.54 "Explain that."

which itself follows the acronym set-up "We're left with ED, Energetics and
Dynamics" (651.18–653.70). The in-point is 0.40s after the question it
answers. The declared `hook` is a **160-character hard truncation of the
source sentence ending mid-word** — `…the energy being put into the part of
thi` (`editorial_report.json`, `selections.json`). That same broken string is
the EDL `TITLE`, both FCPXML event and project names, and the first entry of
`content_pack/03.json`'s `titles` list.

**Payoff — 3.** Unlike the other two, this clip does contain a real payoff:
the "can you hear me now?" gag (669.90–671.38), a pop-culture handle for space
weather's effect on cell phones. Two problems: the out-point 671.38 is the
exact start of the next word "Hello," (671.38–671.62), so there is **zero air
after the final word**; and the joke's tag is dropped:

> 671.38–672.32 "Hello, hello, right?"

**Context — 2.** "this region of the atmosphere" is used twice (654.94–658.56)
and the region — the thermosphere — is named only in the discarded setup
(594.43: "he's studying the final frontier of the earth, which is the
thermosphere"). "timed" (665.32, 663.80 context) is the **TIMED** mission
(Thermosphere, Ionosphere, Mesosphere, Energetics and Dynamics); the acronym
expansion lives at 588.55–593.01, and the transcript itself spells it at
649.12 ("the TIM of timed"). Shipped lowercase, the clip's single most
searchable proper noun is misspelled.

**Cut precision — 3.** In at 654.94 = start of "Well,"; out at 671.38 = end of
"now?" (671.26–671.38). Densest clip of the three (3.6% silence, 3.74 w/s).
The missing tail is the whole problem — a human leaves 0.3–0.5s after a final
word, especially a punchline.

**Captions — 2.** An ASR artifact is shipped verbatim in the captions *and*
the selection text:

> 668.44–669.60 "will hopefully keep us one day from **I'm never having to say**, can you hear me now?"

("I'm never" is a mis-recognition; the speaker restarts the phrase and the
caption keeps both halves with no repair.) Cue 11 `now?` runs 0.40s; cue 12
`Can you hear me now?` runs 0.50s.

**Deliverability — 2 (specific to this clip).** In `timeline/03.fcpxml` the
asset is declared with `duration="20141121/30000s"` = frame 20121, while the
clip's own out-point is start frame 19629 + duration 493 = **frame 20122**.
The clip extends one frame past its declared media range. In Final Cut Pro this
is an off-by-one the conform will flag. (`timeline/01.fcpxml` and `02.fcpxml`
declare the asset as ending *exactly* at the clip out-point — frame 13162 and
frame 10378 respectively — which is a different defect with the same root:
the asset duration is written from the clip, not from the source. All three
FCPXMLs therefore describe a 24:29 source as being 7:19 / 5:46 / 11:11 long.)

**Verdict: REJECT as cut.** The material is worth keeping but this span is not
salvageable by trimming alone, because the in-point cannot be fixed locally:
the sentence it opens on is a jargon-heavy answer, and its question is 3.76s
earlier. The correct action is a **different selection**, not an edit to this
one. If the ranker had returned 651.18 → ~672.4 (question + answer + gag +
tag, ~21s) it would have been the best clip of the three.

---

## 3. Failure hunting

### 3.1 The ranking almost certainly missed better stories — but the run does not let me prove it

`report.json` reports `candidate_count: 4000`, `selected_count: 3`,
`rejected_by_context_guard: 0`, `truncated_by_limit: 6`, and
`rejection_reasons: {}`. `selekyt/pipeline.py:311` computes
`truncated_by_limit = max(0, len(passed) - limit)`, so **six candidates passed
every editorial gate and were discarded by the output budget**, and no artifact
anywhere records them (`selections.json`, `editorial_report.json` and
`pacing_report.json` contain only the three winners; `pacing_report.json`
reports `total_candidates: 3`). The single most important editorial question —
"did the ranker prefer a worse story over a better one?" is therefore
**unauditable by construction**. Everything in this section is inference from
the transcript plus the ranker's documented heuristics, and I label it as such.

Reading the transcript as an editor, five passages are stronger than at least
one shipped cut on hook and payoff:

| Time | Passage | Why it beats a shipped cut |
|---|---|---|
| **764.90–779.30** | "So we have lots of really important satellites like Jason, Jason II, that actually measure the height of the ocean from 800 miles in space. And they can measure it with the accuracy of about an inch. That's about the size of a quarter from 800 miles in space." | Complete, self-contained, number-led, with a visual analogy as the payoff. Beats Cut 01 on both hook and payoff. 14.4s — inside the engine's own 8–55s window. |
| **880.12–899.78** | "There is a very large signal out there, Johnny… Sea level has risen almost eight inches in the last hundred years. And so really, it's the unequivocal proof of global warming." | Highest-stakes payoff in the episode. Cut 03 ships a cell-phone joke instead. |
| **233.72–246.58** | "In fact, so many people want to use it that only one in seven actually get to use it at any given time. And it's peer reviewed…" | A genuine "revelation"-typed fact — the type Cut 01 and 02 are mislabelled as — inside the same Hubble segment that produced two of the three winners. |
| **996.76–1008.36** | "It's kind of like doing investigative forensic work, but without being able to set foot on the scene of the crime." | A one-line image hook with a complete thought; 11.6s. |
| **1287.44–1312.56** | "Dude, are you sure there's water on Mars? … a lot of Mars today looks just like this… we can tell that a few billion years ago… there's water all over the place. We see evidence for rivers that are now dry…" | Question-led, conflict-shaped, visually grounded in the Mars test yard. This is the episode's best cold-open and it is not in the run. |

Two of the ranker's diversity rules in `engine/editorial.py:rank` make these
misses *expected* rather than accidental, and both are worth challenging:

- `topic_overlap > 12` skips a candidate sharing more than twelve content
  tokens with anything already chosen. In a single-topic documentary nearly
  every candidate shares "space", "NASA", "ocean", "water", "Earth". The rule
  is designed to stop near-duplicates, but in this source it structurally
  starves the later segments.
- `kinds[c.story_type] >= 2` caps each labelled type at two. Cuts 01 and 02
  both received `story_type: "conflict"`, so the cap was reached on a
  **mislabel**, potentially blocking a genuinely conflict-shaped candidate
  (the Mars-water exchange above) from the shortlist.

### 3.2 The pick set is topically lopsided, and the run measures this without acting on it

The episode has five segments (Hubble, atmosphere/TIMED, oceans, Mars CRISM,
Mars rovers). All three cuts fall between 05:27 and 11:11 of a 24:29 source:

- Cut 02: 327.86–346.28 (Hubble)
- Cut 01: 414.30–439.16 (Hubble)
- Cut 03: 654.94–671.38 (TIMED)

The two Mars segments (983–1383s, ~27% of the runtime) and the oceans segment
(747–940s) are entirely unrepresented, even though the source description in
`pipeline_state.json` names Mars/CRISM and sea level as the episode's
highlights. `qa.json` reports `source_coverage: coverage_pct 4.1` (59.72s of
1469.77s) and `diversity: 0.528` — both numbers are computed, printed, and
connected to no threshold and no action.

### 3.3 Missing payoffs — two of three cuts end on an answered question

Already evidenced in §2: Cut 01's final line "So, Carrie, looking at space from
space is truly the way to go." (435.56–439.16) is answered at 439.48; Cut 02's
final line "So Mike, Hubble has more than one instrument." (344.52–346.28) is
answered at 346.74. In both cases the answer begins within 0.5s of the
out-point. This is a single systematic failure — the ranker treats a host's
**handoff question** as a payoff — and `pacing_report.json` reports
`payoff_presence_rate: 1.0` across the same three clips. The metric is
self-certifying: `engine/edit_plan.py` defines

```python
payoff_presence = bool(re.search(r"[.!?।॥？！]$", c.text.strip())) and total_words >= 15
```

i.e. the clip ends with terminal punctuation and has ≥15 words. Both
handoff questions end with a period or question mark. The run reports 100%
payoff presence while two of three clips ship their setup without its landing.

### 3.4 Late starts

- **Cut 01 starts 5.54s too late**, inside a sentence that begins at 408.76
  (evidenced in §2). Worst case in the run.
- **Cut 03 starts 0.40s too late** to include the question it answers
  ("Explain that." ends 654.54) and 3.76s too late to include that question's
  setup ("We're left with ED, Energetics and Dynamics." 651.18).
- **Cut 02 starts correctly** — at a sentence start following 12.6s of source
  silence.

### 3.5 Early ends

- **Cut 01 ends 8.7s early**: the expert's answer with the clip's only
  concrete fact ("we can see things as small as 10 meters across") runs to
  447.88; the cut ends 439.16.
- **Cut 02 ends 0.46s early** — the answer "Right, it has actually five
  instruments…" begins at 346.74.
- **Cut 03 ends with no tail at all**: the out-point 671.38 is the exact start
  of "Hello," and the joke's tag ("Hello, hello, right?" 671.38–672.32) is
  dropped.

### 3.6 Decontextualisation

- **Cut 01** opens on a clause whose subject ("Hubble… is using a camera
  that's") is outside the clip, and refers to "this image"/"this object" whose
  referent (the comet photograph, 388.54) is outside the clip. The opening is
  *unparseable* cold, though not misleading once the subject is restored.
- **Cut 03** never names the region it is about (thermosphere) and lowercases
  the mission's name ("timed"). Not misleading, but the clip cannot be
  searched or cited.
- **None of the three is misleadingly decontextualised** in the sense of
  reversing meaning. The `context_guard` rejected nothing
  (`rejected_by_context_guard: 0`) and I find no quote in the shipped spans
  that changes meaning when removed from its surroundings. The failure mode
  here is *missing* context, not *false* context — which is the easier of the
  two to fix at the in/out points.
- One content-pack decontextualisation worth flagging: all three packs ship
  the hashtag `#india` (`content_pack/01..03.json`) for a NASA public-domain
  episode, alongside `#conflict` / `#revelation`, which are the engine's
  internal `story_type` labels leaked verbatim into the hashtag list. Neither
  set is derived from the source speech the packs claim to be built from.

---

## 4. Critique of `tools/editorial_audit.py`

The tool's own contract is to "score the concrete properties that determine
whether a clip is publishable". It is a genuine attempt at a falsifiable audit
— word-edge boundary checks, a positional word-to-sentence mapper (correctly
avoiding the substring trap it calls out in its own docstring), caption-vs-render
duration reconciliation. But on this run **it prints `ALL CHECKS PASS` (exit 0)
over artifacts that a professional editor would send back.** The scores in §2
total 14, 21 and 15 out of 35; the audit sees none of it.

Its checks fail in three distinct ways:

**(a) Checks that pass because they inspect the candidate rather than the
source.** `check_cuts_respect_sentences` maps words to sentences *using only
the candidate's own truncated text* (`_word_sentence_map(cand)`), then asks
whether a boundary splits a sentence. If a cut opens mid-sentence — as Cut 01
does — the sentence's first words are not in the candidate, the boundary
cannot be "inside" any sentence it knows about, and the check passes by
construction. The failure it is written to catch is exactly the failure this
run has, and it is structurally invisible. **Fix: evaluate boundaries against
the full transcript, testing the word immediately before the in-point and the
word immediately after the out-point.**

**(b) Checks whose thresholds are too loose to fire on real failure.**

- `check_silence_share` returns `share <= 0.55` — a clip that is 55% pause
  passes. Measured values here are 3.6%, 10.3%, 14.7%, so the check adds no
  information at all; it would only fire on a clip that is mostly nothing.
- `check_cues_fit_the_render` tests only overrun (`cue end > render + 0.05s`).
  A caption track ending ten seconds before the render passes.
- `check_caption_text_is_transcript_text` catches *extra* words, never *wrong*
  words. Cut 03's "from I'm never having to say" passes, because it is
  verbatim from a transcript that is itself wrong. The audit has no concept of
  a transcript defect.
- `check_last_word_is_not_shaved` is sound in principle (it correctly
  threshold-tests the interval rather than word duration) but it tests the
  float intervals, not the frame-rounded EDL/FCPXML out-points. The exports
  floor to the previous frame instead of rounding to the nearest frame, which
  costs Cut 03 one frame of its final word relative to its own selection
  (671.38s → frame 20122 = 671.27s; rounding would give 20123), and nothing
  in the audit would notice.

**(c) Entire dimensions are absent.** No check anywhere in the file touches:

- **Caption readability.** No minimum cue duration, so a **0.120s cue** ("go.")
  passes; no reading-speed check; no orphan-word check; no number-mangling
  check, so `1 ,024` passes. Nothing checks that cues use more than one line
  or that they do not flicker at ~1.2s intervals.
- **Reframing.** No aspect-ratio or SAR assertion on the renders, so a file
  shipped as 9:16 with `display_aspect_ratio=1088:2187` passes unnoticed (§5).
- **Loudness.** No LUFS measurement at all; −31.0 / −28.0 / −30.5 LUFS passes.
- **Timeline fidelity.** Nothing parses the EDL or FCPXML. The frame-floored
  out-points, the FCPXML asset durations written from the clip instead of the
  source (making a 24:29 source appear as 7:19 / 5:46 / 11:11), and Cut 03's
  clip extending one frame past its declared asset range are all unchecked.
- **Content pack.** Mid-word titles (`…part of thi`) and `story_type` labels
  shipped as hashtags are unchecked.
- **Hook and payoff.** No check that the opening sentence is complete, that it
  contains a subject, or that the clip does not end on an answered question.

**The one check I would add first:** a **dangling-handoff check**. For each
clip, take the out-point, look at the first words after it in the *full
transcript*, and fail when the clip's final sentence is a question or a
name-address and speech resumes within ~2 seconds. It would have failed Cuts
01 and 02 immediately, it needs only `transcript.json` and `selections.json`,
and it attacks the exact failure the `payoff_presence_rate: 1.0` metric is
currently hiding.

Second priority: persist the ranked-but-truncated candidates
(`report.json` already carries `truncated_by_limit: 6`) so that ranking
quality becomes auditable at all. Third: an aspect-ratio assertion on every
render (`display_aspect_ratio` must equal the platform's target), which would
have caught the finding in §5 without watching a single frame.

One operational note: the documented invocation
`python tools/editorial_audit.py out/url-e2e/project` raises
`ModuleNotFoundError: No module named 'engine'` from a plain checkout; it only
runs with the repo root on `PYTHONPATH`. An audit that composes with CI, which
is this tool's stated purpose, should not depend on the caller setting an
import path.

---

## 5. The reframing is not reframing — measured, not guessed

This is the one finding that needs no editorial judgement at all, and it is
the reason reframing scores 1 across the board.

```
source/NASA_360_program5_HD.mov   720x486  SAR 8:9   DAR 320:243   29.97fps
facebook/01..03.mp4               720x486  SAR 8:9   DAR 320:243
shorts/01..03.mp4                 272x486  SAR 8:9   DAR 1088:2187
```

- The **"16:9"** renders are byte-identical in geometry to the source: 720x486
  with SAR 8:9, i.e. a **4:3 SD frame** (DAR 320:243 = 1.317). 16:9 is 1.778.
  No scaling, no reframing, no aspect correction happened. The output also
  inherits the source's own letterbox — `cropdetect` puts active picture at
  y≈52–440 of 486, i.e. about 80% of frame height — so on a 16:9 platform the
  "16:9" file becomes a 4:3 box carrying a wide picture inside it: pillarboxed
  *and* letterboxed simultaneously.
- The **"9:16"** renders are 272x486 with the source's SAR 8:9 **inherited
  rather than recomputed**, giving `display_aspect_ratio=1088:2187` = **0.498
  — not 0.5625**. The displayed picture is 11.5% narrower than 9:16. Whatever
  horizontal transform was applied (crop or squeeze — I could not determine
  which without viewing frames, and I will not guess), the resulting file does
  not declare the aspect ratio it is sold as, so any player that honours SAR
  shows a 1:2 image.
- Both renders keep the source's letterbox bars, so the portrait file carries
  black above and below a picture that is itself ~0.70 aspect inside a 0.56
  canvas — the vertical canvas is not filled by picture at all.

Because I did not decode frames, I explicitly do **not** claim anything about
whether the two hosts survive the horizontal crop, whether faces are bisected,
or whether the burned-in captions are legible over the picture. Those are
real, separate questions and this evaluation does not answer them. What the
metadata alone establishes is that neither deliverable has the aspect ratio
its directory name and its product contract claim — which is why reframing is
scored 1 rather than 3.

Loudness, for completeness: integrated loudness on the facebook renders is
**−31.0 / −28.0 / −30.5 LUFS**, identical to the source at the corresponding
range (−31.0 LUFS), with true peaks at −12.9 / −12.2 / −13.0 dBFS. There is no
loudness normalisation stage in the render path; the output sits ~14–17 LU
below the −14 to −16 LUFS that short-form platforms normalise to, with ~12 dB
of unused headroom. Captions **are** burned into both renders (verified by
detecting bright text-shaped pixels in the bottom band, y≈414–439, of all six
files) and are also shipped as sidecar SRT/ASS/JSON — so mute-autoplay works,
which is a genuine plus.

---

## 6. Verdict

**Usable as a first pass by a professional editor — a weak one.** Specifically:
the parts an editor would happily delegate are done well, and the parts that
require judgement are not done at all.

What is genuinely worth the editor's time: the transcript is solid and
word-timed; **Cut 02's pause removal is textbook** (removes exactly the
1.34s gap between "it." at 342.94 and "So" at 344.52, keeping 0.18s of tail
and 0.06s of pre-roll — `edit_plan.json`); the caption track correctly tracks
the *edited* timeline rather than the source timeline, which is the detail
most automation gets wrong; boundaries never clip a phoneme in any of the
three clips; captions are burned into the renders; and nothing in the shipped
spans is fabricated or misleading.

What the editor will have to redo: essentially the editorial layer. Two of
three cuts open on incomplete sentences; two of three end on questions whose
answers are within half a second of the out-point; the ranker's three picks
all sit in the first 11 minutes of a 24:29 episode with five segments; and
the vertical deliverable does not have a 9:16 aspect ratio, so no amount of
trimming fixes it.

A fair way to say it: **SelekyT has built a good assistant editor and stopped
before it built an editor.** The mechanical craft (word-edge cutting, pause
removal, caption-timeline sync) is at a level I would accept from a human
assistant. The judgement layer — what makes a hook, what makes a payoff, and
whether the thing on the timeline is the thing the platform asked for — is
either absent or, in the case of `payoff_presence_rate: 1.0` and
`avg_hook_latency: 0.63`, actively misleading about its own quality. Both
metrics are defined in `engine/edit_plan.py`: payoff presence is "ends with
terminal punctuation and ≥15 words" (so a host's handoff question counts as a
payoff), and hook latency is the time until the first match of
`\b(?:why|how|what|the|first|problem|secret|actually)\b` — the word **"the"**
matches, and all three clips' reported hook latency is literally the time
until "the" appears (0.54 / 0.70 / 0.66, averaging exactly the reported 0.63).
A clip gets a perfect hook-latency score for opening with the word "the". These
metrics are not measuring what their names claim, and they are the numbers the
report leads with.

**The single weakest dimension is reframing.** It is the one dimension where
the output is not weak but *wrong*, and wrong in a way that is measurable from
the file's own metadata: `shorts/*.mp4` declare `display_aspect_ratio=
1088:2187` against a 9:16 contract, and `facebook/*.mp4` declare a 4:3 frame
against a 16:9 contract. Hook quality and payoff completeness are worse
*editorially* — Cut 01's mid-sentence opening is the individual failure I'd
be most embarrassed by — but those are judgement calls a reader can dispute,
and they are fixable by moving two in/out points. The aspect ratios are not
disputable and are not fixable by trimming.

If the ranking must be re-run with one change: **persist the six
ranked-but-truncated candidates** and add the dangling-handoff check from §4.
Everything else in this document follows from those two gaps — the first
makes the ranking unauditable, the second makes the payoff metrics
self-certifying.

---

## Appendix: score summary

| Dimension | Cut 01 | Cut 02 | Cut 03 |
|---|---|---|---|
| Hook quality | 1 | 5 | 2 |
| Payoff completeness | 2 | 2 | 3 |
| Context preservation | 2 | 3 | 2 |
| Cut precision / pacing | 4 | 5 | 3 |
| Caption quality | 2 | 3 | 2 |
| Reframing | 1 | 1 | 1 |
| Deliverability | 2 | 2 | 2 |
| **Total /35** | **14** | **21** | **15** |
| **Verdict** | KEEP WITH EDITS | KEEP WITH EDITS | REJECT as cut |
