# Memory subsystem — failure-mode inventory

An adversarial review of `engine/memory/intervals.py` and `engine/memory/evidence.py`,
organised by how the failure reaches an editor (an editor never sees a traceback; they
see a confident wrong answer). Every trigger below was **executed** against the current
code on 2026-09-29, not reasoned about; see "Verified by execution" at the end.

Ranking: **S1** = can put a wrong clip in front of an editor or make the verify stage
lie about backing. **S2** = a confident wrong answer whose blast radius is bounded or
needs an unusual caller path. **S3** = a design gap or documented behaviour worth
pinning, with no editor-visible consequence on its own.

`MERGE_EPS = 1e-6`; `engine.cut_algebra.DEGENERATE_SECONDS = 0.01`. All floats below are
source seconds.

---

## 1. `expand(pad, bounds)` invents a span outside the bounds it was clamped to — S1

**(a) Trigger.** `Interval(200.0, 210.0).expand(0.0, bounds=Interval(0.0, 100.0))`.
The span lies entirely past the bounds, so no amount of clamping can intersect it.

**(b) Editor sees.** Clipping memory to the source turns evidence that does not exist in
the source into a **100-second span starting at the bounds' own end**:
`Interval(start=100.0, end=200.0)`. Downstream it gets worse, because `covers()` trusts
it: `covers(Interval(100.0, 200.0))` → `True`, `contains_time(150.0)` → `True`. An editor
who clips the memory to the source and asks "is this clip backed?" gets "fully backed"
for a clip in a region no evidence ever covered. Half-outside spans corrupt the same way:
`Interval(105.0, 110.0).expand(0.0, [0,100])` → `Interval(100.0, 105.0)` — a span that
starts *at* the bounds end and runs past it. `IntervalSet.expand(..., bounds=...)`
inherits it. The half-inside case is fine (`Interval(95,105)` → `Interval(95,100)`), so
this is specifically the "span does not reach the bounds" path.

**(c) Silent.** No exception; the result is a valid, normalised `Interval` with the wrong
coordinates.

**(d) Test.**
`assert Interval(200.0, 210.0).expand(0.0, bounds=Interval(0.0, 100.0)).end <= 100.0`

## 2. `covers()` returns `True` for every zero-width span, including one in a gap — S1

**(a) Trigger.** `IntervalSet.of((0.0, 4.0), (6.0, 10.0)).covers(Interval(5.0, 5.0))`.

**(b) Editor sees.** The implementation is
`span.duration <= self.slice(...).duration + MERGE_EPS`; for an instant both sides are 0,
so `0 <= 0 + 1e-6` is always true. `covers(Interval(5.0, 5.0))` → `True` while `at(5.0)`
→ `None` and `contains_time(5.0)` → `False` — the module's own three answers about one
point contradict each other. It is unbounded in range as well:
`covers(Interval(1000.0, 1000.0))` → `True`, `covers(Interval(-100.0, -100.0))` →
`True`. The docstring sells `covers` as the verify stage's "is the whole proposed clip
backed by evidence"; for a zero-width clip (a cut point, a beat — half the interesting
moments, per this module's own rationale) it always says yes.

**(c) Silent.** Returns a boolean.

**(d) Test.**
`assert not IntervalSet.of((0.0, 4.0), (6.0, 10.0)).covers(Interval(5.0, 5.0))`

## 3. The merge rule disagrees with `cut_algebra`, so "in or out" is module-dependent — S1

**(a) Trigger.** Two raw detections `(0.0, 10.0)` and `(10.0 + 5e-7, 20.0)` — a gap of
half of `MERGE_EPS`. `_normalise` merges them (`iv.start <= last.end + MERGE_EPS`);
`cut_algebra.merge_intervals(..., gap=0.0)` does not (`start <= prev_end`).

**(b) Editor sees.** For `t = 10.00000025`: `IntervalSet.contains_time(t)` → `True`,
`cut_algebra`'s merged list → `False`. The same speech regions, split between the cut
list (which `cutlist_from_intervals` builds with gap-0 merging) and the memory layer,
disagree about whether a span is in or out. `cut_algebra` with `gap=MERGE_EPS` does agree,
so the divergence is exactly the band `(0, 1e-6]`. `_normalise`'s docstring claims it
"mirrors `engine.cut_algebra.merge_intervals` exactly in rule" — that claim is false
inside that band. The 1e-14 float-noise case that motivated `MERGE_EPS` is fine (both
merge it once `gap` is passed); the defect is the unparametrised tolerance.

**(c) Silent.** Both return plausible, valid structures.

**(d) Test.**
`assert IntervalSet.of((0.0, 10.0), (10.0 + MERGE_EPS/2, 20.0)).cardinality == len(merge_intervals([(0.0,10.0),(10.0+MERGE_EPS/2,20.0)], gap=0.0))`

## 4. A `NaN` or `+inf` confidence is clamped to `1.0` — S1

**(a) Trigger.** `Evidence(..., confidence=float("nan"))`. `__post_init__` guards
`0.0 <= confidence <= 1.0`; NaN fails both comparisons, and
`max(0.0, min(1.0, nan))` resolves to `1.0` because every NaN comparison is False.

**(b) Editor sees.** The single worst sensor reading a detector can produce — an
undefined one — becomes **maximum** confidence. It then passes every filter:
`above_confidence(0.0)` keeps it, so it survives into any confidence-ranked view and any
"high-confidence evidence" shortlist. `+inf` also becomes `1.0`. `intervals.py` clamps
non-finite coordinates with `math.isfinite` at the door; `Evidence.confidence` does not,
so the two halves of the package treat poison values oppositely. Note the -5.0 → 0.0 case
below is the same code path but benign in direction.

**(c) Silent.** The field is silently rewritten; there is no record that a value was
repaired, unlike `Interval`'s inversion repair which is at least documented.

**(d) Test.**
`assert math.isnan(Evidence(..., confidence=float("nan")).confidence)` — or, if NaN is to
be refused, that it raises rather than becoming the maximum.

## 5. `subtract` can move a surviving span outside the base — S2

**(a) Trigger.** `IntervalSet.of((0.0, 10.0)).subtract(IntervalSet.of((-5.0, -5e-7)))` —
the removed span *ends* half an epsilon below the base start. The `while` guard only
skips other-intervals ending below `a.start - MERGE_EPS`, and the left-piece guard
`cut.start > cur.start + MERGE_EPS` is false, so the loop falls through to
`cur = Interval(cut.end, cur.end)` and re-anchors the surviving span at the **removed
span's end**.

**(b) Editor sees.** The kept set is `Interval(-5e-7, 10.0)` — evidence that starts before
the source begins. `at(-1e-7)` returns that span, `contains_time(-1e-7)` → `True`. The
symmetric case grows the tail: removing `(10.0 + 5e-7, 20.0)` from `(0, 10)` yields
`(0.0, 10.0000005)`. The existing property test
`test_algebra_round_trip_union_after_subtract` ("the property the cut list and the
evidence set are kept consistent by") is violated by this input: `rest.union(b)` is
`Interval(-5.0, 10.0)`, duration **15.0** against the original **10.0**. And
`cut_algebra.subtract_intervals([(0,10)], [(-5,-5e-7)])` → `[(0.0, 10.0)]`, so the two
subtracts disagree, contradicting `subtract`'s own docstring ("the clip rule is the
same"). Honest caveat: the corruption is bounded by `MERGE_EPS`, and
`cutlist_from_intervals` clamps the negative start to `0.0`, so an editor's rendered cut
is not itself displaced — the damage is to the invariant, not the frame.

**(c) Silent.**

**(d) Test.**
`assert all(iv.start >= 0.0 and iv.end <= 10.0 for iv in IntervalSet.of((0.0,10.0)).subtract(IntervalSet.of((-5.0,-MERGE_EPS/2))))`

## 6. `intersect` manufactures a sliver across a real gap, and `covers()` honours it — S2

**(a) Trigger.** Evidence `IntervalSet.of((0.0, 5.0), (5.0 + 1.5e-6, 10.0))` — a genuine
1.5 µs gap that survives normalisation. `Interval.intersect` accepts a hit when
`start <= end + MERGE_EPS`, so a clip ending inside the gap still "hits" the second
member, and the inverted result is repaired by `Interval.__post_init__` into a span of
width up to `MERGE_EPS`; normalisation then merges it onto the first member.

**(b) Editor sees.** `slice(4.0, 5.00000075)` returns a **single** span
`Interval(4.0, 5.0000015)` — the 7.5e-7 s of unsupported gap has been welded shut — and
`covers(Interval(4.0, 5.00000075))` → `True` for a clip that is genuinely not fully
backed. `restrict` keeps the item with support `[5.0, 5.0000015]` and its original
confidence 0.9 when restricted to `[5.000001, 5.0000011]`, a region **entirely inside the
gap**. Magnitude is sub-microsecond, so no editor's clip moves; the defect is that the
verify primitive can be satisfied by manufactured support.

**(c) Silent.**

**(d) Test.**
`assert not IntervalSet.of((0.0,5.0),(5.0+1.5e-6,10.0)).covers(Interval(4.0, 5.0+7.5e-7))`

## 7. `for_modality` with a plain string silently returns nothing — S2

**(a) Trigger.** `EvidenceSet.for_modality("text")` — a `str` instead of a `Modality`.

**(b) Editor sees.** `for_modality` compares `i.modality is modality`. `Modality.TEXT is
"text"` is `False` even though `Modality.TEXT == "text"` is `True`, so a retrieval layer
that passes the channel name as a string gets **an empty answer** — "there is no evidence
on this channel" — and acts on it. The enum-vs-enum case is fine (`Modality("text") is
Modality.TEXT` → `True`, so a JSON round trip is safe); only the raw-string caller is
bitten.

**(c) Silent.** The empty `EvidenceSet` is valid data, indistinguishable from a genuine
"nothing on this channel".

**(d) Test.**
`assert len(es.for_modality("text")) == len(es.for_modality(Modality.TEXT))` — or that a
`str` raises `TypeError`.

## 8. `about()` matches the negation of what was searched for — S2

**(a) Trigger.** `EvidenceSet.about("speaker")` over a claim `"no speaker is visible in
this span"`.

**(b) Editor sees.** Substring matching over `claim` and `rationale` returns the
"no speaker" item alongside `"speaker two is on camera"`, and also `"the budget is
discussed"`, whose rationale is `"not about speakers at all"`. A keyword search for a
presence returns evidence asserting an absence, and rationale text can drag in items
whose claim never mentioned the term. The docstring is candid that the matcher is crude,
which is the right call for the module — but a caller who treats `about()` hits as
"evidence that X occurs here" will publish a clip from the item that says X never occurs.
Negative claims need a distinguishable shape (a claim field, or an explicit polarity) for
a crude matcher to be safe.

**(c) Silent.**

**(d) Test.**
`assert Evidence(claim="no speaker is visible", ...).claim not in [i.claim for i in es.about("speaker")]` — as a pinned *desired* behaviour once polarity exists.

## 9. An inverted `in_window` is silently repaired into a wide match — S2

**(a) Trigger.** `EvidenceSet.in_window(10.0, 0.0)`.

**(b) Editor sees.** `Interval(start, end)` swaps the endpoints in `__post_init__`, so the
query becomes `[0, 10]` and every item inside that range matches — including evidence at
t=2..8. A caller whose arguments are transposed (two variables the caller believes are
ordered the other way) gets a broad positive answer rather than an empty or wrong one.
The same repair is a *feature* when the inversion comes from a decoder; here it hides a
caller error at the query boundary.

**(c) Silent.**

**(d) Test.**
`assert list(EvidenceSet.of(ev2to8).in_window(10.0, 0.0)) == []` — as the pinned desired
behaviour, or document the repair.

## 10. Zero-width spans are kept as members, contradicting `_normalise`'s own docstring — S2

**(a) Trigger.** `IntervalSet.of((0.0, 10.0), Interval(15.0, 15.0), (20.0, 30.0))`.

**(b) Editor sees.** `_normalise` documents "sort, drop empties, merge overlaps and
near-touches", but no branch drops anything: cardinality is **3** with an instant in the
middle, and `IntervalSet.of((5.0, 5.0))` reports `cardinality 1`, `duration 0.0`,
`is_empty False`. An evaluation harness comparing cardinality against a ground-truth span
count will count beats and cut points as spans. This is defensible — the module's own
rationale says instants are legal and common — but the code and the docstring disagree,
and the disagreement is exactly the kind that makes a later "obviously we drop empties"
refactor silently change the answer type.

**(c) Silent.**

**(d) Test.** Pick one and pin it:
`assert IntervalSet.of((0.0,10.0), Interval(15.0,15.0), (20.0,30.0)).cardinality == 3`
— and fix the docstring, or drop the empties and keep the test at `2`.

## 11. `restrict` keeps the original confidence on a sliver and drops what it excludes — S2

**(a) Trigger.** `EvidenceSet.of(wide_0_to_60_conf_0.95).restrict(IntervalSet.of((30.0, 30.0 + 5e-7)))`.

**(b) Editor sees.** The item survives with confidence **0.95** and its full original
claim, now "supporting" a 5e-7 s sliver — the confidence was measured over a minute of
audio and is now presented as the measured confidence of half a microsecond of it. In the
other direction, an item that does not overlap the region is **dropped** with no report,
so a caller cannot distinguish "no evidence here" from "evidence existed but was
elsewhere". `EvidenceSet`'s docstring promises "filters report rather than repair";
`restrict` neither reports nor preserves. Neither half moves a frame on its own; the risk
is a confidence-weighted decision made on clipped evidence.

**(c) Silent.**

**(d) Test.**
`assert EvidenceSet.of(wide).restrict(IntervalSet.of((30.0, 30.0+MERGE_EPS/2)))[0].confidence != wide.confidence or "clipped" in rationale` — i.e. pin that clipping either re-scores or says so.

## 12. `Evidence.link` overwrites the one relation label across several targets — S2

**(a) Trigger.** `Evidence(...).link("e1", "during").link("e2", "before")`.

**(b) Editor sees.** `relates_to == ("e1", "e2")` but `relation == "before"`. The
`during` label for `e1` is gone, and `before` — a relation to `e2` — now appears to
describe both edges. The evidence graph is typed to prevent exactly this ("a reader can
ask what is *during* this span and get a typed answer"), and a two-edge node carries one
label. Two construction shapes are also accepted silently: `relates_to=("e1","e2")` with
`relation=""` (untyped links) and `relates_to=()` with `relation="during"` (a label with
no target). All three corrupt the graph without raising.

**(c) Silent.** `link` validates the *name* but not the arity.

**(d) Test.**
`assert len(e.link("e1","during").link("e2","before").relates_to) == len({e.relation})` fails — pin that multi-link items carry one label per edge or are refused.

## 13. `EvidenceSet.from_dict` is all-or-nothing — one bad record kills the load — S2

**(a) Trigger.** A memory file whose items include one record with
`"modality": "haptic"` (a future schema's channel) among otherwise-valid records.

**(b) Editor sees.** `EvidenceSet.from_dict` raises `ValueError: 'haptic' is not a valid
Modality` and the **entire set** fails to load, including every valid record before and
after it. `Modality(...)` and `Evidence.from_dict`'s required `claim` key are both
raise-first-ask-questions-later, and there is no skip-and-report variant. For a memory
that is supposed to accumulate across runs and survive an instrument being replaced, one
unrecognised value makes the whole store unreadable rather than degrading to "one item
could not be read". Loud failure is the right default for a *write* path; a *read* path
over persisted history needs a report.

**(c) Raises** — listed here because the raise is the failure mode at the store boundary,
not because it is silent.

**(d) Test.**
`assert len(EvidenceSet.from_dict_safe(payload).items) == 2 and len(errors) == 1` — once a reporting variant exists.

## 14. `Source.of` gives mathematically equal params different digests — S2

**(a) Trigger.** `Source.of("m", "f", threshold=0.1 + 0.2)` vs
`Source.of("m", "f", threshold=0.3)`.

**(b) Editor sees.** Digests `88f468847194` and `8705fd1d30d0`. Same instrument, same
effective parameter, different digest — because the digest is SHA1 over `f"{value}"`,
which renders `0.30000000000000004` and `0.3` differently. The consequence the field
exists to prevent runs backwards: a caller computing a threshold two ways (a literal vs
`1/3`, a stored value vs a recomputed one) produces a memory the store treats as built by
a different instrument, invalidating conclusions or forcing re-runs. Key order is stable
(`threshold`/`samples` either way → `c30bd58e6d30`), so this is specifically the float
rendering path. A canonical float formatter (`repr` of the shortest round-trip is what
`f"{x}"` already gives; the fix is to round or normalise before hashing) would close it.

**(c) Silent.**

**(d) Test.**
`assert Source.of("m","f", t=0.1+0.2).params_digest == Source.of("m","f", t=0.3).params_digest`

## 15. `relation()` and `overlaps()` disagree about the same pair — S3

**(a) Trigger.** `Interval(0.0, 5.0)` and `Interval(5.0 + 5e-7, 10.0)`.

**(b) Editor sees.** `overlaps()` → `True` (tolerant, `+MERGE_EPS`), `relation()` →
`"before"` (strict: `meets` requires exact `==`). An evidence link typed from the same two
spans says `before` while the set algebra says they share points. Nobody publishes a wrong
clip; the Allen label is just not derived from the same predicate as the algebra, and a
reader of the graph will eventually notice the inconsistency.

**(c) Silent.**

**(d) Test.**
`assert Interval(0.0,5.0).relation(Interval(5.0+MERGE_EPS/2, 10.0)) == "meets" if Interval(0.0,5.0).overlaps(Interval(5.0+MERGE_EPS/2,10.0)) else True`

## 16. `is_instant` (1e-6) and `DEGENERATE_SECONDS` (0.01) are two different "zero widths" — S3

**(a) Trigger.** An evidence span of 0.005 s.

**(b) Editor sees.** Memory: `cardinality 1`, `is_instant False`, a real member.
`cut_algebra.normalize_keeps`: dropped outright (`end - start <= DEGENERATE_SECONDS`).
The same detection is a span in memory and nothing in the cut list. This is the same
class of defect as #3 with a different threshold — the two modules' notions of degeneracy
were never reconciled — and at 5 ms the divergence is editor-visible in a way the
sub-microsecond ones are not.

**(c) Silent.**

**(d) Test.**
`assert not any(0.005 <= iv.duration <= DEGENERATE_SECONDS for iv in evidence_intervals)` — or pin the two thresholds' relationship explicitly.

## 17. Empty support with full confidence is legal, and `grounded` defaults to `True` — S3

**(a) Trigger.** `Evidence(claim="faces detected", intervals=IntervalSet.empty(), ...)`,
and any inferred item constructed without `grounded=False`.

**(b) Editor sees.** Cardinality 0 with confidence 1.0 and `grounded=True` is accepted
without question, and it is **indistinguishable at construction** from a detector that ran
and returned no regions. Both serialise to the same shape; nothing records which
happened. Empty support is often *correct* — "the budget is never mentioned" is a real
negative answer and the empty `IntervalSet` is the right carrier for it — so this is not
"make it illegal". It is: the type cannot separate a negative result from a failed
detection, and the default `grounded=True` means an inference that forgets the flag reads
as a direct observation. Both are provenance questions, and provenance is what the module
exists to carry.

**(c) Silent.**

**(d) Test.**
`assert Evidence(claim="faces detected", intervals=IntervalSet.empty(), ...).rationale != ""` — pin that a zero-cardinality claim must explain itself.

## 18. Nothing bounds-checks evidence against the source — S3

**(a) Trigger.** `Evidence(claim="speech", intervals=IntervalSet.of((0.0, 7200.0)))`
about a 90-minute source.

**(b) Editor sees.** Legal; `to_dict` has no field for a source duration. `expand`'s
`bounds` argument is the only clamp in the package and it is optional and (per #1) broken
for out-of-range spans. `shift` is unclamped by design. So the *only* place a source
duration exists is `cut_algebra`, and an evidence set and a cut list can disagree about
the out-point of a clip whose evidence runs past the end of the source. This is the root
of #1 and of the `expand`-past-the-end question; it is called out separately because the
fix is a field, not a clamp.

**(c) Silent.**

**(d) Test.**
`assert ev.intervals.bounds.end <= ev.source_duration` — once `Evidence` (or the store) carries one.

## 19. Determinism and serialisation are clean — verified, not a defect

Executed, and no failure found:

- **Float round trip is exact.** `364.84`, `364.84000000000003`,
  `235.21999999999997`, `0.1+0.2`, `1e-9`, `3599.999999999` all survive
  `to_dict`/`from_dict` and a real `json.dumps`/`json.loads` cycle bit-for-bit.
- **Every `Evidence` field survives a JSON round trip** with value equality:
  `claim`, `modality`, `confidence`, `rationale`, `grounded`, `artifacts`,
  `relates_to`, `relation`, `id`, `intervals`, `source`. `modality` and `grounded` come
  back `is`-identical as well.
- **Determinism holds.** Repeating `union`/`intersect`/`subtract`/`complement` chains and
  `json.dumps` produced identical results across runs.
- **`IntervalSet.from_dict` ignores the persisted `duration` and `cardinality` measures**
  and recomputes — so a stale persisted `duration: 999.0` cannot corrupt a loaded set
  (verified). Worth pinning as a test, since it is the load path's integrity assumption.

One non-defect worth recording so it is not re-litigated: a round trip does **not** change
cardinality, because two spans within `MERGE_EPS` are already merged at *construction*, by
`IntervalSet.__post_init__`, before serialisation ever sees them. The merge is the
documented invariant, not a serialisation loss.

## 20. Minor, listed for completeness — S3

- **`nearest()` ties are broken silently to the earlier member** (`nearest(3.0)` on
  instants at 1.0 and 5.0 → the 1.0 member, both 2.0 away). Deterministic and harmless;
  pin it so a future tie-break does not change grounding results.
- **`above_confidence(NaN)` / `below_confidence(NaN)` return empty sets** (all
  comparisons against NaN are False). A caller passing a NaN floor gets "nothing" with no
  signal that the floor was poison. Same root as #4.
- **`EvidenceSet.union` accepts duplicate ids**; `with_id` returns the first and the rest
  are unreachable except by scanning. The store assigns ids, so this is a store-layer
  concern, but `union` is the merge path and it does not check.
- **`subtract` can emit a zero-width remainder**: removing `(5.0, 10.0 - 5e-7)` from
  `(0, 10)` yields `[0,5]` and a second member of width 5e-7. `cut_algebra` produces the
  same pair numerically, so the two agree here — but the second member is `is_instant`,
  and per #10 it counts toward cardinality. Feed #6.

---

## Verified by execution

Ran four read-only probe scripts against `.venv/bin/python` (Python 3.12), importing
`engine.memory.intervals`, `engine.memory.evidence` and `engine.cut_algebra`. Every number
quoted above is printed output, not analysis:

| # | Claim | Result |
|---|---|---|
| 1 | `Interval(200,210).expand(0.0, [0,100])` | `Interval(100.0, 200.0)`; `covers((100,200))` → `True`; `contains_time(150.0)` → `True` |
| 1 | `Interval(105,110).expand(0.0, [0,100])` | `Interval(100.0, 105.0)` |
| 1 | `Interval(95,105).expand(0.0, [0,100])` | `Interval(95.0, 100.0)` — correct, so the bug is specific to spans that do not reach the bounds |
| 2 | `covers(Interval(5,5))` on `[0,4]∪[6,10]` | `True`; `at(5.0)` → `None`; also `True` at `t=50`, `1000`, `-100` |
| 2 | `covers(Interval(4,6))` on the same set | `False` — the bug is zero-width only |
| 3 | gap `5e-7` | memory cardinality 1, `cut_algebra(gap=0)` 2, `cut_algebra(gap=1e-6)` 1; membership at `t=10.00000025`: memory `in`, cut_algebra `out` |
| 3 | touching exactly, gap `2e-6` | both agree (1 and 2 members) |
| 4 | `confidence=NaN/+inf/-inf/-5.0/2.0/0.31` | `1.0 / 1.0 / 0.0 / 0.0 / 1.0 / 0.31`; the NaN item passes `above_confidence(0.0)` |
| 5 | `subtract` with a cutter ending `5e-7` below the base start | `Interval(-5e-7, 10.0)`; `rest ∪ b` duration 15.0 vs original 10.0; `cut_algebra` → `[(0.0, 10.0)]` |
| 5 | `subtract` with a cutter starting `5e-7` above the base end | `Interval(0.0, 10.0000005)` |
| 5 | downstream | `cutlist_from_intervals` clamps the negative start back to `0.0` — the editor's rendered cut is not displaced |
| 6 | `slice(4.0, 5.00000075)` across a `1.5e-6` gap | one span `Interval(4.0, 5.0000015)`; `covers` → `True` |
| 6 | `restrict` to `[5.000001, 5.0000011]` inside the gap | 1 item survives, support `[5.0, 5.0000015]`, confidence 0.9 |
| 7 | `for_modality("text")` vs `for_modality(Modality.TEXT)` | 0 items vs 1 item; `Modality("text") is Modality.TEXT` → `True`; `Modality.TEXT is "text"` → `False` |
| 8 | `about("speaker")` | matches "no speaker is visible…" and "the budget is discussed" (via its rationale) |
| 9 | `in_window(10.0, 0.0)` on evidence `[2,8]` | matches |
| 10 | `IntervalSet.of((0,10), (15,15), (20,30))` | cardinality 3, duration 20.0; `of((5,5))` → cardinality 1, `is_empty False` |
| 11 | `restrict` to a `5e-7` sliver | item kept, confidence 0.95, claim unchanged; non-overlapping restrict → 0 items |
| 12 | `link("e1","during").link("e2","before")` | `relates_to=("e1","e2")`, `relation="before"` |
| 13 | `EvidenceSet.from_dict` with one `"modality": "haptic"` record | `ValueError`, whole set fails to load |
| 14 | `Source.of(..., threshold=0.1+0.2)` vs `0.3` | digests `88f468847194` vs `8705fd1d30d0`; key order stable (`c30bd58e6d30` either way) |
| 15 | `overlaps` vs `relation` for a `5e-7` gap | `True` vs `"before"`; `intersect` → `Interval(5.0, 5.0000005)` |
| 16 | widths `1e-7 … 0.02` | memory keeps all as members; `normalize_keeps` drops every one ≤ 0.01 |
| 19 | float and `Evidence` JSON round trips | exact for all tested values and all fields |
| 19 | stale persisted `duration: 999.0` | ignored; recomputed to 10.0 |
| 19 | determinism | all repeated chains and `json.dumps` outputs identical |
| 20 | `nearest` tie at `t=3.0` between instants 1.0 and 5.0 | returns the 1.0 member |
| 20 | `above_confidence(NaN)` / `below_confidence(NaN)` | 0 items, both |

## Could not verify

- **Whether sub-`MERGE_EPS` gaps and sub-`MERGE_EPS` boundary straddles actually occur in
  this pipeline's real outputs.** The `364.84` / `364.84000000000003` precedent in
  `engine.edit_plan._word_aligned` makes them plausible for ASR-derived bounds, and #3
  and #5 both depend on one occurring to bite. I did not run the pipeline against the
  corpus; the defects above are proven against constructed inputs, and their *frequency*
  is unmeasured.
- **Whether the manufactured sliver in #6 is bounded by `MERGE_EPS` in every overlap
  geometry.** It is bounded by construction for the two cases I tried (`5e-7` and the
  `1.5e-6` gap), but I did not prove the bound over all configurations; a pathological
  multi-member slice might exceed it.
- **The store and ingest layers.** `engine/memory/__init__.py` documents `store`
  (`VideoMemory`) and `ingest` adapters; neither file exists in the tree yet. Everything
  about id assignment, persistence, staleness detection and how `Evidence` reaches disk is
  untested here, and several entries above (#13, #17, #18) are really store-layer
  contracts that the value types can only half-express.
- **Whether any caller currently passes a zero-width span to `covers()`** (#2) or a raw
  string to `for_modality` (#7). There are no callers yet — these modules are newly built
  — so these are hazards for the retrieval and grounding layers rather than live bugs.
- **`Interval.shift` clamping.** It is unclamped by design and documented as such; I found
  no path in these two modules that reaches it, and did not audit the export layer.
