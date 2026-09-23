"""The provenance and config spine of the pipeline's ``report.json``.

``run()`` in ``selekyt/pipeline.py`` assembles ``report.json`` itself, because
its consumers (the desktop app's sidecar, ``tools/nle_check.py``, the editorial
audit) read its keys at the top level. This module carries the run-level facts
those sections do not hold and that the deliverable otherwise cannot state
about itself: which build made it (git SHA, software version, platform,
interpreter), when, how long it took, and which parameters were resolved.

The remaining sections this builder can hold — input, analysis, candidates,
editing, output, qa — are deliberately *not* populated by the pipeline: every
one of them already has one authoritative home in the report, and a second copy
of the same fact under a different key is how the two drift. They stay available
because the report's own tests exercise the builder end to end.
"""
from __future__ import annotations

import json
import platform
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class Provenance:
    source_path: str = ""
    source_fingerprint: str = ""
    audio_fingerprint: str = ""
    whisper_model: str = ""
    whisper_language: str = ""
    whisper_version: str = ""
    vad_backend: str = ""
    vad_version: str = ""
    software_version: str = ""
    git_sha: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    cache_status: str = ""
    platform: str = ""
    python: str = ""


@dataclass
class StageTimings:
    transcribe: float = 0.0
    editorial: float = 0.0
    vad: float = 0.0
    jumpcut: float = 0.0
    render: float = 0.0
    total: float = 0.0


@dataclass
class ReportSections:
    provenance: Provenance = field(default_factory=Provenance)
    config: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    candidates: dict[str, Any] = field(default_factory=dict)
    editing: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    qa: dict[str, Any] = field(default_factory=dict)


class ReportBuilder:
    """Builds and serializes a pipeline report."""

    def __init__(self, started_at: float | None = None):
        self._start = started_at or time.time()
        self.sections = ReportSections()
        self.sections.provenance.started_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        self.sections.provenance.platform = platform.platform()
        self.sections.provenance.python = platform.python_version()
        self.sections.provenance.git_sha = self._git_sha()
        self.sections.provenance.software_version = self._software_version()

    # ---- setters ----

    def set_provenance(self, **kw: Any) -> None:
        for k, v in kw.items():
            if hasattr(self.sections.provenance, k):
                setattr(self.sections.provenance, k, v)

    def set_config(self, config: dict[str, Any]) -> None:
        self.sections.config = config

    def set_analysis(self, **kw: Any) -> None:
        self.sections.analysis.update(kw)

    def set_candidates(self, **kw: Any) -> None:
        self.sections.candidates.update(kw)

    def set_editing(self, **kw: Any) -> None:
        self.sections.editing.update(kw)

    def set_output(self, **kw: Any) -> None:
        self.sections.output.update(kw)

    def set_qa(self, **kw: Any) -> None:
        self.sections.qa.update(kw)

    # ---- finalize ----

    def finalize(self, finished_at: float | None = None) -> "ReportBuilder":
        end = finished_at or time.time()
        self.sections.provenance.finished_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        self.sections.provenance.duration_seconds = round(end - self._start, 3)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.sections)

    def provenance_dict(self) -> dict[str, Any]:
        """The provenance section alone, for merging into a report the caller
        has already assembled.

        ``report.json`` carries its content sections at the top level its
        readers depend on; only the provenance comes from here, so this is the
        one key the pipeline takes rather than the whole structure."""
        return asdict(self.sections.provenance)

    @staticmethod
    def _git_sha() -> str:
        """The short SHA of the checkout this code was imported from.

        Resolved by walking up from this file rather than from the process's
        working directory: the report names the build that produced the tree,
        and a user launching the pipeline from their home directory is running
        exactly the same code as one launching it from the repo root. Reading
        the cwd instead made the field silently empty for one of the two, and
        an empty SHA reads as "unreleased build" to whoever finds the report.

        A checkout found above the package is only accepted if it actually
        tracks this file. A vendored or pip-installed copy of the package
        sitting inside an unrelated project would otherwise be reported as a
        build of that project, which is worse than saying nothing.

        In an exported tarball there is no ``.git`` anywhere above the package,
        and the honest value really is empty.
        """
        try:
            here = Path(__file__).resolve()
        except OSError:
            here = None
        roots = []
        if here is not None:
            # __file__ is <root>/engine/report.py, so the checkout is the parent.
            for candidate in [here.parent.parent, *here.parent.parents]:
                roots.append(candidate)
                if (candidate / ".git").exists():
                    break
        # The cwd is a last resort, and behaves as it always did: it may be the
        # only checkout in reach for an installed copy of the package.
        roots.append(Path.cwd())
        for root in roots:
            sha = ReportBuilder._sha_at(root, here)
            if sha:
                return sha
        return ""

    @staticmethod
    def _sha_at(root: Path, here: Path | None) -> str:
        """The short HEAD SHA of ``root``, but only if ``root`` is this code's
        own history.

        When ``here`` sits under ``root``, that is checked by asking the index
        whether it tracks the running file — a checkout that does not is a
        project the package was copied into, not the package's own repository.
        When it does not (the cwd fallback for an installed copy), no such
        question is meaningful and the checkout is taken at face value.
        """
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5, encoding="utf-8",
            )
            if out.returncode != 0 or not out.stdout.strip():
                return ""
            if here is not None:
                try:
                    rel = here.relative_to(root)
                except ValueError:
                    return out.stdout.strip()
                tracked = subprocess.run(
                    ["git", "-C", str(root), "ls-files", "--error-unmatch",
                     str(rel)],
                    capture_output=True, text=True, timeout=5, encoding="utf-8",
                )
                if tracked.returncode != 0:
                    return ""
            return out.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _software_version() -> str:
        try:
            import importlib.metadata
            return importlib.metadata.version("selekyt") or "dev"
        except Exception:
            return "dev"


def write_report_json(
    builder: ReportBuilder,
    output: str | Path,
) -> Path:
    """Write report.json, returning the path written."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(builder.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def terminal_summary(builder: ReportBuilder) -> str:
    """Human-readable terminal summary from the report."""
    p = builder.sections.provenance
    s = builder.sections
    lines = [
        "SelekyT pipeline report",
        "=" * 40,
        f"Source            : {p.source_path or '(none)'}",
        f"Git SHA           : {p.git_sha or '(unknown)'}",
        f"Whisper           : {p.whisper_model or '(none)'} "
        f"({p.whisper_language or '-'})",
        f"VAD backend       : {p.vad_backend or '(none)'}",
        f"Cache status      : {p.cache_status or '(none)'}",
        f"Elapsed           : {p.duration_seconds:.1f}s",
    ]
    if s.candidates:
        lines.append(f"Candidate clips  : {s.candidates.get('count', '-')}")
    if s.editing:
        lines.append(
            f"Edited duration  : {s.editing.get('edited_duration', '-')}s "
            f"(compression {s.editing.get('compression_ratio', '-')})"
        )
    if s.output:
        lines.append(f"Outputs          : {list(s.output.keys())}")
    if s.qa:
        lines.append(f"QA : {s.qa.get('status', '-')}")
    return "\n".join(lines)


def write_terminal_summary(builder: ReportBuilder, output: str | Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(terminal_summary(builder) + "\n", encoding="utf-8")
    return output