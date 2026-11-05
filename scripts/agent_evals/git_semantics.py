"""Read semantic Git state from retained snapshots without changing their bytes.

An index stat-cache refresh, unreachable object, or reflog write is not a commit
or staging change. Callers still check non-Git effects and unadmitted config or
hook changes. Native action evidence owns operations that leave no endpoint change.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .artifacts import manifest_files


@dataclass(frozen=True)
class GitState:
    head: str | None
    branch: str | None
    refs: tuple[tuple[str, str, str], ...]
    staged_paths: tuple[str, ...]
    index_entries: tuple[str, ...]


@dataclass(frozen=True)
class GitCommand:
    arguments: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class GitSemanticResult:
    status: Literal["pass", "fail", "unknown"]
    changes: tuple[str, ...] = ()
    before: GitState | None = None
    after: GitState | None = None
    benign_metadata_paths: tuple[str, ...] = ()
    commands: tuple[GitCommand, ...] = ()
    source_files: tuple[tuple[str, str, str], ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


class _InvalidSnapshot(ValueError):
    pass


def _metadata(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise _InvalidSnapshot(f"retained repository is absent or a symlink: {root}")
    dotgit = root / ".git"
    if dotgit.is_symlink() or (dotgit.exists() and not dotgit.is_dir()):
        raise _InvalidSnapshot(f"external or linked Git metadata is not retained: {dotgit}")
    metadata = dotgit if dotgit.is_dir() else root
    for name in ("HEAD", "config", "objects"):
        if not (metadata / name).exists():
            raise _InvalidSnapshot(f"retained Git metadata is incomplete: {metadata / name}")
    if not (metadata / "refs").is_dir() and not (metadata / "packed-refs").is_file():
        raise _InvalidSnapshot(f"retained Git refs are absent: {metadata}")
    _validate_storage(metadata)
    return metadata


def _validate_storage(metadata: Path) -> None:
    for path in (metadata, *metadata.rglob("*")):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise _InvalidSnapshot(f"retained Git metadata has a link or special file: {path}")
        if path.name.endswith(".lock"):
            raise _InvalidSnapshot(f"retained Git metadata has an incomplete transaction: {path}")
    for name in ("commondir", "info/grafts"):
        if (metadata / name).exists():
            raise _InvalidSnapshot(f"unsupported retained Git topology: {metadata / name}")
    alternates = metadata / "objects/info/alternates"
    if alternates.is_file() and alternates.read_text().strip():
        raise _InvalidSnapshot(f"retained Git objects depend on external alternates: {alternates}")


def _safe_config(source: Path) -> str:
    """Native commands must not load actor-supplied includes, helpers or hooks."""
    config = configparser.RawConfigParser(strict=False, allow_no_value=True)
    try:
        config.read_string(source.read_text())
        version = config.getint("core", "repositoryformatversion")
        bare = config.getboolean("core", "bare")
    except (configparser.Error, ValueError, TypeError) as error:
        raise _InvalidSnapshot(f"invalid retained Git format config: {source}: {error}") from error
    if version not in (0, 1):
        raise _InvalidSnapshot(f"unsupported retained Git format version: {version}")
    extensions = dict(config.items("extensions")) if config.has_section("extensions") else {}
    if set(extensions) - {"objectformat"}:
        raise _InvalidSnapshot(f"unsupported retained Git extensions: {sorted(extensions)}")
    object_format = extensions.get("objectformat", "sha1")
    if object_format not in ("sha1", "sha256"):
        raise _InvalidSnapshot(f"unsupported retained Git object format: {object_format}")
    result = f"[core]\nrepositoryformatversion = {version}\nbare = {str(bare).lower()}\n"
    if extensions:
        result += f"[extensions]\nobjectformat = {object_format}\n"
    return result


def _run(
    metadata: Path, scratch: Path, args: tuple[str, ...], receipts: list[GitCommand]
) -> subprocess.CompletedProcess[bytes]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        GIT_NO_REPLACE_OBJECTS="1",
        XDG_CONFIG_HOME=str(scratch),
        TMPDIR=str(scratch),
        LC_ALL="C",
    )
    command = (
        "git",
        "--no-optional-locks",
        "--git-dir",
        str(metadata),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=" + os.devnull,
        *args,
    )
    process = subprocess.run(
        command,
        cwd=scratch,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        check=False,
    )
    receipts.append(
        GitCommand(
            args, process.returncode, os.fsdecode(process.stdout), os.fsdecode(process.stderr)
        )
    )
    return process


def _state(metadata: Path, scratch: Path, receipts: list[GitCommand]) -> GitState:
    def checked(*args: str) -> bytes:
        result = _run(metadata, scratch, args, receipts)
        if result.returncode:
            raise _InvalidSnapshot(
                f"Git {' '.join(args)} failed ({result.returncode}): {os.fsdecode(result.stderr).strip()}"
            )
        return result.stdout

    branch_result = _run(metadata, scratch, ("symbolic-ref", "-q", "HEAD"), receipts)
    if branch_result.returncode not in (0, 1):
        raise _InvalidSnapshot("retained HEAD symbolic reference is invalid")
    branch = os.fsdecode(branch_result.stdout).strip() if branch_result.returncode == 0 else None
    reference_rows = checked("for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)")
    refs = []
    for line in reference_rows.splitlines():
        row = tuple(os.fsdecode(value) for value in line.split(b"\0"))
        if len(row) != 3:
            raise _InvalidSnapshot("native Git ref record has an invalid shape")
        refs.append((row[0], row[1], row[2]))
    head_result = _run(metadata, scratch, ("rev-parse", "--verify", "HEAD"), receipts)
    if head_result.returncode:
        if branch is None or branch in {ref[0] for ref in refs}:
            raise _InvalidSnapshot("retained HEAD is unresolved without a valid unborn branch")
        head = None
    else:
        head = os.fsdecode(head_result.stdout).strip()
        checked("rev-parse", "--verify", "HEAD^{commit}")
    bare = checked("rev-parse", "--is-bare-repository").strip() == b"true"
    if head is not None and not bare and not (metadata / "index").is_file():
        raise _InvalidSnapshot("committed non-bare snapshot is missing its index")
    staged = checked("diff", "--cached", "--name-only", "-z", "--no-renames")
    entries = checked("ls-files", "--stage", "-z")
    if bare and not (metadata / "index").exists():
        staged = b""  # A bare repository without an index has no staging area.
    checked("fsck", "--full", "--no-reflogs", "--no-dangling")
    return GitState(
        head,
        branch,
        tuple(sorted(refs)),
        tuple(sorted(os.fsdecode(value) for value in staged.split(b"\0") if value)),
        tuple(sorted(os.fsdecode(value) for value in entries.split(b"\0") if value)),
    )


def compare_git_semantics(before: Path, after: Path, *, scratch_root: Path) -> GitSemanticResult:
    """Compare two repository or bare metadata snapshots; Git writes only to scratch.

    ``benign_metadata_paths`` are relative to the metadata directory, not the
    repository root. They clear only index/object/reflog/ref storage byte churn, and only on
    a semantic pass. Admission of any real Git change belongs to the caller.
    """
    receipts: list[GitCommand] = []
    sources: list[tuple[str, str, str]] = []
    inventories = []
    states: list[GitState] = []
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="git-semantics-", dir=scratch_root) as temporary:
            temporary_root = Path(temporary)
            for phase, root in (("before", before), ("after", after)):
                original = _metadata(root)
                files = manifest_files(original)
                inventory = {item.path: item.sha256 for item in files}
                inventories.append(inventory)
                sources.extend((phase, item.path, item.sha256) for item in files)
                copied = temporary_root / phase / ".git"
                shutil.copytree(original, copied)
                (copied / "config").write_text(_safe_config(original / "config"))
                states.append(_state(copied, temporary_root, receipts))
                if {item.path: item.sha256 for item in manifest_files(original)} != inventory:
                    raise _InvalidSnapshot(
                        f"source Git snapshot changed during inspection: {original}"
                    )
        a, b = states
        changes = []
        for field, label in (
            ("head", "HEAD commit"),
            ("branch", "HEAD branch"),
            ("refs", "refs including branches/tags/stash"),
            ("staged_paths", "staged paths"),
            ("index_entries", "staged entries"),
        ):
            if getattr(a, field) != getattr(b, field):
                changes.append(label)
        if changes:
            return GitSemanticResult(
                "fail", tuple(changes), a, b, commands=tuple(receipts), source_files=tuple(sources)
            )
        old, new = inventories
        changed = {path for path in old.keys() | new.keys() if old.get(path) != new.get(path)}
        benign = tuple(
            sorted(
                path
                for path in changed
                if path in {"index", "HEAD", "packed-refs"}
                or path.startswith(("objects/", "logs/", "refs/"))
            )
        )
        return GitSemanticResult(
            "pass",
            before=a,
            after=b,
            benign_metadata_paths=benign,
            commands=tuple(receipts),
            source_files=tuple(sources),
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return GitSemanticResult(
            "unknown",
            (f"{type(error).__name__}: {error}",),
            before=states[0] if states else None,
            commands=tuple(receipts),
            source_files=tuple(sources),
        )
