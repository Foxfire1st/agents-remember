"""Models for benchmark MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import FlexibleToolResponse

# This is the public MCP default, so models owns it; benchmark execution imports the value rather
# than forcing the transport to import the high-rank benchmark implementation.
CODEX_BENCHMARK_SANDBOX = "default"


class CodexBenchmarkPrepareResponse(FlexibleToolResponse):
    operation: Literal["codex_benchmark_prepare"] = "codex_benchmark_prepare"
    messages: list[str] = Field(default_factory=list)


class CodexBenchmarkRunResponse(FlexibleToolResponse):
    operation: Literal["codex_benchmark_run"] = "codex_benchmark_run"
    messages: list[str] = Field(default_factory=list)
    codexExecutable: str | None = None
    codexResolution: str | None = None
    executable: str | None = None
    resolution: str | None = None
    codexExecutionPolicy: dict[str, Any] | None = None
