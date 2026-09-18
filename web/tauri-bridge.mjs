// SelekyT webview transport bridge.
//
// Loaded only when the page runs inside the Tauri desktop shell. It replaces
// window.fetch for the `/api/*` surface so web/app.mjs — written for the Node
// HTTP harness (web/server.mjs) — talks to the bundled sidecars through the
// Tauri `selekyt_rpc` command unchanged. Under a plain browser or the Node
// harness this file is inert: `window.__TAURI__` is undefined, so the original
// fetch is left alone.
//
// Scope (deliberate): this bridges the JSON-RPC analysis transport — the ops
// the editorial UI needs to segment, score and export. The harness-only
// conveniences that need a local HTTP server (the generated sample WAV, the
// /api/file download links) are not bridged yet; in the desktop shell you pick
// a WAV from disk with the file input instead. That gap is recorded in
// docs/packaging.md ("What is NOT built").

const TAURI = globalThis.window && globalThis.window.__TAURI__;
const isTauri = Boolean(
  TAURI && TAURI.core && typeof TAURI.core.invoke === "function",
);

if (isTauri) {
  const originalFetch = globalThis.fetch.bind(globalThis);

  async function tauriFetch(input, init) {
    const url = new URL(String(input), "http://selekyt.local");
    if (!url.pathname.startsWith("/api/")) {
      return originalFetch(input, init);
    }
    const op = url.pathname.slice("/api/".length);
    const body = init && init.body ? JSON.parse(String(init.body)) : {};
    const envelope = await TAURI.core.invoke("selekyt_rpc", {
      target: "python",
      op,
      params: body,
    });
    // selekyt_rpc returns the sidecar's own envelope verbatim:
    // {"ok": true, "op": ..., "data": ...} | {"ok": false, "error": ...}
    return new Response(JSON.stringify(envelope), {
      status: envelope.ok === false ? 500 : 200,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }

  globalThis.fetch = tauriFetch;

  // Health badge: ask both sidecars for `ping` and surface the result in the
  // header element the shell already renders, so a missing engine is visible
  // instead of silently failing on the first analysis.
  (async function reportHealth() {
    try {
      const health = await TAURI.core.invoke("selekyt_health");
      const el = document.getElementById("engine");
      if (!el) return;
      const parts = [];
      for (const target of ["python", "mech"]) {
        const entry = health && health[target];
        if (entry && entry.ok && entry.data) {
          const d = entry.data;
          parts.push(`${target}: ${d.engine || target} ${d.version || ""} (${d.vad_backend || "?"})`);
        } else {
          parts.push(`${target}: unavailable`);
        }
      }
      el.textContent = parts.join(" · ");
      el.classList.toggle("error", !parts.every((p) => !p.endsWith("unavailable")));
    } catch (err) {
      const el = document.getElementById("engine");
      if (el) {
        el.textContent = `shell: ${String(err)}`;
        el.classList.add("error");
      }
    }
  })();
}

export { isTauri };
