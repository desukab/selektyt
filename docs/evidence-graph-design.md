# Evidence graph layer — typed Allen edges, integrity, and traceability over `EvidenceSet`

**Status: SUPERSEDED by the implementation.** This was written as a design for
the layer to be built next. `engine/memory/graph.py` now exists — 854 lines,
typed Allen edges, witness-checking and the integrity report — and it is on the
run path: the corpus stage calls `engine.memory.populate.build`, which builds
the graph from the speech regions and the corpus analysis the run already held.
It is kept because the reasoning that chose the design is not recoverable from
the code, and because the sections on what the layer deliberately does *not* do
are still the contract. Where the code and this document disagree, the code and
`tests/test_memory_graph.py` are the truth.

**Status:** design, not implementation. `engine/memory/graph.py` does not exist in
the tree; `intervals.py`, `evidence.py`, `store.py` and `ingest.py` do. This
document describes the layer to be built next. It touches no file under `engine/`
or `tests/`.

**Scope:** the structure that makes a conclusion traceable from an editorial claim
back to the observation that grounds it, over a memory that may contain thousands
of items across four modalities. It designs the *graph* — edges, integrity,
witness-checking, trace — and the foundation changes the graph requires. It does
not design retrieval (`docs/retrieval-design.md`), grounding, story structure
(`docs/story-graph-design.md`), or any editorial decision.

**What it is not.** Not a query language, not a traversal engine, not a knowledge
base. It is a read index with an integrity report. §2.9 names what it
deliberately does not build.

---

## 0. Provenance, and the status of every claim below

Part 1 is an adversarial review of `engine/memory/intervals.py` and
`engine/memory/evidence.py` — the review that produced `docs/memory-failure-modes.md`
— filtered to the findings the graph layer depends on. Findings D1–D8 are
reproduced with their severities and their **current status**, because the review
predates several fixes and three of its entries now describe behaviour the code no
longer has.

Every status in Part 1 was **verified against the tree as it stands** on
2026-09-29, by reading the code, not by recalling the review. Where the review's
line numbers no longer hold, this document cites the current ones. Where the
review claimed a defect and the code has since been fixed, this document says
*fixed* and cites the fix, rather than leaving a reader to chase a defect that is
not there.

`MERGE_EPS = 1e-6` (`engine/memory/intervals.py:67`); `SCHEMA_VERSION = "1.1"`
(`engine/memory/evidence.py:62`).

---

## Part 1 — The foundation the graph builds on

### D1 — `link` could not carry two edges with two labels — **CRITICAL → FIXED**

**Was.** `Evidence` carried `relates_to: tuple[str, ...]` alongside a singular
`relation: str`. `Evidence.link("B", "before").link("C", "after")` kept both
targets but recorded only `after` — the first edge was silently relabelled, and a
reader tracing the chain read the wrong relation for half of it. Three
construction shapes were accepted without raising: two targets with one label, two
targets with no label, and a label with no target. This is failure mode **#12** in
`docs/memory-failure-modes.md`.

**Why it was critical here.** The graph's entire purpose is that a reader can ask
"what is *during* this span" and get a typed answer. A node with two edges and one
label makes every typed answer wrong half the time, and the error is invisible in
the serialised record — it is a wrong *relation*, carried in a field that looks
well-formed.

**Is.** Schema bumped to `1.1`. `EvidenceLink(target, relation)`
(`engine/memory/evidence.py:135-167`) is one record per edge, appended by
`Evidence.link` (`:229-247`) without touching the existing edges:

```python
def link(self, other_id: str, relation: str) -> Evidence:
    """... The relation is validated at construction of the ``EvidenceLink`` —
    so a typo is a loud error rather than an unmatchable string in the evidence
    graph — and every edge keeps its own label. The converse is available via
    ``EvidenceLink.converse``; call it when building the reverse edge, so the
    graph never carries two spellings of one relation."""
```

Validation is at the edge, not at the graph: a non-Allen relation raises
`ValueError` at `EvidenceLink.__post_init__` (`:150-155`), naming
`ALLEN_RELATIONS` (`engine/memory/intervals.py:74-78`) in the message. An empty
target is still constructible — the graph's dangling check (§2.5) is what reports
it, because an unattached edge and an edge to a not-yet-recorded item are
different facts and the value type cannot tell them apart.

**The graph inherits.** One label per edge is a hard requirement, not a
preference. Nothing in §2 may reintroduce a shared label, and the schema-1.0
migration in `Evidence.from_dict` (`:267-294`) is the reason the graph must build
over ids rather than over objects: a loaded 1.0 record's edges are *reconstructed*,
and a graph that assumed edges were authoritative would treat reconstruction as
truth.

### D2 — `relation()` disagreed with the algebra about boundaries — **HIGH → FIXED**

**Was.** `Interval.relation()` tested boundaries with exact `==` while
`overlaps()`, `intersect()` and `_normalise` all used `MERGE_EPS`. For
`Interval(1, 2)` against `Interval(2 + 1e-9, 3)`: `relation()` said `before`,
`overlaps()` said they shared a point, and `IntervalSet` merged them into one span.
Three layers, three answers about whether a cut point existed. Failure mode
**#15**.

**Why it was high.** The graph's typed edges are *labels for the algebra's own
result*. If the label and the algebra disagree, the geometry check (§2.6) cannot
work at all — it would be checking a declaration against a computation that uses a
different predicate, and a link typed `before` between two spans the set algebra
considers merged is not a subtle inconsistency but a contradiction the graph must
report. The check is unusable until this is fixed; this is the finding that gated
§2.6.

**Is.** Fixed. `Interval.relation` (`engine/memory/intervals.py:187-226`) uses the
`_normalise` condition at both boundaries, with the reasoning recorded in the
docstring:

```python
if self.end <= other.start + MERGE_EPS:
    return "meets" if other.start <= self.end + MERGE_EPS else "before"
if other.end <= self.start + MERGE_EPS:
    return "met_by" if self.start <= other.end + MERGE_EPS else "after"
```

The invariant the graph depends on now holds by construction:
`relation(a, b) == "before"` exactly when `_normalise` keeps `a` and `b` apart. A
sub-`MERGE_EPS` gap is `meets`, matching what `IntervalSet` merges; the boundary
cases and the algebra now answer the same question the same way.

**The graph inherits.** §2.6's witness test may now treat `relation()` output as
ground truth. The graph still does not recompute relations from raw floats; it
reads them off the same method the merger does, which is what makes the check
meaningful rather than decorative.

### D3 — `restrict` orphans edges — **HIGH → pinned by design, not a defect**

`EvidenceSet.restrict` (`engine/memory/evidence.py:396-414`) clips every item to a
span set and **keeps the clipped item's `links` verbatim** — including links to
items the restriction dropped. The item is still in the world; its edges point out
of it.

**Why this is right and not a bug.** `restrict` is a filter, and the
`EvidenceSet` contract is that filters report rather than repair
(`engine/memory/evidence.py:307-310`). A filter that rewrote links to match the
survivors would be *repairing* — it would be deciding which of an item's declared
relations are still true, which is exactly the judgement §2.6 exists to make with
geometry as evidence, not as a side effect of clipping. And a filter that dropped
the orphaned item would silently discard evidence because *another* item was
outside a window, which is a much worse failure.

**What the graph does about it.** It does not patch `restrict`. After any
`restrict` or `union`, the graph is **rebuilt** from the resulting set, and the
rebuild reports the orphans (§2.5). An orphan is reported as `dangling` and
excluded from traversal under both strict modes; the report is the mechanism a
caller uses to decide whether the clipped world is still a coherent one.

### D4 — no referential integrity in the value types — **MEDIUM → pinned by design**

`Evidence` is a value: it does not know whether the id in its `EvidenceLink` names
anything. `EvidenceSet.union` (`engine/memory/evidence.py:393`) accepts duplicate
ids; `with_id` (`:385`) returns the first and the rest are unreachable except by
scanning. Both are failure mode **#20** entries, and both are deliberate — the
store, not the value type, assigns ids (`engine/memory/store.py`), and pushing the
check into the value type would make every construction a set-membership test
against a collection the value does not hold.

**What the graph owns.** Integrity is a build-time concern of the graph and only
of the graph. The graph holds `_items: dict[str, Evidence]` and can answer "does
this id exist" in O(1); the value types cannot, and no attempt is made to make
them. A duplicate id in a set the graph builds over is reported in
`IntegrityReport.duplicates` with a count, and `neighbours`/`trace` address the
first occurrence — the same resolution as `with_id`, so the graph and the set
agree about which of two same-named items the world contains.

### D5 — `covers()` returned `True` for any instant — **MEDIUM → FIXED**

**Was.** `covers(Interval(5.0, 5.0))` on `[0,4] ∪ [6,10]` returned `True`, while
`at(5.0)` returned `None` and `contains_time(5.0)` returned `False` — the module's
three answers about one point contradicted each other, unbounded in range (`True`
at `t=1000`, `True` at `t=-100`). Failure mode **#2**, rated S1: the verify stage
asks `covers` "is the whole proposed clip backed", and for a zero-width clip — a
cut point, a beat — it always said yes.

**Why it mattered to the graph.** `covers` is the verify primitive; the graph's
`supports` query (§2.7) is the fact-checker's version of the same question. A
graph built over a `covers` that was satisfied by nothing would report every
conclusion as grounded.

**Is.** Fixed at `IntervalSet.covers` (`engine/memory/intervals.py:380-399`): the
empty slice returns `False` on its own (`:387-393`) rather than falling through to
a width comparison of `0 <= 0 + MERGE_EPS`, and the endpoints are tested
explicitly (`:395-398`) so a member has to *reach* both ends of the span.

### D6 — `EvidenceSet.intervals` was quadratic — **MEDIUM (perf) → FIXED**

**Was.** `EvidenceSet.intervals` folded `union` across items, re-normalising a
growing set per item. At 4000 items / 8000 spans this was measured against a
single flat-then-normalise pass.

**Why it mattered.** The graph's geometry check (§2.6) intersects sets; the
ingest join (`engine/memory/ingest.py`) runs set-against-set overlap tests; a
trace (§2.7) walks edges and reads intervals at every step. A quadratic fold on
the union of all spans would have been in the path of every query.

**Is.** Fixed at `engine/memory/evidence.py:334-341`:

```python
def intervals(self) -> IntervalSet:
    """... Flat then normalise once. Folding union across the items re-normalises
    a growing set per item — quadratic in the span count, and measured at
    ~2700x a single pass on a 4000-item set for an identical result."""
    return IntervalSet([s for i in self.items for s in i.intervals])
```

Result identical, cost linear. The measured factor is ~2700×, not the smaller
figure an earlier draft of this review carried; this document quotes the
measurement in the code.

**The graph inherits.** Cost is not free even so — §2.6 avoids calling this
property where it can, testing extremes in O(1) instead (§2.6.2).

### D7 — `for_modality` used `is` — **LOW → FIXED**

**Was.** `for_modality` compared `i.modality is modality`. `Modality.TEXT is "text"`
is `False` even though `==` is `True`, so a caller passing the channel name as a
plain string — a retrieval layer reading a channel from a query — got an empty
answer and acted on it. Failure mode **#7**. The enum-vs-enum case was safe
(`Modality("text") is Modality.TEXT` → `True`), so only the raw-string caller was
bitten, silently.

**Is.** Fixed at `engine/memory/evidence.py:345-351`, with the hazard recorded in
the comment: `Modality` mixes in `str`, so a modality read back from a persisted
record is a plain string until re-wrapped, and `is` compares the two as distinct
objects. The graph's `TraceResult` collects modalities from loaded records and so
depends on this comparison being `==`.

### D8 — `from_dict` was all-or-nothing over nulls — **LOW → FIXED**

**Was.** A JSON `null` in an optional field — `"artifacts": null`,
`"links": null` — distinguished from an absent key only by `KeyError` vs
`AttributeError`, and a record with a present-but-null field could fail to
deserialise even though its substantive fields were intact.

**Why it mattered to the graph.** The graph is rebuilt on load from a persisted
set. A load that failed on a null field would leave the graph unbuilt over a
memory whose evidence was fine — and since the graph is the traceability layer,
that would be a memory that cannot be audited over a formatting quirk.

**Is.** Fixed at `Evidence.from_dict` (`engine/memory/evidence.py:267-294`):
present-but-null is absorbed via `d.get(...) or {}` / `or ()` for `intervals`,
`source`, `artifacts` and `links`, and the *absence* of `links` triggers the
schema-1.0 migration rather than an empty edge list:

```python
legacy_targets = d.get("relates_to") or ()
legacy_relation = d.get("relation") or ""
if legacy_targets and legacy_relation:
    links = tuple(EvidenceLink(t, legacy_relation) for t in legacy_targets)
```

One edge per target, and targets-without-a-label are **dropped rather than
guessed** — a record the review called untypeable is still untypeable; it is
reported by the store, not silently typed. Related but separate: failure mode
**#13** (an unrecognised *modality* value still raises and takes the whole set
with it) is not fixed by D8 and is not something the graph repairs either; §2.5
reports load-time problems it can see and does not paper over the ones it cannot.

### D-extra — set-against-set overlap — **FIXED (this session)**

Not in the original review, because the review predates the joins that need it.
`IntervalSet.overlaps` accepted only a single `Interval`. The memory stack's joins
are set against set — `link_overlapping` in `engine/memory/ingest.py` tests an
item's whole interval set against each target's — and a caller forced to unwrap
would have had to guess which member to test, silently reducing a
variable-cardinality answer to its first span. This is the same hazard
`docs/retrieval-design.md:285-296` pins at the retrieval layer.

**Is.** `IntervalSet.overlaps(span: Interval | IntervalSet)`
(`engine/memory/intervals.py:351-364`) dispatches on the operand, and the two-set
path is a new merge-pass helper:

```python
def _sorted_overlap(a: list[Interval], b: list[Interval]) -> bool:
    """Do two sorted-disjoint sequences share any point? One merge pass, stops
    at the first hit — the boolean form of ``IntervalSet.intersect``, for the
    callers that only need to know *whether* two items relate."""
```

O(N + M) on the sorted invariant, not O(N·M) pairwise — the same cost argument as
the set algebra (`engine/memory/__init__.py:60-63`). The graph's edge construction
and geometry witness tests both use this spelling.

### Deliberate, and not to be changed by this layer

- **`union` keeps duplicate ids** (failure mode #20). The store assigns ids; the
  graph reports duplicates rather than renaming a caller's name (compare
  `engine/memory/store.py`'s rule that a duplicate supplied id is kept and
  reported, not renamed).
- **`Evidence` is frozen but unhashable** — `IntervalSet` is mutable, so
  `frozen=True` does not make instances hashable. The graph keys on `id` never on
  the object, and this is why.
- **Confidence is clamped silently** (`evidence.py` `__post_init__`). The
  NaN-becomes-1.0 path (failure mode #4) is a defect in the value type, not
  something the graph patches; a NaN-confidence item reaches the graph as a
  maximum-confidence item and the graph reports what it sees.
- **`with_id` is a linear scan**. The graph builds one `dict[str, Evidence]`
  instead and does not call it in a loop.

---

## Part 2 — The graph layer

### 2.1 What it is

`engine/memory/graph.py`, one new module. **A read index over an `EvidenceSet`,
not a parallel store.** It holds no data the set does not; it holds access
structures and an integrity report. Given the same set it builds the same index
byte-for-byte, and it is re-derived from the set on load — never persisted as a
new artefact. The only thing written to disk is `IntegrityReport`, and only as a
diagnostic for an editor to read; it is not read back, because it is derivable.

The distinction matters because of what the graph is for. Memory accumulates
across runs and survives instruments being replaced (`Source.params_digest`,
`engine/memory/evidence.py:86-99`); a *persisted* graph would be a second copy of
the evidence's relation structure that could drift from the evidence it describes.
A read index cannot drift, because it does not outlive the set it was built from.

Pure values: no I/O except the optional report write, no wall clock, no network,
no inference, no unordered-key iteration in anything that affects the output.

### 2.2 Values

In `engine/memory/evidence.py` (already present): `EvidenceLink(target, relation)`.

In `engine/memory/graph.py`:

```python
@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str          # the declared Allen relation, source → target
    converse: str          # derived via converse(), never a second declaration
    declared: bool         # True if an EvidenceLink states it; False if derived
    geometry: str          # the relation the spans actually realise, or ""

@dataclass(frozen=True)
class Neighbour:
    item: Evidence
    relation: str          # how this neighbour relates to the queried item
    converse: str
    declared: bool

@dataclass(frozen=True)
class GeometryConflict:
    source: str
    target: str
    declared: str
    computed: str
    kind: str              # "contradiction" | "bounds_mismatch"

@dataclass(frozen=True)
class IntegrityReport:
    dangling: tuple[str, ...]                 # edge targets no item has
    self_links: tuple[str, ...]
    converse_conflicts: tuple[tuple[str, str, str, str], ...]
    geometry_conflicts: tuple[GeometryConflict, ...]
    cycles: tuple[tuple[str, ...], ...]
    duplicates: dict[str, int]
    unsupported: tuple[str, ...]              # non-Allen labels, pre-D1 shape
    def ok(self) -> bool: ...

@dataclass(frozen=True)
class TraceStep:
    item: Evidence
    relation: str
    converse: str
    geometry: str
    declared: bool

@dataclass(frozen=True)
class TraceChain:
    root: str
    steps: tuple[TraceStep, ...]
    depth: int
    terminated: str        # "grounded" | "dangling" | "cycle" | "unresolved"

@dataclass(frozen=True)
class TraceResult:
    root: str
    chains: tuple[TraceChain, ...]
    sources: tuple[Source, ...]     # every instrument the chain crossed
    modalities: tuple[Modality, ...]
    report: IntegrityReport
```

Frozen throughout, matching the value types it indexes. Every tuple, never a list:
a report is a value an editor reads and a test compares.

### 2.3 The index

```python
class EvidenceGraph:
    @staticmethod
    def build(items, *, strict: bool = True) -> EvidenceGraph: ...

    def neighbours(self, id: str) -> tuple[Neighbour, ...]: ...
    def edges(self) -> tuple[Edge, ...]: ...
    def trace(self, id: str, *, max_depth: int = 8,
              max_chains: int = 16) -> TraceResult: ...
    def supports(self, id: str) -> tuple[Evidence, ...]: ...
    def report(self) -> IntegrityReport: ...
    def items(self) -> tuple[Evidence, ...]: ...
```

State, all private:

- `_items: dict[str, Evidence]` — first occurrence wins on a duplicate id,
  matching `EvidenceSet.with_id`.
- `_out: dict[str, tuple[EvidenceLink, ...]]` — declared edges, grouped by source.
- `_in: dict[str, tuple[str, ...]]` — source ids per target, for the forward walk
  in `supports`.
- `_duplicates: dict[str, int]`.
- `_report: IntegrityReport` — computed once at build, never per query.

Everything is keyed on `id`. `Evidence` is unhashable (D-extra), so no set of
objects and no dict keyed on the instance.

### 2.4 Direction and the converse

**An edge points from the *derived* item to the item it was derived *from*.** A
topic inferred from transcript words links to the word evidence; a face-track
observation is the target, not the source. This matches the production pattern
already in the codebase — `engine/corpus.py:24-30` attaches `reason` and
`evidence` to a *derived* judgement, naming what produced it, and an inferred
`Evidence` marks itself `grounded=False` for the same reason (failure mode #17:
the default `grounded=True` means an inference that forgets the flag reads as a
direct observation).

**The converse is derived, never stored twice.** `EvidenceLink.converse()`
(`engine/memory/evidence.py:159-164`) names the reverse edge via
`converse()` (`engine/memory/intervals.py:90-99`), so the graph carries one
spelling per direction. Storing both would invite the two to disagree, and a
disagreement would be a silent inconsistency in the structure whose entire purpose
is consistency.

**Deduplication.** At most one triple per adjacent item. Two cases:

- **A mutually-declared agreeing pair** — A declares `during` to B and B declares
  `contains` to A, and `converse("during") == "contains"` — collapses to **one
  declared edge**, with the converse available on read. Both declarations say the
  same thing; carrying two would make `edges()` double-count every reciprocal
  link.
- **A disagreeing pair** — A declares `during` to B while B declares `before` to
  A, and `converse("during") != "before"` — is reported in `converse_conflicts`
  as the four-tuple `(a, b, r_ab, r_ba)`, and **both directions are excluded from
  traversal**. Not repaired: the graph does not know which of two instruments is
  wrong, and picking one would be fabricating a relation. Excluded, because a
  traverse that followed a contested edge would present one side's claim as
  settled.

### 2.5 Integrity

Checked **once at build**, and again at `Evidence.link` creation time by the value
type's own validation. **Never per query** — a query that validated would make
integrity O(Q) over a memory where it is O(1), and would make the report a function
of when you asked rather than of what the memory contains.

| Condition | `strict=True` | `strict=False` |
|---|---|---|
| **dangling** — target id no item has (the D3 orphan, from `restrict`/`union`) | raise | report, exclude from traversal |
| **unsupported** — a relation name not in `ALLEN_RELATIONS` | raise | report, exclude |
| **self-link** — an edge to the item's own id | raise | report, exclude |
| **converse conflict** — §2.4 | raise | report, exclude both directions |
| **geometry conflict**, kind `contradiction` | raise | report, **keep**, flag |
| **cycle** over non-`equals` edges | raise | report, exclude the closing edge |
| **duplicate ids** | raise | report; first wins |

The one asymmetry is deliberate: a geometry `contradiction` is a *witness failure*
(§2.6) — the declared relation is not realised by any pair of spans — but the
items themselves and the *declared* edge are still facts about what an instrument
asserted. An editor investigating it wants the edge visible with a flag on it, not
silently removed; a strict build wants it loud because a chain that crosses a
contradicted edge is not a trace.

**Cycles.** 2.5.1 — a cycle over non-`equals` Allen edges is a *temporal
impossibility*: every one of `before`/`after`/`meets`/`met_by`/`overlaps`/`overlapped_by`
and the containment family imposes a strict temporal order, and composing strict
orders cannot return to the start. So a cycle of `before` edges is not a graph
quirk to tolerate; it means at least one declared relation is false, and the
report names the ring. `equals` is skipped — mutual `equals` between two items is
redundant, not impossible, and is covered by the dedup rule.

### 2.6 The geometry check — a witness test, not a bounds test

**The question.** Does a declared Allen relation hold between two items? The
items have *sets* of spans, and a relation declared between two sets has no single
truth value: `A` may be `before` `B` on one pair of spans and `during` on another.

**Three readings, and why the witness reading is the one.**

- *Bounds* — test `A.bounds` against `B.bounds`. Cheap, and wrong: two interleaved
  sets have overlapping bounds and still admit a genuinely disjoint pair.
- *Universal* — require every member pair to realise the relation. Wrong the other
  way: a single cross-grained pair would fail a relation that holds on all the
  rest, and a multi-span item would be untraceable because of one outlier.
- *Witness* — **the relation is supported if some member pair realises a
  compatible class.** Not "all", not "the bounding box": one pair is enough to say
  the declaration is not baseless, and no pair is enough to say it contradicts the
  geometry. This is the reading that matches what the declared relation *means* —
  an instrument asserting "this marker is during that scene" is asserting the
  existence of such a configuration, not its universality.

**Coarsening the thirteen relations into six fine classes.** Allen's thirteen are
finer than the comparison needs, and two relations that differ only in orientation
should not count as a conflict:

```python
_FINE_CLASS: dict[str, str] = {
    "before": "precedes",     "meets": "precedes",
    "after": "preceded_by",   "met_by": "preceded_by",
    "overlaps": "partial",    "overlapped_by": "partial_reverse",
    "starts": "inside", "during": "inside", "finishes": "inside", "equals": "inside",
    "started_by": "contains", "contains": "contains", "finished_by": "contains",
}
```

Compatibility is `_FINE_CLASS[computed] == _FINE_CLASS[declared]`. So `meets`
satisfies a declared `before` (both `precedes` — the instrument got the ordering
right and only the boundary tolerance wrong, which D2 makes a matter of `MERGE_EPS`),
and `during` satisfies a declared `starts` (both `inside`). `before` against a
declared `contains` is a `contradiction`; a `bounds_mismatch` is the informational
kind — computed and declared are in the same fine class on the extremes, but the
witness pair that realises it does not realise the declared relation exactly.
`bounds_mismatch` is reported; `contradiction` is the failing test.

**2.6.2 Cost.** The precedence classes do not need a member scan at all. On the
sorted-disjoint invariant, "does some member of `A` precede some member of `B`"
reduces to an extremes test:

```python
a_precedes_b = A.intervals[0].end <= B.intervals[-1].start + MERGE_EPS
b_precedes_a = B.intervals[0].end <= A.intervals[-1].start + MERGE_EPS
```

O(1) per pair, because the sequences are sorted. Only where the two sets intersect
— `A.intervals.overlaps(B.intervals)`, the D-extra merge pass — does the check fall
back to a bounded scan of the intersecting members. This is the reason not to call
`EvidenceSet.intervals` here even after D6: the extremes test reads two members,
not the union of all of them.

**Dependency.** Unusable before D2. A witness test compares a declared relation
against `Interval.relation()`'s output; when the two used different boundary
predicates, every sub-`MERGE_EPS` gap was a spurious `contradiction`. D2 is what
makes the check say what it means.

### 2.7 Traceability

**`trace(id)`** walks declared edges *backward* from a conclusion to its support.
It is the editor's "why does the system say this" query, and the reason the graph
exists.

Termination, in the order the walk applies it:

1. Stop at the first item with `grounded=True` — the chain **terminated `grounded`**.
   This is the observation, the end of the trace.
2. **`dangling`** — the next edge names an id nothing has (the D3 orphan, or an
   edge to an item dropped by a restriction).
3. **`cycle`** — an id already on this chain. Defensive: §2.5.1 reports cycles at
   build, so a strict build cannot reach one; the check exists so a permissive
   build still terminates.
4. **`unresolved`** — an item with `grounded=False` and no outgoing declared edges.
   This is **the query's most useful failure**: an inference that asserts support
   and names none. It is reported as a chain that stops with a reason, not as an
   error, because the editor's next act is to go and ground it — which is what the
   ladder in `engine/memory/store.py` is for (`escalate_needed` names the spans
   that need a harder rung).

One chain per maximal path; `max_depth = 8` and `max_chains = 16` bound the walk.
Both are named parameters, not buried literals, because the depth that is
appropriate for a two-span trace is not appropriate for a corpus-scale one, and
truncation must be the caller's decision. Ordering is declaration order, then id —
deterministic, and independent of dict insertion order.

**`TraceResult`** collects what an editor needs to check the conclusion: every
`Source` the chain crossed (so a replaced instrument is visible on the trace
itself, via `params_digest`), and every `Modality` (so a conclusion that rests
entirely on `TEXT` from a `poor` transcript is visible as such — the verdict
travels, `docs/retrieval-design.md:469-472`).

**`supports(grounded_id)`** walks `_in` *forward*: given an observation, which
conclusions rest on it? This is the fact-checker's query, and it is what makes a
replaced instrument actionable. When a detector is re-run at a different sample
count, `params_digest` changes and the instrument is a different instrument;
`supports` enumerates every conclusion that inherited the old one, which is the
list a maintainer must re-derive rather than re-discover by failure.

### 2.8 Determinism and persistence

`build` is a pure function of the set. No wall clock, no network, no inference, no
iteration over an unordered collection in anything that affects the output — dict
iteration order is insertion order in CPython and must not be relied on as though
it were semantic; every output is sorted or ordered by an explicit key.

Byte-identical for identical input, which is what makes these things testable:

- `report() == report()` across two builds over the same set.
- `trace(id).chains == trace(id).chains` across two builds.

And — the check the store must pin — the round trip: **build → write → reload in a
fresh interpreter → rebuild → compare**. A graph that differed before and after
persistence would mean the persisted record is not the one the graph was built
over, and the traceability layer would be answering questions about a memory that
no longer exists. This is the same determinism contract the store already holds
for the memory itself (`engine/memory/store.py`: `save` is deterministic because
`sort_keys=True` and no wall clock, which is what makes a committed memory file an
auditable artefact).

**The graph is not persisted as a new artefact.** Only `IntegrityReport` may be
written, as a diagnostic; it is never read back.

### 2.9 What this layer does not build

- **No query language or traversal engine.** `neighbours`, `trace`, `supports` are
  three named queries. There is no path expression, no pattern matching, no
  recursion exposed to a caller.
- **No new modality, confidence model or provenance field.** The graph reads the
  ones `Evidence` already carries.
- **No semantic links.** Edges are Allen relations only. No `same_speaker`, no
  `same_topic`, no `supports_the_claim_of`. Reasoning about identity is a
  grounding-layer concern, and a semantic edge asserted without a grounding
  instrument would be exactly the fabricated evidence the memory stack exists to
  exclude — `engine/corpus.py:33-34`: *"Signals the transcript does not carry are
  reported as `None` or an empty list — never as a fabricated value."* If a later
  layer gains an identity instrument, it emits `Evidence` with `links` in the same
  typed shape, and the graph carries them without change.
- **No edit decisions.** Nothing here says what to clip, what to keep, or whether
  a conclusion is worth acting on. That is the editorial layer, and
  `engine/memory/evidence.py` is explicit that an `Evidence` "is the input to that
  weighing", not the judgement.
- **No repair.** Everything found is reported or excluded, never rewritten. The
  one exception — a geometry contradiction is *kept and flagged* under
  `strict=False` — keeps the assertion visible rather than deleting a fact about
  what an instrument claimed.

---

## Required foundation changes — status

Only #1 was a schema change; #2 and #3 were one-line-scale; #4 was a measured
perf fix.

| # | Change | Status |
|---|---|---|
| 1 | Per-edge relation labels (`EvidenceLink`) | **DONE** — schema 1.1, `evidence.py:135-167` |
| 2 | `Interval.relation()` eps-tolerant at both boundaries | **DONE** — `intervals.py:187-226`, tests at `:208-210` |
| 3 | `covers()` instant fallback | **DONE** — `intervals.py:387-393` |
| 4 | `EvidenceSet.intervals` one normalise pass | **DONE** — `evidence.py:334-341`, measured ~2700× |
| 5 | `for_modality` `is` → `==` | **DONE** — `evidence.py:345-351` |
| 6 | `Evidence.from_dict` null tolerance + 1.0 migration | **DONE** — `evidence.py:267-294` |
| 7 | `IntervalSet.overlaps` accepts a set (new, post-review) | **DONE** — `intervals.py:351-364`, helper at `:516` |

The foundation is ready. Every precondition §2 states is met in the tree, and the
geometry check — the part that was gated on D2 — is now buildable.

---

## Test surface

`tests/test_memory_graph.py`, failure-first, each test naming the hazard it guards
in its docstring — the convention already in `tests/test_memory_intervals.py`,
`tests/test_memory_evidence.py`, `tests/test_memory_ingest.py` and
`tests/test_memory_store.py`.

**Edges and the converse**

- `test_all_thirteen_relations_are_addressable_from_neighbours` — *hazard: a
  relation the graph cannot traverse silently makes that link invisible, and the
  reader never learns the chain had a leg.*
- `test_two_links_keep_both_labels` — *hazard: the D1 regression returns — a node
  with two edges and one label, and a typed query answers wrong for half of it.*
- `test_a_one_directional_edge_yields_a_derived_converse` — *hazard: the converse
  is stored as a second declaration and the two drift apart.*
- `test_an_agreeing_mutual_pair_collapses_to_one_declared_edge` — *hazard:
  `edges()` double-counts every reciprocal link and the graph looks twice as
  connected as it is.*
- `test_a_disagreeing_mutual_pair_is_excluded_both_ways_and_reported` — *hazard:
  traversal follows a contested edge and presents one instrument's claim as
  settled.*

**Integrity**

- `test_dangling_edge_raises_under_strict_and_reports_under_permissive` — *hazard:
  a `restrict` orphan is traversed as though the target still exists, and a trace
  ends at an item that is no longer in the world.*
- `test_self_link_is_reported` — *hazard: an item is its own support and the trace
  terminates at itself, reading as grounded.*
- `test_non_allen_relation_is_refused` — *hazard: an unmatchable label sits in the
  graph and a typed query silently returns nothing.*
- `test_duplicate_ids_are_reported_and_first_wins` — *hazard: two items share an id
  and `neighbours` answers for whichever one happens to be first, nondeterministically
  across set construction order.*

**Geometry**

- `test_declared_before_on_overlapping_spans_is_a_contradiction` — *hazard: the
  geometry check is decorative and a link that the spans contradict is presented as
  supported.*
- `test_interleaved_sets_with_one_real_disjoint_pair_support_declared_before` —
  *hazard: a bounds test fails a relation that genuinely holds, and every
  multi-span item becomes untraceable.*
- `test_sub_eps_gap_realises_meets_not_before` — *hazard: D2 regresses and every
  near-touching pair reports as a contradiction, drowning the real findings.*
- `test_meets_satisfies_declared_before` — *hazard: the fine classes are too fine,
  and an instrument that got the ordering right is reported as wrong.*

**Cycles**

- `test_a_three_cycle_of_before_is_reported` — *hazard: a temporal impossibility
  is traversed as a support chain, and a conclusion is traced back to itself.*
- `test_mutual_equals_is_not_reported_as_a_cycle` — *hazard: redundancy is treated
  as contradiction and a legitimate reciprocal link is excluded.*

**Trace**

- `test_a_three_deep_chain_terminates_grounded` — *hazard: a chain stops short of
  the observation and reads as though an inference had no support.*
- `test_trace_stops_at_the_first_grounded_item` — *hazard: the walk continues past
  the observation and the trace carries support for the support, burying the
  answer.*
- `test_an_inference_with_no_supporting_edges_is_unresolved` — *hazard: an
  ungrounded inference with no links is presented as an error, or worse as grounded,
  instead of as the actionable thing it is.*
- `test_a_trace_to_a_missing_target_is_dangling` — *hazard: the chain ends
  silently and the editor cannot tell "no support" from "support was removed".*
- `test_trace_crosses_every_source_and_modality_on_the_chain` — *hazard: a
  conclusion resting on a `poor` transcript and one resting on a visual detector
  are indistinguishable on the trace.*

**Determinism and the store round trip**

- `test_the_graph_is_identical_in_a_fresh_interpreter` — *hazard: the persisted
  record and the rebuilt graph disagree, and traceability answers for a memory
  that no longer exists.*
- `test_supports_enumerates_what_rests_on_an_observation` — *hazard: a replaced
  instrument's inherited conclusions must each be re-discovered by failure.*
- `test_restrict_then_rebuild_reports_the_orphaned_edges` — *hazard: D3's orphans
  are invisible after a restriction and the clipped world reads as coherent.*

---

## Summary of the design's bets

1. **The graph is a read index, not a store.** It holds no data the `EvidenceSet`
   does not. A persisted graph would be a second copy of the relation structure
   that could drift from the evidence it describes; a read index cannot.
2. **Allen relations are the only edge semantics.** No `same_speaker`, no
   `supports_the_claim_of`. Identity is a grounding-layer concern, and a semantic
   edge with no instrument behind it is exactly the fabricated evidence this stack
   exists to exclude.
3. **The witness reading, not the bounds or universal reading.** A declared
   relation is supported if *some* member pair realises a compatible fine class —
   because that is what the declaration means. Bounds tests are wrong on
   interleaved sets; universal tests fail every multi-span item on one outlier.
4. **The precedence classes are O(1) on the sorted invariant.** Extremes tests
   replace member scans for `precedes`/`preceded_by`, and only intersecting sets
   fall back to a bounded scan. This is why the layer does not call
   `EvidenceSet.intervals` even after D6 fixed its cost.
5. **Integrity is checked at build, never per query.** A query that validated would
   make the report a function of when it was asked rather than of what the memory
   contains.
6. **Report and exclude, never repair.** The single exception — a geometry
   contradiction is kept and flagged under `strict=False` — exists because the
   *assertion* is still a fact about what an instrument claimed, and an editor
   investigating it needs it visible.
7. **`unresolved` is a feature.** An inference that asserts support and names none
   is the query's most useful failure, and its remedy is a harder rung on the
   store's ladder — not an exception, and not a silent `grounded=True`.
8. **The foundation was the work.** Seven changes were required before this layer
   could be built; all seven are in the tree, verified. The one that gated the
   geometry check — D2, `relation()` agreeing with the merger — is fixed, with the
   reasoning recorded in the docstring so it is not re-litigated.
