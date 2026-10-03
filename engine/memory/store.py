"""``VideoMemory`` — the persistent, queryable world for one source.

Why this module exists
----------------------

``engine.memory.evidence`` defines what a claim *is*. It does not answer the two
questions an editor asks over an hour-scale source that a bare list of evidence
cannot:

1. **How much of this source have I actually examined?** A memory built at
   28 samples per window over a 47-minute video has looked hard at perhaps four
   minutes of it, and a conclusion phrased as "the video never returns to the
   opening topic" is only supportable if the memory can say which spans were
   *not* examined at the level that would have seen a return. An absence claim
   needs a coverage record, not just a confidence.

2. **How hard did I look, and where must I look harder?** The Level 0..5
   hierarchy exists because hour-scale video is a search problem, and a search
   that runs level-5 fine inspection over all 47 minutes to answer a level-1
   question is a budget error, not an accuracy win. The store does not escalate
   computation — it has none. It records, per item, the level its source
   instrument belongs to, so that "these spans are covered at level 2 but have
   no level-4 inspection" is a query result and a work order rather than an
   unanswerable question.

What the store is *not*: it does not perceive anything. It has no decoder, no
model and no notion of video. ``engine.memory.ingest`` is the layer that turns
pipeline outputs into ``Evidence``; this layer keeps them, versions them, and
reports honestly on what they cover and what they are missing. Keeping capture
out of the store is what lets the same store back a live run and a re-loaded
one, and it is what makes "recompute this span at level 4" a command to the
ingest layer rather than a shadow pipeline inside the store.

Levels
------

The level ladder is the brief's, not invented here:

    0  source metadata      duration, dimensions, fps, fingerprint, transcript
    1  coarse temporal      speech regions, pauses, word times, speaker turns
    2  shots / scenes       shot boundaries, scene classes, slides, transitions
    3  semantic candidates  topics, threads, beats, b-roll marks, rankings
    4  fine inspection      per-window face tracks, crop plans, subject centres
    5  precise grounding    span-level verification against raw frames/audio

"Only escalate computation when evidence requires it" is the whole point, and
the requirement flows downhill into one design rule: the store never *claims*
coverage it did not record. ``level_of`` maps a source instrument to a level and
falls back to GROUND for an instrument it does not recognise, so an unknown
detector inflates nothing — it is reported as needing a level-5 pass, which is
the conservative and defensible reading of "we do not know how hard we looked".

Validation reports; it does not repair
--------------------------------------

``validate`` returns a list of findings rather than raising, and every finding
names the item. The three failure modes it covers are recorded in
``docs/memory-failure-modes.md`` and are worth restating because each one is a
way an earlier, simpler store would have been silently wrong:

- **#17** — evidence with zero supporting spans, confidence 1.0 and
  ``grounded=True`` is indistinguishable from a detection that returned nothing.
  The fix is provenance, not a clamp: a zero-cardinality item must carry a
  rationale that says what it looked for and did not find.
- **#18** — nothing bounds-checks evidence against source duration. Evidence
  outside ``[0, duration]` is not clamped here; the span is not necessarily
  wrong (a coarser instrument may report past the end), but an editor must be
  told rather than trust it.
- **#20** — ``EvidenceSet.union`` permits duplicate ids. In-memory sets are
  caller's risk; a *persisted* memory with duplicate ids cannot be addressed, so
  this is the layer that reports it.

Purity and determinism
----------------------

Values are pure. ``record`` is batched and never re-appends item-by-item, so
recording ten thousand items is ten thousand copies and not a quadratic chain.
Persistence is deterministic: ``save`` writes ``json.dumps(sort_keys=True)``
with no wall clock, no absolute paths and no machine-specific separators, so a
memory written on the editor's desktop and read back on a render node is
byte-identical input to byte-identical output. ``load`` is the one impure
operation and it is deliberate — it raises ``StaleMemory`` or
``IncompatibleSchema`` rather than migrating a record that might no longer
describe the file on disk, because a memory that was silently repaired is a
memory the editor cannot check against the source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from engine.memory.evidence import Evidence, EvidenceSet, Modality, Source
from engine.memory.intervals import Interval, IntervalSet

SCHEMA_VERSION = "1.1"


class Level(IntEnum):
    """The rungs of the perception ladder, as the brief defines them.

    An ``IntEnum`` so the ordinal *is* the escalation order — comparing levels
    is a comparison of how hard we looked, and that ordering has to be total for
    ``escalate_needed`` to mean anything. Levels are not quality tiers: level 1
    speech regions from a good VAD are better evidence than level 4 face tracks
    from a detector that saw the wrong thing, and ``confidence`` carries that.
    """

    SOURCE = 0       # source metadata: duration, dimensions, fps, fingerprint
    COARSE = 1       # speech regions, pauses, word times, speaker turns
    SCENES = 2       # shot boundaries, scene classes, slides, transitions
    CANDIDATES = 3   # topics, threads, beats, b-roll marks
    INSPECT = 4      # per-window face tracks, crop plans, subject centres
    GROUND = 5       # span-level verification against raw frames / audio


# The instrument → level table. Keyed by "module.method" where a method's level
# is not implied by its module, and by module prefix elsewhere.
#
# ``engine.reframe`` is the one module that spans levels and so needs per-method
# keys: ``plan_crop`` and ``track_subject_center`` read specific windows closely
# (level 4) while the filtergraph builders in the same file read nothing at all.
# Everything else is homogeneous enough that the module decides.
#
# An instrument absent from this table falls back to GROUND — see the module
# docstring for why "we do not know how hard we looked" is recorded as the
# hardest looking rather than the easiest.
_INSTRUMENT_LEVEL: dict[str, Level] = {
    # ── level 1: coarse temporal sampling ──────────────────────────────────
    "engine.vad": Level.COARSE,
    "engine.coverage": Level.COARSE,
    "engine.rhythm": Level.COARSE,
    "engine.lexical": Level.COARSE,
    # ── level 2: shots and scenes ───────────────────────────────────────────
    "engine.reframe_scenes.analyse_scene": Level.SCENES,
    "engine.reframe_scenes.detect_slides": Level.SCENES,
    "engine.reframe_scenes.classify_scene": Level.SCENES,
    "engine.reframe_scenes.find_shot_boundaries": Level.SCENES,
    "engine.reframe_perception.find_shot_boundaries": Level.SCENES,
    # ── level 3: semantic candidates ────────────────────────────────────────
    "engine.topics": Level.CANDIDATES,
    "engine.threads": Level.CANDIDATES,
    "engine.corpus": Level.CANDIDATES,
    "engine.pause_classifier": Level.CANDIDATES,
    # ── level 4: fine visual/audio inspection ───────────────────────────────
    "engine.reframe.plan_crop": Level.INSPECT,
    "engine.reframe.track_subject_center": Level.INSPECT,
    "engine.audio_analysis": Level.INSPECT,
    "engine.audio_fusion": Level.INSPECT,
    # ── level 5: precise evidence grounding ─────────────────────────────────
    "engine.memory.ground": Level.GROUND,
    "engine.memory.ingest": Level.GROUND,
    "engine.ground.sample_window": Level.GROUND,
}


@dataclass(frozen=True)
class SourceMeta:
    """Level 0: what the source *is*, independent of anything perceived from it.

    ``fingerprint`` is the digest of the media file itself — the identity the
    memory is bound to. ``transcript_fingerprint`` is separate and optional
    because a transcript is a *different* input with a different update cadence:
    a re-transcribed source invalidates every TEXT item in memory while leaving
    the VISUAL items perfectly good, and a store that keyed everything on the
    video fingerprint would either discard good evidence or keep stale text.
    """

    path: str
    duration: float
    fingerprint: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    transcript_fingerprint: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": self.duration,
            "fingerprint": self.fingerprint,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "transcript_fingerprint": self.transcript_fingerprint,
        }

    @staticmethod
    def from_dict(d: dict) -> SourceMeta:
        return SourceMeta(
            path=d.get("path") or "",
            duration=float(d.get("duration", 0.0) or 0.0),
            fingerprint=d.get("fingerprint") or "",
            width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0),
            fps=float(d.get("fps", 0.0) or 0.0),
            transcript_fingerprint=d.get("transcript_fingerprint") or "",
        )


@dataclass(frozen=True)
class LevelRecord:
    """What one rung of the ladder reached, over this source.

    ``coverage`` is an ``IntervalSet``, not a ratio, because "covered 62%" and
    "covered 06:00–18:00 and 20:00–31:00" are different facts — the second one
    is what an escalation decision needs, and a ratio cannot be turned back into
    it. ``items`` is a count, not a list: the items themselves live in
    ``VideoMemory.items`` and are queryable by level, so the record is a summary
    rather than a second copy of the same data.
    """

    level: Level
    coverage: IntervalSet
    items: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "level": int(self.level),
            "coverage": self.coverage.to_dict(),
            "items": self.items,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(d: dict) -> LevelRecord:
        return LevelRecord(
            level=Level(int(d.get("level", 0))),
            coverage=IntervalSet.from_dict(d.get("coverage") or {}),
            items=int(d.get("items", 0) or 0),
            notes=d.get("notes", ""),
        )


@dataclass(frozen=True)
class Finding:
    """One validation result: a severity, a message, and the id it names.

    Ids, not indices: a finding that names an index is a finding that goes stale
    the moment an item is added before it. The id is stable for the life of the
    memory.
    """

    severity: str   # "error" | "warning"
    item_id: str
    message: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "item_id": self.item_id,
            "message": self.message,
        }


class StaleMemory(RuntimeError):
    """The memory's source fingerprint does not match the file it names.

    Raised rather than repaired: a memory that describes a different cut of the
    video has timestamps that are wrong in a way no migration can undo, and
    rebuilding it is a decision the caller must make with the fact in front of
    them. The caller may decide the re-encode is close enough — but that is
    *their* judgement, and a store that absorbed it silently has taken the
    editor's ability to check the evidence away.
    """


class IncompatibleSchema(RuntimeError):
    """The record's schema version is not one this code can read.

    Distinct from ``StaleMemory``: a future schema is a version problem with a
    migration path, a stale fingerprint is a content problem without one.
    Conflating them would mean an editor seeing "incompatible" for a re-encoded
    file and learning nothing about which kind of trouble they have.
    """


@dataclass
class VideoMemory:
    """The queryable, versioned record of everything perceived from one source.

    ``items`` is a tuple, not a list: the memory is written to disk and read
    back, and a caller who appends to a list they hold a reference to while the
    store has already serialised it produces a memory on disk that disagrees
    with the object in flight. ``record`` returns a new memory, so mutation is
    visible at the call site rather than happening through an alias.
    """

    meta: SourceMeta
    items: tuple[Evidence, ...] = ()
    levels: tuple[LevelRecord, ...] = ()

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def empty(cls, meta: SourceMeta) -> VideoMemory:
        """A memory with no evidence, at level 0 only.

        Level 0 is always covered by construction — the source metadata *is*
        level 0 — so an empty memory still has a complete lowest rung. A store
        that started with no levels at all would report the source as totally
        unexamined, which is not true and would trigger a pointless escalation.
        """
        return cls(
            meta=meta,
            items=(),
            levels=(LevelRecord(Level.SOURCE, IntervalSet.of((0.0, meta.duration))),),
        )

    # ── level resolution ───────────────────────────────────────────────────

    def level_of(self, item: Evidence) -> Level:
        """The ladder rung an instrument belongs to.

        Looked up by exact ``module.method`` first, then by module prefix, then
        falls back to GROUND. The fallback is the load-bearing case: an
        instrument the table does not know is one this code has not been told
        the resolution of, and the honest recording of that is "looked at the
        hardest level we have", because that is the assumption under which no
        escalation is skipped.
        """
        src = item.source
        exact = f"{src.module}.{src.method}"
        if exact in _INSTRUMENT_LEVEL:
            return _INSTRUMENT_LEVEL[exact]
        if src.module in _INSTRUMENT_LEVEL:
            return _INSTRUMENT_LEVEL[src.module]
        return Level.GROUND

    # ── query ──────────────────────────────────────────────────────────────

    def evidence(self) -> EvidenceSet:
        """Every item, as an ``EvidenceSet`` — the retrieval layer's entry point.
        Building this is one copy, and it is the copy that makes the store's
        query algebra (``in_window``, ``for_modality``, ``above_confidence``)
        available without the caller re-wrapping the items."""
        return EvidenceSet(list(self.items))

    def at_level(self, level: Level) -> EvidenceSet:
        """Items produced by instruments at exactly ``level``."""
        return EvidenceSet([i for i in self.items if self.level_of(i) == level])

    def at_or_above(self, level: Level) -> EvidenceSet:
        """Items at ``level`` or any harder rung.

        The complement query an escalation pass asks: "what do I already have
        that is at least this fine?", so a planned level-4 pass can skip spans
        where a level-5 verification already exists.
        """
        return EvidenceSet([i for i in self.items if self.level_of(i) >= level])

    def coverage(self, level: Level) -> IntervalSet:
        """Where the memory has evidence at exactly ``level``.

        Read from the items themselves rather than from ``levels``: ``levels``
        is a summary written by whichever process last saved the memory, and a
        caller who has ``record``ed since the last ``save`` must see the new
        coverage. Deriving it keeps the two from drifting; the stored records
        exist for the on-disk reader who cannot derive anything.
        """
        spans = [s for i in self.items if self.level_of(i) == level for s in i.intervals]
        return IntervalSet(spans)

    def escalate_needed(self, level: Level) -> IntervalSet:
        """Spans with no evidence at ``level`` or above — the escalation work order.

        The complement of ``at_or_above(level).intervals`` against the whole
        source, and the reason the ladder exists at all: this is the set a
        planner asks "do I need to run level 4 over?" and the answer is a set of
        intervals, not a fraction, because a fraction cannot tell the planner
        *where*.
        """
        have = self.at_or_above(level).intervals
        return IntervalSet([Interval(0.0, self.meta.duration)]).subtract(have)

    # ── mutation ───────────────────────────────────────────────────────────

    def record(self, *items: Evidence) -> VideoMemory:
        """Return a memory that also carries ``items``, with ids assigned.

        Ids are assigned here and nowhere else, because the store is the layer
        that persists them and a persisted id must be stable for the life of the
        record. A caller-supplied id is respected — an ingest adapter that has
        already given an item a durable name keeps it — and an id is only
        generated for an item that arrived without one.

        The whole batch is copied once rather than folded item by item: folding
        ``items + (new,)`` per record is quadratic in the batch size, and at
        per-word granularity a single ``record`` call can carry thousands.

        A caller-supplied id that duplicates one already present is kept, not
        renamed and not dropped: renaming would silently change the caller's
        reference to the item, and dropping would discard evidence over a naming
        mistake. ``validate`` reports the collision, so a memory with two items
        under one id is *visible* rather than resolved in whichever way the store
        guessed. A query for that id returns the first one, which is the
        documented resolution and the reason the finding names it.
        """
        if not items:
            return self
        existing = {i.id for i in self.items if i.id}
        # The counter starts past the highest numeric id already taken, caller-
        # supplied or generated, so a generated id cannot collide with a
        # caller's later ``record`` call and the two id spellings interleave
        # instead of fighting over the same numbers.
        highest = 0
        for i in self.items:
            if len(i.id) == 5 and i.id[0] == "e" and i.id[1:].isdigit():
                highest = max(highest, int(i.id[1:]))
        next_id = highest + 1
        with_ids: list[Evidence] = []
        for item in items:
            if item.id:
                existing.add(item.id)
                with_ids.append(item)
                if len(item.id) == 5 and item.id[0] == "e" and item.id[1:].isdigit():
                    highest = max(highest, int(item.id[1:]))
                    next_id = highest + 1
                continue
            while f"e{next_id:04d}" in existing:
                next_id += 1
            assigned = f"e{next_id:04d}"
            existing.add(assigned)
            with_ids.append(item.with_id(assigned))
            next_id += 1
        if not with_ids:
            return self
        return VideoMemory(
            meta=self.meta,
            items=(*self.items, *with_ids),
            levels=self.levels,
        )

    def with_levels(self, *records: LevelRecord) -> VideoMemory:
        """Return a memory that also carries level-run records.

        Optional and separate from ``record``: an ingest pass that has just run
        a level-2 sweep over a span records *both* the evidence it produced and
        the rung it reached, but the rung record is the planner-facing summary
        and it is not derivable from the evidence (an empty sweep is still
        coverage — the span was examined and nothing was found).
        """
        if not records:
            return self
        by_level = {r.level: r for r in self.levels}
        for r in records:
            prior = by_level.get(r.level)
            by_level[r.level] = r if prior is None else LevelRecord(
                level=r.level,
                coverage=prior.coverage.union(r.coverage),
                items=prior.items + r.items,
                notes=r.notes or prior.notes,
            )
        return VideoMemory(
            meta=self.meta,
            items=self.items,
            levels=tuple(by_level[r] for r in sorted(by_level)),
        )

    # ── validation ─────────────────────────────────────────────────────────

    def validate(self) -> list[Finding]:
        """Report defects without repairing any of them.

        Every finding is an error or a warning and names the item id, so a
        caller can present "these 12 conclusions are not supported by any span"
        to the editor rather than having the store quietly drop them. See the
        module docstring for the three failure modes and why each is reported
        rather than fixed.
        """
        findings: list[Finding] = []
        seen: set[str] = set()
        bounds = Interval(0.0, self.meta.duration)
        for item in self.items:
            if item.id:
                if item.id in seen:
                    findings.append(Finding(
                        "error", item.id,
                        "duplicate id in the same memory; the first item with "
                        "this id is the one a query will return",
                    ))
                else:
                    seen.add(item.id)
            # #17: zero-cardinality evidence is only honest if it says what it
            # looked for. A full-confidence grounded claim with no supporting
            # span is indistinguishable from a detection that returned nothing.
            # A rationale turns the same shape into a legitimate absence claim —
            # "swept 0..100s for a slide; none detected" is a conclusion an
            # editor can check — so it is accepted rather than flagged.
            if item.cardinality == 0 and not item.rationale:
                if item.grounded and item.confidence >= 1.0:
                    findings.append(Finding(
                        "error", item.id,
                        "no supporting spans, grounded, full confidence and no "
                        "rationale — this is indistinguishable from a failed "
                        "detection (failure mode #17)",
                    ))
                else:
                    findings.append(Finding(
                        "warning", item.id,
                        "no supporting spans and no rationale; an absence claim "
                        "should say what was looked for and not found",
                    ))
            # #18: bounds are reported, not clamped. A span past the end is not
            # necessarily wrong — a coarse instrument can report past it — but
            # an editor trusting a timestamp outside the source is being misled.
            for span in item.intervals:
                if span.end > self.meta.duration + 1e-6 or span.start < -1e-6:
                    findings.append(Finding(
                        "warning", item.id,
                        f"span {span.start:g}..{span.end:g} lies outside the "
                        f"source 0..{self.meta.duration:g} (failure mode #18)",
                    ))
                    break
        return findings

    # ── serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "meta": self.meta.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "levels": [r.to_dict() for r in self.levels],
        }

    @staticmethod
    def from_dict(d: dict) -> VideoMemory:
        schema = d.get("schema", "")
        if schema != SCHEMA_VERSION:
            raise IncompatibleSchema(
                f"memory record has schema {schema!r}, this code reads "
                f"{SCHEMA_VERSION!r}; migrate the record explicitly rather than "
                f"reading a shape it may not have"
            )
        return VideoMemory(
            meta=SourceMeta.from_dict(d.get("meta") or {}),
            items=tuple(Evidence.from_dict(i) for i in d.get("items", [])),
            levels=tuple(LevelRecord.from_dict(r) for r in d.get("levels", [])),
        )

    def save(self, path: Path | str) -> Path:
        """Write the memory to ``path`` and return it.

        ``sort_keys=True`` and no wall clock: the same memory is the same bytes
        wherever and whenever it is written, which is what makes a committed
        memory file an auditable artifact — a diff of it is a diff of the
        evidence, not of when someone ran the tool.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(p)
        return p

    @staticmethod
    def load(path: Path | str, *, fingerprint: str | None = None) -> VideoMemory:
        """Read a memory from ``path``.

        The only impure operation here, and it is where the store is most
        conservative. ``fingerprint`` is the live digest of the media file the
        caller wants evidence about; when it is given and disagrees with the
        record, ``StaleMemory`` is raised instead of a migration, because the
        timestamps in the record describe a different file.

        A missing file raises ``FileNotFoundError`` rather than returning an
        empty memory: an empty memory claims level-0 coverage of a source, and
        returning one for a path that does not exist would have the store
        asserting something about a file it never opened.
        """
        p = Path(path)
        blob = p.read_text(encoding="utf-8")
        memory = VideoMemory.from_dict(json.loads(blob))
        if fingerprint is not None and memory.meta.fingerprint and \
                memory.meta.fingerprint != fingerprint:
            raise StaleMemory(
                f"memory at {p} was built from source fingerprint "
                f"{memory.meta.fingerprint[:12]}, the file now has "
                f"{fingerprint[:12]}; its timestamps may not describe this cut"
            )
        return memory

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)
