"""Single-invocation hook scope batch emission for Git hook tiers."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from agents_remember_test_support.code_quality.scope import (
    GateScope,
    derive_scope,
    validate_quality_config,
)

HOOK_GENERATED_SCRIPTS: tuple[tuple[str, Path], ...] = (
    ("projection", Path("scripts/sync-projection-types.py")),
    ("skills", Path("scripts/renew-instructions.py")),
    ("runtime", Path("scripts/sync-runtime.py")),
)

HOOK_FIXED_STEPS: tuple[str, ...] = (
    "ruff",
    "ruff-format",
    "pyrefly",
    "evidence-lifecycle",
)


@dataclass(frozen=True)
class HookBatchRequest:
    """Typed boundary for one single-invocation hook scope batch."""

    project_root: Path
    tier: str
    output_dir: Path


@dataclass(frozen=True)
class HookScopeLineBuilders:
    """Scope line renderers supplied by scope_reporting without circular imports."""

    invocation_env: str
    validate_invocation: Callable[[dict[str, str] | None], None]
    wrapper_line: Callable[..., str]
    untracked_lines: Callable[[GateScope], list[str]]
    generated_line: Callable[[Path, str, Path], str]
    fixed_step_line: Callable[..., str]


def _collect_generated_lines(
    project_root: Path,
    generated_line: Callable[[Path, str, Path], str],
) -> list[tuple[str, str]]:
    with ThreadPoolExecutor(max_workers=len(HOOK_GENERATED_SCRIPTS)) as pool:
        futures = [
            (name, pool.submit(generated_line, project_root, name, script))
            for name, script in HOOK_GENERATED_SCRIPTS
        ]
        return [(f"generated-{name}", future.result()) for name, future in futures]


def collect_hook_scope_batch(
    project_root: Path,
    tier: str,
    builders: HookScopeLineBuilders,
    *,
    environment: dict[str, str] | None = None,
    python_executable: str = sys.executable,
) -> dict[str, str]:
    """Derive scope once and render every hook-tier scope payload in fixed order."""
    env = dict(os.environ) if environment is None else environment
    validate_quality_config(project_root)
    builders.validate_invocation(env)
    scope = derive_scope(project_root)

    lines: dict[str, str] = {
        "hook-tier": builders.wrapper_line(project_root, scope, env, name=f"{tier}-tier"),
    }
    if env.get(builders.invocation_env) == "pre-commit-sequencer":
        lines["untracked"] = "\n".join(builders.untracked_lines(scope))

    for key, line in _collect_generated_lines(project_root, builders.generated_line):
        lines[key] = line

    for step in HOOK_FIXED_STEPS:
        lines[step] = builders.fixed_step_line(
            step,
            project_root,
            scope,
            python_executable=python_executable,
        )
    return lines


def write_hook_scope_batch(output_dir: Path, lines: Mapping[str, str]) -> None:
    """Persist each precomputed scope payload to one file under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for key, content in lines.items():
        (output_dir / key).write_text(f"{content}\n", encoding="utf-8")
