"""Engine Room process map: one node per worktree contract (slice 5e).

Composed, not read: contract + status guidance join the worktree's provider
stack and setup-progress boot sequence into the process node vocabulary. Pure
and deterministic so the served projection and sim replay stay byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.observer.projection import (
    CommitRefNode,
    EnclosureNode,
    EngineProcessEdge,
    EngineProcessFacts,
    EngineProcessNode,
    LandingRefNode,
    ProcessFactState,
    ProcessHealth,
    ProviderBootNode,
    ProviderNode,
    SetupProgressNode,
)

# --- engine room process map (slice 5e) --------------------------------------

_ENGINE_DOWN: frozenset[str] = frozenset({"stopped", "failed", "error", "down", "unreachable"})
_ENGINE_INDEXING: frozenset[str] = frozenset(
    {"indexing", "indexing-pending", "reindexing", "seeding"}
)
_SETUP_FAILED: frozenset[str] = frozenset({"failed", "failed-unchecked"})
_SETUP_DONE: frozenset[str] = frozenset({"ok", "complete", "prepared"})
_ROLE_ORDER: dict[str, int] = {"code": 0, "memory": 1}

# The ``lifecycle_guidance`` phases -> the process-map phase vocabulary (slice 5e; 05l adds ``abandoned``).
_GUIDANCE_PHASE: dict[str, str] = {
    "worktree-started": "worktree-started",
    "closeout-pending": "closeout-pending",
    "commit-approval-pending": "commit-approval-pending",
    "integration-pending": "integration-pending",
    "integration-blocked": "integration-blocked",
    "carryover-pending": "carryover-pending",
    "cleanup-pending": "cleanup-pending",
    "cleanup-completed": "completed",
    "abandoned": "abandoned",
}


def _is_disposed(fact: EngineProcessFacts) -> bool:
    """A cleaned-up or abandoned worktree whose runtime is gone (05l Gap B).

    ``cleanup`` reaching ``completed``/``abandoned`` means ``worktree_cleanup``/``worktree_abandon``
    already removed the worktrees + reclaimed the provider stack, so the enclosure is disposed. Drop
    it from the active engine-room set so the frontend (05k) animates the removal instead of
    rendering a fully-active phantom. ``cleanup-pending`` (cleanup not yet run) is intentionally kept,
    so the de-materialise beat still has a live node to animate.
    """
    return str(fact.contract.get("cleanup", "")) in {"completed", "abandoned"}


def build_engine_processes(
    facts: list[EngineProcessFacts],
    enclosures: list[EnclosureNode],
    providers: list[ProviderNode],
    setup_progress: list[SetupProgressNode],
    start_progress: list[dict[str, Any]] | None = None,
) -> list[EngineProcessNode]:
    """The enclosure-centered Engine Room process map (slice 5e), composed -- not read.

    One node per worktree contract, joining the recorded contract + status guidance (existence,
    dirty, base freshness, provider boot) with the worktree's isolated provider stack and its
    setup-progress boot sequence. ``start_progress`` (§5.4) adds a node for any start blocked
    *before* its contract was written -- the one start state the contract-keyed surface cannot
    see. Pure and deterministic (sorted by repo/task/id) so the served projection and sim replay
    stay byte-identical. The provider/setup join is on the worktree *group basename*:
    ``EnclosureNode.worktreeGroup`` is a full path, while ``ProviderNode.worktreeGroup`` and
    ``SetupProgressNode.group`` are the basename -- matching the existing dashboard join.
    """
    enclosure_by_id = {enclosure.enclosure: enclosure for enclosure in enclosures}
    providers_by_group: dict[str, list[ProviderNode]] = {}
    for provider in providers:
        if provider.scope == "worktree" and provider.worktreeGroup:
            providers_by_group.setdefault(provider.worktreeGroup, []).append(provider)
    for stack in providers_by_group.values():
        stack.sort(key=lambda provider: (_ROLE_ORDER.get(provider.role or "", 9), provider.id))
    setup_by_group = {node.group: node for node in setup_progress}
    nodes = [
        _engine_process(
            fact,
            enclosure_by_id.get(str(fact.contract.get("contract_path", ""))),
            providers_by_group,
            setup_by_group,
        )
        for fact in facts
        if not _is_disposed(fact)
    ]
    contract_groups = {
        str(fact.contract.get("worktree_group", "")).rsplit("/", 1)[-1] for fact in facts
    }
    for entry in start_progress or []:
        group = str(entry.get("worktreeGroup", "")).rsplit("/", 1)[-1]
        if group and group in contract_groups:
            continue  # the contract now anchors this enclosure; do not double-render
        nodes.append(_start_process_node(entry))
    nodes.sort(key=lambda node: (node.repoName, node.taskName, node.id))
    return nodes


# The pre-contract start phases (§5.4) -> the process-map phase vocabulary.
_START_PHASE: dict[str, str] = {
    "stale-base-blocked": "preflight",
    "memory-blocked": "memory-compatibility",
    "provider-blocked": "provider-setup",
    "preflight": "preflight",
    "code-worktree": "code-worktree",
    "contract-written": "contract-written",
}


def _start_process_node(entry: dict[str, Any]) -> EngineProcessNode:
    """Synthesize a node for a start blocked before its contract exists (slice 5e §5.4)."""
    completed = [str(phase) for phase in entry.get("completedPhases", []) or []]
    code_exists = "code-worktree" in completed
    blocked_reason = _str_or_none(entry.get("blockedReason"))
    phase = _START_PHASE.get(str(entry.get("phase", "")), "unknown")
    memory_mode = str(entry.get("memoryMode", ""))
    source_file = str(entry.get("sourceFile", ""))
    code_source = CommitRefNode(
        branch=_str_or_none(entry.get("codeSourceBranch")),
        commit=_str_or_none(entry.get("codeBaseCommit")),
        path=_str_or_none(entry.get("codeRepoPath")),
        factState="derived",
    )
    code_worktree = CommitRefNode(
        path=_str_or_none(entry.get("codeWorktree")),
        exists=code_exists,
        factState="observed" if code_exists else "planned",
    )
    memory_source = CommitRefNode(factState="planned") if memory_mode == "external" else None
    memory_worktree = (
        CommitRefNode(exists=False, factState="missing") if memory_mode == "external" else None
    )
    edges = [
        EngineProcessEdge(
            id="code-worktree-add",
            fromNode="code-source",
            toNode="code-worktree",
            kind="worktree-add",
            state="complete" if code_exists else "planned",
            label="add code worktree",
        ),
        EngineProcessEdge(
            id="cgc-seed",
            fromNode="code-worktree",
            toNode="cgc-engine",
            kind="cgc-seed",
            state="planned",
            label="CGC seed",
        ),
    ]
    if memory_mode == "external":
        edges.append(
            EngineProcessEdge(
                id="memory-worktree-add",
                fromNode="memory-source",
                toNode="memory-worktree",
                kind="ledger-map",
                state="blocked" if blocked_reason else "planned",
                label="ledger-map + memory worktree",
            )
        )
        edges.append(
            EngineProcessEdge(
                id="grepai-clone",
                fromNode="memory-worktree",
                toNode="grepai-engine",
                kind="grepai-clone",
                state="planned",
                label="GrepAI clone",
            )
        )
    missing = [f"start gated at {phase} — contract not yet written"]
    if blocked_reason:
        missing.append(blocked_reason)
    choices = [str(choice) for choice in entry.get("choices", []) or []]
    return EngineProcessNode(
        id=source_file or f"start:{entry.get('worktreeName', '')}",
        enclosure=source_file,
        worktreeGroup=str(entry.get("worktreeGroup", "")),
        taskId=str(entry.get("worktreeName", "")).upper(),
        taskName=str(entry.get("taskName", "")),
        repoName=str(entry.get("repoName", "")),
        phase=phase,
        health="blocked" if blocked_reason else "running",
        codeSource=code_source,
        codeWorktree=code_worktree,
        memoryMode=memory_mode,
        memorySource=memory_source,
        memoryWorktree=memory_worktree,
        humanReviewStatus="pending-review",
        closeoutStatus="not-started",
        integrationStatus="not-started",
        cleanup="pending",
        providers=[],
        edges=edges,
        actions=[],
        nextAction=choices[0] if choices else None,
        summary=blocked_reason or "Worktree start in progress (pre-contract).",
        missingFacts=missing,
        sourceFiles=[source_file] if source_file else [],
    )


def _engine_process(
    fact: EngineProcessFacts,
    enclosure: EnclosureNode | None,
    providers_by_group: dict[str, list[ProviderNode]],
    setup_by_group: dict[str, SetupProgressNode],
) -> EngineProcessNode:
    """One worktree contract as a process node: its two lanes, its boot facts, its conduits.

    The three derivations that make up the node are each a unit of their own -- the code
    lane's commit refs, the memory lane's (absent unless the contract runs external memory),
    and the worktree group's provider-boot facts -- so each is composed by its own helper
    below and this stays the assembly.
    """
    cp = fact.contract
    status = fact.status or {}
    guidance = fact.guidance
    enclosure_id = str(cp.get("contract_path", ""))
    group_full = str(cp.get("worktree_group", ""))
    group_key = group_full.rsplit("/", 1)[-1]
    memory_mode = str(cp.get("memory_mode", ""))

    freshness = _as_dict(status.get("freshness"))
    behind_official = freshness.get("state") == "behind-official"
    code = _code_refs(contract=cp, status=status, freshness=freshness)
    memory = _memory_refs(contract=cp, status=status, freshness=freshness, memory_mode=memory_mode)

    setup_node = setup_by_group.get(group_key)
    setup = _setup_facts(setup_node, status)
    observed_providers = providers_by_group.get(group_key, [])
    boot = _provider_boot_nodes(
        observed_providers,
        group_key=group_key,
        memory_mode=memory_mode,
        setup_state=setup.state,
    )
    has_provider_surface = bool(setup_node or observed_providers)

    phase = _process_phase(guidance.get("phase"), setup.state, behind_official=behind_official)
    health = _process_health(phase, setup.state, setup.failed_phases)
    edges = _process_edges(
        _ProcessLanes(
            memory_mode=memory_mode,
            code_worktree=code.worktree,
            memory_worktree=memory.worktree,
        ),
        boot,
        setup,
        behind_official=behind_official,
    )

    return EngineProcessNode(
        id=enclosure_id,
        enclosure=enclosure_id,
        worktreeGroup=group_full,
        taskId=str(cp.get("task_id", "")),
        leafId=str(cp.get("leaf_id", "")),
        taskName=str(cp.get("task_name", "")),
        repoName=str(cp.get("repo_name", "")),
        lifecycleId=_str_or_none(cp.get("lifecycle_id")),
        phase=phase,
        health=health,
        codeSource=code.source,
        codeWorktree=code.worktree,
        memoryMode=memory_mode,
        memorySource=memory.source,
        memoryWorktree=memory.worktree,
        ledgerPath=memory.ledger_path,
        ledgerRows=fact.ledger_rows,
        ledgerRowCount=fact.ledger_row_count,
        humanReviewStatus=str(cp.get("human_review_status", "")),
        closeoutStatus=str(cp.get("closeout_status", "")),
        integrationStatus=str(cp.get("integration_status", "")),
        integrationStrategy=_str_or_none(cp.get("integration_strategy")),
        cleanup=str(cp.get("cleanup", "")),
        carryoverDoneAt=_str_or_none(status.get("carryoverDoneAt")),
        setupState=setup.state,
        currentPhase=setup.current_phase,
        completedPhases=setup.completed_phases,
        failedPhases=setup.failed_phases,
        heartbeatAgeSeconds=setup.heartbeat_age_seconds,
        seedFallback=setup.seed_fallback,
        retryArgs=setup.retry_args,
        providers=boot,
        edges=edges,
        landing=[LandingRefNode(**ref) for ref in (status.get("landing") or [])],
        actions=list(enclosure.actions) if enclosure else [],
        nextAction=_str_or_none(guidance.get("nextOperation")),
        summary=str(guidance.get("summary", "")),
        missingFacts=_missing_facts(
            has_status=bool(status),
            contract=cp,
            memory_mode=memory_mode,
            setup_state=setup.state,
            boot=boot,
        ),
        sourceFiles=_source_files(cp, memory_mode, group_full, has_setup=has_provider_surface),
    )


@dataclass(frozen=True)
class _CodeRefs:
    """The code lane of one worktree contract: the official source line and the checkout."""

    source: CommitRefNode
    worktree: CommitRefNode


@dataclass(frozen=True)
class _MemoryRefs:
    """The memory lane of one worktree contract: its two refs plus the ledger that maps them.

    All three are ``None`` unless the contract runs external memory -- an internal or disabled
    contract has no memory lane, so the node leaves those fields unset rather than rendering
    an empty lane as an observation.
    """

    source: CommitRefNode | None
    worktree: CommitRefNode | None
    ledger_path: str | None


def _code_refs(
    *, contract: dict[str, Any], status: dict[str, Any], freshness: dict[str, Any]
) -> _CodeRefs:
    """Compose the code lane's source/worktree refs from the contract + status probe.

    The source ref is the official line the worktree was cut from -- recorded fields, plus how
    far that recorded base now sits behind the source tip. The worktree ref is the checkout
    itself, and reads ``observed`` only where the probe actually saw it on disk.
    """
    code_fresh = _as_dict(freshness.get("code"))
    exists = status.get("code_worktree_exists")
    return _CodeRefs(
        source=CommitRefNode(
            branch=_str_or_none(contract.get("code_source_branch")),
            commit=_str_or_none(contract.get("code_base_commit")),
            path=_str_or_none(contract.get("code_repo_path")),
            behindSource=_int_or_none(code_fresh.get("baseBehindSource")),
            factState="observed" if status else "derived",
        ),
        worktree=CommitRefNode(
            branch=_str_or_none(contract.get("code_work_branch")),
            commit=_str_or_none(contract.get("code_commit"))
            or _str_or_none(contract.get("code_base_commit")),
            path=_str_or_none(contract.get("code_worktree")),
            exists=exists if isinstance(exists, bool) else None,
            dirty=_bool_or_none(status.get("code_worktree_dirty")),
            factState=_ref_fact_state(bool(status), exists),
        ),
    )


def _memory_refs(
    *,
    contract: dict[str, Any],
    status: dict[str, Any],
    freshness: dict[str, Any],
    memory_mode: str,
) -> _MemoryRefs:
    """Compose the memory lane, mirroring the code lane -- or an absent lane when not external."""
    if memory_mode != "external":
        return _MemoryRefs(source=None, worktree=None, ledger_path=None)
    memory_fresh = _as_dict(freshness.get("memory"))
    exists = status.get("memory_worktree_exists")
    return _MemoryRefs(
        source=CommitRefNode(
            branch=_str_or_none(contract.get("memory_source_branch")),
            commit=_str_or_none(contract.get("memory_base_commit")),
            path=_str_or_none(contract.get("memory_repo_path")),
            behindSource=_int_or_none(memory_fresh.get("baseBehindSource")),
            factState="observed" if status else "derived",
        ),
        worktree=CommitRefNode(
            branch=_str_or_none(contract.get("memory_work_branch")),
            commit=_str_or_none(contract.get("memory_content_commit"))
            or _str_or_none(contract.get("memory_base_commit")),
            path=_str_or_none(contract.get("memory_worktree")),
            exists=exists if isinstance(exists, bool) else None,
            dirty=_bool_or_none(status.get("memory_worktree_dirty")),
            factState=_ref_fact_state(bool(status), exists),
        ),
        ledger_path=_str_or_none(contract.get("ledger_path")),
    )


@dataclass(frozen=True)
class _SetupFacts:
    """A worktree group's provider-boot facts, as the process node reports them."""

    state: str | None
    current_phase: str | None
    heartbeat_age_seconds: float | None
    failed_phases: list[str]
    completed_phases: list[str]
    seed_fallback: bool
    retry_args: dict[str, Any] | None


def _setup_facts(setup_node: SetupProgressNode | None, status: dict[str, Any]) -> _SetupFacts:
    """Derive the boot facts, preferring the live setup run over the recorded status block.

    A group with a live ``SetupProgressNode`` has the better witness -- only it carries the
    current phase and the heartbeat age -- so it wins field by field; the status payload's
    ``providers`` block is the fallback, and remains the only source for the completed
    phases, the seed fallback and the retry arguments.
    """
    provider_status = _as_dict(status.get("providers"))
    retry = provider_status.get("retryArgs")
    return _SetupFacts(
        state=setup_node.state if setup_node else _str_or_none(provider_status.get("state")),
        current_phase=setup_node.currentPhase if setup_node else None,
        heartbeat_age_seconds=setup_node.heartbeatAgeSeconds if setup_node else None,
        failed_phases=(
            list(setup_node.failedPhases)
            if setup_node
            else _str_list(provider_status.get("failedPhases"))
        ),
        completed_phases=_str_list(provider_status.get("completedPhases")),
        seed_fallback=bool(provider_status.get("seedFallback")),
        retry_args=retry if isinstance(retry, dict) else None,
    )


def _provider_boot_nodes(
    providers: list[ProviderNode],
    *,
    group_key: str,
    memory_mode: str,
    setup_state: str | None,
) -> list[ProviderBootNode]:
    boot = [
        ProviderBootNode(
            id=provider.id,
            role=provider.role or "code",
            runtimeState=_engine_runtime_state(provider),
        )
        for provider in providers
    ]
    if setup_state in {"running", "blocked"} and not boot:
        return boot
    observed_roles = {node.role for node in boot}
    for role in _expected_provider_roles(memory_mode):
        if role not in observed_roles:
            boot.append(
                ProviderBootNode(
                    id=f"missing-{role}@{group_key}",
                    role=role,
                    runtimeState="missing",
                    factState="missing",
                )
            )
    boot.sort(key=lambda node: (_ROLE_ORDER.get(node.role, 9), node.id))
    return boot


def _expected_provider_roles(memory_mode: str) -> list[str]:
    roles = ["code"]
    if memory_mode == "external":
        roles.append("memory")
    return roles


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_processes.py:478).
def _engine_runtime_state(provider: ProviderNode) -> str:  # pragma: no cover
    """Bucket a worktree engine's runtime state (mirrors the dashboard ``engineState``)."""
    if provider.ok is False or provider.state in _ENGINE_DOWN:
        return "down"
    if provider.indexingState in _ENGINE_INDEXING:
        return "indexing"
    if provider.state == "configured":
        return "configured"
    return "nominal"


def _ref_fact_state(has_status: bool, exists: object) -> ProcessFactState:
    """observed = on disk; derived = recorded but absent; missing = unobservable."""
    if not has_status:
        return "missing"
    if exists is True:
        return "observed"
    if exists is False:
        return "derived"
    return "missing"


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_processes.py:500).
def _process_phase(  # pragma: no cover
    guidance_phase: object, setup_state: str | None, *, behind_official: bool
) -> str:
    base = _GUIDANCE_PHASE.get(str(guidance_phase or ""), "unknown")
    if base != "worktree-started":
        return base
    if behind_official:
        return "sync-needed"
    if setup_state in {"running", "stale"} or setup_state in _SETUP_FAILED:
        return "provider-setup"
    return base


def _process_health(phase: str, setup_state: str | None, failed_phases: list[str]) -> ProcessHealth:
    if failed_phases or setup_state in _SETUP_FAILED:
        return "failed"
    if setup_state == "stale":
        return "stale"
    if setup_state == "running":
        return "running"
    if phase in {"commit-approval-pending", "integration-blocked", "sync-needed"}:
        return "blocked"
    if phase == "completed":
        return "complete"
    return "nominal"


@dataclass(frozen=True)
class _ProcessLanes:
    """The two lanes of one engine process: the mode that decides which of them exist, and each
    lane's worktree ref (the memory one absent unless the contract runs external memory)."""

    memory_mode: str
    code_worktree: CommitRefNode
    memory_worktree: CommitRefNode | None


def _process_edges(
    lanes: _ProcessLanes,
    boot: list[ProviderBootNode],
    setup: _SetupFacts,
    *,
    behind_official: bool,
) -> list[EngineProcessEdge]:
    """The core conduits: worktree materialization, provider seed/clone, and a sync feedback edge."""
    memory_mode = lanes.memory_mode
    code_worktree = lanes.code_worktree
    memory_worktree = lanes.memory_worktree
    setup_state = setup.state
    failed_phases = setup.failed_phases
    edges = [
        EngineProcessEdge(
            id="code-worktree-add",
            fromNode="code-source",
            toNode="code-worktree",
            kind="worktree-add",
            state=_materialize_edge_state(code_worktree),
            label="add code worktree",
        ),
        EngineProcessEdge(
            id="cgc-seed",
            fromNode="code-worktree",
            toNode="cgc-engine",
            kind="cgc-seed",
            state=_seed_edge_state(
                setup_state,
                failed_phases,
                any(node.role == "code" and node.factState != "missing" for node in boot),
                "codegraphcontext",
            ),
            label="CGC seed",
        ),
    ]
    if memory_mode == "external":
        edges.append(
            EngineProcessEdge(
                id="memory-worktree-add",
                fromNode="memory-source",
                toNode="memory-worktree",
                kind="ledger-map",
                state=_materialize_edge_state(memory_worktree),
                label="ledger-map + memory worktree",
            )
        )
        edges.append(
            EngineProcessEdge(
                id="grepai-clone",
                fromNode="memory-worktree",
                toNode="grepai-engine",
                kind="grepai-clone",
                state=_seed_edge_state(
                    setup_state,
                    failed_phases,
                    any(node.role == "memory" and node.factState != "missing" for node in boot),
                    "grepai",
                ),
                label="GrepAI clone",
            )
        )
    if behind_official:
        edges.append(
            EngineProcessEdge(
                id="sync",
                fromNode="official-line",
                toNode="code-worktree",
                kind="sync",
                state="blocked",
                label="official line moved — sync",
            )
        )
    return edges


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_processes.py:613).
def _materialize_edge_state(ref: CommitRefNode | None) -> str:  # pragma: no cover
    if ref is None:
        return "skipped"
    if ref.exists is True:
        return "complete"
    if ref.exists is None:
        return "unknown"
    return "planned"


# Setup states that decide a seed edge on their own, whatever the engine reports.
_DECISIVE_SETUP_EDGE_STATES: dict[str, str] = {
    "running": "running",
    "stale": "stale",
    **dict.fromkeys(_SETUP_FAILED, "failed"),
}


def _seed_edge_state(
    setup_state: str | None, failed_phases: list[str], has_engine: bool, token: str
) -> str:
    """The seed edge's state, strongest evidence first.

    A failed phase naming this seed outranks everything; then the setup run's own state
    where that is decisive; then the engine's presence, which is what turns a finished or
    unobserved setup into a complete edge.
    """
    if any(token in line for line in failed_phases):
        return "failed"
    decisive = _DECISIVE_SETUP_EDGE_STATES.get(setup_state) if setup_state is not None else None
    if decisive is not None:
        return decisive
    if has_engine and (setup_state is None or setup_state in _SETUP_DONE):
        return "complete"
    return "skipped" if setup_state == "skipped" else "planned"


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_processes.py:650).
def _missing_facts(  # pragma: no cover
    *,
    has_status: bool,
    contract: dict[str, Any],
    memory_mode: str,
    setup_state: str | None,
    boot: list[ProviderBootNode],
) -> list[str]:
    missing: list[str] = []
    if not has_status:
        missing.append(
            "worktree existence, dirty, and base freshness not observed (status probe unavailable)"
        )
    if not contract.get("code_base_commit"):
        missing.append("code base commit not recorded in the contract")
    if memory_mode == "external" and not contract.get("ledger_path"):
        missing.append("memory ledger path not recorded")
    if setup_state is None and all(node.factState == "missing" for node in boot):
        missing.append("provider setup not observed for this worktree group")
    missing_roles = [node.role for node in boot if node.factState == "missing"]
    if missing_roles:
        missing.append(
            "provider runtime not observed for this worktree group: "
            + ", ".join(sorted(missing_roles, key=lambda role: _ROLE_ORDER.get(role, 9)))
        )
    return missing


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_processes.py:678).
def _source_files(  # pragma: no cover
    contract: dict[str, Any], memory_mode: str, group_full: str, *, has_setup: bool
) -> list[str]:
    sources = [str(contract.get("contract_path", ""))]
    if contract.get("code_worktree"):
        sources.append(str(contract["code_worktree"]))
    if memory_mode == "external" and contract.get("memory_worktree"):
        sources.append(str(contract["memory_worktree"]))
    if has_setup and group_full:
        sources.append(f"{group_full}/provider-runtime/setup-progress.json")
    return [path for path in sources if path]


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _str_list(value: Any) -> list[str]:
    """A recorded sequence as strings; an absent or empty value reads as no entries."""
    return [str(line) for line in value or []]


# 260731-EFA-L7 R10: verbatim L7 split; unchanged branch, out of this leaf's behavior scope (mcp/src/agents_remember/observer/reducer_impl/_processes.py:704).
def _int_or_none(value: object) -> int | None:  # pragma: no cover
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
