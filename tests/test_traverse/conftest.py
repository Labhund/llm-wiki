from __future__ import annotations

import pytest

from llm_wiki.config import WikiConfig
from llm_wiki.traverse.llm_client import LLMResponse
from llm_wiki.vault import Vault


class MockLLMClient:
    """LLM client returning scripted responses for testing."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._call_index = 0
        self.calls: list[list[dict]] = []

    async def complete(
        self, messages: list[dict], temperature: float = 0.7, **kwargs
    ) -> LLMResponse:
        self.calls.append(messages)
        if self._call_index >= len(self._responses):
            raise RuntimeError("MockLLMClient: no more scripted responses")
        content = self._responses[self._call_index]
        self._call_index += 1
        return LLMResponse(content=content, input_tokens=100, output_tokens=0)


@pytest.fixture
def vault(sample_vault):
    return Vault.scan(sample_vault)


@pytest.fixture
def config():
    return WikiConfig()
