"""OpenAI-compatible chat-completions provider — BYOK remote APIs.

Works with any /chat/completions endpoint: OpenAI, Groq, OpenRouter,
DeepSeek, Together, LM Studio, vLLM, llama.cpp server.
`format_json` is a no-op here (no cross-provider standard) — JSON mode
relies on the prompt plus the fence-repair in generate_json().
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("openai_compat")


def _extract_content(r: httpx.Response) -> str:
    """Pull message content from a chat-completions response.

    Some OpenAI-compatible routers return malformed SSE even without stream=true:
    leading whitespace, the full completion object, then a glued "data: [DONE]"
    with no newline separators. Handle plain JSON, proper SSE, and that mess.
    """
    # 1) clean plain-JSON response (normal case)
    try:
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        pass
    try:
        body = (r.text or "").strip()
    except Exception:
        body = ""
    if not body:
        raise ProviderError("api empty response")
    # Malformed/glued SSE (router glitch): "{...}data: [DONE]" with no newlines.
    # Walk the body with raw_decode: parse each embedded JSON object, skip "data:"-ish junk.
    decoder = json.JSONDecoder()
    content = ""
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch.isspace() or ch == "d":  # whitespace or "data:" fragment like [DONE]
            i += 1
            continue
        if not ch.isdigit() and ch not in "-{tnf[" :
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(body, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict) and ("choices" in obj):
            for ch in obj.get("choices") or []:
                msg = ch.get("message") or {}
                delta = ch.get("delta") or {}
                content += (delta.get("content")
                            or msg.get("content")
                            or msg.get("reasoning_content")
                            or "") or ""
        i = end
    if content:
        return content
    # last resort: find the first balanced JSON object in the body
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(body)
        return obj["choices"][0]["message"]["content"]
    except Exception as e:
        raise ProviderError(f"api unparseable response ({e}): {body[:200]}") from e


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 180.0,
                 fallback_models: list[str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.fallback_models = fallback_models or []

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30),
           retry=retry_if_exception_type(ProviderError), reraise=True)
    def generate(self, prompt: str, model: str | None = None,
                 system: str | None = None, format_json: bool = False,
                 temperature: float = 0.7, max_tokens: int | None = None,
                 stream: bool = False) -> str:
        # Fallback chain: try each configured model until one succeeds.
        # Per-model retry (3x, exponential) is the decorator's job; this loop
        # advances to the next model when a provider is truly down.
        chain = [model or self.model] + [m for m in self.fallback_models
                                         if m != (model or self.model)]
        last_err: Exception | None = None
        for m in chain:
            try:
                return self._generate_once(prompt, model=m, system=system,
                                           format_json=format_json,
                                           temperature=temperature,
                                           max_tokens=max_tokens, stream=stream)
            except ProviderError as e:
                last_err = e
                import logging
                logging.getLogger(__name__).warning(
                    "LLM model %s failed (%s) → trying next fallback", m, e)
        raise ProviderError(f"all {len(chain)} LLM models failed; last error: {last_err}")

    def _generate_once(self, prompt: str, model: str | None = None,
                       system: str | None = None, format_json: bool = False,
                       temperature: float = 0.7, max_tokens: int | None = None,
                       stream: bool = False) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                r.raise_for_status()
                return _extract_content(r)
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"api {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.TimeoutException as e:
            raise ProviderError(f"api timeout after {self.timeout}s") from e
        except (KeyError, IndexError) as e:
            raise ProviderError(f"api unexpected response shape: {e}") from e
        except Exception as e:
            raise ProviderError(f"api error: {e}") from e

    def generate_json(self, prompt: str, model: str | None = None,
                      system: str | None = None,
                      temperature: float = 0.2) -> dict:
        raw = self.generate(prompt, model=model, system=system, temperature=temperature)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(cleaned)
