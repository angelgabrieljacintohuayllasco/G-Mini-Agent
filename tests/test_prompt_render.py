"""El render de prompts nunca debe romperse por llaves literales."""

from __future__ import annotations

import pytest

from backend.core import prompt_manager as pm


def test_known_placeholders_and_format_spec():
    out = pm.render_template("Hola {name}, confianza {c:.2f} {name!r}", {"name": "Ana", "c": 0.456})
    assert out == "Hola Ana, confianza 0.46 'Ana'"


def test_unknown_and_non_identifier_braces_stay_literal():
    template = 'Usa arguments={...} o {"k": 1} y {0} {} {desconocida}'
    assert pm.render_template(template, {"x": 1}) == template


def test_double_braces_become_single():
    assert pm.render_template("{{literal}} {v}", {"v": 3}) == "{literal} 3"


def test_bad_format_spec_falls_back_to_str():
    assert pm.render_template("{v:.2f}", {"v": "texto"}) == "texto"


@pytest.mark.parametrize("prompt_key", sorted(pm.CORE_PROMPT_SPECS))
def test_every_core_prompt_renders(prompt_key):
    variables = {
        "model_name": "m", "provider_name": "p", "parent_mode_name": "a", "worker_mode_name": "b",
        "max_iterations": 3, "effective_capabilities": "x", "restricted_capabilities": "y",
        "critic_confidence": 0.5, "highest_threshold": 0.7,
    }
    text = pm.render_prompt_text(prompt_key, variables=variables)
    assert isinstance(text, str)


def test_executor_prompt_mentions_only_real_file_actions():
    text = pm.render_prompt_text("subagent_executor_system", variables={"max_iterations": 3})
    for fake in ("[ACTION:file_read(", "[ACTION:file_write(", "[ACTION:dir_list(", "server=nombre_servidor"):
        assert fake not in text
    assert "server_id=" in text
