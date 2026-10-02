"""Retrieval: INDEX and RETRIEVE at hour scale.

Why this module exists
----------------------

The pipeline can already *cut* a long source, and ``engine.memory`` can already
hold what was perceived in it. What neither can do is answer an editor's
question: *where in this hour did they talk about the coolant pump?* The
selection layer ranks passages it already built; the memory layer records what
a detector saw. Between them there is no query path, so the moment an editor
stops browsing and starts asking, the whole index has to be re-derived by hand
or by re-decoding the source.

This module is that path — stages 1 and 2 of the INDEX -> RETRIEVE -> GROUND ->
VERIFY -> REASON order the design document commits to
(`docs/retrieval-design.md`). It indexes the one channel that is both structured
and reliably on disk — the word-timestamped transcript — and it answers queries
against that index without decoding a single frame. That ordering is the point:
ffmpeg decode is the only expensive thing on this host, and a retrieve call
costs ~0.1 ms against ~1,620 s of 720p decode, so the index exists to shrink the
decode set before any decode happens.

What it is not
--------------

It is not a semantic retriever. There is no embedding model on this host, no
``torch``, and the design is explicit that a keyword matcher honest about being
a keyword matcher is worth more here than a fuzzy matcher that implies
understanding it does not have (`engine.memory.evidence`, the ``EvidenceSet.about``
note). The query language is terms, optionally grouped into ordered phrases.
No boolean operators, no wildcard, no relevance feedback: anything more
expressive is a reasoning-stage concern, expressed against the evidence this
module returns.

It is not a grounder and never decodes. Retrieval stops at the union of its
post-NMS intervals; GROUND receives exactly that and expands nothing silently,
because an interval set that grew after the answer of record is an answer nobody
can check.

Honesty contract
----------------

The project's standing rule is that an absent signal is reported rather than
fabricated ("Signals the transcript does not carried are reported as ``None`` or
an empty list — never as a fabricated value", ``engine/corpus.py:33``; "An empty
answer is data", ``engine/memory/intervals.py``). This module inherits it:

* a query that matches nothing returns an empty interval set, an empty evidence
  tuple and a ``report`` naming which channels *are* queryable — never a nearest
  span, never an automatically loosened query;
* optional inputs (speech regions, editorial units) are recorded per input as
  ``loaded`` or not, and a channel that did not load is reported as absent
  rather than defaulted to empty, because "VAD is empty" and "VAD never ran" are
  two different runs of the same source;
* a character n-gram match — the high-WER fallback — is marked ``grounded=False``
  and capped in confidence, because it is a *hypothesis about what was probably
  said*, not an observation of what the transcript contains;
* the transcript's own language fitness verdict travels into every result's
  ``rationale``, so a retrieval over a 91.54%-WER transcript can never present
  itself with the confidence of one over an 11%-WER one.

Purity
------

The index is a pure function of its inputs: no wall clock, no network, no
random, no model. Two builds on the same transcript are byte-identical, which is
what makes "the index is stale" a testable predicate rather than a judgement
call. Scores are derived on read and never stored — a stored score would be a
stored opinion; the postings are the only stored fact.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Sequence

from engine.language_fitness import WER_BAR, FitnessReport
from engine.lexical import fingerprint, idf_weights, weighted_containment
from engine.memory.evidence import Evidence, Modality, Source
from engine.memory.intervals import MERGE_EPS, IntervalSet

SCHEMA_VERSION = "1.0"

# Named rather than buried in the scoring loop, because each one appears in
# every result's rationale and in the Source params digest, so a reader who
# checks a number against the code finds it in one place.
DEFAULT_K = 3
DEFAULT_NMS_S = 60.0
PHRASE_GAP_S = 0.5

# The character n-gram fallback's window. n = 3..5 over the normalised term: at
# 91.54% WER / 69.24% CER there is character-level signal where there is almost
# no word-level signal, and a 3-gram is small enough to survive a wrong script
# while a 5-gram is still discriminative. Below NGRAM_MIN the term is its own
# only n-gram, so a short term stays exactly retrievable rather than unmatched.
NGRAM_MIN = 3
NGRAM_MAX = 5

# Optional-channel re-ranking factors. A window inside a measured speech region
# or inside an editorial unit is a better editorial answer than the same lexical
# match in dead air, but the boost is *recorded in the rationale*, never folded
# silently into the score, and a channel that did not load contributes nothing.
SPEECH_REGION_BOOST = 0.25
TOPIC_UNIT_BOOST = 0.20

# An n-gram match can never outrank an exact word-level one, so its confidence
# is capped below the floor any exact match can reach. The ceiling is a property
# of the *kind* of match, not a tuning constant: the n-gram index is lossy by
# construction.
NGRAM_CONFIDENCE_CEILING = 0.5

# The idf formula ``engine.lexical.idf_weights`` uses is
# ``log((n_docs + 1) / (df + 1))``, which is exactly 0 for a term that occurs in
# every utterance — a saturation, not a judgement. At full coverage that zero
# propagates into ``weighted_containment``, where both the shared weight and the
# smaller bag's total become 0 and the function returns 0 for a *real* match,
# collapsing ranking and confidence alike on the short transcripts a test and a
# single-utterance query both produce. The floor says what is true: a term
# present everywhere in this source is common, but it is not evidence-free, and
# a window that matches it has scored something rather than nothing.
IDF_FLOOR = 0.1


def _scored_idf(index: RetrievalIndex) -> dict[str, float]:
    """The index's idf table with the saturation floor applied.

    Derived per retrieve call, never stored: a stored weight would be a stored
    opinion about a term's rarity, and the postings are the only stored fact.
    """
    return {term: max(weight, IDF_FLOOR)
            for term, weight in index.idf().items()}


class StaleIndexError(RuntimeError):
    """The index on disk was built for a different source than the one asked about.

    Raised rather than rebuilt here: the caller owns the transcript and the
    media, so it is the caller that can decide whether to rebuild or to stop.
    Rebuilding silently would answer a question about the wrong video and write
    the answer into a report a user reads as authoritative.
    """


# ── terms ────────────────────────────────────────────────────────────────────


def _term(text: str) -> str:
    """The stored/indexed form of one word.

    ``strip().casefold()`` is deliberately the whole of normalisation: no
    stemming, no stopword removal, no digit filtering. Stopwords are scored low
    by idf instead of deleted, because deleting them would make every phrase
    containing one unmatchable and would make the term set depend on an English
    stoplist in a corpus that is mostly Telugu (`docs/retrieval-design.md` §3.4)
    — low weight can be overridden, deletion cannot.
    """
    return (text or "").strip().casefold()


def _ngrams(term: str) -> tuple[str, ...]:
    """Character n-grams of a stored term, n = NGRAM_MIN..NGRAM_MAX.

    A term shorter than the floor is its own only n-gram. The result is a tuple
    in a fixed order so the fallback's index build is deterministic even though
    a set would iterate in a stable order too — the ordering is part of the
    byte-identical-build contract, so it is not left to the caller's container.
    """
    if len(term) < NGRAM_MIN:
        return (term,) if term else ()
    out: list[str] = []
    for n in range(NGRAM_MIN, NGRAM_MAX + 1):
        for i in range(len(term) - n + 1):
            out.append(term[i:i + n])
    return tuple(out)


def digest_of(path: str | Path) -> str:
    """SHA256 hex of a file's bytes, streamed so a 4 GB source never loads.

    The digest is over *bytes*, not over the textual fingerprint, because it
    identifies a media file: two files with the same resolved path and different
    bytes are different sources, and the index must not be served across them.
    """
    handle = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            handle.update(chunk)
    return handle.hexdigest()


def source_fingerprint(media_path: str | Path, transcript_path: str | Path) -> str:
    """Bind an index to the exact media and transcript it was built from.

    The media digest excludes the ASR parameters — a re-transcribe changes the
    words and not the pixels — and the transcript digest includes them for
    exactly that reason, so a re-transcribed source produces a different
    fingerprint and the index is rebuilt rather than inherited. Uses the
    project's own ``fingerprint`` so the digest is stable across key order and
    float formatting, the way every other provenance record in the repo is.
    """
    return fingerprint(f"{digest_of(media_path)}|{digest_of(transcript_path)}")


def load_transcript(path: str | Path) -> tuple[list, str]:
    """Decode a committed ``transcript.json`` into ``Segment`` objects.

    Mirrors ``selekyt.pipeline._load_transcript``, the exact decoder the real
    run used; re-implementing the mapping here would risk decoding the on-disk
    record differently from the pipeline that wrote it, which is precisely how a
    query and its own index come to disagree.
    """
    from models.core import Segment, Word

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segments = [
        Segment(
            float(s["start"]), float(s["end"]), s.get("text") or "",
            [Word(**w) for w in (s.get("words") or [])],
        )
        for s in (data.get("segments") or [])
    ]
    return segments, str(data.get("language") or "unknown")


# ── the optional channels ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChannelStatus:
    """Whether one optional index input actually loaded, and what it carried.

    ``loaded`` is false when the input was absent or unreadable, and ``count``
    is then zero by construction. The two are kept as separate fields rather
    than collapsing to "count == 0 means absent" because a speech analysis that
    ran and found no speech is a *different fact* about the source from a speech
    analysis that never ran, and a query whose answer depends on which one
    happened must be able to tell them apart — the same trap
    `docs/retrieval-design.md` §1.2 names for VAD.
    """

    loaded: bool
    count: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {"loaded": self.loaded, "count": self.count, "note": self.note}

    @staticmethod
    def from_dict(d: dict) -> ChannelStatus:
        return ChannelStatus(
            loaded=bool(d.get("loaded")),
            count=int(d.get("count") or 0),
            note=str(d.get("note") or ""),
        )


# ── the index ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetrievalIndex:
    """An inverted file over one transcript, plus the provenance that binds it.

    Only two things are stored as data: the ``postings`` map from each
    normalised word type to its ``[(start, end, word_idx), ...]`` occurrences in
    ascending word order, and the ``segments`` array mapping each utterance to
    its word-index range. Everything else — the parallel start/end arrays,
    segment containment, df/idf, every score — is derived on read in one linear
    or logarithmic pass, so each fact lives in exactly one place and a stale
    derived value is impossible rather than unlikely.
    """

    schema: str
    source_fingerprint: str
    language: str
    # The fitness verdict travels *with* the index rather than being looked up
    # by a reader, so an index built on a `poor` transcript can never be read as
    # an authoritative one by a consumer that did not ask about language fitness.
    fitness: dict
    params: dict
    postings: dict[str, tuple[tuple[float, float, int], ...]]
    segments: tuple[tuple[float, float, int, int, int], ...]
    # Empty unless the fitness verdict is not `usable`: the lossy character
    # n-gram index is built only where the measured WER says the word-level one
    # will not carry recall (see `_wants_ngram_fallback`).
    ngram_postings: dict[str, tuple[tuple[float, float, int], ...]] = field(
        default_factory=dict)
    # word_idx-addressable side data. The transcript's own per-word confidence
    # is what lets a retrieval result report doubt the ASR itself measured,
    # rather than presenting every match as equally solid.
    word_confidence: tuple[float, ...] = ()
    word_terms: tuple[str, ...] = ()
    speech_regions: tuple[tuple[float, float], ...] = ()
    editorial_units: tuple[tuple[float, float], ...] = ()
    optional: dict[str, ChannelStatus] = field(default_factory=dict)

    # ── derived on read ──────────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        return not self.postings

    @property
    def n_words(self) -> int:
        return len(self.word_terms)

    @property
    def n_terms(self) -> int:
        return len(self.postings)

    @cached_property
    def _first_word_indices(self) -> list[int]:
        # Cached because ``segment_index_of_word`` is called once per posting
        # during the window sweep: rebuilding a list of every utterance's first
        # word index on each of those calls turned a query of two common terms
        # into a quadratic scan over the whole segment array.
        return [s[2] for s in self.segments]

    def segment_index_of_word(self, word_idx: int) -> int | None:
        """Which utterance a global word index falls into, or None if outside.

        ``bisect_right`` over the segments' ``first_word_idx`` column, which is
        ascending by construction. A word past the last utterance's last word is
        clamped to that utterance rather than reported as outside, because the
        only way to reach this function with such an index is a postings entry
        the index itself built, and "your own index is inconsistent" is a defect
        a caller cannot act on; None is reserved for a genuinely negative index.
        """
        if word_idx < 0 or not self.segments:
            return None
        firsts = self._first_word_indices
        # A zero-word utterance would collide with its successor here, but such
        # segments are not stored (see build_index), so firsts is strictly
        # increasing and the bisect lands on the owning utterance.
        idx = bisect_right(firsts, word_idx) - 1
        if idx < 0:
            return 0
        return min(idx, len(self.segments) - 1)

    def df_table(self) -> "dict[str, int]":
        """Document frequency of every stored term, one utterance per document.

        Built from the index's own postings rather than from
        ``engine.lexical.df_table``, which applies the content-token filter and
        would drop stopwords — the exact terms §3.4 requires to stay indexable
        so that a phrase containing "the" can still match. df over utterances is
        what makes a term rare *in this source*, which is the only rarity a
        single-source retrieval can mean.

        One pass over the segment array and the parallel ``word_terms`` slice,
        with no bisects: df is needed on every retrieve call, and a bisect per
        posting made it the most expensive thing the retrieve path did.
        """
        df: dict[str, int] = {}
        for first, last in ((s[2], s[3]) for s in self.segments):
            seen: set[str] = set()
            for term in self.word_terms[first:last + 1]:
                if term and term not in seen:
                    seen.add(term)
                    df[term] = df.get(term, 0) + 1
        return df

    def idf(self) -> dict[str, float]:
        """idf weights over the stored vocabulary, smoothed, computed per call.

        Never stored: a stored weight would be a stored opinion about a term's
        rarity, and the postings are the only stored fact (`docs/retrieval-design.md`
        §2.2). Computed per retrieval session, the way the design specifies.
        """
        return idf_weights(self.df_table(), len(self.segments))

    def words_in(self, seg_a: int, seg_b: int) -> tuple[int, int]:
        """The global word-index range [first, last] covered by utterances a..b."""
        seg_a = max(0, min(seg_a, len(self.segments) - 1))
        seg_b = max(seg_a, min(seg_b, len(self.segments) - 1))
        return self.segments[seg_a][2], self.segments[seg_b][3]

    def terms_in(self, seg_a: int, seg_b: int) -> set[str]:
        """The distinct indexed terms inside utterances a..b.

        A slice of the parallel ``word_terms`` array — O(window) rather than a
        scan of the vocabulary, which is what keeps a retrieve call cheap on a
        12,000-word source. Empty terms (unindexable words) contribute nothing.
        """
        if not self.segments:
            return set()
        first, last = self.words_in(seg_a, seg_b)
        return {t for t in self.word_terms[first:last + 1] if t}

    def ngrams_in(self, seg_a: int, seg_b: int) -> set[str]:
        """The distinct character n-grams inside utterances a..b.

        Derived from the ``word_terms`` slice, so the lossy fallback scores
        against n-grams rather than against the words they were built from —
        scoring a bag of 3-grams against a bag of words is always an empty
        intersection, and it would have scored every fallback match as zero.
        """
        if not self.segments:
            return set()
        first, last = self.words_in(seg_a, seg_b)
        out: set[str] = set()
        for term in self.word_terms[first:last + 1]:
            if term:
                out.update(_ngrams(term))
        return out

    def mean_confidence(self, seg_a: int, seg_b: int) -> float:
        """Mean of the ASR's own per-word confidence across utterances a..b.

        Falls back to 1.0 only when the window has no confidence records at all,
        which happens on a transcript whose words carried no confidence field;
        that is an absence, and it is reported as "unmeasured" (the maximum a
        reader can assume) rather than as a measured 0.0.
        """
        if not self.segments or not self.word_confidence:
            return 1.0
        first, last = self.words_in(seg_a, seg_b)
        window = self.word_confidence[first:last + 1]
        if not window:
            return 1.0
        return sum(window) / len(window)

    def in_speech(self, start: float, end: float) -> bool:
        """Whether a window opens inside a measured speech region.

        Tested on the *start*, not on the whole span: a window that begins in
        speech and runs into a pause is still a speech-led answer, and testing
        full containment would drop every utterance that ends in a breath.
        """
        for rs, re_ in self.speech_regions:
            if rs - MERGE_EPS <= start <= re_ + MERGE_EPS:
                return True
        return False

    def in_editorial_unit(self, start: float, end: float) -> bool:
        """Whether a window overlaps an editorial unit (a topic's span)."""
        for us, ue in self.editorial_units:
            if start <= ue + MERGE_EPS and us <= end + MERGE_EPS:
                return True
        return False

    # ── serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        out = {
            "schema": self.schema,
            "source_fingerprint": self.source_fingerprint,
            "language": self.language,
            "fitness": self.fitness,
            "built_at_params": self.params,
            "postings": {
                term: [[s, e, i] for s, e, i in hits]
                for term, hits in sorted(self.postings.items())
            },
            "segments": [list(s) for s in self.segments],
            "optional": {k: v.to_dict() for k, v in sorted(self.optional.items())},
        }
        if self.ngram_postings:
            out["ngram_postings"] = {
                term: [[s, e, i] for s, e, i in hits]
                for term, hits in sorted(self.ngram_postings.items())
            }
        if self.word_confidence:
            out["word_confidence"] = list(self.word_confidence)
        return out

    def save(self, path: str | Path) -> Path:
        """Write the index as UTF-8 JSON beside the transcript it was built from.

        ``ensure_ascii=False`` and a fixed key order are what make the file
        byte-identical across builds, which is what makes "stale" a diff rather
        than an opinion.
        """
        target = Path(path)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2,
                             sort_keys=False)
        target.write_text(payload + "\n", encoding="utf-8")
        return target

    @staticmethod
    def from_dict(d: dict) -> RetrievalIndex:
        fitness = dict(d.get("fitness") or {})
        postings = {
            str(t): tuple(tuple(h) for h in hits)
            for t, hits in (d.get("postings") or {}).items()
        }
        ngram = d.get("ngram_postings") or {}
        word_confidence = tuple(d.get("word_confidence") or ())
        # ``word_terms`` is derived rather than stored: it is a per-word view of
        # the postings map, so storing it would double the index for a fact that
        # is already there, and a stored copy could drift from the postings.
        n_words = 0
        for hits in postings.values():
            for hit in hits:
                n_words = max(n_words, int(hit[2]) + 1)
        for hits in ngram.values():
            for hit in hits:
                n_words = max(n_words, int(hit[2]) + 1)
        if len(word_confidence) > n_words:
            n_words = len(word_confidence)
        word_terms: list[str] = [""] * n_words
        for term, hits in postings.items():
            for _, _, word_idx in hits:
                if 0 <= word_idx < n_words:
                    word_terms[word_idx] = term
        return RetrievalIndex(
            schema=str(d.get("schema") or SCHEMA_VERSION),
            source_fingerprint=str(d.get("source_fingerprint") or ""),
            language=str(d.get("language") or "unknown"),
            fitness=fitness,
            params=dict(d.get("built_at_params") or d.get("params") or {}),
            postings=postings,
            segments=tuple(tuple(s) for s in (d.get("segments") or [])),
            ngram_postings={
                str(t): tuple(tuple(h) for h in hits) for t, hits in ngram.items()
            },
            word_confidence=word_confidence,
            word_terms=tuple(word_terms),
            speech_regions=tuple(tuple(r) for r in (d.get("speech_regions") or ())),
            editorial_units=tuple(tuple(u) for u in (d.get("editorial_units") or ())),
            optional={
                str(k): ChannelStatus.from_dict(v)
                for k, v in (d.get("optional") or {}).items()
            },
        )

    @staticmethod
    def load(path: str | Path, *, source_fingerprint: str | None = None) -> RetrievalIndex:
        """Read an index, refusing one built for a different source.

        A mismatch is an error and not a rebuild: the caller owns the source and
        is the one positioned to decide whether to re-index or to stop, and an
        index that quietly re-answered for whatever file is present now is an
        index that answers questions about the wrong video.
        """
        index = RetrievalIndex.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")))
        if source_fingerprint is not None and index.source_fingerprint != source_fingerprint:
            raise StaleIndexError(
                f"the retrieval index at {path} was built for source "
                f"{index.source_fingerprint!r}, not {source_fingerprint!r}; "
                f"rebuild it from the current transcript before querying it"
            )
        return index


def _wants_ngram_fallback(fitness: FitnessReport | None) -> bool:
    """Whether the lossy n-gram index should be built at all.

    Built wherever the verdict is not ``usable`` — i.e. where the measured WER
    sits above ``WER_BAR`` or no measurement exists. The ``not_measured`` case is
    deliberate and one-directional: with no measurement there is no evidence the
    transcript is good either, the fallback is consulted *only* when the exact
    index returns nothing, and it can never shadow an exact match, so building
    it is a cost and never a correctness risk.
    """
    if fitness is None:
        return True
    return fitness.verdict != "usable"


def build_index(
    segments: Sequence,
    *,
    language: str,
    source_fingerprint: str,
    fitness: FitnessReport | None = None,
    speech_regions: Sequence[tuple[float, float]] | None = None,
    editorial_units: Sequence[tuple[float, float]] | None = None,
) -> RetrievalIndex:
    """Build the retrieval index from a word-timestamped transcript.

    The transcript is the only mandatory input and the only one assumed present;
    every other channel is optional and its presence is recorded per input. The
    build is a pure function of its arguments — identical input yields a
    byte-identical index — and it reads no files and writes none.

    ``speech_regions`` and ``editorial_units`` are plain ``(start, end)`` spans
    rather than the pipeline's own ``SpeechRegion`` / topic records, so this
    module stays independent of the perception and corpus layers' record types
    while still re-ranking against their results.
    """
    postings: dict[str, list[tuple[float, float, int]]] = {}
    ngram_postings: dict[str, list[tuple[float, float, int]]] = {}
    word_confidence: list[float] = []
    word_terms: list[str] = []
    seg_records: list[tuple[float, float, int, int, int]] = []

    want_ngrams = _wants_ngram_fallback(fitness)
    word_idx = 0
    for seg in segments:
        first = word_idx
        n_indexed = 0
        for word in (getattr(seg, "words", None) or []):
            text = getattr(word, "text", "") or ""
            start = float(getattr(word, "start", 0.0) or 0.0)
            end = float(getattr(word, "end", start) or start)
            conf = getattr(word, "confidence", None)
            conf = float(conf) if conf is not None else 1.0
            # A non-finite confidence is a failed measurement, not superlative
            # trust — the same rule ``Evidence.__post_init__`` states, applied
            # here so the index never stores an ASR NaN as perfect word quality.
            if conf != conf or conf in (float("inf"), float("-inf")):
                conf = 0.0
            elif conf < 0.0:
                conf = 0.0
            elif conf > 1.0:
                conf = 1.0
            word_confidence.append(conf)
            term = _term(text)
            word_terms.append(term)
            this_idx = word_idx
            word_idx += 1
            if not term:
                continue
            postings.setdefault(term, []).append((start, max(end, start), this_idx))
            n_indexed += 1
            if want_ngrams:
                for ng in _ngrams(term):
                    ngram_postings.setdefault(ng, []).append(
                        (start, max(end, start), this_idx))
        if word_idx <= first:
            # An utterance with no word records contributes nothing indexable and
            # has no word range to map a query into; storing it would collide
            # with the next utterance's ``first_word_idx`` and make
            # ``segment_index_of_word`` ambiguous.
            continue
        seg_records.append(
            (float(getattr(seg, "start", 0.0) or 0.0),
             float(getattr(seg, "end", 0.0) or 0.0),
             first, word_idx - 1, n_indexed))

    vad_status = ChannelStatus(
        loaded=bool(speech_regions),
        count=len(speech_regions or ()),
        note="" if speech_regions else
        "speech regions were not supplied; a window inside measured speech "
        "cannot be boosted and the absence is reported rather than defaulted",
    )
    editorial_status = ChannelStatus(
        loaded=bool(editorial_units),
        count=len(editorial_units or ()),
        note="" if editorial_units else
        "editorial units were not supplied; a window inside a topic cannot be "
        "boosted and the absence is reported rather than defaulted",
    )

    fitness_dict = fitness.as_dict() if fitness is not None else {}

    return RetrievalIndex(
        schema=SCHEMA_VERSION,
        source_fingerprint=source_fingerprint,
        language=language,
        fitness=fitness_dict,
        params={
            "n_words": len(word_terms),
            "n_indexed_words": sum(s[4] for s in seg_records),
            "n_terms": len(postings),
            "n_ngram_terms": len(ngram_postings),
            "n_segments": len(seg_records),
            "tokeniser": "strip+casefold",
            "ngram": want_ngrams,
        },
        postings={t: tuple(h) for t, h in sorted(postings.items())},
        segments=tuple(seg_records),
        ngram_postings={t: tuple(h) for t, h in sorted(ngram_postings.items())},
        word_confidence=tuple(word_confidence),
        word_terms=tuple(word_terms),
        speech_regions=tuple(tuple(r) for r in (speech_regions or ())),
        editorial_units=tuple(tuple(u) for u in (editorial_units or ())),
        optional={"vad": vad_status, "editorial_corpus": editorial_status},
    )


# ── retrieving ───────────────────────────────────────────────────────────────


def _term_present(word_ids: tuple[int, ...], first: int, last: int) -> bool:
    """Whether a term occurs anywhere in the global word range [first, last].

    Two bisects into the term's postings — ascending by word index by
    construction — instead of a scan of the window's words. This is what keeps
    a retrieve call linear in the *query*, not in the window size, on a source
    where a common term has a posting list hundreds long.
    """
    if not word_ids:
        return False
    return bisect_left(word_ids, first) < bisect_left(word_ids, last + 1)


def _word_id_table(index: RetrievalIndex, terms) -> dict[str, tuple[int, ...]]:
    """The ascending word-index list for each of these terms, built once.

    A per-window ``[h[2] for h in hits]`` rebuild was the single most expensive
    thing the retrieve path did: on a 75-minute source a common term carries
    ~800 postings, and rebuilding that list for every candidate window and every
    query term turns a query of two common terms into a quadratic scan. Hoisted
    out, the same query is a few thousand bisects.
    """
    return {
        term: tuple(h[2] for h in index.postings.get(term, ()))
        for term in terms
    }


def _window_idf_mass(index: RetrievalIndex, first: int, last: int,
                     idf: dict[str, float]) -> float:
    """Total idf mass of the distinct terms in a word range.

    Needed only when a window carries fewer distinct terms than the query — the
    smaller-bag side of ``weighted_containment`` — which is rare, so it walks
    the slice rather than being precomputed.
    """
    mass = 0.0
    seen: set[str] = set()
    for term in index.word_terms[first:last + 1]:
        if term and term not in seen:
            seen.add(term)
            mass += idf.get(term, IDF_FLOOR)
    return mass


@dataclass(frozen=True)
class _Window:
    """One candidate answer, with the scoring facts that ranked it.

    ``seg_a``/``seg_b`` are utterance indices into the index's segment array;
    the interval is the expanded span, which is what an editor clips and what
    GROUND receives. ``boosts`` records which optional channels raised the score
    and by how much, so the rationale can name them instead of folding them in.
    """

    interval: tuple[float, float]
    seg_a: int
    seg_b: int
    score: float
    base_score: float
    boosts: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class RetrievalResult:
    """The answer to one retrieve call.

    ``intervals`` is the union of the post-NMS windows and is *the* answer of
    record: GROUND receives exactly this set and expands nothing on its own.
    ``evidence`` is one record per window; ``report`` is what the retrieve
    actually did, including which channels it could not consult, because a query
    that returns nothing is only useful if it also says what would have been
    queryable.
    """

    intervals: IntervalSet
    evidence: tuple[Evidence, ...]
    channels: dict[str, bool]
    report: str
    query_terms: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.intervals.is_empty


_QueryElement = "str | tuple[str, ...]"
Query = "Sequence[str | tuple[str, ...]]"


def _normalise_query(query) -> tuple[list[list[str]], list[str]]:
    """Split a query into per-element term groups, plus the flat term list.

    Each element is either a bare term (ANDed with the others) or a tuple read
    as an ordered phrase. Normalisation is ``_term`` on each part, and an
    element that normalises to nothing is dropped — but if *every* element does,
    the query has no content and the caller learns that from an empty result
    rather than from a loosened one.
    """
    groups: list[list[str]] = []
    flat: list[str] = []
    for element in query:
        if isinstance(element, (tuple, list)):
            terms = [t for t in (_term(x) for x in element) if t]
            if terms:
                groups.append(terms)
                flat.extend(terms)
        else:
            term = _term(element)
            if term:
                groups.append([term])
                flat.append(term)
    return groups, flat


def _phrase_matches(index: RetrievalIndex, terms: list[str]) -> list[tuple[float, float, int]]:
    """Ordered, time-bounded occurrences of a phrase, as (start, end, first_word_idx).

    Term i must occur after term i-1 in the word sequence, and each consecutive
    pair must fall within ``PHRASE_GAP_S`` of each other. Reversed order
    therefore does not match — "B A" is not the phrase "A B" — and two terms ten
    minutes apart are not a phrase either, which is what stops "adjacent" from
    collapsing into "both present somewhere".
    """
    if not terms:
        return []
    first = index.postings.get(terms[0])
    if not first:
        return []
    # Precomputed word-index lists per term so each continuation step is a
    # bisect rather than a scan, and the word starts stay available for the gap
    # test without re-reading the postings tuples.
    starts: dict[str, list[float]] = {}
    indices: dict[str, list[int]] = {}
    for term in terms:
        hits = index.postings.get(term)
        if hits:
            starts[term] = [h[0] for h in hits]
            indices[term] = [h[2] for h in hits]
    out: list[tuple[float, float, int]] = []
    for start0, _end0, w0 in first:
        current_end = _end0
        current_w = w0
        ok = True
        for nxt in terms[1:]:
            word_ids = indices.get(nxt)
            if not word_ids:
                ok = False
                break
            # The postings are ascending by word index, so a bisect finds the
            # first candidate able to continue the phrase at all.
            lo = bisect_left(word_ids, current_w + 1)
            tail_starts = starts[nxt]
            best_start: float | None = None
            best_w: int | None = None
            for offset in range(lo, len(word_ids)):
                s = tail_starts[offset]
                if s - current_end > PHRASE_GAP_S:
                    # Beyond the adjacency window; a later candidate starts
                    # later still, so nothing further can fit either.
                    break
                if best_start is None or s < best_start:
                    best_start, best_w = s, word_ids[offset]
            if best_start is None:
                ok = False
                break
            current_end = best_start
            current_w = best_w
        if ok:
            out.append((start0, current_end, w0))
    return out


def _element_matches(index: RetrievalIndex, terms: list[str]) -> list[tuple[float, float, int]]:
    """The occurrences one query element contributes: a phrase, or one term's postings."""
    if len(terms) == 1:
        return list(index.postings.get(terms[0], ()))
    return _phrase_matches(index, terms)


def _candidate_windows(index: RetrievalIndex, groups: list[list[str]]) -> list[_Window]:
    """Minimal utterance-granularity windows covering every query element.

    A window is a contiguous run of utterances containing at least one occurrence
    of *each* element, found by a sliding sweep over (utterance, element) events
    — the minimum-window-cover algorithm, over a sequence whose positions are
    utterance indices rather than characters. Windows are at utterance
    granularity because that is the unit a phrase actually spans, and because a
    sub-utterance answer is not a tighter answer, it is a truncated word.
    """
    if not groups:
        return []
    events: list[tuple[int, int, int]] = []
    for element_id, terms in enumerate(groups):
        for _start, _end, word_idx in _element_matches(index, terms):
            seg = index.segment_index_of_word(word_idx)
            if seg is None:
                continue
            events.append((seg, element_id, word_idx))
    if len(events) < len(groups):
        # At least one element matched nothing, so no window can cover all of
        # them — and this is reported as emptiness, not loosened into a partial
        # match.
        return []
    events.sort(key=lambda ev: (ev[0], ev[2]))

    need = len(groups)
    seen: dict[int, int] = {}
    have = 0
    left = 0
    spans: list[tuple[int, int]] = []
    for right in range(len(events)):
        element = events[right][1]
        seen[element] = seen.get(element, 0) + 1
        if seen[element] == 1:
            have += 1
        while have == need:
            spans.append((events[left][0], events[right][0]))
            drop = events[left][1]
            seen[drop] -= 1
            if seen[drop] == 0:
                have -= 1
            left += 1
    return _score(index, groups, spans)


def _score(index: RetrievalIndex, groups: list[list[str]],
           spans: list[tuple[int, int]]) -> list[_Window]:
    """Rank candidate windows: idf-weighted term overlap, then tighter spans.

    The window score is ``weighted_containment`` between the query's terms and
    the window's terms over an idf table derived from the index itself. It is
    evaluated as the shared idf mass over the smaller bag's mass — the same
    value — at a cost of one bisect per query term rather than a materialised
    set of the window's words, which is what keeps a retrieve call linear in the
    query instead of in the window on a source where a common term's posting
    list is thousands long. Optional channels then raise a window's score by a
    recorded factor, and ties break toward the tighter span, so a phrase
    spanning 3 s outranks one spanning 40 s at equal term overlap.
    """
    idf = _scored_idf(index)
    query_terms = {t for group in groups for t in group}
    query_mass = sum(idf.get(t, IDF_FLOOR) for t in query_terms) or 1.0
    word_ids = _word_id_table(index, query_terms)
    out: list[_Window] = []
    for seg_a, seg_b in spans:
        first, last = index.words_in(seg_a, seg_b)
        shared = {t for t in query_terms
                  if _term_present(word_ids[t], first, last)}
        # A candidate window contains at least one occurrence of every query
        # element by construction, so the common case is complete containment:
        # the smaller bag is the query's and the score is 1.0 before boosts.
        # Computing the window's full idf mass here would walk every word in a
        # window that can be thousands long, on every candidate — so the general
        # formula is evaluated only for the partial case that can actually arise
        # if the window sweep ever changes.
        if shared == query_terms:
            base = 1.0
        else:
            shared_mass = sum(idf.get(t, IDF_FLOOR) for t in shared)
            window_mass = _window_idf_mass(index, first, last, idf)
            smaller = min(query_mass, window_mass)
            base = shared_mass / smaller if smaller > 0.0 else 0.0
            base = min(base, 1.0)
        start = index.segments[seg_a][0]
        end = index.segments[seg_b][1]
        boosts: list[tuple[str, float]] = []
        score = base
        if index.optional.get("vad") and index.optional["vad"].loaded:
            if index.in_speech(start, end):
                score = score * (1.0 + SPEECH_REGION_BOOST)
                boosts.append(("speech_region", SPEECH_REGION_BOOST))
        if index.optional.get("editorial_corpus") and index.optional["editorial_corpus"].loaded:
            if index.in_editorial_unit(start, end):
                score = score * (1.0 + TOPIC_UNIT_BOOST)
                boosts.append(("editorial_unit", TOPIC_UNIT_BOOST))
        out.append(_Window(
            interval=(start, end),
            seg_a=seg_a,
            seg_b=seg_b,
            score=score,
            base_score=base,
            boosts=tuple(boosts),
        ))
    # Score desc, then the tighter span first, then the earlier start — a stable
    # and fully determined order, so two builds rank identically.
    out.sort(key=lambda w: (-w.score, w.interval[1] - w.interval[0], w.interval[0]))
    return out


def _suppress(windowed: list[_Window], *, k: int, nms_s: float) -> list[_Window]:
    """Top-K after non-maximum suppression on window starts.

    A candidate is dropped when its start is within ``nms_s`` of an already-kept
    candidate's start. ``nms_s`` is a chapter-scale suppression, not a tuned
    constant: it stops one passage spoken twice eight minutes apart from filling
    every slot while still letting genuinely distinct chapters through. It is a
    named parameter on the call rather than a buried literal, because the first
    time retrieval quality can be measured at all this is the first constant to
    re-tune.
    """
    kept: list[_Window] = []
    for window in windowed:
        if any(abs(window.interval[0] - k_.interval[0]) < nms_s for k_ in kept):
            continue
        kept.append(window)
        if len(kept) >= k:
            break
    return kept


def _verdict_phrase(index: RetrievalIndex) -> str:
    fitness = index.fitness or {}
    verdict = str(fitness.get("verdict") or "not_measured")
    if verdict == "not_measured":
        return "the transcript's language fitness was never measured"
    wer = fitness.get("wer")
    if isinstance(wer, (int, float)):
        return f"the transcript's language fitness verdict is {verdict} (WER {wer:.1%})"
    return f"the transcript's language fitness verdict is {verdict}"


def _evidence_for(index: RetrievalIndex, window: _Window, *, query_terms: tuple[str, ...],
                  k: int, nms_s: float, via_ngram: bool, artifacts: tuple[str, ...]) -> Evidence:
    """One window as an ``Evidence`` record, with every judgement it made named."""
    start, end = window.interval
    fitness = index.fitness or {}
    verdict = str(fitness.get("verdict") or "not_measured")

    boosts = "; ".join(
        f"{name} re-ranking applied (+{factor:.0%})" for name, factor in window.boosts)
    absent = [
        name for name, key in (("speech regions", "vad"),
                               ("editorial units", "editorial_corpus"))
        if not (index.optional.get(key) and index.optional[key].loaded)]
    absent_note = (
        f"; the {', '.join(absent)} channel was not loaded, so its re-ranking "
        f"is absent rather than defaulted" if absent else "")

    word_conf = index.mean_confidence(window.seg_a, window.seg_b)
    # A window's confidence is the ranking score moderated by the ASR's own word
    # quality in that window: a window whose words the transcriber itself scored
    # low is a window retrieval must not present as solid. Both factors are
    # recorded in the rationale; neither is smoothed or inflated.
    confidence = window.score * word_conf
    if via_ngram:
        confidence = min(confidence, NGRAM_CONFIDENCE_CEILING)

    claim = (
        f"The phrase {' '.join(query_terms)!r} occurs here"
        if not via_ngram else
        f"A character-level match for {' '.join(query_terms)!r} probably occurs here"
    )
    rationale_bits = [
        f"window score {window.base_score:.3f} over the query's terms in a "
        f"window of {end - start:.1f}s",
        f"confidence {confidence:.3f} = ranking {window.score:.3f} x mean ASR "
        f"word confidence {word_conf:.2f}",
    ]
    if via_ngram:
        rationale_bits.insert(
            0,
            f"matched by the lossy character n-gram index (n={NGRAM_MIN}.."
            f"{NGRAM_MAX}), which is a hypothesis about what was probably said "
            f"and is marked ungrounded and capped at "
            f"{NGRAM_CONFIDENCE_CEILING:.2f}")
    if boosts:
        rationale_bits.append(boosts)
    rationale_bits.append(_verdict_phrase(index))
    if absent_note:
        rationale_bits.append(absent_note.lstrip("; "))

    return Evidence(
        claim=claim + ".",
        intervals=IntervalSet.of((start, end)),
        modality=Modality.EDITORIAL if any(
            name == "editorial_unit" for name, _ in window.boosts) else Modality.TEXT,
        source=Source.of(
            "engine.retrieval", "retrieve",
            k=k, nms_s=nms_s, tokeniser="strip+casefold",
            ngram=via_ngram),
        confidence=confidence,
        rationale="; ".join(rationale_bits),
        grounded=not via_ngram,
        artifacts=artifacts,
    )


def retrieve(
    index: RetrievalIndex,
    query: Query,
    *,
    k: int = DEFAULT_K,
    nms_s: float = DEFAULT_NMS_S,
    artifacts: tuple[str, ...] = (),
) -> RetrievalResult:
    """Answer a query against the index, without decoding anything.

    ``query`` is a sequence of elements. A bare string is one term; a tuple is
    an ordered phrase whose consecutive terms must occur in order and within
    ``PHRASE_GAP_S`` of each other. Every element must be matched — this is an
    AND over elements — and the answer is the set of post-NMS windows, never a
    single span.

    The answer is honest about its own limits: a query that matches nothing
    returns empty intervals and empty evidence, and the ``report`` names which
    channels were queryable instead. Nothing is loosened, and no nearest span is
    substituted, because automatic relaxation is how "not found" becomes "found
    something unrelated" with no record of the change.
    """
    groups, flat = _normalise_query(query)
    channels = {name: bool(status.loaded)
                for name, status in index.optional.items()}
    query_terms = tuple(dict.fromkeys(flat))

    if not groups or index.is_empty:
        return RetrievalResult(
            intervals=IntervalSet.empty(),
            evidence=(),
            channels=channels,
            query_terms=query_terms,
            report=_empty_report(index, groups, query_terms),
        )

    windows = _candidate_windows(index, groups)
    if windows:
        kept = _suppress(windows, k=k, nms_s=nms_s)
        intervals = IntervalSet.of(
            *[(w.interval[0], w.interval[1]) for w in kept])
        evidence = tuple(
            _evidence_for(index, w, query_terms=query_terms, k=k, nms_s=nms_s,
                          via_ngram=False, artifacts=artifacts)
            for w in kept)
        return RetrievalResult(
            intervals=intervals,
            evidence=evidence,
            channels=channels,
            query_terms=query_terms,
            report=(
                f"kept {len(kept)} of {len(windows)} candidate windows after "
                f"top-{k} selection with NMS at {nms_s:g}s; {_verdict_phrase(index)}"),
        )

    # The exact word-level index answered nothing. Where the transcript's own
    # fitness says it would — a verdict that is not `usable` — the lossy
    # character n-gram index is consulted, and only here: it can never shadow an
    # exact match, because this branch is reached only when the exact index
    # produced no window at all.
    if index.ngram_postings and _wants_ngram_fallback_from_dict(index.fitness):
        ngram_groups = [[ng for t in group for ng in _ngrams(t)] for group in groups]
        ngram_windows = _candidate_windows_ngram(index, ngram_groups)
        if ngram_windows:
            kept = _suppress(ngram_windows, k=k, nms_s=nms_s)
            intervals = IntervalSet.of(
                *[(w.interval[0], w.interval[1]) for w in kept])
            evidence = tuple(
                _evidence_for(index, w, query_terms=query_terms, k=k, nms_s=nms_s,
                              via_ngram=True, artifacts=artifacts)
                for w in kept)
            return RetrievalResult(
                intervals=intervals,
                evidence=evidence,
                channels=channels,
                query_terms=query_terms,
                report=(
                    f"the word-level index matched nothing, so the lossy "
                    f"character n-gram index (n={NGRAM_MIN}..{NGRAM_MAX}) "
                    f"answered with {len(kept)} window(s); every result is "
                    f"marked ungrounded and capped at confidence "
                    f"{NGRAM_CONFIDENCE_CEILING:.2f}; "
                    f"{_verdict_phrase(index)}"),
            )

    return RetrievalResult(
        intervals=IntervalSet.empty(),
        evidence=(),
        channels=channels,
        query_terms=query_terms,
        report=_empty_report(index, groups, query_terms),
    )


def _wants_ngram_fallback_from_dict(fitness: dict) -> bool:
    """Read the stored verdict the way ``_wants_ngram_fallback`` decided it."""
    return str(fitness.get("verdict") or "not_measured") != "usable"


def _candidate_windows_ngram(index: RetrievalIndex,
                             groups: list[list[str]]) -> list[_Window]:
    """Windows over the n-gram index, scored by n-gram overlap.

    The n-gram index shares the segment array and the suppression machinery with
    the exact path; only the postings consulted and the scoring differ. An
    n-gram element matches when *any* of its query n-grams occurs, and the score
    is the idf-weighted containment of the query's n-grams in the window's — a
    deliberately weaker, lossier measure than the exact one, which is why its
    results carry ``grounded=False``.
    """
    if not groups:
        return []
    events: list[tuple[int, int, int]] = []
    for element_id, ngrams in enumerate(groups):
        seen_words: set[int] = set()
        for ng in ngrams:
            for _s, _e, word_idx in index.ngram_postings.get(ng, ()):
                seen_words.add(word_idx)
        for word_idx in sorted(seen_words):
            seg = index.segment_index_of_word(word_idx)
            if seg is None:
                continue
            events.append((seg, element_id, word_idx))
    if not events or len({e[1] for e in events}) < len(groups):
        return []

    need = len(groups)
    seen: dict[int, int] = {}
    have = 0
    left = 0
    spans: list[tuple[int, int]] = []
    events.sort(key=lambda ev: (ev[0], ev[2]))
    for right in range(len(events)):
        element = events[right][1]
        seen[element] = seen.get(element, 0) + 1
        if seen[element] == 1:
            have += 1
        while have == need:
            spans.append((events[left][0], events[right][0]))
            drop = events[left][1]
            seen[drop] -= 1
            if seen[drop] == 0:
                have -= 1
            left += 1

    # The n-gram query terms have no idf table of their own — n-grams of the
    # *query*, not of the vocabulary — so the fallback scores on plain overlap:
    # ``weighted_containment`` defaults an unknown term's weight to 1.0, and an
    # empty table makes every weight uniform. That is a deliberately weaker,
    # lossier measure than the exact path's idf-weighted one, which is exactly
    # why its results carry ``grounded=False`` and a capped confidence.
    query_ngrams = {ng for group in groups for ng in group}
    out: list[_Window] = []
    for seg_a, seg_b in spans:
        window_ngrams = index.ngrams_in(seg_a, seg_b)
        base = weighted_containment(query_ngrams, window_ngrams, {})
        start = index.segments[seg_a][0]
        end = index.segments[seg_b][1]
        out.append(_Window(
            interval=(start, end),
            seg_a=seg_a,
            seg_b=seg_b,
            score=base,
            base_score=base,
            boosts=(),
        ))
    out.sort(key=lambda w: (-w.score, w.interval[1] - w.interval[0], w.interval[0]))
    return out


def _empty_report(index: RetrievalIndex, groups: list[list[str]],
                  query_terms: tuple[str, ...]) -> str:
    """What a zero-hit query reports: nothing matched, and here is what is queryable.

    Reporting the queryable channels is the alternative to fabricating a span.
    Speech regions and editorial units carry temporal structure that is not
    word-shaped, so a query that cannot be answered lexically can still tell an
    editor which channels would answer a differently-shaped one.
    """
    asked = ", ".join(repr(t) for t in query_terms) or "the query"
    available = [
        f"{name} ({status.count} spans)"
        for name, status in sorted(index.optional.items()) if status.loaded]
    channels = (
        f" queryable non-lexical channels: {'; '.join(available)}"
        if available else
        " no non-lexical channel was loaded, so no differently-shaped query is "
        "answerable from this index either")
    reason = "the index is empty" if index.is_empty else "no window matched every element"
    return (
        f"nothing in this source matches {asked}: {reason}.{channels}. "
        f"The query was not loosened and no nearest span was substituted."
    )


__all__ = [
    "ChannelStatus",
    "Query",
    "RetrievalIndex",
    "RetrievalResult",
    "SCHEMA_VERSION",
    "StaleIndexError",
    "build_index",
    "digest_of",
    "load_transcript",
    "retrieve",
    "source_fingerprint",
]
