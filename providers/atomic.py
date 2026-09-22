"""Atomic JSON persistence for checkpoints and pipeline state.

Two failure modes motivated this:

* a process killed mid-``write_text`` leaves a truncated file that parses as
  nothing, so the next run throws away work it already finished;
* a partially-written state file can parse as an *empty* structure (``""``
  or ``0``), which is worse: it silently resets progress.

``write_json_atomic`` lands the whole payload via ``os.replace`` on a
same-directory temp file, so a reader either sees the previous contents or
the new ones, never a half. ``read_json`` reports failure explicitly instead
of raising, so callers can decide between retrying the work and recovering
from a backup.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, payload: Any, *, indent: int = 2) -> None:
    """Write ``payload`` as JSON, replacing ``path`` atomically.

    The temp file lives in the same directory on purpose: ``os.replace`` is
    only atomic within a filesystem, and a cross-device ``/tmp`` would turn
    it into a plain copy.

    Its name is unique per write, not a fixed ``path.name + ".tmp"``. Two
    processes writing the same state file used to share one temp name, so the
    second write truncated the first's buffer in place and the first's
    ``os.replace`` then landed the *second* process's payload — while the
    second's own replace raised, its source name having been consumed. Both
    writers believing they succeeded is how state loses one of them without an
    error anywhere. A unique name gives each writer its own buffer and its own
    replace, which is the property atomicity was meant to provide.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        # mkstemp does not delete on failure once its fd is handed to fdopen,
        # and a crash between creation and replace would leave the unique name
        # behind as an intermediate nobody cleans up.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path) -> Any | None:
    """Read JSON, returning ``None`` when it is missing or not parseable.

    ``None`` is deliberately not a valid payload shape for any caller: a file
    that parses to ``None``/``""``/``0`` is treated as unreadable too, so a
    truncated write can never be mistaken for an intentionally empty record.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data else None
