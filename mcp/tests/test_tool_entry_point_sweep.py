"""Every public tool's production entry point, swept in one hermetic world.

This module is the durable form of `260918-TSIP-L5`'s trace: it drives **every** tool the
server advertises through the same adapter a consumer's call takes — a real in-memory MCP
client session against a server built by ``create_server`` — and asserts that each one
answers in the product's own vocabulary.

**The arms this sweep publishes, and where each one is reachable:**

======================== =============================================================
``payload``              the tool answered and its payload validates.
``refusal``              ``ok: false`` with a machine-readable refusal identity or a next
                         action, so the caller keeps ``ok``/``status``.
``bare-not-ok``          ``ok: false`` with neither — pinned below.
``unvalidatable``        a payload that does not validate **where the classifier sees it**.
                         Reachable through the entry point by exactly one shape: a handler
                         that returns a raw dict PAST the choke point, which is the only
                         place a payload meets its model. Both controls below drive it.
``boundary-validation``  a raise in which the tool's OWN registered response model refused the
                         payload its producer built — the shape a ``T7``-class break takes when
                         the producer emits the key *through* ``_tool_payload``. Asserted empty
                         by name, so such a break is diagnosed instead of appearing anonymous.
``argument-validation``  a raise in which some other model refused, in practice the generated
                         ``<tool>Arguments`` input model: the CALLER's error. It shares a
                         message shape with the arm above and is separated by model identity,
                         never by wording. Asserted empty by name too.
``error``                any other raise: the envelope is gone. Pinned twice, below.
======================== =============================================================

`T34` (owner `L6`, registered on master `260918_tool-surface-and-process-integrity`) is the
first pin: an ordinary absent-capability condition escapes as a bare Python exception, so the
caller loses ``ok``, ``status`` and every recovery key. `L5`'s round-1 trace measured them;
this module keeps measuring them, so the set can only change on purpose. The second pin is the
same defect on the lifecycle family's *state* precondition, which this sweep reaches a typed
branch for by arranging the state its tools document — so it would otherwise read green over
those five.

**The rule for both pins:** when `L6` repairs one of these tools, delete that entry **in the
same change** that lands the repair — never delete a constant, and never widen one to make a
new failure pass. A tenth raiser is a failure: each pin is asserted *equal* to what was
observed, in both directions.

The positive control is a case, not a comment (``ChokePointControlTests``): it drives
``models/tools/tool_response.py::finalize_tool_response`` with a payload its model forbids and
requires it to refuse, and requires the sweep's own classifier to mark that payload
unvalidatable. If the choke point ever stops validating, that case goes red rather than the
whole sweep quietly passing on payloads nothing checked.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import anyio
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.registration import core as core_registration
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools.base import _tool_payload
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.tools.tool_response import finalize_tool_response
from agents_remember.observer.ambient import ambient
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

REPO = "repo-a"
ADOPT_REPO = "adopt-repo"
MASTER = "m"
LEAF_ID = "L1"
WORKTREE_NAME = "w"

# ---------------------------------------------------------------------------------------
# T34: the tools that lose the whole envelope. Owner L6.
#
# Every entry names an ordinary precondition, not a crash: an absent provider, or a memory
# repository whose default-branch authority was never recorded. `provider_status` and
# `memory_baseline_status` meet the SAME conditions and answer with a typed payload, which
# is why this is a defect rather than a contract. When L6 repairs one, remove that entry in
# the same change; the sweep's equality assertion is what makes the removal mandatory.
# ---------------------------------------------------------------------------------------
ENVELOPE_LOSING_RAISERS: dict[str, str] = {
    "memory_baseline_adopt": "memory repository default-branch authority is unavailable",
    "grepai_search": "grepai-memory provider is not configured",
    "grepai_trace": "grepai-memory provider is not configured",
    "cgc_symbol_search": "providers are disabled in the on-disk authority settings",
    "cgc_callers": "providers are disabled in the on-disk authority settings",
    "cgc_callees": "providers are disabled in the on-disk authority settings",
    "cgc_dependencies": "providers are disabled in the on-disk authority settings",
    "cgc_complexity": "providers are disabled in the on-disk authority settings",
    "cgc_visualize": "providers are disabled in the on-disk authority settings",
}

# ---------------------------------------------------------------------------------------
# The second pin, same defect, different precondition: the lifecycle family's *state*.
#
# Each of these five tools documents one ambient state and answers in an envelope in that
# state (the sweep arranges it, and reaches a payload or a refusal for all five). Called in
# any other state it raises, so the caller loses `ok`/`status`/`nextStep` exactly as T34's
# nine do. Measured as a four-state matrix (none / running / blocked / awaiting-developer);
# the map below is that matrix, tool -> the states in which the envelope is lost. Owner L6,
# with the sibling census's rows 13/15/16/17. Same rule: repair one, remove its entry in the
# same change; the matrix case asserts this map equals what it observes, in both directions.
# ---------------------------------------------------------------------------------------
STATE_DEPENDENT_RAISERS: dict[str, tuple[str, ...]] = {
    "lifecycle_start": ("running", "blocked", "awaiting-developer"),
    "lifecycle_resume": ("none", "running", "awaiting-developer"),
    "lifecycle_turn_end_notification": ("none", "blocked", "awaiting-developer"),
    "lifecycle_end": ("none",),
    "lifecycle_phase": ("none",),
}

AMBIENT_STATES = ("none", "running", "blocked", "awaiting-developer")

# The population the matrix is measured over, derived by RULE from the roster rather than from
# the pin: every lifecycle-state tool. Measuring the matrix over the pin itself would make the
# pin self-confirming — deleting a tool from it would delete the measurement that would have
# failed. Three members of this population answer in an envelope in all four states
# (`lifecycle_finalize_task`, `switch_lifecycle`, `lifecycle_gate`) and are therefore not
# entries; only the raising subset is pinned.
LIFECYCLE_STATE_POPULATION = tuple(
    tool for tool in PUBLIC_TOOLS if tool.startswith("lifecycle_") or tool == "switch_lifecycle"
)

# ...and the population the rule above must produce, written out. The rule alone cannot be
# checked by a subset assertion: `population == pin` satisfies "the pin is inside the
# population", so a weakened predicate would silently stop measuring the three members that do
# not raise. This constant is the second, visible edit a weakening requires, and the case
# asserts the derived population EQUALS it and that the population is STRICTLY larger than the
# pin.
EXPECTED_LIFECYCLE_STATE_POPULATION = frozenset(
    {
        "lifecycle_start",
        "lifecycle_resume",
        "lifecycle_turn_end_notification",
        "lifecycle_end",
        "lifecycle_phase",
        "lifecycle_gate",
        "lifecycle_finalize_task",
        "switch_lifecycle",
    }
)

# What the sweep is allowed to add inside its own world's official memory repository. The
# product's own `memory_init` repair writes these two scaffold files; everything else about
# that repository — and everything about the code repository — must be byte-identical after
# the sweep. A write, a rewrite or a delete outside this list fails the hermeticity case.
KNOWN_MEMORY_SCAFFOLD_ADDITIONS = frozenset({"system/sources.md", "system/tools.md"})

# The machine-readable refusal identity — this product has three spellings of it, and a
# refusal may instead carry its next action (the guidance layer binds `nextStep` to the
# response's own address, so navigation is present when a next move exists at all).
# A validated payload that reports ``ok: false`` while carrying neither a refusal code nor a
# next action: an envelope, but one a caller cannot act on without reading prose. Same rule as
# the pinned raisers — a new member is a failure unless it is added here deliberately, in the
# change that introduces it.
UNMARKED_NOT_OK: dict[str, str] = {
    "citation_migrate": (
        "reports a no-work run (all counters zero) as ok:false with no status, state or "
        "next action, so a caller cannot tell 'nothing to migrate' from 'refused'"
    ),
}

REFUSAL_IDENTITY_KEYS = ("status", "state", "refusalStatus")
NAVIGATION_KEYS = ("nextStep", "nextTool", "nextAction", "nextArgs")

ARMS = (
    "payload",
    "refusal",
    "bare-not-ok",
    "unvalidatable",
    "boundary-validation",
    "argument-validation",
    "error",
)

# The refusal a validation error names: `1 validation error for <Model>`. Matched as the MODEL
# IDENTITY, never as "did a validation message appear" — FastMCP refuses an invalid ARGUMENT
# with the same wording and a different model (`<tool>Arguments`), and calling that "the tool's
# own response model refused its payload" would be a wrong diagnosis.
REFUSED_MODEL = re.compile(r"validation error for (?P<model>[A-Za-z_][A-Za-z0-9_]*)")

# Where the product legitimately writes inside the coordination root during a sweep: its
# observer and transcript logs, its tool-report/temp trees, its control-plane records, the
# enclosure worktrees it opens, the task documents the fixture authors, and the installed
# scaffold. Measured, not assumed: a sweep of all 67 tools adds 18 files and rewrites 2, every
# one of them inside these zones, and removes none.
COORDINATION_WRITE_ZONES = (
    "benchmarks/",
    "controlplane/",
    "logs/",
    "notes/",
    "providers/",
    "skills/",
    "system/",
    "tasks/",
    "temp/",
    "worktrees/",
)


def refusal_marks(payload: dict[str, Any] | None) -> list[str]:
    """The machine-readable marks a refusal carries: a refusal identity, or a next action.

    Read from the payload itself, never from the arm the classifier assigned, so a case that
    asserts on marks cannot be satisfied by a classifier that labels everything a refusal.
    """

    if not isinstance(payload, dict):
        return []
    return [
        key
        for key in (*REFUSAL_IDENTITY_KEYS, *NAVIGATION_KEYS)
        if isinstance(payload.get(key), str) and str(payload[key]).strip()
    ]


def _raised_arm(tool: str, payload: dict[str, Any] | None) -> tuple[str, str]:
    """The arm for a call that did not return, decided by WHICH model refused.

    ``boundary-validation`` is the tool's own registered response model refusing the payload its
    producer built — a `T7`-class break. Any other model refusing, which in practice means the
    generated ``<tool>Arguments`` input model, is the CALLER's error and gets its own arm: the
    two share a message shape, and only the model identity separates them.
    """

    detail = str(payload.get("detail", "")) if isinstance(payload, dict) else ""
    refused = REFUSED_MODEL.search(detail)
    if refused is None:
        return "error", "the call raised; the envelope is gone"
    model = refused.group("model")
    if model == TOOL_RESPONSE_MODELS[tool].__name__:
        return "boundary-validation", f"{model} refused its own tool's payload"
    return "argument-validation", f"{model} refused the call's arguments"


def _returned_arm(tool: str, payload: dict[str, Any] | None) -> tuple[str, str]:
    """The arm for a call that returned: does its payload validate, and can a caller act on it?"""

    if payload is None:
        return "unvalidatable", "no structured payload reached the model"
    try:
        TOOL_RESPONSE_MODELS[tool].model_validate(payload)
    except ValidationError as error:
        # Two lines, not one: the first names the model and the second names the key, which is
        # the part a reader needs when this case fires.
        return "unvalidatable", " | ".join(str(error).splitlines()[:2])
    if payload.get("ok") is False:
        marks = refusal_marks(payload)
        if not marks:
            return (
                "bare-not-ok",
                "a validated payload reporting ok:false with no refusal code and no next action",
            )
        return "refusal", str(payload[marks[0]])
    return "payload", "ok"


def classify(tool: str, outcome: tuple[str, dict[str, Any] | None]) -> tuple[str, str]:
    """Which arm one tool's outcome falls in, and the machine-readable detail.

    The single classifier the sweep and the positive control both use, so the control proves
    the sweep — not a second implementation of it.
    """

    kind, payload = outcome
    if kind.startswith("RAISED") or kind.startswith("ERROR"):
        return _raised_arm(tool, payload)
    return _returned_arm(tool, payload)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(path: Path, branch: str = "main") -> None:
    """A Git repository with remote-tracking authority, but no remote to fetch from.

    ``refs/remotes/origin/HEAD`` is what the worktree family reads for a code repository's
    default branch (``worktrees/integration/integration_branch_repository.py``). Recording it
    directly — as ``mcp/tests/test_worktree_support.py`` does — keeps the world hermetic.
    """

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


class EntryPointWorld:
    """One disposable coordination root, one registered server, one MCP client session per call."""

    def __init__(self) -> None:
        declare_test_process()
        self._temporary = tempfile.TemporaryDirectory(prefix="ar-entry-point-")
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
        (self.memory / "system" / "settings.md").write_text(
            f"# {REPO} Memory Settings\n", encoding="utf-8"
        )
        _git(self.memory, "add", "-A")
        _git(self.memory, "commit", "-m", "memory scaffold")
        # The T34 arrangement for `memory_baseline_adopt`: a memory repository that exists and
        # carries an onboarding root but has never recorded default-branch authority, which is
        # the condition its own sibling `memory_baseline_status` answers with a payload.
        self.adopt = self.coord / "memory-repos" / f"ar-{ADOPT_REPO}"
        self.adopt.mkdir(parents=True)
        _git(self.adopt, "init", "-b", "main")
        _git(self.adopt, "config", "user.email", "test@example.invalid")
        _git(self.adopt, "config", "user.name", "Agents Remember Tests")
        (self.adopt / "memory.md").write_text("# Memory\n", encoding="utf-8")
        (self.adopt / "onboarding").mkdir()
        (self.adopt / "onboarding" / ".gitkeep").write_text("", encoding="utf-8")
        _git(self.adopt, "add", "-A")
        _git(self.adopt, "commit", "-m", "adopt base")
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
                    "repositories": {
                        REPO: {
                            "path": self.code.as_posix(),
                            "memoryRoot": self.memory.as_posix(),
                        },
                        ADOPT_REPO: {
                            "path": self.adopt.as_posix(),
                            "memoryRoot": self.adopt.as_posix(),
                        },
                    },
                    "providers": {},
                    "benchmarksEnabled": True,
                }
            ),
            encoding="utf-8",
        )
        # The documented home for the delegation policy (the MCP authority file rejects it).
        (self.coord / "system").mkdir()
        (self.coord / "system" / "settings.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "orchestration": {"gateDelegation": {"policy": "manager-decides-leaf-gates"}},
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
                benchmarks_enabled=True,
                repositories={
                    REPO: RepositoryScope(repo_id=REPO, path=self.code, memory_root=self.memory),
                    ADOPT_REPO: RepositoryScope(
                        repo_id=ADOPT_REPO, path=self.adopt, memory_root=self.adopt
                    ),
                },
            )
        )
        self.enclosure = self.coord / "tasks" / REPO / MASTER / "enclosures" / LEAF_ID.lower()
        self.contract = self.enclosure / "series-contract.md"
        self.task_ref = {"repository": REPO, "path": f"{MASTER}/task.json"}
        self.leaf_ref = {"repository": REPO, "path": f"{MASTER}/1_leaf.json"}
        self.manager = {"role": "manager", "task_document_ref": self.task_ref}
        self.worker = {"role": "worker", "task_document_ref": self.leaf_ref}
        self.orchestrator = {"role": "orchestrator", "task_document_ref": self.task_ref}

    def close(self) -> None:
        self._temporary.cleanup()

    # -- the production entry point ------------------------------------------------------

    async def _call(self, name: str, args: dict[str, Any]):
        async with create_connected_server_and_client_session(self.server._mcp_server) as client:
            return await client.call_tool(name, args)

    def call(self, name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        """Invoke one registered tool exactly as a consumer does, and read back what arrived."""

        try:
            result = anyio.run(self._call, name, args)
        except BaseException as exc:
            return f"RAISED {type(exc).__name__}", None
        if result.isError:
            text = "\n".join(
                text for content in result.content if (text := getattr(content, "text", None))
            )
            return "RAISED ToolError", {"detail": text}
        return "RETURNED", result.structuredContent

    # -- the fixture the sweep addresses -------------------------------------------------

    def census(self, root: Path) -> dict[str, str]:
        """Every file under ``root`` outside ``.git``, by relative path and digest."""

        found: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_file() and ".git" not in path.parts:
                found[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return found

    def coordination_census(self) -> dict[str, str]:
        """The coordination root's files, excluding `.git` and the two repositories.

        The two repositories are censused on their own (``repository_state``), so counting them
        here would report the same file twice with two different boundaries.
        """

        return {
            path: digest
            for path, digest in self.census(self.coord).items()
            if not path.startswith("memory-repos/")
        }

    def coordination_boundary(
        self, before: dict[str, str], after: dict[str, str]
    ) -> dict[str, list[str]]:
        """How a coordination-root census moved, split by whether the move is in a write zone.

        The predicate the hermeticity case asserts and the census control exercises, so the
        control proves the same function the case depends on.
        """

        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        rewritten = sorted(path for path in set(before) & set(after) if before[path] != after[path])

        def outside_zones(paths: list[str]) -> list[str]:
            return [path for path in paths if not path.startswith(COORDINATION_WRITE_ZONES)]

        return {
            "removed": removed,
            "added": added,
            "rewritten": rewritten,
            "added_outside_zones": outside_zones(added),
            "rewritten_outside_zones": outside_zones(rewritten),
        }

    def repository_state(self, repo: Path) -> dict[str, Any]:
        """What a hermetic sweep must not change about a repository it only reads."""

        return {
            "head": _git(repo, "rev-parse", "HEAD"),
            "status": subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout,
            "census": self.census(repo),
        }

    def build(self) -> None:
        """Author the master + leaf and open one real leaf enclosure, through the tools."""

        self.created = self.call(
            "task_doc",
            {
                "repo_id": REPO,
                "operation": "create",
                "task_name": MASTER,
                "fields": {
                    "id": "M",
                    "slug": "task",
                    "title": "Fixture master",
                    "kind": "master",
                    "repo": REPO,
                    "type": "test",
                    "createdAt": "2026-09-18T12:00:00+02:00",
                    "objective": "Give the sweep a real task document to address.",
                    "requirements": ["The sweep addresses real documents."],
                    "subTasks": [
                        {
                            "number": LEAF_ID,
                            "name": "fixture leaf",
                            "file": "1_leaf.md",
                            "status": "planning",
                            "scope": "sweep",
                        }
                    ],
                    "sections": [{"heading": "Why", "body": "Entry-point sweep."}],
                },
            },
        )
        self.leaf = self.call(
            "task_doc",
            {
                "repo_id": REPO,
                "operation": "create",
                "task_name": MASTER,
                "slug": "1_leaf",
                "fields": {
                    "id": LEAF_ID,
                    "slug": "1_leaf",
                    "title": "Fixture leaf",
                    "kind": "subTask",
                    "repo": REPO,
                    "type": "test",
                    "createdAt": "2026-09-18T12:00:00+02:00",
                    "objective": "Give the sweep a real leaf document to address.",
                    "requirements": ["One enclosure exists."],
                    "steps": [{"id": "S1", "title": "sweep", "status": "pending"}],
                },
            },
        )
        self.started = self.call(
            "worktree_start",
            {
                "repo_id": REPO,
                "task_name": MASTER,
                "worktree_name": WORKTREE_NAME,
                "leaf_id": LEAF_ID,
                "parent_task": MASTER,
                "workflow_kind": "light-task",
                "skip_provider_setup": True,
            },
        )
        assert self.contract.exists(), f"the fixture enclosure is missing: {self.contract}"
        # `worktree_start` starts the ambient lifecycle; the sweep's own `lifecycle_start`
        # case needs no active one, and its `lifecycle_resume` case prepares its own state.
        running = ambient()
        if running is not None and running.current is not None:
            self.call("lifecycle_end", {"outcome": "completed"})
        self.code_base = _git(self.code, "rev-parse", "HEAD")

    # -- benign arguments, and the states two tools need prepared ------------------------

    def benign(self) -> dict[str, dict[str, Any]]:
        """One benign invocation per public tool: enough to reach a real branch, never a mutation.

        Every mutating member is issued with ``dry_run`` where the tool offers one; the cases
        that cannot be dry-run address state this fixture does not have, so the product refuses
        them before acting. ``EntryPointProbeTests`` asserts that afterwards.
        """

        contract = self.contract.as_posix()
        absent = (
            self.coord / "tasks" / REPO / MASTER / "enclosures" / "absent" / "series-contract.md"
        ).as_posix()
        return {
            "ping": {},
            "server_info": {},
            "context_packet": {"repo_id": REPO, "include_providers": False},
            "read_ar_files": {"repo_id": REPO, "files": [{"path": "not-covered.md"}]},
            "resolve_context": {"repo_id": REPO},
            "runtime_install": {"dry_run": True, "install_provider_deps": False},
            "skills_install": {"dry_run": True},
            "dispatch_agent": {
                "task_document_ref": self.task_ref,
                "role": "worker",
                "brief": "entry-point sweep",
            },
            "retire_child": {"task_document_ref": self.task_ref, "role": "worker"},
            "rename_child": {
                "task_document_ref": self.task_ref,
                "role": "worker",
                "label": "sweep",
            },
            "rename_self": {"label": "sweep"},
            "drift_check": {"repo_id": REPO},
            "memory_quality_check": {
                "request": {"mode": "sync", "repo_id": REPO, "contract_path": contract}
            },
            "citation_fix": {"repo_id": REPO, "contract_path": contract, "dry_run": True},
            "citation_migrate": {"repo_id": REPO, "contract_path": contract, "dry_run": True},
            "route_index_refresh": {"repo_id": REPO, "dry_run": True},
            "memory_init": {"repo_id": REPO, "initialize_git": True},
            "memory_baseline_status": {"repo_id": REPO},
            "memory_baseline_adopt": {"repo_id": ADOPT_REPO},
            "memory_carryover_plan": {
                "repo_id": REPO,
                "contract_path": contract,
                "source_memory": (self.memory / "onboarding").as_posix(),
                "official_code_ref": "HEAD",
                "source_code_ref": "HEAD",
                "old_base": "HEAD",
            },
            "memory_carryover_apply": {
                "repo_id": REPO,
                "contract_path": contract,
                "source_memory": (self.memory / "onboarding").as_posix(),
                "official_code_ref": "HEAD",
                "source_code_ref": "HEAD",
                "old_base": "HEAD",
                "intent_note": "entry-point sweep",
            },
            "provider_status": {},
            "provider_diagnostics": {},
            "provider_watchers": {"action": "status"},
            "grepai_search": {"query": "entry-point sweep"},
            "grepai_trace": {"symbol": "entry_point_sweep", "trace_action": "callers"},
            "cgc_symbol_search": {"repo_id": REPO, "name": "sweep"},
            "cgc_callers": {"repo_id": REPO, "function": "sweep"},
            "cgc_callees": {"repo_id": REPO, "function": "sweep"},
            "cgc_dependencies": {"repo_id": REPO, "module": "sweep"},
            "cgc_complexity": {"repo_id": REPO},
            "cgc_visualize": {"repo_id": REPO, "dry_run": True},
            "task_doc": {
                "repo_id": REPO,
                "operation": "read_steps",
                "task_name": MASTER,
                "slug": "1_leaf",
            },
            "lifecycle_start": {},
            "lifecycle_phase": {"phase": "build"},
            "lifecycle_turn_end_notification": {"summary": "entry-point sweep"},
            "lifecycle_end": {"outcome": "completed"},
            "switch_lifecycle": {"on_unsaved": "discard"},
            "lifecycle_resume": {},
            "lifecycle_gate": {
                "kind": "plan-approval",
                "wait": True,
                "caller": {
                    "role": "worker",
                    "task_document_ref": {"repository": REPO, "path": "absent/task.json"},
                },
            },
            "gate_list": {"caller": self.worker},
            "gate_decide": {
                "task_document_ref": self.leaf_ref,
                "kind": "plan-approval",
                "decision": "approve",
                "caller": self.manager,
            },
            "role_capsule_compile": {
                "contract_path": contract,
                "task_path": f"{MASTER}/1_leaf.json",
                "role": "worker",
            },
            "skill_catalog_list": {},
            "skill_catalog_read": {"uri": "__FROM_LIST__"},
            "curator_coherence": {"request": {"action": "status", "contract_path": contract}},
            "closeout_queue": {
                "request": {
                    "action": "status",
                    "sprint_task_document_ref": self.task_ref,
                    "caller": self.orchestrator,
                }
            },
            "direct_landing": {
                "contract_path": absent,
                "code_commit": "deadbeef",
                "dry_run": True,
            },
            "worktree_start": {
                "repo_id": REPO,
                "task_name": f"{MASTER}-absent",
                "worktree_name": WORKTREE_NAME,
            },
            "worktree_status": {
                "repo_id": REPO,
                "task_name": MASTER,
                "worktree_name": WORKTREE_NAME,
                "leaf_id": LEAF_ID,
            },
            "worktree_attach": {
                "repo_id": REPO,
                "task_name": MASTER,
                "worktree_name": WORKTREE_NAME,
                "leaf_id": LEAF_ID,
            },
            "worktree_sync": {"contract_path": contract, "dry_run": True},
            "worktree_pause": {"contract_path": contract},
            "worktree_closeout_preview": {"contract_path": contract},
            "worktree_closeout_apply": {
                "contract_path": contract,
                "intent_note": "entry-point sweep",
                "dry_run": True,
            },
            "worktree_integrate": {"contract_path": contract, "dry_run": True},
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
                "intent_note": "entry-point sweep",
                "dry_run": True,
            },
            "worktree_cleanup": {"contract_path": contract, "dry_run": True},
            "worktree_abandon": {"contract_path": contract, "dry_run": True},
            "worktree_checkpoint_landing": {"contract_path": contract, "dry_run": True},
            "task_reopen": {"contract_path": contract, "dry_run": True},
            "lifecycle_finalize_task": {"contract_path": contract, "dry_run": True},
            "codex_benchmark_prepare": {"dry_run": True},
            "codex_benchmark_run": {"dry_run": True},
            "message_parent": {"ask": "sweep", "response": "sweep"},
            "message_child": {
                "task_document_ref": self.task_ref,
                "role": "worker",
                "ask": "sweep",
                "response": "sweep",
            },
        }

    def ambient_state(self) -> str:
        """The ambient lifecycle's state, or ``none`` when no lifecycle is active."""

        current = ambient()
        if current is None or current.current is None:
            return "none"
        return str(current.current.state)

    def prepare(self, tool: str) -> None:
        """Place the ambient lifecycle in the state the tool under test documents.

        Two tools are pure functions of lifecycle state and have no arguments to vary:
        ``lifecycle_start`` documents "no active lifecycle", and ``lifecycle_resume``
        documents ``blocked``. The blocked state cannot be reached through a second seat in
        one process — a real gate raise blocks the raiser, and ``gate_decide`` refuses a gate
        "that cannot be decided by its owning lifecycle" — so the state is arranged here with
        the same product method the gate path uses. ``sweep`` records the state this arranged
        and the state the call left behind, and ``test_the_resume_case_asserts_the_transition_
        it_arranged`` asserts both for ``lifecycle_resume``. See the round-2 addendum for the
        measurement behind it, and the round-3 addendum for what the arrangement does NOT buy:
        the unprepared states still lose the envelope, which is ``STATE_DEPENDENT_RAISERS``.
        """

        current = ambient()
        if tool == "lifecycle_start":
            if current is not None and current.current is not None:
                self.call("lifecycle_end", {"outcome": "completed"})
            return
        if tool == "lifecycle_resume":
            if current is None or current.current is None:
                self.call("lifecycle_start", {})
                current = ambient()
            if current is None or current.current is None:
                raise AssertionError("the sweep could not place a lifecycle to resume")
            if current.current.state != "blocked":
                current.block(kind="plan-approval", prompt="entry-point sweep")
            return

    def sweep(self) -> dict[str, dict[str, Any]]:
        """Invoke every public tool once, in registration order, and classify each outcome.

        The ambient state is recorded around each call, so a case can assert an arranged state
        rather than trusting that ``prepare`` arranged one.
        """

        arguments = self.benign()
        listing = self.call("skill_catalog_list", {})
        skills = (listing[1] or {}).get("skills") or []
        if skills:
            arguments["skill_catalog_read"] = {"uri": skills[0]["uri"]}
        results: dict[str, dict[str, Any]] = {}
        for tool in PUBLIC_TOOLS:
            self.prepare(tool)
            prepared = self.ambient_state()
            outcome = self.call(tool, arguments[tool])
            arm, detail = classify(tool, outcome)
            results[tool] = {
                "arm": arm,
                "detail": detail,
                "outcome": outcome,
                "payload": outcome[1],
                "prepared_state": prepared,
                "state_after": self.ambient_state(),
            }
        return results

    def matrix(self) -> dict[str, list[str]]:
        """Every tool that is a pure function of ambient state, in every ambient state.

        The measurement behind ``STATE_DEPENDENT_RAISERS``: for each state, the ambient is
        arranged and the tool is called with the same arguments the sweep uses.
        """

        arguments = self.benign()
        observed: dict[str, list[str]] = {}
        for state in AMBIENT_STATES:
            for tool in LIFECYCLE_STATE_POPULATION:
                self.arrange(state)
                arm, _ = classify(tool, self.call(tool, arguments[tool]))
                if arm == "error":
                    observed.setdefault(tool, []).append(state)
        self.arrange("none")
        return observed

    def arrange(self, state: str) -> None:
        """Put the ambient lifecycle into one of ``AMBIENT_STATES`` through the product's API."""

        current = ambient()
        if state == "none":
            if current is not None and current.current is not None:
                self.call("lifecycle_end", {"outcome": "completed"})
            return
        if current is None or current.current is None:
            self.call("lifecycle_start", {})
            current = ambient()
        if current is None or current.current is None:
            raise AssertionError(f"could not place an ambient lifecycle for state {state!r}")
        if current.current.state == "blocked" and state != "blocked":
            current.resume()
        elif current.current.state == "awaiting-developer" and state != "awaiting-developer":
            current.resume_from_await()
        if state == "blocked" and current.current.state != "blocked":
            current.block(kind="plan-approval", prompt="entry-point matrix")
        if state == "awaiting-developer" and current.current.state != "awaiting-developer":
            current.await_developer(summary="entry-point matrix")


class EntryPointCoverageTests(unittest.TestCase):
    """The swept population is derived, so a new tool cannot arrive unswept."""

    def test_the_swept_population_is_the_advertised_one(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        arguments = world.benign()
        self.assertEqual(set(), set(PUBLIC_TOOLS) - set(arguments), "roster tools with no case")
        self.assertEqual(set(), set(arguments) - set(PUBLIC_TOOLS), "cases for no tool")
        self.assertEqual(
            set(),
            set(PUBLIC_TOOLS) - set(TOOL_RESPONSE_MODELS),
            "advertised tools with no registered response model",
        )
        # Both sides are derived from the live server, so the sweep covers what is advertised
        # rather than what this module believes is advertised.
        advertised = {tool.name for tool in anyio.run(world.server.list_tools)}
        self.assertEqual(set(PUBLIC_TOOLS), advertised)


class EntryPointProbeTests(unittest.TestCase):
    """One sweep, one world, and the assertions that make it mean something."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()
        cls.before_contract = cls.world.contract.read_bytes()
        cls.before_worktrees = _git(cls.world.code, "worktree", "list")
        cls.before_code = cls.world.repository_state(cls.world.code)
        cls.before_memory = cls.world.repository_state(cls.world.memory)
        cls.before_coordination = cls.world.coordination_census()
        cls.results = cls.world.sweep()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def test_every_public_tool_answers_in_the_products_own_vocabulary(self) -> None:
        broken = {
            tool: row["detail"]
            for tool, row in self.results.items()
            if row["arm"] == "unvalidatable"
        }
        self.assertEqual({}, broken, "payloads that failed their own response model")
        # Not "no exceptions": a producer key its model forbids arrives HERE at the entry
        # point, because the choke point validates before returning. Asserted by name so a
        # T7-class break is diagnosed as what it is instead of as a raiser.
        self.assertEqual(
            {},
            {
                tool: row["detail"]
                for tool, row in self.results.items()
                if row["arm"] == "boundary-validation"
            },
            "a tool's own response model refused its payload at the boundary",
        )
        errors = {tool for tool, row in self.results.items() if row["arm"] == "error"}
        self.assertEqual(
            set(ENVELOPE_LOSING_RAISERS),
            errors,
            "tools that raise instead of answering (T34 pins exactly these)",
        )
        self.assertEqual(
            set(UNMARKED_NOT_OK),
            {tool for tool, row in self.results.items() if row["arm"] == "bare-not-ok"},
            "a payload reporting ok:false with no refuse code and no next action appeared or "
            "disappeared: update UNMARKED_NOT_OK in the same change",
        )
        self.assertEqual(
            len(PUBLIC_TOOLS), len(self.results), "the sweep did not invoke every public tool"
        )

    def test_the_pinned_raisers_are_exactly_the_ones_that_lose_the_envelope(self) -> None:
        observed = {tool for tool, row in self.results.items() if row["arm"] == "error"}
        self.assertEqual(
            set(ENVELOPE_LOSING_RAISERS),
            observed,
            "a raiser appeared or disappeared: update ENVELOPE_LOSING_RAISERS in the same "
            "change that repairs or introduces it (T34, owner L6)",
        )

    def test_the_state_dependent_raisers_lose_the_envelope_when_unprepared(self) -> None:
        """The second pin is measured, not declared: the four-state matrix is re-derived."""

        allowed = set(LIFECYCLE_STATE_POPULATION)
        self.assertEqual(
            EXPECTED_LIFECYCLE_STATE_POPULATION,
            allowed,
            "the derivation rule no longer produces the population it is meant to: a weakened "
            "rule would otherwise stop measuring the members that do not raise",
        )
        self.assertLessEqual(
            set(STATE_DEPENDENT_RAISERS), allowed, "a pinned tool outside the measured population"
        )
        self.assertLess(
            len(STATE_DEPENDENT_RAISERS),
            len(allowed),
            "the population collapsed onto the pin, so the matrix measures nothing the pin does "
            "not already assert",
        )
        observed = self.world.matrix()
        pinned = {tool: list(states) for tool, states in STATE_DEPENDENT_RAISERS.items()}
        self.assertEqual(
            pinned,
            observed,
            "the lifecycle state matrix moved: update STATE_DEPENDENT_RAISERS in the same "
            "change (T34's family, owner L6)",
        )
        self.assertTrue(
            any(states for states in observed.values()),
            "a matrix with no state-dependent raiser would make this pin vacuous",
        )

    def test_every_refusal_carries_a_machine_readable_identity(self) -> None:
        """Marks are read from the PAYLOAD, so a mislabelled arm cannot satisfy this case."""

        unmarked: dict[str, str] = {}
        marked = 0
        for tool, row in sorted(self.results.items()):
            payload = row["payload"]
            if not isinstance(payload, dict) or payload.get("ok") is not False:
                continue
            marks = refusal_marks(payload)
            if marks:
                marked += 1
                continue
            unmarked[tool] = f"ok:false with none of {(*REFUSAL_IDENTITY_KEYS, *NAVIGATION_KEYS)}"
        self.assertTrue(marked, "a sweep with no marked refusal proves nothing")
        self.assertEqual(
            set(UNMARKED_NOT_OK),
            set(unmarked),
            "a refusal lost or gained its machine-readable identity: update UNMARKED_NOT_OK in "
            "the same change that causes it",
        )

    def test_the_resume_case_asserts_the_transition_it_arranged(self) -> None:
        """``prepare`` claims the state it arranged; this asserts it, and the move after."""

        resume = self.results["lifecycle_resume"]
        self.assertEqual(
            "blocked",
            resume["prepared_state"],
            "the resume case did not arrange the state lifecycle_resume documents",
        )
        self.assertEqual(
            "running",
            resume["state_after"],
            "lifecycle_resume did not leave the ambient in the state it reports",
        )

    def test_the_sweep_reads_the_worktree_instead_of_performing_the_operations(self) -> None:
        """The bound is a boundary, not "wrote nothing" — and here is exactly what it covers.

        Asserted, per repository: HEAD, the Git status, and every file OUTSIDE ``.git`` by
        digest. ``.git`` is excluded and deliberately so: opening a linked worktree rewrites its
        administrative files (measured, the memory worktree's ``.git/worktrees/…/index``), which
        is Git doing its job, not the product writing where it should not. Asserted for the
        coordination root: nothing is removed, and nothing is added or rewritten outside the
        declared write zones. Not asserted: anything inside ``.git``.
        """

        self._assert_repositories_untouched()
        self._assert_coordination_root_respected()

    def _assert_repositories_untouched(self) -> None:

        self.assertTrue(self.world.contract.exists(), "the enclosure contract is gone")
        self.assertEqual(
            self.before_contract,
            self.world.contract.read_bytes(),
            "the sweep rewrote the enclosure contract",
        )
        self.assertEqual(
            self.before_worktrees,
            _git(self.world.code, "worktree", "list"),
            "the sweep moved a worktree",
        )
        self.assertEqual(
            "not-started",
            self._closeout_status(),
            "the sweep started the closeout it is only supposed to probe",
        )
        after_code = self.world.repository_state(self.world.code)
        self.assertEqual(self.before_code["head"], after_code["head"], "the code HEAD moved")
        self.assertEqual(self.before_code["status"], after_code["status"], "the code repo is dirty")
        self.assertEqual(
            self.before_code["census"],
            after_code["census"],
            "the sweep wrote to, rewrote or deleted a file in the code repository",
        )
        after_memory = self.world.repository_state(self.world.memory)
        self.assertEqual(self.before_memory["head"], after_memory["head"], "the memory HEAD moved")
        added = set(after_memory["census"]) - set(self.before_memory["census"])
        removed = set(self.before_memory["census"]) - set(after_memory["census"])
        rewritten = {
            path
            for path in set(self.before_memory["census"]) & set(after_memory["census"])
            if self.before_memory["census"][path] != after_memory["census"][path]
        }
        self.assertEqual(set(), removed, "the sweep deleted a file from the memory repository")
        self.assertEqual(set(), rewritten, "the sweep rewrote a file in the memory repository")
        self.assertEqual(
            set(KNOWN_MEMORY_SCAFFOLD_ADDITIONS),
            added,
            "the memory repository gained a file the sweep is not declared to add",
        )
        # The memory status is compared too, in the one way the declared additions allow: every
        # TRACKED line must be identical, and the untracked lines must be exactly the additions.
        self.assertEqual(
            [
                line
                for line in self.before_memory["status"].splitlines()
                if not line.startswith("??")
            ],
            [line for line in after_memory["status"].splitlines() if not line.startswith("??")],
            "the sweep changed the memory repository's tracked status",
        )
        self.assertEqual(
            sorted(f"?? {path}" for path in KNOWN_MEMORY_SCAFFOLD_ADDITIONS),
            sorted(line for line in after_memory["status"].splitlines() if line.startswith("??")),
            "the memory repository reports untracked files the sweep is not declared to add",
        )

    def _assert_coordination_root_respected(self) -> None:
        """Nothing under the coordination root disappears, and nothing moves outside a zone."""

        boundary = self.world.coordination_boundary(
            self.before_coordination, self.world.coordination_census()
        )
        self.assertEqual(
            [], boundary["removed"], "the sweep deleted a file from its own coordination root"
        )
        self.assertEqual(
            [],
            boundary["added_outside_zones"],
            "the sweep added a file outside the declared coordination write zones",
        )
        self.assertEqual(
            [],
            boundary["rewritten_outside_zones"],
            "the sweep rewrote a file outside the declared coordination write zones",
        )

    def _closeout_status(self) -> str:
        lines = self.world.contract.read_text(encoding="utf-8").splitlines()
        start = lines.index("closeout:")
        for line in lines[start + 1 :]:
            if line.startswith("  status:"):
                return line.split(":", 1)[1].strip()
            if line and not line.startswith(" "):
                break
        return ""


class ChokePointControlTests(unittest.TestCase):
    """The control the sweep depends on: a bad payload must be caught, not described."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        world = cls.world
        declare_test_process()
        cls.good = (world.call("ping", {})[1]) or {}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def test_a_payload_its_model_forbids_is_refused_by_the_choke_point(self) -> None:
        self.assertTrue(self.good, "the control needs a real payload to corrupt")
        self.assertEqual("payload", classify("ping", ("RETURNED", self.good))[0])
        corrupted = {**self.good, "undeclaredControlKey": 1}
        with self.assertRaises(ValidationError):
            finalize_tool_response("ping", corrupted)
        self.assertEqual(
            "unvalidatable",
            classify("ping", ("RETURNED", corrupted))[0],
            "the choke point validated but the sweep's classifier did not notice",
        )

    def test_a_raising_entry_point_is_classified_as_losing_the_envelope(self) -> None:
        self.assertEqual("error", classify("ping", ("RAISED ToolError", None))[0])
        self.assertEqual("unvalidatable", classify("ping", ("RETURNED", None))[0])
        bare = classify("ping", ("RETURNED", {**self.good, "ok": False}))[0]
        self.assertNotEqual("refusal", bare, "a bare ok:false must not pass as a typed refusal")
        self.assertEqual("bare-not-ok", bare)

    def test_a_producer_key_its_model_forbids_arrives_as_boundary_validation(self) -> None:
        """A `T7`-class break, driven through the entry point, lands in its own named arm.

        The arm is not decoration: because the choke point validates before returning, this is
        the shape such a break actually takes for a caller, so the sweep asserts THIS arm empty
        by name rather than letting the break appear as an anonymous raiser.
        """

        # Patch where the handler LOOKS IT UP: the registrar imports the builder by name, so
        # patching the defining module would leave the registered handler untouched and the
        # case would pass on an uncorrupted payload.
        original = core_registration.ping_payload

        def through_the_choke_point() -> dict[str, Any]:
            return _tool_payload("ping", {**original(), "undeclaredControlKey": 1})

        with mock.patch.object(core_registration, "ping_payload", through_the_choke_point):
            kind, payload = self.world.call("ping", {})
        arm, detail = classify("ping", (kind, payload))
        self.assertEqual("boundary-validation", arm, detail)
        self.assertIn("PingResponse", detail, "the detail must name the model that refused")
        self.assertEqual(
            "payload", classify("ping", self.world.call("ping", {}))[0], "the patch leaked"
        )

    def test_a_handler_that_bypasses_the_choke_point_arrives_as_unvalidatable(self) -> None:
        """The `unvalidatable` arm IS reachable at the entry point — by the bypass shape.

        ``mcp/tools/base.py::_tool_payload`` is the only route from a handler to the wire and
        the only place a payload meets its model, so a handler that returns a raw dict past it
        hands the consumer a payload nothing validated. That is the shape
        ``test_tool_response_conformance``'s structural case guards against statically; this
        case measures what it costs the caller, and is why the sweep asserts the arm empty.
        """

        original = core_registration.ping_payload

        def bypassing() -> dict[str, Any]:
            return {**original(), "undeclaredControlKey": 1}

        with mock.patch.object(core_registration, "ping_payload", bypassing):
            kind, payload = self.world.call("ping", {})
        arm, detail = classify("ping", (kind, payload))
        self.assertEqual("unvalidatable", arm, detail)
        self.assertIn("undeclaredControlKey", detail)

    def test_an_invalid_argument_lands_in_its_own_arm_and_names_the_callers_side(self) -> None:
        """The two validation refusals share a message shape; only the model separates them.

        ``citation_fix`` requires a ``contract_path``, so calling it without one is refused by
        the generated ``citation_fixArguments`` input model. That must NOT be reported as the
        tool's own response model refusing its payload — the fix exists so the diagnosis stays
        right the first time a benign argument stops being accepted.
        """

        kind, payload = self.world.call("citation_fix", {"repo_id": REPO})
        arm, detail = classify("citation_fix", (kind, payload))
        self.assertEqual("argument-validation", arm, detail)
        self.assertIn("citation_fixArguments", detail, "the detail must name the input model")
        self.assertNotIn(
            TOOL_RESPONSE_MODELS["citation_fix"].__name__,
            detail,
            "an argument refusal reported as the response model refusing its payload",
        )


class EntryPointCensusControlTests(unittest.TestCase):
    """The hermeticity check must be able to see a write, a rewrite and a delete."""

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)

    def test_the_census_notices_a_write_a_rewrite_and_a_delete(self) -> None:
        for name, mutate in (
            ("write into the memory repository", self._write_memory),
            ("rewrite the code repository's README", self._rewrite_code_readme),
            ("delete the code repository's README", self._delete_code_readme),
        ):
            repo = self.world.memory if "memory" in name else self.world.code
            before = self.world.repository_state(repo)
            mutate()
            after = self.world.repository_state(repo)
            self.assertNotEqual(
                before["census"],
                after["census"],
                f"the census did not notice a mutation that did {name}",
            )

    def _write_memory(self) -> None:
        (self.world.memory / "system" / "undeclared-write.md").write_text(
            "written by the census control\n", encoding="utf-8"
        )

    def _rewrite_code_readme(self) -> None:
        (self.world.code / "README.md").write_text("rewritten\n", encoding="utf-8")

    def _delete_code_readme(self) -> None:
        (self.world.code / "README.md").unlink()

    def test_the_coordination_boundary_notices_an_undeclared_add_rewrite_and_delete(self) -> None:
        """The coordination-root half of the hermeticity case, exercised where it is blind.

        Three mutations the repository censuses cannot see: a file added at the coordination
        root outside every write zone, a pre-existing file there rewritten, and a file deleted.
        The predicate is the one the case asserts, so this control proves the case rather than a
        second implementation of it.
        """

        world = self.world
        # An anchor at the coordination root itself: outside every declared write zone, and not
        # inside either repository, so only this boundary can see it. `coord/system/` would be
        # inside a zone (it holds the installed scaffold) and would prove nothing.
        anchor = world.coord / "outside-zone-anchor.md"
        anchor.write_text("anchor\n", encoding="utf-8")
        for name, mutate in (
            ("add a file outside every write zone", self._add_outside_zones),
            ("rewrite a file outside every write zone", self._rewrite_outside_zones),
            ("delete a file outside every write zone", self._delete_outside_zones),
        ):
            before = world.coordination_census()
            mutate()
            after = world.coordination_census()
            boundary = world.coordination_boundary(before, after)
            noticed = (
                boundary["added_outside_zones"]
                or boundary["rewritten_outside_zones"]
                or boundary["removed"]
            )
            self.assertTrue(noticed, f"the boundary did not notice a mutation that did {name}")

    def _add_outside_zones(self) -> None:
        (self.world.coord / "undeclared.md").write_text("added\n", encoding="utf-8")

    def _rewrite_outside_zones(self) -> None:
        (self.world.coord / "outside-zone-anchor.md").write_text("rewritten\n", encoding="utf-8")

    def _delete_outside_zones(self) -> None:
        (self.world.coord / "outside-zone-anchor.md").unlink()
