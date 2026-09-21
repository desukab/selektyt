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

4. The release workflow's signed/unsigned label is derived from what the build
   actually did. The artifact name is the only trust claim a downloaded file
   carries before it is run, so a labelling step that cannot see the signing
   hook's output is worse than no labelling at all: it publishes every unsigned
   build as signed, with a green checkmark on the run. This pins the facts that
   keep it honest — the marker is grepped out of the build step's own captured
   log, and an absent marker with no configured credentials fails the job
   instead of defaulting to "signed".
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

SRC_TAURI = pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop" / "src-tauri"
TAURI_CONF = SRC_TAURI / "tauri.conf.json"
SIGN_SCRIPT = SRC_TAURI / "scripts" / "sign.ps1"
MSIX_DIR = SRC_TAURI / "msix"
APPX_MANIFEST = MSIX_DIR / "AppxManifest.xml"
WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
BUILD_WINDOWS = WORKFLOWS / "build-windows.yml"

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


# ── the release label ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def build_workflow() -> dict:
    import yaml

    assert BUILD_WINDOWS.is_file(), f"the release workflow is missing: {BUILD_WINDOWS}"
    return yaml.safe_load(BUILD_WINDOWS.read_text(encoding="utf-8"))


def _steps(workflow: dict) -> dict[str, dict]:
    """The build job's steps, keyed by name, so the wiring between two of them
    can be asserted without depending on step order."""
    steps = workflow["jobs"]["build"]["steps"]
    named = {}
    for step in steps:
        if step.get("name"):
            named[step["name"]] = step
    return named


def _job_env(workflow: dict) -> dict:
    """Env declared on the job is visible to every step; env on a step is not."""
    return workflow["jobs"]["build"].get("env", {})


def _visible_env(workflow: dict, *steps: dict) -> dict:
    """Everything a given step can actually read: the job's env, then the step's
    own, with the step taking precedence as it does on the runner."""
    visible = dict(_job_env(workflow))
    for step in steps:
        visible.update(step.get("env", {}))
    return visible


def test_the_unsigned_marker_is_read_from_the_build_step_own_log(
    build_workflow: dict, sign_text: str,
) -> None:
    """The artifact name is the trust claim. The step that sets it must read the
    signing hook's output out of the build step's captured log; grepping
    ``$GITHUB_STEP_LOG`` reads the labelling step's own log, where the marker
    never appears, and every unsigned build then ships as "signed".

    The path may be declared on the job or on a step. The requirement is that
    *both* the step that tees and the step that grep can see the same name — a
    variable visible to only one of them is what failed run 35543527611, where
    the labelling step expanded it to an empty string and ``[ "" -gt 0 ]`` was
    a bash type error rather than a comparison."""
    steps = _steps(build_workflow)
    build = steps["Build the Windows installers"]
    label = steps["Record whether the build was signed"]

    # The build step captures its own output where the next step can find it.
    log_var = next(
        (k for k, v in _visible_env(build_workflow, build, label).items()
         if isinstance(v, str) and "selekyt-build.log" in v),
        None,
    )
    assert log_var, (
        "the build log path must be published in an env var both the build and "
        "the labelling step can read; without it the marker has nowhere to be found"
    )
    # The variable must be visible to *each* step without the other's env —
    # GitHub step env does not cross steps. Declaring it on the build step alone
    # is what failed run 35543527611: the path expanded to an empty string in the
    # labelling step, grep errored on the missing filename, and `[ "" -gt 0 ]`
    # was a bash type error rather than a comparison. Declaring it on the
    # labelling step alone is the mirror image: `tee` would write nowhere. Either
    # is a broken contract, so check each step's view in isolation.
    for step in (build, label):
        assert log_var in _visible_env(build_workflow, step), (
            f"{log_var} is not visible to the '{step['name']}' step from the job "
            "env or its own; step-scoped env does not cross steps"
        )
    # And each step must actually reach it by that name — a variable only one of
    # them names is a variable only one of them has.
    assert f"${log_var}" in build["run"], "the build must tee its output to that log"
    assert "pipefail" in build["run"], (
        "a tee over a failing build would otherwise mask the failure and ship "
        "a broken installer"
    )

    label_script = label["run"]
    assert "$GITHUB_STEP_LOG" not in label_script, (
        "GITHUB_STEP_LOG is the labelling step's own log; the marker is in the "
        "build step's. This was the bug that named every unsigned build signed."
    )
    assert f"${log_var}" in label_script, (
        f"the labelling step must grep ${log_var}, the build step's captured log"
    )
    # An unset or empty path must fail the job rather than fall through to a
    # type error. grep on a missing filename prints nothing to stdout, so an
    # unguarded `[ "$marker" -gt 0 ]` is not a comparison at all — it is what
    # turned a wiring bug into a silent pass at "signed".
    assert re.search(r"\[\s*! -s", label_script), (
        "the labelling step must refuse when the build log is missing or empty; "
        "an unreadable marker must fail the run, not default it"
    )
    # The marker the workflow greps must be the one the hook actually emits —
    # a rename on either side is a silently mislabelled release.
    assert "SELEKYT_UNSIGNED" in label_script
    assert "SELEKYT_UNSIGNED" in sign_text

    # Contexts are not equally available everywhere. The runner context is
    # valid in a *step's* env but not in the *job's*, and a job-level
    # `${{ runner.temp }}` makes GitHub reject the whole workflow file before a
    # single job runs — the run dies at parse time, so nothing else in the file
    # is exercised and the failure message names the wrong thing. Visibility
    # alone is not enough; the declaration has to be somewhere GitHub accepts.
    # Discovered when the fix for run 35543527611 moved the variable to job env
    # and run 35545641450 came back "failure" with zero jobs executed.
    assert "runner.temp" not in " ".join(_job_env(build_workflow).values()), (
        "the runner context is not available in job-level env; GitHub rejects the "
        "workflow file at parse time and no job ever runs. Declare the path on the "
        "steps that need it instead"
    )


def test_an_unsigned_build_without_credentials_fails_the_job(build_workflow: dict) -> None:
    """Fail closed. The labelling step is a shell script and this *runs it* in
    the three states it can be reached in, rather than reimplementing its
    branching — a reimplementation would pass while the real script named an
    unsigned build "signed", which is the exact failure this guards.

    state                          marker   credentials   requested   expected
    no credentials, hook declined   yes      false         no          unsigned, exit 0
    credentials present, signed     no       true          no          signed, exit 0
    marker missing, no credentials  no       false         no          FAIL — never "signed"
    log path unset, no credentials  n/a      false         no          FAIL — never "signed"
    signing asked, hook declined    yes      false         yes         FAIL — the request is not satisfied
    """
    import subprocess
    import tempfile

    raw = _steps(build_workflow)["Record whether the build was signed"]["run"]
    assert "exit 1" in raw, (
        "the labelling step must exit non-zero rather than default to signed=true"
    )

    def run_label(
        marker_in_log: bool, creds_configured: bool, *, log_path: str | None = "set",
        sign_requested: bool = False,
    ) -> tuple[int, str]:
        # GitHub substitutes the secrets expression before the step runs; do the
        # same so the script under test is the one the runner will execute.
        script = raw.replace(
            "${{ secrets.SELEKYT_AZURE_TENANT_ID != '' }}",
            "true" if creds_configured else "false",
        )
        with tempfile.TemporaryDirectory() as tmp:
            if log_path == "set":
                log = pathlib.Path(tmp) / "selekyt-build.log"
                log.write_text(
                    "##[warning]SELEKYT_UNSIGNED\n" if marker_in_log else "build output\n",
                    encoding="utf-8",
                )
                path = str(log)
            elif log_path == "empty":
                # Declared but points at nothing — the state an unset variable
                # expands to once the shell has had it.
                path = str(pathlib.Path(tmp) / "no-such-log")
            else:
                # Unset entirely. This is the shape of run 35543527611: the
                # variable lived on the build step and this step never saw it.
                path = None
            out = pathlib.Path(tmp) / "github_output"
            env = {**os.environ, "GITHUB_OUTPUT": str(out)}
            if path is not None:
                env["SELEKYT_BUILD_LOG"] = path
            env["SELEKYT_SIGN_REQUESTED"] = "1" if sign_requested else "0"
            proc = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                env=env,
            )
            emitted = out.read_text(encoding="utf-8") if out.exists() else ""
        return proc.returncode, emitted

    code, emitted = run_label(marker_in_log=True, creds_configured=False)
    assert code == 0 and "signed=false" in emitted, (
        "a build whose hook reported SELEKYT_UNSIGNED must be labelled unsigned, "
        f"not fail or be relabelled (exit {code}: {emitted!r})"
    )

    code, emitted = run_label(marker_in_log=True, creds_configured=False, sign_requested=True)
    assert code != 0 and "signed=true" not in emitted and "signed=false" not in emitted, (
        "a build signing was explicitly requested for must not be labelled either "
        "way when the hook reported SELEKYT_UNSIGNED — the request is unsatisfied, "
        "and labelling it at all would let an unsigned build through with a "
        f"signed-looking name (exit {code}: {emitted!r})"
    )

    code, emitted = run_label(marker_in_log=False, creds_configured=True)
    assert code == 0 and "signed=true" in emitted, (
        "a build with credentials and no decline marker is the only state that "
        f"may be labelled signed (exit {code}: {emitted!r})"
    )

    code, emitted = run_label(marker_in_log=False, creds_configured=False)
    assert code != 0, (
        "a build that neither reported the marker nor had credentials cannot "
        "have been signed; labelling it would publish an unsigned installer as "
        "signed, so the run must fail instead"
    )
    assert "signed=true" not in emitted

    for bad in ("empty", "unset"):
        code, emitted = run_label(
            marker_in_log=False, creds_configured=False, log_path=bad,
        )
        assert code != 0 and "signed=true" not in emitted, (
            f"an {bad} build log must fail the job rather than be labelling from "
            "an unreadable marker — this is the state that misnamed every "
            f"unsigned build before the guard existed (exit {code}: {emitted!r})"
        )


def test_the_signing_request_input_reaches_the_step_that_acts_on_it(
    build_workflow: dict,
) -> None:
    """The dispatch input has to be wired to the one step that reads it.

    It was declared for a whole session and consumed by nothing, which made the
    checkbox in the Actions UI a control that silently does nothing: a
    maintainer asking for a signed build got an unsigned one with no indication
    the request had been ignored. A declared input is not a wired input.
    """
    label = _steps(build_workflow)["Record whether the build was signed"]
    env = label.get("env", {})
    assert "SELEKYT_SIGN_REQUESTED" in env, (
        "the labelling step does not read the sign input, so requesting signing "
        "from the Actions UI has no effect"
    )
    assert "inputs.sign" in env["SELEKYT_SIGN_REQUESTED"], (
        f"the variable must come from the dispatch input, not a constant: "
        f"{env['SELEKYT_SIGN_REQUESTED']!r}"
    )
    # A substring check is not enough: inputs.signing contains inputs.sign, and
    # a rename like that would leave the step reading an input the workflow
    # never declares.
    assert re.search(r"inputs\.sign(?![A-Za-z0-9_])", env["SELEKYT_SIGN_REQUESTED"]), (
        f"the expression must name the input exactly: "
        f"{env['SELEKYT_SIGN_REQUESTED']!r}"
    )
    # And the input has to exist, or the expression reads a void.
    inputs = build_workflow.get(True, {}).get("workflow_dispatch", {}).get("inputs", {})
    assert "sign" in inputs, "the workflow declares no sign input for the step to read"
    assert env["SELEKYT_SIGN_REQUESTED"].endswith("}}"), (
        "an unterminated expression is a silent empty string at runtime"
    )


def test_the_artifact_name_branches_on_the_marker(build_workflow: dict) -> None:
    """The upload step's name is the two-valued choice, so neither label can be
    written by hand."""
    upload = next(
        s for s in build_workflow["jobs"]["build"]["steps"]
        if s.get("uses", "").startswith("actions/upload-artifact")
    )
    name = upload["with"]["name"]
    assert "selekyt-windows-x64-signed" in name and "selekyt-windows-x64-unsigned" in name, (
        "the artifact name must be derived from the signing outcome rather than "
        "hardcoded, or a labelling bug becomes invisible"
    )
    assert "steps.signed.outputs.signed" in name


def test_the_msix_job_consumes_whatever_the_build_job_uploaded(
    build_workflow: dict,
) -> None:
    """The MSIX job repacks the installer the build job produced, so it has to
    ask for the artifact by the name that job actually uploaded.

    Both sides branch on the signing outcome, and the branch is written out
    twice — once as ``steps.signed.outputs.signed`` in the upload, once as
    ``needs.build.outputs.signed`` in the download. Two hand-written copies of a
    two-valued choice drift apart silently, and the failure is an MSIX job that
    downloads nothing while the build job looks healthy.
    """
    jobs = build_workflow["jobs"]
    assert "msix" in jobs, "the Store/sideload pack job is missing"

    def artifact_name(step: dict) -> str:
        return step["with"]["name"]

    def uses(action: str, step: dict) -> bool:
        return step.get("uses", "").startswith(action)

    upload = next(s for s in jobs["build"]["steps"] if uses("actions/upload-artifact", s))
    download = next(
        s for s in jobs["msix"]["steps"] if uses("actions/download-artifact", s)
    )
    uploaded, fetched = artifact_name(upload), artifact_name(download)
    # Both branches must carry both labels, or one path silently uploads to a
    # name nothing ever downloads.
    for label, expr in (("upload", uploaded), ("download", fetched)):
        assert "selekyt-windows-x64-signed" in expr and "selekyt-windows-x64-unsigned" in expr, (
            f"the {label} artifact name must branch on the signing outcome rather "
            f"than hardcode one label, or an unsigned build can be fetched as signed: {expr!r}"
        )
    # And the branch must read the same output, not re-derive the answer.
    assert "outputs.signed" in fetched, (
        f"the download name must read the build job's signed output; it is "
        f"instead hardcoding or recomputing it: {fetched!r}"
    )
    assert "steps.signed.outputs.signed" in jobs["build"].get("outputs", {}).get(
        "signed", ""
    ), "the job output has to forward the step's, or the download reads a void"
    # Ordering: a pack job that raced ahead of the upload it consumes would
    # download a stale or empty bundle and never notice.
    assert jobs["msix"].get("needs") == "build" or "build" in (
        jobs["msix"].get("needs") or []
    ), "the MSIX job must depend on the build job, whose installer it repacks"


def test_the_release_workflow_sets_exactly_the_credentials_the_hook_reads(
    build_workflow: dict, sign_text: str,
) -> None:
    """Every variable the hook reads must be set by the workflow, and the
    workflow must not set a credential the hook never reads — that would be a
    secret silently ignored at release time."""
    build = _steps(build_workflow)["Build the Windows installers"]
    set_vars = {k for k, v in build.get("env", {}).items() if k.startswith("SELEKYT_AZURE")}
    read_vars = {n for n in CREDENTIAL_VARS if re.search(rf"\b{re.escape(n)}\b", sign_text)}
    assert set_vars == read_vars, (
        f"the credential contract drifted: workflow sets {sorted(set_vars - read_vars)} "
        f"that the hook never reads, and the hook reads {sorted(read_vars - set_vars)} "
        "that the workflow never sets"
    )


# ── the MSIX / Store package ──────────────────────────────────────────────────


def _manifest_text() -> str:
    """The manifest is XML with no schema here, so it is read as text: the
    assertions are about the values it declares, which is the contract that
    MakeAppx will not check on this host."""
    assert APPX_MANIFEST.is_file(), f"the MSIX manifest is missing: {APPX_MANIFEST}"
    import xml.etree.ElementTree as ET

    ET.fromstring(APPX_MANIFEST.read_text(encoding="utf-8"))  # well-formedness
    return APPX_MANIFEST.read_text(encoding="utf-8")


def _manifest_body() -> str:
    """The manifest with comments stripped.

    Comments are where the *reasoning* about an attribute lives — including the
    names of attributes this file deliberately does not use — so a substring
    check over the raw text sees the commentary and not the markup. Assertions
    about what the manifest declares read this instead; assertions about what
    it explains still read the raw text."""
    return re.sub(r"<!--.*?-->", "", _manifest_text(), flags=re.S)


def test_the_msix_manifest_declares_the_application_identity(conf: dict) -> None:
    """The MSIX identity is a *different* identity from the installer's registry
    one, and both have to agree with the config or the two distribution paths
    ship two different products. Version must be four-part; a three-part value
    is rejected at install time, not at pack time."""
    manifest = _manifest_body()

    def attr(tag: str, name: str) -> str:
        m = re.search(rf'<{tag}\b[^>]*\b{name}="([^"]+)"', manifest)
        assert m, f"the manifest declares no {tag}/{name}, so the MSIX has no {name}"
        return m.group(1)

    assert attr("Identity", "Name") == conf["productName"], (
        "the MSIX package name must match the product name, or the Store package "
        "and the installer are two different products"
    )
    publisher = attr("Identity", "Publisher")
    assert publisher.startswith("CN="), (
        f"an MSIX Publisher must be a distinguished name (CN=...); '{publisher}' "
        "would not match any certificate subject"
    )
    assert conf["bundle"]["publisher"] in publisher, (
        "the MSIX publisher must carry the configured publisher name, or the "
        "Store listing and the package disagree about who publishes the app"
    )
    # A quad version is built from the config's three-part one with a zero
    # revision, which is the documented form for an appx Identity.
    parts = attr("Identity", "Version").split(".")
    assert len(parts) == 4 and all(p.isdigit() for p in parts), (
        "an appx Identity Version must be four-part dotted numeric; anything "
        "else is accepted by nothing that installs packages"
    )
    assert ".".join(parts[:3]) == conf["version"], (
        "the MSIX version's first three parts must track the config version, or "
        "the package and the installer claim different releases"
    )


def test_the_msix_manifest_names_an_executable_the_config_builds(conf: dict) -> None:
    """The manifest's Executable is package-relative. If it names the wrong file
    the package installs and does nothing, which is a failure mode no later
    check catches. It must end in the configured binary name."""
    manifest = _manifest_body()
    exe = re.search(r'\bExecutable="([^"]+)"', manifest)
    assert exe, "the manifest declares no Executable, so the package has no entry point"
    declared = exe.group(1)
    assert declared.endswith(conf["mainBinaryName"] + ".exe"), (
        f"the manifest's Executable is '{declared}' but the built binary is "
        f"{conf['mainBinaryName']}.exe; the package would install and never run"
    )
    # A package-relative path may not be rooted or contain a drive letter.
    assert not declared.startswith(("/", "\\")), (
        f"a package-relative Executable cannot be rooted: '{declared}'"
    )
    assert ":" not in declared, f"a package-relative Executable has a drive: '{declared}'"


def test_the_pack_script_rejects_a_relative_path_that_is_not_relative() -> None:
    """``package-msix.ps1`` computes the manifest's ``Executable`` by slicing the
    installed executable's full path at the payload directory's length. The two
    paths have to be the *same path in the same form* or the slice is by the
    wrong amount.

    Run `35551339487` sliced the canonical form by the 8.3 short form's length:
    ``$env:TEMP`` on a GitHub runner is ``C:\\Users\\RUNNER~1\\...`` while
    ``Get-ChildItem`` reports ``C:\\Users\\runneradmin\\...`` — three characters
    longer — so the cut landed three early, inside the payload directory's own
    name, and produced ``Executable="ad\\SelekyT.exe"``. The manifest was then
    "repaired" to that value and the warning told the maintainer to fix the
    *committed manifest*, which was the one correct thing in the chain.

    Two pins, because they fail for different reasons:

    * the payload the script enumerates and the length it slices by must come
      from one ``Get-Item`` call, not from ``$env:TEMP`` — consistent by
      construction instead of by assumption;
    * the result must be rejected when it is not package-relative, so a future
      form mismatch throws both paths at the reader instead of becoming a
      convincing-looking warning about manifest drift.

    PowerShell cannot run on this host, so the guard's regex is extracted from
    the script and evaluated in Python against the value the runner actually
    produced. The character class is identical in both engines; what is *not*
    tested here is the ``Get-Item`` call itself, which only the runner can
    exercise.
    """
    script = (SRC_TAURI.parent / "package-msix.ps1").read_text(encoding="utf-8")

    # The reconciliation has to re-anchor $payload on the directory object
    # before either side reads it, or the two forms can still disagree.
    reanchor = re.search(
        r"(?m)^\s*\$payload\s*=\s*\(?\s*Get-Item\b.*\)\s*$"
        r"|^\s*\$payload\s*=\s*\$\w+\.FullName\s*$",
        script,
    )
    assert reanchor, (
        "the script never re-anchors $payload on a Get-Item result before "
        "deriving the executable's relative path, so the enumerated paths and "
        "the length sliced by can still be two forms of one directory"
    )
    # ...and it has to happen before the slice, not after.
    slice_at = re.search(r"\$exe\.FullName\.Substring\(\s*\$payload\.Length", script)
    assert slice_at, "the script no longer derives the relative path by slicing $payload"
    assert reanchor.start() < slice_at.start(), (
        "$payload is re-anchored after the slice that consumes it; the two are "
        "still whichever forms they happened to be in"
    )

    # The guard. Shape checks are not enough here — 'ad\SelekyT.exe' fails a
    # "no separators" test, but so does 'SelekyT\SelekyT.exe', which is a legal
    # package-relative path if Tauri ever nests the install, and a shape guard
    # would then be the thing breaking the build. The script has to verify the
    # operation instead: rejoining the derived path to the payload must resolve
    # to a file. A reimplementation in Python cannot model "this file exists",
    # so what is pinned is that the check is a resolution, and that a
    # non-resolving value is fatal.
    resolve = re.search(
        r"(?s)if\s*\(-not \(Test-Path \(?Join-Path\s+\$payload\s+\$relative.*?\n\}",
        script,
    )
    assert resolve, (
        "the script no longer verifies the derived path resolves under the "
        "payload; a path-form mismatch would be written into the manifest and "
        "reported to the maintainer as manifest drift"
    )
    clause = resolve.group(0)
    assert "Join-Path $payload $relative" in clause, (
        "the guard tests something other than the joined path"
    )
    assert "throw" in clause, (
        "the guard logs instead of failing; a non-resolving path would still be "
        "written into the manifest"
    )
    # A derived path with a drive or a leading separator is not package-relative
    # even when it does resolve, so that is checked separately.
    assert re.search(r"\$relative\s+-match\s+'[^']*:'", script), (
        "the script no longer rejects a derived path that carries a drive or a "
        "leading separator"
    )


def _asset_references(manifest: str) -> list[str]:
    """Every image path the manifest asks for.

    Two forms carry one: the ``Logo=``/``Image=`` attributes on the tile and
    splash elements, and the ``<Logo>`` element text under ``<Properties>``
    (which is the store logo). Missing the second means the store logo is
    unchecked, and it is the one asset MakeAppx looks for earliest.
    """
    pattern = (
        r'(?i)(?:Logo|Image)="([^"]+\.(?:png|jpg|jpeg))"'
        r'|<Logo>\s*([^<]+\.(?:png|jpg|jpeg))\s*</Logo>'
    )
    return [m for pair in re.findall(pattern, manifest) for m in pair if m]


def test_every_image_the_manifest_references_exists() -> None:
    """MakeAppx fails the pack on a referenced-but-missing image, but only after
    minutes of installer and SDK work, and its message does not name the file.
    This checks the same thing in milliseconds, and names the offender."""
    referenced = _asset_references(_manifest_body())
    assert referenced, (
        "the manifest references no images at all; it is missing the tile and "
        "store logos, and the package would have no Start Menu presence"
    )
    for rel in set(referenced):
        resolved = (MSIX_DIR / rel.replace("\\", "/")).resolve()
        assert resolved.is_file(), (
            f"the manifest references {rel} but {resolved} does not exist; "
            "MakeAppx would refuse to pack — regenerate with icons/make_icons.py"
        )


def test_the_pack_script_finds_every_image_the_manifest_references() -> None:
    """``package-msix.ps1`` re-implements the asset check above in PowerShell,
    and a re-implementation is where a contract silently halves itself: the
    script matched only the attribute form for a whole session, so the store
    logo went unchecked while the step logged "all referenced assets present".

    The script cannot run on this host, so what is pinned here is its parsing.
    Every single-quoted literal in it that compiles as a regex and matches an
    image path in the manifest is collected, and the union has to be exactly
    what the canonical parser above produces — so a dropped alternative, a
    renamed group, or a pattern that stops matching fails here rather than in a
    25-minute Windows run.
    """
    script = (SRC_TAURI.parent / "package-msix.ps1").read_text(encoding="utf-8")
    manifest = _manifest_body()
    canonical = set(_asset_references(manifest))
    assert canonical, "the manifest references no images for the script to find"

    # Single quotes are PowerShell string literals; the two image patterns are
    # among them, and so is incidental prose, which simply will not compile.
    candidates = re.findall(r"'([^'\n]*)'", script)
    found: set[str] = set()
    for literal in candidates:
        if "Logo" not in literal and "Image" not in literal:
            continue
        try:
            matches = re.findall(literal, manifest)
        except re.error:
            continue
        for match in matches:
            # findall yields a tuple when the pattern has groups; either way the
            # path is the non-empty member.
            if isinstance(match, tuple):
                found.update(m for m in match if m)
            elif match:
                found.add(match)

    assert found == canonical, (
        "the pack script's regexes resolve to a different asset set than the "
        "canonical parser; the store logo is the usual casualty — it lives in "
        f"the <Logo> element, not an attribute. symmetric difference: "
        f"{sorted(found ^ canonical)}"
    )
    # An empty match set makes the presence loop pass vacuously, so the script
    # has to refuse that state rather than report success about nothing.
    assert "No image references were found" in script, (
        "a manifest that lost every image reference would pack with no Start "
        "Menu presence and a green log; the script must fail instead of "
        "iterating over an empty set"
    )


def test_the_pack_script_anchors_the_manifest_at_the_directory_containing_it() -> None:
    """The script copies ``AppxManifest.xml`` from ``apps/desktop/src-tauri/msix/``
    and lives at ``apps/desktop/`` itself, so its anchor is its *own* directory.
    Taking the parent of that gives ``apps/``, which has no ``src-tauri`` under
    it at all.

    That one-line mistake is run 35549741215: the script printed
    ``apps\\src-tauri\\msix\\AppxManifest.xml`` — one directory short of the real
    file — and the message reads as a missing manifest rather than as a wrong
    anchor. It survived a whole session for the same reason the asset check did:
    the path it named was plausible-looking, and nothing on this host resolves
    PowerShell path arithmetic to check it against the tree.

    So resolve it here. Whichever expression the script assigns, the manifest has
    to exist at the end of it, and a form this test does not recognise fails
    rather than passing unmodelled.
    """
    script_path = SRC_TAURI.parent / "package-msix.ps1"
    source = script_path.read_text(encoding="utf-8")

    # $here is itself the script's directory (Split-Path -Parent of the script
    # path), so the two anchors this script can plausibly name are its own
    # directory and its parent.
    anchors = {
        "$here": script_path.parent,
        "Split-Path -Parent $here": script_path.parent.parent,
    }
    assignment = re.search(r"(?m)^\s*\$tauri\s*=\s*(.+)$", source)
    assert assignment, (
        "the script never assigns $tauri, so it has no way to locate the manifest"
    )
    expression = assignment.group(1).strip()
    assert expression in anchors, (
        f"the anchor is written as {expression!r}, which this test does not model; "
        "add it so the path it resolves to is checked against the real tree"
    )
    resolved = anchors[expression] / "src-tauri" / "msix" / "AppxManifest.xml"
    assert resolved.is_file(), (
        f"the script anchors the manifest at {expression}, which resolves to "
        f"{resolved} and no manifest is there. The script sits at "
        f"{script_path.parent} and the manifest below its src-tauri, so the "
        "anchor is the script's own directory and not its parent."
    )


def test_the_manifest_and_the_icon_generator_agree_on_the_asset_set() -> None:
    """``icons/make_icons.py`` emits the MSIX assets and the manifest references
    them, so the two are one contract split across a Python dict and an XML
    file. Either can drift without the other noticing; this pins them together
    so a new logo is added in both places or neither."""
    import importlib.util

    gen = SRC_TAURI / "icons" / "make_icons.py"
    assert gen.is_file(), f"the icon generator is missing: {gen}"
    spec = importlib.util.spec_from_file_location("selekyt_make_icons", gen)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = set(module.MSIX_ASSETS)
    referenced = {
        # The manifest writes Windows-style backslash paths; on a non-Windows
        # host Path() does not treat those as separators, so normalise first.
        pathlib.Path(rel.replace("\\", "/")).name
        for rel in _asset_references(_manifest_body())
    }
    assert referenced <= generated, (
        "the manifest references assets the generator does not emit: "
        f"{sorted(referenced - generated)}"
    )
    # Emitted-but-unreferenced assets are dead weight in the package and a sign
    # the manifest lost an entry, so flag them rather than shipping them.
    assert not (generated - referenced), (
        "the generator emits assets the manifest never references: "
        f"{sorted(generated - referenced)} — either the manifest lost an entry "
        "or the generator gained one that nothing displays"
    )


def test_the_msix_manifest_targets_the_desktop_full_trust_profile() -> None:
    """A Tauri shell that spawns sidecars and reads the whole filesystem has to
    be a full-trust desktop package. The activation form is what makes that
    true, and it is the one detail of the manifest that has to be exactly right
    or the app installs and cannot do anything it needs to."""
    manifest = _manifest_body()
    assert "windows.fullTrustApplication" in manifest, (
        "the manifest must declare the full-trust activation entry point, or the "
        "packaged app runs sandboxed and cannot reach the filesystem"
    )
    assert "Windows.Desktop" in manifest, (
        "the target device family must be Windows.Desktop for a converted Win32 app"
    )
    assert "runFullTrust" in manifest, (
        "runFullTrust is the capability that the full-trust entry point requires; "
        "one without the other does not install"
    )
    # uap10:RuntimeBehavior only exists from 10.0.19041; this manifest targets
    # 17763 and uses the EntryPoint form. Declaring both, contradictorily, is an
    # error, so a RuntimeBehavior attribute must not appear at all.
    assert "RuntimeBehavior" not in manifest, (
        "a RuntimeBehavior attribute would contradict the EntryPoint form this "
        "manifest uses on its target minimum version"
    )
