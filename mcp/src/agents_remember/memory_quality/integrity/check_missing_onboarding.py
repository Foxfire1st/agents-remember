"""Check current worktree additions for missing onboarding pairs."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel import filesystem
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationHints,
    StorageSettings,
    is_sidecar_storage,
    mirror_onboarding_path,
    normalize_rel_path,
    resolve_coordination_context,
    resolve_storage_for_source,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.integrity.onboarding_drift_check.drift import (
    extract_inline_onboarding_block,
)
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader


@dataclass(frozen=True)
class MissingOnboarding:
    source_file: str
    storage_mode: str
    expected_onboarding: str
    state: str
    note: str

    def to_dict(self) -> dict[str, str]:
        return {
            "sourceFile": self.source_file,
            "storageMode": self.storage_mode,
            "expectedOnboarding": self.expected_onboarding,
            "state": self.state,
            "note": self.note,
        }


def check_missing_onboarding(
    *,
    code_repository_root: Path,
    onboarding_root: Path,
    settings: StorageSettings,
    code_repository_name: str | None = None,
) -> dict[str, Any]:
    candidates = worktree_added_sources(code_repository_root)
    scoped_repo_path = code_repository_name or code_repository_root.name
    missing = [
        row
        for source_file in candidates
        if (
            row := missing_onboarding_for_source(
                code_repository_root, onboarding_root, settings, scoped_repo_path, source_file
            )
        )
        is not None
    ]
    return {
        "ok": not missing,
        "operation": "check_missing_onboarding",
        "codeRepositoryRoot": code_repository_root.as_posix(),
        "onboardingRoot": onboarding_root.as_posix(),
        "sourceCount": len(candidates),
        "missingCount": len(missing),
        "missing": [row.to_dict() for row in missing],
    }


def worktree_added_sources(repo_root: Path) -> list[str]:
    sources: set[str] = set()
    for args in (
        ["diff", "--cached", "--name-status", "--find-renames", "--diff-filter=ACR", "-z"],
        ["diff", "--name-status", "--find-renames", "--diff-filter=ACR", "-z"],
    ):
        sources.update(parse_name_status(require_git(repo_root, args).stdout))
    untracked = require_git(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"])
    sources.update(normalize_rel_path(path) for path in split_z(untracked.stdout))
    return sorted(sources)


def missing_onboarding_for_source(
    code_repository_root: Path,
    onboarding_root: Path,
    settings: StorageSettings,
    scoped_repo_path: str,
    source_file: str,
) -> MissingOnboarding | None:
    storage_mode = resolve_storage_for_source(source_file, settings, scoped_repo_path)
    if storage_mode == "disabled":
        return None
    if is_sidecar_storage(storage_mode):
        return _missing_sidecar_onboarding(onboarding_root, source_file, storage_mode)
    if storage_mode == "inline":
        return _missing_inline_onboarding(code_repository_root, source_file, storage_mode)
    return MissingOnboarding(
        source_file=source_file,
        storage_mode=storage_mode,
        expected_onboarding="",
        state="unsupported",
        note=f"Unsupported storage mode '{storage_mode}' for newly added worktree file.",
    )


def _missing_sidecar_onboarding(
    onboarding_root: Path, source_file: str, storage_mode: str
) -> MissingOnboarding | None:
    """The mirrored sidecar this source expects, reported when that file does not exist."""
    expected = mirror_onboarding_path(onboarding_root, source_file)
    if filesystem.exists(expected):
        return None
    return MissingOnboarding(
        source_file=source_file,
        storage_mode=storage_mode,
        expected_onboarding=expected.as_posix(),
        state="missing",
        note="Sidecar onboarding is missing for this newly added worktree file.",
    )


def _missing_inline_onboarding(
    code_repository_root: Path, source_file: str, storage_mode: str
) -> MissingOnboarding | None:
    """The in-source onboarding block, reported when it is absent or unreadable."""
    expected = f"inline:{source_file}"
    try:
        text = filesystem.read_text(code_repository_root / source_file, encoding="utf-8")
    except UnicodeDecodeError:
        return MissingOnboarding(
            source_file=source_file,
            storage_mode=storage_mode,
            expected_onboarding=expected,
            state="unsupported",
            note="Inline onboarding could not be checked because the source is not UTF-8 text.",
        )
    if extract_inline_onboarding_block(text) is not None:
        return None
    return MissingOnboarding(
        source_file=source_file,
        storage_mode=storage_mode,
        expected_onboarding=expected,
        state="missing",
        note="Inline onboarding block is missing for this newly added worktree file.",
    )


def parse_name_status(output: str) -> list[str]:
    values = split_z(output)
    paths: list[str] = []
    index = 0
    while index < len(values):
        status = values[index]
        index += 1
        if status.startswith(("R", "C")):
            index += 1
            if index < len(values):
                paths.append(normalize_rel_path(values[index]))
            index += 1
            continue
        if status.startswith("A") and index < len(values):
            paths.append(normalize_rel_path(values[index]))
        index += 1
    return paths


def split_z(output: str) -> list[str]:
    return [value for value in output.split("\0") if value]


def require_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """The one runner, plus this module's contract that any git failure is fatal.

    Returns the completed process rather than its text (the shape the other
    ``require_git`` helpers use) because every caller reads NUL-delimited output,
    which a ``.strip()`` would corrupt. This used to be a sixth copy of ``run_git``
    with the environment guard, the timeout and the explicit encoding missing.
    """

    result = run_git(repo_root, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def code_repository_name_from_git(repo_root: Path) -> str:
    common_dir = require_git(
        repo_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
    ).stdout.strip()
    if common_dir:
        common_path = Path(common_dir)
        if common_path.name == ".git":
            return common_path.parent.name
    return repo_root.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--code-repository-root",
        required=True,
        type=Path,
        help="Root directory of the code repository to check.",
    )
    parser.add_argument(
        "--onboarding-root",
        type=Path,
        help="Override for the resolved code repository onboarding root.",
    )
    parser.add_argument(
        "--topology",
        choices=("internal", "external"),
        help="Topology for this code repository. Defaults to internal when no onboarding root is supplied.",
    )
    parser.add_argument(
        "--coordination-root",
        type=Path,
        help="Coordination root. Required for --topology external unless --onboarding-root is supplied.",
    )
    parser.add_argument(
        "--settings-path",
        type=Path,
        help="Override the active settings.md path for this run.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Stdout format.",
    )
    args = parser.parse_args(argv)

    code_repository_root = args.code_repository_root.resolve()
    code_repository_name = code_repository_name_from_git(code_repository_root)
    context = resolve_coordination_context(
        code_repository_name=code_repository_name,
        workspace_root=code_repository_root.parent,
        code_repository_root=code_repository_root,
        request=CoordinationRequest(
            hints=CoordinationHints(
                topology=args.topology,
                coordination_root=args.coordination_root,
                settings_path=args.settings_path,
                onboarding_root=args.onboarding_root,
            ),
            contract_reader=WorktreeContractReader(),
        ),
    )
    if not filesystem.exists(context.onboarding_root):
        parser.error(f"onboarding root does not exist: {context.onboarding_root}")
    result = check_missing_onboarding(
        code_repository_root=code_repository_root,
        onboarding_root=context.onboarding_root,
        settings=context.storage,
        code_repository_name=code_repository_name,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print_text(result)
    return 0 if result["ok"] else 1


def print_text(result: dict[str, Any]) -> None:
    print(f"sourceCount={result['sourceCount']}")
    print(f"missingCount={result['missingCount']}")
    for row in result["missing"]:
        print(
            "\t".join(
                [
                    row["state"],
                    row["storageMode"],
                    row["sourceFile"],
                    row["expectedOnboarding"],
                    row["note"],
                ]
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
