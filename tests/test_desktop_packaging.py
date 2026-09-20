"""The Windows packaging contract: identity, the signing hook, and no secrets.

Three separate things, each worth failing on its own:

1. `signCommand` in tauri.conf.json points at a script that is actually in the
   tree. A path typo is invisible until the Windows runner is minutes into a
   compile and a 1 GB download, and the error it produces then does not name the
   real cause.

2. The signing script honours the documented credential contract. This asserts
   the *static* facts — that the environment variable names the script reads are
   the ones the release workflow sets, and that the skip flag is checked. It does
   NOT execute PowerShell (no Windows here) and does not reimplement the
   script's branching: a reimplementation would pass while the real script was
   wrong. What it catches is the rename on either side of that boundary.

3. No credential is committed. A secret in the packaging config would ship
   inside every installer, which is the one defect here that cannot be undone by
   a later release.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

SRC_TAURI = pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop" / "src-tauri"
TAURI_CONF = SRC_TAURI / "tauri.conf.json"
SIGN_SCRIPT = SRC_TAURI / "scripts" / "sign.ps1"

# The names the release workflow sets and the script reads. Both sides must
# agree; a rename on one side is a silent unsigned release.
CREDENTIAL_VARS = [
    "SELEKYT_AZURE_TENANT_ID",
    "SELEKYT_AZURE_CLIENT_ID",
    "SELEKYT_AZURE_CLIENT_SECRET",
    "SELEKYT_AZURE_KEY_VAULT_URI",
    "SELEKYT_AZURE_CERT_NAME",
]
SKIP_VAR = "SELEKYT_SKIP_SIGNING"


@pytest.fixture(scope="module")
def conf() -> dict:
    assert TAURI_CONF.is_file(), f"the desktop config is missing: {TAURI_CONF}"
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sign_text() -> str:
    assert SIGN_SCRIPT.is_file(), f"the signing hook is missing: {SIGN_SCRIPT}"
    return SIGN_SCRIPT.read_text(encoding="utf-8")


def test_the_sign_command_names_a_script_that_exists(conf: dict) -> None:
    """A typo in `signCommand` would only surface on the Windows runner."""
    win = conf["bundle"]["windows"]
    assert "signCommand" in win, "the signing hook must be wired or the build ships unsigned by default"
    cmd = win["signCommand"]
    assert set(cmd) == {"cmd", "args"}, f"only the two documented keys, got {sorted(cmd)}"
    assert "%1" in cmd["args"], (
        "Tauri substitutes the binary path for %1; without it the script would "
        "receive no file to sign"
    )
    # The path is written config-relative, so resolve it the same way.
    script_arg = next(a for a in cmd["args"] if a.endswith("sign.ps1"))
    resolved = (SRC_TAURI / script_arg).resolve()
    assert resolved.is_file(), (
        f"signCommand points at {script_arg}, which does not resolve to a file ({resolved})"
    )


def test_the_signing_hook_honours_the_documented_contract(sign_text: str) -> None:
    """Every variable the release workflow sets must appear in the script, and
    the skip flag must take precedence — that is what makes the same config
    able to build an honestly-labelled unsigned artifact."""
    for name in CREDENTIAL_VARS:
        assert re.search(rf"\b{re.escape(name)}\b", sign_text), (
            f"{name} is not read by the signing script; the release workflow "
            "would set a variable that does nothing"
        )
    assert re.search(rf"\b{re.escape(SKIP_VAR)}\b", sign_text), (
        f"{SKIP_VAR} is the documented escape hatch for an unsigned release and "
        "must be honoured"
    )
    # Azure Trusted Signing timestamps with RFC 3161 (/tr), and the config's
    # tsp:true must match what the script actually asks the client to do.
    assert re.search(r"-tr\b", sign_text), "the script must request RFC 3161 timestamping"


def test_no_credential_is_committed(conf: dict, sign_text: str) -> None:
    """A secret in the config or the script ships inside every installer, and
    uninstalling the app does not take it back."""
    blob = json.dumps(conf) + sign_text
    # A literal certificate thumbprint is 40 hex digits; a GUID tenant/client id
    # is 36 with dashes. Neither belongs in a committed file.
    assert not re.search(r"\b[0-9a-fA-F]{40}\b", blob), (
        "a 40-hex-digit string in the packaging files looks like a certificate "
        "thumbprint; credentials belong in runner secrets"
    )
    assert not re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", blob), (
        "a GUID in the packaging files looks like a tenant or client id; "
        "credentials belong in runner secrets"
    )
    # PEM bodies would be an inlined private key or certificate.
    for marker in ("-----BEGIN", "PRIVATE KEY", "MII"):
        assert marker not in blob, f"{marker!r} in the packaging files looks like an inlined key"
    # The vault endpoint comes from the environment, not from a hardcoded URI.
    # A backtick-continued PowerShell line can wrap anywhere, so compare on
    # whitespace-normalised text rather than a literal substring.
    flat = re.sub(r"\s+", " ", sign_text)
    assert "-kvu $azure.VaultUri" in flat, (
        "the vault URI must be read from the environment, not inlined, or a "
        "single credential would be baked into every installer"
    )


def test_the_config_declares_an_application_identity(conf: dict) -> None:
    """Identity is what makes installs upgrade rather than accumulate, and what
    a certificate's subject must eventually match."""
    assert conf["productName"] == "SelekyT"
    assert conf["identifier"] == "com.selekyt.desktop", (
        "the reverse-DNS identifier is the application's identity in the "
        "installer's registry entry and, later, in an MSIX manifest"
    )
    assert conf["bundle"]["publisher"], "an empty publisher shows as blank in Programs and Features"
    assert conf["version"], "an empty version makes every install the same identity"


def test_digest_and_timestamp_are_sha256_and_rfc3161(conf: dict) -> None:
    """SHA-256 with an RFC 3161 timestamp is what current Windows requires; an
    SHA-1 digest is rejected and an untimestamped signature expires with the
    certificate."""
    win = conf["bundle"]["windows"]
    assert win.get("digestAlgorithm") == "sha256"
    assert win.get("tsp") is True, "tsp must be true for an RFC 3161 timestamp server"
    assert win.get("timestampUrl"), "a timestamp URL is required for a long-lived signature"
    assert win.get("timestampUrl", "").startswith("http"), "the timestamp endpoint must be a URL"
