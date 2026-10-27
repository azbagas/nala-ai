"""Universal LLM client wrapper using LiteLLM.

Supports any provider (Gemini, Claude, GPT, DeepSeek, Groq, OpenRouter, Ollama, etc.)
with retries, timeouts, and token/latency logging to SQLite.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import litellm

from app.config import LLMSection

# Disable telemetry and drop unsupported parameters gracefully
litellm.telemetry = False
litellm.drop_params = True

log = logging.getLogger(__name__)


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class LLMError(Exception):
    pass


def resolve_litellm_model(model: str, provider: str | None = None) -> str:
    """Format model name with appropriate provider prefix for LiteLLM if needed."""
    model = (model or "").strip()
    if not model:
        return model

    prov = (provider or "").strip().lower()

    if prov == "openrouter":
        if not model.startswith("openrouter/"):
            return f"openrouter/{model}"
        return model

    if prov in ("gemini", "google"):
        if not (model.startswith("gemini/") or model.startswith("google/")):
            return f"gemini/{model}"
        return model

    if prov == "deepseek":
        if not model.startswith("deepseek/"):
            return f"deepseek/{model}"
        return model

    if prov == "groq":
        if not model.startswith("groq/"):
            return f"groq/{model}"
        return model

    if prov == "ollama":
        if not model.startswith("ollama/"):
            return f"ollama/{model}"
        return model

    if prov == "anthropic":
        if not (model.startswith("anthropic/") or model.startswith("claude")):
            return f"anthropic/{model}"
        return model

    if prov == "openai":
        if not model.startswith("openai/"):
            return f"openai/{model}"
        return model

    # If provider is empty or unrecognized, return model as-is
    return model


class LLMClient:
    def __init__(
        self,
        api_key: str,
        cfg: LLMSection,
        store: Any = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._cfg = cfg
        self._store = store
        self._base_url = base_url or getattr(cfg, "base_url", None)

    def set_store(self, store: Any) -> None:
        self._store = store

    async def generate(
        self,
        contents: str | list[dict[str, Any]] | list[Any],
        *,
        model: str | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_mime_type: str | None = None,
        purpose: str = "chat",
    ) -> LLMResult:
        raw_model = model or self._cfg.chat_model
        resolved_model = resolve_litellm_model(raw_model, self._cfg.provider)

        # Normalize messages into standard list of dicts: [{"role": "...", "content": "..."}]
        messages: list[dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        if isinstance(contents, str):
            messages.append({"role": "user", "content": contents})
        elif isinstance(contents, list):
            for item in contents:
                if isinstance(item, dict):
                    role = item.get("role", "user")
                    # Map legacy 'model' or 'bot' role to 'assistant'
                    if role in ("model", "bot"):
                        role = "assistant"
                    messages.append({"role": role, "content": str(item.get("content", ""))})
                elif hasattr(item, "role") and hasattr(item, "content"):
                    role = "assistant" if item.role in ("model", "bot", "assistant") else "user"
                    messages.append({"role": role, "content": str(item.content)})
                elif hasattr(item, "role") and hasattr(item, "parts"):
                    text_parts = [getattr(p, "text", str(p)) for p in item.parts]
                    role = "assistant" if item.role in ("model", "bot", "assistant") else "user"
                    messages.append({"role": role, "content": "".join(text_parts)})
                else:
                    messages.append({"role": "user", "content": str(item)})

        temp = temperature if temperature is not None else self._cfg.temperature_chat
        max_tokens = max_output_tokens or self._cfg.max_output_tokens_chat
        timeout = self._cfg.request_timeout_seconds
        attempts = self._cfg.max_retries + 1

        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": resolved_model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                    "api_key": self._api_key,
                }
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                if response_mime_type == "application/json":
                    kwargs["response_format"] = {"type": "json_object"}
                if getattr(self._cfg, "reasoning_effort", None):
                    kwargs["reasoning_effort"] = self._cfg.reasoning_effort

                resp = await litellm.acompletion(**kwargs)
                latency_ms = int((time.perf_counter() - started) * 1000)

                choices = getattr(resp, "choices", [])
                text = ""
                if choices:
                    msg = getattr(choices[0], "message", None)
                    text = getattr(msg, "content", "") or ""
                text = text.strip()

                usage = getattr(resp, "usage", None)
                in_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                out_tokens = getattr(usage, "completion_tokens", None) if usage else None

                result = LLMResult(
                    text=text,
                    model=resolved_model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    latency_ms=latency_ms,
                )
                log.info(
                    "llm_call purpose=%s model=%s attempt=%d latency_ms=%d in_tokens=%s out_tokens=%s empty=%s",
                    purpose, resolved_model, attempt, latency_ms,
                    result.input_tokens, result.output_tokens, not text,
                )
                if self._store:
                    try:
                        await self._store.log_llm_call(
                            purpose=purpose,
                            model=resolved_model,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            latency_ms=latency_ms,
                            success=bool(text),
                        )
                    except Exception as log_err:
                        log.debug("failed_to_log_llm_call error=%r", log_err)

                if not text:
                    choice = choices[0] if choices else None
                    finish_reason = getattr(choice, "finish_reason", None) if choice else None
                    msg_obj = getattr(choice, "message", None) if choice else None
                    tok_details = getattr(usage, "completion_tokens_details", None) if usage else None
                    has_reasoning = bool(
                        getattr(msg_obj, "reasoning_content", None)
                        or getattr(msg_obj, "reasoning", None)
                        or (tok_details and getattr(tok_details, "reasoning_tokens", 0))
                    )
                    if has_reasoning and finish_reason == "length":
                        raise LLMError(
                            f"empty response: model exhausted max_tokens ({max_tokens}) during reasoning. "
                            "Consider increasing max_output_tokens_chat or setting reasoning_effort: 'none'."
                        )
                    raise LLMError("empty response (possibly blocked by safety filters)")
                return result
            except Exception as exc:  # network, quota, timeout, empty
                last_exc = exc
                log.warning(
                    "llm_call_failed purpose=%s model=%s attempt=%d/%d error=%r",
                    purpose, resolved_model, attempt, attempts, exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(min(2 ** attempt, 20) + random.uniform(0, 0.5))

        if self._store:
            try:
                await self._store.log_llm_call(
                    purpose=purpose,
                    model=resolved_model,
                    input_tokens=None,
                    output_tokens=None,
                    latency_ms=0,
                    success=False,
                )
            except Exception as log_err:
                log.debug("failed_to_log_llm_call error=%r", log_err)

        raise LLMError(f"LLM call failed after {attempts} attempts: {last_exc!r}") from last_exc
