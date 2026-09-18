//! SelekyT desktop shell.
//!
//! A deliberately thin webview host. It owns the lifecycle of exactly two
//! bundled sidecars and exposes their JSON-RPC envelope to the frontend as one
//! Tauri command:
//!
//!   - `selekyt-mech`  (this repo's Rust mechanical core, `rust/sidecar`)
//!   - `selekyt-python` (a `python-build-standalone` interpreter renamed as a
//!     Tauri sidecar binary, hosting `selekyt/server.py` — the *same*
//!     intelligence layer the CLI uses, with no duplicated business logic)
//!
//! Both speak `{"op","params"}` -> `{"ok","op","data"|"error"}` over stdio,
//! the contract pinned by tests/test_sidecar_parity.py, so the frontend talks
//! to whichever implementation it wants through one command.
//!
//! ffmpeg/ffprobe are NOT executed by this shell. They are discovered by the
//! Python layer over PATH (see `prepend_bin_to_path`), which keeps the webview's
//! shell permission surface to the two sidecars only.
//!
//! NOTE: this crate has never been compiled on the development host (no Rust
//! toolchain there). Native compilation is the top item in the
//! "VERIFICATION REQUIRED ON" section of docs/packaging.md.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::Child;
use std::sync::Mutex;

use serde_json::Value;
use tauri::{Manager, State};
use tauri_plugin_shell::Command;

const SIDECAR_PYTHON: &str = "selekyt-python";
const SIDECAR_MECH: &str = "selekyt-mech";

/// Per-sidecar stdio handle. The response writer holds the real stdout so the
/// one-JSON-object-per-line wire stays pure, matching selekyt/server.py's own
/// `_progress_to_stderr` convention on the other end of the pipe.
struct Sidecar {
    child: Child,
}

impl Sidecar {
    fn spawn(name: &str, args: &[&str]) -> std::io::Result<Self> {
        let mut cmd = Command::new_sidecar(name)?;
        for a in args {
            cmd = cmd.arg(*a);
        }
        let child = cmd.spawn()?;
        Ok(Sidecar { child })
    }

    fn call(&mut self, op: &str, params: &Value) -> std::io::Result<Value> {
        let request = serde_json::json!({"op": op, "params": params});
        let line = serde_json::to_string(&request)? + "\n";
        {
            let stdin = self
                .child
                .stdin
                .as_mut()
                .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::BrokenPipe, "no stdin"))?;
            stdin.write_all(line.as_bytes())?;
            stdin.flush()?;
        }
        let stdout = self
            .child
            .stdout
            .as_mut()
            .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::BrokenPipe, "no stdout"))?;
        let mut reader = BufReader::new(stdout);
        let mut buf = String::new();
        if reader.read_line(&mut buf)? == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "sidecar closed the pipe before answering",
            ));
        }
        Ok(serde_json::from_str(buf.trim())?)
    }
}

#[derive(Default)]
struct Sidecars(Mutex<HashMap<String, Sidecar>>);

fn target_name(target: &str) -> Result<&'static str, String> {
    match target {
        "python" => Ok(SIDECAR_PYTHON),
        "mech" => Ok(SIDECAR_MECH),
        other => Err(format!(
            "unknown sidecar target '{other}' (expected 'python' or 'mech')"
        )),
    }
}

/// Prepend the bundled `bin/` resource directory to this process's PATH so the
/// Python sidecar's `shutil.which("ffmpeg")` (providers/ffmpeg.py) finds the
/// bundled LGPL ffmpeg/ffprobe without any shell permission on the webview
/// side and without modifying the engine.
fn prepend_bin_to_path(app: &tauri::App) {
    if let Ok(resource_dir) = app.path().resource_dir() {
        let bin = resource_dir.join("bin");
        if bin.is_dir() {
            let key = if cfg!(windows) { "Path" } else { "PATH" };
            let current = std::env::var(key).unwrap_or_default();
            let updated = format!("{}{}{}", bin.display(), std::path::MAIN_SEPARATOR, current);
            std::env::set_var(key, updated);
        }
    }
}

/// One JSON-RPC call to a bundled sidecar. The child is spawned on first use
/// and reused for the lifetime of the app; a dead child is dropped and the
/// caller's error surfaces (the next call respawns it).
fn rpc(state: &Sidecars, target: &str, op: &str, params: &Value) -> Result<Value, String> {
    let name = target_name(target)?;
    let mut map = state.0.lock().map_err(|e| e.to_string())?;
    if !map.contains_key(name) {
        let args: &[&str] = if name == SIDECAR_PYTHON {
            &["-m", "selekyt", "--server"]
        } else {
            &[]
        };
        let sidecar = Sidecar::spawn(name, args).map_err(|e| {
            format!(
                "failed to spawn the '{name}' sidecar: {e} \
                 (bundled under resources/ as an externalBin binary)"
            )
        })?;
        map.insert(name.to_string(), sidecar);
    }
    let entry = map.get_mut(name).ok_or("sidecar vanished")?;
    match entry.call(op, params) {
        Ok(value) => Ok(value),
        Err(e) => {
            // Drop the dead child; the next call respawns it once.
            map.remove(name);
            Err(format!("sidecar '{name}' failed on op '{op}': {e}"))
        }
    }
}

#[tauri::command]
fn selekyt_rpc(
    target: String,
    op: String,
    params: Value,
    state: State<'_, Sidecars>,
) -> Result<Value, String> {
    rpc(&state, &target, &op, &params)
}

/// Ask both sidecars `ping` and report their envelopes, so the frontend can
/// show a single honest "engine ready / engine missing" state.
#[tauri::command]
fn selekyt_health(state: State<'_, Sidecars>) -> Value {
    let mut out = serde_json::json!({"python": null, "mech": null});
    for key in ["python", "mech"] {
        if let Ok(value) = rpc(&state, key, "ping", &serde_json::json!({})) {
            out[key] = value;
        }
    }
    out
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            prepend_bin_to_path(app);
            app.manage(Sidecars::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![selekyt_rpc, selekyt_health])
        .run(tauri::generate_context!())
        .expect("error while running the SelekyT desktop shell");
}
