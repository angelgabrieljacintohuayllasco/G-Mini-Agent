"""config.user.yaml guarda solo las diferencias: los defaults nuevos llegan con cada actualización."""

from __future__ import annotations

import yaml

from backend import config as config_module
from backend.config import DEFAULT_CONFIG, Config


def _fresh(monkeypatch, user_file):
    monkeypatch.setattr(config_module, "USER_CONFIG", user_file)
    instance = object.__new__(Config)
    instance._load()
    return instance


def test_first_run_writes_an_empty_user_file(tmp_path, monkeypatch):
    user_file = tmp_path / "config.user.yaml"
    cfg = _fresh(monkeypatch, user_file)
    assert (yaml.safe_load(user_file.read_text(encoding="utf-8")) or {}) == {}
    assert cfg.get("server", "host") == "127.0.0.1"


def test_old_full_copy_shrinks_to_what_the_user_changed(tmp_path, monkeypatch):
    default_text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    old_copy = (default_text
                .replace('language: "es"', 'language: "en"', 1)
                .replace('version: "', 'version: "0.0.1-', 1))
    user_file = tmp_path / "config.user.yaml"
    user_file.write_text(old_copy, encoding="utf-8")

    cfg = _fresh(monkeypatch, user_file)

    saved = yaml.safe_load(user_file.read_text(encoding="utf-8"))
    assert saved == {"app": {"language": "en"}}
    defaults = yaml.safe_load(default_text)
    assert cfg.get("app", "version") == defaults["app"]["version"]
    assert cfg.get("app", "language") == "en"


def test_user_deltas_are_kept_as_they_are(tmp_path, monkeypatch):
    user_file = tmp_path / "config.user.yaml"
    user_file.write_text("model_router:\n  default_provider: vertex\n", encoding="utf-8")
    cfg = _fresh(monkeypatch, user_file)
    assert cfg.get("model_router", "default_provider") == "vertex"
    assert user_file.read_text(encoding="utf-8") == "model_router:\n  default_provider: vertex\n"
