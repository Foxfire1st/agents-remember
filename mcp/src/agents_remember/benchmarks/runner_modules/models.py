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
class BenchmarkWorkspace:
    """One benchmark case's materialized with-memory workspace.

    The case plus the roots that were laid down for it. Prepared once by
    ``prepare_case`` and then handed as a whole to workspace registration.
    """

    case: BenchmarkCase
    workspace_root: Path
    coordination_root: Path
    source_repo_root: Path
    memory_repo: Path


@dataclass(frozen=True)
class BenchmarkTask:
    """One prompt/variant/repetition scheduled inside a benchmark run."""

    prompt: dict[str, Any]
    variant: dict[str, Any]
    repetition: int


@dataclass(frozen=True)
class BenchmarkRun:
    """One benchmark case execution: what it reads, where results land, how Codex runs."""

    benchmarks_root: Path
    case: BenchmarkCase
    output_root: Path
    dry_run: bool
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX


@dataclass(frozen=True)
class BenchmarkPreparation:
    """How a benchmark workspace is (re)materialized before it is used.

    Shared by prepare and run: both requests project onto this via their
    ``preparation`` property, so ``prepare_case`` takes one object instead of
    the same fields unpacked at every layer.
    """

    benchmarks_root: Path
    dry_run: bool = True
    force_clone: bool = False


@dataclass(frozen=True)
class BenchmarkRunOutcome:
    """What one ``codex_benchmark_run`` produced, before it is rendered as a payload."""

    cases: list[BenchmarkCase]
    output_roots: list[Path]
    messages: list[str]
    codex_executable: str


@dataclass(frozen=True)
class BenchmarkPrepareRequest:
    benchmarks_root: Path
    target: str = "all"
    case_id: str | None = None
    dry_run: bool = True
    force_clone: bool = False

    @property
    def preparation(self) -> BenchmarkPreparation:
        return BenchmarkPreparation(
            benchmarks_root=self.benchmarks_root.resolve(),
            dry_run=self.dry_run,
            force_clone=self.force_clone,
        )


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
    force_clone: bool = False
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX

    @property
    def preparation(self) -> BenchmarkPreparation:
        return BenchmarkPreparation(
            benchmarks_root=self.benchmarks_root.resolve(),
            dry_run=self.dry_run,
            force_clone=self.force_clone,
        )
