"""The AR workflow as one ordered path through the tools, walked in a disposable world.

`260918-TSIP-L8`: the workflow statement this leaf owns. A trace that lives in the task root is
not a landing, so the ordered path is re-derived here, by a test run, at every revision:

* the operations run **in order** through the registered entry points -- authoring, `worktree_start`,
  the work, closeout preview and apply, integration preview and apply, cleanup preview, finalize --
  and reach the terminal `cleanup-completed` contract;
* every **guidance block** a response carries is checked against **the caller's own contract**,
  never against whatever lifecycle the process happens to hold (`T54`: in one round every response
  of a leaf named a concurrent session's enclosure);
* every **recommended preview step** is actually **executed** and required to answer (`T62`: the
  preview every closeout doctrine tells a seat to take first raised an internal `RuntimeError`
  whenever a drift snapshot existed for the task's branch).

**Why a walk and not a table.** The properties above are asserted over *populations derived at run
time*: the hops come from executing the chain, the guidance channels come from each response the
chain produced, and the arguments a recommendation supplies are read from the product's own
guidance state machine over the contract on disk. The only literal sequences here are (a)
`WORKFLOW_OPERATIONS`, which *is* the workflow statement this leaf owns and is asserted against
what the walk observed, and (b) value fixtures for arguments a recommendation leaves to the
caller. Nothing is keyed by tool name except the two pins, and each pin is asserted **equal in both
directions** to what the run derived.

**Every response's operational triple is called, not just the one the walk happens to follow**
(`260918-TSIP-L8` fix round 2). The first revision of this module measured `worktree_status`'s
recommendation through the phase state machine only, so the *same* operation being recommended
uncallably by the `worktree_start` response's own `nextTool`/`nextArgs` was invisible to it: the
mutation that repaired that channel left the module green. A recommendation channel a case does not
call is a channel the case cannot see, so the walk now probes **both** producers of the same
recommendation -- each hop's own triple, and the `worktree_start` retry-provider-setup response,
driven in its own disposable world because the start guard needs the session's lifecycle handed back
first -- and the callability pin is asserted over the union.

**Two guidance channels, two authorities** (measured, and the reason the address check has two
arms). A worktree response can carry:

* the **top-level triple** ``nextAction``/``nextTool``/``nextArgs``, declared on
  ``models/worktree.py::WorktreeCommandResponse`` and produced from the response's **own**
  contract by ``worktrees/modules/guidance.py``. It is the *operational* channel: it names the next
  operation of this task, and being producer-derived it cannot name another task.
* ``nextStep``, the *lifecycle* overlay computed at the choke point from the **process-global**
  ambient lifecycle (``application/next_step.py::next_step_for`` reads ``LifecycleState.enclosure``)
  and then filtered by ``application/tool_response.py::bound_next_step``, which withholds any hint
  that does not name the response's own place. It is what carries the turn-end/notify moments.

So guidance is authoritative for navigation only where the address binding holds, and the two
channels must be read as two: a case that inspects only ``nextStep`` reports "withheld" where the
seat in fact received the same operational move top-level.

The module is hermetic: one `TemporaryDirectory`, two real Git repositories, real task documents,
a real server built by ``create_server`` and a real in-memory MCP client session per call. No
Docker, no network, no writes outside the temporary tree.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from agents_remember.application.next_step import next_step_for
from agents_remember.application.provider_runtime import setup_progress_path
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.models.base import NextStep
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.observer.ambient import ambient
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from mcp.shared.memory import create_connected_server_and_client_session

REPO = "repo-a"
MASTER = "workflow-master"
SIBLING_MASTER = "sibling-master"
LEAF_ID = "L1"
SECOND_LEAF_ID = "L2"
WORKTREE_NAME = "w"
SIBLING_WORKTREE = "sibling"
STALE_WORKTREE = "stale"
CODE_CHANGE = "work.txt"

# ---------------------------------------------------------------------------------------------
# The workflow statement, as the ordered operations a seat performs. This is the ORDER the walk
# asserts, not a population of what is checked: every property below is measured over what the
# walk produced. `worktree_integrate` and `lifecycle_finalize_task` appear twice on purpose --
# each is a preview followed by its apply, and the preview is the step `T62` broke.
# ---------------------------------------------------------------------------------------------
WORKFLOW_OPERATIONS: tuple[str, ...] = (
    "worktree_start",
    "worktree_status",
    "worktree_closeout_preview",
    "worktree_closeout_apply",
    "worktree_integrate",
    "worktree_integrate",
    "worktree_cleanup",
    "lifecycle_finalize_task",
    "lifecycle_finalize_task",
)

# The states the chain must reach, in order. `worktree_status` answers with a payload carrying no
# `state`, so it is asserted through the phase guidance reports instead.
EXPECTED_STATES: tuple[tuple[str, str], ...] = (
    ("worktree_start", "started"),
    ("worktree_closeout_preview", "would-closeout"),
    ("worktree_closeout_apply", "closed"),
    ("worktree_integrate", "would-integrate"),
    ("worktree_integrate", "integrated"),
    ("worktree_cleanup", "would-cleanup"),
    ("lifecycle_finalize_task", "would-finalize"),
    ("lifecycle_finalize_task", "finalized"),
)

# The phases the product's own guidance state machine must report along the way.
EXPECTED_PHASES: tuple[str, ...] = (
    "worktree-started",
    "integration-pending",
    "cleanup-pending",
    "cleanup-completed",
)

# ---------------------------------------------------------------------------------------------
# THE TWO PINS. Rule for both, from `test_tool_entry_point_sweep.ENVELOPE_LOSING_RAISERS`: when the
# defect is repaired, remove that entry **in the same change** that lands the repair -- never
# delete the constant, and never widen it to make a new failure pass. Each is asserted EQUAL to
# what the run derived, in both directions, so both a recurrence and a widening are failures.
# ---------------------------------------------------------------------------------------------

# Recommended steps whose guidance names arguments the tool's own registered argument model
# refuses: derived as {tool: the required fields the model named as absent}.
#
# `worktree_status` was the member this leaf found, on THREE producers, and all three are repaired:
# the `worktree-started` phase's step (`worktrees/modules/guidance.py::_pre_integration_phase`,
# round 1) and the two operational triples that recommend the same operation --
# `worktrees/modules/startup/start_result.py` (the successful start response of every leaf) and
# `worktrees/modules/start.py::_retry_provider_setup_result` (round 2). A seat following any of them
# got `1 validation error for worktree_statusArguments: repo_id Field required`, and the workflow's
# SECOND operation was unreachable by the guidance meant to lead to it. Each producer now carries
# `repo_id` from the contract, and this population is asserted over BOTH channels: the walk's own
# as-given calls and every response's operational triple, called verbatim
# (`WorkflowChainObservation._probe_operational_triples`). Round 1 measured the phase channel only,
# which is why the operational-channel repair was invisible to it.
#
# THE REMAINING ENTRY IS REGISTERED, NOT REPAIRED, and it needs a channel-level repair this leaf
# does not own. `worktree_closeout_apply`'s recommendation is the closeout preview's own
# *operational* triple (`models/worktree.py::WorktreeCommandResponse.nextTool/nextArgs`), which
# carries the commit messages but not `intent_note`, which the tool requires. The lifecycle
# overlay's `NextStep` model can declare what it leaves to the caller (``nextRequiredArgs``), and
# the ``closeout-pending`` phase does declare `intent_note` -- but that phase is unreachable from
# the tools' own writers, because `approved_for_commit` is written in the same contract write as
# ``closeout_status: completed`` by the apply it would be recommending (measured in case 5 below).
# The operational triple has no such declaration field at all, so the fix belongs to the tool
# surface: either declare the required arguments on that channel or supply them. Owner: the
# tool-surface owner (`L10`), with this workflow statement (`L8`) carrying the statement.
# Rule: repair it and delete this entry in the same change; never delete the constant.
GUIDANCE_STEPS_MISSING_REQUIRED_ARGS: dict[str, frozenset[str]] = {
    "worktree_closeout_apply": frozenset({"intent_note"}),
}

# Responses that produced lifecycle guidance which `bound_next_step` then WITHHELD, by tool and
# count. The mechanism is `T82`'s: the worktree producers declare their address in snake_case
# (`contract_path`/`enclosure_path`) while the guard reads the envelope's camelCase
# `contractPath`/`enclosurePath`, so such a response cannot vouch for guidance derived from the
# process-global ambient lifecycle and the hint is withheld rather than emitted unchecked. It costs
# this chain nothing HERE because those responses still carry the same operational move in the
# declared top-level triple -- which is exactly why the count is pinned: the day a producer stops
# emitting the top-level triple, the seat loses the move and this dict must change. Owner: the
# guidance surface with this workflow statement.
WITHHELD_GUIDANCE_HOPS: dict[str, int] = {
    "worktree_start": 1,
    "worktree_status": 1,
    "worktree_integrate": 1,
    "worktree_cleanup": 1,
}

# Operations whose refusal for a STALE BASE escapes as a raise instead of a typed refusal, so the
# caller loses `ok`, `status` and every recovery key -- `T34`'s class, on the ordinary state `T87`
# says every leaf that falls behind reaches. Measured: with the recorded base behind its source
# branch, `worktree_closeout_preview` raises
# ``closeout requires current transitive source lineage (source-lineage-stale)`` while the sibling
# operations in the identical state answer typed (`worktree_closeout_apply` closes the task,
# `worktree_integrate` answers ``would-integrate``, `lifecycle_finalize_task` answers
# ``not-finalizable-yet``, `worktree_sync` answers ``would-sync``). The remedy is named in the
# exception TEXT only, so nothing machine-readable reaches the caller.
# Rule: repair it and delete this entry in the same change; never delete the constant, never widen
# it. Owner: the tool surface (`L10`), with this workflow statement carrying the statement.
STALE_BASE_RAISERS: frozenset[str] = frozenset({"worktree_closeout_preview"})

# Values for arguments a recommendation leaves to the caller. Values, never a population: which
# argument names are needed is parsed from the argument model's own refusal, at run time.
FIXTURE_ARGS: dict[str, Any] = {
    "intent_note": "walk the workflow chain",
    "code_commit_message": "Add the workflow chain fixture",
    "memory_commit_message": "Record the workflow chain fixture",
    "dry_run": True,
    "strategy": "ff-only",
}

MISSING_FIELD = re.compile(r"^(?P<field>[a-z_][a-z0-9_]*)\n\s+Field required", re.MULTILINE)
# `validation errors` is PLURAL whenever the model refused more than one field, and a singular-only
# pattern reads that refusal as "the envelope was lost" (`raised`) instead of "the caller's
# arguments were refused". `test_tool_entry_point_sweep.REFUSED_MODEL` carries the singular-only
# form, which is a defect of that instrument rather than of this one -- reported, not edited here.
REFUSED_MODEL = re.compile(r"validation errors? for (?P<model>[A-Za-z_][A-Za-z0-9_]*)")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(path: Path, branch: str = "main") -> None:
    """A Git repository with remote-tracking authority and no remote to fetch from."""

    path.mkdir(parents=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Agents Remember Tests")
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "fixture base")
    commit = _git(path, "rev-parse", "HEAD")
    _git(path, "update-ref", f"refs/remotes/origin/{branch}", commit)
    _git(path, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")


def classify_call(tool: str, kind: str, payload: dict[str, Any] | None) -> str:
    """Whether a call answered, was refused by its OWN argument model, or lost its envelope.

    The two failure arms are separated by MODEL IDENTITY, never by wording: the generated
    ``<tool>Arguments`` model refusing the call is the caller's error (an unusable
    recommendation), while any other raise lost the response envelope entirely.
    """

    if kind == "RETURNED":
        return "answered"
    detail = str((payload or {}).get("detail", ""))
    refused = REFUSED_MODEL.search(detail)
    if refused is not None and refused.group("model") == f"{tool}Arguments":
        return "argument-refused"
    return "raised"


def missing_required_args(kind: str, payload: dict[str, Any] | None) -> frozenset[str]:
    """The fields a tool's own argument model named as required and absent."""

    if kind == "RETURNED":
        return frozenset()
    return frozenset(
        match.group("field")
        for match in MISSING_FIELD.finditer(str((payload or {}).get("detail", "")))
    )


def _names_same_place(observed: str, expected: Path) -> bool:
    """Whether a path names the expected contract file, or the directory that holds it."""

    return Path(observed).resolve() in {expected, expected.parent}


@dataclass
class Hop:
    """One operation, the arguments its own guidance supplied, and what came back."""

    tool: str
    source: str
    as_given_args: dict[str, Any]
    as_given_arm: str
    missing_args: frozenset[str]
    kind: str
    payload: dict[str, Any]
    phase: str
    raw_next: NextStep | None
    emitted_next: NextStep | None

    @property
    def state(self) -> str | None:
        return self.payload.get("state") or self.payload.get("status")

    @property
    def top_next_tool(self) -> str | None:
        value = self.payload.get("nextTool")
        return value if isinstance(value, str) else None

    @property
    def top_next_args(self) -> dict[str, Any]:
        """The arguments this response's own operational triple publishes, verbatim."""

        value = self.payload.get("nextArgs")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def withheld(self) -> bool:
        return self.raw_next is not None and self.emitted_next is None

    @property
    def channels(self) -> list[tuple[str, dict[str, Any]]]:
        """Every guidance channel this response carries that names artifacts, for the address check."""

        found: list[tuple[str, dict[str, Any]]] = []
        if self.emitted_next is not None and isinstance(self.emitted_next.nextArgs, dict):
            found.append(("nextStep", self.emitted_next.nextArgs))
        top = self.payload.get("nextArgs")
        if isinstance(top, dict):
            found.append(("top-level", top))
        return found


@dataclass
class RecommendedCall:
    """One response's own operational triple, and what calling it exactly as published produced.

    `source` names the response that published the recommendation, so the coverage case can assert
    that *every* response carrying a triple was called rather than that a call was made somewhere.
    `arm` separates an unusable recommendation (the tool's own argument model refused it) from a
    call that lost its envelope, and `missing` is the fields that model named.
    """

    source: str
    tool: str
    args: dict[str, Any]
    arm: str
    missing: frozenset[str]


class WorkflowWorld:
    """One disposable coordination root, one registered server, one session per call."""

    def __init__(self) -> None:
        declare_test_process()
        self._temporary = tempfile.TemporaryDirectory(prefix="ar-workflow-chain-")
        self.root = Path(self._temporary.name)
        self.coord = self.root / "coord"
        self.coord.mkdir(parents=True)
        self.code = self.coord / "repo"
        _init_repo(self.code)
        self.memory = self.coord / "memory-repos" / f"ar-{REPO}"
        _init_repo(self.memory)
        for name in ("onboarding", "docs"):
            (self.memory / name).mkdir()
            (self.memory / name / ".gitkeep").write_text("", encoding="utf-8")
        (self.memory / "system").mkdir()
        (self.memory / "system" / "settings.json").write_text(
            json.dumps({"version": 1, "onboarding": {"storage": {"mode": "memory-repo"}}}),
            encoding="utf-8",
        )
        _git(self.memory, "add", "-A")
        _git(self.memory, "commit", "-m", "memory scaffold")
        # `load_config` resolves a configured repository as `<workspaceRoot>/<repo_id>` and ignores
        # the settings file's `path` key (`kernel/primitives/runtime_config.py::_parse_repository_entry`);
        # that is the convention the mutation boundaries re-read afterwards. Without this link the
        # world is refused with `configured-contract-authority-invalid` before closeout.
        (self.root / REPO).symlink_to(self.code, target_is_directory=True)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.settings = self.root / "mcp" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coord.as_posix(),
                    "workspaceRoot": self.root.as_posix(),
                    "harnessSkillRoot": self.skills.as_posix(),
                    "repositories": {REPO: {}},
                    "providers": {},
                }
            ),
            encoding="utf-8",
        )
        self.server = create_server(
            McpRuntimeConfig(
                config_path=self.settings,
                coordination_root=self.coord,
                workspace_root=self.root,
                transcript_root=self.coord / "logs" / "mcp",
                harness_skill_root=self.skills,
                repositories={
                    REPO: RepositoryScope(repo_id=REPO, path=self.code, memory_root=self.memory)
                },
            )
        )
        self.enclosure = self.coord / "tasks" / REPO / MASTER / "enclosures" / LEAF_ID.lower()
        self.contract = self.enclosure / "series-contract.md"

    # -- the production entry point ----------------------------------------------------------

    async def _call(self, name: str, args: dict[str, Any]):
        async with create_connected_server_and_client_session(self.server._mcp_server) as client:
            return await client.call_tool(name, args)

    def call(self, name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        """Invoke one registered tool exactly as a consumer does, and read back what arrived."""

        try:
            result = anyio.run(self._call, name, args)
        except BaseException as exc:  # "did it raise" IS the arm under test
            return f"RAISED {type(exc).__name__}", None
        if result.isError:
            text = "\n".join(
                text for content in result.content if (text := getattr(content, "text", None))
            )
            return "RAISED ToolError", {"detail": text}
        return "RETURNED", result.structuredContent

    # -- the fixture --------------------------------------------------------------------------

    def author_documents(self) -> None:
        """Author the master and two leaves whose single step is already done."""

        created = self.call(
            "task_doc",
            {
                "repo_id": REPO,
                "operation": "create",
                "task_name": MASTER,
                "fields": {
                    "id": "M",
                    "slug": "task",
                    "title": "Workflow fixture master",
                    "kind": "master",
                    "repo": REPO,
                    "type": "test",
                    "createdAt": "2026-09-18T12:00:00+02:00",
                    "objective": "Give the workflow walk real documents to address.",
                    "requirements": ["The chain runs in order."],
                    "subTasks": [
                        {
                            "number": LEAF_ID,
                            "name": "workflow leaf",
                            "file": "1_leaf.md",
                            "status": "planning",
                            "scope": "walk",
                        },
                        {
                            "number": SECOND_LEAF_ID,
                            "name": "sibling leaf",
                            "file": "2_leaf.md",
                            "status": "planning",
                            "scope": "walk",
                        },
                    ],
                    "sections": [{"heading": "Why", "body": "Workflow chain walk."}],
                },
            },
        )
        assert created[1] is not None and created[1].get("ok"), created
        for leaf, slug in ((LEAF_ID, "1_leaf"), (SECOND_LEAF_ID, "2_leaf")):
            authored = self.call(
                "task_doc",
                {
                    "repo_id": REPO,
                    "operation": "create",
                    "task_name": MASTER,
                    "slug": slug,
                    "fields": {
                        "id": leaf,
                        "slug": slug,
                        "title": f"Workflow fixture leaf {leaf}",
                        "kind": "subTask",
                        "repo": REPO,
                        "type": "test",
                        "createdAt": "2026-09-18T12:00:00+02:00",
                        "objective": "Give the workflow walk a real enclosure to address.",
                        "requirements": ["One enclosure exists."],
                        "steps": [{"id": "S1", "title": "walk", "status": "done"}],
                    },
                },
            )
            assert authored[1] is not None and authored[1].get("ok"), authored

    def start_leaf(
        self, leaf_id: str, worktree_name: str, task_name: str = MASTER
    ) -> tuple[str, dict[str, Any] | None]:
        """The declared start arguments; the first hop measures them as given."""

        return self.call(
            "worktree_start",
            {
                "repo_id": REPO,
                "task_name": task_name,
                "worktree_name": worktree_name,
                "leaf_id": leaf_id,
                "parent_task": task_name,
                "workflow_kind": "light-task",
                "skip_provider_setup": True,
            },
        )

    def author_sibling_master(self) -> None:
        """A second master, its leaf, and its enclosure: a real concurrent seat's task."""

        created = self.call(
            "task_doc",
            {
                "repo_id": REPO,
                "operation": "create",
                "task_name": SIBLING_MASTER,
                "fields": {
                    "id": "S",
                    "slug": "task",
                    "title": "Sibling master",
                    "kind": "master",
                    "repo": REPO,
                    "type": "test",
                    "createdAt": "2026-09-18T12:00:00+02:00",
                    "objective": "Give the walk a concurrent seat to be misdirected by.",
                    "requirements": ["One other enclosure exists."],
                    "subTasks": [
                        {
                            "number": LEAF_ID,
                            "name": "sibling leaf",
                            "file": "1_leaf.md",
                            "status": "planning",
                            "scope": "walk",
                        }
                    ],
                    "sections": [{"heading": "Why", "body": "Sibling fixture."}],
                },
            },
        )
        assert created[1] is not None and created[1].get("ok"), created
        authored = self.call(
            "task_doc",
            {
                "repo_id": REPO,
                "operation": "create",
                "task_name": SIBLING_MASTER,
                "slug": "1_leaf",
                "fields": {
                    "id": LEAF_ID,
                    "slug": "1_leaf",
                    "title": "Sibling leaf",
                    "kind": "subTask",
                    "repo": REPO,
                    "type": "test",
                    "createdAt": "2026-09-18T12:00:00+02:00",
                    "objective": "One enclosure exists.",
                    "requirements": ["One enclosure exists."],
                    "steps": [{"id": "S1", "title": "walk", "status": "done"}],
                },
            },
        )
        assert authored[1] is not None and authored[1].get("ok"), authored

    def close(self) -> None:
        self._temporary.cleanup()

    def fact_args(self, contract_path: str) -> dict[str, Any]:
        """Values this world can supply for arguments a recommendation leaves to the caller."""

        return {
            "repo_id": REPO,
            "contract_path": contract_path,
            "enclosure_path": contract_path,
            "task_name": MASTER,
            "leaf_id": LEAF_ID,
            "worktree_name": WORKTREE_NAME,
        }


class WorkflowChainObservation:
    """The single walk of the chain, the hops it produced, and the sibling arrangement."""

    def __init__(self) -> None:
        self.world = WorkflowWorld()
        self.hops: list[Hop] = []
        self.drift_snapshot: Path | None = None
        self.drift_snapshot_present_at_preview = False
        self.drift_snapshot_preserved_by_previews = False
        self.sibling_contract: Path | None = None
        self.sibling_hop: Hop | None = None
        self.terminal_cleanup = ""
        self.worktree_status_retry: dict[str, Any] = {}
        self.stale_base_calls: list[Hop] = []
        self.stale_sync_state = ""
        # Every operational triple any response published, and what calling it produced.
        self.operational_triples: list[RecommendedCall] = []
        # The phase guidance read at the one moment the phase machine declares a requirement the
        # walk follows, and the contract as the real closeout apply leaves it (`T109`).
        self.cleanup_pending_guidance: dict[str, Any] = {}
        self.contract_after_closeout: WorktreeContract | None = None
        self.retry_provider_setup_state = ""
        self._walk()

    # -- the walk -----------------------------------------------------------------------------

    def _phase_guidance(self) -> dict[str, Any]:
        """The product's own recommendation for the contract on disk, or an empty front half."""

        if not self.world.contract.exists():
            return {}
        return dict(lifecycle_guidance(load_contract(self.world.contract)))

    def _apply_half(self, tool: str) -> bool:
        """Whether this hop is the second half of an already-taken preview.

        The declared half-pairs are `(worktree_integrate, lifecycle_finalize_task)`; the closeout
        pair is two distinct tools and needs no flag. The population is derived from
        `WORKFLOW_OPERATIONS` -- a tool named twice is a preview followed by its apply.
        """

        return WORKFLOW_OPERATIONS.count(tool) > 1 and any(hop.tool == tool for hop in self.hops)

    def _recommendation(self, tool: str, source: str) -> tuple[str, dict[str, Any], list[str]]:
        """What the guidance supplies for `tool`, and the arguments it declares as required.

        `source` names the channel a seat follows for this hop, and the two differ on purpose:
        the phase state machine (`worktrees/modules/guidance.py::lifecycle_guidance`) is the
        recommendation for a *state-driven* move, while the previous response's own operational
        triple is the recommendation for the *apply half* of a preview -- it is the response that
        carries `dry_run: false`, and the phase (still "pending") cannot know the seat already
        previewed. When neither names the operation, it is this workflow statement's declared step.

        The arguments the phase guidance declares as required (`nextRequiredArgs`) are always
        supplied first: a declared requirement is not an omission, and counting it as one would
        make this instrument report a defect where the product says exactly what to pass.
        """

        guidance = self._phase_guidance()
        declared_required = (
            list(guidance.get("nextRequiredArgs") or []) if guidance.get("nextTool") == tool else []
        )
        if source == "phase-guidance" and guidance.get("nextTool") == tool:
            args = dict(guidance.get("nextArgs") or {})
            if tool in ("worktree_closeout_apply", "worktree_integrate", "lifecycle_finalize_task"):
                for name in ("code_commit_message", "memory_commit_message"):
                    args.setdefault(name, FIXTURE_ARGS[name])
            return "phase-guidance", args, declared_required
        if source == "response-guidance" and self.hops:
            previous = self.hops[-1].payload
            if previous.get("nextTool") == tool and isinstance(previous.get("nextArgs"), dict):
                return "response-guidance", dict(previous["nextArgs"]), declared_required
        return "declared", {}, declared_required

    def _declared_args(self, contract_path: str, dry_run: bool | None) -> dict[str, Any]:
        """The arguments this workflow statement's own step supplies for an operation.

        `dry_run` is explicit rather than derived: a preview hop that silently lost it would run
        the real operation inside this fixture, which is exactly the class of mistake (`T62`'s
        preview read as its own apply) this module exists to keep visible.
        """

        as_given = dict(self.world.fact_args(contract_path))
        as_given.update(
            {
                "code_commit_message": FIXTURE_ARGS["code_commit_message"],
                "memory_commit_message": FIXTURE_ARGS["memory_commit_message"],
                "intent_note": FIXTURE_ARGS["intent_note"],
            }
        )
        if dry_run is not None:
            as_given["dry_run"] = dry_run
        return as_given

    def _arguments(
        self, tool: str, source: str, dry_run: bool | None, contract_path: str
    ) -> tuple[str, dict[str, Any]]:
        """What the chosen channel supplies for this hop, with its declared requirements filled."""

        if source == "declared":
            return "declared", self._declared_args(contract_path, dry_run)
        channel, as_given, required = self._recommendation(tool, source)
        for name in required:
            as_given.setdefault(name, FIXTURE_ARGS.get(name, f"walk-{name}"))
        if channel == "declared" or not as_given:
            return "declared", self._declared_args(contract_path, dry_run)
        return channel, as_given

    def _as_given_call(
        self, tool: str, as_given: dict[str, Any], contract_path: str
    ) -> tuple[str, dict[str, Any] | None, str, frozenset[str]]:
        """The first call, exactly as the guidance gave it, and what its own model said of it."""

        kind, payload = self.world.call(tool, as_given)
        arm = classify_call(tool, kind, payload)
        missing = missing_required_args(kind, payload)
        if arm == "answered":
            return kind, payload, arm, missing
        facts = self.world.fact_args(contract_path)
        retry = dict(as_given)
        for name in missing:
            if name in facts:
                retry[name] = facts[name]
            elif name in FIXTURE_ARGS:
                retry[name] = FIXTURE_ARGS[name]
        if tool == "worktree_status":
            self.worktree_status_retry = dict(retry)
        kind, payload = self.world.call(tool, retry)
        return kind, payload, arm, missing

    def _record(
        self,
        tool: str,
        source: str = "phase-guidance",
        *,
        dry_run: bool | None = None,
        contract_path: str | None = None,
        record_in_walk: bool = True,
    ) -> Hop:
        """Execute one recommended step as given, then again with only the arguments it omitted.

        **This is the measurement that matters.** The first call uses exactly what the guidance
        supplied (plus the arguments that guidance itself declares as required). If the tool's own
        argument model refuses it, the recommendation is not followable, and the run records the
        field names the model named. The second call supplies those from the world's own facts so
        the chain can continue -- a check that stopped at the refusal could not tell an unusable
        recommendation from a broken chain.
        """

        target = contract_path or self.world.contract.as_posix()
        source, as_given = self._arguments(tool, source, dry_run, target)
        kind, payload, arm, missing = self._as_given_call(tool, as_given, target)
        payload = payload or {}
        current = ambient()
        raw = next_step_for(current, tool) if current is not None else None
        emitted = payload.get("nextStep")
        contract = load_contract(Path(target))
        hop = Hop(
            tool=tool,
            source=source,
            as_given_args=as_given,
            as_given_arm=arm,
            missing_args=missing if arm != "answered" else frozenset(),
            kind=kind,
            payload=payload,
            phase=str(lifecycle_guidance(contract).get("phase", "")),
            raw_next=raw,
            emitted_next=NextStep.model_validate(emitted) if isinstance(emitted, dict) else None,
        )
        if record_in_walk:
            self.hops.append(hop)
        return hop

    def _walk(self) -> None:
        self.world.author_documents()
        started = self._record("worktree_start")
        assert started.kind == "RETURNED", started

        # The work itself: one real change inside the code worktree.
        (load_contract(self.world.contract).code_worktree / CODE_CHANGE).write_text(
            "work\n", encoding="utf-8"
        )

        self._record("worktree_status", "phase-guidance")
        # The seat's own decision: closeout is previewed when its work is done, which no phase
        # recommendation can know. Every operation after it is driven by the response before it.
        self._record("worktree_closeout_preview", "declared")
        self._record("worktree_closeout_apply", "response-guidance", dry_run=False)
        # The contract as the real apply leaves it: `approved_for_commit` and `closeout_status`
        # arrive together, which is the measured half of `T109`'s unreachability claim.
        self.contract_after_closeout = load_contract(self.world.contract)
        self._record("worktree_integrate", "phase-guidance", dry_run=True)
        self._record("worktree_integrate", "response-guidance", dry_run=False)
        # The one walk moment at which the phase machine's own step declares a requirement
        # (`cleanup-pending` -> `lifecycle_finalize_task`, `nextRequiredArgs: ["contract_path"]`).
        self.cleanup_pending_guidance = self._phase_guidance()

        # `T62`'s trigger: a drift snapshot only exists once something has written one, which is
        # why three earlier leaves' previews ran clean. It is planted at the producer's own path
        # (`worktrees/modules/cleanup.py::remove_drift_snapshot` -> `contract.code_worktree.name` /
        # `contract.code_work_branch`) so the preview's drift-snapshot collection actually reads it.
        closed = load_contract(self.world.contract)
        self.drift_snapshot = drift_snapshot_path(
            self.world.coord,
            repository=closed.code_worktree.name,
            branch=closed.code_work_branch,
        )
        self.drift_snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.drift_snapshot.write_text('{"schema": "ar-drift-snapshot/v1"}\n', encoding="utf-8")

        self._record("worktree_cleanup", "declared", dry_run=True)
        self.drift_snapshot_present_at_preview = self.drift_snapshot.exists()

        self._record("lifecycle_finalize_task", "declared", dry_run=True)
        # Read here, between the last preview and the real finalize: the real call is supposed to
        # reclaim the snapshot, so the preservation claim belongs to the previews alone.
        self.drift_snapshot_preserved_by_previews = bool(
            self.drift_snapshot is not None and self.drift_snapshot.exists()
        )
        self._record("lifecycle_finalize_task", "declared", dry_run=False)
        self.terminal_cleanup = load_contract(self.world.contract).cleanup

        self._sibling_arrangement()
        self._retry_provider_setup_arrangement()
        self._stale_base_arrangement()
        self._probe_operational_triples()

    def _sibling_arrangement(self) -> None:
        """A concurrent sibling's lifecycle holds the process, then this leaf's task is addressed.

        `T54` was systematic: guidance comes from the process-global ambient lifecycle, so while
        another session's enclosure is promoted, every response of this task carries a hint derived
        from that other task. The arrangement is asserted before the property, so the case cannot
        pass by having tested nothing: the unguarded guidance MUST name the sibling.
        """

        # A sibling start is refused while this session's own lifecycle is bound elsewhere
        # (`T90`: the only thing that blocks a start is the calling session's own binding), so the
        # walk hands its lifecycle back before the sibling takes one -- which is exactly the live
        # shape: two seats, two lifecycles, one process-global ambient slot.
        self.world.author_sibling_master()
        ended = self.world.call("lifecycle_end", {"outcome": "completed"})
        assert ended[1] is not None and ended[1].get("ok"), ended
        kind, payload = self.world.start_leaf(LEAF_ID, SIBLING_WORKTREE, task_name=SIBLING_MASTER)
        assert kind == "RETURNED" and payload is not None, (kind, payload)
        assert payload.get("state") == "started", payload
        self.sibling_contract = (
            self.world.coord
            / "tasks"
            / REPO
            / SIBLING_MASTER
            / "enclosures"
            / LEAF_ID.lower()
            / "series-contract.md"
        )
        assert self.sibling_contract.exists(), "the sibling enclosure is missing"

        tool = "lifecycle_finalize_task"
        args = {"contract_path": self.world.contract.as_posix(), "dry_run": True}
        kind, payload = self.world.call(tool, args)
        payload = payload or {}
        current = ambient()
        raw = next_step_for(current, tool) if current is not None else None
        emitted = payload.get("nextStep")
        self.sibling_hop = Hop(
            tool=tool,
            source="declared",
            as_given_args=args,
            as_given_arm=classify_call(tool, kind, payload),
            missing_args=frozenset(),
            kind=kind,
            payload=payload,
            phase="",
            raw_next=raw,
            emitted_next=NextStep.model_validate(emitted) if isinstance(emitted, dict) else None,
        )

    def _stale_base_arrangement(self) -> None:
        """`T87`: a leaf whose recorded base is behind its source branch, driven through the tools.

        The arrangement is measured, not described: the sibling enclosure the previous arrangement
        opened is the second leaf, its source branch then advances the way a sibling's landing
        advances it -- and `worktree_sync` must report `would-sync`, or the arm tested nothing. The
        closing operations are then driven in exactly that state.
        """

        stale_contract = self.sibling_contract
        assert stale_contract is not None
        contract = load_contract(stale_contract)
        (contract.code_worktree / CODE_CHANGE).write_text("work\n", encoding="utf-8")

        source = contract.code_source_branch
        _git(self.world.code, "checkout", source)
        (self.world.code / "sibling-landing.txt").write_text("landed\n", encoding="utf-8")
        _git(self.world.code, "add", "-A")
        _git(self.world.code, "commit", "-m", "a sibling lands on the source branch")
        advanced = _git(self.world.code, "rev-parse", "HEAD")
        _git(self.world.code, "update-ref", f"refs/remotes/origin/{source}", advanced)

        for tool, dry_run in (
            ("worktree_closeout_preview", None),
            ("worktree_sync", True),
        ):
            hop = self._record(
                tool,
                "declared",
                dry_run=dry_run,
                contract_path=stale_contract.as_posix(),
                record_in_walk=False,
            )
            self.stale_base_calls.append(hop)
            if tool == "worktree_sync":
                self.stale_sync_state = str(hop.state or "")

    # -- the operational channel, called rather than read ---------------------------------------

    def _call_recommendation(self, source: str, tool: str, args: dict[str, Any]) -> RecommendedCall:
        """Call one response's operational triple exactly as it was published, and classify it."""

        kind, payload = self.world.call(tool, dict(args))
        payload = payload or {}
        arm = classify_call(tool, kind, payload)
        return RecommendedCall(
            source=source,
            tool=tool,
            args=dict(args),
            arm=arm,
            missing=missing_required_args(kind, payload) if arm != "answered" else frozenset(),
        )

    def _probe_operational_triples(self) -> None:
        """Call every hop's own `nextTool` with its own `nextArgs`, verbatim.

        `worktree_status` is recommended by two different producers, and this module used to
        measure only the phase state machine's channel: the `worktree_start` response's own triple
        was never called, so a repair to it (or its absence) was invisible. Every response that
        publishes a triple is called here, exactly as published, and the callability pin is
        asserted over the union of these calls and the walk's own as-given calls.
        """

        for index, hop in enumerate(self.hops):
            tool = hop.top_next_tool
            if tool is None:
                continue
            self.operational_triples.append(
                self._call_recommendation(f"hop {index} {hop.tool}", tool, hop.top_next_args)
            )

    def _retry_provider_setup_arrangement(self) -> None:
        """The SECOND producer of the same recommendation: `_retry_provider_setup_result`.

        `worktree_start(retry_provider_setup=True)` answers `blocked` with "poll worktree_status
        instead of retrying" and its own operational triple. It is not a hop of the walk, so it is
        driven here in the *same* disposable world against the sibling's still-live contract -- the
        coordination root has to be the one the runtime is configured with (a second world's
        contract path is refused as outside it: `contract_path must stay inside coordination_root`),
        the producer's `setup_running` arm needs a contract that is not yet closed, and this leaf's
        own contract is already terminal by the time the arrangements run. It happens **before** the
        stale-base arrangement advances the source branch, because the producer's retry path is
        reached only while the leaf's lineage is current.

        The lifecycle is handed back first because `worktree_start` refuses any start while this
        session's lifecycle is bound (`T90`: the only thing that blocks a start is the calling
        session's own binding). Nothing after this arrangement needs an active lifecycle.
        """

        stale_contract = self.sibling_contract
        assert stale_contract is not None
        world = self.world
        ended = world.call("lifecycle_end", {"outcome": "completed"})
        assert ended[1] is not None and ended[1].get("ok"), ended

        contract = load_contract(stale_contract)
        progress = setup_progress_path(contract.worktree_group)
        progress.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).isoformat()
        progress.write_text(
            json.dumps(
                {
                    "schema": "ar-provider-setup-progress/v1",
                    "repoName": contract.repo_name,
                    "taskName": contract.task_name,
                    "worktreeGroup": contract.worktree_group.as_posix(),
                    "state": "running",
                    "startedAt": now,
                    "updatedAt": now,
                    "currentPhase": None,
                    "completedPhases": [],
                    "seedFallback": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            kind, payload = world.call(
                "worktree_start",
                {
                    "repo_id": REPO,
                    "task_name": SIBLING_MASTER,
                    "worktree_name": SIBLING_WORKTREE,
                    "leaf_id": LEAF_ID,
                    "parent_task": SIBLING_MASTER,
                    "workflow_kind": "light-task",
                    "retry_provider_setup": True,
                    "skip_provider_setup": True,
                },
            )
        finally:
            progress.unlink(missing_ok=True)
        payload = payload or {}
        self.retry_provider_setup_state = str(payload.get("state") or kind)
        tool = payload.get("nextTool")
        if isinstance(tool, str):
            raw = payload.get("nextArgs")
            self.operational_triples.append(
                self._call_recommendation(
                    "worktree_start retry-provider-setup",
                    tool,
                    dict(raw) if isinstance(raw, dict) else {},
                )
            )

    # -- the populations a case asserts over ----------------------------------------------------

    def callability_population(self) -> dict[str, frozenset[str]]:
        """Every recommended call this run made, by tool, with the required fields it omitted.

        The union of two populations: what each hop's *own* guidance supplied when the walk called
        it, and what each response's *operational triple* supplied when the probe called it. Both
        are calls a seat could make, so both belong to the pin.
        """

        derived: dict[str, frozenset[str]] = {}
        for hop in self.hops:
            if hop.missing_args:
                derived[hop.tool] = derived.get(hop.tool, frozenset()) | hop.missing_args
        for call in self.operational_triples:
            if call.missing:
                derived[call.tool] = derived.get(call.tool, frozenset()) | call.missing
        return derived

    def close(self) -> None:
        """End the ambient lifecycle this walk promoted, then remove the whole world."""

        current = ambient()
        if current is not None and current.current is not None:
            self.world.call("lifecycle_end", {"outcome": "completed"})
        self.world.close()


class WorkflowChainTests(unittest.TestCase):
    """The ordered chain, its guidance, and its previews, over one shared disposable world."""

    observation: WorkflowChainObservation

    @classmethod
    def setUpClass(cls) -> None:
        cls.observation = WorkflowChainObservation()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.observation.close()

    # -- 1. the order -------------------------------------------------------------------------

    def test_the_chain_runs_in_the_declared_order_through_the_registered_entry_points(self) -> None:
        """The workflow statement, asserted against what the run actually did.

        The population is the walk's own hops, so an operation that silently stops happening
        shortens this tuple and fails here rather than passing quietly.
        """

        observed = tuple(hop.tool for hop in self.observation.hops)
        self.assertEqual(observed, WORKFLOW_OPERATIONS)
        for hop in self.observation.hops:
            self.assertIn(hop.tool, PUBLIC_TOOLS, f"{hop.tool} is not on the advertised roster")
            self.assertEqual(
                classify_call(hop.tool, hop.kind, hop.payload),
                "answered",
                f"{hop.tool} lost its envelope: {str(hop.payload.get('detail'))[:200]}",
            )

    def test_the_chain_reaches_every_declared_state_and_the_terminal_contract(self) -> None:
        """Each operation's own vocabulary, in order, and the terminal cleanup cell."""

        seen = [(hop.tool, hop.state) for hop in self.observation.hops]
        for tool, state in EXPECTED_STATES:
            self.assertIn((tool, state), seen, f"{tool} never reached {state!r}: {seen}")
        self.assertEqual(self.observation.terminal_cleanup, "completed")
        # The phase progression comes from the product's state machine over the real contract file,
        # never from a literal in this module.
        phases = [hop.phase for hop in self.observation.hops]
        for phase in EXPECTED_PHASES:
            self.assertIn(phase, phases, f"the walk never reported phase {phase!r}: {phases}")
        self.assertEqual(phases[0], "worktree-started", phases)
        self.assertEqual(phases[-1], "cleanup-completed", phases)

    def test_each_hop_was_the_move_the_previous_response_named(self) -> None:
        """Order derived from the guidance, not merely asserted beside it.

        Where a response carries an operational `nextTool` that is a forward move of the chain, the
        next hop taken must be exactly that tool. This is what makes the statement a *chain*: the
        product's own guidance, not this module's literal, decides what comes next.
        """

        hops = self.observation.hops
        forward = 0
        holds = 0
        for index, previous in enumerate(hops):
            named = previous.top_next_tool
            if named is None:
                continue
            if named == previous.tool:
                # The phase has not advanced, so the guidance points at the same operation again
                # ("keep working here"). That is a hold, not a move, and it is counted as one.
                holds += 1
                continue
            following = hops[index + 1] if index + 1 < len(hops) else None
            if following is not None and following.tool == named:
                forward += 1
                continue
            # A named move may be preceded by a step the workflow itself interleaves (the cleanup
            # preview between integration and finalize), but it may never be skipped or replaced.
            self.assertIn(
                named,
                [hop.tool for hop in hops[index + 1 :]],
                f"{previous.tool} named {named}, and the walk never took it",
            )
        self.assertGreaterEqual(
            forward, 4, "the operational channel named the following hop too rarely to be a chain"
        )
        self.assertGreaterEqual(holds, 1, "no phase ever held the walk at one operation")

    # -- 2. the guidance resolves to the caller's own task ------------------------------------

    def test_every_guidance_channel_names_the_callers_own_task(self) -> None:
        """`T54`: guidance must name the response's own place, on every channel it uses."""

        own = self.observation.world.contract.resolve()
        checked = 0
        for hop in self.observation.hops:
            for channel, args in hop.channels:
                for key in ("contract_path", "enclosure_path", "contractPath", "enclosurePath"):
                    path = args.get(key)
                    if not path:
                        continue
                    checked += 1
                    self.assertTrue(
                        _names_same_place(str(path), own),
                        f"{hop.tool}'s {channel} guidance named {path}, not this task's {own}",
                    )
        self.assertGreaterEqual(
            checked, len(self.observation.hops), "no guidance channel carried a path at all"
        )

    def test_a_concurrent_siblings_guidance_never_reaches_this_task(self) -> None:
        """The normal case on this machine: another leaf's lifecycle holds the process.

        The arrangement is asserted first -- the unguarded guidance derived from the ambient
        lifecycle MUST name the sibling's enclosure -- so a green here cannot mean the guard was
        never exercised. Then the property: no channel of this task's response names the sibling,
        and the operational channel still names this task's next move.
        """

        hop = self.observation.sibling_hop
        assert hop is not None
        sibling = self.observation.sibling_contract
        assert sibling is not None

        self.assertIsNotNone(hop.raw_next, "the ambient lifecycle produced no guidance to withhold")
        raw_args = (hop.raw_next.nextArgs or {}) if hop.raw_next is not None else {}
        self.assertTrue(
            any(
                _names_same_place(str(value), sibling)
                for key, value in raw_args.items()
                if key in ("contract_path", "enclosure_path")
            ),
            f"the ambient guidance did not name the sibling ({raw_args}); the arm tested nothing",
        )

        for channel, args in hop.channels:
            for key in ("contract_path", "enclosure_path", "contractPath", "enclosurePath"):
                path = args.get(key)
                if path:
                    self.assertFalse(
                        _names_same_place(str(path), sibling),
                        f"{hop.tool}'s {channel} guidance named the sibling enclosure {path}",
                    )
        if hop.top_next_tool is not None:
            self.assertIn(hop.top_next_tool, PUBLIC_TOOLS)

    # -- 3. the recommended steps are usable --------------------------------------------------

    def test_every_recommended_step_is_callable_with_the_args_its_guidance_supplies(self) -> None:
        """The derived population of recommendations, with `T60`/`T97`'s lesson applied to it.

        Each hop was first called with exactly what its own guidance supplied (plus the arguments
        that guidance declares as required), and **every response's own operational triple was then
        called verbatim** (`WorkflowChainObservation._probe_operational_triples`). Both are
        recommendations a seat could follow, so the pin is asserted EQUAL to the union: a
        recommendation that stops being callable on *either* channel is a failure, and the day one
        is deliberately tolerated, its entry has to be named in the same change.
        """

        derived = self.observation.callability_population()
        self.assertEqual(
            derived,
            GUIDANCE_STEPS_MISSING_REQUIRED_ARGS,
            "a recommended step's guidance no longer supplies arguments its tool requires, on the "
            "channel the walk followed or on a response's own operational triple; repair the "
            "guidance, or name the entry in GUIDANCE_STEPS_MISSING_REQUIRED_ARGS in the same "
            "change that introduces it",
        )

    def test_every_response_that_published_an_operational_triple_was_called_with_it(self) -> None:
        """The population of the case above, asserted so that it cannot silently shrink.

        A pin is only as wide as the population it is asserted over, so this case states the
        population: every hop carrying a `nextTool`/`nextArgs` triple has exactly one probe, each
        probe called the tool and the arguments its own response published, and the second producer
        of the same recommendation (`worktree_start`'s retry-provider-setup answer) was driven too
        rather than cited. An arm that stops probing a channel reds here.
        """

        probes = self.observation.operational_triples
        expected = {
            f"hop {index} {hop.tool}"
            for index, hop in enumerate(self.observation.hops)
            if hop.top_next_tool is not None
        }
        observed = {call.source for call in probes if call.source.startswith("hop ")}
        self.assertEqual(observed, expected, "a hop's operational triple was never called")
        self.assertIn(
            "worktree_start retry-provider-setup",
            {call.source for call in probes},
            "the retry-provider-setup response's own triple was not driven",
        )
        self.assertEqual(
            self.observation.retry_provider_setup_state,
            "blocked",
            "the retry-provider-setup response did not reach the arm that recommends worktree_status",
        )
        # Every probe must have called exactly what its response published: same shape as the
        # walk's own as-given call, one per triple, in walk order.
        by_source = {call.source: call for call in probes}
        for index, hop in enumerate(self.observation.hops):
            tool = hop.top_next_tool
            if tool is None:
                continue
            call = by_source[f"hop {index} {hop.tool}"]
            self.assertEqual(call.tool, tool)
            self.assertEqual(call.args, hop.top_next_args)
            # A probe that lost its envelope would classify as `raised`; that is a different
            # finding from an unusable recommendation, and neither belongs here.
            self.assertNotEqual(
                call.arm, "raised", f"{call.source} lost its envelope when called as published"
            )
        self.assertNotEqual(by_source["worktree_start retry-provider-setup"].arm, "raised")
        # And the negative fact, stated rather than left implicit: the operations that publish no
        # operational triple at all are the two terminal `lifecycle_finalize_task` calls, which are
        # exactly the ones the lifecycle overlay is authoritative for.
        self.assertEqual(
            {hop.tool for hop in self.observation.hops if hop.top_next_tool is None},
            {"lifecycle_finalize_task"},
        )

    def test_the_callability_arm_can_actually_fail(self) -> None:
        """The control for the case above: the classifier must see an unusable recommendation.

        `worktree_closeout_apply` requires `contract_path` and `intent_note`; called with neither,
        its own argument model must refuse, and the refusal must be attributed to that model -- not
        to the tool, and not to a generic failure. Without this, an "answered" classification could
        be satisfied by a classifier that never refuses anything.
        """

        kind, payload = self.observation.world.call("worktree_closeout_apply", {})
        self.assertEqual(
            classify_call("worktree_closeout_apply", kind, payload), "argument-refused"
        )
        self.assertEqual(
            missing_required_args(kind, payload), frozenset({"contract_path", "intent_note"})
        )
        # The same arm one field short: the recorded `intent_note` shape, and the PLURAL/ singular
        # message forms must classify alike or the population above would miss exactly the case it
        # was written for.
        partial = {"contract_path": self.observation.world.contract.as_posix()}
        kind, payload = self.observation.world.call("worktree_closeout_apply", partial)
        self.assertEqual(
            classify_call("worktree_closeout_apply", kind, payload), "argument-refused"
        )
        self.assertEqual(missing_required_args(kind, payload), frozenset({"intent_note"}))
        # And the other arm is separated by model identity: a call that loses the envelope
        # entirely is not an unusable recommendation.
        self.assertEqual(
            classify_call("worktree_closeout_apply", "RAISED ToolError", {"detail": "boom"}),
            "raised",
        )

    def test_the_guidance_that_could_not_be_validated_is_withheld_and_counted(self) -> None:
        """`T54`/`T82`: the withhold is real, counted, and explained in both directions.

        A response that declares no camelCase address cannot vouch for guidance derived from the
        process-global lifecycle, so `bound_next_step` withholds it. That is pinned by tool and
        count so it cannot grow unnoticed, and so repairing the producers is a visible edit here
        rather than a silent improvement. Asserted EQUAL in both directions.
        """

        counts: dict[str, int] = {}
        for hop in self.observation.hops:
            if hop.withheld:
                counts[hop.tool] = counts.get(hop.tool, 0) + 1
        self.assertEqual(counts, WITHHELD_GUIDANCE_HOPS)
        # Not vacuous: at least one withheld hop must still publish the same move top-level, which
        # is why the withhold costs this chain nothing today.
        carried = [
            hop.tool
            for hop in self.observation.hops
            if hop.withheld and hop.top_next_tool is not None
        ]
        self.assertTrue(carried, "every withheld hop also lost the top-level operational channel")

    # -- 4. the previews are usable -----------------------------------------------------------

    def test_every_preview_step_answered_and_the_drift_snapshot_was_actually_read(self) -> None:
        """`T62`'s trigger, inside the workflow, on this base.

        The drift snapshot is planted at the producer's own path before the cleanup and finalize
        previews, and the case asserts it was present -- otherwise a green would only mean the
        previews never met the shape that used to crash them. Every preview must have answered, and
        the snapshot must survive a preview untouched.
        """

        assert self.observation.drift_snapshot is not None
        self.assertTrue(
            self.observation.drift_snapshot_present_at_preview,
            "the drift snapshot was not present when the previews ran; the arm tested nothing",
        )
        previews = [
            hop
            for hop in self.observation.hops
            if hop.tool.endswith("_preview")
            or (hop.tool == "worktree_cleanup" and hop.as_given_args.get("dry_run"))
            or (
                hop.tool in ("lifecycle_finalize_task", "worktree_integrate")
                and hop.as_given_args.get("dry_run")
            )
        ]
        self.assertGreaterEqual(len(previews), 4, [hop.tool for hop in previews])
        for hop in previews:
            self.assertEqual(classify_call(hop.tool, hop.kind, hop.payload), "answered", hop.tool)
            self.assertTrue(
                str(hop.state or "").startswith("would-") or hop.state == "closed",
                f"{hop.tool} preview answered {hop.state!r}",
            )
        self.assertTrue(
            self.observation.drift_snapshot_preserved_by_previews,
            "a preview removed the drift snapshot it only planned to remove",
        )
        # And the real finalize did reclaim it, so the preservation above is not an artefact of
        # nothing ever touching the file.
        self.assertFalse(
            self.observation.drift_snapshot.exists(),
            "the real finalize left the drift snapshot behind",
        )

    def test_a_stale_base_closes_the_envelope_by_raising_and_is_counted(self) -> None:
        """`T87` measured, and `T34`'s class found on it.

        The register's order -- sync the leaf onto the current tip, then close out -- is asserted by
        driving the state rather than describing it: the master's source branch advances after the
        leaf started, `worktree_sync` reports `would-sync`, and the closing operations are called in
        exactly that state. The preview's refusal escapes as a raise, so the caller loses `ok`,
        `status` and the machine-readable remedy (`worktree_sync` appears only in the prose). The
        pin is asserted EQUAL in both directions: repair it and the entry goes; a second raiser is a
        failure.
        """

        calls = self.observation.stale_base_calls
        self.assertEqual(
            [hop.tool for hop in calls], ["worktree_closeout_preview", "worktree_sync"]
        )
        self.assertEqual(
            self.observation.stale_sync_state,
            "would-sync",
            "the arrangement did not leave the leaf behind its source branch; the arm is vacuous",
        )
        raisers = frozenset(
            hop.tool
            for hop in calls
            if classify_call(hop.tool, hop.kind, hop.payload) != "answered"
        )
        self.assertEqual(raisers, STALE_BASE_RAISERS)
        detail = str(calls[0].payload.get("detail", ""))
        self.assertIn("source-lineage-stale", detail)
        self.assertIn("worktree_sync", detail)

    # -- 5. the declared-requirement channel --------------------------------------------------

    def test_the_declared_requirement_channel_is_real_and_its_one_needed_case_is_unreachable(
        self,
    ) -> None:
        """`T109`: the overlay can declare what it leaves to the caller, and where that channel is.

        `NextStep.nextRequiredArgs` is the channel a recommendation uses to say "this call needs
        this argument", and `worktree_closeout_apply`'s `intent_note` is exactly the requirement the
        *operational* triple cannot carry. Three measured facts, in order:

        1. where the channel is reachable, the product uses it -- `cleanup-pending` declares
           `contract_path` for `lifecycle_finalize_task`, read from the contract on disk at the one
           walk moment that phase is live;
        2. the phase that declares `intent_note` (`closeout-pending`) is **unreachable from the
           tools' own writers**: the real apply leaves `approved_for_commit` and
           `closeout_status: completed` in the *same* contract write
           (`worktrees/modules/closeout.py::_amended_closeout_contract`), and the phase machine
           tests `closeout_status == "completed"` first, so no tool-sequenced state ever reads it;
        3. the declaration is nonetheless real, and deleting it would be invisible unless it is
           asserted where it is defined -- the contract state the phase is defined over is built
           here from the *real* post-apply contract with only that cell rewritten, which is the
           phase's own input and the reason `T109` is a finding rather than a repair.

        The declared requirement being absent from the operational triple is
        `GUIDANCE_STEPS_MISSING_REQUIRED_ARGS`'s remaining entry: the two are the same defect seen
        from its two sides.

        **Population, stated rather than implied.** The phase machine declares requirements in four
        places (`guidance.py`: `carryover-pending`, `cleanup-pending`, the published-authority
        `integration-pending`, and `closeout-pending`). This case pins two of them -- the one the
        walk reaches (`cleanup-pending`) and the one it cannot (`closeout-pending`). The other two
        are **not** asserted here: `carryover-pending` needs an integration that landed without the
        exact memory mapping, and the published-authority `integration-pending` needs a published
        curator-coherence digest, neither of which this fixture builds. Deleting either of those two
        declarations leaves this module green, which is a stated limit of this instrument rather
        than a claim it makes (`notes/reports/tsip-instruments/l8-fix2-03-mutations.py`, the `A5c`
        mutation, records the measurement).
        """

        # 1. reachable, and used: the phase declares `contract_path`, and the very next hop of the
        #    walk is the operation that phase named, carrying the declared requirement.
        hops = self.observation.hops
        cleanup_pending = self.observation.cleanup_pending_guidance
        self.assertEqual(cleanup_pending.get("phase"), "cleanup-pending", cleanup_pending)
        self.assertEqual(cleanup_pending.get("nextTool"), "lifecycle_finalize_task")
        self.assertEqual(cleanup_pending.get("nextRequiredArgs"), ["contract_path"])
        finalize = next(hop for hop in hops if hop.tool == "lifecycle_finalize_task")
        for name in cleanup_pending["nextRequiredArgs"]:
            self.assertIn(
                name, finalize.as_given_args, f"the walk's finalize hop ignored the declared {name}"
            )

        # 2. the declaring phase's input is never produced by a tool writer.
        after_closeout = self.observation.contract_after_closeout
        assert after_closeout is not None
        self.assertTrue(after_closeout.approved_for_commit)
        self.assertEqual(after_closeout.closeout_status, "completed")
        self.assertEqual(lifecycle_guidance(after_closeout).get("phase"), "integration-pending")

        # 3. the declaration, asserted on the state that defines it.
        declaring = replace(after_closeout, closeout_status="not-started")
        declaring_guidance = lifecycle_guidance(declaring)
        self.assertEqual(declaring_guidance.get("phase"), "closeout-pending", declaring_guidance)
        self.assertEqual(declaring_guidance.get("nextTool"), "worktree_closeout_apply")
        self.assertEqual(declaring_guidance.get("nextRequiredArgs"), ["intent_note"])
        self.assertNotIn("intent_note", declaring_guidance.get("nextArgs") or {})
        # The walk never reached that phase, and the operational triple that recommends the same
        # tool is the one the pin already carries -- the two are the same defect from its two sides.
        self.assertEqual(
            [hop.tool for hop in hops if hop.phase == "closeout-pending"],
            [],
            "a hop followed the closeout-pending phase; T109's unreachability claim is now wrong",
        )
