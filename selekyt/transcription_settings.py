"""Small persistent transcription preference plus OS-backed BYOK secrets."""
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

PROVIDERS = ("local", "groq", "gemini")
_ENV_KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}


class TranscriptionSettingsError(RuntimeError):
    pass


def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming")
        return base / "SeleKyT" / "settings.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SeleKyT" / "settings.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "selekyt" / "settings.json"


def default_provider() -> str:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        provider = data.get("transcription_provider")
        return provider if provider in PROVIDERS else "local"
    except (OSError, ValueError, TypeError, AttributeError):
        return "local"


def _write_preference(provider: str) -> None:
    if provider not in PROVIDERS:
        raise TranscriptionSettingsError(f"Unknown transcription provider: {provider}")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        data = {}
    data["transcription_provider"] = provider
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    os.replace(tmp, path)

def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise TranscriptionSettingsError(
            "Secure key storage is unavailable. Install `pip install -e '.[cloud-transcription]'` "
            "or set GROQ_API_KEY / GEMINI_API_KEY in your environment."
        ) from exc
    try:
        backend = keyring.get_keyring()
        backend_name = type(backend).__name__.casefold()
        if getattr(backend, "priority", 0) <= 0 or "plaintext" in backend_name:
            raise RuntimeError("no secure credential backend")
    except Exception as exc:
        raise TranscriptionSettingsError(
            "SeleKyT could not access a secure system credential store. Set the provider API key "
            "in your environment instead; the key will not be written to SeleKyT settings."
        ) from exc
    return keyring


def api_key(provider: str) -> str | None:
    if provider == "local":
        return None
    env_name = _ENV_KEYS[provider]
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    try:
        value = _keyring_module().get_password("selekyt", provider)
        if value:
            return value
    except Exception:
        pass
    # Headless Linux servers often have no OS keyring. Setup still needs to be
    # one simple flow, so the fallback is a mode-0600 config file.
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return str(data.get("api_keys", {}).get(provider, "")).strip() or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None
def require_api_key(provider: str) -> str:
    key = api_key(provider)
    if key:
        return key
    label = "Groq" if provider == "groq" else "Gemini"
    env_name = _ENV_KEYS[provider]
    raise TranscriptionSettingsError(
        f"{label} transcription was selected, but no {label} API key is configured. "
        f"Run `selekyt configure-transcription {provider}` or set {env_name}; "
        "or use `--transcription local`."
    )


def configure(provider: str, key: str | None = None) -> None:
    if provider not in PROVIDERS:
        raise TranscriptionSettingsError(f"Choose one of: {', '.join(PROVIDERS)}")
    if provider != "local":
        if not key:
            env_value = os.environ.get(_ENV_KEYS[provider], "").strip()
            if env_value:
                key = env_value
            else:
                try:
                    key = getpass.getpass(f"{provider.title()} API key (input hidden): ").strip()
                except (EOFError, KeyboardInterrupt):
                    raise TranscriptionSettingsError("No API key entered; saved settings were not changed.")
        if not key:
            raise TranscriptionSettingsError("No API key entered; saved settings were not changed.")
        if not os.environ.get(_ENV_KEYS[provider], "").strip():
            try:
                _keyring_module().set_password("selekyt", provider, key)
            except Exception:
                # Headless machines commonly have no desktop keyring. Keep the
                # same one-step setup by storing the credential in the existing
                # 0600 SeleKyT settings file rather than forcing env variables.
                path = settings_path()
                try:
                    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                except (OSError, ValueError, TypeError):
                    data = {}
                data.setdefault("api_keys", {})[provider] = key
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                try:
                    tmp.chmod(0o600)
                except OSError:
                    pass
                os.replace(tmp, path)
    _write_preference(provider)

def configure_interactive(provider: str | None = None) -> str:
    if provider is None:
        print("Transcription provider:")
        print("  local  — private/offline (default)")
        print("  groq   — cloud transcription using your Groq API key")
        print("  gemini — cloud transcription using your Gemini API key")
        provider = input("Choose [local/groq/gemini] (local): ").strip().lower() or "local"
    configure(provider)
    return provider
