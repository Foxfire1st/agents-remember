"""The structural knowledge merge of the managed sync, and the unconverted-line refusal (MIK-R24).

**Every memory merge with a converted side merges structurally (MIK-R24 rule 8 step 3, MIK-R26
rule 2).** :func:`merge_structure` reads the layout marker of the merge base, the own side and the
incoming side. When all three are converted, the three trees merge as they are. When at least one is
unconverted and at least one converted, the merge is a *crossing sync* (rule 8): markers move and
the unconverted trees are converted first. Only a merge of three unconverted trees is plain Git.

Before Git merges anything, :func:`crossing_plan` asks the bound ``KnowledgeCrossingPort`` for the
structural merge of the three trees; a failing step is refused with its name and the line is left
unchanged. After ``git merge --no-commit``, :func:`apply_crossing` makes every ``knowledge/`` and
``onboarding/`` path of the merge the plan's: clean paths are staged, and each conflicted path is
left unmerged with its base, own and incoming versions as index stages 1-3, so the ordinary
resolution route (``continue``) is how the curator finishes it. The merge commit itself is validated
(MIK-R22), against converted bases (rule 7), when it is committed. A crossing sync's own commit
converts the line; there is no separate conversion commit.

**Unconverted lines (rule 9).** :func:`unconverted_line_refusal` is what a write or a memory-quality
run calls on a leaf's memory tree: an unconverted tree whose official line is already converted is
refused, naming the crossing sync, which is only for lines that descend from a converted official
line. Any other unconverted tree is refused by the cutover lock (:mod:`.cutover_lock`, MIK-R09 rule 6)
once its memory repository holds converted memory anywhere -- the cutover window of MIK-R37 rule 6,
in which other masters' unconverted lines are only read. A repository that holds no converted memory
is not locked: its official line converts by running ``agents-remember knowledge-convert`` on that
line and committing it through its normal route (for this master's line, MIK-R37), and until then
this returns ``None``, so no route changes behaviour.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Final, Literal

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.models.knowledge_files.canonical import canonical_text, parse_json
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.cutover_lock import cutover_lock_refusal
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
_ZERO_OBJECT: Final = "0" * 40


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


def _tracked(worktree: Path) -> set[str]:
    output = _git(worktree, ["ls-files", "-z", "--", *KNOWLEDGE_ROOTS])
    return {path for path in output.split("\0") if path}


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


def _write_plan_files(worktree: Path, plan: CrossingPlanView, tracked: set[str]) -> None:
    """Delete the tracked paths the plan drops, and write every path whose bytes it changes.

    A path that already holds the plan's bytes is left alone, so an ordinary sync of converted
    memory rewrites only what the merge changed and every other file keeps its file time.
    """

    kept = {path for path, data in plan.files.items() if data is not None}
    for path in sorted(tracked - kept):
        (worktree / path).unlink(missing_ok=True)
    for path, data in sorted(plan.files.items()):
        target = worktree / path
        if data is None:
            target.unlink(missing_ok=True)
            continue
        if _holds(target, data):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _unmerge(worktree: Path, plan: CrossingPlanView, conflicted: tuple[str, ...]) -> None:
    """Leave each conflicted path unmerged: its converted base, own and incoming as stages 1-3."""

    rows: list[str] = []
    for path in conflicted:
        rows.append(f"0 {_ZERO_OBJECT}\t{path}")
        for stage, data in enumerate(plan.conflict_versions[path], start=1):
            if data is not None:
                blob = _git(
                    worktree, ["hash-object", "-w", "--stdin"], input_text=data.decode("utf-8")
                ).strip()
                rows.append(f"100644 {blob} {stage}\t{path}")
    _git(worktree, ["update-index", "-z", "--index-info"], input_text="\0".join(rows) + "\0")


def apply_crossing(worktree: Path, plan: CrossingPlanView) -> tuple[str, ...]:
    """Make the merge's knowledge and onboarding paths the plan; return the conflicted paths."""

    tracked = _tracked(worktree)
    _git(worktree, ["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", *KNOWLEDGE_ROOTS])
    _write_plan_files(worktree, plan, tracked)
    _git(worktree, ["add", "-A", "--", *KNOWLEDGE_ROOTS])
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
    """Rule 9: refuse ``operation`` on an unconverted tree whose official line is converted.

    Otherwise the cutover lock decides (:func:`~.cutover_lock.cutover_lock_refusal`): an unconverted
    tree in a memory repository that holds converted memory anywhere is refused as well.
    """

    if (memory_worktree / LAYOUT_MARKER_PATH).is_file():
        return None
    if memory_repository is None or not memory_repository.is_dir():
        return None
    if not _official_line_converted(memory_repository, official_branch):
        return cutover_lock_refusal(
            memory_repository, operation=operation, line=os.fspath(memory_worktree)
        )
    return (
        f"{operation} refuses the unconverted memory tree {os.fspath(memory_worktree)}: its "
        f"official line {official_branch} is already converted (knowledge/layout.json), and a "
        "line that descends from a converted official line converts only through the crossing "
        "sync -- run worktree_sync first (MIK-R24 rules 8 and 9)"
    )


def _official_line_converted(repository: Path, official_branch: str) -> bool:
    """Whether the official line's tip holds the marker (no line, or an unreadable one: no)."""

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
    except LayoutProbeError:
        return False


_CROSSING_HISTORY: Final = re.compile(r"^knowledge/history/[^/]+-crossing-[1-9][0-9]*\.json$")


def close_crossing_history(worktree: Path, parents: tuple[str, str]) -> tuple[str, ...]:
    """Close each master-line crossing history file this merge adds (MIK-R24 rule 8 step 4).

    A crossing sync on a master line opens ``<task-id>-crossing-<n>.json`` for the row of a record
    conflict the curator resolved; the file is ``closed`` in the merge commit that performs the
    sync, so it is frozen from then on (MIK-R07 rule 7). A file either parent already holds is left
    alone. Returns the paths closed and staged.
    """

    staged = _git(worktree, ["diff", "--cached", "--name-only", "--diff-filter=A", *parents[:1]])
    closed: list[str] = []
    for path in sorted(line for line in staged.splitlines() if _CROSSING_HISTORY.match(line)):
        if run_git(worktree, ["cat-file", "-e", f"{parents[1]}:{path}"]).returncode == 0:
            continue
        target = worktree / path
        document = parse_json(target.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("closed") is False:
            document["closed"] = True
            target.write_text(canonical_text(document), encoding="utf-8")
            _git(worktree, ["add", "--", path])
            closed.append(path)
    return tuple(closed)


CROSSING_REPORT_PREFIX: Final = "crossing-sync-report"
HOW_TO_RESOLVE: Final = (
    "Each conflicted JSON item stands in the worktree as a 'crossing-conflict' marker holding the "
    "base, own and incoming values; the knowledge validator refuses the file until the marker is "
    "replaced by the resolved item. Markdown conflicts carry Git conflict markers. Resolve a card's "
    "Markdown and its sidecar together: resolving only one side leaves [n] markers and references "
    "that disagree, which R22.3-markers refuses for the card. Resolve every listed item, stage, "
    "then continue."
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
        "markerRows": document.get("markerRows", 0),
        "recordConflictHistoryOwner": document.get("recordConflictHistoryOwner"),
        "howToResolve": HOW_TO_RESOLVE,
    }
