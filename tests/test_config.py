from __future__ import annotations

from pathlib import Path

import pytest

from app import config as cfgmod
from app.config import ConfigError, expand_env, load_app_config, load_persona

ROOT = Path(__file__).resolve().parent.parent


def test_expand_env_with_default(monkeypatch):
    monkeypatch.delenv("FOO_X", raising=False)
    assert expand_env("${FOO_X:-bar}") == "bar"
    monkeypatch.setenv("FOO_X", "baz")
    assert expand_env({"a": ["${FOO_X}"]}) == {"a": ["baz"]}


def test_expand_env_missing_raises(monkeypatch):
    monkeypatch.delenv("NOPE_X", raising=False)
    with pytest.raises(ConfigError):
        expand_env("${NOPE_X}")


def test_shipped_config_loads(monkeypatch):
    monkeypatch.setenv("OWNER_ID", "12345")
    cfg = load_app_config(ROOT / "config" / "config.yaml")
    assert cfg.app.owner_telegram_id == 12345
    assert cfg.app.timezone == "Asia/Jakarta"
    assert {w.name for w in cfg.proactive.windows} == {"morning", "midday", "afternoon", "evening"}


def test_owner_id_empty_is_none(monkeypatch):
    monkeypatch.setenv("OWNER_ID", "")
    cfg = load_app_config(ROOT / "config" / "config.yaml")
    assert cfg.app.owner_telegram_id is None


def test_shipped_persona_loads():
    p = load_persona(ROOT / "config" / "persona.yaml")
    assert p.name == "Nala"
    assert p.pronouns.self_ == "aku"
    assert p.traits


def test_invalid_hhmm_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_ID", "1")
    text = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "config.yaml"
    bad.write_text(text.replace('nightly_job_time: "23:30"', 'nightly_job_time: "25:00"'), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_app_config(bad)


def test_missing_secrets(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "x")
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        cfgmod.load_secrets()


def test_missing_llm_api_key(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="LLM_API_KEY"):
        cfgmod.load_secrets()


def test_llm_api_key_loaded(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("LLM_API_KEY", "universal_key")
    s = cfgmod.load_secrets()
    assert s.llm_api_key == "universal_key"


def test_secrets_repr_masked():
    s = cfgmod.Secrets(telegram_bot_token="tok123", llm_api_key="key456")
    assert "tok123" not in repr(s) and "key456" not in repr(s)


def test_reasoning_effort_validation():
    cfg = cfgmod.LLMSection(
        chat_model="foo",
        utility_model="bar",
        reasoning_effort="none",
    )
    assert cfg.reasoning_effort == "none"

    cfg_empty = cfgmod.LLMSection(
        chat_model="foo",
        utility_model="bar",
        reasoning_effort="  ",
    )
    assert cfg_empty.reasoning_effort is None

    with pytest.raises(ValueError, match="reasoning_effort"):
        cfgmod.LLMSection(
            chat_model="foo",
            utility_model="bar",
            reasoning_effort="invalid_mode",
        )

