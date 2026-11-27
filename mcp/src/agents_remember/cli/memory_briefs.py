"""CLI adapter: refresh derived ``briefs/`` in an external memory checkout.

    agents-remember memory-briefs --repo <repo_id> [--config <MCP settings>] [--json]
    agents-remember memory-briefs --memory-root <dir> --code-root <dir> --coordination-root <dir>
        [--code-commit <sha>] [--json]

Refreshes the Git-ignored ``briefs/`` directory of a converted memory checkout paired with its
code repository (for example, after a manual ``git pull`` outside AR landing tools).

Exit status: 0 when briefs were written or already up to date, 2 when refused or failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.file_briefs.folder import WriteReport
from agents_remember.application.file_briefs.triggers import refresh_written_memory_folder
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.kernel.primitives.runtime_config import (
    load_config,
    require_config_path,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.cutover_lock import LEGACY_FORMAT_CODE, legacy_format_refusal
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.modules.git import head_commit

EXIT_OK = 0
EXIT_REFUSED = 2


@dataclass(frozen=True)
class _BriefRefreshTarget:
    memory_root: Path
    code_root: Path
    code_commit: str
    coordination_root: Path
    repo_id: str | None = None


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        default=None,
        help="Repository id declared in MCP settings (pairs memoryRoot, code checkout, and coordination root).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to the MCP settings JSON file (used with --repo).",
    )
    parser.add_argument(
        "--memory-root", type=Path, default=None, help="Explicit memory checkout directory."
    )
    parser.add_argument(
        "--code-root", type=Path, default=None, help="Explicit paired code checkout directory."
    )
    parser.add_argument(
        "--code-commit",
        default=None,
        help="Optional code commit SHA (defaults to HEAD of the paired code checkout).",
    )
    parser.add_argument(
        "--coordination-root",
        type=Path,
        default=None,
        help="Explicit coordination root (used for the derived knowledge-index cache).",
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON summary.")


def run(args: argparse.Namespace) -> int:
    target = _resolve_target(args)
    if isinstance(target, str):
        _emit({"ok": False, "error": target}, as_json=bool(args.json))
        return EXIT_REFUSED
    if not (target.memory_root / LAYOUT_MARKER_PATH).is_file():
        refusal = str(
            legacy_format_refusal(
                target.memory_root,
                operation="memory-briefs",
                subject=f"the memory checkout {target.memory_root}",
            )
        )
        _emit(
            {
                "ok": False,
                "state": LEGACY_FORMAT_CODE,
                "memoryRoot": str(target.memory_root),
                "refusal": refusal,
            },
            as_json=bool(args.json),
        )
        return EXIT_REFUSED
    result = refresh_written_memory_folder(
        target.memory_root,
        PairedCode(target.code_root, target.code_commit),
        target.coordination_root,
    )
    if isinstance(result, str):
        _emit(
            {
                "ok": False,
                "memoryRoot": str(target.memory_root),
                "codeRoot": str(target.code_root),
                "codeCommit": target.code_commit,
                "error": result,
            },
            as_json=bool(args.json),
        )
        return EXIT_REFUSED
    payload = _report_payload(target, result)
    _emit(payload, as_json=bool(args.json))
    return EXIT_OK


def _resolve_target(args: argparse.Namespace) -> _BriefRefreshTarget | str:
    if args.repo is not None:
        return _resolve_from_repo(args)
    if args.memory_root is None or args.code_root is None or args.coordination_root is None:
        return "pass --repo <id> or all of --memory-root, --code-root, and --coordination-root"
    code_commit = args.code_commit or _read_head(args.code_root)
    if isinstance(code_commit, str) and code_commit.startswith("error: "):
        return code_commit.removeprefix("error: ")
    return _BriefRefreshTarget(
        memory_root=args.memory_root.resolve(),
        code_root=args.code_root.resolve(),
        code_commit=code_commit,
        coordination_root=args.coordination_root.resolve(),
    )


def _resolve_from_repo(args: argparse.Namespace) -> _BriefRefreshTarget | str:
    try:
        config_path = (
            require_config_path(Path(args.config)) if args.config is not None else discover_config()
        )
        config = load_config(config_path)
    except (ConfigDiscoveryError, OSError, ValueError) as error:
        return f"could not load MCP settings: {error}"
    scope = config.repositories.get(args.repo)
    if scope is None:
        return f"repository {args.repo!r} is not configured in {config.config_path}"
    if scope.memory_root is None:
        return f"repository {args.repo!r} has no external memoryRoot configured"
    code_root = args.code_root.resolve() if args.code_root is not None else scope.path.resolve()
    memory_root = (
        args.memory_root.resolve()
        if args.memory_root is not None
        else Path(scope.memory_root).resolve()
    )
    coordination_root = (
        args.coordination_root.resolve()
        if args.coordination_root is not None
        else Path(config.coordination_root).resolve()
    )
    code_commit = args.code_commit or _read_head(code_root)
    if isinstance(code_commit, str) and code_commit.startswith("error: "):
        return code_commit.removeprefix("error: ")
    return _BriefRefreshTarget(
        memory_root=memory_root,
        code_root=code_root,
        code_commit=code_commit,
        coordination_root=coordination_root,
        repo_id=args.repo,
    )


def _read_head(code_root: Path) -> str:
    try:
        return head_commit(code_root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return f"error: could not read HEAD of {code_root}: {error}"


def _report_payload(target: _BriefRefreshTarget, report: WriteReport | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "memoryRoot": str(target.memory_root),
        "codeRoot": str(target.code_root),
        "codeCommit": target.code_commit,
        "briefs": 0 if report is None else report.briefs,
        "written": 0 if report is None else report.written,
        "unchanged": 0 if report is None else report.unchanged,
        "removed": 0 if report is None else report.removed,
        "seconds": 0.0 if report is None else round(report.seconds, 4),
    }
    if target.repo_id is not None:
        payload["repo"] = target.repo_id
    return payload


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    if payload.get("ok"):
        print(
            f"Refreshed briefs in {payload['memoryRoot']} "
            f"(briefs={payload['briefs']}, written={payload['written']}, "
            f"unchanged={payload['unchanged']}, removed={payload['removed']}, "
            f"seconds={payload['seconds']})."
        )
        return
    print(payload.get("refusal") or payload.get("error") or "memory-briefs failed")


__all__ = ["add_arguments", "run"]
