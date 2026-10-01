"""Application entry points for memory-facing MCP tools."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_bootstrap_admission import (
    BootstrapRefusal,
    admit_bootstrap_context,
)
from agents_remember.application.memory_scope import (
    MemoryScope,
)
from agents_remember.application.memory_scope import (
    resolve_memory_scope as _memory_scope,
)
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.application.runtime.startup import measuring_build_stamp
from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory import baseline, carryover
from agents_remember.memory.conversion import card_authoring
from agents_remember.memory_quality import reference_state
from agents_remember.memory_quality.converted_cards import card_sidecar_path
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.style.citations import (
    fixer,
    migration,
    range_resolution,
    source_index,
)
from agents_remember.memory_quality.style.citations.exclusion_register import (
    validate_caller_excludes,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
)
from agents_remember.worktrees.integration.integration_branch_repository import (
    BranchAuthorityUnavailable,
)
from agents_remember.worktrees.worktree_contract import load_contract


@dataclass(frozen=True)
class CitationOperationScope:
    """One owned memory document and the prevalidated source generation it uses.

    An expected snapshot is an explicit frozen-wave lease, not a freshness detector. The
    operator builds and validates once, freezes source, and passes the returned id to every
    independent document operation. A source edit requires a new freeze/build; Git HEAD is
    intentionally not consulted because it cannot represent dirty or untracked content.
    """

    document: str | None = None
    expected_snapshot: str | None = None
    excludes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.validate(document_alone=True)

    def validate(self, *, document_alone: bool = False) -> None:
        """Enforce the indivisible document-plus-generation frozen-wave contract.

        The caller's own excludes ride with the scope because they scope the same acquisition
        the document and the frozen generation do: which bytes this one operation reads. They
        are validated here, before any work tree is resolved, so a pattern that cannot mean
        anything is refused at the call rather than during a tree walk.

        ``document_alone`` admits one document with no frozen generation. That is the converted
        memory's form (L37 ruling of 2026-10-01T10:06:02): a converted card is authored against
        the code working tree and reads no source index, so a curator names one card and nothing
        else. Construction validates in that form, because the tree's format is unknown until the
        leaf is resolved; every legacy operation then validates strictly before it does any work.
        """
        if document_alone and self.document is not None and self.expected_snapshot is None:
            validate_caller_excludes(self.excludes)
            return
        source_index.validate_operation_scope(
            self.document,
            self.expected_snapshot,
            leased_index=False,
        )
        validate_caller_excludes(self.excludes)


DEFAULT_CITATION_OPERATION_SCOPE = CitationOperationScope()


def drift_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    detail_limit: int = 50,
    contract_path: str | None = None,
) -> dict[str, Any]:
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    packet = run_drift_summary(
        code_repository_root=scope.code_root,
        context=scope.context,
        detail_limit=detail_limit,
    )
    return {
        "ok": packet.get("status") == "checked",
        "operation": "drift_check",
        "onboardingRoot": scope.onboarding_root.as_posix(),
        **packet,
    }


def _refuse_official_memory(repo: RepositoryScope, scope: MemoryScope) -> None:
    """The write guard: a citation rewrite happens in a LEAF or it does not happen.

    ``contract_path`` is mandatory rather than optional here, which is the whole guard --
    there is no argument list that names the official memory repo. This is the second half,
    for a contract that names the official repo AS its memory worktree.

    The defect this exists to avoid inheriting is a measured one: ``route_index_refresh``
    resolved the official repo from ``repo_id`` alone and generated indexes into a
    repository the session did not own, which left the leaf stale and made the official
    repo dirty -- and ``worktree_start`` refuses to open the next task while it is. A
    citation fix rewrites ranges across thousands of rows, with no closeout reviewing the
    result. It refuses; it never falls back.
    """
    official = repo.memory_root.resolve() if repo.memory_root is not None else None
    target = scope.onboarding_root.resolve()
    if official is not None and (official == target or official in target.parents):
        raise AuthorityError(
            f"memory-tree mutation refuses to write into the OFFICIAL memory repo "
            f"({official.as_posix()}); it writes only into a leaf's memory worktree, "
            f"resolved from that leaf's enclosure contract. Start or attach a worktree and "
            f"pass its contract path"
        )


def _leaf_memory_writer_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    operation: str,
) -> MemoryScope:
    path = require_within_coordination(config, contract_path, "contract_path")
    contract = load_contract(path)
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    _refuse_official_memory(require_repo(config, repo_id), scope)
    require_ordinary_worktree(contract, operation=operation)
    return scope


def _citation_trees(
    scope: MemoryScope,
    caller_excludes: Sequence[str] = (),
) -> Trees:
    """The citation operation's trees, carrying the caller's own excludes for this call.

    One construction point for all four citation operations, so a caller-supplied exclude
    cannot be honoured by one of them and silently dropped by another. The settings-derived
    register and the caps come from the memory layer's ``system/settings.json`` through
    :class:`Trees` itself.
    """
    return Trees(
        code_root=scope.code_root,
        memory_root=scope.onboarding_root.parent,
        cache_authority=scope.cache_authority,
        caller_excludes=validate_caller_excludes(caller_excludes),
    )


def citation_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Report one leaf's citations, optionally scoped to a single document.

    Separate from the memory-quality controller because ``document`` would be a LIE there:
    the other style checks do not honour it, so a scoped call would report the citations of
    one document beside three tree-wide results under one ``ok``. Scope belongs where every
    check under it is scoped.

    Read-only, so unlike the fix and migrate tools this does not refuse the official memory
    repo -- reading it is legitimate; only writing it is not.
    """
    operation_scope.validate()
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    memory_root = scope.onboarding_root.parent
    if reference_state.is_converted_memory(memory_root):
        # MIK-R24 rule 5: a converted tree's citations are its sidecar references; a stale one
        # is reported (report-only), never a gate finding.
        return {
            "repoId": scope.repo_id,
            **reference_state.check_references(memory_root, scope.code_root),
        }
    trees = _citation_trees(scope, operation_scope.excludes)
    return {
        "repoId": scope.repo_id,
        **range_resolution.check_onboarding_root(
            scope.onboarding_root,
            trees,
            only=operation_scope.document,
            expected_snapshot=operation_scope.expected_snapshot,
        ),
    }


def citation_source_index_build_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Build or validate the reusable source snapshot selected by a leaf contract.

    A caller that adds excludes is asserting a *narrower* population than the register alone,
    because the source is frozen while a curator wave runs and one document may need a tree
    another document still cites.
    """
    operation_scope.validate()
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    return {
        "repoId": scope.repo_id,
        **source_index.build_repository_index(_citation_trees(scope, operation_scope.excludes)),
    }


def citation_fix_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    dry_run: bool = False,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Regenerate ranges in one leaf, optionally against an explicit frozen generation.

    ``caller_excludes`` narrows the acquisition for THIS call only: a curator repairing one
    document may need a vendored or generated tree out of the way without editing the shared
    register every other document reads.

    On converted memory one document needs no frozen generation (``--document`` alone); on
    unconverted memory a document still comes with its snapshot.
    """
    operation_scope.validate(document_alone=True)
    scope = _leaf_memory_writer_scope(
        config,
        repo_id=repo_id,
        contract_path=contract_path,
        operation="citation_fix",
    )
    memory_root = scope.onboarding_root.parent
    if reference_state.is_converted_memory(memory_root):
        return {
            "repoId": scope.repo_id,
            **_converted_citation_fix(memory_root, scope.code_root, operation_scope, dry_run),
            **measuring_build_stamp(),
        }
    operation_scope.validate()
    trees = _citation_trees(scope, operation_scope.excludes)
    return {
        "repoId": scope.repo_id,
        **fixer.fix_onboarding_root(
            scope.onboarding_root,
            trees,
            dry_run=dry_run,
            only=operation_scope.document,
            expected_snapshot=operation_scope.expected_snapshot,
        ),
        # D-33: the repair engine is part of the measuring machinery, so the response names the
        # build whose rules produced these counts rather than leaving the reader to assume it is
        # the candidate's own code.
        **measuring_build_stamp(),
    }


def _converted_citation_fix(
    memory_root: Path, code_root: Path, scope: CitationOperationScope, dry_run: bool
) -> dict[str, Any]:
    """``citation_fix`` on a converted tree: author the cards' citation rows into resolved sidecar
    references (:mod:`...memory.conversion.card_authoring`), then re-record the mechanically moved
    anchors (MIK-R24 rule 5); a stale anchor is left to the curator. With one document named, both
    steps touch that card and its sidecar only."""

    authoring = card_authoring.author_card_references(
        memory_root, code_root, only=scope.document, dry_run=dry_run
    )
    sidecar = (
        None if scope.document is None else card_sidecar_path(f"{ONBOARDING_ROOT}/{scope.document}")
    )
    fixed = reference_state.fix_references(memory_root, code_root, dry_run=dry_run, only=sidecar)
    return {**fixed, "ok": fixed["ok"] and not authoring["refused"], "authoring": authoring}


def citation_migrate_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
    dry_run: bool = False,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Convert a memory tree to the anchored citation format, inside one leaf's worktree.

    Same guard as ``citation_fix_tool`` and for a stronger reason: this rewrites the SHAPE of
    every evidence table in the tree, which is not a diff any closeout could review if it
    landed in a repository the session does not own.
    """
    operation_scope.validate()
    scope = _leaf_memory_writer_scope(
        config,
        repo_id=repo_id,
        contract_path=contract_path,
        operation="citation_migrate",
    )
    trees = _citation_trees(scope, operation_scope.excludes)
    return {
        "repoId": scope.repo_id,
        **migration.migrate_onboarding_root(
            scope.onboarding_root,
            trees,
            dry_run=dry_run,
            only=operation_scope.document,
            expected_snapshot=operation_scope.expected_snapshot,
        ),
        # Same ruler problem as ``citation_fix``: this rewrites ranges with the serving build's
        # rules, so the response has to say which build that was (D-33).
        **measuring_build_stamp(),
    }


def route_index_refresh_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    contract_path: str | None = None,
) -> dict[str, Any]:
    if not dry_run and contract_path is None:
        raise AuthorityError(
            "route_index_refresh apply requires a leaf contract_path; the configured "
            "official memory checkout is read-only outside a journaled landing"
        )
    scope = (
        _leaf_memory_writer_scope(
            config,
            repo_id=repo_id,
            contract_path=contract_path,
            operation="route_index_refresh",
        )
        if not dry_run and contract_path is not None
        else _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    )
    result = build_route_indexes(
        code_root=scope.code_root,
        onboarding_root=scope.onboarding_root,
        repository=scope.context.code_repository_name,
        storage=scope.context.storage,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "operation": "route_index_refresh",
        "repoId": scope.repo_id,
        "onboardingRoot": scope.onboarding_root.as_posix(),
        "dryRun": dry_run,
        **result.to_dict(),
    }


# The one route that populates a repository's knowledge foundation, named once because two
# different operations have to hand the developer the same next step.
KNOWLEDGE_BOOTSTRAP_ROUTE = (
    "run the taskless bootstrap: agents-remember knowledge-bootstrap --repo <repo_id> "
    "--list <curator hand-off list> --authorization-ref <ref> --commit"
)


def memory_init_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    initialize_git: bool = True,
    initial_branch: str | None = None,
) -> dict[str, Any]:
    """Scaffold a repository's memory root, and report what its knowledge foundation is.

    The scaffold this calls has never created knowledge storage, and that is correct: a memory root
    is a *place*, while the knowledge dataset is an authored result no initializer may invent. What
    was missing is the other half of that statement -- the initializer said nothing at all about
    where knowledge will live, so "the normal memory initializer never creates knowledge storage"
    read as "the product has no knowledge step" rather than as "the next step is the curator's
    bootstrap". The ``knowledge`` block below is that half, and it is a **measurement**: the location
    is the one the ordinary read route selects, its state is what a read of that location finds now
    (``not-recorded``, ``recorded`` or ``unusable``), and a location whose context cannot be admitted
    yet reports the admission's own refusal rather than a hopeful path.
    """

    result = initialize_memory(
        config,
        repo_id=repo_id,
        dry_run=dry_run,
        initialize_git=initialize_git,
        initial_branch=initial_branch,
    )
    result["knowledge"] = _knowledge_foundation_state(config, repo_id)
    return result


def _knowledge_foundation_state(config: McpRuntimeConfig, repo_id: str) -> dict[str, Any]:
    """Where this repository's knowledge foundation lives, and what is there now.

    The admission used here is the bootstrap's own, so the location a curator is told to populate is
    the location the bootstrap publishes to, which is the location the ordinary read route selects.
    Nothing here writes: an initializer that created knowledge would be inventing authored content,
    and the whole point of the split is that it does not.
    """

    admitted = admit_bootstrap_context(config, repo_id)
    if isinstance(admitted, BootstrapRefusal):
        return {
            "state": "context-not-admitted",
            "datasetPath": None,
            "code": admitted.code,
            "detail": admitted.detail,
            "nextAction": admitted.next_action,
        }
    resolved = resolve_published_intent(admitted.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return {
            "state": resolved.state,
            "datasetPath": resolved.dataset_path.as_posix(),
            "code": resolved.code,
            "detail": resolved.detail,
            "nextAction": KNOWLEDGE_BOOTSTRAP_ROUTE,
        }
    return {
        "state": "recorded",
        "datasetPath": resolved.database_path.as_posix(),
        "code": None,
        "detail": (
            f"the repository's published knowledge dataset {resolved.logical_digest} is recorded "
            f"at the location the ordinary read route selects, bound to {resolved.repository_id}"
        ),
        "nextAction": (
            "resume or extend it through the ordinary curator ingest: agents-remember "
            "knowledge-ingest, or knowledge-bootstrap for a taskless run"
        ),
    }


@dataclass(frozen=True)
class MemoryBranches:
    """The external-memory repo's branch pair: the line adoption starts from and the line it
    works on. Either side omitted falls back to the memory repo's configured default."""

    source_branch: str | None = None
    work_branch: str | None = None


DEFAULT_MEMORY_BRANCHES = MemoryBranches()
"""The memory repo's own configured branches -- neither side overridden by the caller."""


@dataclass(frozen=True)
class CarryoverSelection:
    """Which branch memory is carried onto which landed code.

    ``source_memory`` is the branch's memory repo; the three refs bound the landed
    range the carryover is allowed to trust -- the landed code tip, the source branch tip
    its memory describes, and the base the branch was cut from. ``contract_path`` names the
    open recovery leaf that owns the target memory worktree. ``replace_existing`` decides
    whether landed entries may overwrite different recovery-leaf entries.
    """

    repo_id: str
    contract_path: str
    source_memory: str
    official_code_ref: str
    source_code_ref: str
    old_base: str
    replace_existing: bool = False


@dataclass(frozen=True)
class CarryoverCommitMessages:
    """The recovery-leaf onboarding commit message."""

    memory: str = "Carry over landed branch memory"


DEFAULT_CARRYOVER_MESSAGES = CarryoverCommitMessages()
"""The standard carryover commit subjects, used when the caller supplies none."""


def memory_baseline_status_tool(config: McpRuntimeConfig, *, repo_id: str) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    payload = baseline.baseline_status(_baseline_request(config, repo))
    return {
        "ok": payload.get("state") not in {"blocked-drift", "unavailable"},
        "operation": "memory_baseline_status",
        **payload,
    }


def memory_baseline_adopt_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    accept_drift: bool = False,
    branches: MemoryBranches = DEFAULT_MEMORY_BRANCHES,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Adopt the first memory baseline, or refuse in the envelope like its status sibling.

    The sibling ``memory_baseline_status`` answers the same repository with
    ``state: "ready"``-and-``ok``; a memory repository that has never recorded its
    default-branch authority is an ordinary *precondition*, not a crash, so it is answered
    with ``state``/``status``/``nextAction`` here rather than escaping as a traceback in which
    the caller loses the whole envelope (`260918-TSIP` `T34`, owner `L6`).

    The caught type is deliberately narrow. ``BranchAuthorityUnavailable`` means "the authority
    this operation needs was never recorded", and its own message names the remedy. The other
    ``RuntimeError``s on this path -- *memory baseline adoption is a bootstrap-only exception on
    the checked-out repository-default branch*, *memory root does not exist* -- are refusals of
    a state the caller must understand and change, and they keep raising: turning them into
    refusals would hide a misuse behind an envelope. That boundary is what
    ``test_memory_branch_authority.py::test_baseline_adoption_refuses_a_branch_the_memory_repository_does_not_record``
    holds, and this is the narrow form that keeps it true.
    """

    repo = require_repo(config, repo_id)
    try:
        returncode, payload = baseline.baseline_adopt(
            _baseline_request(config, repo),
            accept_drift=accept_drift,
            source_branch=branches.source_branch,
            work_branch=branches.work_branch,
            dry_run=dry_run,
        )
    except BranchAuthorityUnavailable as error:
        return _baseline_adopt_refusal(repo, error)
    return {"ok": returncode == 0, "operation": "memory_baseline_adopt", **payload}


def _baseline_adopt_refusal(repo, error: BranchAuthorityUnavailable) -> dict[str, Any]:
    """The typed refusal for an adoption precondition the memory layer reports by raising."""

    memory_root = Path(repo.memory_root).as_posix() if repo.memory_root is not None else ""
    return {
        "ok": False,
        "operation": "memory_baseline_adopt",
        "state": "refused",
        "status": "memory-baseline-precondition-unavailable",
        "detail": str(error).strip(),
        "repoId": repo.repo_id,
        "memoryRoot": memory_root,
        "nextAction": (
            "initialize the memory repository through memory_init (which records the "
            "default-branch authority adoption requires), then rerun memory_baseline_adopt; "
            "memory_baseline_status reports the same repository's state"
        ),
        "nextStep": {
            "summary": (
                "memory_baseline_adopt did not run: the memory repository is not ready for "
                "adoption."
            ),
            "nextTool": "memory_init",
        },
    }


def memory_carryover_plan_tool(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
) -> dict[str, Any]:
    request = _carryover_request(config, selection)
    carryover._require_carryover_authority(request, config)
    payload = carryover.build_plan_for_request(request)
    return {"ok": True, "operation": "memory_carryover_plan", **payload}


def memory_carryover_apply_tool(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
    messages: CarryoverCommitMessages = DEFAULT_CARRYOVER_MESSAGES,
) -> dict[str, Any]:
    payload = carryover._apply_carryover_for_request(
        _carryover_request(config, selection),
        authority=config,
        options=carryover.CarryoverApplyOptions(
            intent_note=intent_note,
            include_review_required=include_review_required,
            memory_commit_message=messages.memory,
        ),
    )
    return {"ok": True, "operation": "memory_carryover_apply", **payload}


def _baseline_request(config: McpRuntimeConfig, repo: RepositoryScope) -> baseline.BaselineRequest:
    return baseline.BaselineRequest(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        coordination_root=config.coordination_root,
        topology="external",
    )


def _carryover_request(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
) -> carryover.CarryoverRequest:
    repo = require_repo(config, selection.repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {selection.repo_id!r} does not have a memory root")
    source_memory_path = require_within_coordination(
        config, selection.source_memory, "source_memory"
    )
    contract_path = require_within_coordination(config, selection.contract_path, "contract_path")
    contract = load_contract(contract_path)
    if (
        contract.kind != "leaf"
        or contract.repo_name != repo.repo_id
        or contract.memory_mode != "external"
        or contract.memory_worktree is None
    ):
        raise AuthorityError(
            "memory carryover requires an external-memory leaf contract owned by repo_id"
        )
    if contract.closeout_status != "not-started" or contract.integration_status != "not-started":
        raise AuthorityError(
            "memory carryover target leaf must be open before closeout and integration"
        )
    require_ordinary_worktree(contract, operation="memory_carryover_apply")
    return carryover.CarryoverRequest(
        config_path=config.config_path,
        target_contract_path=contract.contract_path,
        code_repository_root=repo.path,
        official_code_ref=selection.official_code_ref,
        source_code_ref=selection.source_code_ref,
        old_base=selection.old_base,
        target_memory=contract.memory_worktree,
        source_memory=source_memory_path,
        code_repository_name=repo.repo_id,
        replace_existing=selection.replace_existing,
    )
