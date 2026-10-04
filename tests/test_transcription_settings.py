from __future__ import annotations

import json

import pytest

from selekyt import transcription_settings as settings


class FakeKeyring:
    class Backend:
        priority = 1

    def __init__(self):
        self.values = {}

    def get_keyring(self):
        return self.Backend()

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password


def test_local_is_the_initial_default_and_user_config_does_not_store_secrets(tmp_path, monkeypatch):
    keyring = FakeKeyring()
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "settings_path", lambda: path)
    monkeypatch.setattr(settings, "_keyring_module", lambda: keyring)
    assert settings.default_provider() == "local"

    settings.configure("groq", "user-owned-key")
    assert settings.default_provider() == "groq"
    assert keyring.values[("selekyt", "groq")] == "user-owned-key"
    assert "user-owned-key" not in path.read_text()

    settings.configure("local")
    assert settings.default_provider() == "local"


def test_environment_key_is_supported_without_keyring(monkeypatch):
    monkeypatch.setattr(settings, "_keyring_module", lambda: pytest.fail("keyring should not be used"))
    monkeypatch.setenv("GEMINI_API_KEY", "environment-owned-key")
    assert settings.require_api_key("gemini") == "environment-owned-key"


@pytest.mark.parametrize(
    ("provider", "message"),
    [("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY")],
)
def test_missing_cloud_key_has_actionable_error(monkeypatch, provider, message):
    monkeypatch.delenv(message, raising=False)
    monkeypatch.setattr(settings, "_keyring_module", lambda: FakeKeyring())
    with pytest.raises(settings.TranscriptionSettingsError) as error:
        settings.require_api_key(provider)
    assert f"{provider}" in str(error.value)
    assert f"--transcription local" in str(error.value)
    assert message in str(error.value)


def test_settings_file_permissions_are_restricted(tmp_path, monkeypatch):
    path = tmp_path / "private" / "settings.json"
    monkeypatch.setattr(settings, "settings_path", lambda: path)
    settings.configure("local")
    if hasattr(path.stat(), "st_mode"):
        assert path.stat().st_mode & 0o077 == 0
        assert path.parent.stat().st_mode & 0o077 == 0


def test_corrupt_preferences_keep_local_default(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("not json")
    monkeypatch.setattr(settings, "settings_path", lambda: path)
    assert settings.default_provider() == "local"


def test_settings_preference_file_has_no_key_fields(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "settings_path", lambda: path)
    settings._write_preference("gemini")
    assert json.loads(path.read_text()) == {"transcription_provider": "gemini"}
