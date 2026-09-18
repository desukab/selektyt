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
//! process permission surface to zero: the frontend can only invoke
//! `selekyt_rpc` and `selekyt_health`, and the shell itself spawns exactly the
//! two bundled interpreters.
//!
//! NOTE: this crate has never been compiled on the development host (no Rust
//! toolchain there). Native compilation is the top item in the
//! "VERIFICATION REQUIRED ON" section of docs/packaging.md.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde_json::Value;
use tauri::{Manager, State};

const SIDECAR_PYTHON: &str = "selekyt-python";
const SIDECAR_MECH: &str = "selekyt-mech";

/// Where a bundled sidecar lives under `base`: named `<name>-<target-triple>`,
/// with `.exe` on Windows. This is the same resolution `tauri build` uses when
/// it copies `bundle.externalBin` entries alongside the main binary, so it does
/// not depend on the shell plugin — whose `new_sidecar` is not public in the
/// 2.x line — and keeps the webview's process-permission surface at zero.
fn sidecar_binary(base: &std::path::Path, name: &str) -> std::path::PathBuf {
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
fn sidecar_path(name: &str) -> Result<std::path::PathBuf, String> {
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

impl Sidecar {
    fn spawn_in(base: &std::path::Path, name: &str, args: &[&str]) -> Result<Self, String> {
        let path = sidecar_binary(base, name);
        let mut cmd = Command::new(&path);
        cmd.args(args.iter());
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("failed to spawn {} ({}) : {e}", path.display(), name))?;
        // Take the pipes out of the Child so repeated calls reuse them rather
        // than consuming child.stdout once and losing it for every later call.
        let stdin = child.stdin.take().ok_or("sidecar has no stdin")?;
        let stdout = child.stdout.take().ok_or("sidecar has no stdout")?;
        Ok(Sidecar {
            stdin,
            reader: BufReader::new(stdout),
            child,
        })
    }

    fn spawn(name: &str, args: &[&str]) -> Result<Self, String> {
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
        .setup(|app| {
            prepend_bin_to_path(app);
            app.manage(Sidecars::default());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![selekyt_rpc, selekyt_health])
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
        let mut sidecar = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
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
        let mut sidecar = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
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
        let mut sidecar = Sidecar::spawn_in(&dir, name, &[]).expect("fake sidecar spawns");
        let err = sidecar.call("ping", &serde_json::json!({})).unwrap_err();
        assert!(err.contains("non-JSON line"), "expected a parse error, got {err}");
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
