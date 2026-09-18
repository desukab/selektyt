fn main() {
    // The Tauri config lives one level up (apps/desktop/tauri.conf.json) so it
    // stays the single packaging manifest for both the CI workflows and this
    // crate; point the codegen at it explicitly instead of duplicating a
    // copy inside src-tauri/.
    tauri_build::Builder::new()
        .tauri_conf("../tauri.conf.json")
        .try_build()
        .expect("failed to run tauri codegen — is apps/desktop/tauri.conf.json valid?");
}
