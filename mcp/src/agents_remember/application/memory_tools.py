"""Application entry points for memory-facing MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_bootstrap_admission import (
    BootstrapRefusal,
    admit_bootstrap_context,
)
from agents_remember.application.memory_quality.converted_base import converted_check_base
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
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory import baseline, carryover
from agents_remember.memory.conversion import card_authoring
from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.memory_quality import reference_state
from agents_remember.memory_quality.converted_cards import card_sidecar_path
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    DriftSummaryOutput,
    run_drift_summary,
)
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT
from agents_remember.models.knowledge_files.records import RECORD_MODELS
from agents_remember.models.lifecycles.curator_coherence import MAX_CURATOR_SOURCE_CANDIDATES
from agents_remember.worktrees.cutover_lock import (
    LEGACY_FORMAT_CODE,
    is_legacy_format_refusal,
    legacy_format_refusal,
)
from agents_remember.worktrees.integration.configured_contract_authority import (
    require_configured_contract_repositories,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
)
from agents_remember.worktrees.integration.integration_branch_repository import (
    BranchAuthorityUnavailable,
)
from agents_remember.worktrees.worktree_contract import load_contract


@dataclass(frozen=True)
class CitationOperationScope:
    """The converted cards one citation operation owns."""

    document: str | None = None
    documents: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        # Structural shape only: both/empty/bound are refused here, before anything is read,
        # for every operation. Whether the operation takes a list is the operation's own
        # validation at its call (``allow_documents``): only ``citation_fix`` passes it.
        self.validate(allow_documents=True)

    @property
    def selected_documents(self) -> tuple[str, ...] | None:
        """The named documents as one list, with ``document`` meaning a list of one.

        None is the form without a document: every card of the worktree in one run.
        """

        if self.document is not None:
            return (self.document,)
        if self.documents is not None:
            return self.documents
        return None

    def validate(self, *, allow_documents: bool = False) -> None:
        """Refuse both selectors, empty or oversized lists, and lists outside citation_fix.

        The document bound is the leaf's changed-card bound (MIK-R110 rule 2).
        Validation happens before the memory tree is resolved.
        """

        if self.document is not None and self.documents is not None:
            raise ValueError(
                "citation_fix takes document or documents, not both: document names one "
                "onboarding-relative file, documents names several in one call."
            )
        if self.documents is not None:
            if not allow_documents:
                raise ValueError(
                    "documents names several cards in one call and only citation_fix takes "
                    "it: name one document, or run citation_fix for the list."
                )
            if len(self.documents) == 0:
                raise ValueError(
                    "citation_fix with an empty documents list is refused and changes "
                    "nothing: name at least one onboarding-relative file."
                )
            if len(self.documents) > MAX_CURATOR_SOURCE_CANDIDATES:
                raise ValueError(
                    f"citation_fix takes at most {MAX_CURATOR_SOURCE_CANDIDATES} documents in "
                    f"one call (one leaf's changed files with cards); this call names "
                    f"{len(self.documents)}. Split the wave across calls."
                )


DEFAULT_CITATION_OPERATION_SCOPE = CitationOperationScope()


def drift_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    detail_limit: int = 50,
    contract_path: str | None = None,
) -> dict[str, Any]:
    scope = _memory_scope(config, repo_id=repo_id, contract_path=contract_path)
    # A drift read files no temp report (rule 6); the observer snapshot still applies and is
    # a named open boundary, not part of this fix.
    memory_root = scope.onboarding_root.parent
    if memory_root.is_dir() and not reference_state.is_converted_memory(memory_root):
        # MIK-R46 rules 1 and 2: drift on unconverted memory is status "error" with the one
        # rule-2 message; it is not measured in the old format.
        message = legacy_format_refusal(
            memory_root,
            operation="drift_check",
            subject=f"the memory tree {memory_root.as_posix()}",
        )
        return {
            "ok": False,
            "operation": "drift_check",
            "onboardingRoot": scope.onboarding_root.as_posix(),
            "status": "error",
            "error": message,
        }
    packet = run_drift_summary(
        code_repository_root=scope.code_root,
        context=scope.context,
        detail_limit=detail_limit,
        # A leaf that deleted a file carries that anchor as carried and stale, not as
        # newly written: with a contract the check compares against the leaf's converted
        # base, without one against the memory checkout's converted HEAD (MIK-R44 rule 4).
        base=(
            converted_check_base(scope, config.coordination_root)
            if contract_path is not None
            else None
        ),
        output=DriftSummaryOutput(write_report=False),
    )
    return {
        "ok": packet.get("status") == "checked",
        "operation": "drift_check",
        "onboardingRoot": scope.onboarding_root.as_posix(),
        **packet,
    }


def _refuse_official_memory(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    scope: MemoryScope,
) -> None:
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
    # A task-scoped MCP config replaces ``repo.memory_root`` with the admitted leaf worktree.
    # Resolve the official root from the original settings only after the existing contract
    # authority owner has checked both repository lineage and the candidate worktree identity.
    configured = (
        require_configured_contract_repositories(
            scope.contract,
            config.config_path.as_posix(),
        )
        if scope.contract is not None
        else repo
    )
    official = configured.memory_root.resolve() if configured.memory_root is not None else None
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
    _refuse_official_memory(config, require_repo(config, repo_id), scope)
    require_ordinary_worktree(contract, operation=operation)
    return scope


#: The admission a pre-baseline fixer call carries (MIK-R45 rule 2): the existing fixer
#: admitted on the configured memory root while that root has no attributed baseline.
PRE_BASELINE_ADMISSION = "pre-baseline"


def _memory_has_linked_worktrees(memory_root: Path) -> bool:
    """Whether the memory repository owns linked worktrees, read fail-closed.

    Before the first baseline no leaf can exist, because the memory repository has no
    commit to start a worktree from. A linked worktree beside the main checkout therefore
    means a leaf may depend on the root, and the pre-baseline admission does not apply.
    An unreadable worktree list is not an empty one: it refuses the same way.
    """

    listed = run_git(memory_root, ["worktree", "list", "--porcelain"])
    if listed.returncode != 0:
        return True
    entries = [line for line in listed.stdout.splitlines() if line.startswith("worktree ")]
    return len(entries) != 1


def _pre_baseline_official_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    operation: str,
) -> MemoryScope:
    """Admit one fixer call without a contract on the configured memory root (MIK-R45 rule 2).

    All five conditions must hold: the repository is declared in the settings; the resolved
    memory root is the declared one; the root holds ``knowledge/layout.json``;
    ``has_adopted_baseline`` is false for it; and the memory repository has no linked
    worktree, so no leaf depends on the root. Each way out names its missing condition and
    the leaf route, and writes nothing. Once the baseline exists, the refusal says so.
    """

    repo = require_repo(config, repo_id)
    if repo.memory_root is None:
        raise AuthorityError(
            f"{operation} without a leaf contract is refused: repo_id {repo_id!r} declares "
            f"no external memory root, so no pre-baseline admission applies; it requires a "
            f"leaf contract_path -- start or attach a worktree and pass its contract path"
        )
    scope = _memory_scope(config, repo_id=repo_id, contract_path=None)
    if scope.onboarding_root.parent.resolve() != repo.memory_root.resolve():
        raise AuthorityError(
            f"{operation} without a leaf contract is refused: the resolved memory root "
            f"({scope.onboarding_root.parent.as_posix()}) is not the declared one "
            f"({repo.memory_root.as_posix()}), so no pre-baseline admission applies; it "
            f"requires a leaf contract_path -- start or attach a worktree and pass its "
            f"contract path"
        )
    if not reference_state.is_converted_memory(repo.memory_root):
        raise AuthorityError(
            f"{operation} without a leaf contract is refused: the configured memory root "
            f"({repo.memory_root.as_posix()}) holds no knowledge/layout.json, so no "
            f"pre-baseline admission applies -- {legacy_format_refusal(repo.memory_root, operation=operation, subject='this memory root')}"
        )
    if baseline.has_adopted_baseline(repo.memory_root):
        raise AuthorityError(
            f"{operation} without a leaf contract is refused: the repository already has an "
            f"adopted memory baseline, so it requires a leaf contract_path -- start or "
            f"attach a worktree and pass its contract path"
        )
    if _memory_has_linked_worktrees(repo.memory_root):
        raise AuthorityError(
            f"{operation} without a leaf contract is refused: the memory repository has "
            f"linked worktrees (or its worktree list is unreadable), so a leaf may depend "
            f"on the root and no pre-baseline admission applies; it requires a leaf "
            f"contract_path -- start or attach a worktree and pass its contract path"
        )
    return scope


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

    Read-only, so unlike the fix tool this does not refuse the official memory
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
    # MIK-R46 rule 1: the check refuses unconverted memory with the one rule-2 message,
    # exactly as the fix does; read_ar_files is how such a tree is looked at.
    return {
        "repoId": scope.repo_id,
        "ok": False,
        "code": LEGACY_FORMAT_CODE,
        "detail": legacy_format_refusal(
            memory_root,
            operation="citation_check",
            subject=f"the memory tree {memory_root.as_posix()}",
        ),
    }


def citation_fix_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str | None = None,
    dry_run: bool = False,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
) -> dict[str, Any]:
    """Regenerate converted references in one leaf's memory worktree.

    ``documents`` names several cards for one call (MIK-R110 rule 2); each is
    handled exactly as a call naming it alone handles it. Old-format memory
    is refused before any authoring or reference repair.

    Two admissions: a leaf enclosure contract (the ordinary write route), or no contract on
    the configured memory root while that root has no attributed baseline (the pre-baseline
    bootstrap admission, MIK-R45 rule 2). In every other case a call without a contract
    refuses and names the leaf route.
    """
    operation_scope.validate(allow_documents=True)
    if contract_path is None:
        scope = _pre_baseline_official_scope(config, repo_id=repo_id, operation="citation_fix")
        memory_root = scope.onboarding_root.parent
        # The admission requires a converted root, so this always holds here.
        assert reference_state.is_converted_memory(memory_root)
        return {
            "repoId": scope.repo_id,
            "admission": PRE_BASELINE_ADMISSION,
            **_converted_citation_fix(memory_root, scope.code_root, operation_scope, dry_run),
            **measuring_build_stamp(),
        }
    scope = _leaf_memory_writer_scope(
        config,
        repo_id=repo_id,
        contract_path=contract_path,
        operation="citation_fix",
    )
    memory_root = scope.onboarding_root.parent
    if reference_state.is_converted_memory(memory_root):
        full = _converted_citation_fix(memory_root, scope.code_root, operation_scope, dry_run)
        return _citation_fix_result(
            {
                "repoId": scope.repo_id,
                # MIK-R110 rule 1: the answer's locations name where the call wrote.
                "onboardingRoot": scope.onboarding_root.as_posix(),
                **full,
                **_citation_fix_status(
                    full,
                    document_count=_citation_fix_document_count(memory_root, operation_scope, full),
                ),
            }
        )
    # MIK-R46 rule 1: the fix refuses unconverted memory with the one rule-2 message, in
    # every mode -- the old-format write path is gone, and the old-format dry-run report
    # with it; convert first, then repeat. The next action rides the whole result so the
    # delivered answer validates against it.
    return {
        "repoId": scope.repo_id,
        "ok": False,
        "code": LEGACY_FORMAT_CODE,
        "detail": legacy_format_refusal(
            memory_root,
            operation="citation_fix",
            subject=f"the memory tree {memory_root.as_posix()}",
        ),
        "nextAction": "citation_fix",
        **measuring_build_stamp(),
    }


def _citation_fix_result(result: dict[str, Any]) -> dict[str, Any]:
    """Name the recovery on a failed run beside its entries.

    MIK-R110 rule 1: a failed run is recovered by working the actionable
    entries, then running the tool again.
    """

    if not result.get("ok", False):
        result["nextAction"] = "citation_fix"
    return result


def _citation_fix_document_count(
    memory_root: Path, operation_scope: CitationOperationScope, full: dict[str, Any]
) -> int:
    """How many documents one ``citation_fix`` call covered (MIK-R110 rule 2).

    The named selection, with ``document`` meaning a list of one; without a named
    document, the documents the run scanned.
    """

    selected = operation_scope.selected_documents
    if selected is not None:
        return len(selected)
    scanned = full.get("documentsScanned")
    if isinstance(scanned, int):
        return scanned
    onboarding = memory_root / ONBOARDING_ROOT
    return sum(
        1
        for path in onboarding.rglob("*.md")
        if path.is_file() and not any(part.startswith(".") for part in path.parts)
    )


def _citation_fix_status(full: dict[str, Any], *, document_count: int) -> dict[str, Any]:
    """One ``citation_fix`` result answered as a status (MIK-R110 rules 1-2).

    The outcome, how many documents the call covered, and the counts: rewritten,
    stale, unreadable, refused. The lists themselves stay in the whole result --
    the answer keeps the first entries up to the list bound and the log names the
    detail file holding the rest.
    """

    authoring = full.get("authoring")
    refused = authoring.get("refused") if isinstance(authoring, dict) else None
    return {
        "documentCount": document_count,
        "rewrittenCount": len(full.get("rewrittenSidecars") or []),
        "staleCount": len(full.get("stale") or []),
        "unreadableCount": len(full.get("unreadableSidecars") or []),
        "refusedCount": len(refused or []),
    }


def _converted_citation_fix(
    memory_root: Path, code_root: Path, scope: CitationOperationScope, dry_run: bool
) -> dict[str, Any]:
    """``citation_fix`` on a converted tree: author the cards' citation rows into resolved sidecar
    references (:mod:`...memory.conversion.card_authoring`), then re-record the mechanically moved
    anchors (MIK-R24 rule 5); a stale anchor is left to the curator. With one document named, both
    steps touch that card and its sidecar only. A refused card is written by neither stage:
    its sidecar stays out of the mechanical re-recording as well (MIK-R45 failure behavior).

    With ``documents`` (MIK-R110 rule 2) each named card is handled exactly as a call naming
    it alone handles it -- author then re-record, one card at a time -- so the bytes on
    disk are the same either way. A name that matches no card is a refusal of that card
    and does not stop the others. A call with ``document`` behaves as today, including
    its refusal of a name that matches nothing.
    """

    if scope.documents is None:
        return _converted_single_citation_fix(memory_root, code_root, scope.document, dry_run)
    merged_authoring: dict[str, Any] | None = None
    merged_fixed: dict[str, Any] | None = None
    for name in dict.fromkeys(scope.documents):
        try:
            authoring = card_authoring.author_card_references(
                memory_root, code_root, only=name, dry_run=dry_run
            )
        except ValueError as error:
            authoring = {
                "dryRun": dry_run,
                "authoredReferences": 0,
                "reauthoredReferences": 0,
                "removedReferences": 0,
                "authoredCards": [],
                "createdSidecars": [],
                "unresolvedTargets": [],
                "refused": [{"card": f"{ONBOARDING_ROOT}/{name}", "reason": str(error)}],
            }
            fixed = {
                "ok": False,
                "status": "converted",
                "dryRun": dry_run,
                "refreshedAnchors": 0,
                "rewrittenSidecars": [],
                "unreadableSidecars": [],
                "stale": [],
            }
        else:
            sidecar = card_sidecar_path(f"{ONBOARDING_ROOT}/{name}")
            refused_sidecars = {
                card_sidecar_path(entry["card"])
                for entry in authoring["refused"]
                if isinstance(entry, dict) and isinstance(entry.get("card"), str)
            }
            fixed = reference_state.fix_references(
                memory_root,
                code_root,
                dry_run=dry_run,
                only=sidecar,
                exclude=refused_sidecars,
            )
        merged_authoring = (
            authoring if merged_authoring is None else _merge_authoring(merged_authoring, authoring)
        )
        merged_fixed = fixed if merged_fixed is None else _merge_converted_fix(merged_fixed, fixed)
    assert merged_authoring is not None and merged_fixed is not None
    return {
        **merged_fixed,
        "ok": merged_fixed["ok"] and not merged_authoring["refused"],
        "authoring": merged_authoring,
    }


def _merge_authoring(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Two per-card authoring reports as one call's report: summed counts, joined lists."""

    merged = dict(first)
    for key in ("authoredReferences", "reauthoredReferences", "removedReferences"):
        merged[key] = int(first.get(key, 0)) + int(second.get(key, 0))
    for key in ("authoredCards", "createdSidecars", "unresolvedTargets", "refused"):
        merged[key] = list(first.get(key, [])) + list(second.get(key, []))
    return merged


def _merge_converted_fix(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Two per-card re-record reports as one call's report."""

    return {
        "ok": bool(first.get("ok")) and bool(second.get("ok")),
        "status": "converted",
        "dryRun": bool(first.get("dryRun")) and bool(second.get("dryRun")),
        "refreshedAnchors": int(first.get("refreshedAnchors", 0))
        + int(second.get("refreshedAnchors", 0)),
        "rewrittenSidecars": list(first.get("rewrittenSidecars", []))
        + list(second.get("rewrittenSidecars", [])),
        "unreadableSidecars": list(first.get("unreadableSidecars", []))
        + list(second.get("unreadableSidecars", [])),
        "stale": list(first.get("stale", [])) + list(second.get("stale", [])),
    }


def _converted_single_citation_fix(
    memory_root: Path, code_root: Path, document: str | None, dry_run: bool
) -> dict[str, Any]:
    authoring = card_authoring.author_card_references(
        memory_root, code_root, only=document, dry_run=dry_run
    )
    sidecar = None if document is None else card_sidecar_path(f"{ONBOARDING_ROOT}/{document}")
    refused_sidecars = {
        card_sidecar_path(entry["card"])
        for entry in authoring["refused"]
        if isinstance(entry, dict) and isinstance(entry.get("card"), str)
    }
    fixed = reference_state.fix_references(
        memory_root, code_root, dry_run=dry_run, only=sidecar, exclude=refused_sidecars
    )
    return {**fixed, "ok": fixed["ok"] and not authoring["refused"], "authoring": authoring}


def route_index_refresh_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = False,
    contract_path: str | None = None,
) -> dict[str, Any]:
    if not dry_run and contract_path is None:
        scope = _pre_baseline_official_scope(
            config, repo_id=repo_id, operation="route_index_refresh"
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
            "admission": PRE_BASELINE_ADMISSION,
            "repoId": scope.repo_id,
            "onboardingRoot": scope.onboarding_root.as_posix(),
            "dryRun": dry_run,
            **result.to_dict(),
        }
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
    memory_root = scope.onboarding_root.parent
    if (
        not dry_run
        and memory_root.is_dir()
        and not reference_state.is_converted_memory(memory_root)
    ):
        # MIK-R46 rule 1: the apply refuses unconverted memory with the one rule-2 message;
        # the preview (dry_run) still reports as today. Convert first, then repeat.
        return {
            "ok": False,
            "operation": "route_index_refresh",
            "repoId": scope.repo_id,
            "code": LEGACY_FORMAT_CODE,
            "detail": legacy_format_refusal(
                memory_root,
                operation="route_index_refresh",
                subject=f"the memory tree {memory_root.as_posix()}",
            ),
            "onboardingRoot": scope.onboarding_root.as_posix(),
            "dryRun": dry_run,
        }
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
            "nextAction": (
                legacy_format_refusal(
                    admitted.context.memory_root,
                    operation="knowledge-bootstrap",
                    subject="this memory root",
                )
                if resolved.code == LEGACY_FORMAT_CODE
                else KNOWLEDGE_BOOTSTRAP_ROUTE
            ),
        }
    tree = resolved.memory_tree
    assert tree is not None
    with KnowledgeIndex(resolved.database_path, expected_key=tree.tree_key) as index:
        has_records = any(index.record_ids(kind).value for kind in RECORD_MODELS)
    state = (
        "unusable"
        if tree.index_state == "partial"
        else "recorded"
        if has_records
        else "not-recorded"
    )
    return {
        "state": state,
        "datasetPath": resolved.database_path.as_posix(),
        "code": "snapshot_unavailable" if state == "unusable" else None,
        "detail": (
            f"the memory tree {tree.tree_key} has a partial index: {tree.problems[:3]}"
            if state == "unusable"
            else f"text knowledge records at {admitted.context.memory_root} are indexed for memory tree {tree.tree_key}"
            if has_records
            else f"the complete memory tree {tree.tree_key} records no authored knowledge at {admitted.context.memory_root}"
        ),
        "nextAction": (
            "repair the named knowledge files, then rebuild their derived index"
            if state == "unusable"
            else KNOWLEDGE_BOOTSTRAP_ROUTE
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


def memory_baseline_status_tool(config: McpRuntimeConfig, *, repo_id: str) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    payload = baseline.baseline_status(_baseline_request(config, repo))
    return {
        "ok": payload.get("state")
        not in {
            "blocked-drift",
            "blocked-invalid",
            "blocked-legacy-cards",
            "unavailable",
            "legacy-format",
        },
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
    carryover.require_converted_formats(
        request.source_memory, request.target_memory, "memory_carryover_plan"
    )
    carryover._require_carryover_authority(request, config)
    payload = carryover.build_plan_for_request(request)
    return {"ok": True, "operation": "memory_carryover_plan", **payload}


def _carryover_legacy_refusal(
    detail: str, *, operation: str = "memory_carryover_apply"
) -> dict[str, Any]:
    """The typed refusal for a carryover the rule-1 lock reports by raising.

    The short message carries the refusal once for the answer guard; the full rule-2
    text stays in the logged detail.
    """

    short = (
        f"{operation} did not run: carryover memory is unconverted "
        "(no knowledge/layout.json); convert it first."
    )
    return {
        "ok": False,
        "operation": operation,
        "state": "refused",
        "code": LEGACY_FORMAT_CODE,
        "message": short,
        "detail": detail.strip(),
        "nextAction": (
            "convert the unconverted memory first (agents-remember knowledge-convert "
            f"MEMORY_ROOT --code CODE_REPOSITORY), then rerun {operation}; "
            "memory_baseline_status reports the same repository's state"
        ),
        "nextStep": {
            "summary": short,
            "nextTool": "memory_baseline_status",
        },
    }


def memory_carryover_apply_tool(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
) -> dict[str, Any]:
    try:
        payload = carryover._apply_carryover_for_request(
            _carryover_request(config, selection),
            authority=config,
            options=carryover.CarryoverApplyOptions(
                intent_note=intent_note,
                include_review_required=include_review_required,
            ),
        )
    except RuntimeError as error:
        if not is_legacy_format_refusal(str(error)):
            raise
        return _carryover_legacy_refusal(str(error))
    return {
        "ok": payload.get("state") != "refused",
        "operation": "memory_carryover_apply",
        **payload,
    }


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
