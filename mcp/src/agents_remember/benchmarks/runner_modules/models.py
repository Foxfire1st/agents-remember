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

    The case plus the roots that were laid down for it and the providers it
    arms. Prepared once by ``prepare_case`` and then handed as a whole to
    everything that writes the workspace registration or launches providers.
    """

    case: BenchmarkCase
    workspace_root: Path
    coordination_root: Path
    source_repo_root: Path
    memory_repo: Path
    provider_ids: tuple[str, ...] = ()


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
    the same six fields unpacked at every layer.
    """

    benchmarks_root: Path
    dry_run: bool = True
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800
    # See BenchmarkPrepareRequest.allowed_provider_ids (containment R1).
    allowed_provider_ids: tuple[str, ...] | None = None


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
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800
    # Containment R1 (260707-HFX-L1): the live MCP authority's provider ids. The
    # MCP controllers always pass this; manifest-requested providers outside the
    # set are skipped (and reported), never armed or launched. None = no
    # authority context (direct script use) and is FAIL-CLOSED by the consumer
    # (workspace.filter_benchmark_provider_ids) unless
    # AR_BENCHMARK_ALLOW_UNFILTERED_PROVIDERS=1 — the explicit developer act.
    allowed_provider_ids: tuple[str, ...] | None = None

    @property
    def preparation(self) -> BenchmarkPreparation:
        return BenchmarkPreparation(
            benchmarks_root=self.benchmarks_root.resolve(),
            dry_run=self.dry_run,
            skill_exposure_mode=self.skill_exposure_mode,
            force_clone=self.force_clone,
            provider_timeout=self.provider_timeout,
            allowed_provider_ids=self.allowed_provider_ids,
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
    skill_exposure_mode: str = "copy"
    force_clone: bool = False
    provider_timeout: int = 1800
    codex_sandbox: str = CODEX_BENCHMARK_SANDBOX
    # See BenchmarkPrepareRequest.allowed_provider_ids (containment R1).
    allowed_provider_ids: tuple[str, ...] | None = None

    @property
    def preparation(self) -> BenchmarkPreparation:
        return BenchmarkPreparation(
            benchmarks_root=self.benchmarks_root.resolve(),
            dry_run=self.dry_run,
            skill_exposure_mode=self.skill_exposure_mode,
            force_clone=self.force_clone,
            provider_timeout=self.provider_timeout,
            allowed_provider_ids=self.allowed_provider_ids,
        )
