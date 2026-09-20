//! SelekyT desktop shell.
//!
//! A deliberately thin webview host. It owns the lifecycle of the bundled
//! SelekyT sidecars and exposes their JSON-RPC envelope to the frontend as
//! Tauri commands:
//!
//!   - `selekyt-python` — the *same* intelligence layer the CLI uses
//!     (`selekyt/server.py`, hosted in a bundled interpreter). This is the
//!     engine: transcription, story selection, captions, rendering.
//!   - `selekyt-mech`   — this repo's Rust mechanical core (`rust/sidecar`),
//!     the parity layer, when it has been staged for the target triple.
//!
//! Both speak `{"op","params"}` -> `{"ok","op","data"|"error"}` over stdio,
//! the contract pinned by tests/test_sidecar_parity.py, so the frontend talks
//! to whichever implementation it wants through one command.
//!
//! Progress is not invented here. The engine prints its own stage lines
//! (`[transcribe] model=small`, `[cache] reusing rendered clip 01`, …) to
//! stderr, which this shell captures verbatim and forwards to the webview as
//! `engine-log` events. The UI derives its stage indicators from those lines
//! and never marks a stage complete without a line that says so.
//!
//! ffmpeg/ffprobe are NOT executed by this shell. They are discovered by the
//! Python layer over PATH (see `prepend_bin_to_path`), which keeps the
//! webview's process permission surface to zero: the frontend can only invoke
//! `selekyt_rpc` / `selekyt_health` / `reveal_path`, and the shell itself
//! spawns only declared sidecar binaries.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde_json::Value;
use tauri::{Emitter, Manager, State};

const SIDECAR_PYTHON: &str = "selekyt-python";
const SIDECAR_MECH: &str = "selekyt-mech";

/// Where a bundled sidecar lives under `base`: named `<name>-<target-triple>`,
/// with `.exe` on Windows. This is the same resolution `tauri build` uses when
/// it copies `bundle.externalBin` entries alongside the main binary, so it does
/// not depend on the shell plugin — whose `new_sidecar` is not public in the
/// 2.x line — and keeps the webview's process-permission surface at zero.
pub(crate) fn sidecar_binary(base: &std::path::Path, name: &str) -> std::path::PathBuf {
    let triple = tauri_utils::platform::target_triple().unwrap_or_default();
    let mut file = format!("{name}-{triple}");
    if cfg!(windows) {
        file.push_str(".exe");
    }
    base.join(file)
}

/// As [`sidecar_binary`], resolved relative to this executable. Cargo runs unit
/// tests from a `deps` subdirectory, where sidecars sit one level up alongside
/// the real binary; the installed app has no such indirection.
///
/// `SELEKYT_PYTHON` / `SELEKYT_MECH` override the resolved path. They exist for
/// development and for the integration tests, which point them at a system
/// interpreter instead of a bundled one; in a packaged install they are unset.
fn sidecar_path(name: &str) -> Result<std::path::PathBuf, String> {
    let override_key = match name {
        SIDECAR_PYTHON => "SELEKYT_PYTHON",
        SIDECAR_MECH => "SELEKYT_MECH",
        _ => return Err(format!("unknown sidecar '{name}'")),
    };
    if let Ok(path) = std::env::var(override_key) {
        if !path.trim().is_empty() {
            return Ok(std::path::PathBuf::from(path));
        }
    }
    let exe = std::env::current_exe().map_err(|e| format!("cannot locate this executable: {e}"))?;
    let dir = exe
        .parent()
        .ok_or_else(|| "this executable has no parent directory".to_string())?;
    let base = if dir.ends_with("deps") {
        dir.parent().unwrap_or(dir)
    } else {
        dir
    };
    Ok(sidecar_binary(base, name))
}

/// Per-sidecar stdio handle, held open for the app's lifetime. The reply reader
/// owns the real stdout so the one-JSON-object-per-line wire stays pure,
/// matching selekyt/server.py's own `_progress_to_stderr` convention on the
/// other end of the pipe: one request line in, exactly one reply line out.
struct Sidecar {
    stdin: std::process::ChildStdin,
    reader: BufReader<std::process::ChildStdout>,
    child: Child,
}

/// The stderr half of a sidecar, returned alongside it by `Sidecar::spawn_in`.
///
/// Engine progress lines are drained on a dedicated thread the moment the
/// sidecar is spawned. Reading stderr lazily would let the engine block
/// writing to a full kernel pipe before it ever wrote its reply, which
/// deadlocks the RPC; draining unconditionally means the wire stays responsive
/// even when nothing is consuming the lines — which is exactly what
/// `a_chatty_stderr_does_not_deadlock_the_rpc` asserts.
struct SidecarLogs {
    rx: std::sync::mpsc::Receiver<String>,
}

impl SidecarLogs {
    /// Forward every line to the webview as an `engine-log` event.
    fn relay(self, app: tauri::AppHandle, target: String) {
        std::thread::spawn(move || {
            while let Ok(line) = self.rx.recv() {
                // A failure to emit must not kill the relay: a UI that loses
                // progress is degraded, a relay that dies takes the engine's
                // stderr buffer with it and re-introduces the deadlock.
                let _ = app.emit(
                    "engine-log",
                    serde_json::json!({"target": target, "line": line}),
                );
            }
        });
    }
}

impl Sidecar {
    fn spawn_in(
        base: &std::path::Path,
        name: &str,
        args: &[&str],
    ) -> Result<(Self, SidecarLogs), String> {
        let path = sidecar_binary(base, name);
        let mut cmd = Command::new(&path);
        cmd.args(args.iter());
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            // stderr is the engine's own progress channel, read continuously
            // below rather than left to fill up.
            .stderr(Stdio::piped());
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("failed to spawn {} ({}) : {e}", path.display(), name))?;
        // Take the pipes out of the Child so repeated calls reuse them rather
        // than consuming child.stdout once and losing it for every later call.
        let stdin = child.stdin.take().ok_or("sidecar has no stdin")?;
        let stdout = child.stdout.take().ok_or("sidecar has no stdout")?;
        let stderr = child.stderr.take().ok_or("sidecar has no stderr")?;
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stderr);
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line) {
                    Ok(0) => break,
                    Ok(_) => {
                        let trimmed = line.trim_end_matches(['\r', '\n']);
                        if !trimmed.is_empty() && tx.send(trimmed.to_string()).is_err() {
                            // Every receiver dropped (the shell is shutting
                            // down); stop reading rather than spinning.
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });
        Ok((
            Sidecar {
                stdin,
                reader: BufReader::new(stdout),
                child,
            },
            SidecarLogs { rx },
        ))
    }

    fn spawn(name: &str, args: &[&str]) -> Result<(Self, SidecarLogs), String> {
        let base = sidecar_path(name)?;
        Self::spawn_in(&base, name, args)
    }

    fn call(&mut self, op: &str, params: &Value) -> Result<Value, String> {
        let request = serde_json::json!({"op": op, "params": params});
        let line = serde_json::to_string(&request).map_err(|e| e.to_string())? + "\n";
        self.stdin
            .write_all(line.as_bytes())
            .map_err(|e| format!("cannot write to the sidecar: {e}"))?;
        self.stdin
            .flush()
            .map_err(|e| format!("cannot flush the sidecar pipe: {e}"))?;
        let mut buf = String::new();
        let n = self
            .reader
            .read_line(&mut buf)
            .map_err(|e| format!("cannot read the sidecar reply: {e}"))?;
        if n == 0 {
            return Err("sidecar closed the pipe before answering".to_string());
        }
        serde_json::from_str(buf.trim())
            .map_err(|e| format!("sidecar sent a non-JSON line: {}: {e}", buf.trim()))
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        // Never leave an orphaned interpreter behind if the shell drops a
        // sidecar mid-conversation; the next call respawns it on demand.
        let _ = self.child.kill();
        let _ = self.child.wait();
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
/// bundled LGPL ffmpeg/ffprobe without giving the webview any process
/// permission at all and without modifying the engine.
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

/// Forward the sidecar's stderr to the webview as `engine-log` events.
///
/// The engine's own stage announcements live on stderr (see
/// `selekyt/server.py::_progress_to_stderr`, which keeps the JSON-RPC stdout
/// stream pure). This is the only channel the UI has for real progress, so it
/// is relayed verbatim — the frontend decides what a line means, and this shell
/// One JSON-RPC call to a bundled sidecar. The child is spawned on first use
/// and reused for the lifetime of the app; a dead child is dropped and the
/// caller's error surfaces (the next call respawns it).
fn rpc(app: &tauri::AppHandle, state: &Sidecars, target: &str, op: &str, params: &Value) -> Result<Value, String> {
    let name = target_name(target)?;
    let mut map = state.0.lock().map_err(|e| e.to_string())?;
    if !map.contains_key(name) {
        let args: &[&str] = if name == SIDECAR_PYTHON {
            &["-m", "selekyt", "--server"]
        } else {
            &[]
        };
        let (sidecar, logs) = Sidecar::spawn(name, args).map_err(|e| {
            format!(
                "failed to spawn the '{name}' sidecar: {e} \
                 (bundled under resources/ as an externalBin binary)"
            )
        })?;
        logs.relay(app.clone(), target.to_string());
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
    app: tauri::AppHandle,
    target: String,
    op: String,
    params: Value,
    state: State<'_, Sidecars>,
) -> Result<Value, String> {
    rpc(&app, &state, &target, &op, &params)
}

/// Ask both sidecars `ping` and report their envelopes, so the frontend can
/// show a single honest "engine ready / engine missing" state.
#[tauri::command]
fn selekyt_health(app: tauri::AppHandle, state: State<'_, Sidecars>) -> Value {
    let mut out = serde_json::json!({"python": null, "mech": null});
    for key in ["python", "mech"] {
        if let Ok(value) = rpc(&app, &state, key, "ping", &serde_json::json!({})) {
            out[key] = value;
        }
    }
    out
}

/// Where the pipeline should write its project tree.
///
/// A packaged app is often installed under `Program Files`, which a standard
/// user cannot write to; writing the run output next to the executable would
/// then fail at the very end of a long run. This resolves a per-user directory
/// (`~/SelekyT`) and creates it, so the run has somewhere writable before it
/// starts and the user has one predictable place to look.
#[tauri::command]
fn selekyt_out_dir() -> Result<String, String> {
    let home = dirs_or_err()?;
    let out = home.join("SelekyT");
    std::fs::create_dir_all(&out)
        .map_err(|e| format!("cannot create the output directory {}: {e}", out.display()))?;
    Ok(out.to_string_lossy().into_owned())
}

fn dirs_or_err() -> Result<std::path::PathBuf, String> {
    if let Ok(home) = std::env::var("USERPROFILE") {
        // Windows primary; also covers a USERPROFILE set on other platforms.
        if !home.trim().is_empty() {
            return Ok(std::path::PathBuf::from(home));
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        if !home.trim().is_empty() {
            return Ok(std::path::PathBuf::from(home));
        }
    }
    Err("neither USERPROFILE nor HOME is set; cannot locate a writable output directory".to_string())
}

/// Open the pipeline's output directory in the platform file manager.
///
/// This is the "Open Output Folder" affordance, and it points at a directory
/// the engine itself reported — never at a path the webview may choose
/// freely. A path that does not exist is reported rather than silently
/// ignored, because a missing output directory is evidence of a failed run
/// and a silent no-op would look like success.
#[tauri::command]
fn reveal_path(path: String) -> Result<String, String> {
    let target = std::path::PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("the output path does not exist: {path}"));
    }
    #[cfg(target_os = "windows")]
    {
        // `explorer /select,<file>` or a bare directory. The argument is
        // quoted locally rather than passed through a shell, and the path
        // came from the engine, not from the webview.
        let mut cmd = Command::new("explorer.exe");
        if target.is_file() {
            cmd.arg(format!("/select,{}", target.display()));
        } else {
            cmd.arg(target.as_os_str());
        }
        cmd.spawn()
            .map_err(|e| format!("could not open the file manager: {e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(target.as_os_str())
            .spawn()
            .map_err(|e| format!("could not open Finder: {e}"))?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(target.as_os_str())
            .spawn()
            .map_err(|e| format!("could not open the file manager: {e}"))?;
    }
    Ok(path)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            prepend_bin_to_path(app);
            app.manage(Sidecars::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            selekyt_rpc,
            selekyt_health,
            selekyt_out_dir,
            reveal_path
        ])
        .run(tauri::generate_context!())
        .expect("error while running the SelekyT desktop shell");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidecar_binary_is_named_for_the_target_triple() {
        let base = std::path::Path::new("/opt/selekyt");
        let got = sidecar_binary(&base, "selekyt-python");
        let file = got.file_name().unwrap().to_string_lossy().into_owned();
        assert!(
            file.starts_with("selekyt-python-"),
            "expected a target-triple suffix, got {file}"
        );
        // The triple this build was compiled for is always a suffix of the
        // resolved name, so the bundled binary is found on the real platform.
        if let Ok(triple) = tauri_utils::platform::target_triple() {
            assert!(file.ends_with(&triple), "{file} does not end with {triple}");
        }
        assert_eq!(got.parent(), Some(std::path::Path::new("/opt/selekyt")));
    }

    #[test]
    fn target_name_accepts_only_the_two_engines() {
        assert_eq!(target_name("python").unwrap(), SIDECAR_PYTHON);
        assert_eq!(target_name("mech").unwrap(), SIDECAR_MECH);
        let err = target_name("sh").unwrap_err();
        assert!(
            err.contains("unknown sidecar target"),
            "expected a rejection of an arbitrary target, got {err}"
        );
    }

    /// A stand-in interpreter: one reply line per request line, counting them,
    /// which is exactly the contract selekyt/server.py keeps.
    #[cfg(unix)]
    fn fake_sidecar(dir: &std::path::Path, name: &str, body: &str) -> std::path::PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let path = sidecar_binary(dir, name);
        std::fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    #[cfg(unix)]
    #[test]
    fn rpc_reuses_one_child_across_many_calls() {
        // Regression: call() used to move child.stdout out of the Child, so the
        // second call on a kept-alive sidecar found no stdout, was reported as
        // a failure and silently respawned the interpreter for every other
        // request. A healthy sidecar must answer twice on one child.
        let dir = tempfile_dir();
        let name = "selekyt-fake";
        fake_sidecar(
            &dir,
            name,
            "n=0\nwhile IFS= read -r line; do\n  n=$((n + 1))\n  \
             echo \"{\\\"ok\\\": true, \\\"op\\\": \\\"ping\\\", \\\"data\\\": {\\\"calls\\\": $n}}\"\ndone\n",
        );
        let (mut sidecar, _logs) = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
        for expected in 1..=3 {
            let reply = sidecar.call("ping", &serde_json::json!({})).expect("reply");
            assert_eq!(reply["data"]["calls"], expected, "call {expected} lost its pipe");
            assert_eq!(reply["ok"], true);
            assert_eq!(reply["op"], "ping");
        }
    }

    #[cfg(unix)]
    #[test]
    fn rpc_reports_a_sidecar_that_dies_before_answering() {
        let dir = tempfile_dir();
        let name = "selekyt-fake";
        fake_sidecar(&dir, name, "exit 0\n");
        let (mut sidecar, _logs) = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
        let err = sidecar.call("ping", &serde_json::json!({})).unwrap_err();
        assert!(
            err.contains("closed the pipe before answering"),
            "expected a pipe-closed error, got {err}"
        );
    }

    #[cfg(unix)]
    #[test]
    fn rpc_rejects_a_non_json_reply() {
        let dir = tempfile_dir();
        let name = "selekyt-fake";
        fake_sidecar(&dir, name, "read line\necho 'this is not json'\n");
        let (mut sidecar, _logs) = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
        let err = sidecar.call("ping", &serde_json::json!({})).unwrap_err();
        assert!(err.contains("non-JSON line"), "expected a parse error, got {err}");
    }

    /// A sidecar that writes progress to stderr must not block: this is the
    /// kernel-buffer case `relay_logs` exists for, and it used to deadlock the
    /// long-running pipeline by never draining the pipe.
    #[cfg(unix)]
    #[test]
    fn a_chatty_stderr_does_not_deadlock_the_rpc() {
        let dir = tempfile_dir();
        let name = "selekyt-fake";
        // 256 KiB of stderr (4× a typical 64 KiB pipe buffer) before the reply.
        fake_sidecar(
            &dir,
            name,
            "yes 'progress: still working' | head -c 262144 >&2\n\
             echo '{\"ok\": true, \"op\": \"ping\", \"data\": {}}'\n",
        );
        let (mut sidecar, _logs) = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
        // Not reading stderr here would block on the write end; the caller
        // succeeds because the engine's stderr is drained elsewhere.
        let reply = sidecar.call("ping", &serde_json::json!({})).expect("reply");
        assert_eq!(reply["ok"], true);
    }

    #[cfg(unix)]
    fn tempfile_dir() -> std::path::PathBuf {
        // A private scratch directory per test, cleaned up when it goes out of
        // scope; std::env::temp_dir() alone can collide between concurrent
        // tests in the same crate.
        let mut dir = std::env::temp_dir();
        dir.push(format!(
            "selekyt-shell-test-{}-{}",
            std::process::id(),
            super_count()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    // Each test needs its own scratch directory, and Rust gives no per-test
    // id, so this hands out distinct suffixes within the process.
    #[cfg(unix)]
    use std::sync::atomic::{AtomicUsize, Ordering};
    #[cfg(unix)]
    static COUNTER: AtomicUsize = AtomicUsize::new(0);
    #[cfg(unix)]
    fn super_count() -> usize {
        COUNTER.fetch_add(1, Ordering::SeqCst)
    }
}
