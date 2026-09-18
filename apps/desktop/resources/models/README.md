# Bundled model files

ASR models are deliberately **not** committed to this repository and are not
fetched at packaging time by default. Reasons in `docs/desktop-bundling.md`
("Model files"): a `base`-sized Whisper model is roughly 150 MB, it belongs to
the user's first-run choice of size and language, and shipping it would pin
every install to one model.

At runtime the intelligence layer obtains the model it needs into the user's
cache (`~/.cache/telugify/models` — the same path the CLI uses; see
`providers/sherpa.py` `DEFAULT_MODELS_DIR`), on first transcription, with a
visible progress indication in the shell. That download happens inside the
Python sidecar; the webview itself has no network permission at all
(`apps/desktop/src-tauri/capabilities/main.json`).

**Shipping a model inside the app is therefore a documented gap, not a config
switch.** `providers/sherpa.py` resolves its model directory to
`~/.cache/telugify/models` with no environment override, so a pre-seeded model
would either have to be written into the user's cache on first run, or the
engine would need a small change to accept a resource-relative model
directory. That engine change is out of scope for the packaging work and is
listed in `docs/packaging.md` under "VERIFICATION REQUIRED ON / NOT built".
