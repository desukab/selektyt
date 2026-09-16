//! Rust port of AutoCut's `src/lib/cuts.ts` — the deterministic interval
//! algebra behind the cut editor. Ported with permission; the TS functions
//! were split into modules and re-typed, the constants and behaviour are
//! unchanged, and the unit cases below mirror `cuts.test.ts`.
//!
//! These functions define what actually lands in an export: keeps are
//! normalized (clipped, de-degenerate, merged only when genuinely
//! overlapping), tiled so every gap becomes a remove, and resolved so a
//! frontend-only `disabled` flag collapses into the surrounding removes before
//! the exporters ever see it.
//!
//! The disabled flag is a TS/frontend concept that AutoCut's Rust `CutList`
//! deliberately does not carry. This module models it as `EditCutList` (the
//! canonical editorial representation) and converts to `cutlist::CutList`
//! (the export representation) via `resolve_for_export`.

use serde::{Deserialize, Serialize};

use crate::cutlist::{Cut, CutKind, CutList};

/// Shorter than this and the interval is a drag artifact, not a cut, so
/// normalization drops it outright.
const DEGENERATE_SECONDS: f64 = 0.01;

/// The shortest a keep may be squeezed to by editing one of its edges.
pub const MIN_KEEP_SECONDS: f64 = 0.05;

/// Once the playhead is this close to an interval's out-point it is already
/// leaving; re-issuing the same skip only makes the audio context click.
const SKIP_SETTLED_SECONDS: f64 = 0.005;

/// Past this far into a keep, "previous" restarts it rather than stepping back.
const RESTART_WINDOW_SECONDS: f64 = 1.0;

/// "Next" ignores keeps starting within this of the playhead.
const NEXT_EPSILON_SECONDS: f64 = 0.05;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Edge {
    In,
    Out,
}

/// A keep as normalized: absolute time bounds plus the frontend-only disabled
/// flag.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Keep {
    pub start: f64,
    pub end: f64,
    pub disabled: bool,
}

/// A canonical cutlist interval that may still carry the editorial `disabled`
/// flag. `CutKind` keeps its AutoCut serde representation (`"keep"`/`"remove"`,
/// lowercase).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct EditCut {
    pub start: f64,
    pub end: f64,
    pub kind: CutKind,
    #[serde(default)]
    pub disabled: bool,
}

/// Canonical editorial cutlist: tiles the source, every interval is either a
/// keep (possibly disabled) or a remove. This is the single representation
/// the Python sidecar, this crate, and eventually the Tauri shell all speak.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EditCutList {
    pub source_duration: f64,
    pub intervals: Vec<EditCut>,
}

impl EditCutList {
    /// Drop the editorial flag into the export shape the Rust exporters use.
    /// Callers are expected to have run `resolve_for_export` first, which
    /// turns every disabled keep into a remove; this conversion is then a pure
    /// erasure.
    pub fn into_cutlist(&self) -> CutList {
        CutList {
            source_duration: self.source_duration,
            intervals: self
                .intervals
                .iter()
                .map(|c| Cut {
                    start: c.start,
                    end: c.end,
                    kind: c.kind,
                })
                .collect(),
        }
    }
}

/// Clamp a proposed new in/out point for `keeps[index]` so it stays between
/// its neighbours, inside [0, duration], and at least MIN_KEEP_SECONDS long.
pub fn clamp_keep_edge(
    keeps: &[Keep],
    index: usize,
    edge: Edge,
    proposed: f64,
    duration: f64,
) -> f64 {
    let Some(own) = keeps.get(index) else {
        return proposed;
    };
    match edge {
        Edge::In => {
            let floor = if index > 0 { keeps[index - 1].end } else { 0.0 };
            let ceil = own.end - MIN_KEEP_SECONDS;
            proposed.clamp(floor, ceil)
        }
        Edge::Out => {
            let floor = own.start + MIN_KEEP_SECONDS;
            let ceil = if index < keeps.len() - 1 {
                keeps[index + 1].start
            } else {
                duration
            };
            proposed.clamp(floor, ceil)
        }
    }
}

/// Clip keeps to [0, duration], drop degenerate ones, sort by start, and fuse
/// the ones that genuinely overlap.
///
/// Touching is not overlapping: two keeps sharing a boundary stay two keeps.
pub fn normalize_keeps(input: &[(f64, f64, bool)], duration: f64) -> Vec<Keep> {
    let mut clipped: Vec<Keep> = input
        .iter()
        .map(|&(start, end, disabled)| Keep {
            start: start.max(0.0).min(duration),
            end: end.max(0.0).min(duration),
            disabled,
        })
        .filter(|k| k.end - k.start > DEGENERATE_SECONDS)
        .collect();
    clipped.sort_by(|a, b| a.start.total_cmp(&b.start));

    let mut merged: Vec<Keep> = Vec::new();
    for k in clipped {
        if let Some(last) = merged.last_mut() {
            if k.start < last.end {
                last.end = last.end.max(k.end);
                // Active content wins over a disabled keep it overlaps.
                last.disabled = last.disabled && k.disabled;
                continue;
            }
        }
        merged.push(k);
    }
    merged
}

/// Everything an export will drop: real removes plus disabled keeps.
pub fn omitted_intervals(cutlist: &EditCutList) -> Vec<EditCut> {
    cutlist
        .intervals
        .iter()
        .filter(|c| c.kind == CutKind::Remove || (c.kind == CutKind::Keep && c.disabled))
        .copied()
        .collect()
}

/// Where playback should jump to from `time`, or None to keep rolling.
/// `lookahead` pre-empts an upcoming omitted interval so the dead frame is
/// never rendered; pass 0 while paused.
pub fn skip_target_for(omitted: &[(f64, f64)], time: f64, lookahead: f64) -> Option<f64> {
    // First interval that hasn't already ended.
    let mut lo = 0usize;
    let mut hi = omitted.len();
    while lo < hi {
        let mid = (lo + hi) >> 1;
        if omitted[mid].1 > time {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    for c in [omitted.get(lo), omitted.get(lo + 1)].into_iter().flatten() {
        if time >= c.0 && time < c.1 - SKIP_SETTLED_SECONDS {
            return Some(c.1);
        }
        if time < c.0 && c.0 - time < lookahead {
            return Some(c.1);
        }
    }
    None
}

fn enabled(keeps: &[Keep]) -> Vec<&Keep> {
    keeps.iter().filter(|k| !k.disabled).collect()
}

/// Where "previous cut" should seek to, or None.
pub fn previous_keep_target(keeps: &[Keep], time: f64) -> Option<f64> {
    let active = enabled(keeps);
    if active.is_empty() {
        return None;
    }
    if let Some(idx) = active.iter().position(|k| time >= k.start && time < k.end) {
        if time - active[idx].start > RESTART_WINDOW_SECONDS {
            return Some(active[idx].start);
        }
        return Some(if idx > 0 {
            active[idx - 1].start
        } else {
            active[idx].start
        });
    }
    let mut previous = None;
    for k in &active {
        if k.end <= time {
            previous = Some(k.start);
        } else {
            break;
        }
    }
    Some(previous.unwrap_or_else(|| active[0].start))
}

/// Where "next cut" should seek to, or None. Past the last keep runs out to
/// `duration`.
pub fn next_keep_target(keeps: &[Keep], time: f64, duration: f64) -> Option<f64> {
    let active = enabled(keeps);
    if active.is_empty() {
        return None;
    }
    if let Some(upcoming) = active.iter().find(|k| k.start > time + NEXT_EPSILON_SECONDS) {
        return Some(upcoming.start);
    }
    if let Some(current) = active.iter().find(|k| time >= k.start && time < k.end) {
        return Some(current.end);
    }
    Some(duration)
}

/// Turn normalized keeps into a canonical cutlist that tiles [0, duration]:
/// every gap becomes a remove, keeps carry their disabled flag through.
pub fn tile_cutlist(keeps: &[Keep], duration: f64) -> EditCutList {
    let mut intervals = Vec::new();
    let mut cursor = 0.0;
    for k in keeps {
        if k.start > cursor {
            intervals.push(EditCut {
                start: cursor,
                end: k.start,
                kind: CutKind::Remove,
                disabled: false,
            });
        }
        intervals.push(EditCut {
            start: k.start,
            end: k.end,
            kind: CutKind::Keep,
            disabled: k.disabled,
        });
        cursor = k.end;
    }
    if cursor < duration {
        intervals.push(EditCut {
            start: cursor,
            end: duration,
            kind: CutKind::Remove,
            disabled: false,
        });
    }
    EditCutList {
        source_duration: duration,
        intervals,
    }
}

/// Collapse disabled keeps into the surrounding removes so exporters see a
/// clean keep/remove cutlist. Coalesces adjacent removes closer than 1e-6.
/// The result carries no disabled flags and converts losslessly to a bounded
/// `cutlist::CutList`.
pub fn resolve_for_export(cutlist: &EditCutList) -> EditCutList {
    let mut out: Vec<EditCut> = Vec::new();
    for c in &cutlist.intervals {
        let next = EditCut {
            start: c.start,
            end: c.end,
            kind: if c.disabled && c.kind == CutKind::Keep {
                CutKind::Remove
            } else {
                c.kind
            },
            disabled: false,
        };
        if let Some(last) = out.last_mut() {
            if last.kind == CutKind::Remove
                && next.kind == CutKind::Remove
                && (last.end - next.start).abs() < 1e-6
            {
                last.end = last.end.max(next.end);
                continue;
            }
        }
        out.push(next);
    }
    EditCutList {
        source_duration: cutlist.source_duration,
        intervals: out,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn editcut(start: f64, end: f64, kind: CutKind, disabled: bool) -> EditCut {
        EditCut {
            start,
            end,
            kind,
            disabled,
        }
    }

    // ---- omitted_intervals ----

    #[test]
    fn collects_removes_and_disabled_keeps_and_nothing_else() {
        let cl = EditCutList {
            source_duration: 12.0,
            intervals: vec![
                editcut(0.0, 2.0, CutKind::Remove, false),
                editcut(2.0, 4.0, CutKind::Keep, false),
                editcut(4.0, 6.0, CutKind::Keep, true),
                editcut(6.0, 8.0, CutKind::Remove, false),
                editcut(8.0, 12.0, CutKind::Keep, false),
            ],
        };
        assert_eq!(
            omitted_intervals(&cl),
            vec![
                editcut(0.0, 2.0, CutKind::Remove, false),
                editcut(4.0, 6.0, CutKind::Keep, true),
                editcut(6.0, 8.0, CutKind::Remove, false),
            ]
        );
    }

    // ---- skip_target_for ----

    fn omitted() -> Vec<(f64, f64)> {
        vec![(2.0, 3.0), (7.0, 9.0), (20.0, 24.0)]
    }

    #[test]
    fn jumps_out_of_an_interval_the_playhead_landed_inside() {
        assert_eq!(skip_target_for(&omitted(), 7.5, 0.08), Some(9.0));
    }

    #[test]
    fn prerolls_over_an_interval_so_its_first_frame_never_renders() {
        assert_eq!(skip_target_for(&omitted(), 6.95, 0.08), Some(9.0));
    }

    #[test]
    fn stays_put_when_the_next_interval_is_still_far_away() {
        assert_eq!(skip_target_for(&omitted(), 5.0, 0.08), None);
    }

    #[test]
    fn zero_lookahead_disables_preroll_for_the_paused_case() {
        assert_eq!(skip_target_for(&omitted(), 6.95, 0.0), None);
    }

    #[test]
    fn zero_lookahead_still_escapes_an_interval_the_playhead_is_inside() {
        assert_eq!(skip_target_for(&omitted(), 7.5, 0.0), Some(9.0));
    }

    #[test]
    fn does_not_refire_on_the_last_ms_of_an_interval_it_is_leaving() {
        assert_eq!(skip_target_for(&omitted(), 8.999, 0.08), None);
    }

    #[test]
    fn catches_the_following_interval_when_leaving_one_that_abuts_it() {
        let abutting = vec![(1.0, 2.0), (2.01, 3.0)];
        assert_eq!(skip_target_for(&abutting, 1.997, 0.08), Some(3.0));
    }

    #[test]
    fn returns_none_past_the_last_interval_or_with_nothing_omitted() {
        assert_eq!(skip_target_for(&omitted(), 30.0, 0.08), None);
        assert_eq!(skip_target_for(&[], 5.0, 0.08), None);
    }

    // ---- previous / next ----

    fn nk() -> Vec<Keep> {
        normalize_keeps(
            &[(0.0, 4.0, false), (6.0, 10.0, false), (12.0, 16.0, false)],
            20.0,
        )
    }

    #[test]
    fn previous_restarts_the_current_keep_when_well_into_it() {
        assert_eq!(previous_keep_target(&nk(), 8.0), Some(6.0));
    }

    #[test]
    fn previous_steps_back_when_barely_into_the_current_keep() {
        assert_eq!(previous_keep_target(&nk(), 6.2), Some(0.0));
    }

    #[test]
    fn previous_stays_put_at_the_very_first_keep() {
        assert_eq!(previous_keep_target(&nk(), 0.2), Some(0.0));
    }

    #[test]
    fn previous_from_inside_a_gap_lands_on_the_keep_before_it() {
        assert_eq!(previous_keep_target(&nk(), 11.0), Some(6.0));
    }

    #[test]
    fn previous_skips_a_disabled_keep() {
        let with_disabled = normalize_keeps(
            &[(0.0, 4.0, false), (6.0, 10.0, true), (12.0, 16.0, false)],
            20.0,
        );
        assert_eq!(previous_keep_target(&with_disabled, 12.2), Some(0.0));
    }

    #[test]
    fn previous_returns_none_when_nothing_is_enabled() {
        let all_disabled = normalize_keeps(&[(0.0, 4.0, true)], 20.0);
        assert_eq!(previous_keep_target(&all_disabled, 2.0), None);
    }

    #[test]
    fn next_jumps_to_the_next_keeps_in_point() {
        assert_eq!(next_keep_target(&nk(), 2.0, 20.0), Some(6.0));
    }

    #[test]
    fn next_from_inside_a_gap_jumps_to_the_upcoming_keep() {
        assert_eq!(next_keep_target(&nk(), 5.0, 20.0), Some(6.0));
    }

    #[test]
    fn next_runs_to_the_end_of_the_current_keep_when_it_is_the_last() {
        assert_eq!(next_keep_target(&nk(), 13.0, 20.0), Some(16.0));
    }

    #[test]
    fn next_runs_to_the_duration_past_the_last_keep() {
        assert_eq!(next_keep_target(&nk(), 18.0, 20.0), Some(20.0));
    }

    #[test]
    fn next_skips_a_disabled_keep() {
        let with_disabled = normalize_keeps(
            &[(0.0, 4.0, false), (6.0, 10.0, true), (12.0, 16.0, false)],
            20.0,
        );
        assert_eq!(next_keep_target(&with_disabled, 2.0, 20.0), Some(12.0));
    }

    // ---- clamp_keep_edge ----

    fn kk() -> Vec<Keep> {
        normalize_keeps(
            &[(1.0, 3.0, false), (5.0, 7.0, false), (9.0, 11.0, false)],
            20.0,
        )
    }

    #[test]
    fn in_edge_cannot_cross_the_previous_keeps_out() {
        assert_eq!(clamp_keep_edge(&kk(), 1, Edge::In, 2.0, 20.0), 3.0);
    }

    #[test]
    fn in_edge_cannot_swallow_its_own_keep() {
        assert_eq!(
            clamp_keep_edge(&kk(), 1, Edge::In, 99.0, 20.0),
            7.0 - MIN_KEEP_SECONDS
        );
    }

    #[test]
    fn out_edge_cannot_cross_the_next_keeps_in() {
        assert_eq!(clamp_keep_edge(&kk(), 1, Edge::Out, 10.0, 20.0), 9.0);
    }

    #[test]
    fn out_edge_cannot_swallow_its_own_keep() {
        assert_eq!(
            clamp_keep_edge(&kk(), 1, Edge::Out, -5.0, 20.0),
            5.0 + MIN_KEEP_SECONDS
        );
    }

    #[test]
    fn first_keeps_in_floors_at_zero_and_last_keeps_out_ceils_at_duration() {
        assert_eq!(clamp_keep_edge(&kk(), 0, Edge::In, -4.0, 20.0), 0.0);
        assert_eq!(clamp_keep_edge(&kk(), 2, Edge::Out, 40.0, 20.0), 20.0);
    }

    #[test]
    fn an_out_of_range_index_returns_the_proposal_unchanged() {
        assert_eq!(clamp_keep_edge(&kk(), 7, Edge::In, 4.2, 20.0), 4.2);
    }

    // ---- normalize_keeps ----

    #[test]
    fn keeps_two_touching_intervals_separate() {
        let out = normalize_keeps(&[(0.0, 5.0, false), (5.0, 9.0, false)], 10.0);
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn merges_genuinely_overlapping_intervals() {
        let out = normalize_keeps(&[(0.0, 5.0, false), (4.0, 9.0, false)], 10.0);
        assert_eq!(out, vec![Keep { start: 0.0, end: 9.0, disabled: false }]);
    }

    #[test]
    fn does_not_re_enable_a_disabled_keep_that_merely_touches_an_active_one() {
        let out = normalize_keeps(&[(0.0, 5.0, false), (5.0, 9.0, true)], 10.0);
        assert_eq!(
            out,
            vec![
                Keep { start: 0.0, end: 5.0, disabled: false },
                Keep { start: 5.0, end: 9.0, disabled: true },
            ]
        );
    }

    #[test]
    fn active_content_wins_when_an_active_keep_truly_overlaps_a_disabled_one() {
        let out = normalize_keeps(&[(0.0, 5.0, false), (4.0, 9.0, true)], 10.0);
        assert_eq!(out, vec![Keep { start: 0.0, end: 9.0, disabled: false }]);
    }

    #[test]
    fn clips_to_duration_and_drops_degenerate_intervals() {
        let out = normalize_keeps(
            &[(-3.0, 2.0, false), (4.0, 4.005, false), (8.0, 40.0, false)],
            10.0,
        );
        assert_eq!(
            out,
            vec![
                Keep { start: 0.0, end: 2.0, disabled: false },
                Keep { start: 8.0, end: 10.0, disabled: false },
            ]
        );
    }

    #[test]
    fn sorts_unordered_input() {
        let out = normalize_keeps(&[(6.0, 8.0, false), (1.0, 3.0, false)], 10.0);
        assert_eq!(out.iter().map(|k| k.start).collect::<Vec<_>>(), vec![1.0, 6.0]);
    }

    // ---- tile_cutlist ----

    #[test]
    fn fills_gaps_with_removes_and_tiles_zero_to_duration() {
        let keeps = normalize_keeps(&[(2.0, 4.0, false), (6.0, 8.0, false)], 10.0);
        let cl = tile_cutlist(&keeps, 10.0);
        assert_eq!(cl.source_duration, 10.0);
        let kinds: Vec<_> = cl.intervals.iter().map(|c| (c.start, c.end, c.kind)).collect();
        assert_eq!(
            kinds,
            vec![
                (0.0, 2.0, CutKind::Remove),
                (2.0, 4.0, CutKind::Keep),
                (4.0, 6.0, CutKind::Remove),
                (6.0, 8.0, CutKind::Keep),
                (8.0, 10.0, CutKind::Remove),
            ]
        );
    }

    #[test]
    fn carries_the_disabled_flag_onto_the_keep_interval() {
        let keeps = normalize_keeps(&[(0.0, 10.0, true)], 10.0);
        let cl = tile_cutlist(&keeps, 10.0);
        assert_eq!(cl.intervals.len(), 1);
        assert!(cl.intervals[0].disabled);
    }

    #[test]
    fn emits_adjacent_keeps_with_no_remove_between_them() {
        let keeps = normalize_keeps(&[(0.0, 5.0, false), (5.0, 10.0, false)], 10.0);
        let cl = tile_cutlist(&keeps, 10.0);
        assert_eq!(cl.intervals.len(), 2);
        assert!(cl.intervals.iter().all(|c| c.kind == CutKind::Keep));
    }

    // ---- resolve_for_export ----

    #[test]
    fn turns_disabled_keeps_into_removes_and_coalesces_with_neighbours() {
        let cl = resolve_for_export(&EditCutList {
            source_duration: 10.0,
            intervals: vec![
                editcut(0.0, 2.0, CutKind::Remove, false),
                editcut(2.0, 4.0, CutKind::Keep, true),
                editcut(4.0, 6.0, CutKind::Remove, false),
                editcut(6.0, 10.0, CutKind::Keep, false),
            ],
        });
        assert_eq!(cl.intervals.len(), 2);
        assert_eq!(cl.intervals[0].kind, CutKind::Remove);
        assert_eq!((cl.intervals[0].start, cl.intervals[0].end), (0.0, 6.0));
        assert_eq!(cl.intervals[1].kind, CutKind::Keep);
    }

    #[test]
    fn strips_the_frontend_only_disabled_flag_from_surviving_keeps() {
        let cl = resolve_for_export(&EditCutList {
            source_duration: 4.0,
            intervals: vec![editcut(0.0, 4.0, CutKind::Keep, false)],
        });
        assert!(!cl.intervals[0].disabled);
    }

    #[test]
    fn into_cutlist_erases_disabled_and_preserves_bounds() {
        let cl = resolve_for_export(&EditCutList {
            source_duration: 10.0,
            intervals: vec![editcut(1.0, 3.0, CutKind::Keep, false)],
        });
        let plain: CutList = cl.into_cutlist();
        assert_eq!(plain.intervals.len(), 1);
        assert_eq!((plain.intervals[0].start, plain.intervals[0].end), (1.0, 3.0));
    }
}