from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.config import LLMSection
from app.engine.llm_client import (
    LLMClient,
    LLMError,
    LLMResult,
    resolve_litellm_model,
)


def test_resolve_litellm_model():
    # Gemini
    assert resolve_litellm_model("gemini-2.5-flash", "gemini") == "gemini/gemini-2.5-flash"
    assert resolve_litellm_model("gemini/gemini-2.5-flash", "gemini") == "gemini/gemini-2.5-flash"
    assert resolve_litellm_model("google/gemini-2.5-flash", "gemini") == "google/gemini-2.5-flash"

    # OpenRouter
    assert resolve_litellm_model("anthropic/claude-3.5-sonnet", "openrouter") == "openrouter/anthropic/claude-3.5-sonnet"
    assert resolve_litellm_model("openrouter/anthropic/claude-3.5-sonnet", "openrouter") == "openrouter/anthropic/claude-3.5-sonnet"

    # DeepSeek
    assert resolve_litellm_model("deepseek-chat", "deepseek") == "deepseek/deepseek-chat"

    # Groq
    assert resolve_litellm_model("llama-3.3-70b-versatile", "groq") == "groq/llama-3.3-70b-versatile"

    # Anthropic
    assert resolve_litellm_model("claude-3-5-sonnet-20241022", "anthropic") == "claude-3-5-sonnet-20241022"

    # OpenAI
    assert resolve_litellm_model("gpt-4o", "openai") == "openai/gpt-4o"

    # Ollama
    assert resolve_litellm_model("llama3.2", "ollama") == "ollama/llama3.2"


@pytest.mark.asyncio
async def test_llm_client_generate_success():
    cfg = LLMSection(
        provider="openrouter",
        chat_model="anthropic/claude-3.5-sonnet",
        utility_model="google/gemini-2.5-flash",
        max_retries=1,
    )
    store = AsyncMock()
    client = LLMClient("fake-key", cfg, store=store)

    fake_resp = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = "Halo dari Claude!"
    fake_resp.choices = [fake_choice]
    fake_resp.usage.prompt_tokens = 45
    fake_resp.usage.completion_tokens = 12

    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_resp)) as mock_acomp:
        res = await client.generate("Halo!", purpose="test_chat")

        assert res.text == "Halo dari Claude!"
        assert res.model == "openrouter/anthropic/claude-3.5-sonnet"
        assert res.input_tokens == 45
        assert res.output_tokens == 12
        assert res.latency_ms >= 0

        # Verify litellm call
        mock_acomp.assert_called_once()
        call_kwargs = mock_acomp.call_args[1]
        assert call_kwargs["model"] == "openrouter/anthropic/claude-3.5-sonnet"
        assert call_kwargs["api_key"] == "fake-key"
        assert call_kwargs["messages"] == [{"role": "user", "content": "Halo!"}]

        # Verify store logging
        store.log_llm_call.assert_called_once_with(
            purpose="test_chat",
            model="openrouter/anthropic/claude-3.5-sonnet",
            input_tokens=45,
            output_tokens=12,
            latency_ms=res.latency_ms,
            success=True,
        )


@pytest.mark.asyncio
async def test_llm_client_generate_retries_and_fails():
    cfg = LLMSection(
        provider="gemini",
        chat_model="gemini-2.5-flash",
        utility_model="gemini-2.5-flash",
        max_retries=1,
    )
    store = AsyncMock()
    client = LLMClient("fake-key", cfg, store=store)

    with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("connection error"))) as mock_acomp:
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(LLMError, match="LLM call failed after 2 attempts"):
                await client.generate("Halo", purpose="chat")

        assert mock_acomp.call_count == 2
        store.log_llm_call.assert_called_with(
            purpose="chat",
            model="gemini/gemini-2.5-flash",
            input_tokens=None,
            output_tokens=None,
            latency_ms=0,
            success=False,
        )


@pytest.mark.asyncio
async def test_llm_client_json_mode():
    cfg = LLMSection(
        provider="openai",
        chat_model="gpt-4o",
        utility_model="gpt-4o-mini",
        max_retries=0,
    )
    client = LLMClient("fake-key", cfg)

    fake_resp = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = '{"facts": []}'
    fake_resp.choices = [fake_choice]
    fake_resp.usage = None

    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_resp)) as mock_acomp:
        res = await client.generate(
            "Ekstrak fakta",
            response_mime_type="application/json",
            purpose="extract",
        )
        assert res.text == '{"facts": []}'
        call_kwargs = mock_acomp.call_args[1]
        assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_llm_client_passes_reasoning_effort():
    cfg = LLMSection(
        provider="openrouter",
        chat_model="nvidia/nemotron-3.5-super-vl:free",
        utility_model="nvidia/nemotron-3.5-super-vl:free",
        reasoning_effort="none",
        max_retries=0,
    )
    client = LLMClient("fake-key", cfg)

    fake_resp = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = "Halo!"
    fake_resp.choices = [fake_choice]
    fake_resp.usage = None

    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_resp)) as mock_acomp:
        res = await client.generate("Halo!", purpose="chat")
        assert res.text == "Halo!"
        call_kwargs = mock_acomp.call_args[1]
        assert call_kwargs.get("reasoning_effort") == "none"


@pytest.mark.asyncio
async def test_llm_client_reasoning_exhaustion_error():
    cfg = LLMSection(
        provider="openrouter",
        chat_model="nvidia/nemotron-3.5-super-vl:free",
        utility_model="nvidia/nemotron-3.5-super-vl:free",
        max_retries=0,
    )
    client = LLMClient("fake-key", cfg)

    fake_resp = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = None
    fake_choice.message.reasoning_content = "thinking..."
    fake_choice.finish_reason = "length"
    fake_resp.choices = [fake_choice]
    fake_resp.usage = None

    with patch("litellm.acompletion", new=AsyncMock(return_value=fake_resp)):
        with pytest.raises(LLMError, match="exhausted max_tokens .* during reasoning"):
            await client.generate("Halo!", purpose="chat")

