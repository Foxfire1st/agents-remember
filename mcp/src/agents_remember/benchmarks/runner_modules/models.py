from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.benchmarks.runner_modules.constants import CODEX_BENCHMARK_SANDBOX


@dataclass(frozen=True)
class BenchmarkCase:
    path: Path
    data: dict[str, Any]

    @property
    def case_id(self) -> str:
        return str(self.data["id"])

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.case_id))

    @property
    def repository(self) -> dict[str, Any]:
        return dict(self.data["repository"])

    @property
    def memory_repository(self) -> dict[str, Any]:
        return dict(self.data.get("memoryRepository", {}))

    @property
    def workspace(self) -> dict[str, Any]:
        return dict(self.data["workspace"])

    @property
    def prompts(self) -> list[dict[str, Any]]:
        return list(self.data.get("prompts", []))


@dataclass(frozen=True)
class BenchmarkPrepareRequest:
    benchmarks_root: Path
    target: str = "all"
    case_id: str | None = None
    dry_run: bool = True
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800


@dataclass(frozen=True)
class BenchmarkRunRequest:
    benchmarks_root: Path
    target: str = "all"
    case_id: str | None = None
    prompt: str | None = None
    variant: str | None = None
    repetitions: int | None = None
    jobs: int | None = None
    dry_run: bool = True
    skip_prepare: bool = False
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX
