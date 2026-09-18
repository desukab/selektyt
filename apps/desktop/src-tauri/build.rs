use std::path::Path;

fn main() {
    // Tauri's codegen resolves `tauri.conf.json` from the crate directory
    // (apps/desktop/src-tauri/tauri.conf.json), which is the standard Tauri 2
    // layout and the only place tauri-build 2 looks.

    // `bundle.externalBin` and `bundle.resources` name artefacts staged under
    // apps/desktop/src-tauri/{binaries,resources}/ by the packaging scripts,
    // not by the source tree: on a development host `cargo check` would
    // otherwise die on their absence long before any code is compiled. Drop
    // them for this codegen pass only when nothing is staged at all, so a
    // half-staged packaging run still fails loudly where it matters.
    if !Path::new("binaries").is_dir() && !Path::new("resources").is_dir() {
        println!(
            "cargo:warning=apps/desktop: neither binaries/ nor resources/ is staged, so this \
             codegen pass drops bundle.externalBin and bundle.resources. A real packaging run \
             stages them first (see docs/packaging.md); this build is a compile/lint check only."
        );
        // Merged over the on-disk config by tauri-build after it reads it.
        // json_patch (RFC 7396) recurses into objects but replaces arrays and
        // scalars wholesale, so an empty *list* clears these keys where an
        // empty object would be a no-op.
        std::env::set_var(
            "TAURI_CONFIG",
            r#"{"bundle":{"externalBin":[],"resources":[]}}"#,
        );
    }

    tauri_build::try_build(tauri_build::Attributes::new())
        .expect("failed to run tauri codegen — is apps/desktop/src-tauri/tauri.conf.json valid?");
}
