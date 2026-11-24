"""Chat models behind one setting, so a run can move from a local Ollama
model to Claude without code changes."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel

DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"

M = TypeVar("M", bound=BaseModel)
Ask = Callable[[str, str, type[M]], M]  # (system, user, schema) -> an instance of schema

MAX_REPLY_TOKENS = 4096  # Ollama: a stuck local model fails fast instead of generating for minutes
CLAUDE_MAX_TOKENS = 16000  # Claude thinks before answering, and thinking counts as output
KEY_FILE = Path("~/.config/groomroom/anthropic_key").expanduser()  # outside the repo, never committed; the project's old name
# $ per million tokens (input, output), for reporting what a run cost.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}
# Claude models that accept server-side refusal fallbacks in the "default" form.
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5"}


def chat_model(provider: str, model: str | None = None, temperature: float = 0,
               effort: str | None = None) -> BaseChatModel:
    """`temperature` applies to Ollama only: current Claude models reject
    sampling settings. `effort` applies to Claude only."""
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=model or DEFAULT_OLLAMA_MODEL, temperature=temperature, num_ctx=16384,
                          num_predict=MAX_REPLY_TOKENS)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = model or DEFAULT_ANTHROPIC_MODEL
        # Automatic prompt caching: an agent re-sends its whole history on every
        # tool call, and the repeated start is billed at a tenth of the price.
        kwargs: dict = {"cache_control": {"type": "ephemeral"}}
        extra: dict = {}
        if model in FALLBACK_MODELS:
            # A declined request is retried on another model inside the same call.
            extra = {"betas": ["server-side-fallback-2026-07-01"]}
            kwargs["fallbacks"] = "default"
        extra["model_kwargs"] = kwargs
        return ChatAnthropic(model=model, max_tokens=CLAUDE_MAX_TOKENS, effort=effort, api_key=anthropic_key(), **extra)
    raise ValueError(f"Unknown provider: {provider}")


def anthropic_key() -> str:
    """ANTHROPIC_API_KEY, or the key file. Never printed or logged."""
    if key := os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return key
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    raise RuntimeError(f"No Anthropic API key: set ANTHROPIC_API_KEY or put the key in {KEY_FILE}")


def parse(chat: BaseChatModel, schema: type[BaseModel]) -> Runnable:
    """The chat model, returning replies parsed into `schema`. JSON-schema mode
    on both providers: Claude rejects the forced tool call LangChain
    otherwise uses, and Ollama enforces the schema while generating."""
    return chat.with_structured_output(schema, method="json_schema")


def structured(chat: BaseChatModel) -> Ask:
    """An `ask` function that returns the reply parsed into a pydantic model."""

    def ask(system: str, user: str, schema: type[M]) -> M:
        return parse(chat, schema).invoke([SystemMessage(system), HumanMessage(user)])

    return ask


def cost(usage_by_model: dict[str, dict]) -> float:
    """Dollars for LangChain usage metadata keyed by model name. Local models
    and unknown ones count as free."""
    total = 0.0
    for model, usage in usage_by_model.items():
        price_in, price_out = next((p for name, p in PRICES.items() if model.startswith(name)), (0, 0))
        details = usage.get("input_token_details") or {}
        read, written = details.get("cache_read") or 0, details.get("cache_creation") or 0
        uncached = usage.get("input_tokens", 0) - read - written  # LangChain's input_tokens includes both
        # Cache reads cost a tenth of the input price; writing to the cache, 1.25 times.
        total += (uncached + 0.1 * read + 1.25 * written) * price_in / 1e6 + usage.get("output_tokens", 0) * price_out / 1e6
    return total
