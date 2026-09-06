"""Closing coverage for the anthropic provider (SPEC-023 / T12.5 / AC7).

The individual ACs are already covered elsewhere (test_llm_anthropic — AC1/AC5,
test_llm_provider_default — AC2, test_llm_agent_model_resolution — AC3/AC4,
test_rag_embeddings_provider_decoupled — AC6). This module closes the one public-
API path not exercised directly: ``call_llm_stream`` routing to the anthropic
streaming branch *through* the retry wrapper (``_retry_stream``).
"""
import pytest

import app.platform.llm as llm
from app.platform.llm import TransientLLMError
from app.core import config


@pytest.fixture(autouse=True)
def _anthropic_provider(monkeypatch):
    monkeypatch.setattr(config.settings, "LLM_PROVIDER", "anthropic", raising=False)
    monkeypatch.setattr(config.settings, "ANTHROPIC_MODEL", "claude-opus-5", raising=False)
    # Zero-delay retries so the retry-before-first-token test doesn't sleep.
    monkeypatch.setattr(llm, "_retry_params", lambda: (3, 0.0, 0.0))


@pytest.mark.asyncio
async def test_call_llm_stream_routes_to_anthropic(monkeypatch):
    """call_llm_stream dispatches to the anthropic streamer with the default model."""
    seen = {}

    # `temperature` la añadió #320: el doble refleja la firma real.
    async def fake_stream(prompt, model, timeout, system_prompt=None, temperature=None):
        seen["model"] = model
        seen["prompt"] = prompt
        for tok in ["Ho", "la", " mundo"]:
            yield tok

    monkeypatch.setattr(llm, "_call_anthropic_stream", fake_stream)

    tokens = [t async for t in llm.call_llm_stream("x")]

    assert "".join(tokens) == "Hola mundo"
    assert seen["model"] == "claude-opus-5"  # get_default_model() → ANTHROPIC_MODEL


@pytest.mark.asyncio
async def test_call_llm_stream_anthropic_retries_transient_before_first_token(monkeypatch):
    """A connection-level failure before any token is retried on the anthropic path."""
    attempts = {"n": 0}

    async def flaky(prompt, model, timeout, system_prompt=None, temperature=None):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise TransientLLMError("all connection attempts failed")
            yield  # unreachable — marks this as an async generator
        yield "ok"

    monkeypatch.setattr(llm, "_call_anthropic_stream", flaky)

    tokens = [t async for t in llm.call_llm_stream("x")]

    assert tokens == ["ok"]
    assert attempts["n"] == 2  # failed once, succeeded on the retry
