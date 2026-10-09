"""Runtime assets shipped with the production PyPI distribution.

Three things the wheel carries are not Python: the bundled whisper tiny
weights, the silero/YuNet ONNX models, and the compiled Rust ``selekyt-mech``
sidecar. ``--doctor`` and the release acceptance suite ask this module whether
they are present and reachable, so it is the single name the CLI and CI agree
on.

Resolution is delegated to the providers that actually load these assets —
``providers.whisper.resolve_model_path`` and ``providers.sidecar`` — rather than
re-deriving the paths here. A second copy of a layout is how a wheel and a
runtime's answer about it drift apart: the provider is where the path is
load-bearing, so it is where the truth lives.

Every accessor returns ``None`` when the asset is absent rather than raising,
because a doctor report that cannot distinguish "not bundled" from "broken"
cannot tell the user which problem to fix.
"""

from __future__ import annotations

from pathlib import Path


def bundled_whisper_model() -> Path | None:
    """The bundled whisper ``tiny`` directory, or ``None`` when it is absent.

    ``resolve_model_path`` returns the size name back when no bundled directory
    carries ``model.bin``, which is exactly the "would download" case this
    function must report as absent.
    """
    from providers.whisper import resolve_model_path

    resolved = Path(resolve_model_path("tiny"))
    return resolved if resolved.is_dir() else None


def bundled_mech_path() -> Path | None:
    """The bundled Rust sidecar, or ``None`` when no candidate is available."""
    from providers.sidecar import sidecar_path

    return sidecar_path()


def ping_mech(timeout: float = 5.0) -> dict:
    """Round-trip a real JSON-RPC ping with the sidecar and return its result.

    Raises when the sidecar is absent or does not answer, because a caller that
    asks for a live round trip wants the failure reported rather than translated
    into a boolean it would then have to invent a message for. ``--doctor``
    catches it and reports the rung as unavailable.
    """
    from providers.sidecar import ping

    return ping(timeout=timeout)
