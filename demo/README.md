# SeleKyT Try Demo

Vercel-ready public demo for the existing SeleKyT Azure worker.

Set the Vercel project **Root Directory** to `demo`. Optional environment variable:

`SELEKYT_ORIGIN` — stable HTTPS origin for the SeleKyT worker. A temporary Quick Tunnel URL is used as the development fallback.

The demo intentionally exposes only:
- health check
- public HTTP(S) source submission
- bounded first-pass pipeline settings
- generated artifacts under `out/web/pipeline`

It does not expose cookies, arbitrary local paths, arbitrary JSON-RPC operations, or the existing engineering shell controls.

For production, replace the Quick Tunnel with a stable named Cloudflare Tunnel hostname before publishing broadly.