"""The mandatory gate at the leaf and series integration routes.

MIK-R09 rule 4 (a master or checkpoint landing waits for valid, current knowledge) and MIK-R52
rule 3 (a leaf's integration judges its closeout pair once more). Preview and apply prove the
same gate before any ref moves; a refusal carries state ``knowledge-gate-refused`` and names
every finding. The cutover lock stays: unconverted memory is refused by it, never by the gate.
"""

from __future__ import annotations

import subprocess

from agents_remember.worktrees.disabled_memory import opt_out_refusal_result
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.integration.master_review_gate import blocked_integration_payload
from agents_remember.worktrees.knowledge_gate import (
    GateProbeError,
    converted_memory,
    landing_gate_refusal,
    leaf_cutover_refusal,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout_external import leaf_pair_refusal
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.series_closeout import landed_chain_refusal
from agents_remember.worktrees.services import LandingGateRequest
from agents_remember.worktrees.worktree_contract import WorktreeContract, original_base_pair

__all__ = ["integration_gate_block"]


def integration_gate_block(
    contract: WorktreeContract,
    args: WorktreeArgs,  # noqa: ARG001 - route args kept for interface stability; a refusal writes nothing in preview or apply (F02)
    commits: IntegratedCommits,
    sources: IntegrationSources,
    *,
    operation: str,
) -> WorktreeCommandResult | None:
    """MIK-R09 rule 4: a master or checkpoint landing waits for valid, current knowledge.

    The validator runs on the master's memory commit against the parent line's memory tip, and
    every entry at a path the master's net code diff changed must not be stale at its code commit.
    The landing chain proof (MIK-R52 rule 4) follows: the final landing requires every
    non-abandoned canonical leaf landed, while a checkpoint proves the chain over the leaves that
    have landed. A leaf's integration judges its closeout pair once more (MIK-R52 rule 3). Preview
    and apply refuse alike. Unconverted memory is not gated; the cutover lock refuses it instead -- for a
    leaf's integration as well -- once the repository holds converted memory (MIK-R09 rule 6). A
    contract that carries the removed mode never reaches the gate: the route entry refuses it with
    the opt-out refusal first (MIK-R50 R4a), and this function refuses it itself for a direct
    caller (MIK-R50 R4b).
    """

    if contract.memory_mode != "external" or contract.memory_repo_path is None:
        if contract.memory_mode != "external":
            return opt_out_refusal_result(contract, operation)
        return None
    if contract.kind != "series":
        refusal = _leaf_landing_lock(contract, commits, sources)
    else:
        refusal = landing_gate_refusal(
            LandingGateRequest(
                memory_repository=contract.memory_repo_path,
                memory_commit=commits.memory_content,
                memory_bases=(sources.current_memory_source,),
                code_repository=contract.code_repo_path,
                code_commit=commits.code,
                code_base=sources.current_code_source,
                series_contract=contract.contract_path.as_posix(),
                # Only the landing that finishes the master refuses document debt (MIK-R124
                # rule 8); a checkpoint lands and leaves the master open, so it reports.
                master_end=operation != "worktree_checkpoint_landing",
                document_base=original_base_pair(contract),
            )
        )
        if refusal is None:
            # MIK-R52 rule 4: the master's line holds only judged leaf publications and what
            # came from its parent line. The final landing proves the full chain; a checkpoint
            # proves it over the leaves that have landed. Unconverted lines keep the prior
            # behavior (MIK-R46).
            refusal = _series_chain_refusal(
                contract, commits, require_all=operation != "worktree_checkpoint_landing"
            )
    if refusal is None:
        return None
    # A refusal alters neither the contract nor any file (MIK-R52 F02): the payload projects
    # the blocked decision in memory without persisting it, and the pair judgment above never
    # writes the recomputed worklist.
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            "knowledge-gate-refused",
            refusal,
            persist=False,
            developer_decision_required=False,
        ),
    )


def _series_chain_refusal(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    *,
    require_all: bool,
) -> str | None:
    """The landing chain proof for a series integration route (MIK-R52 rule 4).

    Converted lines prove the chain over the landed leaves up to the landing pair; unconverted
    lines keep the prior behavior (MIK-R46), and a line the marker probe cannot read refuses.
    """

    assert contract.memory_repo_path is not None
    try:
        converted = converted_memory(
            contract.memory_repo_path, commits.memory_content, contract.memory_base_commit
        )
    except GateProbeError as error:
        return str(error)
    if not converted:
        return None
    return landed_chain_refusal(
        contract,
        code_tip=commits.code,
        memory_tip=commits.memory_content,
        require_all=require_all,
    )


def _leaf_landing_lock(
    contract: WorktreeContract, commits: IntegratedCommits, sources: IntegrationSources
) -> str | None:
    """The leaf's landing lock: the cutover lock (MIK-R09 rule 6) and the pair judgment (MIK-R52).

    A leaf's memory commit was gated at its closeout, but the landing judges the closeout cell's
    pair once more: a contract edited by hand, or a closeout made by an older build, lands
    unjudged otherwise (MIK-R52 rule 3, settled G1). Converted memory that holds no converted side
    is still refused only by the cutover lock, when neither the landed commit nor the line it
    lands on holds the layout marker and the repository holds converted memory elsewhere.
    """

    if not commits.memory_content:
        return None  # no memory lands
    assert contract.memory_repo_path is not None
    try:
        if converted_memory(
            contract.memory_repo_path, commits.memory_content, sources.current_memory_source
        ):
            return _closeout_pair_refusal(contract, commits, sources)
    except GateProbeError as error:
        return str(error)
    return leaf_cutover_refusal(contract, "the leaf's integration")


def _closeout_pair_refusal(
    contract: WorktreeContract, commits: IntegratedCommits, sources: IntegrationSources
) -> str | None:
    """Judge the closeout cell's pair exactly as the closeout judged its candidate (MIK-R52 rule 3).

    The validator runs as a leaf publication and the gate recomputes the worklist over the two
    commits' trees, with the parent line's memory tip as base. ``None`` when the pair passes; the
    refusal naming every finding when it does not. A base or side Git cannot read refuses as an
    unreadable input.
    """

    base = sources.current_memory_source
    if not base:
        return (
            "the leaf's integration refused: the parent memory line's tip is unknown, so the "
            "closeout pair cannot be judged (MIK-R52 rule 3); sync the leaf so the contract "
            "names its source line, then retry the integration"
        )
    try:
        return leaf_pair_refusal(
            contract,
            code_commit=commits.code,
            memory_commit=commits.memory_content,
            bases=(base,),
            persist_worklist=False,
        )
    except (RuntimeError, subprocess.SubprocessError) as error:
        return str(error)
