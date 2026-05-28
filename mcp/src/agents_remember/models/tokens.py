"""Token accounting helpers for modeled tool responses."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import tiktoken

from agents_remember.models.base import ToolResponse


class ResponseTokenCounter(Protocol):
    """Counts tokens for the canonical JSON form of a tool response."""

    @property
    def name(self) -> str: ...

    @property
    def exact(self) -> bool: ...

    def count(self, payload: Mapping[str, Any]) -> int:
        """Return the token count for payload."""
        ...


def _payload_json(payload: Mapping[str, Any]) -> str:
    """Return the canonical JSON form used for response token accounting."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class ApproximateTokenCounter:
    """Deterministic fallback counter based on compact JSON character length."""

    name: str = "approx:json_chars_div_4"
    exact: bool = False

    def count(self, payload: Mapping[str, Any]) -> int:
        text = _payload_json(payload)
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class TiktokenTokenCounter:
    """Token counter for a named tiktoken encoding."""

    encodingName: str = "o200k_base"
    exact: bool = True
    _encoding: tiktoken.Encoding = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_encoding", tiktoken.get_encoding(self.encodingName))

    @property
    def name(self) -> str:
        return f"tiktoken:{self.encodingName}"

    def count(self, payload: Mapping[str, Any]) -> int:
        return len(self._encoding.encode(_payload_json(payload)))


DEFAULT_TOKEN_COUNTER = TiktokenTokenCounter()


def count_response_tokens(
    payload: Mapping[str, Any],
    *,
    token_counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> int:
    """Return the token count for a JSON payload using the configured counter."""

    return token_counter.count(payload)


def _finalize_token_count(
    payload: dict[str, Any],
    *,
    token_counter: ResponseTokenCounter,
) -> int:
    """Set a stable token count that includes the token metadata fields."""

    previous = None
    while previous != payload["tokens"]:
        previous = payload["tokens"]
        payload["tokens"] = token_counter.count(payload)
    return payload["tokens"]


def response_payload(
    response: ToolResponse,
    *,
    token_counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> dict[str, Any]:
    """Serialize a tool response and add token accounting metadata."""

    payload = response.model_dump(mode="json", exclude_none=True)
    payload["tokens"] = 0
    payload["tokenizer"] = token_counter.name
    payload["tokenCountExact"] = token_counter.exact
    _finalize_token_count(payload, token_counter=token_counter)
    return payload


def dump_with_token_count(
    response: ToolResponse,
    *,
    counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> dict[str, Any]:
    """Backward-compatible alias for response_payload."""

    return response_payload(response, token_counter=counter)
