#!/usr/bin/env python3
"""Adopt existing external-memory onboarding as an attributed Git baseline.

Requires Python 3.14 and git. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import CoordinationHints
from agents_remember.kernel.git_command import copy_git_index, run_git
from agents_remember.kernel.memory_attribution import (
    MemoryAttributionError,
    attributed_commits,
    render_memory_content_message,
)
from agents_remember.kernel.memory_cache import (
    derive_memory_ledger,
    prepare_memory_cache,
    refresh_memory_cache,
)
from agents_remember.kernel.memory_init import DEFAULT_BRANCH_CONFIG_KEY
from agents_remember.kernel.memory_ledger import LEDGER_RELATIVE_PATH
from agents_remember.kernel.memory_mode import Topology, require_supported_topology
from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    ACTIONABLE_CLASSIFICATIONS,
    CLASSIFICATIONS,
    DriftSummaryPacket,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    CONVERTED_BASIS,
    DriftSummaryOutput,
    run_drift_summary,
)
from agents_remember.memory_quality.legacy_cards import find_legacy_cards
from agents_remember.memory_quality.reference_state import is_converted_memory
from agents_remember.models.memory_content_excludes import MEMORY_CONTENT_EXCLUDES
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.integration.integration_branch_authority import (
    memory_repository_default_branch,
)
from agents_remember.worktrees.knowledge_validation import (
    PairedCode,
    memory_commit_refusal,
)
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.modules.git import (
    _IndexCopyUnusable,
    _real_index,
    _require_git_with_index,
    branch_commit,
    commit_if_dirty,
    ensure_git_identity,
    has_changes,
    local_branch_ref,
    require_git,
    stage_worktree_content,
)
from agents_remember.worktrees.services import WorktreeServicesUnboundError


@dataclass(frozen=True)
class BaselineRequest:
    code_repository_name: str
    workspace_root: Path
    code_repository_root: Path | None = None
    coordination_root: Path | None = None
    topology: Topology | None = "external"
    report: Path | None = None


def _normalize_topology(value: str | None) -> Topology | None:
    """Narrow ``--topology`` onto the supported set, or refuse.

    Through the shared vocabulary helper, for two reasons that used to be one: letting an
    unrecognized value fall through to ``None`` would turn an explicit request into ordinary
    detection, and answering *every* unrecognized value with "which was removed" tells a
    developer who mistyped the flag that a mode was removed when none was named.
    """
    if value is None:
        return None
    return require_supported_topology(value)


def request_from_args(args: argparse.Namespace) -> BaselineRequest:
    return BaselineRequest(
        code_repository_name=args.code_repository_name,
        workspace_root=args.workspace_root,
        code_repository_root=args.code_repository_root,
        coordination_root=args.coordination_root,
        topology=_normalize_topology(args.topology),
        report=args.report,
    )


def resolve_request_context(request: BaselineRequest):
    return resolver.resolve_coordination_context(
        code_repository_name=request.code_repository_name,
        workspace_root=request.workspace_root,
        code_repository_root=request.code_repository_root,
        request=CoordinationRequest(
            hints=CoordinationHints(
                topology=request.topology, coordination_root=request.coordination_root
            ),
            contract_reader=WorktreeContractReader(),
        ),
    )


def resolve_baseline_context(args: argparse.Namespace):
    return resolve_request_context(request_from_args(args))


def run_drift(
    context, report_path: Path | None, *, write_report: bool = True
) -> tuple[DriftSummaryPacket, Path | None]:
    """Measure drift with the one shared summary (MIK-R44 rule 1).

    With ``write_report=False`` the call is a read: nothing is filed and no
    report path is returned (rule 6). Callers that really ask for a report
    pass an explicit path (or leave the default) and keep full behavior.

    The legacy classification is not run here for any tree: `run_drift_summary`
    measures a converted tree from `converted_knowledge_check` and keeps today's
    behavior for an unconverted one. The memory layer imports nothing from the
    application layer; the optional converted comparison base stays with the
    application callers that own a contract.
    """

    summary = run_drift_summary(
        code_repository_root=context.code_repository_root,
        context=context,
        output=DriftSummaryOutput(
            report_path=report_path, include_rows=True, write_report=write_report
        ),
    )
    if summary.get("status") != "checked":
        raise RuntimeError(str(summary.get("error") or "drift measurement failed"))
    report = summary.get("reportPath")
    if not report:
        if write_report:
            raise RuntimeError("drift measurement produced no report")
        return summary, None
    return summary, Path(report)


def drift_summary(rows: list[drift.DriftRow]) -> dict[str, int]:
    return drift.counts(rows)


def actionable_rows(rows: list[drift.DriftRow]) -> list[drift.DriftRow]:
    return [row for row in rows if row.classification in drift.ACTIONABLE_CLASSIFICATIONS]


def current_branch(repo: Path) -> str:
    return worktree_manager.current_branch(repo)


def head_commit(repo: Path, ref: str = "HEAD") -> str:
    return worktree_manager.head_commit(repo, ref)


#: The exclusions below are the shared memory-content policy, declared in ``models`` so all
#: four producing seams read one set. They are carried on the call that actually stages:
#: ``commit_if_dirty`` re-stages the whole worktree, so an exclusion applied only to a bare
#: ``git add`` before it is inert and the file lands in the commit anyway. That was this
#: leaf's baseline defect; the case in ``test_memory_branch_authority.py`` fails if it
#: returns.


def _baseline_default_branch(memory_root: Path) -> str:
    """Prove the existing default, or the exact unborn branch minted by memory_init.

    The branch is whatever ``memory_init`` recorded, not a fixed name: the memory
    repository is founded on the code branch the developer chose, and hard-coding ``main``
    here would refuse every repository whose memory is founded on anything else.
    """

    head = run_git(memory_root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode == 0:
        return memory_repository_default_branch(memory_root)
    configured = run_git(
        memory_root,
        ["config", "--get", DEFAULT_BRANCH_CONFIG_KEY],
    )
    branch = configured.stdout.strip().removeprefix("refs/heads/")
    if configured.returncode != 0 or not branch:
        raise RuntimeError(
            "memory baseline adoption requires explicit default-branch authority from "
            "memory_init; the memory repository records no "
            f"{DEFAULT_BRANCH_CONFIG_KEY} value"
        )
    ref = local_branch_ref(branch)
    existing = run_git(memory_root, ["show-ref", "--verify", "--quiet", ref])
    if existing.returncode == 0:
        return memory_repository_default_branch(memory_root)
    if existing.returncode != 1:
        raise RuntimeError("memory baseline adoption cannot verify its configured default branch")
    local_heads = run_git(
        memory_root,
        ["for-each-ref", "--format=%(refname)", "refs/heads"],
    )
    symbolic_head = run_git(memory_root, ["symbolic-ref", "--quiet", "HEAD"])
    if (
        local_heads.returncode != 0
        or local_heads.stdout.strip()
        or symbolic_head.returncode != 0
        or symbolic_head.stdout.strip() != ref
    ):
        raise RuntimeError(
            "memory baseline adoption requires the exact unborn default branch created by "
            "memory_init"
        )
    return branch


def _stage_for_commit(repo: Path, *, index_path: Path | None = None) -> None:
    """Stage the worktree exactly as `commit_if_dirty` would before it commits.

    With ``index_path`` the staging writes a private index file instead of the
    worktree's own index; without it the worktree's own index is staged.
    """

    stage_worktree_content(repo, exclude_paths=MEMORY_CONTENT_EXCLUDES, index_path=index_path)
    if MEMORY_CONTENT_EXCLUDES:
        if index_path is not None:
            _require_git_with_index(
                repo, ["update-index", "--force-remove", "--", *MEMORY_CONTENT_EXCLUDES], index_path
            )
        else:
            require_git(repo, ["update-index", "--force-remove", "--", *MEMORY_CONTENT_EXCLUDES])


def _converted_tree_refusal(context, candidate_tree: str, code_source_commit: str) -> str | None:
    """Ask `memory_commit_refusal` about the staged tree (MIK-R44 rule 7).

    No comparison bases: the first baseline validates the tree as authored. The
    tree is paired with the code commit the adoption attributes. Where the
    validator is not bound, this refuses; it never commits unvalidated.
    """

    try:
        return memory_commit_refusal(
            memory_repository=context.memory_root,
            candidate_tree=candidate_tree,
            bases=[],
            paired_code=PairedCode(
                repository=context.code_repository_root,
                commit=code_source_commit,
            ),
        )
    except WorktreeServicesUnboundError:
        return (
            "the knowledge validator (MIK-R22) is not bound in this process; "
            "a converted memory commit is never committed unvalidated"
        )


@dataclass(frozen=True)
class _AdoptionEntry:
    """The entry `.gitignore` a converted adoption must give back on refusal or dry run.

    Only the working file needs giving back: the worktree's own index is
    never written during the prepare/stage/validate interval, so there is no
    index state to restore.
    """

    ignore_bytes: bytes | None
    ignore_mode: int | None


def _read_ignore_file(repo: Path) -> tuple[bytes | None, int | None]:
    ignore = repo / ".gitignore"
    if not ignore.is_file():
        return None, None
    return ignore.read_bytes(), stat.S_IMODE(ignore.stat().st_mode)


def _capture_entry(repo: Path) -> _AdoptionEntry:
    ignore_bytes, ignore_mode = _read_ignore_file(repo)
    return _AdoptionEntry(ignore_bytes=ignore_bytes, ignore_mode=ignore_mode)


def _expected_prepared_bytes(entry_bytes: bytes | None) -> bytes:
    """The bytes preparation leaves behind, computed from entry bytes alone.

    Replicates the deterministic ignore-line rule owned by
    :func:`kernel.memory_cache.ignore_memory_cache` for the failure path where
    preparation raised before returning its bytes (L44-C1-03). If that rule
    ever changes, the byte guard below fails safe: a mismatch preserves the
    file and propagates the original error.
    """

    text = entry_bytes.decode("utf-8") if entry_bytes is not None else ""
    line = f"/{LEDGER_RELATIVE_PATH}"
    if line not in text.splitlines():
        separator = "" if not text or text.endswith("\n") else "\n"
        text = f"{text}{separator}{line}\n"
    return text.encode("utf-8")


def _restore_file(repo: Path, entry: _AdoptionEntry, prepared: bytes | None) -> bool:
    """Restore owned `.gitignore` bytes iff they are provably ours.

    When preparation never returned its bytes, the expected bytes are computed
    from the entry instead. Anything else on disk — including a concurrent
    external edit or an unreadable file — is preserved, reported by returning
    `False`, and never raises. Returns `True` when the entry bytes were put back.
    """

    expected = prepared if prepared is not None else _expected_prepared_bytes(entry.ignore_bytes)
    try:
        current, _ = _read_ignore_file(repo)
    except OSError:
        return False
    if current != expected:
        return False
    ignore = repo / ".gitignore"
    if entry.ignore_bytes is None:
        ignore.unlink(missing_ok=True)
    else:
        ignore.write_bytes(entry.ignore_bytes)
        if entry.ignore_mode is not None:
            os.chmod(ignore, entry.ignore_mode)
    return True


@contextmanager
def _private_staging_index(repo: Path) -> Iterator[Path]:
    """A private copy of the worktree's own index for one staging interval.

    Every prepare/stage/tree command of the interval runs against this file,
    so the worktree's own index is never written before validation passes; a
    dry run, a refusal, or any exception discards the file, and concurrent
    staged edits are preserved by construction. The copy keeps the index
    file's time (:func:`copy_git_index`), so ``git add`` trusts exactly what
    it would trust on the worktree's own index. A worktree with no index file
    yet stages from an empty private index, exactly as ``git add`` would on
    the worktree's own missing index. Any other copy failure refuses loudly:
    there is no fallback index to stage from.
    """

    try:
        real_index, _top = _real_index(repo)
    except _IndexCopyUnusable as error:
        raise RuntimeError(
            f"memory baseline adoption cannot locate the worktree index: {error}"
        ) from error
    with tempfile.TemporaryDirectory(prefix="ar-baseline-index-") as temporary:
        private = Path(temporary) / "index"
        try:
            copy_git_index(real_index, private)
        except FileNotFoundError:
            # No index file yet: the private path stays absent, which Git
            # reads as an empty index — exactly as it would read the
            # worktree's own missing index.
            pass
        except OSError as error:
            raise RuntimeError(
                f"memory baseline adoption cannot copy the worktree index: {error}"
            ) from error
        yield private


def _prepare_stage_validate(context, code_source_commit: str):
    """Run the whole owned prepare/stage/validate interval on a private index.

    Every step after the read-only entry capture — cache preparation, staging
    and validation — runs against a private copy of the worktree's own index,
    so the worktree's own index is never written before validation passes: a
    dry run, a refusal, or any exception leaves it exactly as it was, and
    concurrent staged edits are preserved by construction (L44-C1-02,
    L44-C1-03). Any exception first restores the prepared `.gitignore` iff
    provably ours, then propagates with its name. The prepared bytes come
    from preparation itself, so no second file read can fail outside the
    guard. No unvalidated fallback is added. Returns the entry, the staged
    tree, the prepared bytes and the refusal (possibly `None`).
    """

    repo = context.memory_root
    entry = _capture_entry(repo)
    prepared_ignore: bytes | None = None
    try:
        with _private_staging_index(repo) as private:
            prepared_ignore = prepare_memory_cache(repo, index_path=private)
            _stage_for_commit(repo, index_path=private)
            staged_tree = _require_git_with_index(repo, ["write-tree"], private)
            refusal = _converted_tree_refusal(context, staged_tree, code_source_commit)
    except Exception:
        _restore_file(repo, entry, prepared_ignore)
        raise
    assert prepared_ignore is not None
    return entry, staged_tree, prepared_ignore, refusal


def _dry_run_converted_validation(context, code_source_commit: str) -> str | None:
    """Validate what a real adoption would record, changing nothing.

    A refusal, a dry run, or an exception anywhere in the owned interval leaves
    the index, the refs and every owned file as they were: the interval stages
    a private index that is discarded, the prepared `.gitignore` is given back
    before return, a raised error keeps its name, and a concurrent external
    edit is preserved while the original refusal meaning is still returned
    (L44-C1-02, L44-C1-03).
    """

    repo = context.memory_root
    if not has_changes(repo, exclude_paths=MEMORY_CONTENT_EXCLUDES):
        head = head_commit(repo)
        tree = require_git(repo, ["rev-parse", f"{head}^{{tree}}"]).strip()
        return _converted_tree_refusal(context, tree, code_source_commit)
    entry, _staged, prepared, refusal = _prepare_stage_validate(context, code_source_commit)
    _restore_file(repo, entry, prepared)
    return refusal


def _publish_validated_tree(repo: Path, staged_tree: str, message: str) -> str:
    """Record exactly the validated tree without advancing past it (L44-C1-04).

    The commit object is built from the immutable validated tree, so a hook or
    a concurrent index change can no longer smuggle different bytes into the
    publication; the branch ref then moves to that object only when it still
    points where validation saw it, and the recorded tree is verified.
    """

    try:
        parent = require_git(repo, ["rev-parse", "--verify", "HEAD"]).strip()
    except RuntimeError:
        parent = ""
    new_commit = require_git(
        repo,
        ["commit-tree", staged_tree, "-m", message, *(["-p", parent] if parent else [])],
    ).strip()
    branch = current_branch(repo)
    old = parent if parent else "0" * 40
    try:
        require_git(repo, ["update-ref", local_branch_ref(branch), new_commit, old])
    except RuntimeError as error:
        raise RuntimeError(
            f"adopted memory commit refuses to publish: the branch moved after validation ({error})"
        ) from error
    committed_tree = require_git(repo, ["rev-parse", f"{new_commit}^{{tree}}"]).strip()
    if committed_tree != staged_tree:
        raise RuntimeError(
            "adopted memory commit records a different tree than the validated staged tree"
        )
    return new_commit


def _adopt_validated_converted_tree(context, code_source_commit: str, message: str) -> str:
    """Stage the converted content, validate the staged tree, and commit exactly it.

    The interval stages a private index; on a refusal the prepared file is
    given back and the refusal is raised with the worktree's own index
    untouched. Only after validation passes is the validated tree published
    and the worktree's own index brought to the adopted state, so the
    published tree is still exactly the validated tree (L44-C1-04).
    """

    repo = context.memory_root
    if not has_changes(repo, exclude_paths=MEMORY_CONTENT_EXCLUDES):
        head = head_commit(repo)
        tree = require_git(repo, ["rev-parse", f"{head}^{{tree}}"]).strip()
        refusal = _converted_tree_refusal(context, tree, code_source_commit)
        if refusal is not None:
            raise BaselineValidationRefused(refusal)
        return head
    entry, staged, prepared, refusal = _prepare_stage_validate(context, code_source_commit)
    if refusal is not None:
        _restore_file(repo, entry, prepared)
        raise BaselineValidationRefused(refusal)
    published = _publish_validated_tree(repo, staged, message)
    prepare_memory_cache(repo)
    _stage_for_commit(repo)
    return published


def adopt_initial_baseline(context, source_branch: str, memory_branch: str) -> dict[str, object]:
    if not context.memory_root.exists():
        raise RuntimeError(
            f"memory root does not exist: {context.memory_root}. "
            "Run c-00-initialize-memory-repo before adopting a memory baseline."
        )
    if not (context.memory_root / ".git").exists():
        raise RuntimeError(
            f"external memory root is not a Git repo: {context.memory_root}. "
            "Run c-00-initialize-memory-repo before adopting a memory baseline."
        )

    if has_adopted_baseline(context.memory_root):
        raise RuntimeError("memory baseline adoption is only valid before attributed memory exists")
    default_branch = _baseline_default_branch(context.memory_root)
    requested_branch = memory_branch.removeprefix("refs/heads/")
    if requested_branch != default_branch or current_branch(context.memory_root) != default_branch:
        raise RuntimeError(
            "memory baseline adoption is a bootstrap-only exception on the checked-out "
            "repository-default branch; it cannot create, switch, or commit an integration ref"
        )

    ensure_git_identity(context.memory_root)

    existing_paths = [
        path.name
        for path in (context.memory_root / name for name in ("onboarding", "docs", "system"))
        if path.exists()
    ]
    if not existing_paths:
        raise RuntimeError(
            f"memory root has no onboarding, docs, or system content: {context.memory_root}. "
            "Run c-00-initialize-memory-repo first, then add onboarding before adopting."
        )

    # Resolve attribution once; the consumer cache derives the same pair from this commit.
    code_source_commit = branch_commit(context.code_repository_root, source_branch)
    message = render_memory_content_message(
        f"[adopt-{context.code_repository_name}-memory-baseline] Adopt external memory content",
        code_source_commit,
    )
    if is_converted_memory(context.memory_root):
        # The first baseline of a converted tree is validated before it is made:
        # the adoption stages the content, validates the staged tree with no bases
        # paired with the attributed code commit, and commits exactly that tree.
        # Cache preparation runs inside, after the entry state is captured, so a
        # refusal or an exception gives back the ignore line and the index.
        memory_content_commit = _adopt_validated_converted_tree(
            context, code_source_commit, message
        )
    else:
        prepare_memory_cache(context.memory_root)
        memory_content_commit = commit_if_dirty(
            context.memory_root,
            message,
            exclude_paths=MEMORY_CONTENT_EXCLUDES,
        )
    return {
        "state": "adopted-baseline",
        "memoryContentCommit": memory_content_commit,
        "ledgerCache": refresh_memory_cache(
            context.memory_root,
            path=context.ledger_path,
            repo_name=context.code_repository_name,
        ),
    }


def has_adopted_baseline(repository: Path) -> bool:
    if run_git(repository, ["rev-parse", "--verify", "HEAD"]).returncode != 0:
        return False
    return any(commit.is_attributed for commit in attributed_commits(repository, tip="HEAD"))


def ledger_status(repository: Path) -> dict[str, object]:
    try:
        ledger = derive_memory_ledger(repository)
    except MemoryAttributionError as error:
        return {"state": "unavailable", "error": str(error)}
    return {
        "state": "derived",
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
    }


class BaselineValidationRefused(RuntimeError):
    """The staged converted tree failed pre-commit validation; the index is already restored."""


def _legacy_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """The legacy classification counts from the summary's own serialized rows."""

    return {
        name: sum(1 for row in rows if row.get("classification") == name)
        for name in CLASSIFICATIONS
    }


def _legacy_actionable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("classification", "")) in ACTIONABLE_CLASSIFICATIONS]


def base_payload(
    context, summary: DriftSummaryPacket | list, report: Path | None
) -> dict[str, object]:
    ledger = ledger_status(context.memory_root)
    if isinstance(summary, dict) and summary.get("basis") == CONVERTED_BASIS:
        return _converted_base_payload(context, summary, report, ledger)
    rows = [row for row in summary.get("rows", [])] if isinstance(summary, dict) else list(summary)
    actionable = _legacy_actionable(rows)
    state = "ready"
    if ledger.get("state") == "derived" and ledger.get("lastMemoryContentCommit"):
        state = "already-adopted"
    elif (
        ledger.get("state") == "unavailable"
        and run_git(context.memory_root, ["rev-parse", "--verify", "HEAD"]).returncode == 0
    ):
        state = "unavailable"
    elif actionable:
        state = "blocked-drift"
    return {
        "state": state,
        "code_repository_name": context.code_repository_name,
        "topology": context.topology,
        "code_repository_root": context.code_repository_root.as_posix(),
        "memory_root": context.memory_root.as_posix(),
        "onboarding_root": context.onboarding_root.as_posix(),
        "ledger_path": context.ledger_path.as_posix() if context.ledger_path is not None else None,
        **({"drift_report": report.as_posix()} if report is not None else {}),
        "drift": {
            "counts": _legacy_counts(rows),
            "actionable": len(actionable),
        },
        "ledger": ledger,
    }


def _converted_base_payload(
    context, summary: DriftSummaryPacket, report: Path | None, ledger: dict[str, object]
) -> dict[str, object]:
    """The baseline payload for a converted tree (MIK-R44 rule 7).

    Three blocking states: `blocked-invalid` when validator violations exist,
    `blocked-drift` when stale anchors exist and drift was not accepted, and
    `unavailable` as today. The drift section carries the rule-2 fields of the one
    shared summary.
    """

    violation_count = int(summary.get("violationCount") or 0)
    stale = int(summary.get("actionableCount") or 0)
    states = summary.get("states") or {}
    legacy_cards = find_legacy_cards(context.memory_root)
    state = "ready"
    if ledger.get("state") == "derived" and ledger.get("lastMemoryContentCommit"):
        state = "already-adopted"
    elif (
        ledger.get("state") == "unavailable"
        and run_git(context.memory_root, ["rev-parse", "--verify", "HEAD"]).returncode == 0
    ):
        state = "unavailable"
    elif legacy_cards:
        state = "blocked-legacy-cards"
    elif violation_count:
        state = "blocked-invalid"
    elif stale:
        state = "blocked-drift"
    return {
        "state": state,
        "code_repository_name": context.code_repository_name,
        "topology": context.topology,
        "code_repository_root": context.code_repository_root.as_posix(),
        "memory_root": context.memory_root.as_posix(),
        "onboarding_root": context.onboarding_root.as_posix(),
        "ledger_path": context.ledger_path.as_posix() if context.ledger_path is not None else None,
        **({"drift_report": report.as_posix()} if report is not None else {}),
        "legacyCards": legacy_cards,
        "drift": {
            "status": "checked",
            "basis": CONVERTED_BASIS,
            "memoryRoot": summary.get("memoryRoot"),
            "codeRoot": summary.get("codeRoot"),
            "sidecarCount": summary.get("sidecarCount"),
            "count": summary.get("count"),
            "states": dict(states),
            "counts": dict(states),
            "actionable": stale,
            "actionableCount": stale,
            "actionableSample": summary.get("actionableSample"),
            "violationCount": violation_count,
            "violationSample": summary.get("violationSample"),
            "reportOnlyCount": summary.get("reportOnlyCount"),
            "unreadableSidecars": summary.get("unreadableSidecars"),
        },
        "ledger": ledger,
    }


def baseline_status(request: BaselineRequest) -> dict[str, object]:
    context = resolve_request_context(request)
    # A status read files nothing (rule 6): only an explicit report path is a
    # report producer.
    summary, report = run_drift(
        context, request.report, write_report=request.report is not None
    )
    return base_payload(context, summary, report)


def _converted_adopt_gate(
    payload: dict[str, object], *, accept_drift: bool
) -> tuple[int, dict[str, object]] | None:
    """The converted adoption gate (MIK-R44 rule 7, MIK-R45 rule 3).

    Returns the refusal `(returncode, payload)` when the tree may not be adopted,
    else `None`. `accept_drift` accepts stale anchors only: it never lets a tree
    with a validator violation through, and it never lifts `blocked-legacy-cards`.
    """

    legacy_cards = payload.get("legacyCards")
    if isinstance(legacy_cards, list) and legacy_cards:
        payload["message"] = (
            "old-format cards block adoption: the first baseline of a converted tree "
            "holds no card with a metadata-table stamp row or an Update History heading -- "
            "correct the listed cards and run adoption again; accept_drift cannot clear it"
        )
        return 2, payload
    drift_section = payload.get("drift")
    drift_info = drift_section if isinstance(drift_section, dict) else {}
    if int(drift_info.get("violationCount") or 0):
        payload["message"] = (
            "the knowledge validator refuses this memory tree; "
            "accept_drift cannot clear a validator violation -- correct the named "
            "files and run adoption again"
        )
        return 2, payload
    if int(drift_info.get("actionableCount") or 0) and not accept_drift:
        payload["message"] = (
            "actionable drift blocks adoption; refresh onboarding with the "
            "c-05-create-or-update-onboarding-files skill or rerun with --accept-drift"
        )
        return 2, payload
    return None


def baseline_adopt(
    request: BaselineRequest,
    *,
    accept_drift: bool = False,
    source_branch: str | None = None,
    work_branch: str | None = None,
    dry_run: bool = False,
) -> tuple[int, dict[str, object]]:
    context = resolve_request_context(request)
    if context.topology != "external":
        raise RuntimeError("adoption requires external topology")
    # A dry-run preview files nothing (rule 6); the mutating adoption keeps
    # full report behavior.
    summary, report = run_drift(
        context, request.report, write_report=not dry_run
    )
    payload = base_payload(context, summary, report)
    if payload["state"] == "already-adopted":
        return 0, payload
    if payload["state"] == "unavailable":
        payload["message"] = (
            "memory Git history is unavailable; restore readable history before adoption"
        )
        return 2, payload
    if summary.get("basis") == CONVERTED_BASIS:
        return _baseline_adopt_converted(
            context,
            payload,
            _ConvertedAdoptOptions(
                accept_drift=accept_drift,
                source_branch=source_branch,
                work_branch=work_branch,
                dry_run=dry_run,
            ),
        )
    if _legacy_actionable([row for row in summary.get("rows", [])]) and not accept_drift:
        payload["message"] = (
            "actionable drift blocks adoption; refresh onboarding with the c-05-create-or-update-onboarding-files skill or rerun with --accept-drift"
        )
        return 2, payload
    if dry_run:
        payload["state"] = "would-adopt"
        payload["accepted_drift"] = bool(accept_drift)
        return 0, payload

    resolved_source_branch = source_branch or current_branch(context.code_repository_root)
    result = adopt_initial_baseline(
        context, resolved_source_branch, work_branch or resolved_source_branch
    )
    payload["state"] = "adopted"
    payload["accepted_drift"] = bool(accept_drift)
    payload["bootstrap"] = result
    payload["ledger"] = ledger_status(context.memory_root)
    return 0, payload


@dataclass(frozen=True)
class _ConvertedAdoptOptions:
    accept_drift: bool
    source_branch: str | None
    work_branch: str | None
    dry_run: bool


def _baseline_adopt_converted(
    context,
    payload: dict[str, object],
    options: _ConvertedAdoptOptions,
) -> tuple[int, dict[str, object]]:
    gate = _converted_adopt_gate(payload, accept_drift=options.accept_drift)
    if gate is not None:
        return gate
    resolved_source_branch = options.source_branch or current_branch(context.code_repository_root)
    code_source_commit = branch_commit(context.code_repository_root, resolved_source_branch)
    if options.dry_run:
        refusal = _dry_run_converted_validation(context, code_source_commit)
        if refusal is not None:
            payload["state"] = "blocked-invalid"
            payload["message"] = refusal
            return 2, payload
        payload["state"] = "would-adopt"
        payload["accepted_drift"] = bool(options.accept_drift)
        return 0, payload
    try:
        result = adopt_initial_baseline(
            context, resolved_source_branch, options.work_branch or resolved_source_branch
        )
    except BaselineValidationRefused as error:
        payload["state"] = "blocked-invalid"
        payload["message"] = str(error)
        return 2, payload
    payload["state"] = "adopted"
    payload["accepted_drift"] = bool(options.accept_drift)
    payload["bootstrap"] = result
    payload["ledger"] = ledger_status(context.memory_root)
    return 0, payload


def command_status(args: argparse.Namespace) -> int:
    print(json.dumps(baseline_status(request_from_args(args)), indent=2))
    return 0


def command_adopt(args: argparse.Namespace) -> int:
    returncode, payload = baseline_adopt(
        request_from_args(args),
        accept_drift=args.accept_drift,
        source_branch=args.source_branch,
        work_branch=args.work_branch,
        dry_run=args.dry_run,
    )
    print(json.dumps(payload, indent=2))
    return returncode


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--code-repository-name", help="Code repository name to resolve.")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.cwd(),
        help="Workspace root used to find --code-repository-name.",
    )
    parser.add_argument(
        "--code-repository-root",
        type=Path,
        help="Root directory of the code repository to resolve.",
    )
    parser.add_argument(
        "--topology",
        metavar="external",
        help="Optional topology override. `external` is the only supported topology.",
    )
    parser.add_argument("--coordination-root", type=Path, help="Optional coordination root.")
    parser.add_argument(
        "--report", type=Path, help="Optional c-02-memory-quality-control drift report path."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status")
    add_common(status)
    status.set_defaults(func=command_status)

    adopt = subparsers.add_parser("adopt")
    add_common(adopt)
    adopt.add_argument(
        "--accept-drift",
        action="store_true",
        help="Accept current onboarding as factual enough to become the baseline.",
    )
    adopt.add_argument(
        "--source-branch", help="Code branch to attribute in Git. Defaults to current branch."
    )
    adopt.add_argument("--work-branch", help="Memory branch to use. Defaults to source branch.")
    adopt.add_argument("--dry-run", action="store_true")
    adopt.set_defaults(func=command_adopt)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
