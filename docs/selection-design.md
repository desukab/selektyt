# Editorial Selection 2.0 — the selection policy

Subtitle: why SeleKyT shipped four of six clips out of one 134-second window, and the smallest coherent correction that stops it.

## 0. How to read this document

Every claim is tagged:

- **[CODE]** — stated by the code itself (docstring, comment, signature or behaviour I read). File:line given.
- **[BENCH]** — stated by the benchmark evidence supplied with the brief. Not reproduced here, and not treated as ground truth: an external clipper's choices are a second editorial opinion, not the truth.
- **[INFER]** — my conclusion from reading the code. Where I infer, I say so, and I do not infer where the code already speaks.

No benchmark timestamp is hardcoded anywhere in the change. The three regions the brief named were used to understand the *shape* of the loss, and the tests pin the shape, never the times.

---

## 1. The primary engineering question, answered

The brief asked: *why does SeleKyT's existing editorial intelligence fail to produce globally strong selections?* The answer is not that the scoring is weak. It is that **a per-clip score cannot answer a question about a set**, and the architecture asked it to.

Six findings, each one read out of the code:

### 1.1 The final selection was a sort and a slice

`selekyt/pipeline.py` read, before this change:

```python
selected = passed[:limit]
```

**[CODE]** Every candidate was scored independently by `engine.editorial`, sorted, and the top `limit` taken. Nothing in that operation knows which other clips were taken. Two clips 20 seconds apart in the same conversation each score well on their own merits and both win, because the score is a property of one clip and the selection is a property of the set.

### 1.2 The diversity guards were dead code at the real budget

`engine/editorial.py:rank` carries two caps — a topic-overlap cap and a per-story-type cap — both gated on `len(chosen) < limit - 1` **[CODE]**. The pipeline calls `rank(thoughts, limit=max(limit * 3, limit))` **[CODE]** so a bench of near-misses survives into the report, which is correct. But at the real budget of 18 the bar was 17 clips, and the loop breaks at `limit`. Both caps were unreachable. **[INFER]** They were written as if `rank` were called with the selection limit — and a cap keyed on the inflated limit fires at the same moment the loop breaks, so it is dead by construction, not merely mis-tuned.

The benchmark's own numbers confirm this from the outside: `truncated_by_limit = 6` against `selected_count = 6` **[BENCH]** means `len(passed) = 12` at `limit = 6` — twelve candidates passed the gate for six slots, and all six the policy could have substituted were taken by budget. The guards that were meant to stop a reel of near-identical clips never fired.

### 1.3 The only temporal guard was a 4-second overlap rule

`rank` skips a candidate that overlaps a chosen one within 4 seconds **[CODE]**. Four non-overlapping clips inside a 134-second window all pass it. **[INFER]** The guard prevents literal duplication and nothing looser — it has no notion of a scene.

### 1.4 The corpus analysis ran *after* selection

`build_corpus` was called below the selection block, to write `editorial_corpus.json` for the memory and story-view layers **[CODE]**. The selector never saw it. Topics, story threads, dramatic beats, redundancy groups and setup-payoff links were all computed, all correct, and all invisible to the decision that needed them. **[INFER]** This is the single most consequential finding: the evidence was already present in the process and was not reaching the decision.

### 1.5 The opening measurement reached the editor as prose and stopped

`engine.editorial._score` computes an opening strength and `engine.editorial._explain` renders it into `reason` as *"opens slowly, with no hook in the first sentence"* **[CODE]**. The benchmark ranked such clips highly **[BENCH]** — the signal was measured, reported, and acted on by nothing. The LLM pass (`attach_metadata`) runs after selection and never reorders or rejects **[CODE]**.

### 1.6 What is *not* the cause

- The scoring weights are not the cause. `Candidate.total_score` is a fixed 5-term blend **[CODE]**; weak selections survived despite it, not because of it, and reweighting it would not fix a problem that is structural.
- The context gate is not the cause. `validate` removes clips that cannot stand alone, and on the benchmark twelve candidates survived it **[BENCH]**.
- Rendering is not the cause. The brief said so, and the code agrees: the render path reads `selected` and never writes it.

---

## 2. The correction

The brief asked for the *smallest coherent architectural correction*, and warned against a second scoring system. The corpus layer is a finished, tested analysis that nothing consumed; the correction is to let the selector see it, and to score survivors against each other rather than against an empty set.

One new module — `engine/selection.py` — and three wiring changes.

### 2.1 The corpus is built before selection

`build_corpus` moved above `make_thoughts` / `rank`, wrapped so a failure sets `corpus_report = None` and the run continues on the diversity-only terms. Measured cost: 67 ms at 19.2 minutes of source, 151 ms at 53.0 **[CODE, measured]**. Cheaper than one candidate's word-timestamp pass, and it was already being built — the change is when, not whether.

### 2.2 `rank`'s guards bind at the real budget

```python
select_limit = max(1, limit if select_limit is None else select_limit)
...
if topic_overlap > 12 and len(chosen) < select_limit: continue
if kinds[c.story_type] >= 2 and len(chosen) < select_limit: continue
```

**[CODE]** `rank` gained a `select_limit` parameter, which the pipeline passes explicitly because the budget is not derivable from the list's own `limit`:

```python
ranked = rank(thoughts, limit=max(limit * 3, limit), select_limit=limit)
```

The caps fire while the chosen set is short of that budget, so they keep the budget slots free of topical and story-type duplication; past it they stop filtering, which leaves the policy layer a bench of real alternatives to substitute against the set it is building. The deeper, set-level terms live in the policy layer, where they can see what has already been chosen.

### 2.3 The opening measurement is carried on the record

`Candidate` gained `opening_score: float = 1.0` **[CODE]**. `_score` returns it as its last value and `score_candidate` carries it onto the record, so the policy reads the measurement instead of re-parsing the prose it was rendered into. The default is deliberately neutral: *unmeasured* is not *weak*, and a candidate built by an older caller must not be penalised for predating the field.

### 2.4 `engine/selection.py`

`select(ranked, *, limit, corpus)` is a greedy loop that re-scores the whole remaining pool after each pick, because a clip's standing changes once its neighbour is chosen — the property `rank` cannot have. Each term produces a `PolicyNote` naming the term, the size of the move and the clip it was measured against, and the notes land in `editorial_report.json` under a `policy` key.

The terms, in the order an editor would challenge them:

| Term | Constant | What it reads | Sign |
|---|---|---|---|
| weak opening | `WEAK_OPENING = 0.30` / `WEAK_OPENING_PENALTY = 0.12` | `Candidate.opening_score`, already measured | − |
| local scene | `LOCAL_SCENE_S = 60.0` / `LOCAL_SCENE_PENALTY = 0.22` | source time against the chosen set, doubling per neighbour | − |
| content repeat | `CONTENT_OVERLAP = 0.55` / `CONTENT_PENALTY = 0.22` | content-token overlap with a chosen clip, via the corpus layer's own `content_tokens` | − |
| complete arc | `ARC_COVERAGE = 0.50` / `ARC_BONUS = 0.10` | the corpus's `SetupPayoffLink` records, scaled by thread confidence | + |

Three properties of that table are deliberate:

**The penalty terms only ever reduce, the arc term only ever increases.** The policy cannot manufacture a ranking the underlying scores do not support. A clip `editorial` judged weak is not rescued here.

**Every input is a record the corpus already computed.** No signal is invented. The arc term reads `SetupPayoffLink` — explicit question→answer, problem→solution, causal, callback and contrast-turn relations with both halves timecoded — rather than re-deriving structure from beat kinds, because the beat lexicon never names a beat `setup` on the fixtures that do have arcs (`tension → resolution`). The definition of a content token is the corpus layer's own, not a second copy: an earlier version duplicated it and had silently dropped the stopword filter.

**The penalties are penalties, not caps.** `--limit` still binds, and the loop fills the budget rather than dropping a clip the policy pushed below zero — an editor who asked for six clips gets six, and the report says which of them only made it on a depleted bench. A clip that is genuinely stronger than anything else still ships from a scene already used.

### 2.5 What it deliberately does not do

It does not re-score content, pick topics, or decide what the edit is *about*. It does not remove human-editor control: every candidate it does not ship is on the bench with the reason and the clip that beat it, in the report the editor already reads. It does not depend on the corpus — a transcript too sparse to analyse degrades rather than raising. It touches nothing in the render, caption, EDL, FCPXML or QA paths, which read `selected` and never write it.

---

## 3. Verification

`tests/test_selection_policy.py` — 37 tests — pins the properties, not the timestamps. Each one breaks on the old code by construction:

- a second clip in one 60-second window loses to an equal-score clip elsewhere, and the penalty doubles per additional clip from the scene;
- clips separated by more than the window do not penalise each other — the term is temporal, not a blanket diversity cap;
- a weak opener loses to a strong one at equal score, still ships on an empty bench, and an unmeasured opening is never penalised;
- a clip closing a corpus arc outranks an equal-score punchline, a clip touching a thread by one passage is not credited with it, and the bonus is bounded by the corpus's own confidence;
- the same subject in different words is one selection — the case `rank`'s text-similarity cap was meant for and could not catch, because it was disabled at the budget;
- the topic-overlap cap keyed on the selection budget admits more than one keyed on the inflated limit, which is the difference between a guard that binds and one that fires only as the loop breaks;
- the content-token definition is the shared one, pinned against a second copy drifting;
- a `None` corpus degrades to the diversity-only terms and an empty pool selects nothing;
- the pipeline wiring: `_apply_selection_policy` returns both selection and report, the terms are keyed on the clip's identity rather than its slot, and the stage summary reports the selection the policy made rather than `passed[:limit]`.

Full suite: **2568 passed, 16 skipped** excluding the render tests, 11 minutes. The render tests are excluded because they encode real media; nothing in this change touches the render path, which reads `selected` and never writes it.

### 3.1 What the change actually does on a benchmark-shaped pool

`inspect_selection.py` at the repo root builds a pool of the shape the benchmark's `passed` list had — one candidate per complete story arc, plus five independently strong isolated moments inside 120 seconds, the isolated ones scored *higher* than the arcs:

```
=== OLD: sort by score, take 6 ===
    900.0-918.0    0.704
    922.0-940.0    0.704
    944.0-962.0    0.704
    966.0-984.0    0.704
    988.0-1006.0   0.704
      0.0-173.1    0.672
  -> 5 of 6 from the one 120s window

=== NEW: the policy's 6 ===
    900.0-918.0    base 0.704 -> 0.779  arc +0.076
      0.0-173.1    base 0.672 -> 0.750  arc +0.078
    988.0-1006.0   base 0.704 -> 0.704  no term fired
    468.0-572.0    base 0.672 -> 0.530  content -0.220; arc +0.078
     84.0-125.0    base 0.672 -> 0.310  scene -0.220; content -0.220; arc +0.078
    922.0-940.0    base 0.704 -> 0.264  scene -0.440
  -> 3 of 6 from the one 120s window
```

Three things to read in that. The clustered region keeps one clip, not five — the fourth costs it 0.44, twice the second, which is the doubling term doing exactly the work the benchmark's repeated choice demanded. The three complete arcs all ship, promoted by the corpus's own confidence in each thread. And the first clip out of the cluster earns an arc bonus of its own: its span happens to close a story the corpus measured there, so it is kept *and* credited, which is the honest outcome — the policy does not decide the cluster is bad, only that five clips from it is not an edit.

**[INFER]** On the benchmark this is the difference between `source_coverage ≈ 2.6%` and a selection that spans the source, and between `diversity ≈ 0.498` and one measured across acts. Those figures are not asserted anywhere in the code — they are the *measurements of a loss this change addresses*, and the claim is about the mechanism, not the number.

---

## 4. What this does not settle

The policy can only diversify within the pool `rank` and `validate` admit. On synthetic fixtures with heavily reused connective text, `rank`'s similarity and topic caps collapse hundreds of candidates to four or five — correctly, since they are near-duplicates. **[INFER]** The depth of the bench is a property of `rank`'s caps and the gate, not of this layer, and on a real 53-minute source twelve candidates survived for six slots **[BENCH]**, which is enough room for the substitution above but is not a deep bench. Widening the funnel without letting near-duplicates back in is the next question, and it is a `rank` question.

The corpus's beat lexicon names no beat `setup` on the question/answer fixtures — their arcs read `tension → resolution` and `continuation` **[CODE]**. The arc term therefore reads the `SetupPayoffLink` records, which are present and typed, rather than the beat kinds. If the beat lexicon and the link layer ever disagree on a real source, the link layer is the one with both halves timecoded, and the one this term should trust.
