"""Packaging contracts a build alone cannot prove.

``python -m build`` succeeding is not the acceptance criterion here; the real
one is that a clean machine with no checkout can ``pip install selekyt`` and
get a working CLI. Two specific defects that build does not catch are what this
module pins, because both of them shipped green and both of them broke exactly
that path:

1. ``engine.llm_editorial`` imported ``requests`` at module scope. ``requests``
   is the optional ``llm`` extra, but ``selekyt/pipeline.py`` imports
   ``attach_metadata`` unconditionally at the top of the module, so a *base*
   install — the one ``pip install selekyt`` produces — crashed on
   ``selekyt --help`` with ``ModuleNotFoundError: No module named 'requests'``
   before argparse ran. The base install is the primary distribution path, so
   this was the front door failing on the first thing a new user types.

2. ``av`` resolved to 19.x on a clean install. faster-whisper 1.2.1 calls
   ``av.open(..., metadata_errors="ignore")`` and PyAV 19 removed that
   argument, but faster-whisper declares only ``av>=11``, so the bound has to
   come from this project's extras. Without it, ``pip install
   'selekyt[transcription]'`` on a clean machine got a stack where every
   transcription died on the first chunk. The fix is a pin, and a pin nobody
   can see the reason for gets lifted the next time someone tidies the extras,
   so the reason is in ``pyproject.toml`` beside it and here as a check.

Both were found by building the wheel and installing it in a bare venv — which
is what the clean-install section below does. A test that only ever runs inside
the development checkout cannot see either one, because the checkout has both
``requests`` and ``av==18.1.0`` installed and the failure is precisely that a
clean environment does not.
"""

from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def _project() -> dict:
    """``pyproject.toml`` as ``[project]`` sees it, parsed rather than grepped.

    Grepping the text is what let three of these checks break on the first
    formatting change: the extras span wrapped lines, the base list is one
    line, and a comment containing a dependency name is indistinguishable from
    the dependency. The TOML parse makes each check about the value instead.
    """
    import tomllib

    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _base_requires() -> list[str]:
    """The runtime dependencies a bare ``pip install selekyt`` pulls in."""
    from importlib.metadata import requires

    return list(requires("selekyt") or [])


@pytest.mark.parametrize("module", ["selekyt.cli", "selekyt.pipeline"])
def test_the_cli_imports_on_a_base_install(module, monkeypatch):
    """A bare install must be able to reach argparse.

    This is the front door. ``selekyt --help`` is the first command a new user
    runs, and it crashed on a base install while ``requests`` was imported at
    module scope in ``engine.llm_editorial`` — the import is reachable from
    ``selekyt.pipeline``, which the CLI imports unconditionally.

    The check has to simulate the absence rather than trust the current
    interpreter: the development checkout has ``requests`` installed, so an
    unguarded import passes here while failing on a clean machine. Hiding the
    module and re-importing is what reproduces the clean-install state.
    """
    monkeypatch.setitem(sys.modules, "requests", None)
    # Snapshot every SelekyT module the process already holds, and put the same
    # objects back at teardown. Deleting the entry is what forces the
    # re-execution — but the re-import leaves a *different* object behind in two
    # places at once, and both have to be undone or the simulation leaks.
    #
    # ``sys.modules[name]`` is the obvious one. The other is the attribute the
    # import machinery sets on the parent package: importing ``selekyt.cli``
    # binds ``selekyt.cli`` on the ``selekyt`` module object too, and the
    # re-import overwrote it with the fresh copy. Restoring only ``sys.modules``
    # left the two out of sync, so a later test's
    # ``monkeypatch.setattr("selekyt.cli.run", ...)`` resolved through one and
    # ``cli_mod.main()`` — bound at collection by ``from selekyt import cli`` —
    # called through the other. The stub never reached the call, the real
    # pipeline ran, and six unrelated CLI tests failed on a placeholder source.
    originals = {name: mod for name, mod in sys.modules.items()
                 if name == "selekyt" or name.startswith("selekyt.")}
    for name, mod in originals.items():
        monkeypatch.setitem(sys.modules, name, mod)
        parent, _, attr = name.rpartition(".")
        if parent in sys.modules:
            monkeypatch.setattr(sys.modules[parent], attr, mod, raising=False)
    # Drop any cached copy so the module body actually re-executes.
    for name in list(sys.modules):
        if name == module or name.startswith(module + "."):
            del sys.modules[name]
    # `requests` absent must not be fatal to importing the CLI. The failure the
    # fix replaced was an ImportError raised here, before a single flag parsed.
    importlib.import_module(module)


def test_llm_editorial_degrades_without_requests(monkeypatch):
    """The documented contract of the LLM layer: no Ollama, no crash, no
    metadata. That has to hold for a missing *package* too, not only for an
    unreachable server — the module's own docstring promises the pipeline
    "continues normally", and a base install is the commonest way to be without
    it."""
    import engine.llm_editorial as llm

    from models.core import Candidate

    monkeypatch.setattr(llm, "requests", None)
    c = Candidate(0.0, 1.0, "We shipped it.", "story", "the hook", "the payoff",
                  editorial_score=0.8, virality_score=0.7, context_score=0.9,
                  confidence=0.9, reason="t", visual_potential=0.8)
    out = llm.attach_metadata(c, "http://localhost:11434")
    assert out.llm_metadata == {}


def test_base_install_contains_every_default_runtime_dependency():
    """Plain `pip install selekyt` is the product, not a shell of the product.

    The default CLI is local transcription and the default output includes
    independently readable timelines, so those runtime packages cannot live
    behind extras. FFmpeg/ffprobe are also provisioned by static-ffmpeg.
    """
    base = _project()["project"]["dependencies"]
    names = {req.split("[", 1)[0].split(";", 1)[0].split(">", 1)[0].split("=", 1)[0].split("<", 1)[0].strip() for req in base}
    for required in (
        "faster-whisper", "av", "onnxruntime", "pycmx",
        "opentimelineio", "static-ffmpeg",
    ):
        assert required in names, f"{required} is missing from the base install"
    assert not any(req.startswith("groq") for req in base)
    assert not any(req.startswith("keyring") for req in base)

def test_requests_is_not_a_base_dependency():
    """``requests`` belongs to the ``llm`` extra. If it appears in the base
    list, the extra is redundant; if it disappears from the extra, the LLM path
    breaks for everyone who installed it. Either direction is a packaging
    defect, and neither is visible from the wheel alone.

    Read from ``pyproject.toml`` rather than the installed metadata: the
    development checkout is an editable install whose metadata lags the file,
    so the *installed* requirement list can describe a version of the project
    that no longer matches the source beside it. The file is what ships."""
    base = _project()["project"]["dependencies"]
    assert not any(req.startswith("requests") for req in base), (
        "requests is in the base dependencies; it is the optional `llm` extra "
        "and making it base forces it on every install"
    )
    extras = _project()["project"]["optional-dependencies"]
    assert any(req.startswith("requests") for req in extras.get("llm", [])), (
        "requests left the llm extra, which is the only place it is declared"
    )


@pytest.mark.parametrize("module", ["keyring", "groq", "google-genai"])
def test_a_cloud_client_is_not_a_base_dependency(module):
    """``groq``, ``google-genai`` and the ``keyring`` store they read a key from
    are the BYOK cloud path. Each is imported lazily and guarded with an install
    hint naming ``[cloud-transcription]``, so a local-only install never reaches
    them — and the local path is what a bare ``pip install selekyt`` is.

    Keeping them base forces an async HTTP stack and a system credential backend
    on every install for a feature the installer did not turn on. ``requests``
    was the same defect and the same fix; this pins the rest so they do not
    creep back one commit at a time."""
    base = _project()["project"]["dependencies"]
    assert not any(req.startswith(module) for req in base), (
        f"{module} is a base dependency; it belongs to [cloud-transcription], "
        f"which a local-only install never imports"
    )
    extras = _project()["project"]["optional-dependencies"]
    assert any(req.startswith(module) for req in extras.get("cloud-transcription", [])), (
        f"{module} left cloud-transcription, which is the only place it is "
        f"declared, and the guarded import hint names that extra"
    )


def test_the_all_extra_installs_every_runtime_group():
    """``[all]`` is the "give me everything" install. A group it omits is a
    feature a user who asked for everything does not get — and the failure is
    silent, surfacing later as an ImportError from the guarded import with an
    install hint pointing at an extra they believe they already installed.

    ``dev`` is excluded: it pulls in pytest and the corpus tooling, which are
    not runtime dependencies and are covered by the CI install.

    Base ``dependencies`` are subtracted from both sides: they are installed by
    *every* ``pip install selekyt``, so a group that graduated from an extra to
    base is still covered. Listing them in ``[all]`` too would duplicate the
    version specs in two places where only one is authoritative."""
    project = _project()["project"]
    extras = project["optional-dependencies"]
    base = set(project.get("dependencies", []))
    all_reqs = set(extras.get("all", []))
    assert all_reqs, "the all extra is missing entirely"
    expected = set()
    for name, reqs in extras.items():
        if name == "all" or name == "dev":
            continue
        expected |= set(reqs)
    installed = base | all_reqs
    missing = sorted(r for r in expected if r not in installed)
    assert not missing, (
        f"[all] does not install these runtime requirements, so a user who ran "
        f"`pip install 'selekyt[all]'` does not have them: {missing}"
    )


def test_the_transcription_extra_pins_av_against_the_pyav_19_break():
    """faster-whisper 1.2.1 calls ``av.open(..., metadata_errors="ignore")``;
    PyAV 19 removed that argument and faster-whisper declares only ``av>=11``.
    Without an upper bound here, a clean install resolves av 19 and every
    transcription fails on the first chunk. See ``pyproject.toml`` for the
    reasoning — the pin is load-bearing, not tidying."""
    transcription = _project()["project"]["optional-dependencies"].get(
        "transcription", [])
    av_specs = [req for req in transcription if req.startswith("av")]
    assert av_specs, (
        "the av compat pin left the transcription extra; faster-whisper 1.2.1 "
        "calls av.open(metadata_errors=...) and PyAV 19 removed it, so an "
        "unpinned clean install cannot transcribe"
    )
    # `<19` is the bound that matters: av 19 is exactly the breaking release,
    # and a pin naming only a lower bound protects nothing.
    assert any("<19" in req for req in av_specs), (
        f"the pin lost its upper bound: {av_specs}"
    )


def test_the_wheel_bundles_the_onnx_models_the_engine_loads():
    """``engine/vad.py`` and ``engine/reframe_perception.py`` resolve their
    models relative to ``__file__``, so a wheel that ships the loader without
    the weights silently degrades to weaker backends on first use. The models
    are MIT-licensed and 1.5 MB, so they travel with the package; this checks
    the declared package-data still names them, which is the one fact a
    successful build does not verify."""
    package_data = _project()["tool"]["setuptools"]["package-data"]["engine"]
    assert any(g.endswith("models/*.onnx") for g in package_data), (
        "the ONNX model glob left [tool.setuptools.package-data]; without it "
        "a pip install has the VAD/face-detection code and none of the weights"
    )


@pytest.fixture(scope="module")
def wheel() -> pathlib.Path:
    """A built wheel.

    Built into the pytest tmp tree rather than the repo's own ``dist/`` so the
    artifact never has to be cleaned out of the working tree, and so a failure
    here is a packaging failure rather than a stray-file surprise.

    A wheel build that runs and fails is never a skip. Every assertion in this
    module is downstream of this fixture, so turning a failed build into a skip
    would publish a wheel whose contents were never checked — the tests would
    report green having proven nothing. The one legitimate skip is a checkout
    without the ``build`` backend at all, which is an environment limitation
    rather than a packaging defect; and not even that under
    SELEKYT_RELEASE_BUILD, where the backend is a release requirement.

    Both the availability probe and the build itself run from an empty cwd
    rather than the repo root. ``build/`` is gitignored because it is what
    setuptools leaves behind after *any* wheel build, and it is a package the
    interpreter can import — so with the repo root as cwd, or on ``sys.path``
    as it is during this suite, ``import build`` and ``python -m build`` both
    resolve the artifact directory instead of the frontend, and the build dies
    with "No module named build.__main__; 'build' is a package and cannot be
    directly executed". That looks like a broken backend and is really a stale
    directory, so the frontend is resolved from a cwd that cannot contain it
    and the source tree is passed as the frontend's own argument."""
    import tempfile

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="selekyt-pkg-"))
    probe = subprocess.run(
        [sys.executable, "-c", "import build"],
        cwd=str(workdir), capture_output=True,
    )
    if probe.returncode != 0:
        if os.environ.get("SELEKYT_RELEASE_BUILD"):
            pytest.fail("the `build` backend is not installed in a release build")
        pytest.skip("the `build` backend is not installed")

    # Isolation on: this venv does not carry setuptools, so a --no-isolation
    # build dies with "Backend 'setuptools.build_meta' is not available" before
    # it gets to the project's own metadata. Isolated builds fetch the backend
    # themselves, which is also what a real release does. ROOT is the frontend's
    # source-directory argument, not its cwd, for the reason above.
    build_env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel",
         "--outdir", str(workdir), str(ROOT)],
        cwd=str(workdir), env=build_env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.fail(
            "the wheel does not build, so no packaging assertion in this "
            f"module can be trusted:\n{proc.stderr[-1200:]}")
    wheels = sorted(workdir.glob("selekyt-*.whl"))
    assert wheels, "build reported success but wrote no wheel"
    return wheels[-1]


def _wheel_names(wheel: pathlib.Path) -> list[str]:
    import zipfile

    with zipfile.ZipFile(wheel) as zf:
        return zf.namelist()


def test_the_wheel_names_every_runtime_package(wheel):
    """The entry point is ``selekyt.cli:main``, but the CLI transitively
    imports ``engine``, ``models``, ``providers`` and the vendored FCPXML
    reader. A package dropped from ``packages.find`` is a wheel that installs
    and then cannot be imported — the failure only appears at runtime, after
    pip reports success."""
    names = _wheel_names(wheel)
    for package in ("selekyt", "engine", "models", "providers", "third_party"):
        assert any(n.startswith(package + "/") for n in names), (
            f"{package}/ is absent from the wheel; the CLI imports it and "
            f"would fail on a clean install"
        )


def test_the_wheel_carries_the_onnx_weights(wheel):
    """The package-data declaration names the models; this proves they actually
    landed in the artifact. ``pyproject`` globbing under a namespace package is
    exactly the kind of thing that stops matching silently after a directory
    move, and the engine resolves these by path rather than by import, so
    nothing at import time would notice them missing."""
    names = _wheel_names(wheel)
    assert "engine/models/silero_vad_v6.onnx" in names
    assert "engine/models/face_detection_yunet.onnx" in names
    # MIT and MIT-adjacent licences that must travel with the weights.
    assert "engine/models/silero_vad_v6.LICENSE" in names
    assert "engine/models/face_detection_yunet.LICENSE" in names
    if os.environ.get("SELEKYT_RELEASE_BUILD"):
        # The layout the wheel actually vendors — engine/models/whisper_tiny/
        # and engine/bin/, which is where providers/whisper.py and
        # providers/sidecar.py resolve them from. Asserting these paths is what
        # makes a package-data glob that silently stopped matching a red test
        # instead of a wheel that degrades on first use.
        assert "engine/models/whisper_tiny/model.bin" in names
        assert "engine/models/whisper_tiny/tokenizer.json" in names
        assert "engine/models/whisper_tiny/config.json" in names
        assert "engine/models/whisper_tiny/LICENSE" in names
        assert "engine/models/whisper_tiny/PROVENANCE.md" in names
        assert "engine/bin/selekyt-mech" in names



def test_the_wheel_metadata_declares_the_complete_core_runtime(wheel):
    import zipfile
    with zipfile.ZipFile(wheel) as zf:
        meta_name = next(n for n in zf.namelist() if n.endswith('.dist-info/METADATA'))
        meta = zf.read(meta_name).decode('utf-8')
    for line in [
        'Requires-Dist: faster-whisper', 'Requires-Dist: av',
        'Requires-Dist: onnxruntime', 'Requires-Dist: pycmx',
        'Requires-Dist: opentimelineio', 'Requires-Dist: static-ffmpeg',
    ]:
        assert line in meta, f'{line} is missing from the built wheel METADATA'


def test_the_wheel_is_portable_and_says_what_native_runtime_it_carries(wheel):
    """The wheel is deliberately ``py3-none-any`` rather than platform-tagged.

    Tagging it ``linux_x86_64`` would deny the editorial engine to macOS,
    Windows and aarch64 hosts for the sake of one binary. Nothing on the
    pipeline's critical path requires the sidecar — ``providers.sidecar``'
    resolvers return ``None`` and every caller degrades — so the honest
    contract is a portable wheel that carries the Linux x86_64 binary and
    reports it unavailable elsewhere. What must not happen is a portable tag
    with no binary inside, which is why this runs under SELEKYT_RELEASE_BUILD."""
    if os.environ.get('SELEKYT_RELEASE_BUILD'):
        assert wheel.name.endswith('-py3-none-any.whl'), wheel.name
        names = _wheel_names(wheel)
        assert "engine/bin/selekyt-mech" in names

def test_the_wheel_ships_no_repo_only_directories(wheel):
    """``corpus/`` holds gigabytes of regenerable media, ``tests/`` is the
    suite, and ``apps/``/``rust/``/``web/`` are the desktop and site builds.
    None of it is runtime, and all of it is either unclonably large or simply
    not the installer's business. A packaging find pattern that widens by one
    glob is how it leaks in."""
    names = _wheel_names(wheel)
    leaked = {n.split("/")[0] for n in names} - {
        "selekyt", "engine", "models", "providers", "third_party",
    }
    leaked = {p for p in leaked if not p.endswith(".dist-info")}
    assert not leaked, f"the wheel carries repo-only trees: {sorted(leaked)}"


def test_the_wheel_declares_the_readme_as_its_long_description(wheel):
    """A package with no long description lands on PyPI as a name and a one-line
    summary, and the installation instructions a user needs are invisible from
    the page they install from. The readme is declared, so it is the page."""
    import zipfile

    with zipfile.ZipFile(wheel) as zf:
        meta = zf.read(next(n for n in zf.namelist()
                            if n.endswith(".dist-info/METADATA"))).decode()
    assert "Description-Content-Type: text/markdown" in meta, (
        "README.md is not declared as the long description; the distribution "
        "lands with no installation instructions on its own page"
    )
    # The README body itself must be in the metadata — a declared
    # ``readme`` with content that does not land is an empty PyPI page. What
    # actually matters is that the installation instructions travel with the
    # distribution, so the check is for the install command rather than for the
    # heading text, whose casing ("SeleKyT") has changed between releases and
    # is not a packaging defect worth failing a build over.
    body = meta.split("\n\n", 1)[1] if "\n\n" in meta else ""
    assert "pip install" in body, (
        "the README body is not in the metadata; the long description is empty "
        "or is a different document, and the installation instructions a user "
        "needs are invisible from the page they install from"
    )
