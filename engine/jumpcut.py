"""Jump-cut engine — dead-air removal, filler trim, word-safe boundaries.

Takes a fused AudioTruth and produces a CutList (list of keep intervals
annotated with type). Uses engine.cut_algebra for all interval operations.

Word-safe: never cuts inside a word — always snaps to the nearest word
boundary. Preserves intentional pauses by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from models.core import Word
from engine.vad import SpeechRegion, SilenceRegion
from engine.pause_classifier import ClassifiedPause, PauseType
from engine.audio_fusion import AudioTruth, FusedWord, FusedRegion
from engine import cut_algebra


class SegmentType(str, Enum):
    KEEP = "keep"
    REMOVE_SILENCE = "remove_silence"
    REMOVE_FILLER = "remove_filler"
    REMOVE_HALLUCINATION = "remove_hallucination"


@dataclass
class CutSegment:
    start: float
    end: float
    segment_type: SegmentType
    reason: str = ""
    duration: float = 0.0

    def __post_init__(self):
        self.duration = round(self.end - self.start, 3)


@dataclass
class CutList:
    segments: list[CutSegment] = field(default_factory=list)
    total_keep_duration: float = 0.0
    total_remove_duration: float = 0.0
    original_duration: float = 0.0
    compression_ratio: float = 1.0
    segments_removed: int = 0
    words_removed: int = 0

    def __post_init__(self):
        self.total_keep_duration = round(
            sum(s.duration for s in self.segments if s.segment_type == SegmentType.KEEP), 3
        )
        self.total_remove_duration = round(
            sum(s.duration for s in self.segments if s.segment_type != SegmentType.KEEP), 3
        )
        self.segments_removed = sum(1 for s in self.segments if s.segment_type != SegmentType.KEEP)
        if self.original_duration > 0:
            self.compression_ratio = round(
                self.total_keep_duration / self.original_duration, 3
            )


def cutlist_from_wire(intervals: list[dict], source_duration: float) -> CutList:
    """Build a CutList from the JSON-RPC wire format the Rust sidecar speaks
    (``{"start", "end", "kind"}`` where kind is ``keep``/``remove``), so the
    two implementations can be handed identical params."""
    kind_map = {"keep": SegmentType.KEEP}
    segments = [
        CutSegment(
            start=float(i["start"]),
            end=float(i["end"]),
            segment_type=kind_map.get(str(i.get("kind", "remove")), SegmentType.REMOVE_SILENCE),
            reason=str(i.get("kind", "remove")),
        )
        for i in intervals
    ]
    return CutList(segments=segments, original_duration=float(source_duration))


class JumpCutEngine:
    """Configurable jump-cut engine.

    Parameters:
        silence_threshold: minimum silence duration (s) to remove
        hesitation_threshold: hesitation pauses longer than this are removed
        filler_threshold: pauses below this are trimmed if not intentional
        keep_intentional: preserve intentional pauses
        keep_breathing: preserve breathing pauses
        remove_filler: remove hesitation and filler pauses (the editorial
            "ums, ahs and stalls" pass; set False to keep the speaker's
            hesitations verbatim)
        word_safe_margin: extra padding (s) added around word boundaries
        remove_hallucinations: remove words outside VAD speech regions
    """

    def __init__(
        self,
        silence_threshold: float = 0.5,
        hesitation_threshold: float = 0.9,
        filler_threshold: float = 0.6,
        keep_intentional: bool = True,
        keep_breathing: bool = True,
        remove_filler: bool = True,
        word_safe_margin: float = 0.02,
        remove_hallucinations: bool = True,
    ):
        self.silence_threshold = silence_threshold
        self.hesitation_threshold = hesitation_threshold
        self.filler_threshold = filler_threshold
        self.keep_intentional = keep_intentional
        self.keep_breathing = keep_breathing
        self.remove_filler = remove_filler
        self.word_safe_margin = word_safe_margin
        self.remove_hallucinations = remove_hallucinations

    def generate_cutlist(self, truth: AudioTruth) -> CutList:
        """Generate a cut list from fused audio-truth.

        Strategy:
        1. Start with VAD speech regions as candidate KEEP intervals.
        2. Word-safe snap KEEP outward to word boundaries.
        3. Subtract REMOVE pauses (dead-air, hesitation) from KEEP.
        4. Union KEEP with kept pauses (intentional/breathing).
        5. Subtract hallucinations from KEEP.
        6. Merge adjacent KEEP intervals.
        7. Derive REMOVE segments as the gaps between KEEP segments.
        8. Return CutList with compression stats.
        """
        keep_intervals: list[tuple[float, float]] = []
        for sr in truth.vad_speech:
            keep_intervals.append((sr.start, sr.end))

        snapped = self._word_safe_snap(keep_intervals, truth.fused_words)
        kept_pauses: list[tuple[float, float]] = []
        # Gaps are re-derived from the keep tiling after the fact, so the reason
        # a pause was dropped would be lost. Carry it alongside the interval and
        # let _derive_segments attribute a gap to the decision that made it.
        removed_reasons: list[tuple[float, float, str]] = []

        for pause in truth.pauses:
            keep, reason = self._should_keep_pause(pause)
            if keep:
                kept_pauses.append((pause.start, pause.end))
            else:
                removed_reasons.append((pause.start, pause.end, reason))
                if snapped:
                    snapped = cut_algebra.subtract_intervals(
                        snapped, [(pause.start, pause.end)]
                    )

        snapped = cut_algebra.merge_intervals(snapped + kept_pauses)

        if self.remove_hallucinations:
            hallucination_intervals = [
                (fw.word.start, fw.word.end)
                for fw in truth.fused_words if not fw.vad_speech
            ]
            if hallucination_intervals and snapped:
                snapped = cut_algebra.subtract_intervals(
                    snapped, hallucination_intervals
                )
            for fw in truth.fused_words:
                if not fw.vad_speech:
                    removed_reasons.append(
                        (fw.word.start, fw.word.end, "hallucination")
                    )

        keep_intervals = cut_algebra.merge_intervals(snapped)

        segments = self._derive_segments(
            keep_intervals, truth.duration, removed_reasons
        )

        return CutList(
            segments=segments,
            original_duration=truth.duration,
            words_removed=self._count_removed_words(truth),
        )

    def _derive_segments(
        self,
        keep_intervals: list[tuple[float, float]],
        duration: float,
        removed_reasons: list[tuple[float, float, str]] | None = None,
    ) -> list[CutSegment]:
        """Convert KEEP intervals into annotated CUT segments with gaps.

        AutoCut-exact tiling: keeps have already been merged with the `s <=
        last.1` rule, so every gap between them (and the leading/trailing
        silence) becomes a Remove segment covering [0, duration] exactly.
        """
        segments: list[CutSegment] = []
        prev_end = 0.0

        def emit_gap(g0: float, g1: float) -> None:
            gap_type, reason = self._classify_gap(g0, g1, removed_reasons or [])
            segments.append(CutSegment(g0, g1, gap_type, reason))

        for start, end in keep_intervals:
            if start > prev_end:
                emit_gap(prev_end, start)
            segments.append(CutSegment(start, end, SegmentType.KEEP, "speech"))
            prev_end = end

        if prev_end < duration:
            emit_gap(prev_end, duration)

        return segments

    def _classify_gap(
        self,
        start: float,
        end: float,
        removed_reasons: list[tuple[float, float, str]],
    ) -> tuple[SegmentType, str]:
        """Name a gap for the decision that actually created it.

        Gaps are derived from the final keep tiling, which loses the reason a
        pause was dropped. Without this lookup a removed hesitation is reported
        as dead air — the cut is right, but the editor's traceability is wrong,
        and REMOVE_FILLER becomes unreachable: any pause long enough to count as
        filler already exceeds silence_threshold, so duration alone never
        selects it.
        """
        mid = (start + end) / 2.0
        for r0, r1, reason in removed_reasons:
            if r0 <= mid <= r1:
                if reason == "hallucination":
                    return SegmentType.REMOVE_HALLUCINATION, reason
                if reason in ("hesitation", "filler"):
                    return SegmentType.REMOVE_FILLER, reason
                return SegmentType.REMOVE_SILENCE, reason
        gap_type = self._gap_type_by_duration(end - start)
        return gap_type, gap_type.value

    def _gap_type_by_duration(self, gap_dur: float) -> SegmentType:
        if gap_dur >= self.silence_threshold:
            return SegmentType.REMOVE_SILENCE
        return SegmentType.REMOVE_FILLER

    def _should_keep_pause(self, pause: ClassifiedPause) -> tuple[bool, str]:
        if pause.pause_type == PauseType.DEAD_AIR and pause.duration >= self.silence_threshold:
            return False, "dead_air"
        if pause.pause_type == PauseType.INTENTIONAL and self.keep_intentional:
            return True, "intentional_kept"
        if pause.pause_type == PauseType.BREATHING and self.keep_breathing:
            return True, "breathing_kept"
        # Hesitation and filler removal is one editorial switch: without it the
        # speaker's stalls stay verbatim, which is what --no-remove-filler asks
        # for. Dead air and breath handling are independent switches above.
        if self.remove_filler:
            if pause.pause_type == PauseType.HESITATION and pause.duration >= self.hesitation_threshold:
                return False, "hesitation"
            if pause.duration >= self.filler_threshold and pause.pause_type in (
                PauseType.HESITATION, PauseType.UNCERTAIN
            ):
                return False, "filler"
        return True, "short_kept"

    def _word_safe_snap(
        self,
        intervals: list[tuple[float, float]],
        fused_words: list[FusedWord],
    ) -> list[tuple[float, float]]:
        """Snap intervals outward to word boundaries (never inward).

        If a word is partially inside an interval, extend the interval
        to include the whole word. Never shrink the original interval.
        """
        if not fused_words:
            return intervals

        sorted_words = sorted(fused_words, key=lambda fw: fw.word.start)
        snapped: list[tuple[float, float]] = []

        for start, end in intervals:
            new_start = start
            new_end = end
            for fw in sorted_words:
                w = fw.word
                if w.end < start - 0.05:
                    continue
                if w.start > end + 0.05:
                    break
                if w.start < start and w.end >= start - 0.05:
                    new_start = min(new_start, w.start)
                if w.end > end and w.start <= end + 0.05:
                    new_end = max(new_end, w.end)

            snapped.append((
                max(0.0, new_start - self.word_safe_margin),
                new_end + self.word_safe_margin,
            ))

        return snapped

    def _count_removed_words(self, truth: AudioTruth) -> int:
        if not self.remove_hallucinations:
            return 0
        return sum(1 for fw in truth.fused_words if not fw.vad_speech)


def quick_cut(
    truth: AudioTruth,
    silence_threshold: float = 0.5,
    keep_intentional: bool = True,
) -> CutList:
    """Convenience function for simple dead-air removal."""
    engine = JumpCutEngine(
        silence_threshold=silence_threshold,
        keep_intentional=keep_intentional,
    )
    return engine.generate_cutlist(truth)
