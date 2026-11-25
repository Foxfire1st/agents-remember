"""The structural knowledge merge of the managed sync, and the unconverted-line refusal (MIK-R24).

**Every memory merge with a converted side merges structurally (MIK-R24 rule 8 step 3, MIK-R26
rule 2).** :func:`merge_structure` reads the layout marker of the merge base, the own side and the
incoming side. When all three are converted, the three trees merge as they are. When at least one is
unconverted and at least one converted, the merge is a *crossing sync* (rule 8): markers move and
the unconverted trees are converted first. A merge of three unconverted trees stays plain Git
inside this structural helper only; the managed sync never runs it there -- it refuses the
all-unconverted sides with the one ``legacy-format`` message before Git moves (MIK-R46 rule 1,
the plain-Git merge of memory is gone). The helper itself stays until MIK-R65 removes the
retired machinery; this prose does not promise the managed path it no longer takes.

Before Git merges anything, :func:`crossing_plan` asks the bound ``KnowledgeCrossingPort`` for the
structural merge of the three trees; a failing step is refused with its name and the line is left
unchanged. After ``git merge --no-commit``, :func:`apply_crossing` overlays the modeled paths.
Clean paths are staged, and each modeled conflict holds its base, own and incoming versions as
index stages 1-3. Unmodeled paths keep the native merge result. The curator finishes conflicts
through the ordinary resolution route (``continue``). The merge commit itself is validated
(MIK-R22), against converted bases (rule 7), when it is committed. A crossing sync's own commit
converts the line; there is no separate conversion commit.

**Unconverted lines (MIK-R46 rule 1).** :func:`unconverted_line_refusal` is what a write or a
memory-quality run calls on a leaf's memory tree: an unconverted tree is refused, naming the
crossing sync when its official line is already converted (only lines that descend from a
converted official line convert that way), and the one rule-2 message otherwise. The probe
:func:`~.cutover_lock.converted_memory_location` only words the message; it decides nothing.
A converting candidate is never refused: it holds the marker on its own side, so the gate
applies to it instead, against the converted base (MIK-R24 rule 7).
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
    run_git_bytes,
)
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH, history_path
from agents_remember.models.knowledge_files.history import empty_history
from agents_remember.models.knowledge_files.shapes import GitBlobId, RepositoryPath
from agents_remember.worktrees.cutover_lock import cutover_lock_refusal, legacy_format_refusal
from agents_remember.worktrees.knowledge_validation import (
    LayoutProbeError,
    PairedCode,
    has_layout_marker,
)
from agents_remember.worktrees.services import (
    CrossingPlanView,
    CrossingRequest,
    CrossingStepFailed,
    worktree_services,
)

KNOWLEDGE_ROOTS: Final = ("knowledge", "onboarding")


class CrossingSyncError(RuntimeError):
    """The crossing sync cannot run or apply; the message names the step."""


def _git(worktree: Path, args: list[str], *, input_text: str | None = None) -> str:
    result = run_git(worktree, args, GitRunnerOptions(input_text=input_text))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
        raise CrossingSyncError(f"git {args[0]} failed during the crossing sync: {detail}")
    return result.stdout


def merge_base(worktree: Path, own: str, incoming: str) -> str:
    return _git(worktree, ["merge-base", own, incoming]).strip()


MergeStructure = Literal["plain", "converted", "crossing"]


def merge_structure(repository: Path, base: str, own: str, incoming: str) -> MergeStructure:
    """How the three trees of a memory merge are merged.

    ``converted``: all three hold the layout marker and merge structurally as they are (MIK-R26
    rule 2). ``crossing``: one is unconverted and one converted (MIK-R24 rule 8). ``plain``: none
    is converted, so Git merges them as before.
    """

    try:
        states = {has_layout_marker(repository, tree) for tree in (base, own, incoming)}
    except LayoutProbeError as error:
        raise CrossingSyncError(f"crossing sync step 'markers' failed: {error}") from error
    if states == {True}:
        return "converted"
    return "crossing" if True in states else "plain"


def crossing_plan(
    worktree: Path,
    sides: tuple[str, str, str],
    *,
    paired_code: PairedCode | None,
    owner: tuple[Literal["leaf", "master"], str],
) -> CrossingPlanView:
    """Run steps 1-4 on (base, own, incoming) through the bound port; ``worktree`` is untouched.

    A merge of three converted trees converts nothing, so it needs no paired code commit; a
    crossing does, for the fallback cards of the trees it converts.
    """

    converting = merge_structure(worktree, *sides) == "crossing"
    if converting and (paired_code is None or not paired_code.commit):
        raise CrossingSyncError(
            "crossing sync step 'convert' failed: the paired code commit is unknown, so the "
            "fallback cards have no code tree"
        )
    port = worktree_services().knowledge_crossing
    if port is None:
        raise CrossingSyncError(
            "crossing sync step 'convert' failed: the knowledge crossing is not bound in this "
            "process, and converted memory is never merged as plain Git"
        )
    try:
        return port.plan(
            CrossingRequest(
                memory_repository=worktree,
                sides=sides,
                code_repository=worktree if paired_code is None else paired_code.repository,
                code_commit="" if paired_code is None else paired_code.commit,
                owner_kind=owner[0],
                owner_id=owner[1],
            )
        )
    except CrossingStepFailed as error:
        raise CrossingSyncError(str(error)) from error


def _holds(target: Path, data: bytes) -> bool:
    """Whether ``target`` is a regular file that already holds exactly ``data``."""

    try:
        return (
            not target.is_symlink()
            and target.stat().st_size == len(data)
            and target.read_bytes() == data
        )
    except OSError:
        return False


def _write_plan_files(worktree: Path, plan: CrossingPlanView) -> None:
    """Apply explicit writes and deletions while leaving other paths alone.

    A path that already holds the plan's bytes is left alone, so an ordinary sync of converted
    memory rewrites only what the merge changed and every other file keeps its file time.
    """

    for path, data in sorted(plan.files.items()):
        target = worktree / path
        if data is None:
            target.unlink(missing_ok=True)
            continue
        if _holds(target, data):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _stage_plan_files(worktree: Path, plan: CrossingPlanView) -> None:
    """Update only the modeled entries without restaging any of Git's other results."""

    removed = "".join(f"{path}\0" for path, data in plan.files.items() if data is None)
    if removed:
        _git(worktree, ["update-index", "--force-remove", "-z", "--stdin"], input_text=removed)
    kept = "".join(f"{path}\0" for path, data in plan.files.items() if data is not None)
    if kept:
        _git(
            worktree,
            ["--literal-pathspecs", "add", "--pathspec-from-file=-", "--pathspec-file-nul"],
            input_text=kept,
        )


def _staged_files(worktree: Path) -> dict[str, tuple[str, str]]:
    """Return the stage-zero mode and object ID of each staged file under the knowledge roots."""

    result = run_git_bytes(worktree, ["ls-files", "--stage", "-z", "--", *KNOWLEDGE_ROOTS])
    if result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise CrossingSyncError(f"git ls-files failed during the crossing sync: {detail}")
    entries = {}
    for row in result.stdout.split(b"\0"):
        if not row:
            continue
        fields, path = row.split(b"\t", 1)
        mode, object_id, stage = fields.decode("ascii").split()
        if stage == "0":
            entries[os.fsdecode(path)] = (mode, object_id)
    return entries


def _unmerge(worktree: Path, plan: CrossingPlanView, conflicted: tuple[str, ...]) -> None:
    """Leave each conflicted path unmerged: its converted base, own and incoming as stages 1-3."""

    rows: list[str] = []
    entries = _staged_files(worktree)
    for path in conflicted:
        mode, object_id = entries[path]
        rows.append(f"0 {'0' * len(object_id)}\t{path}")
        for stage, data in enumerate(plan.conflict_versions[path], start=1):
            if data is not None:
                blob = _git(
                    worktree,
                    ["hash-object", "-w", "--stdin"],
                    input_text=data.decode("utf-8", errors="surrogateescape"),
                ).strip()
                rows.append(f"{mode} {blob} {stage}\t{path}")
    _git(worktree, ["update-index", "-z", "--index-info"], input_text="\0".join(rows) + "\0")


def apply_crossing(worktree: Path, plan: CrossingPlanView) -> tuple[str, ...]:
    """Overlay the structural plan and return the conflicts it introduced."""

    _write_plan_files(worktree, plan)
    _stage_plan_files(worktree, plan)
    conflicted = tuple(sorted(plan.conflict_versions))
    if conflicted:
        _unmerge(worktree, plan, conflicted)
    return conflicted


def unconverted_line_refusal(
    *,
    memory_worktree: Path,
    memory_repository: Path | None,
    official_branch: str,
    operation: str,
) -> str | None:
    """Rule 1: refuse ``operation`` on an unconverted tree (MIK-R46).

    A tree whose official line is converted converts only through the crossing sync, so the
    refusal names it. Otherwise the cutover lock refuses with the one rule-2 message. ``None``
    only when the tree already holds the layout marker (a converting candidate or converted
    leaf) or there is no memory repository to judge.
    """

    if (memory_worktree / LAYOUT_MARKER_PATH).is_file():
        return None
    if memory_repository is None or not memory_repository.is_dir():
        return None
    official_state = _official_line_converted(memory_repository, official_branch)
    if isinstance(official_state, str):
        return official_state  # the official line cannot be probed; name that error
    if not official_state:
        return cutover_lock_refusal(
            memory_repository, operation=operation, line=os.fspath(memory_worktree)
        )
    return legacy_format_refusal(
        memory_repository,
        operation=operation,
        subject=(
            f"the unconverted memory tree {os.fspath(memory_worktree)}: its official line "
            f"{official_branch} is already converted (knowledge/layout.json), so the line "
            "converts only through the crossing sync -- run worktree_sync with merge-memory "
            "first (MIK-R24 rules 8 and 9)"
        ),
    )


def _official_line_converted(repository: Path, official_branch: str) -> bool | str:
    """Whether the official line's tip holds the marker.

    No official line is unconverted (``False``). A tip Git cannot answer is returned as
    its named error text, never read as unconverted: conversion does not repair an
    unreadable object (MIK-R46).
    """

    if not official_branch:
        return False
    tip = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{official_branch}^{{commit}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    commit = tip.stdout.strip()
    if tip.returncode != 0 or not commit:
        return False
    try:
        return has_layout_marker(repository, commit)
    except LayoutProbeError as error:
        return str(error)


_CROSSING_HISTORY_PATTERN: Final = r"^knowledge/history/[^/]+-crossing-[1-9][0-9]*\.json$"
_CROSSING_HISTORY: Final = re.compile(_CROSSING_HISTORY_PATTERN)


def open_crossing_history(worktree: Path, plan: CrossingPlanView) -> str:
    """Stage the master history named by a structural merge that needs resolution."""

    owner = str(plan.report["recordConflictHistoryOwner"])
    path = history_path(owner)
    if path not in plan.files:
        target = worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(canonical_text(empty_history("crossing", owner).to_document()), "utf-8")
        _git(worktree, ["add", "--", path])
    return owner


class _OpenCrossingHistory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: RepositoryPath = Field(pattern=_CROSSING_HISTORY_PATTERN)
    original: str
    mode: Literal["100644", "100755"]
    blob: GitBlobId
    closed: str
    closed_blob: GitBlobId


class _CrossingHistoryClose(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parents: tuple[GitBlobId, GitBlobId]
    entries: tuple[_OpenCrossingHistory, ...]


def _stage_zero_entry(worktree: Path, path: str) -> tuple[Literal["100644", "100755"], str] | None:
    index = _git(worktree, ["--literal-pathspecs", "ls-files", "--stage", "-z", "--", path])
    rows = [row for row in index.split("\0") if row]
    if len(rows) != 1:
        return None
    fields = rows[0].partition("\t")[0].split()
    if len(fields) != 3:
        return None
    mode, blob, stage = fields
    if stage != "0" or mode not in ("100644", "100755"):
        return None
    return mode, blob


def _regular_disk_state(target: Path) -> tuple[Literal["100644", "100755"], bytes] | None:
    try:
        info = target.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    mode: Literal["100644", "100755"] = "100755" if info.st_mode & 0o111 else "100644"
    return mode, target.read_bytes()


def _open_history_entries(
    worktree: Path, parents: tuple[str, str]
) -> Iterator[_OpenCrossingHistory]:
    staged = _git(
        worktree, ["diff", "--cached", "--name-only", "-z", "--diff-filter=A", parents[0]]
    )
    for path in sorted(line for line in staged.split("\0") if _CROSSING_HISTORY.fullmatch(line)):
        if run_git(worktree, ["cat-file", "-e", f"{parents[1]}:{path}"]).returncode == 0:
            continue
        staged_entry = _stage_zero_entry(worktree, path)
        if staged_entry is None:
            continue
        mode, blob = staged_entry
        original = (worktree / path).read_bytes().decode("utf-8")
        document = parse_json(original)
        if isinstance(document, dict) and document.get("closed") is False:
            document["closed"] = True
            closed = canonical_text(document)
            closed_blob = _git(
                worktree, ["hash-object", "--stdin", f"--path={path}"], input_text=closed
            ).strip()
            yield _OpenCrossingHistory(
                path=path,
                original=original,
                mode=mode,
                blob=blob,
                closed=closed,
                closed_blob=closed_blob,
            )


def _history_close_checkpoint(worktree: Path) -> Path:
    git_dir = _git(worktree, ["rev-parse", "--absolute-git-dir"]).strip("\n")
    return Path(git_dir) / "agents-remember-crossing-history-close.json"


def _read_history_close_checkpoint(checkpoint: Path) -> _CrossingHistoryClose | None:
    try:
        saved = checkpoint.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return _CrossingHistoryClose.model_validate_json(saved)
    except ValidationError as error:
        raise CrossingSyncError(
            f"cannot read the pending crossing history close: {error}"
        ) from error


def clear_crossing_history_close(worktree: Path, parents: tuple[str, str]) -> None:
    """Retire a matching close checkpoint after a verified commit or rollback."""

    checkpoint = _history_close_checkpoint(worktree)
    pending = _read_history_close_checkpoint(checkpoint)
    if pending is not None and pending.parents == parents:
        checkpoint.unlink(missing_ok=True)


def restore_crossing_history(worktree: Path, parents: tuple[str, str]) -> None:
    """Reopen only checkpoint-owned entries for this still-active merge."""

    checkpoint = _history_close_checkpoint(worktree)
    pending = _read_history_close_checkpoint(checkpoint)
    if pending is None or pending.parents != parents:
        return
    active = run_git(worktree, ["rev-parse", "HEAD", "MERGE_HEAD"])
    if active.returncode != 0 or tuple(active.stdout.splitlines()) != parents:
        return
    index_rows: list[str] = []
    for entry in pending.entries:
        original_bytes = entry.original.encode("utf-8")
        closed_bytes = entry.closed.encode("utf-8")
        disk = _regular_disk_state(worktree / entry.path)
        staged = _stage_zero_entry(worktree, entry.path)
        disk_owned = (
            disk is not None and disk[0] == entry.mode and disk[1] in (original_bytes, closed_bytes)
        )
        index_owned = staged in ((entry.mode, entry.blob), (entry.mode, entry.closed_blob))
        if not (disk_owned and index_owned):
            continue
        if disk is not None and disk[1] == closed_bytes:
            (worktree / entry.path).write_bytes(original_bytes)
        if staged == (entry.mode, entry.closed_blob):
            index_rows.append(f"{entry.mode} {entry.blob} 0\t{entry.path}\0")
    if index_rows:
        _git(worktree, ["update-index", "-z", "--index-info"], input_text="".join(index_rows))
    checkpoint.unlink(missing_ok=True)


@contextmanager
def close_crossing_history(worktree: Path, parents: tuple[str, str]) -> Iterator[tuple[str, ...]]:
    """Stage closed history for a merge commit, restoring open entries on refusal."""

    captured = tuple(_open_history_entries(worktree, parents))
    if not captured:
        yield ()
        return
    checkpoint = _history_close_checkpoint(worktree)
    pending = _CrossingHistoryClose(parents=parents, entries=captured)
    # The Git admin directory holds one image for the in-progress close. Publish it
    # before changing either the file or the index so a dying process can be replayed.
    atomic_write_text(checkpoint, pending.model_dump_json())
    try:
        for entry in captured:
            (worktree / entry.path).write_text(entry.closed, encoding="utf-8")
            _git(worktree, ["--literal-pathspecs", "add", "--", entry.path])
        yield tuple(entry.path for entry in pending.entries)
    except BaseException:
        restore_crossing_history(worktree, parents)
        raise
    else:
        checkpoint.unlink(missing_ok=True)


CROSSING_REPORT_PREFIX: Final = "crossing-sync-report"
HOW_TO_RESOLVE: Final = (
    "Each conflicted JSON item stands in the worktree as a 'crossing-conflict' marker holding the "
    "base, own and incoming values; the knowledge validator refuses the file until the marker is "
    "replaced by the resolved item. Markdown conflicts carry Git conflict markers. Resolve a card's "
    "Markdown and its sidecar together: resolving only one side leaves [n] markers and references "
    "that disagree, which R22.3-markers refuses for the card. Reference numbers are aligned by the "
    "merge itself -- the syncing line's references take the incoming numbers where they match, and "
    "fresh numbers otherwise -- so never renumber a reference by hand to clear a conflict. "
    "Resolve every listed item, stage, then continue."
)
_PAYLOAD_CONFLICT_LIMIT: Final = 100


def write_crossing_report(worktree: Path, own: str, incoming: str, plan: CrossingPlanView) -> Path:
    """Write the crossing's report into the worktree group's ``reports/`` and return its path.

    The directory holds the sync journal too, and is outside every repository.

    It holds every item-level conflict -- file, item, reason and the base, own and incoming values
    -- and the cards taken from each side. A conflicted JSON item stands in the worktree as an
    explicit ``crossing-conflict`` marker the validator refuses, so the report is the curator's
    worklist and ``continue`` cannot commit an unresolved item.
    """

    reports = worktree.parent / "reports"  # the worktree group's reports, beside the sync journal
    target = reports / f"{CROSSING_REPORT_PREFIX}-{own[:12]}-{incoming[:12]}.json"
    document = {
        "worktree": worktree.as_posix(),
        "own": own,
        "incoming": incoming,
        "howToResolve": HOW_TO_RESOLVE,
        **plan.report,
    }
    try:
        reports.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise CrossingSyncError(f"the crossing report cannot be written: {error}") from error
    return target


def crossing_summary(report_path: str) -> dict[str, object]:
    """The resolution payload's view of a crossing report: its path, counts and conflict items."""

    try:
        document = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"reportPath": report_path, "unreadable": str(error)}
    conflicts = document.get("conflicts", [])
    return {
        "reportPath": report_path,
        "conflictItems": len(conflicts),
        "conflicts": [
            {"path": one["path"], "item": one["item"], "reason": one["reason"]}
            for one in conflicts[:_PAYLOAD_CONFLICT_LIMIT]
        ],
        "conflictsTruncated": len(conflicts) > _PAYLOAD_CONFLICT_LIMIT,
        "cards": document.get("cards", {}),
        "converted": document.get("converted", []),
        "alignment": document.get(
            "alignment",
            {
                "alignedCards": 0,
                "renumberedReferences": 0,
                "evidenceLinesKept": 0,
                "skippedCards": [],
            },
        ),
        "markerRows": document.get("markerRows", 0),
        "recordConflictHistoryOwner": document.get("recordConflictHistoryOwner"),
        "howToResolve": HOW_TO_RESOLVE,
    }
