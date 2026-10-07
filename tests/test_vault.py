"""API keys: variables de entorno, almacén del sistema y archivo protegido para servidores sin escritorio."""

from __future__ import annotations

import os
import stat

import keyring
import pytest
from keyring.errors import NoKeyringError

from backend.security import vault


@pytest.fixture
def no_keyring(tmp_path, monkeypatch):
    """Como en un VPS sin escritorio: keyring no tiene backend."""
    def fail(*args, **kwargs):
        raise NoKeyringError("No recommended backend was available.")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, fail)
    monkeypatch.setattr(vault, "secrets_file", lambda: tmp_path / "runtime" / "secrets.json")
    return tmp_path / "runtime" / "secrets.json"


@pytest.fixture
def memory_keyring(monkeypatch):
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, name: store.get((service, name)))
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: store.__setitem__((service, name), value))

    def delete(service, name):
        if (service, name) not in store:
            raise keyring.errors.PasswordDeleteError("no existe")
        del store[(service, name)]

    monkeypatch.setattr(keyring, "delete_password", delete)
    return store


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in [vault.env_name("openai_api"), "OPENAI_API_KEY", vault.env_name("home_assistant")]:
        monkeypatch.delenv(name, raising=False)


def test_without_system_keyring_keys_go_to_a_private_file(no_keyring):
    vault.set_api_key("openai_api", "sk-servidor")
    assert vault.get_api_key("openai_api") == "sk-servidor"
    assert no_keyring.exists()
    if os.name != "nt":
        assert stat.S_IMODE(no_keyring.stat().st_mode) == 0o600
    vault.delete_api_key("openai_api")
    assert vault.get_api_key("openai_api") is None


def test_system_keyring_is_used_when_available(memory_keyring, tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "secrets_file", lambda: tmp_path / "secrets.json")
    vault.set_api_key("anthropic_api", "sk-ant")
    assert memory_keyring[(vault.SERVICE_NAME, "anthropic_api")] == "sk-ant"
    assert not (tmp_path / "secrets.json").exists()
    vault.delete_api_key("anthropic_api")
    vault.delete_api_key("anthropic_api")  # borrar dos veces no falla
    assert vault.get_api_key("anthropic_api") is None


def test_environment_overrides_and_standard_names(memory_keyring, monkeypatch):
    memory_keyring[(vault.SERVICE_NAME, "openai_api")] = "sk-guardada"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-estandar")
    assert vault.get_api_key("openai_api") == "sk-guardada"  # lo guardado en G-Mini manda
    monkeypatch.setenv("GMINI_KEY_OPENAI_API", "sk-explicita")
    assert vault.get_api_key("openai_api") == "sk-explicita"  # salvo GMINI_KEY_*
    del memory_keyring[(vault.SERVICE_NAME, "openai_api")]
    monkeypatch.delenv("GMINI_KEY_OPENAI_API")
    assert vault.get_api_key("openai_api") == "sk-estandar"  # y si no hay nada, la estándar
    assert vault.env_name("github-models.api") == "GMINI_KEY_GITHUB_MODELS_API"


def test_config_and_integrations_use_the_vault(no_keyring):
    from backend.config import config
    from backend.security.vault import get_api_key  # Slack y Home Assistant importan desde aquí

    config.set_api_key("home_assistant", "token-ha")
    assert config.get_api_key("home_assistant") == "token-ha" == get_api_key("home_assistant")


def test_agent_cannot_read_the_secrets_file():
    from backend.config import ROOT_DIR
    from backend.core.workspace_manager import credential_read_reason

    assert credential_read_reason((ROOT_DIR / "data" / "runtime" / "secrets.json").resolve())
