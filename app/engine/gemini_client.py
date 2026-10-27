"""Gemini client wrapper: retries, timeouts, token/latency logging."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from app.config import LLMSection

log = logging.getLogger(__name__)


@dataclass
class GeminiResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(self, api_key: str, cfg: LLMSection, store: Any = None) -> None:
        self._client = genai.Client(api_key=api_key)
        self._cfg = cfg
        self._store = store

    def set_store(self, store: Any) -> None:
        self._store = store

    async def generate(
        self,
        contents: str | list,
        *,
        model: str | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_mime_type: str | None = None,
        purpose: str = "chat",
    ) -> GeminiResult:
        model = model or self._cfg.chat_model
        gen_cfg = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type=response_mime_type,
        )

        attempts = self._cfg.max_retries + 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                resp = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=model, contents=contents, config=gen_cfg
                    ),
                    timeout=self._cfg.request_timeout_seconds,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                text = (resp.text or "").strip()
                usage = resp.usage_metadata
                result = GeminiResult(
                    text=text,
                    model=model,
                    input_tokens=getattr(usage, "prompt_token_count", None),
                    output_tokens=getattr(usage, "candidates_token_count", None),
                    latency_ms=latency_ms,
                )
                log.info(
                    "llm_call purpose=%s model=%s attempt=%d latency_ms=%d in_tokens=%s out_tokens=%s empty=%s",
                    purpose, model, attempt, latency_ms,
                    result.input_tokens, result.output_tokens, not text,
                )
                if self._store:
                    try:
                        await self._store.log_llm_call(
                            purpose=purpose,
                            model=model,
                            input_tokens=result.input_tokens,
                            output_tokens=result.output_tokens,
                            latency_ms=latency_ms,
                            success=bool(text),
                        )
                    except Exception as log_err:
                        log.debug("failed_to_log_llm_call error=%r", log_err)
                if not text:
                    raise GeminiError("empty response (possibly blocked by safety filters)")
                return result
            except Exception as exc:  # network, quota, timeout, empty
                last_exc = exc
                log.warning(
                    "llm_call_failed purpose=%s model=%s attempt=%d/%d error=%r",
                    purpose, model, attempt, attempts, exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(min(2 ** attempt, 20) + random.uniform(0, 0.5))

        if self._store:
            try:
                await self._store.log_llm_call(
                    purpose=purpose,
                    model=model,
                    input_tokens=None,
                    output_tokens=None,
                    latency_ms=0,
                    success=False,
                )
            except Exception as log_err:
                log.debug("failed_to_log_llm_call error=%r", log_err)

        raise GeminiError(f"Gemini call failed after {attempts} attempts: {last_exc!r}") from last_exc
