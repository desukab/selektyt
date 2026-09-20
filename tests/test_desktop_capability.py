"""The desktop shell's Tauri capability file must name only real permissions.

`apps/desktop/src-tauri/capabilities/main.json` is the entire permission surface
of the webview: anything it does not grant, the UI cannot do. A permission name
that does not exist in the plugin manifests makes `tauri build` fail at codegen
— on the Windows runner, minutes into a compile and a ~1 GB download, with no
way to reproduce locally. This test fails here instead, in under a second.

The valid set is read from the ACL manifests Tauri's codegen already generated
into `apps/desktop/src-tauri/gen/schemas/`, so a plugin upgrade that renames or
removes a permission is caught by the same check.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SRC_TAURI = Path(__file__).resolve().parent.parent / "apps" / "desktop" / "src-tauri"
CAPABILITY = SRC_TAURI / "capabilities" / "main.json"
ACL_MANIFESTS = SRC_TAURI / "gen" / "schemas" / "acl-manifests.json"


@pytest.fixture(scope="module")
def acl_permissions() -> set[str]:
    """Every permission identifier the bundled plugins actually define."""
    if not ACL_MANIFESTS.is_file():
        pytest.skip("Tauri ACL manifests are not generated in this checkout")
    manifests = json.loads(ACL_MANIFESTS.read_text(encoding="utf-8"))
    names: set[str] = set()
    for plugin, manifest in manifests.items():
        perms = manifest.get("permissions", {})
        if isinstance(perms, dict):
            # Keyed by the short identifier ("allow-copy-file"); a capability
            # writes the qualified form ("fs:allow-copy-file").
            for short in perms:
                names.add(f"{plugin}:{short}")
                names.add(short)
        elif isinstance(perms, list):
            for perm in perms:
                ident = perm.get("identifier") if isinstance(perm, dict) else None
                if isinstance(ident, str):
                    names.add(f"{plugin}:{ident}")
                    names.add(ident)
        # `default` is a permission *set*, not a single permission, but a
        # capability writes it as "core:default" and tauri accepts that.
        if isinstance(manifest.get("default_permission"), dict):
            names.add(f"{plugin}:default")
    return names


def _capability_permissions() -> list[str]:
    assert CAPABILITY.is_file(), f"the shell's capability file is missing: {CAPABILITY}"
    body = json.loads(CAPABILITY.read_text(encoding="utf-8"))
    names = []
    for perm in body.get("permissions", []):
        if isinstance(perm, str):
            names.append(perm)
        elif isinstance(perm, dict):
            ident = perm.get("identifier")
            if isinstance(ident, str):
                names.append(ident)
    assert names, "a capability with no permissions grants the webview nothing"
    return names


@pytest.mark.parametrize("name", _capability_permissions())
def test_each_capability_names_a_real_permission(name, acl_permissions):
    """A permission the plugin manifests do not define fails tauri's codegen,
    which only runs on the packaging host."""
    # Tauri spells a plugin's default set "<plugin>:default" while the manifest
    # records it under `default_permission`, so accept the shorthand directly.
    if name.endswith(":default"):
        return
    assert name in acl_permissions, (
        f"{name!r} is not a permission any bundled plugin defines. "
        "tauri build would fail at codegen on the Windows runner."
    )


def test_the_capability_file_is_valid_json_with_a_main_window():
    """The ACL applies to the one window the shell opens."""
    body = json.loads(CAPABILITY.read_text(encoding="utf-8"))
    assert body.get("windows") == ["main"], (
        "the shell opens a single 'main' window; a capability scoped to "
        "another label would silently grant the UI nothing"
    )
    assert "core:default" in _capability_permissions(), (
        "core:default carries the event and path permissions invoke() needs"
    )


def test_the_capability_grants_no_network():
    """Local-first is an architectural promise of the desktop app, not a
    setting; the permission file is where it is actually enforced.

    Checked against the granted permissions only — the description prose says
    "no network" and a naive substring match on the whole file passes by
    quoting the promise instead of testing it.
    """
    for name in _capability_permissions():
        plugin = name.split(":")[0]
        assert plugin not in ("http", "shell", "process"), (
            f"{name} lets the webview reach outside the machine or spawn "
            "arbitrary programs; the shell delegates both to the sidecars"
        )
