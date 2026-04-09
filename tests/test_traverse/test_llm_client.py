from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.daemon.llm_queue import LLMQueue
from llm_wiki.traverse.llm_client import LLMClient, LLMResponse, _should_retry


def _make_client():
    """Create an LLMClient with a simple pass-through mock queue."""
    queue = MagicMock()
    # queue.submit(fn, priority=...) should call fn() directly
    async def submit(fn, priority="query"):
        return await fn()
    queue.submit = submit
    queue.record_tokens = MagicMock()
    return LLMClient(queue=queue, model="openai/test", api_base="http://localhost:4000/v1")


def test_should_retry_connection_error():
    assert _should_retry(ConnectionError("refused")) is True


def test_should_retry_503():
    exc = Exception("service unavailable")
    exc.status_code = 503
    assert _should_retry(exc) is True


def test_should_not_retry_401():
    exc = Exception("unauthorized")
    exc.status_code = 401
    assert _should_retry(exc) is False


def test_should_not_retry_400():
    exc = Exception("bad request")
    exc.status_code = 400
    assert _should_retry(exc) is False


@pytest.mark.asyncio
async def test_complete_retries_on_transient_error():
    """complete() retries up to 3 times on transient failures then succeeds."""
    call_count = {"n": 0}

    async def fake_completion(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ConnectionError("connection refused")
        resp = MagicMock()
        resp.choices[0].message.content = "answer"
        resp.usage.total_tokens = 10
        return resp

    client = _make_client()
    with patch("litellm.acompletion", side_effect=fake_completion):
        with patch("asyncio.sleep", new_callable=AsyncMock):  # skip real delays
            result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "answer"
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_complete_does_not_retry_permanent_error():
    """complete() raises immediately on a permanent error (401)."""
    call_count = {"n": 0}

    async def fake_completion(**kwargs):
        call_count["n"] += 1
        exc = Exception("unauthorized")
        exc.status_code = 401
        raise exc

    client = _make_client()
    with patch("litellm.acompletion", side_effect=fake_completion):
        with pytest.raises(Exception, match="unauthorized"):
            await client.complete([{"role": "user", "content": "hi"}])

    assert call_count["n"] == 1  # no retries


@pytest.mark.asyncio
async def test_complete_raises_after_max_retries():
    """complete() raises after exhausting all 3 retries."""
    call_count = {"n": 0}

    async def fake_completion(**kwargs):
        call_count["n"] += 1
        raise ConnectionError("always fails")

    client = _make_client()
    with patch("litellm.acompletion", side_effect=fake_completion):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError):
                await client.complete([{"role": "user", "content": "hi"}])

    assert call_count["n"] == 4  # 1 initial + 3 retries


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
