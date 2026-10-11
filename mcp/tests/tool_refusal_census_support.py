"""The L6 refusal-census fixture: every public tool's production failure invocation.

This module is not a test module. It is the *measured population* the durable
refusal-conformance check drives, kept apart from the check so the census is reproducible
from a probe as well as from pytest (`260918-TSIP` `L6`, delivery-shape rule).

**What it measures.** `mcp/tests/test_tool_entry_point_sweep.py` sweeps every public tool with
a *benign* argument set and records the arm each answer falls in. This fixture asks the
complementary question for every tool: driven down a path that cannot succeed, does the tool
answer with a **typed refusal that names what refused, why, and the next action** -- or does it
lose the envelope to a bare exception, or answer ``ok: true`` for an operation it did not do?

**How the population is derived.** The table below is keyed by tool name and the module asserts
it equals `PUBLIC_TOOLS` in both directions, so a new tool cannot arrive uncensused and a stale
entry cannot rot. `EntryPointWorld` (the sweep's hermetic world: a scratch coordination root, a
real code repository, a real external memory repository, real master/leaf task documents and one
real leaf enclosure) supplies everything the invocations address, so every call reaches the
tool's own refusal logic rather than a missing fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.observer.ambient import ambient

REPO = "repo-a"
ADOPT_REPO = "adopt-repo"
MASTER = "m"
LEAF_ID = "L1"
WORKTREE_NAME = "w"

# A master document that exists in the fixture, used where a tool needs a resolvable address
# rather than a refusal; the tools below pass it only to reach a LATER refusal.
TASK_REF: dict[str, str] = {"repository": REPO, "path": f"{MASTER}/task.json"}
LEAF_REF: dict[str, str] = {"repository": REPO, "path": f"{MASTER}/1_leaf.json"}
ABSENT_REF: dict[str, str] = {"repository": REPO, "path": f"{MASTER}/absent-task/task.json"}


def absent_contract(world) -> str:
    """A contract path inside the coordination root that does not exist."""

    return (
        world.coord / "tasks" / REPO / MASTER / "enclosures" / "absent" / "series-contract.md"
    ).as_posix()


def outside_contract(world) -> str:
    """A contract-shaped path OUTSIDE the coordination root, for the confinement refusals."""

    return (world.root / "outside" / "series-contract.md").as_posix()


def failure_invocations(world) -> dict[str, dict[str, Any]]:
    """One invocation per public tool, chosen so the call must refuse.

    Each entry is the *production* call a consumer would make -- the same adapter, the same
    argument model -- not a private function call. Where a tool offers a ``dry_run``, the
    failure path is reached with it set, so the census never mutates the world it measures.
    """

    contract = absent_contract(world)
    outside = outside_contract(world)
    # The knowledge family's unreadable selection: a path inside the coordination root that is
    # not a database. Inside the root so nothing is touched outside the censused boundary even if
    # a handler resolved it before refusing.
    absent_knowledge = (world.coord / "temp" / "knowledge" / "absent-knowledge.db").as_posix()
    return {
        # -- core ---------------------------------------------------------------------
        "server_info": {},
        "context_packet": {"repo_id": "no-such-repo"},
        "read_ar_files": {"repo_id": REPO, "files": [{"path": "not-covered.md"}]},
        "resolve_context": {"repo_id": "no-such-repo"},
        "runtime_install": {"dry_run": True, "install_provider_deps": False},
        # -- structural ---------------------------------------------------------------
        "dispatch_agent": {"task_document_ref": ABSENT_REF, "role": "worker", "brief": "census"},
        # -- memory -------------------------------------------------------------------
        "memory_quality_check": {
            "request": {"mode": "sync", "repo_id": REPO, "contract_path": contract}
        },
        "citation_fix": {"repo_id": REPO, "contract_path": contract, "dry_run": True},
        "route_index_refresh": {"repo_id": REPO, "contract_path": contract, "dry_run": True},
        "memory_init": {"repo_id": REPO, "initialize_git": True, "dry_run": True},
        "memory_baseline_status": {"repo_id": "no-such-repo"},
        # ADOPT_REPO is configured AND has a real memory repository that has never recorded
        # its default-branch authority -- the absence the sweep pins, and the one `L6`
        # repaired (`T34`). The unknown-repo path is a different, still-open defect and is
        # measured by the `require_repo` entry instead.
        "memory_baseline_adopt": {"repo_id": ADOPT_REPO},
        "memory_carryover_plan": {
            "repo_id": REPO,
            "contract_path": contract,
            "source_memory": outside,
            "official_code_ref": "HEAD",
            "source_code_ref": "HEAD",
            "old_base": "HEAD",
        },
        "memory_carryover_apply": {
            "repo_id": REPO,
            "contract_path": contract,
            "source_memory": outside,
            "official_code_ref": "HEAD",
            "source_code_ref": "HEAD",
            "old_base": "HEAD",
            "intent_note": "refusal census",
        },
        # -- worktrees ----------------------------------------------------------------
        "worktree_start": {
            "repo_id": REPO,
            "task_name": f"{MASTER}-absent",
            "worktree_name": WORKTREE_NAME,
        },
        "worktree_attach": {
            "repo_id": REPO,
            "task_name": f"{MASTER}-absent",
            "worktree_name": WORKTREE_NAME,
        },
        "worktree_enclosure_adopt": {
            "contract_path": contract,
            "expected_worktree_group": (world.coord / "worktrees" / "absent").as_posix(),
            "rationale": "refusal census",
            "dry_run": True,
        },
        "worktree_status": {
            "repo_id": REPO,
            "task_name": f"{MASTER}-absent",
            "worktree_name": WORKTREE_NAME,
        },
        "worktree_sync": {"contract_path": contract, "dry_run": True},
        "worktree_pause": {"contract_path": contract},
        "direct_landing": {"contract_path": contract, "code_commit": "deadbeef", "dry_run": True},
        "worktree_closeout_preview": {"contract_path": contract},
        "worktree_closeout_apply": {
            "contract_path": contract,
            "intent_note": "refusal census",
            "dry_run": True,
        },
        "worktree_integrate": {"contract_path": contract, "dry_run": True},
        "worktree_checkpoint_landing": {"contract_path": contract, "dry_run": True},
        "worktree_record_landing": {
            "contract_path": contract,
            "landed_code_commit": "deadbeef",
            "dry_run": True,
        },
        "worktree_operation_control": {
            "contract_path": contract,
            "operation_kind": "closeout",
            "action": "retry",
            "expected_generation": 1,
            "intent_note": "refusal census",
            "dry_run": True,
        },
        "worktree_cleanup": {"contract_path": contract, "dry_run": True},
        "worktree_abandon": {"contract_path": contract, "dry_run": True},
        "task_reopen": {"contract_path": contract, "dry_run": True},
        "lifecycle_finalize_task": {"contract_path": contract, "dry_run": True},
        # -- task documents and queue -------------------------------------------------
        "task_doc": {"repo_id": REPO, "operation": "get", "task_name": f"{MASTER}-absent"},
        "curator_coherence": {"request": {"action": "status", "contract_path": contract}},
        "closeout_queue": {
            "request": {
                "action": "status",
                "sprint_task_document_ref": ABSENT_REF,
                "caller": {"role": "orchestrator", "task_document_ref": ABSENT_REF},
            }
        },
        # MIK-R112 removed the lifecycle-state tools; no lifecycle entries remain.
        # -- role agents ---------------------------------------------------------------
        # Both tools serve only a role agent AR launched. The census server was started by no
        # launch, so each call meets the first refusal the tools have: the caller has no binding
        # (`status: "refused"`, `refusal: "caller-has-no-binding"`, a reason and a next action).
        "role_start": {
            "role": "worker",
            "request_id": "00000000-0000-4000-8000-000000000001",
            "sprint_document_ref": TASK_REF,
            "master_document_ref": TASK_REF,
            "task_document_ref": LEAF_REF,
        },
        "role_message": {
            "text": "refusal census",
            "agent_id": "00000000-0000-4000-8000-000000000002",
        },
        # -- knowledge ----------------------------------------------------------------
        # The three `knowledge_*` operations over a memory tree (MIK-R26 rule 5). Each entry
        # drives the production call with an input that cannot succeed:
        #
        # * `knowledge_read` and `knowledge_integrity_check` name a memory root this world does
        #   not have. The surface answers an absent selection INSIDE its own declared envelope
        #   -- `state: "refused"` with a shipped refusal code and detail -- rather than raising,
        #   which is the property this census exists to check, and the code names the missing
        #   input.
        # * `knowledge_diff` names the same absent memory root, which it refuses as an absent
        #   selection inside its envelope as well.
        #
        # All three answer `ok: true` with `state: "refused"`: the mounted knowledge surface
        # reports a refusal as a STATE of a successful call, which is its own models' declared
        # contract (`models/tools/knowledge_responses.py`: "A refusal is a state, not a partial
        # success"). That shape is named and pinned in the conformance module beside this one
        # (`STATEFUL_REFUSALS`), never tolerated silently through `ALWAYS_ANSWERS`.
        "knowledge_read": {"memoryRoot": absent_knowledge, "view": "source_context"},
        "knowledge_diff": {"memoryRoot": absent_knowledge, "beforeRevision": "HEAD"},
        "knowledge_integrity_check": {
            "memoryRoot": absent_knowledge,
            "codeRoot": world.coord.as_posix(),
        },
    }


def whole_result_for(payload: dict[str, Any]) -> dict[str, Any]:
    """Read back the whole result one answer's ``log`` names (MIK-R108).

    The answer that leaves the server holds what the caller needs next; the
    whole result the function computed lies in the detail file beside the
    daily log. An answer with no readable detail file yields {}.
    """

    log = payload.get("log")
    if not isinstance(log, str) or not log:
        return {}
    try:
        whole = json.loads(Path(log).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return whole if isinstance(whole, dict) else {}


def drive_census(world) -> dict[str, dict[str, Any]]:
    """Invoke one failure path per public tool and read back what arrived."""

    arguments = failure_invocations(world)
    missing = set(PUBLIC_TOOLS) - set(arguments)
    extra = set(arguments) - set(PUBLIC_TOOLS)
    if missing or extra:
        raise AssertionError(
            f"the census table is not the roster: missing={sorted(missing)} extra={sorted(extra)}"
        )

    # The census measures refusals against an active ambient lifecycle (MIK-R112 removed the
    # `lifecycle_start` tool the old arrangement called; the ambient API arranges the same
    # precondition the three navigation-carrying refusals below are measured against).
    current = ambient()
    if current is not None and current.current is None:
        current.start()
    results: dict[str, dict[str, Any]] = {}
    for tool in PUBLIC_TOOLS:
        kind, payload = world.call(tool, arguments[tool])
        answer = payload or {}
        results[tool] = {"kind": kind, "payload": answer, "whole": whole_result_for(answer)}
    return results
