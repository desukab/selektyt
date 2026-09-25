"""Read an ``EditorialCorpus`` back from its on-disk JSON form.

``engine.corpus.build_corpus`` is the only writer; this module is the only
reader, and it deliberately never calls the builder. Re-running the analysis in
order to "read" a file would make every round trip circular — the imported
corpus would be whatever the current analyser produces rather than what the file
says, and an analyser change would silently rewrite history. The discipline is
the one the NLE interchange layer already uses (``tools/nle_check.py`` reads
FCPXML with a parser that never emits it): reader and writer are independent
code paths, so a byte that survives the trip can only have come from the file.

Tolerant of the schema the file was written with — a ``1.0`` corpus is upgraded
in place — and intolerant of anything that makes an index ambiguous. Every
artifact in the corpus is a *pointer into* the unit list: a missing analysis is
an empty list, but an index that resolves to no unit is an error, not a
degraded answer, because there is no honest way to report what such an artifact
means.
"""

from __future__ import annotations

import json
import math
import types
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from engine.corpus import SCHEMA_VERSION, EditorialCorpus

_SUPPORTED_SCHEMAS = ("1.0", "1.1")
# Two timecodes that differ by less than this are the same cut. The builder
# rounds to source-sample precision; a reader that treated 8.316666 and 8.317
# as disagreeing would reject every file it was given.
_TOL = 1e-3


class CorpusReadError(ValueError):
    """A corpus file cannot be reconstructed as the structure it claims to be."""


# ── generic reconstruction ─────────────────────────────────────────────────


_HINTS: dict[type, dict[str, Any]] = {}


def _hints(dataclass: type) -> dict[str, Any]:
    """Resolved field annotations, cached per dataclass.

    The modules use ``from __future__ import annotations``, so a field's declared
    type is a string until it is resolved against the module globals — and
    resolution is what turns ``"list[SetupPayoffLink]"`` into a type this code
    can dispatch on.
    """
    hints = _HINTS.get(dataclass)
    if hints is None:
        hints = get_type_hints(dataclass)
        _HINTS[dataclass] = hints
    return hints


def _is_union(ann: Any) -> bool:
    return get_origin(ann) in (type(None), types.UnionType) and \
        get_origin(ann) is not None and hasattr(ann, "__args__")


def _scalar(ann: Any, v: Any, ctx: str) -> Any:
    """Coerce one JSON value to the declared primitive, or refuse it.

    JSON has one number type, so an ``int`` field may legitimately arrive as
    ``2.0`` and a ``float`` as ``2``; both are accepted. ``true`` arriving for an
    ``int`` field is refused, because in Python the two are indistinguishable by
    isinstance and the file is simply not what the schema describes.
    """
    if ann is Any or ann is None:
        return v
    if ann is float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise CorpusReadError(f"{ctx}: expected a number, got {type(v).__name__}")
        return float(v)
    if ann is int:
        if isinstance(v, bool):
            raise CorpusReadError(f"{ctx}: expected an integer, got boolean")
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        raise CorpusReadError(f"{ctx}: expected an integer, got {v!r}")
    if ann is str:
        if not isinstance(v, str):
            raise CorpusReadError(f"{ctx}: expected a string, got {type(v).__name__}")
        return v
    if ann is bool:
        if not isinstance(v, bool):
            raise CorpusReadError(f"{ctx}: expected a boolean, got {type(v).__name__}")
        return v
    # An annotation this reader does not model (a bare ``object``, or a type
    # introduced after this code was written). Passing the value through keeps
    # a newer file readable rather than refusing it at the wrong layer; the
    # spine validation below is what actually protects the invariants.
    return v


def _convert(ann: Any, v: Any, ctx: str) -> Any:
    """Rebuild one annotated value from its JSON form."""
    if v is None:
        return None
    if _is_union(ann):
        # Every union in the corpus is ``X | None``; the caller has already
        # handled ``None``, so the remaining argument is the real type.
        args = [a for a in get_args(ann) if a is not type(None)]
        ann = args[0] if args else Any
    origin = get_origin(ann)
    if origin is list:
        if not isinstance(v, list):
            raise CorpusReadError(f"{ctx}: expected a list, got {type(v).__name__}")
        args = get_args(ann)
        if not args:
            return v
        return [_convert(args[0], x, f"{ctx}[{i}]") for i, x in enumerate(v)]
    if origin is dict:
        if not isinstance(v, dict):
            raise CorpusReadError(f"{ctx}: expected an object, got {type(v).__name__}")
        args = get_args(ann)
        if not args:
            return v
        kt, vt = args
        out = {}
        for k, x in v.items():
            out[str(k)] = _scalar(vt, x, f"{ctx}[{k!r}]") \
                if kt is str else _convert(vt, x, f"{ctx}[{k!r}]")
        return out
    if isinstance(ann, type) and is_dataclass(ann):
        return _from_dict(ann, v, ctx)
    return _scalar(ann, v, ctx)


def _from_dict(dataclass: type, d: Any, ctx: str) -> Any:
    """Rebuild one corpus dataclass from a dict, applying its own defaults.

    Fields the file omits take the dataclass default, which is how a missing
    analysis stays an empty list rather than an error; fields the file carries
    that the dataclass no longer has are dropped, which is how a file written by
    a newer SeleKyT stays readable by this one for everything this version
    understands.
    """
    if not isinstance(d, dict):
        raise CorpusReadError(
            f"{ctx}: expected an object, got {type(d).__name__}")
    hints = _hints(dataclass)
    kw = {}
    for f in fields(dataclass):
        if f.name in d:
            kw[f.name] = _convert(hints.get(f.name, f.type), d[f.name],
                                  f"{ctx}.{f.name}")
        elif f.default is not MISSING:
            kw[f.name] = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            kw[f.name] = f.default_factory()  # type: ignore[misc]
        else:
            raise CorpusReadError(
                f"{ctx}: the object has no {f.name!r}, which this schema "
                f"declares required")
    try:
        return dataclass(**kw)
    except (TypeError, ValueError) as exc:
        raise CorpusReadError(f"{ctx}: cannot be rebuilt ({exc})") from exc


# ── spine validation ───────────────────────────────────────────────────────


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise CorpusReadError(msg)


def _index(v: Any, n: int, ctx: str) -> None:
    _require(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n,
             f"{ctx}: index {v!r} does not resolve to one of {n} unit(s)")


def _finite(v: Any, ctx: str) -> None:
    _require(isinstance(v, (int, float)) and not isinstance(v, bool)
             and not math.isnan(v) and v != float("inf") and v != float("-inf"),
             f"{ctx}: {v!r} is not a finite number")


def _span(start: float, end: float, ctx: str) -> None:
    _finite(start, f"{ctx}.start")
    _finite(end, f"{ctx}.end")
    _require(end >= start - _TOL,
             f"{ctx}: ends at {end} before it starts at {start}")


def _derive_unit_threads(corpus: EditorialCorpus) -> list[list[int]]:
    """Every thread a unit belongs to, straight from the threads' own membership.

    Thread membership is defined by ``StoryThread.unit_indices``; the flat index
    is a convenience for the story view. Deriving rather than trusting the key
    means a file whose two halves disagree is resolved in favour of the stories
    it actually declares.
    """
    out: list[list[int]] = [[] for _ in corpus.units]
    for t in corpus.threads:
        for u in t.unit_indices:
            if 0 <= u < len(out):
                out[u].append(t.thread_id)
    return out


def _validate(corpus: EditorialCorpus, where: str) -> None:
    """Check that every artifact in the corpus resolves to something real.

    Called after reconstruction, so a failure names the exact artifact that
    cannot be believed. Anything that would make the corpus *useless* rather
    than merely thin raises here; a thin corpus (few or no analyses) is valid.
    """
    n = len(corpus.units)
    _require(n >= 0, f"{where}: a corpus cannot hold a negative number of units")
    if n == 0:
        # An empty corpus is a legal result of analysing an empty transcript.
        # Everything else must be empty with it, or the file is self-contradictory.
        for name in ("topics", "threads", "beats", "links", "redundancy_groups",
                     "speaker_blocks", "broll"):
            _require(not getattr(corpus, name),
                     f"{where}: declares no units but holds {name}")
        return

    _require(corpus.build.unit_count == n,
             f"{where}: build claims {corpus.build.unit_count} unit(s), the spine "
             f"holds {n}")
    for i, u in enumerate(corpus.units):
        _require(u.index == i,
                 f"{where}: unit {i} declares index {u.index}; the spine is "
                 f"position-addressable, so an index that is not its position "
                 f"makes every pointer in the file ambiguous")
        _span(u.start, u.end, f"unit[{i}]")
    n_seg = corpus.build.segment_count
    n_top = len(corpus.topics)
    n_thr = len(corpus.threads)
    n_beat = len(corpus.beats)
    n_grp = len(corpus.redundancy_groups)
    n_blk = len(corpus.speaker_blocks)

    for i, u in enumerate(corpus.units):
        ctx = f"unit[{i}]"
        for s in u.segment_indices:
            _index(s, max(n_seg, 1), f"{ctx}.segment_indices")
        if u.topic_id >= 0:
            _index(u.topic_id, n_top, f"{ctx}.topic_id")
        if u.thread_id is not None:
            _index(u.thread_id, n_thr, f"{ctx}.thread_id")
        if u.beat_id is not None:
            _index(u.beat_id, n_beat, f"{ctx}.beat_id")
        if u.redundancy_group_id >= 0:
            _index(u.redundancy_group_id, n_grp, f"{ctx}.redundancy_group_id")
        if u.speaker_block_id >= 0:
            _index(u.speaker_block_id, n_blk, f"{ctx}.speaker_block_id")

    for t in corpus.topics:
        ctx = f"topic[{t.topic_id}]"
        _span(t.start, t.end, ctx)
        for u in t.unit_indices:
            _index(u, n, f"{ctx}.unit_indices")
        for s in t.segment_indices:
            _index(s, max(n_seg, 1), f"{ctx}.segment_indices")

    for t in corpus.threads:
        ctx = f"thread[{t.thread_id}]"
        _span(t.start, t.end, ctx)
        for u in t.unit_indices:
            _index(u, n, f"{ctx}.unit_indices")
        for b in t.beat_indices:
            _index(b, n_beat, f"{ctx}.beat_indices")
        for lk in t.links:
            _require(lk.setup_unit < lk.payoff_unit,
                     f"{ctx}: a link's setup ({lk.setup_unit}) must precede its "
                     f"payoff ({lk.payoff_unit})")

    for b in corpus.beats:
        _index(b.unit_index, n, f"beat[{b.unit_index}]")
        _span(b.start, b.end, f"beat[{b.unit_index}]")
        if b.thread_id is not None:
            _index(b.thread_id, n_thr, f"beat[{b.unit_index}].thread_id")

    for i, lk in enumerate(corpus.links):
        ctx = f"link[{i}]"
        _index(lk.setup_unit, n, f"{ctx}.setup_unit")
        _index(lk.payoff_unit, n, f"{ctx}.payoff_unit")
        _require(lk.setup_unit < lk.payoff_unit,
                 f"{ctx}: a setup must precede its payoff")
        _span(lk.setup_start, lk.setup_end, f"{ctx}.setup")
        _span(lk.payoff_start, lk.payoff_end, f"{ctx}.payoff")

    for g in corpus.redundancy_groups:
        ctx = f"redundancy_group[{g.group_id}]"
        _span(g.start, g.end, ctx)
        for u in g.members:
            _index(u, n, f"{ctx}.members")
        _index(g.representative, n, f"{ctx}.representative")
        _require(g.representative in g.members,
                 f"{ctx}: the recommended member {g.representative} is not one "
                 f"of the group's members {g.members}")

    for blk in corpus.speaker_blocks:
        ctx = f"speaker_block[{blk.block_id}]"
        _span(blk.start, blk.end, ctx)
        for u in blk.unit_indices:
            _index(u, n, f"{ctx}.unit_indices")

    for m in corpus.broll:
        _span(m.start, m.end, f"broll[{m.unit_index}]")
        _index(m.unit_index, n, f"broll[{m.unit_index}].unit_index")

    if corpus.rhythm.available:
        _require(len(corpus.rhythm.per_unit) == n,
                 f"{where}: rhythm reports {len(corpus.rhythm.per_unit)} "
                 f"per-unit profile(s) for a {n}-unit corpus")

    ut = corpus.unit_threads
    _require(len(ut) == n,
             f"{where}: unit_threads holds {len(ut)} entries for {n} unit(s)")
    for i, ids in enumerate(ut):
        for tid in ids:
            _index(tid, n_thr, f"unit_threads[{i}]")


# ── the reader ─────────────────────────────────────────────────────────────


def read_corpus(src: Path | str | dict[str, Any]) -> EditorialCorpus:
    """Reconstruct an ``EditorialCorpus`` from a path, a JSON string, or a dict.

    The inverse of ``EditorialCorpus.to_dict``: ``read_corpus(c.to_dict())``
    recovers the corpus, and so does reading the file ``build_corpus``'s caller
    wrote. Never calls the analyser — see the module docstring for why that
    would make the round trip circular.

    Raises:
        CorpusReadError: the input is not JSON, is not a corpus, declares an
            unknown schema, or holds an index that resolves to nothing.
    """
    where: str
    d: Any
    if isinstance(src, dict):
        d, where = src, "<dict>"
    else:
        path: Path | None = None
        if isinstance(src, Path):
            path = src
        elif isinstance(src, str):
            # A string is a path when one exists there, otherwise it is a JSON
            # document; a caller that wants no ambiguity passes a Path. Probing
            # can itself fail — a JSON document is a valid but absurdly long
            # filename, and a path fragment under a non-directory raises — and
            # neither of those means the caller passed a bad path.
            candidate = Path(src)
            try:
                path = candidate if candidate.is_file() else None
            except OSError:
                path = None
        if path is None:
            where = "<json string>"
            text: Any = src
        else:
            where = str(path)
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise CorpusReadError(f"cannot read {where}: {exc}") from exc
        try:
            d = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CorpusReadError(f"{where} is not valid JSON ({exc})") from exc

    if not isinstance(d, dict):
        raise CorpusReadError(
            f"{where}: a corpus is a JSON object, got {type(d).__name__}")

    build = d.get("build")
    declared = build.get("schema") if isinstance(build, dict) else None
    if declared is None:
        raise CorpusReadError(
            f"{where}: no schema version is recorded in build.schema, so the "
            f"file's structure cannot be interpreted")
    if declared not in _SUPPORTED_SCHEMAS:
        raise CorpusReadError(
            f"{where}: declares schema {declared!r}; this SeleKyT reads "
            f"{', '.join(_SUPPORTED_SCHEMAS)} and will not guess at a "
            f"structure it does not know")

    corpus = _from_dict(EditorialCorpus, d, where)
    corpus.build.schema = SCHEMA_VERSION

    if "unit_threads" in d:
        ut = corpus.unit_threads
    else:
        # Pre-1.1 files have no thread-membership index. It is derived from the
        # threads rather than from ``unit.thread_id``, because the threads are
        # the definition of a story and the unit field is one primary thread.
        ut = _derive_unit_threads(corpus)
        corpus.notes.append(
            f"read from schema {declared}: per-unit thread membership was "
            f"absent and has been derived from thread.unit_indices")
    corpus.unit_threads = ut

    _validate(corpus, where)
    return corpus


__all__ = ["SCHEMA_VERSION", "CorpusReadError", "read_corpus"]
