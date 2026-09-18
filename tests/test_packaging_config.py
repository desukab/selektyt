"""Packaging-config contract tests.

These run on every host (including the Linux development machine and plain
ci.yml, which installs only the ``dev`` extra and therefore has no PyYAML).
They assert that the desktop packaging surface is well-formed and minimal:

  - apps/desktop/tauri.conf.json parses and carries the required metadata,
    bundle targets and sidecar/resource declarations;
  - the capability file grants local filesystem access plus execution of
    exactly the two bundled sidecars, and no network permission of any kind;
  - the desktop CI workflows parse and contain the jobs/steps/artifact hooks
    that the packaging story depends on;
  - the app version equals the version in pyproject.toml (never bumped
    independently);
  - no secret material of any kind appears in a packaged config.

YAML parsing prefers PyYAML when it is importable and otherwise falls back to
a small indentation parser written for the restricted grammar these workflow
files use. When PyYAML *is* available the two parsers are cross-checked
against each other, which is how the fallback is validated — on this host that
cross-check actually runs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "apps" / "desktop"
TAURI_CONF = DESKTOP / "tauri.conf.json"
CAPABILITY = DESKTOP / "src-tauri" / "capabilities" / "main.json"
WORKFLOWS = sorted(ROOT.glob(".github/workflows/desktop-*.yml"))
PACKAGED_CONFIGS = [
    TAURI_CONF,
    CAPABILITY,
    DESKTOP / "package.json",
    *WORKFLOWS,
    *DESKTOP.glob("scripts/*.py"),
    *DESKTOP.glob("scripts/*.sh"),
]

# The only programs the webview may execute.
ALLOWED_EXECUTABLES = {"binaries/selekyt-mech", "binaries/selekyt-python"}

# Permission identifiers that would hand the webview network access. None of
# them may appear anywhere in the capability list.
NETWORK_PERMISSIONS = (
    "http:", "reqwest:", "websocket:", "core:webview:allow-",
    "shell:allow-open",
)

# Secret-shaped patterns that must never appear in a shipped config. The
# false-positive risk is kept low by requiring the token-shaped body, not the
# bare word "key".
SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api|secret|access|private|auth|bearer|token|password|passwd|credential)[-_ ]?(key|token|secret)?\s*[:=]\s*[\"'][A-Za-z0-9_\-]{12,}[\"']"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bAIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"(?i)git@[^:\s]+:[^@\s]+\.ssh"),
]


# ── minimal YAML reader (fallback when PyYAML is unavailable) ──

def _block_scalar_lines(lines: list[str]) -> set[int]:
    """Indices of lines that are the content of a ``|`` / ``>`` block.

    A ``#`` inside a block scalar is literal text, not a comment, so those
    lines must be protected from comment stripping.
    """
    protected: set[int] = set()
    for i, line in enumerate(lines):
        if not re.match(r"^\s*[^\s#].*:\s*[|>]", line):
            continue
        base = len(line) - len(line.lstrip(" "))
        j = i + 1
        while j < len(lines):
            if not lines[j].strip():
                j += 1
                continue
            if len(lines[j]) - len(lines[j].lstrip(" ")) <= base:
                break
            protected.add(j)
            j += 1
    return protected


def _tokens(text: str) -> list[tuple[int, str, str]]:
    """(indent, stripped_content, raw_line_with_indent). The raw form is kept
    because block scalars must preserve *relative* indentation.

    Blank and comment-only lines are dropped, which means a blank line inside
    a `|` block is not reproduced. None of these workflow files do that, and
    the PyYAML cross-check below makes any such file fail loudly here rather
    than parse subtly wrong.
    """
    lines = text.splitlines()
    protected = _block_scalar_lines(lines)
    out = []
    for n, raw in enumerate(lines):
        line = raw.rstrip()
        if not line.strip():
            continue
        if n not in protected:
            # strip full-line comments and trailing comments outside quotes
            in_s = in_d = False
            cut = len(line)
            for idx, ch in enumerate(line):
                if ch == "'" and not in_d:
                    in_s = not in_s
                elif ch == '"' and not in_s:
                    in_d = not in_d
                elif ch == "#" and not in_s and not in_d:
                    cut = idx
                    break
            line = line[:cut].rstrip()
            if not line.strip():
                continue
        indent = len(line) - len(line.lstrip(" "))
        out.append((indent, line.lstrip(" "), line))
    return out


def _split_key(text: str) -> tuple[str, str | None] | None:
    """Split a `key: value` (or `key:`) line, honouring quotes."""
    in_s = in_d = False
    for idx, ch in enumerate(text):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == ":" and not in_s and not in_d:
            if idx + 1 == len(text):
                return text[:idx].strip(), None
            if text[idx + 1] == " ":
                return text[:idx].strip(), text[idx + 2:].strip()
    return None


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _scalar(text: str):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1]
        if not body.strip():
            return []
        return [_unquote(p.strip()) for p in _split_flow(body)]
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return _unquote(text)


def _split_flow(body: str) -> list[str]:
    parts, cur, in_s, in_d = [], [], False, False
    for ch in body:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "," and not in_s and not in_d:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def _parse_node(tokens: list, i: int):
    if tokens[i][1].startswith("- ") or tokens[i][1] == "-":
        return _parse_seq(tokens, i)
    return _parse_map(tokens, i)


def _parse_map(tokens: list, i: int):
    base = tokens[i][0]
    out: dict = {}
    while i < len(tokens):
        indent, line, _raw = tokens[i]
        if indent != base:
            break
        split = _split_key(line)
        if split is None:
            raise ValueError(f"not a mapping line: {line!r}")
        key, rest = split
        i += 1
        if rest is None:
            if i < len(tokens) and tokens[i][0] > base:
                out[key], i = _parse_node(tokens, i)
            else:
                out[key] = None
        elif rest[0] in "|>":
            raw_lines, j = [], i
            while j < len(tokens) and tokens[j][0] > base:
                raw_lines.append(tokens[j][2])
                j += 1
            if raw_lines:
                common = min(len(r) - len(r.lstrip(" ")) for r in raw_lines)
                block = [r[common:] for r in raw_lines]
            else:
                block = []
            # Clip chomping (plain `|`/`>`): a block ending in a newline keeps
            # exactly one, which is what PyYAML yields for these files.
            body = "\n".join(block) if rest[0] == "|" else " ".join(block)
            out[key] = body + "\n" if block else body
            i = j
        else:
            out[key] = _scalar(rest)
    return out, i


def _parse_seq(tokens: list, i: int):
    base = tokens[i][0]
    out: list = []
    while i < len(tokens):
        indent, line, _raw = tokens[i]
        if indent != base or not (line.startswith("- ") or line == "-"):
            break
        rest = line[1:].strip() if line != "-" else ""
        i += 1
        if not rest:
            if i < len(tokens) and tokens[i][0] > base:
                item, i = _parse_node(tokens, i)
                out.append(item)
            else:
                out.append(None)
        elif _split_key("- " + rest) is not None:
            # `- key: value` — the item is a mapping whose first key sits on
            # the dash line; re-anchor it at the item's content column.
            synthetic = " " * (base + 2) + rest
            tokens[i - 1] = (base + 2, rest, synthetic)
            item, i = _parse_map(tokens, i - 1)
            out.append(item)
        else:
            out.append(_scalar(rest))
    return out, i


def load_yaml(text: str):
    """Parse the restricted YAML subset used by the desktop workflows."""
    tokens = _tokens(text)
    if not tokens:
        return None
    value, _ = _parse_node(tokens, 0)
    return value


def _normalise_keys(obj):
    """PyYAML (YAML 1.1) resolves the bare key `on:` as boolean True."""
    if isinstance(obj, dict):
        return {("on" if k is True else k): _normalise_keys(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalise_keys(v) for v in obj]
    return obj


# ── fixtures ──

@pytest.fixture(scope="module")
def tauri() -> dict:
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def capability() -> dict:
    return json.loads(CAPABILITY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflows() -> dict[str, dict]:
    out = {}
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            out[path.name] = load_yaml(text)
            continue
        parsed = _normalise_keys(yaml.safe_load(text))
        # The fallback parser must agree with PyYAML wherever PyYAML exists.
        assert load_yaml(text) == parsed, (
            f"the fallback YAML parser disagrees with PyYAML on {path.name}; "
            "the fallback is what plain ci.yml relies on, so this is a bug"
        )
        out[path.name] = parsed
    return out


# ── presence ──

def test_packaging_files_exist():
    assert TAURI_CONF.is_file(), f"missing {TAURI_CONF}"
    assert CAPABILITY.is_file(), f"missing {CAPABILITY}"
    assert sorted(p.name for p in WORKFLOWS) == [
        "desktop-macos.yml",
        "desktop-windows.yml",
    ], "desktop workflows must be exactly the two shipped files"
    smoke = ROOT / "tests" / "test_desktop_smoke.py"
    assert smoke.is_file()


# ── tauri.conf.json ──

def test_tauri_metadata(tauri):
    assert tauri["productName"] == "SelekyT"
    assert tauri["identifier"] == "com.selekyt.desktop"
    assert re.fullmatch(r"[a-z0-9.]+", tauri["identifier"])
    assert tauri["bundle"]["publisher"]


def test_tauri_version_matches_pyproject(tauri):
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
    assert match, "could not read the version from pyproject.toml"
    assert tauri["version"] == match.group(1), (
        "the desktop app version must track pyproject.toml, not its own number"
    )


def test_tauri_bundle_targets_cover_windows_and_macos(tauri):
    targets = set(tauri["bundle"]["targets"])
    assert {"app", "dmg", "nsis", "msi"} <= targets, targets
    assert tauri["bundle"]["active"] is True
    assert tauri["bundle"]["publisher"]
    assert "Video" == tauri["bundle"]["category"]


def test_tauri_external_binaries_are_exactly_the_two_sidecars(tauri):
    external = tauri["bundle"]["externalBin"]
    assert sorted(external) == sorted(
        ["binaries/selekyt-mech", "binaries/selekyt-python"]
    ), "the webview may only execute the two bundled sidecars"
    resources = tauri["bundle"]["resources"]
    # The runtime tree ships as a resource so its Lib/lib directory stays
    # adjacent to the sidecar interpreter; the externalBin entry points in.
    assert any("python" in target for target in resources.values()), resources
    assert "bin/" in resources.values(), "ffmpeg/ffprobe must be bundled"


def test_tauri_frontend_is_the_existing_web_shell(tauri):
    """No second UI: the desktop app renders the same web/ shell."""
    assert tauri["build"]["frontendDist"] == "../web"
    assert (ROOT / "web" / "index.html").is_file()
    assert (ROOT / "web" / "tauri-bridge.mjs").is_file()


def test_tauri_security_csp_denies_network(tauri):
    csp = tauri["app"]["security"]["csp"]
    assert "connect-src 'self'" in csp, csp
    assert "default-src 'self'" in csp, csp
    assert "http://" not in csp.replace("http://ipc.localhost", ""), (
        "the CSP may not allow arbitrary network origins"
    )


def test_tauri_windows_and_macos_bundle_settings(tauri):
    mac = tauri["bundle"]["macOS"]
    assert mac["minimumSystemVersion"], "an untargeted macOS floor is a bug"
    win = tauri["bundle"]["windows"]
    assert win["nsis"] and win["wix"], "NSIS and WiX must both be configured"
    assert win["nsis"]["installMode"] in ("currentUser", "perMachine")
    assert tauri["mainBinaryName"]


# ── capability / permission surface ──

def _permission_names(capability) -> list[str]:
    names = []
    for entry in capability["permissions"]:
        if isinstance(entry, str):
            names.append(entry)
        else:
            names.append(entry["identifier"])
    return names


def test_capability_targets_only_the_main_window(capability):
    assert capability["windows"] == ["main"]
    assert capability["webviews"] == ["main"]


def test_capability_grants_no_network(capability):
    names = _permission_names(capability)
    for needle in NETWORK_PERMISSIONS:
        for name in names:
            assert not name.startswith(needle), (
                f"network-shaped permission {name!r} must not be granted"
            )
    assert not any(n.startswith("fs:allow-watch") for n in names)


def test_capability_shell_execute_is_scoped_to_the_two_sidecars(capability):
    entries = [e for e in capability["permissions"]
               if isinstance(e, dict) and e.get("identifier") == "shell:allow-execute"]
    assert len(entries) == 1, "shell:allow-execute must appear exactly once"
    allowed = {a["name"] for a in entries[0]["allow"]}
    assert allowed == ALLOWED_EXECUTABLES, allowed
    for rule in entries[0]["allow"]:
        assert rule["sidecar"] is True, "only declared Tauri sidecars may execute"
        assert rule["args"] is True


def test_capability_filesystem_scope_is_local_and_user_owned(capability):
    text = CAPABILITY.read_text(encoding="utf-8")
    for var in ("$HOME", "$DOCUMENT", "$RESOURCE"):
        assert var in text
    # Credential directories are explicitly denied even though $HOME is read.
    deny = []
    for e in capability["permissions"]:
        if isinstance(e, dict) and e.get("identifier", "").startswith("fs:allow-read"):
            deny += [d["path"] for d in e.get("deny", [])]
    assert any(".ssh" in d for d in deny), "the SSH directory must be denied"


# ── workflows ──

def test_workflows_parse_and_declare_triggers(workflows):
    assert len(workflows) == 2
    for name, doc in workflows.items():
        assert "on" in doc or True in doc, f"{name} has no trigger block"
        triggers = doc.get("on", doc.get(True, {}))
        assert "workflow_dispatch" in triggers, f"{name} must be manually runnable"
        assert "push" in triggers or "pull_request" in triggers


def test_workflows_run_on_the_right_os_and_fail_on_missing_tools(workflows):
    assert workflows["desktop-windows.yml"]["jobs"]["build"]["runs-on"] == "windows-latest"
    assert workflows["desktop-macos.yml"]["jobs"]["build"]["runs-on"] == "macos-latest"
    for name, doc in workflows.items():
        steps = doc["jobs"]["build"]["steps"]
        names = " ".join(s.get("name", "") for s in steps)
        assert "required native tools" in names, (
            f"{name} must fail loudly on a missing toolchain"
        )
        assert "selekyt-mech" in names
        assert "Smoke-test the packaged app" in names
        assert any("upload-artifact" in (s.get("uses", "")) for s in steps), (
            f"{name} must publish an artifact"
        )


def test_workflows_do_not_touch_ci_yml():
    """The new jobs must leave .github/workflows/ci.yml alone."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "desktop-windows" not in ci and "desktop-macos" not in ci


# ── secrets audit ──

@pytest.mark.parametrize("path", PACKAGED_CONFIGS)
def test_no_secrets_in_packaged_configs(path):
    text = path.read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        hit = pattern.search(text)
        if hit:
            pytest.fail(
                f"secret-shaped pattern {pattern.pattern!r} matched in "
                f"{path.relative_to(ROOT)}: {hit.group(0)[:40]!r}"
            )


def test_no_environment_secret_injection_in_workflows(workflows):
    """Env blocks may only carry paths and flags, never material."""
    for name, doc in workflows.items():
        steps = doc["jobs"]["build"]["steps"]
        for step in steps:
            for key, value in (step.get("env") or {}).items():
                assert isinstance(value, str)
                for pattern in SECRET_PATTERNS:
                    assert not pattern.search(value), (name, key)


# ── version consistency across the packaging surface ──

def test_package_json_tracks_pyproject_version():
    pkg = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
    assert pkg["version"] == match.group(1)
