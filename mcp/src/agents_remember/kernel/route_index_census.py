"""Repository-authoritative source census for route indexes."""

from __future__ import annotations

import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.errors import AuthorityError, RouteIndexCensusError
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.coordination_context.storage import resolve_storage_for_source
from agents_remember.kernel.git_command import GIT_METADATA_TIMEOUT_SECONDS, run_git

TRACKED_FILE_MODES = {"100644", "100755", "120000"}
NON_FILE_INDEX_MODES = {"040000", "160000"}


@dataclass(frozen=True)
class RouteIndexCandidate:
    """One exact repository candidate and its authoritative entry type."""

    path: str
    mode: str
    origin: Literal["index", "worktree"]


@dataclass(frozen=True)
class RouteIndexSourceSnapshot:
    """One build's unfiltered repository files and storage-filtered paths."""

    candidates: tuple[RouteIndexCandidate, ...]
    eligible_paths: tuple[str, ...]

    @property
    def repository_paths(self) -> tuple[str, ...]:
        return tuple(candidate.path for candidate in self.candidates)


def route_index_source_snapshot(
    *,
    code_root: Path,
    storage: StorageSettings,
    scoped_repo_path: str,
) -> RouteIndexSourceSnapshot:
    """Capture one exact candidate set for all route-index membership decisions."""

    root = code_root.resolve()
    _require_repository_root(root)
    tracked, non_file_roots = _tracked_source_candidates(root)
    candidates = tuple(
        sorted(
            [*tracked, *_untracked_source_candidates(root, non_file_roots)],
            key=lambda candidate: candidate.path,
        )
    )
    eligible_paths = tuple(
        candidate.path
        for candidate in candidates
        if resolve_storage_for_source(candidate.path, storage, scoped_repo_path) != "disabled"
    )
    return RouteIndexSourceSnapshot(candidates=candidates, eligible_paths=eligible_paths)


def route_index_source_files(
    *,
    code_root: Path,
    storage: StorageSettings,
    scoped_repo_path: str,
) -> list[str]:
    """Return tracked and nonignored candidate files eligible for onboarding."""

    return list(
        route_index_source_snapshot(
            code_root=code_root,
            storage=storage,
            scoped_repo_path=scoped_repo_path,
        ).eligible_paths
    )


def _tracked_source_candidates(
    code_root: Path,
) -> tuple[list[RouteIndexCandidate], list[str]]:
    deleted = set(
        _nul_records(
            _git_output(
                code_root,
                ["diff-files", "--name-only", "--diff-filter=D", "-z", "--"],
                "git diff-files deletion census failed",
            )
        )
    )
    records = _nul_records(
        _git_output(
            code_root,
            ["ls-files", "--stage", "-z", "--"],
            "git index census failed",
        )
    )
    candidates: list[RouteIndexCandidate] = []
    non_file_roots: list[str] = []
    for record in records:
        header, separator, path = record.partition("\t")
        fields = header.split(" ")
        if not separator or len(fields) != 3 or not path:
            raise RouteIndexCensusError("git index census returned an invalid stage record")
        mode, _object_id, stage_number = fields
        if stage_number != "0":
            raise RouteIndexCensusError(
                f"git index census cannot classify unmerged stage {stage_number} for {path!r}"
            )
        if mode in NON_FILE_INDEX_MODES:
            non_file_roots.append(path)
            continue
        if mode not in TRACKED_FILE_MODES:
            raise RouteIndexCensusError(
                f"git index census returned unsupported mode {mode!r} for {path!r}"
            )
        if path not in deleted:
            candidates.append(RouteIndexCandidate(path=path, mode=mode, origin="index"))
    return candidates, non_file_roots


def _untracked_source_candidates(
    code_root: Path, non_file_roots: list[str]
) -> list[RouteIndexCandidate]:
    paths = _nul_records(
        _git_output(
            code_root,
            ["ls-files", "--others", "--exclude-standard", "-z", "--"],
            "git nonignored-candidate census failed",
        )
    )
    candidates: list[RouteIndexCandidate] = []
    for path in paths:
        if any(path == root or path.startswith(f"{root}/") for root in non_file_roots):
            continue
        try:
            mode = (code_root / path).lstat().st_mode
        except FileNotFoundError as error:
            raise RouteIndexCensusError(
                f"nonignored candidate changed during route-index census: {path!r}"
            ) from error
        except OSError as error:
            raise RouteIndexCensusError(
                f"nonignored candidate could not be classified during route-index census: "
                f"{path!r}: {error}"
            ) from error
        candidate_mode = _untracked_candidate_mode(mode)
        if candidate_mode is not None:
            candidates.append(
                RouteIndexCandidate(path=path, mode=candidate_mode, origin="worktree")
            )
    return candidates


def _require_repository_root(code_root: Path) -> None:
    result = _run_git(
        code_root,
        ["rev-parse", "--show-toplevel"],
        "route-index repository authority probe failed",
        authority=True,
    )
    if result.returncode != 0:
        raise AuthorityError(
            _git_failure(
                result.stderr,
                f"route-index source census requires a Git repository: {code_root}",
            )
        )

    repository_root = Path(result.stdout.strip()).resolve()
    if repository_root != code_root:
        raise AuthorityError(
            "route-index code_root must be the Git repository root: "
            f"received {code_root}, resolved {repository_root}"
        )


def _git_output(code_root: Path, args: list[str], failure: str) -> str:
    result = _run_git(code_root, args, failure, authority=False)
    if result.returncode != 0:
        raise RouteIndexCensusError(_git_failure(result.stderr, failure))
    return result.stdout


def _run_git(
    code_root: Path,
    args: list[str],
    failure: str,
    *,
    authority: bool,
) -> subprocess.CompletedProcess[str]:
    try:
        # Census git is `rev-parse` and `ls-files` only, and it runs while a tool call
        # waits, so it takes the metadata bound rather than the local default.
        return run_git(code_root, args, timeout=GIT_METADATA_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError) as error:
        detail = str(error).strip() or error.__class__.__name__
        message = f"{failure}: {detail}"
        if authority:
            raise AuthorityError(message) from error
        raise RouteIndexCensusError(message) from error


def _untracked_candidate_mode(mode: int) -> str | None:
    if stat.S_ISLNK(mode):
        return "120000"
    if not stat.S_ISREG(mode):
        return None
    executable = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    return "100755" if executable else "100644"


def _nul_records(output: str) -> list[str]:
    records = output.split("\0")
    if records[-1:] == [""]:
        records.pop()
    if any(not record for record in records):
        raise RouteIndexCensusError("git census returned an empty NUL-delimited record")
    return records


def _git_failure(stderr: str, default: str) -> str:
    detail = stderr.strip()
    return f"{default}: {detail}" if detail else default
