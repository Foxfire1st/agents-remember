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

from typing import Any

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

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
    manager = {"role": "manager", "task_document_ref": TASK_REF}
    worker = {"role": "worker", "task_document_ref": LEAF_REF}
    return {
        # -- core ---------------------------------------------------------------------
        "ping": {},
        "server_info": {},
        "context_packet": {"repo_id": "no-such-repo"},
        "read_ar_files": {"repo_id": REPO, "files": [{"path": "not-covered.md"}]},
        "resolve_context": {"repo_id": "no-such-repo"},
        "runtime_install": {"dry_run": True, "install_provider_deps": False},
        "skills_install": {"dry_run": True},
        # -- structural ---------------------------------------------------------------
        "dispatch_agent": {"task_document_ref": ABSENT_REF, "role": "worker", "brief": "census"},
        "retire_child": {"task_document_ref": ABSENT_REF, "role": "worker"},
        "rename_child": {
            "task_document_ref": ABSENT_REF,
            "role": "worker",
            "label": "census",
        },
        "rename_self": {"label": "no-such-seat"},
        # -- memory -------------------------------------------------------------------
        "drift_check": {"repo_id": "no-such-repo"},
        "memory_quality_check": {
            "request": {"mode": "sync", "repo_id": REPO, "contract_path": contract}
        },
        "citation_fix": {"repo_id": REPO, "contract_path": contract, "dry_run": True},
        "citation_migrate": {"repo_id": REPO, "contract_path": contract, "dry_run": True},
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
        # -- providers ----------------------------------------------------------------
        "provider_status": {},
        "provider_diagnostics": {},
        "provider_watchers": {"action": "refresh"},
        "grepai_search": {"query": "refusal census"},
        "grepai_trace": {"symbol": "census", "trace_action": "callers"},
        "cgc_symbol_search": {"repo_id": REPO, "name": "census"},
        "cgc_callers": {"repo_id": REPO, "function": "census"},
        "cgc_callees": {"repo_id": REPO, "function": "census"},
        "cgc_dependencies": {"repo_id": REPO, "module": "census"},
        "cgc_complexity": {"repo_id": REPO},
        "cgc_visualize": {"repo_id": REPO, "dry_run": True},
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
        # The benchmark family is driven with `dry_run: True` only. Its REAL run executes
        # Codex agents in a sandbox (minutes, a provider call) and cannot be part of a
        # hermetic seconds-long census; the dry run is the reachable path here, and it
        # answers in an envelope. That the dry run is not read-only is the register's
        # `T12` finding, owned by this leaf, and is NOT asserted by this module.
        "codex_benchmark_prepare": {"case_id": "no-such-case", "dry_run": True},
        "codex_benchmark_run": {"case_id": "no-such-case", "dry_run": True},
        # -- lifecycle ----------------------------------------------------------------
        # `lifecycle_start` is the one tool whose failure path is "already active": the
        # sweep ends the fixture's lifecycle, and the census starts one first.
        "lifecycle_start": {},
        "lifecycle_resume": {},
        "lifecycle_turn_end_notification": {"summary": "refusal census"},
        "lifecycle_end": {"outcome": "abandoned"},
        "switch_lifecycle": {"on_unsaved": "discard"},
        "lifecycle_phase": {"phase": "build"},
        "lifecycle_gate": {
            "kind": "plan-approval",
            "wait": False,
            "caller": {"role": "worker", "task_document_ref": ABSENT_REF},
        },
        "gate_decide": {
            "task_document_ref": ABSENT_REF,
            "kind": "plan-approval",
            "decision": "approve",
            "caller": manager,
        },
        "gate_list": {"caller": worker},
        # -- messages -----------------------------------------------------------------
        "message_parent": {"ask": "refusal census", "response": "refusal census"},
        "message_child": {
            "task_document_ref": ABSENT_REF,
            "role": "worker",
            "ask": "refusal census",
            "response": "refusal census",
        },
        # -- capsule and skills -------------------------------------------------------
        "role_capsule_compile": {
            "contract_path": contract,
            "task_path": f"{MASTER}/1_leaf.json",
            "role": "worker",
        },
        "skill_catalog_list": {},
        "skill_catalog_read": {"uri": "skill://no-such-skill/SKILL.md"},
        # -- knowledge ----------------------------------------------------------------
        # The five `knowledge_*` families, added to the roster by the merged
        # `260915_knowledge-substrate` line and therefore to this table by `260918-TSIP-L10`
        # (`T94`: 72 tools, not the 67 this census was written against). Each entry drives the
        # production call with an input that cannot succeed:
        #
        # * four of the five address a dataset this world does not have. The surface answers an
        #   unreadable selection INSIDE its own declared envelope -- `state: "refused"` with a
        #   shipped refusal code and detail -- rather than raising, which is the property this
        #   census exists to check, and the code names the missing input.
        # * `knowledge_change` writes nothing by contract, so every kind is refused as
        #   `registration_absent` and no dataset is needed at all.
        # * `knowledge_diff` cannot reach its dataset branch here: a valid comparison body
        #   carries two opened snapshot contexts (`KnowledgeDiffRequest.before/.after`), which
        #   need a real knowledge store this fixture deliberately does not build. Its declared
        #   `invalid_payload` refusal for a body the shipped request model refuses is therefore
        #   the reachable failure path -- a named refusal, not a raise.
        #
        # All five answer `ok: true` with `state: "refused"`: the mounted knowledge surface
        # reports a refusal as a STATE of a successful call, which is its own models' declared
        # contract (`models/tools/knowledge_responses.py`: "A refusal is a state, not a partial
        # success"). That shape is named and pinned in the conformance module beside this one
        # (`STATEFUL_REFUSALS`), never tolerated silently through `ALWAYS_ANSWERS`.
        "knowledge_read": {
            "databasePath": absent_knowledge,
            "repositoryId": REPO,
            "view": "source_context",
        },
        "knowledge_change": {
            "databasePath": absent_knowledge,
            "repositoryId": REPO,
            "recordKind": "evidence_claim",
        },
        "knowledge_diff": {
            "databasePath": absent_knowledge,
            "repositoryId": REPO,
            "beforePath": absent_knowledge,
            "afterPath": absent_knowledge,
        },
        "knowledge_integrity_check": {
            "databasePath": absent_knowledge,
            "repositoryId": REPO,
        },
        "knowledge_project": {
            "databasePath": absent_knowledge,
            "repositoryId": REPO,
            "destinationRoot": (world.coord / "temp" / "knowledge-projection").as_posix(),
            "views": [],
        },
    }


def drive_census(world) -> dict[str, dict[str, Any]]:
    """Invoke one failure path per public tool and read back what arrived.

    ``lifecycle_start`` is arranged last-first: the census begins a lifecycle so that
    ``lifecycle_start``'s own failure path (one is already active) is the one measured, and
    ``lifecycle_resume``'s unprepared state is the one the pin already records.
    """

    arguments = failure_invocations(world)
    missing = set(PUBLIC_TOOLS) - set(arguments)
    extra = set(arguments) - set(PUBLIC_TOOLS)
    if missing or extra:
        raise AssertionError(
            f"the census table is not the roster: missing={sorted(missing)} extra={sorted(extra)}"
        )

    # `EntryPointWorld.build` already ends the fixture's lifecycle, so `lifecycle_start`
    # succeeds there; start one here and let the table's `lifecycle_start` entry meet the
    # "already active" precondition its own docstring documents.
    world.call("lifecycle_start", {})
    results: dict[str, dict[str, Any]] = {}
    for tool in PUBLIC_TOOLS:
        kind, payload = world.call(tool, arguments[tool])
        results[tool] = {"kind": kind, "payload": payload or {}}
    return results
