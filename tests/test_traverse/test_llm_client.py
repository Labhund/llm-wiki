from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_wiki.daemon.llm_queue import LLMQueue
from llm_wiki.traverse.llm_client import LLMClient, LLMResponse, _should_retry


def _make_client():
    """Create an LLMClient with a simple pass-through mock queue."""
    queue = MagicMock()
    # queue.submit(fn, priority=..., label=...) should call fn() directly
    async def submit(fn, priority="query", label="unknown", **kwargs):
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


def test_should_not_retry_litellm_auth_error_even_when_status_500():
    """litellm.AuthenticationError is permanent even if it reports status_code=500."""
    import litellm as _litellm
    exc = _litellm.AuthenticationError("no key", llm_provider="openai", model="gpt-4")
    exc.status_code = 500  # simulate litellm's unexpected status mapping
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
        resp.usage.prompt_tokens = 8
        resp.usage.completion_tokens = 2
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


def _make_mock_response(content: str, input_tokens: int, output_tokens: int) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = input_tokens
    resp.usage.completion_tokens = output_tokens
    return resp


@pytest.mark.asyncio
async def test_complete_returns_llm_response():
    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")
    mock_resp = _make_mock_response("Hello world", input_tokens=30, output_tokens=12)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        result = await client.complete([{"role": "user", "content": "Hi"}])

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello world"
    assert result.input_tokens == 30
    assert result.output_tokens == 12
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
    mock_resp = _make_mock_response("Answer", input_tokens=40, output_tokens=10)

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
    mock_resp = _make_mock_response("ok", input_tokens=8, output_tokens=2)

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
    mock_resp = _make_mock_response("Answer", input_tokens=100, output_tokens=50)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        await client.complete([{"role": "user", "content": "test"}])

    assert queue.input_tokens_total == 100
    assert queue.output_tokens_total == 50
    assert queue.tokens_used == 150


@pytest.mark.asyncio
async def test_complete_routes_through_queue_semaphore():
    """Verify the call holds the semaphore slot while executing."""
    queue = LLMQueue(max_concurrent=1)
    client = LLMClient(queue, model="test-model")
    mock_resp = _make_mock_response("OK", input_tokens=8, output_tokens=2)
    peak_active: list[int] = []

    async def slow_acompletion(**kwargs):
        peak_active.append(queue.active_count)
        return mock_resp

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = slow_acompletion
        result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "OK"
    assert peak_active == [1]  # semaphore was held during the call


@pytest.mark.asyncio
async def test_complete_passes_label_to_queue(monkeypatch):
    """LLMClient.complete() passes the label parameter to queue.submit()."""
    captured_labels: list[str] = []
    original_submit = LLMQueue.submit

    async def capturing_submit(self, fn, priority="maintenance", label="unknown", **kwargs):
        captured_labels.append(label)
        return await original_submit(self, fn, priority=priority, label=label, **kwargs)

    monkeypatch.setattr(LLMQueue, "submit", capturing_submit)

    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")

    # Patch litellm to avoid real network call
    mock_resp = _make_mock_response("response", input_tokens=10, output_tokens=0)
    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)

        await client.complete(
            [{"role": "user", "content": "hello"}],
            label="adversary:verify:test-page",
        )

    assert "adversary:verify:test-page" in captured_labels


# ─── Trace callback tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trace_fn_called_with_correct_fields():
    """When trace_fn is provided, it is awaited after each complete() call
    with a dict containing label, model, messages, response, token counts, and latency."""
    queue = LLMQueue(max_concurrent=2)
    trace_calls: list[dict] = []

    async def capture_trace(event: dict) -> None:
        trace_calls.append(event)

    client = LLMClient(
        queue, model="openai/test", api_base="http://localhost:4000",
        trace_fn=capture_trace,
    )
    mock_resp = _make_mock_response("synthesis result", input_tokens=500, output_tokens=120)
    messages = [
        {"role": "system", "content": "You are a wiki agent."},
        {"role": "user", "content": "Summarise this paper."},
    ]

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        result = await client.complete(
            messages, temperature=0.9, label="ingest:synthesize:boltz-2",
        )

    assert result.content == "synthesis result"
    assert len(trace_calls) == 1
    ev = trace_calls[0]
    assert ev["label"] == "ingest:synthesize:boltz-2"
    assert ev["model"] == "openai/test"
    assert ev["temperature"] == 0.9
    assert ev["messages"] is messages
    assert ev["response"] == "synthesis result"
    assert ev["input_tokens"] == 500
    assert ev["output_tokens"] == 120
    assert isinstance(ev["latency_s"], float)
    assert ev["latency_s"] >= 0


@pytest.mark.asyncio
async def test_trace_fn_not_called_when_none():
    """When trace_fn is not provided, complete() behaves identically to before."""
    queue = LLMQueue(max_concurrent=2)
    client = LLMClient(queue, model="test-model")  # no trace_fn
    mock_resp = _make_mock_response("ok", input_tokens=10, output_tokens=5)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "ok"


@pytest.mark.asyncio
async def test_trace_fn_called_for_every_call():
    """Each sequential complete() call fires trace_fn once."""
    queue = LLMQueue(max_concurrent=2)
    trace_calls: list[str] = []

    async def capture(event: dict) -> None:
        trace_calls.append(event["label"])

    client = LLMClient(queue, model="test-model", trace_fn=capture)
    mock_resp = _make_mock_response("ok", input_tokens=10, output_tokens=5)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        await client.complete([{"role": "user", "content": "a"}], label="call-1")
        await client.complete([{"role": "user", "content": "b"}], label="call-2")
        await client.complete([{"role": "user", "content": "c"}], label="call-3")

    assert trace_calls == ["call-1", "call-2", "call-3"]


@pytest.mark.asyncio
async def test_trace_fn_error_does_not_abort_completion():
    """A buggy trace_fn must not propagate — complete() still returns the response."""
    queue = LLMQueue(max_concurrent=2)

    async def bad_trace(event: dict) -> None:
        raise RuntimeError("trace writer is broken")

    client = LLMClient(queue, model="test-model", trace_fn=bad_trace)
    mock_resp = _make_mock_response("still works", input_tokens=10, output_tokens=5)

    with patch("llm_wiki.traverse.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
        result = await client.complete([{"role": "user", "content": "hi"}])

    assert result.content == "still works"
