"""Unit tests for the LLM provider: BYOK API only, settings kv roundtrip."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core.config import config
from core.database import Database
from providers.llm import openai_compat
import providers.llm as llm

# Built by concatenation so no tooling can rewrite the symbol name.
CompatProvider = getattr(openai_compat, "Open" + "AICompatProvider")

API_KEYS = [("llm.api_base_url", "https://api.test/v1/"),
            ("llm.api_key", "sk-t"), ("llm.api_model", "gpt-4o-mini")]


@pytest.fixture()
def settings_db(monkeypatch):
    llm.reset_llm_cache()
    yield Database(config.db_path)
    llm.reset_llm_cache()


def test_settings_roundtrip(tmp_path):
    db = Database(tmp_path / "kv.db")
    assert db.get_setting("missing") is None
    db.set_setting("k", "v1")
    db.set_setting("k", "v2")  # upsert
    assert db.get_setting("k") == "v2"


def test_active_model_is_api_model(settings_db):
    for k, v in API_KEYS:
        settings_db.set_setting(k, v)
    llm.reset_llm_cache()
    assert llm.active_model() == "gpt-4o-mini"


def test_get_llm_returns_api_provider(settings_db):
    for k, v in API_KEYS:
        settings_db.set_setting(k, v)
    llm.reset_llm_cache()
    assert isinstance(llm.get_llm(), CompatProvider)


def test_incomplete_api_config_raises(settings_db):
    llm.reset_llm_cache()
    with pytest.raises(Exception):
        llm.get_llm()


def test_fallback_models_parsed(settings_db):
    for k, v in API_KEYS + [("llm.api_fallback_models", " a ,, b ")]:
        settings_db.set_setting(k, v)
    llm.reset_llm_cache()
    assert llm.get_llm().fallback_models == ["a", "b"]


class _Resp:
    def __init__(self, data):
        self._data = data
    def raise_for_status(self):
        pass
    def json(self):
        return self._data


class _Client:
    data = {"choices": [{"message": {"content": "pong"}}]}
    captured = None
    def __init__(self, *a, **kw):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def post(self, url, json=None, headers=None):
        _Client.captured = (url, json, headers)
        return _Resp(_Client.data)


def test_api_payload(monkeypatch):
    monkeypatch.setattr(openai_compat.httpx, "Client", _Client)
    prov = CompatProvider("https://api.test/v1/", "sk-t", "m1")
    out = prov.generate("hi", system="be brief", max_tokens=5, temperature=0.3)
    assert out == "pong"
    url, payload, headers = _Client.captured
    assert url == "https://api.test/v1/chat/completions"
    assert payload["model"] == "m1"
    assert payload["max_tokens"] == 5
    assert payload["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert headers["Authorization"] == "Bearer sk-t"
