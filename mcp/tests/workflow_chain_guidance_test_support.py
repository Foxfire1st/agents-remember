"""The disposable world, the walk and its derived populations for the workflow chain.

The cases are in ``test_workflow_chain_guidance.py``, whose module docstring states what the
walk measures and why. This module holds the machinery they measure over: the workflow
statement's constants and pins, the call classifier, the ``Hop``/``RecommendedCall`` records,
``WorkflowWorld`` (two real Git repositories, real task documents, a real registered server)
and ``WorkflowChainObservation`` (the one walk and the arrangements driven around it).
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.next_step import next_step_for
from agents_remember.application.provider_runtime import setup_progress_path
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.models.base import NextStep
from agents_remember.models.knowledge_files import canonical_text
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


def emitted_next_step(payload: dict[str, Any]) -> NextStep | None:
    """The lifecycle guidance one answer emitted, in the model's shape.

    MIK-R108: the answer carries the hint as ``next`` ({summary, tool, args})
    instead of ``nextStep``; the observation translates it back so the
    address binding below measures the same guidance content.
    """

    emitted = payload.get("nextStep")
    if emitted is None:
        following = payload.get("next")
        if isinstance(following, dict):
            emitted = {
                "summary": following.get("summary", ""),
                "nextTool": following.get("tool"),
                "nextArgs": following.get("args"),
            }
    return NextStep.model_validate(emitted) if isinstance(emitted, dict) else None


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
        # Converted memory: the layout marker rides the scaffold commit, so lifecycles under
        # test run the converted routes. The scaffold pairs with the code base (Code-Commit
        # trailer): converted memory is judged by the invariant gate, which reads the pairing.
        (self.memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (self.memory / "knowledge" / "layout.json").write_text(
            '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n', encoding="utf-8"
        )
        _git(self.memory, "add", "-A")
        code_base = _git(self.code, "rev-parse", "HEAD")
        _git(self.memory, "commit", "-m", f"memory scaffold\n\nCode-Commit: {code_base}")
        # `load_config` resolves a configured repository as `<workspaceRoot>/<repo_id>` and ignores
        # the settings file's `path` key (`kernel/primitives/runtime_config.py::_parse_repository_entry`);
        # that is the convention the mutation boundaries re-read afterwards. Without this link the
        # world is refused with `configured-contract-authority-invalid` before closeout.
        (self.root / REPO).symlink_to(self.code, target_is_directory=True)
        self.settings = self.root / "mcp" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coord.as_posix(),
                    "workspaceRoot": self.root.as_posix(),
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
                        # MIK-R189 rule 2: the walk starts these leaves, so each records the
                        # architect's go before its start is admitted.
                        "intentReview": {"state": "go"},
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
                    # MIK-R189 rule 2: this sibling's start is one of the walk's hops.
                    "intentReview": {"state": "go"},
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
        raw = next_step_for(target, tool)
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
            emitted_next=emitted_next_step(payload),
        )
        if record_in_walk:
            self.hops.append(hop)
        return hop

    def _walk(self) -> None:
        self.world.author_documents()
        started = self._record("worktree_start")
        assert started.kind == "RETURNED", started

        # The work precedes its source-bound judgment.
        contract = load_contract(self.world.contract)
        (contract.code_worktree / CODE_CHANGE).write_text("work\n", encoding="utf-8")
        code = CodeSnapshot.capture(contract.code_worktree)

        # Converted memory: the leaf's closeout judges the work file against the invariant
        # gate, which needs the file's onboarding trace. Seed L1's open history with the
        # trace row; the closeout extends it with its own rows.
        history_dir = load_contract(self.world.contract).memory_worktree
        assert history_dir is not None
        history_dir = history_dir / "knowledge" / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        (history_dir / f"{LEAF_ID}.json").write_text(
            canonical_text(
                {
                    "schema": "ar-history/v1",
                    "leaf": LEAF_ID,
                    "closed": False,
                    "rows": [
                        {
                            "id": "ROW-000000",
                            "subject": f"onboarding:{CODE_CHANGE}",
                            "disposition": "no_impact",
                            "reason": "The work file carries no invariant.",
                            "items": [],
                            "sources": [{"path": CODE_CHANGE, "blob": code.blobs[CODE_CHANGE]}],
                        }
                    ],
                }
            ),
            encoding="utf-8",
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
        raw = next_step_for(self.world.contract.as_posix(), tool)
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
            emitted_next=emitted_next_step(payload),
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

        self.world.close()
