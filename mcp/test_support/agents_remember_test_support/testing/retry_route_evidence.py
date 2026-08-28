"""Controlled candidate mutations for non-accepting Dagger retry evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from enum import StrEnum
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import run_git

from agents_remember_test_support.code_quality.retry_proof import CACHE_DIRECTORY
from agents_remember_test_support.testing.dagger_admission import require_dagger_admission


class RetryEvidenceScenario(StrEnum):
    PRODUCT = "product"
    TEST = "test"
    SUPPORT = "support"
    PLUGIN = "plugin"
    FIXTURE = "fixture"
    UNKNOWN = "unknown"
    LANE = "lane"
    CONTEXT = "context"
    CORRUPT = "corrupt"
    DISABLED = "disabled"


PLAN_ONLY_SCENARIOS = frozenset(
    {
        RetryEvidenceScenario.UNKNOWN,
        RetryEvidenceScenario.LANE,
        RetryEvidenceScenario.CONTEXT,
        RetryEvidenceScenario.CORRUPT,
        RetryEvidenceScenario.DISABLED,
    }
)
LINT_STOP_PATH = Path("mcp/tests/test_atomic_write.py")
# Keep the seed on a source-derived one-consumer product leaf. Every scenario repairs this
# deliberate uncovered line before applying its own mutation; using a central owner here would
# legitimately add most of the suite to every affected-consumer experiment and falsify the matrix.
SEED_FAILURE_PATH = Path("mcp/src/agents_remember/cli/context_packet.py")
SEED_FAILURE_MARKER = "_retry_evidence_uncovered_branch"
MUTATION_PATHS = {
    # The scenario transitions from the seed candidate back to the real source and then applies
    # its harmless product mutation to that same low-fan-out owner. Choosing a central primitive
    # here would correctly select almost the entire suite and would test fan-out, not targeted
    # product-consumer retry behavior.
    RetryEvidenceScenario.PRODUCT: SEED_FAILURE_PATH,
    RetryEvidenceScenario.TEST: Path("mcp/tests/test_atomic_write.py"),
    RetryEvidenceScenario.SUPPORT: Path("mcp/tests/_evidence_catalog_fixture.py"),
    RetryEvidenceScenario.PLUGIN: Path(
        "mcp/test_support/agents_remember_test_support/pytest_certifying_bootstrap.py"
    ),
    RetryEvidenceScenario.FIXTURE: Path("mcp/tests/fixtures/codex_app_server_0_144_3.json"),
    RetryEvidenceScenario.UNKNOWN: Path("mcp/tests/fixtures/retry-evidence-unknown.bin"),
    RetryEvidenceScenario.LANE: Path("mcp/tests/test-evidence-lanes.toml"),
    RetryEvidenceScenario.CONTEXT: Path("mcp/tests/test_atomic_write.py"),
    RetryEvidenceScenario.CORRUPT: LINT_STOP_PATH,
    RetryEvidenceScenario.DISABLED: LINT_STOP_PATH,
}


def mutate_candidate(
    project_root: Path,
    scenario: RetryEvidenceScenario,
    *,
    cache_root: Path | None,
) -> dict[str, object]:
    """Apply one explicit evidence mutation and stage the complete candidate."""

    require_dagger_admission(subject="Agents Remember retry-route evidence")
    root = project_root.resolve()
    relative = MUTATION_PATHS[scenario]
    path = root / relative
    if scenario is RetryEvidenceScenario.CORRUPT:
        if cache_root is None:
            raise RuntimeError("corrupt retry evidence requires the explicit cache root")
        _corrupt_cache(cache_root)
    elif scenario is RetryEvidenceScenario.DISABLED:
        pass
    elif scenario is RetryEvidenceScenario.FIXTURE:
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif scenario is RetryEvidenceScenario.UNKNOWN:
        path.write_bytes(b"unowned retry evidence input\n")
    elif scenario is RetryEvidenceScenario.LANE:
        _move_lane_member(path)
    else:
        _append_comment(path, scenario)
    if scenario in PLAN_ONLY_SCENARIOS:
        _append_lint_stop(root / LINT_STOP_PATH)
    staged = run_git(root, ["add", "--all"])
    if staged.returncode != 0:
        raise RuntimeError(staged.stderr.strip() or "retry evidence mutation could not stage")
    payload = {
        "schemaVersion": "ar-retry-route-mutation/v1",
        "scenario": scenario.value,
        "mutatedPath": relative.as_posix(),
        "planOnly": scenario in PLAN_ONLY_SCENARIOS,
        "lintStopPath": LINT_STOP_PATH.as_posix() if scenario in PLAN_ONLY_SCENARIOS else None,
    }
    return payload


def prepare_seed_failure(project_root: Path) -> dict[str, object]:
    """Create one real post-pytest diff-coverage failure for proof publication."""

    require_dagger_admission(subject="Agents Remember retry-route evidence")
    root = project_root.resolve()
    path = root / SEED_FAILURE_PATH
    source = path.read_text(encoding="utf-8")
    if SEED_FAILURE_MARKER in source:
        raise RuntimeError("retry evidence seed failure already exists")
    path.write_text(
        source
        + "\n\n"
        + "def _retry_evidence_uncovered_branch(value: bool) -> bool:\n"
        + "    return not value\n",
        encoding="utf-8",
    )
    staged = run_git(root, ["add", "--all"])
    if staged.returncode != 0:
        raise RuntimeError(staged.stderr.strip() or "retry seed candidate could not stage")
    return {
        "schemaVersion": "ar-retry-route-seed/v1",
        "mutatedPath": SEED_FAILURE_PATH.as_posix(),
        "failure": "post-pytest-diff-coverage",
    }


def clone_evidence_cache(source_root: Path, destination_root: Path) -> None:
    """Clone one immutable seed proof into a scenario-owned evidence namespace."""

    require_dagger_admission(subject="Agents Remember retry-route evidence")
    source = _evidence_namespace(source_root, operation="clone source")
    destination = _evidence_namespace(destination_root, operation="clone destination")
    source_cache = source / CACHE_DIRECTORY
    destination_cache = destination / CACHE_DIRECTORY
    if not source_cache.is_dir():
        raise RuntimeError("retry evidence clone requires an existing seed proof")
    if destination_cache.exists():
        raise RuntimeError("retry evidence clone destination already exists")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_cache, destination_cache)


def _append_comment(path: Path, scenario: RetryEvidenceScenario) -> None:
    source = path.read_text(encoding="utf-8")
    path.write_text(
        source.rstrip("\n") + f"\n\n\n# Non-accepting retry evidence mutation: {scenario.value}.\n",
        encoding="utf-8",
    )


def _append_lint_stop(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    path.write_text(
        source + "\nimport retry_evidence_deliberate_lint_stop\n",
        encoding="utf-8",
    )


def _move_lane_member(path: Path) -> None:
    member = '  "mcp/tests/test_causal_failure_localization.py",\n'
    source = path.read_text(encoding="utf-8")
    if source.count(member) != 1:
        raise RuntimeError("lane evidence member does not resolve exactly once")
    source = source.replace(member, "", 1)
    anchor = "architecture-fitness = [\n"
    if source.count(anchor) != 1:
        raise RuntimeError("architecture-fitness lane anchor does not resolve exactly once")
    path.write_text(source.replace(anchor, anchor + member, 1), encoding="utf-8")


def cleanup_evidence_cache(cache_root: Path) -> None:
    """Remove only the uniquely namespaced cache created by this evidence route."""

    require_dagger_admission(subject="Agents Remember retry-route evidence")
    resolved = _evidence_namespace(cache_root, operation="cleanup")
    target = resolved / CACHE_DIRECTORY
    if target.exists():
        shutil.rmtree(target)


def _corrupt_cache(cache_root: Path) -> None:
    resolved = _evidence_namespace(cache_root, operation="corruption")
    manifest = resolved / CACHE_DIRECTORY / "manifest.json"
    if not manifest.is_file():
        raise RuntimeError("retry corruption evidence requires an existing proof manifest")
    atomic_write_text(manifest, "{not-json\n")


def _evidence_namespace(path: Path, *, operation: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_absolute() or "evidence" not in resolved.parts:
        raise RuntimeError(f"retry evidence {operation} requires an absolute evidence namespace")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    mutate = commands.add_parser("mutate")
    mutate.add_argument("--project-root", type=Path, required=True)
    mutate.add_argument("--scenario", choices=tuple(RetryEvidenceScenario), required=True)
    mutate.add_argument("--cache-root", type=Path)
    mutate.add_argument("--output", type=Path, required=True)
    seed = commands.add_parser("seed")
    seed.add_argument("--project-root", type=Path, required=True)
    seed.add_argument("--output", type=Path, required=True)
    clone = commands.add_parser("clone")
    clone.add_argument("--source-cache-root", type=Path, required=True)
    clone.add_argument("--destination-cache-root", type=Path, required=True)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--cache-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "cleanup":
        cleanup_evidence_cache(args.cache_root)
        return 0
    if args.command == "clone":
        clone_evidence_cache(args.source_cache_root, args.destination_cache_root)
        return 0
    if args.command == "seed":
        payload = prepare_seed_failure(args.project_root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            args.output,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
        return 0
    payload = mutate_candidate(
        args.project_root,
        RetryEvidenceScenario(args.scenario),
        cache_root=args.cache_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        args.output,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
