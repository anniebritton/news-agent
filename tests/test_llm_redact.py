from unittest.mock import MagicMock, patch

import pytest

from newsagent.errors import ConfigError
from newsagent.llm import GeminiProvider, build_provider
from newsagent.redact import redact


def test_gemini_falls_back_across_models():
    with patch("google.genai.Client") as client_cls:
        models = client_cls.return_value.models
        models.generate_content.side_effect = [RuntimeError("model gone"), MagicMock(text="hello")]
        p = GeminiProvider("key", models=["m1", "m2"])
        assert p.generate("prompt") == "hello"
        calls = [c.kwargs for c in models.generate_content.call_args_list]
        assert [c["model"] for c in calls] == ["m1", "m2"]
        assert calls[0]["config"] == {"temperature": 0.0} and calls[0]["contents"] == "prompt"


def test_gemini_returns_none_when_all_fail_and_redacts_the_key(caplog):
    with patch("google.genai.Client") as client_cls:
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("bad key=AIzaSECRETKEY123")
        assert GeminiProvider("AIzaSECRETKEY123", models=["m1"]).generate("p") is None
    assert "AIzaSECRETKEY123" not in caplog.text


def test_missing_key_is_a_friendly_config_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        build_provider({})
    with pytest.raises(ConfigError):
        build_provider({"llm": {"provider": "nope"}})


def test_redact_telegram_tokens_and_env_secrets(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "supersecretvalue99")
    out = redact("url https://api.telegram.org/bot123456:ABC-def_123/sendMessage key supersecretvalue99")
    assert "ABC-def_123" not in out and "supersecretvalue99" not in out
    assert redact("plain text") == "plain text"


def test_percent_encoded_token_forms_are_redacted():
    out = redact("GET https://api.telegram.org/bot123456%3ASECRETVALUE/sendMessage failed")
    assert "SECRETVALUE" not in out
    out = redact("see 123456%3ASECRETVALUE and 123456:SECRETVALUE", ["123456:SECRETVALUE"])
    assert "SECRETVALUE" not in out
