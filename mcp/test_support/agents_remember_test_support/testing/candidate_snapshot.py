"""Exact Git working-candidate identity for non-certifying evidence routes."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agents_remember.kernel import git_command


class CandidateSnapshotError(RuntimeError):
    """The complete Git candidate cannot be enumerated or fingerprinted."""


@dataclass(frozen=True)
class CandidateSnapshot:
    """One HEAD-tree plus exact changed/untracked working candidate."""

    digest: str
    head: str
    head_tree: str
    candidate_tree: str
    dirty: bool
    paths: tuple[Path, ...]

    def payload(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "head": self.head,
            "headTree": self.head_tree,
            "candidateTree": self.candidate_tree,
            "dirty": self.dirty,
            "pathCount": len(self.paths),
            "paths": [path.as_posix() for path in self.paths],
        }


def candidate_snapshot(project_root: Path) -> CandidateSnapshot:
    """Hash the immutable HEAD tree plus every changed/untracked working path."""

    root = project_root.resolve()
    head = _required_revision(root, "HEAD^{commit}", "candidate Git HEAD is unavailable")
    head_tree = _required_revision(root, "HEAD^{tree}", "candidate Git HEAD tree is unavailable")
    candidate_tree = _required_git_output(
        root,
        ["write-tree"],
        "candidate Git index tree is unavailable",
    )
    paths = _candidate_paths(root)
    digest = _candidate_digest(root, head, head_tree, candidate_tree, paths)
    return CandidateSnapshot(
        digest=digest,
        head=head,
        head_tree=head_tree,
        candidate_tree=candidate_tree,
        dirty=bool(paths),
        paths=paths,
    )


def _required_revision(root: Path, revision: str, unavailable: str) -> str:
    return _required_git_output(root, ["rev-parse", "--verify", revision], unavailable)


def _required_git_output(root: Path, args: list[str], unavailable: str) -> str:
    result = git_command.run_git(root, args)
    if result.returncode != 0:
        raise CandidateSnapshotError(result.stderr.strip() or unavailable)
    return result.stdout.strip()


def _candidate_paths(root: Path) -> tuple[Path, ...]:
    changed = git_command.run_git(
        root,
        [
            "-c",
            "core.quotePath=false",
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "HEAD",
            "--",
        ],
    )
    if changed.returncode != 0:
        raise CandidateSnapshotError(
            changed.stderr.strip() or "candidate changed-path inventory is unavailable"
        )
    untracked = git_command.run_git(
        root,
        [
            "-c",
            "core.quotePath=false",
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ],
    )
    if untracked.returncode != 0:
        raise CandidateSnapshotError(
            untracked.stderr.strip() or "candidate untracked-path inventory is unavailable"
        )
    return tuple(
        sorted(
            {
                Path(value)
                for payload in (changed.stdout, untracked.stdout)
                for value in payload.split("\0")
                if value
            },
            key=Path.as_posix,
        )
    )


def _candidate_digest(
    root: Path,
    head: str,
    head_tree: str,
    candidate_tree: str,
    paths: tuple[Path, ...],
) -> str:
    digest = hashlib.sha256()
    digest.update(b"head\0")
    digest.update(head.encode("ascii"))
    digest.update(b"\0head-tree\0")
    digest.update(head_tree.encode("ascii"))
    digest.update(b"\0candidate-tree\0")
    digest.update(candidate_tree.encode("ascii"))
    for relative in paths:
        _update_path_digest(digest.update, root, relative)
    return digest.hexdigest()


def _update_path_digest(update: Callable[[bytes], object], root: Path, relative: Path) -> None:
    pure = PurePosixPath(relative.as_posix())
    if pure.is_absolute() or ".." in pure.parts:
        raise CandidateSnapshotError(f"candidate path is not confined: {relative}")
    path = root / relative
    update(b"\0path\0")
    update(relative.as_posix().encode("utf-8"))
    try:
        if path.is_symlink():
            update(b"\0symlink\0")
            update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            update(b"\0file\0")
            update(b"executable\0" if path.stat().st_mode & 0o111 else b"regular\0")
            update(path.read_bytes())
        elif not path.exists():
            update(b"\0deleted\0")
        else:
            raise CandidateSnapshotError(
                f"candidate path is neither file, symlink, nor deletion: {relative}"
            )
    except OSError as error:
        raise CandidateSnapshotError(
            f"cannot fingerprint candidate path {relative}: {error}"
        ) from error
