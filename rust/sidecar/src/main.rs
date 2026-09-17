//! SelekyT mechanical sidecar.
//!
//! A tiny JSON-RPC worker over stdin/stdout: one JSON request per line, one
//! JSON response per line, EOF terminates. It is how the Python intelligence
//! core (and later the Tauri shell) calls the Rust mechanical layer without a
//! full FFI boundary — the Python parent spawns this binary and sends it the
//! canonical cut/timeline JSON.
//!
//! Request:  {"op": "<op>", ...params}
//! Success:  {"ok": true, "data": ...}
//! Error:    {"ok": false, "error": "..."}   (uses AutoCut-style {e:#} chains)
//!
//! Ops:
//!   ping                  -> {"pong": true, "version": .., "backend": ..}
//!   probe {path}          -> MediaInfo (ffprobe wrapper)
//!   score {path}          -> per-32ms-chunk speech scores + provenance
//!   extract_pcm {path}    -> sample count + duration (avoids shipping samples)
//!   waveform {path, bins} -> amplitude envelope Vec<f32>
//!   segment {scores,...}  -> SpeechSegments (hysteresis + min sil/speech)
//!   cutlist_from_segments {segments, source_duration, pad} -> CutList
//!   normalize_keeps {keeps, duration}
//!   tile_cutlist {keeps, duration}          -> EditCutList
//!   resolve_for_export {cutlist}            -> EditCutList (no disabled)
//!   cutlist_to_export {cutlist}             -> CutList (erasures disabled)
//!   ensure_exportable {cutlist}
//!   build_concat_list {source, cutlist}
//!   render_fcpxml {fps,title,reference,linked,cutlist}
//!   detect_rate {fps, drop_frame}
//!   parse_smpte {tc, fps}
//!   seconds_to_rational {seconds, fps, drop_frame}

use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::{anyhow, Context, Result};
use serde::Deserialize;
use serde_json::{Value, json};

use selekyt_core::cutlist::{CutList, SpeechSegment};
use selekyt_core::editalgebra::{self, EditCutList, Keep};
use selekyt_core::export_fcpxml::{FcpxmlParams, Track};
use selekyt_core::timecode::{detect_rate, parse_smpte, seconds_to_rational};

/// Wire-format stand-in for `SpeechSegment` (which carries no serde derive).
#[derive(Deserialize)]
struct SegReq {
    start: f64,
    end: f64,
}

fn ffmpeg_bin() -> PathBuf {
    std::env::var_os("SELEKYT_FFMPEG")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("ffmpeg"))
}

fn ffprobe_bin() -> PathBuf {
    std::env::var_os("SELEKYT_FFPROBE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("ffprobe"))
}

fn params(req: &Value) -> Result<&Value> {
    Ok(req.get("params").unwrap_or(&Value::Null))
}

fn num(params: &Value, key: &str) -> Result<f64> {
    params
        .get(key)
        .and_then(Value::as_f64)
        .ok_or_else(|| anyhow!("'{key}' must be a number"))
}

fn str_key(params: &Value, key: &str) -> Result<String> {
    params
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| anyhow!("'{key}' must be a string"))
}

fn optional_str(params: &Value, key: &str) -> Option<String> {
    params.get(key).and_then(Value::as_str).map(str::to_string)
}

#[derive(Deserialize)]
struct TrackReq {
    path: Option<PathBuf>,
    name: String,
    duration: f64,
    start_timecode: Option<String>,
    has_video: bool,
    has_audio: bool,
    offset: f64,
}

fn to_track<'a>(t: &'a TrackReq) -> Track<'a> {
    Track {
        path: t.path.as_deref(),
        name: &t.name,
        duration: t.duration,
        start_timecode: t.start_timecode.as_deref(),
        has_video: t.has_video,
        has_audio: t.has_audio,
        offset: t.offset,
    }
}

fn handle(op: &str, params: &Value) -> Result<Value> {
    match op {
        "ping" => Ok(json!({
            "pong": true,
            "crate": "selekyt-core",
            "version": env!("CARGO_PKG_VERSION"),
            "vad_backend": selekyt_core::vad_backend(),
            "vad_model": selekyt_core::vad_model(),
        })),
        "probe" => {
            let path = str_key(params, "path")?;
            let info = selekyt_core::probe::probe(&ffprobe_bin(), std::path::Path::new(&path))?;
            serde_json::to_value(info).context("serializing MediaInfo")
        }
        "score" => {
            let path = str_key(params, "path")?;
            let samples = selekyt_core::audio::extract_pcm(&ffmpeg_bin(), std::path::Path::new(&path))
                .with_context(|| format!("extracting PCM from {path}"))?;
            let scores = selekyt_core::score_default(&samples)?;
            Ok(json!({
                "scores": scores,
                "chunk_seconds": selekyt_core::fallback::CHUNK_SECONDS,
                "chunk_size": selekyt_core::fallback::CHUNK_SIZE,
                "sample_rate": selekyt_core::audio::VAD_SAMPLE_RATE,
                "vad_backend": selekyt_core::vad_backend(),
                "vad_model": selekyt_core::vad_model(),
            }))
        }
        "extract_pcm" => {
            let path = str_key(params, "path")?;
            let samples = selekyt_core::audio::extract_pcm(&ffmpeg_bin(), std::path::Path::new(&path))
                .with_context(|| format!("extracting PCM from {path}"))?;
            let sr = selekyt_core::audio::VAD_SAMPLE_RATE as f64;
            Ok(json!({
                "n_samples": samples.len(),
                "duration": samples.len() as f64 / sr,
            }))
        }
        "waveform" => {
            let path = str_key(params, "path")?;
            let bins = params
                .get("bins")
                .and_then(Value::as_u64)
                .unwrap_or(1000) as usize;
            let samples = selekyt_core::audio::extract_pcm(&ffmpeg_bin(), std::path::Path::new(&path))
                .with_context(|| format!("extracting PCM from {path}"))?;
            let env = selekyt_core::waveform::waveform_from_samples(&samples, bins, None)?;
            Ok(json!({ "waveform": env }))
        }
        "segment" => {
            let scores: Vec<f32> = serde_json::from_value(
                params
                    .get("scores")
                    .cloned()
                    .ok_or_else(|| anyhow!("'scores' must be an array"))?,
            )
            .context("parsing scores")?;
            let threshold = params.get("threshold").and_then(Value::as_f64).unwrap_or(0.5) as f32;
            let min_silence_ms = params
                .get("min_silence_ms")
                .and_then(Value::as_u64)
                .unwrap_or(100) as u32;
            let min_speech_ms = params
                .get("min_speech_ms")
                .and_then(Value::as_u64)
                .unwrap_or(150) as u32;
            // Project-time second the scored audio begins at. Non-zero when
            // detection ran on a linked track that rolled after the reference;
            // the cutlist is always expressed in project time (AutoCut
            // DetectParams::source_offset).
            let time_offset = params.get("time_offset").and_then(Value::as_f64).unwrap_or(0.0);
            let vp = selekyt_core::vad::VadParams {
                threshold,
                min_silence_ms,
                min_speech_ms,
            };
            let segs = selekyt_core::vad::segments_from_scores(&scores, vp, time_offset);
            Ok(serde_json::to_value(
                segs.into_iter()
                    .map(|s| json!({"start": s.start, "end": s.end, "score": 1.0}))
                    .collect::<Vec<_>>(),
            )
            .context("serializing segments")?)
        }
        "cutlist_from_segments" => {
            let segments: Vec<SegReq> = serde_json::from_value(
                params
                    .get("segments")
                    .cloned()
                    .ok_or_else(|| anyhow!("'segments' must be an array"))?,
            )
            .context("parsing segments")?;
            let segments: Vec<SpeechSegment> = segments
                .into_iter()
                .map(|s| SpeechSegment { start: s.start, end: s.end })
                .collect();
            let source_duration = num(params, "source_duration")?;
            let pad = params.get("pad").and_then(Value::as_f64).unwrap_or(0.0);
            let cl = CutList::from_speech_segments(&segments, source_duration, pad);
            serde_json::to_value(cl).context("serializing cutlist")
        }
        "normalize_keeps" => {
            let duration = num(params, "duration")?;
            let keeps: Vec<KeepReq> = serde_json::from_value(
                params
                    .get("keeps")
                    .cloned()
                    .ok_or_else(|| anyhow!("'keeps' must be an array"))?,
            )
            .context("parsing keeps")?;
            let raw: Vec<(f64, f64, bool)> = keeps
                .into_iter()
                .map(|k| (k.start, k.end, k.disabled))
                .collect();
            let out = editalgebra::normalize_keeps(&raw, duration);
            serde_json::to_value(
                out.iter()
                    .map(|k| json!({"start": k.start, "end": k.end, "disabled": k.disabled}))
                    .collect::<Vec<_>>(),
            )
            .context("serializing keeps")
        }
        "tile_cutlist" => {
            let duration = num(params, "duration")?;
            let keeps: Vec<KeepReq> = serde_json::from_value(
                params
                    .get("keeps")
                    .cloned()
                    .ok_or_else(|| anyhow!("'keeps' must be an array"))?,
            )
            .context("parsing keeps")?;
            let keeps: Vec<Keep> = keeps
                .into_iter()
                .map(|k| Keep { start: k.start, end: k.end, disabled: k.disabled })
                .collect();
            serde_json::to_value(editalgebra::tile_cutlist(&keeps, duration))
                .context("serializing cutlist")
        }
        "resolve_for_export" => {
            let cutlist: EditCutList = serde_json::from_value(
                params
                    .get("cutlist")
                    .cloned()
                    .ok_or_else(|| anyhow!("'cutlist' must be an object"))?,
            )
            .context("parsing cutlist")?;
            serde_json::to_value(editalgebra::resolve_for_export(&cutlist))
                .context("serializing cutlist")
        }
        "cutlist_to_export" => {
            let cutlist: EditCutList = serde_json::from_value(
                params
                    .get("cutlist")
                    .cloned()
                    .ok_or_else(|| anyhow!("'cutlist' must be an object"))?,
            )
            .context("parsing cutlist")?;
            serde_json::to_value(cutlist.into_cutlist()).context("serializing cutlist")
        }
        "ensure_exportable" => {
            let cutlist: CutList = serde_json::from_value(
                params
                    .get("cutlist")
                    .cloned()
                    .ok_or_else(|| anyhow!("'cutlist' must be an object"))?,
            )
            .context("parsing cutlist")?;
            let total = cutlist.total_kept_duration();
            match cutlist.ensure_exportable() {
                Ok(()) => Ok(json!({"exportable": true, "total_kept_duration": total})),
                Err(e) => Ok(json!({"exportable": false, "reason": e.to_string(), "total_kept_duration": total})),
            }
        }
        "build_concat_list" => {
            let source = str_key(params, "source")?;
            let cutlist: CutList = serde_json::from_value(
                params
                    .get("cutlist")
                    .cloned()
                    .ok_or_else(|| anyhow!("'cutlist' must be an object"))?,
            )
            .context("parsing cutlist")?;
            let list = selekyt_core::export_mp4::build_concat_list(
                std::path::Path::new(&source),
                &cutlist,
            );
            Ok(json!({ "list": list }))
        }
        "render_fcpxml" => {
            let fps = num(params, "fps")?;
            let title = str_key(params, "title")?;
            let cutlist: CutList = serde_json::from_value(
                params
                    .get("cutlist")
                    .cloned()
                    .ok_or_else(|| anyhow!("'cutlist' must be an object"))?,
            )
            .context("parsing cutlist")?;
            let reference: TrackReq = serde_json::from_value(
                params
                    .get("reference")
                    .cloned()
                    .ok_or_else(|| anyhow!("'reference' must be an object"))?,
            )
            .context("parsing reference track")?;
            let linked: Vec<TrackReq> = serde_json::from_value(
                params.get("linked").cloned().unwrap_or(Value::Array(vec![])),
            )
            .context("parsing linked tracks")?;
            let ref_track = to_track(&reference);
            let linked_tracks: Vec<Track> = linked.iter().map(to_track).collect();
            let fxp = FcpxmlParams {
                fps,
                title: &title,
                reference: ref_track,
                linked: &linked_tracks,
            };
            let xml = selekyt_core::export_fcpxml::render(&cutlist, fxp)?;
            Ok(json!({ "xml": xml }))
        }
        "detect_rate" => {
            let fps = num(params, "fps")?;
            let df = params.get("drop_frame").and_then(Value::as_bool).unwrap_or(false);
            let r = detect_rate(fps, df);
            Ok(json!({
                "nominal_fps": r.nominal_fps,
                "is_drop_frame": r.is_drop_frame,
                "frame_duration_num": r.frame_duration_num,
                "frame_duration_den": r.frame_duration_den,
            }))
        }
        "parse_smpte" => {
            let fps = num(params, "fps")?;
            let tc = optional_str(params, "tc");
            Ok(json!({"seconds": parse_smpte(tc.as_deref(), fps)}))
        }
        "seconds_to_rational" => {
            let seconds = num(params, "seconds")?;
            let fps = num(params, "fps")?;
            let df = params.get("drop_frame").and_then(Value::as_bool).unwrap_or(false);
            let r = detect_rate(fps, df);
            Ok(json!({"rational": seconds_to_rational(seconds, &r)}))
        }
        other => Err(anyhow!("unknown op '{other}'")),
    }
}

fn process_line(line: &str) -> String {
    let req: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => {
            return json!({"ok": false, "error": format!("invalid JSON: {e:#}")}).to_string();
        }
    };
    let op = req
        .get("op")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let p = match params(&req) {
        Ok(p) => p,
        Err(e) => return json!({"ok": false, "error": format!("{e:#}")}).to_string(),
    };
    match handle(&op, p) {
        Ok(data) => json!({"ok": true, "op": op, "data": data}).to_string(),
        Err(e) => json!({"ok": false, "op": op, "error": format!("{e:#}")}).to_string(),
    }
}

fn main() -> ExitCode {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(e) => {
                let _ = writeln!(out, "{}", json!({"ok": false, "error": format!("stdin: {e:#}")}));
                let _ = out.flush();
                return ExitCode::FAILURE;
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        let response = process_line(&line);
        if writeln!(out, "{response}").is_err() {
            return ExitCode::FAILURE;
        }
        let _ = out.flush();
    }
    ExitCode::SUCCESS
}

#[derive(Deserialize)]
struct KeepReq {
    start: f64,
    end: f64,
    #[serde(default)]
    disabled: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn call(op: &str, params: Value) -> Value {
        let line = json!({"op": op, "params": params}).to_string();
        serde_json::from_str(&process_line(&line)).expect("valid JSON response")
    }

    fn ok(v: &Value) -> Value {
        assert_eq!(v.get("ok").and_then(Value::as_bool), Some(true), "{v}");
        v.get("data").cloned().unwrap_or(Value::Null)
    }

    #[test]
    fn ping_reports_backend() {
        let data = ok(&call("ping", json!({})));
        assert_eq!(data.get("pong").and_then(Value::as_bool), Some(true));
        assert!(data.get("vad_backend").is_some());
    }

    #[test]
    fn unknown_op_is_a_clean_error() {
        let line = json!({"op": "nope"}).to_string();
        let v: Value = serde_json::from_str(&process_line(&line)).unwrap();
        assert_eq!(v.get("ok").and_then(Value::as_bool), Some(false));
        assert!(v.get("error").and_then(Value::as_str).unwrap().contains("unknown op"));
    }

    #[test]
    fn bad_json_is_a_clean_error() {
        let v: Value = serde_json::from_str(&process_line("not json")).unwrap();
        assert_eq!(v.get("ok").and_then(Value::as_bool), Some(false));
    }

    #[test]
    fn segment_matches_the_hysteresis_contract() {
        // 0.9,0.4,0.9 at threshold 0.5: the 0.4 dip sits above release 0.35,
        // so it must NOT split the segment.
        let data = ok(&call(
            "segment",
            json!({
                "scores": [0.9, 0.4, 0.9],
                "threshold": 0.5, "min_silence_ms": 0, "min_speech_ms": 0,
            }),
        ));
        let segs = data.as_array().unwrap();
        assert_eq!(segs.len(), 1);
        assert!((segs[0]["start"].as_f64().unwrap() - 0.0).abs() < 1e-9);
        assert!((segs[0]["end"].as_f64().unwrap() - 3.0 * 0.032).abs() < 1e-9);
    }

    #[test]
    fn cutlist_from_segments_pads_clamps_and_merges() {
        let data = ok(&call(
            "cutlist_from_segments",
            json!({
                "segments": [{"start": 1.0, "end": 2.0}, {"start": 2.4, "end": 3.0}],
                "source_duration": 5.0,
                "pad": 0.3,
            }),
        ));
        assert_eq!(data["source_duration"].as_f64().unwrap(), 5.0);
        let keeps: Vec<_> = data["intervals"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|c| c["kind"] == "keep")
            .collect();
        assert_eq!(keeps.len(), 1, "overlapping pads merge");
        assert_eq!(keeps[0]["start"].as_f64().unwrap(), 0.7);
        assert_eq!(keeps[0]["end"].as_f64().unwrap(), 3.3);
    }

    #[test]
    fn resolve_for_export_turns_disabled_keeps_into_coalesced_removes() {
        let data = ok(&call(
            "resolve_for_export",
            json!({
                "cutlist": {
                    "source_duration": 10.0,
                    "intervals": [
                        {"start": 0.0, "end": 2.0, "kind": "remove"},
                        {"start": 2.0, "end": 4.0, "kind": "keep", "disabled": true},
                        {"start": 4.0, "end": 6.0, "kind": "remove"},
                        {"start": 6.0, "end": 10.0, "kind": "keep"},
                    ],
                }
            }),
        ));
        let intervals = data["intervals"].as_array().unwrap();
        assert_eq!(intervals.len(), 2);
        assert_eq!(intervals[0]["kind"], "remove");
        assert_eq!(intervals[0]["end"].as_f64().unwrap(), 6.0);
        assert_eq!(intervals[1]["kind"], "keep");
        assert!(!intervals[1].get("disabled").map(Value::as_bool).unwrap_or(Some(true)).unwrap());
    }

    #[test]
    fn ensure_exportable_rejects_an_empty_timeline() {
        let data = ok(&call(
            "ensure_exportable",
            json!({
                "cutlist": {
                    "source_duration": 4.0,
                    "intervals": [{"start": 0.0, "end": 4.0, "kind": "remove"}],
                }
            }),
        ));
        assert!(!data["exportable"].as_bool().unwrap());
    }

    #[test]
    fn build_concat_list_emits_keep_entries_only() {
        let data = ok(&call(
            "build_concat_list",
            json!({
                "source": "/tmp/clip.mp4",
                "cutlist": {
                    "source_duration": 10.0,
                    "intervals": [
                        {"start": 0.0, "end": 1.0, "kind": "keep"},
                        {"start": 1.0, "end": 2.0, "kind": "remove"},
                        {"start": 2.0, "end": 3.5, "kind": "keep"},
                    ],
                }
            }),
        ));
        let list = data["list"].as_str().unwrap();
        assert!(list.starts_with("ffconcat version 1.0\n"));
        assert_eq!(list.matches("file '/tmp/clip.mp4'").count(), 2);
        assert!(list.contains("inpoint 2.000000"));
    }

    #[test]
    fn render_fcpxml_requires_exportable_cutlist() {
        let v = call(
            "render_fcpxml",
            json!({
                "fps": 25.0, "title": "demo",
                "cutlist": {"source_duration": 3.0, "intervals": [{"start": 0.0, "end": 3.0, "kind": "remove"}]},
                "reference": {"name": "ref", "duration": 3.0, "has_video": true, "has_audio": true, "offset": 0.0},
                "linked": [],
            }),
        );
        assert_eq!(v.get("ok").and_then(Value::as_bool), Some(false));
        assert!(v.get("error").and_then(Value::as_str).unwrap().contains("nothing to keep"));
    }

    #[test]
    fn render_fcpxml_renders_tracks_and_drop_frame() {
        let data = ok(&call(
            "render_fcpxml",
            json!({
                "fps": 29.97, "title": "demo",
                "cutlist": {"source_duration": 3.0, "intervals": [
                    {"start": 0.0, "end": 1.0, "kind": "keep"},
                    {"start": 1.0, "end": 2.0, "kind": "remove"},
                    {"start": 2.0, "end": 3.0, "kind": "keep"},
                ]},
                "reference": {"name": "cam", "duration": 3.0, "start_timecode": "01:00:00;00", "has_video": true, "has_audio": true, "offset": 0.0},
                "linked": [{"name": "lav", "duration": 3.0, "has_video": false, "has_audio": true, "offset": 0.0}],
            }),
        ));
        let xml = data["xml"].as_str().unwrap();
        assert!(xml.contains("tcFormat=\"DF\""), "{xml}");
        assert!(xml.contains("frameDuration=\"1001/30000s\""));
        assert!(xml.contains("lane=\"-1\""), "{xml}");
        // 2 keeps x 2 assets (camera reference + linked lav audio) = 4 clip tags.
        assert_eq!(xml.matches("<asset-clip").count(), 4, "{xml}");
    }

    #[test]
    fn timecode_ops_roundtrip_drop_frame() {
        let data = ok(&call("detect_rate", json!({"fps": 29.97, "drop_frame": true})));
        assert_eq!(data["nominal_fps"].as_u64().unwrap(), 30);
        assert!(data["is_drop_frame"].as_bool().unwrap());
        assert_eq!(data["frame_duration_den"].as_u64().unwrap(), 30000);
        let d = ok(&call("parse_smpte", json!({"tc": "01:00:00;00", "fps": 30000.0 / 1001.0})));
        assert!((d["seconds"].as_f64().unwrap() - 3600.0).abs() < 0.01);
        let r = ok(&call("seconds_to_rational", json!({"seconds": 1.0, "fps": 29.97, "drop_frame": true})));
        assert_eq!(r["rational"].as_str().unwrap(), "30030/30000s");
    }

    #[test]
    fn probe_needs_a_file_and_fails_cleanly() {
        let v = call("probe", json!({"path": "/nonexistent/nope.mp4"}));
        assert_eq!(v.get("ok").and_then(Value::as_bool), Some(false));
    }
}