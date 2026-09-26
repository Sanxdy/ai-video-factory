"""LLM provider factory — BYOK API only.

The pipeline generates exclusively through the OpenAI-compatible endpoint
configured in the settings table (`llm.api_base_url` + `llm.api_key` +
`llm.api_model`). Local Ollama support was removed: there is no local
fallback, so an unreachable API fails the run loudly instead of silently
degrading to a small local model.

`llm.api_fallback_models` is a comma-separated list of further *API* models
tried in order when the primary errors or times out — same endpoint, still
the API, never a local model.
"""
from __future__ import annotations

from core.config import config
from core.database import Database
from core.errors import ModelNotAvailableError

_instances: dict[str, object] = {}
_llm_model: str | None = None


def _setting(key: str) -> str | None:
    try:
        return Database(config.db_path).get_setting(key)
    except Exception:
        return None


def reset_llm_cache() -> None:
    """Drop cached provider/model — call after settings change."""
    global _llm_model
    _instances.clear()
    _llm_model = None


def get_llm(model: str | None = None) -> "OpenAICompatProvider":  # type: ignore[name-defined]
    global _llm_model
    if model:
        _llm_model = model
    if "llm" not in _instances:
        base, key, api_model = (_setting("llm.api_base_url"),
                                _setting("llm.api_key"),
                                _setting("llm.api_model"))
        if not (base and key and api_model):
            raise ModelNotAvailableError(
                "API incomplete — set base URL, model and API key in Settings")
        from providers.llm.openai_compat import OpenAICompatProvider
        fb = _setting("llm.api_fallback_models") or ""
        fallbacks = [m.strip() for m in fb.split(",") if m.strip()]
        _instances["llm"] = OpenAICompatProvider(base, key, api_model,
                                                 fallback_models=fallbacks)
    return _instances["llm"]  # type: ignore[return-value]


def active_model() -> str:
    global _llm_model
    if _llm_model:
        return _llm_model
    name = _setting("llm.api_model")
    if not name:
        raise ModelNotAvailableError("API model not set — configure in Settings")
    _llm_model = name
    return name


def generate_parsed(kind: str, prompt: str, temperature: float = 0.2):
    """Generate + strict-parse a registry model in one call.

    Small/API models garble JSON surprisingly often (missing fields,
    string where an object belongs, prose, tool_calls) and one bad reply
    used to kill the whole pipeline. Retry up to 3× with the JSON schema
    attached, then try configured fallback models, then propagate.
    """
    import json as _json

    from core.models import model_class, parse_model

    llm, model = get_llm(), active_model()
    schema = _json.dumps(model_class(kind).model_json_schema())
    last_err = None
    for attempt in range(3):
        if attempt == 0:
            p = prompt
        else:
            p = (prompt + "\n\nYour previous reply was invalid. Reply with ONLY "
                 "minified valid JSON matching this schema — no prose, no markdown:\n"
                 + schema)
        try:
            raw = llm.generate(p, model=model, temperature=temperature,
                               format_json=True)
            return parse_model(kind, raw)
        except Exception as e:
            last_err = e
            continue
    raise last_err


def ensure_llm_ready() -> bool:
    """Verify the API provider works. Returns True when usable."""
    prov = get_llm()
    try:
        prov.generate("ping", max_tokens=1, temperature=0.0)
    except Exception as e:
        raise ModelNotAvailableError(f"API not usable: {e}")
    return True
