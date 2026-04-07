from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.daemon.llm_queue import LLMQueue
from llm_wiki.traverse.llm_client import LLMClient, LLMResponse


def _make_mock_response(content: str, total_tokens: int) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.total_tokens = total_tokens
    return resp


@pytest.mark.asyncio
async def test_complete_returns_llm_response():
    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")
    mock_resp = _make_mock_response("Hello world", 42)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        result = await client.complete([{"role": "user", "content": "Hi"}])

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello world"
    assert result.tokens_used == 42


@pytest.mark.asyncio
async def test_complete_passes_model_temperature_and_proxy_args():
    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(
        queue,
        model="openai/local-instruct",
        api_base="http://localhost:4000",
        api_key="sk-fake",
    )
    mock_resp = _make_mock_response("Answer", 50)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        await client.complete(
            [{"role": "user", "content": "test"}], temperature=0.3
        )
        mock_litellm.acompletion.assert_called_once_with(
            model="openai/local-instruct",
            messages=[{"role": "user", "content": "test"}],
            temperature=0.3,
            api_base="http://localhost:4000",
            api_key="sk-fake",
        )


@pytest.mark.asyncio
async def test_complete_omits_proxy_args_when_none():
    """When api_base/api_key are not configured, they're not passed to litellm."""
    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")  # No api_base/api_key
    mock_resp = _make_mock_response("ok", 10)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        await client.complete([{"role": "user", "content": "test"}])

    call_kwargs = mock_litellm.acompletion.call_args.kwargs
    assert "api_base" not in call_kwargs
    assert "api_key" not in call_kwargs


@pytest.mark.asyncio
async def test_complete_records_tokens_on_queue():
    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")
    mock_resp = _make_mock_response("Answer", 150)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        await client.complete([{"role": "user", "content": "test"}])

    assert queue.tokens_used == 150


@pytest.mark.asyncio
async def test_complete_routes_through_queue_semaphore():
    """Verify the call holds the semaphore slot while executing."""
    queue = LLMQueue(max_concurrent=1)
    client = LLMClient(queue, model="test-model")
    mock_resp = _make_mock_response("OK", 10)
    peak_active: list[int] = []

    async def slow_acompletion(**kwargs):
        peak_active.append(queue.active_count)
        return mock_resp

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = slow_acompletion
        result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "OK"
    assert peak_active == [1]  # semaphore was held during the call
