"""Every public tool's production entry point, swept in one hermetic world.

This module is the durable form of `260918-TSIP-L5`'s trace: it drives **every** tool the
server advertises through the same adapter a consumer's call takes — a real in-memory MCP
client session against a server built by ``create_server`` — and asserts that each one
answers in the product's own vocabulary.

**The arms this sweep publishes, and where each one is reachable:**

======================== =============================================================
``payload``              the tool answered, its whole result (read back from the file
                         the answer names) validates, and its delivered answer is
                         judged as delivered.
``refusal``              ``ok: false`` with a machine-readable refusal identity, a
                         code, or a next action, so the caller keeps ``ok`` and a
                         way forward.
``bare-not-ok``          ``ok: false`` with none of those — pinned below.
``unvalidatable``        a call the classifier cannot judge **where it must**: the
                         whole result is missing or unreadable, the whole refuses
                         its model, or the delivered answer is not the shape
                         MIK-R108 delivers — an envelope field kept, or a key
                         neither the whole result nor the shaping accounts for,
                         at any depth. The bypass shape below reads here too,
                         through whichever of those rejections it meets first:
                         a handler that returns a raw dict past the choke point
                         hands the consumer a payload nothing validated.
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

`T34` (owner `L6`, registered on master `260918_tool-surface-and-process-integrity`)
repaired the original nine raisers: an ordinary absent-capability condition used to
escape as a bare Python exception, so the caller lost ``ok``, ``status`` and every
recovery key. `L5`'s round-1 trace measured them; the positive and regression cases
below keep measuring them, so a repaired tool that raises again fails here by name.
The second pin below is the same defect on the lifecycle family's *state*
precondition, which this sweep reaches a typed branch for by arranging the state
its tools document — so it would otherwise read green over those five.

The current nonempty pins record the measured surface since `b52adc8b` under the
architect's pin handover: four lifecycle preconditions that raise (owner `L111`,
repaired under MIK-R111 rule 7). The fifth pin, one bare ``memory_quality_check``
(owner `L110`), was emptied by its repair under MIK-R110 rule 4 in the same change —
see the copied comments at the constants. They are records of what the sweep observes, not waivers: each owner's
rule binds it to empty its pin with its repair.

**The rule for the pins:** when the owning leaf repairs one of these tools, delete
that entry **in the same change** that lands the repair — never delete a constant,
and never widen one to make a new failure pass. A further raiser, or a bare
``ok: false`` the pin does not name, is a failure: each pin is asserted *equal* to
what was observed, in both directions.

The positive control is a case, not a comment (``ChokePointControlTests``, in
``test_tool_entry_point_controls.py``): it drives
``models/tools/tool_response.py::finalize_tool_response`` with a payload its model forbids and
requires it to refuse, and requires the sweep's own classifier to mark that payload
unvalidatable. If the choke point ever stops validating, that case goes red rather than the
whole sweep quietly passing on payloads nothing checked.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path
from typing import Any
from unittest import mock

import anyio
from _user_settings_guard import user_settings_path_for_write
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.read_only import (
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    UnknownReadOnlyMark,
    read_only_hint,
)
from agents_remember.mcp.registration import core as core_registration
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools.base import _tool_payload
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.tools.tool_response import finalize_tool_response
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError
from test_tool_answer_table import ROW_CASE_FRAMEWORK_KEYS

from mcp import types

REPO = "repo-a"
ADOPT_REPO = "adopt-repo"
MASTER = "m"
LEAF_ID = "L1"
WORKTREE_NAME = "w"
SESSION_WAIT_SECONDS = 60
TOOL_RESPONSE_WAIT_SECONDS = 60


class _ToolCallTimeout(AssertionError):
    """A sweep deadline failed; it is not a product exception to classify as a raiser."""


# ---------------------------------------------------------------------------------------
# T34's original nine tools remain repaired; their positive/regression checks stay below.
# Since b52adc8b (L26's landing), the admitted sweep contract reached four existing
# lifecycle preconditions, which raised instead of returning a refusal envelope. L111 owned
# their product repair (MIK-R111 rule 7) and has repaired all four, so the pin is empty.
# Repair one and remove its entry in the same change. Exact equality below checks both
# directions, so a new raiser or an entry whose tool no longer raises still fails.
# ---------------------------------------------------------------------------------------
ENVELOPE_LOSING_RAISERS: frozenset[str] = frozenset()

# What the repaired tools must answer with now. Derived from the roster rather than
# hard-coded, so the case below cannot rot. MIK-R112 removed the eight code-search
# members T34 named (the CGC and GrepAI tools); only `memory_baseline_adopt` remains,
# and it must still be advertised and must still answer in the envelope.
T34_REPAIRED_TOOLS: frozenset[str] = frozenset(
    {
        "memory_baseline_adopt",
    }
)

# ---------------------------------------------------------------------------------------
# The second pin, same defect, different precondition: the lifecycle family's *state*.
# MIK-R112 removed every lifecycle-state tool (lifecycle_start, lifecycle_resume,
# lifecycle_turn_end_notification, lifecycle_end, lifecycle_phase, switch_lifecycle,
# lifecycle_gate): no tool on the surface is a pure function of ambient state anymore,
# so the pin is EMPTY ON PURPOSE, and not deleted -- the same convention as
# ENVELOPE_LOSING_RAISERS above. A tool that raises outside its envelope must be added
# here deliberately, in the change that introduces it; the matrix case asserts the pin
# equals what it observes, in both directions.
# ---------------------------------------------------------------------------------------
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
#
# EMPTY ON PURPOSE, and not deleted: `260918-TSIP-L6` repaired `T64` (the `citation_*` family
# split its `ok` vocabulary on the same preview shape). `migration.payload` now answers
# ``ok: true`` on a dry run that planned its complete conversion -- matching its sibling
# `citation_fix` -- and declares the distinction the old `ok` folded together in
# ``state``/``outcome``/``findingsRemaining``. So there is no bare ``ok:false`` left to pin,
# and the constant stays here because it is the only place that says what a refusal with no
# identity would look like. The rule above still governs: adding an entry means editing this
# constant in the change that introduces the payload.
UNMARKED_NOT_OK: dict[str, str] = {}

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
# one of them inside these zones, and removes none. **That figure is base-scoped** (`T94`): the
# roster is 72 at the merged base, the five `knowledge_*` tools this leaf's fixture rows add
# answer inside the envelope and write nothing, and the boundary case below re-measures the
# census at run time rather than comparing against this number.
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


def _whole_result(payload: dict[str, Any]) -> dict[str, Any]:
    """The whole result behind one delivered answer.

    The tool server validates and finalizes the whole result, then strips
    the envelope for the agent; the stripped answer cannot validate against
    the whole-result model (its required internal fields are gone by
    design). The whole result is on disk: the answer's ``log`` names the
    detail file holding it. Answers with no recorded detail validate as
    delivered.
    """
    log = payload.get("log")
    if not isinstance(log, str):
        return payload
    try:
        whole = json.loads(Path(log).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return payload
    return whole if isinstance(whole, dict) else payload


def _returned_arm(tool: str, payload: dict[str, Any] | None) -> tuple[str, str]:
    """The arm for a call that returned: does its payload validate, and can a caller act on it?"""

    if payload is None:
        return "unvalidatable", "no structured payload reached the model"
    whole = _whole_result(payload)
    try:
        TOOL_RESPONSE_MODELS[tool].model_validate(whole)
    except ValidationError as error:
        # Two lines, not one: the first names the model and the second names the key, which is
        # the part a reader needs when this case fires.
        return "unvalidatable", " | ".join(str(error).splitlines()[:2])
    # The delivered answer is a projection of the whole result (envelope
    # strip, list caps, guard short form): every key it carries that the
    # whole result does not hold is unvalidated surface, named here.
    outside = sorted(set(payload) - set(whole) - ROW_CASE_FRAMEWORK_KEYS)
    if outside:
        return "unvalidatable", f"fields outside the whole result: {outside}"
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
    _git(
        path,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        f"refs/remotes/origin/{branch}",
    )


class EntryPointWorld:
    """One disposable coordination root, one registered server, one MCP client session per call.

    This world is carried here, not imported from tool_entry_point_world:
    that shared module holds the pre-removal world (removed-tool rows,
    lifecycle-tool arrangement), while this world is the merged-tree one
    (removed rows gone, ambient-API arrangement, knowledge-database API).
    The evidence catalog derives consumers from imports, so this file is
    not listed as that module's consumer.
    """

    def __init__(self) -> None:
        declare_test_process()
        self._temporary = tempfile.TemporaryDirectory(prefix="ar-entry-point-")
        self.root = Path(self._temporary.name)
        self.coord = self.root / "coord"
        self.coord.mkdir(parents=True)
        self.code = self.root / REPO
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
                    # The delegation policy rides the authority file as the
                    # supported legacy fallback (its home is the global agentic
                    # settings file). It must NOT be written to the removed
                    # coordination system/settings.json location: that file
                    # trips the settings-location guard for every tool that
                    # reads runtime settings, including dry runs.
                    "orchestration": {"gateDelegation": {"policy": "manager-decides-leaf-gates"}},
                }
            ),
            encoding="utf-8",
        )
        # The documented home for the delegation policy (the MCP authority file rejects it).
        # Since MIK-R71 the user's settings live outside the coordination root; seeding
        # the removed coord/system location trips the location upgrade refusal on every
        # tool that loads agentic settings, so the policy is seeded where the product
        # reads it, through the same guard as the shared world.
        user_settings = user_settings_path_for_write()
        user_settings.parent.mkdir(parents=True, exist_ok=True)
        user_settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "orchestration": {"gateDelegation": {"policy": "manager-decides-leaf-gates"}},
                }
            ),
            encoding="utf-8",
        )
        # The authority-file gateDelegation below is a knowingly-used legacy
        # fallback: its one-cycle migration warning is informational, and
        # pytest turns it into an error inside tool calls. Suppress exactly
        # that warning here; the fallback stays honored, guards untouched.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="orchestration.gateDelegation moved.*",
                category=UserWarning,
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
                        REPO: RepositoryScope(
                            repo_id=REPO, path=self.code, memory_root=self.memory
                        ),
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
        phase = "session initialization"
        wait_seconds = SESSION_WAIT_SECONDS
        try:
            with anyio.fail_after(wait_seconds) as deadline:
                async with create_connected_server_and_client_session(
                    self.server._mcp_server
                ) as client:
                    phase = "tool response"
                    wait_seconds = TOOL_RESPONSE_WAIT_SECONDS
                    deadline.deadline = anyio.current_time() + wait_seconds
                    result = await client.call_tool(name, args)
                    phase = "session cleanup"
                    wait_seconds = SESSION_WAIT_SECONDS
                    deadline.deadline = anyio.current_time() + wait_seconds
                return result
        except TimeoutError as error:
            raise _ToolCallTimeout(
                f"MCP sweep tool '{name}' {phase} did not finish within {wait_seconds} seconds"
            ) from error

    def call(self, name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        """Invoke one registered tool exactly as a consumer does, and read back what arrived."""

        # The authority-file gateDelegation duality the fixture authors on
        # purpose (legacy fallback beside its documented home) warns once
        # per process; pytest escalates warnings to errors inside tool
        # calls, so silence exactly those two migration notices here, as at
        # server creation. The fallback stays honored, guards untouched.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="orchestration.gateDelegation moved.*",
                category=UserWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message="orchestration.gateDelegation in the MCP authority file.*",
                category=UserWarning,
            )
            try:
                result = anyio.run(self._call, name, args)
            except _ToolCallTimeout:
                raise
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

    def git_census(self, repo: Path) -> dict[str, str]:
        """The repository's Git metadata files, by relative path and digest.

        Worktree files are censused on their own (``census``); Git writes
        land here. Lock and pid files are transient and never evidence.
        """

        git = repo / ".git"
        if not git.is_dir():
            return {}
        found: dict[str, str] = {}
        for path in sorted(git.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix in (".lock", ".pid"):
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            found[path.relative_to(git).as_posix()] = digest
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
            "git": self.git_census(repo),
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
            "server_info": {},
            "context_packet": {"repo_id": REPO},
            "read_ar_files": {"repo_id": REPO, "files": ["not-covered.md"]},
            "runtime_install": {"dry_run": True, "install_provider_deps": False},
            "memory_quality_check": {
                "request": {"mode": "sync", "repo_id": REPO, "contract_path": contract}
            },
            "citation_fix": {
                "repo_id": REPO,
                "contract_path": contract,
                "dry_run": True,
            },
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
            "task_doc": {
                "repo_id": REPO,
                "operation": "read_steps",
                "task_name": MASTER,
                "slug": "1_leaf",
            },
            # Role start and role messaging serve only a role agent AR launched. This server was
            # not started by a launch, so both refuse in the envelope before anything is read,
            # started or sent: the caller has no binding.
            "role_start": {
                "role": "worker",
                "request_id": "00000000-0000-4000-8000-000000000001",
                "sprint_document_ref": self.task_ref,
                "master_document_ref": self.task_ref,
                "task_document_ref": self.leaf_ref,
            },
            # The set and the native retirement need the same launch binding: unbound,
            # both refuse before anything is prepared, started or archived.
            "role_start_set": {
                "request_id": "00000000-0000-4000-8000-000000000003",
                "sprint_document_ref": self.task_ref,
                "master_document_ref": self.task_ref,
                "task_document_ref": self.leaf_ref,
            },
            "retire_child": {
                "task_document_ref": self.leaf_ref,
                "role": "worker",
            },
            "role_message": {
                "text": "entry-point sweep",
                "agent_id": "00000000-0000-4000-8000-000000000002",
            },
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
                "leaf_task_document_ref": {
                    "repository": REPO,
                    "path": f"{MASTER}/absent.json",
                },
                "dry_run": True,
            },
            "direct_execution_grant": {
                "task_document_ref": {"repository": REPO, "path": f"{MASTER}/absent.json"},
                "action": "give",
            },
            "worktree_start": {
                "repo_id": REPO,
                "task_name": f"{MASTER}-absent",
                "worktree_name": WORKTREE_NAME,
            },
            "worktree_status": {
                "repo_id": REPO,
                "contract_path": contract,
            },
            "worktree_attach": {
                "repo_id": REPO,
                "task_name": MASTER,
                "worktree_name": WORKTREE_NAME,
                "leaf_id": LEAF_ID,
            },
            "worktree_enclosure_adopt": {
                "contract_path": contract,
                "expected_worktree_group": self.enclosure.as_posix(),
                "rationale": "entry-point sweep",
                "dry_run": True,
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
            # -- knowledge ----------------------------------------------------------------
            # The three `knowledge_*` operations read through the caller's
            # memory root: naming a tree this world does not have is the
            # reachable branch, and the surface answers a selection it
            # cannot read inside its own envelope rather than raising (see
            # the knowledge block at the end of this table), so the branch
            # is reachable and hermetic -- and the path stays inside the
            # coordination root so nothing is touched outside it.
            "knowledge_read": {
                "memoryRoot": (self.coord / "temp" / "absent-memory").as_posix(),
                "view": "source_context",
            },
            "knowledge_diff": {
                "memoryRoot": (self.coord / "temp" / "absent-memory").as_posix(),
            },
            "knowledge_integrity_check": {
                "memoryRoot": (self.coord / "temp" / "absent-memory").as_posix(),
            },
            # MIK-R73 rule 1: a write with no contract and no authorization is refused before
            # anything is read, so the sweep reaches the refusal branch hermetically.
            "knowledge_write": {"authorizationRef": ""},
        }

    def prepare(self, tool: str) -> None:
        """No per-tool ambient arrangement: MIK-R112 removed the lifecycle-state tools.

        The sweep records the ambient state around each call regardless, and ``matrix``
        arranges states through the ambient API for the remaining lifecycle population.
        """

    def sweep(self) -> dict[str, dict[str, Any]]:
        """Invoke every public tool once, in registration order, and classify each outcome.

        The ambient state is recorded around each call, so a case can assert an arranged state
        rather than trusting that ``prepare`` arranged one.

        A roster member with no row in :meth:`benign` is refused **by name here**, before any call
        is made: the population is derived from ``PUBLIC_TOOLS`` at run time, so a tool that
        arrives without a fixture row must read as *"roster tools with no case"* rather than as the
        ``KeyError`` it used to be -- the same defect either way, but the named one says which five
        tools and what is missing. (Measured on the merged base: five ``knowledge_*`` tools
        arrived and this raised ``KeyError: 'knowledge_read'`` from ``setUpClass``, which is why
        every case in ``EntryPointProbeTests`` errored instead of failing on its own assertion.)
        """

        arguments = self.benign()
        uncased = sorted(set(PUBLIC_TOOLS) - set(arguments))
        if uncased:
            raise AssertionError(f"roster tools with no case: {uncased}")
        results: dict[str, dict[str, Any]] = {}
        # Same known legacy fallback as at server creation: suppress exactly
        # its migration warning for the run; the fallback stays honored.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="orchestration.gateDelegation moved.*",
                category=UserWarning,
            )
            for tool in PUBLIC_TOOLS:
                self.prepare(tool)
                prepared = "none"
                outcome = self.call(tool, arguments[tool])
                arm, detail = classify(tool, outcome)
                results[tool] = {
                    "arm": arm,
                    "detail": detail,
                    "outcome": outcome,
                    "payload": outcome[1],
                    "prepared_state": prepared,
                    "state_after": "none",
                }
        return results


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
        cls.addClassCleanup(cls.world.close)
        cls.world.build()
        cls.before_contract = cls.world.contract.read_bytes()
        cls.before_worktrees = _git(cls.world.code, "worktree", "list")
        cls.before_code = cls.world.repository_state(cls.world.code)
        cls.before_memory = cls.world.repository_state(cls.world.memory)
        cls.before_coordination = cls.world.coordination_census()
        cls.results = cls.world.sweep()

    def test_every_public_tool_answers_in_the_products_own_vocabulary(self) -> None:
        # MIK-R130 rule 6: every aspect below runs even when an earlier one
        # fails -- one tool's failure no longer keeps the aspects behind it
        # unrun. Each tool is judged on its own subTest, so one tool's
        # failure does not hide the rest either.
        for tool, row in sorted(self.results.items()):
            with self.subTest(tool=tool, aspect="unvalidatable"):
                self.assertNotEqual(
                    "unvalidatable",
                    row["arm"],
                    f"payloads that failed their own response model: {row['detail']}",
                )
            # Not "no exceptions": a producer key its model forbids arrives HERE at the entry
            # point, because the choke point validates before returning. Asserted by name so a
            # T7-class break is diagnosed as what it is instead of as a raiser.
            with self.subTest(tool=tool, aspect="boundary-validation"):
                self.assertNotEqual(
                    "boundary-validation",
                    row["arm"],
                    "a tool's own response model refused its payload at the boundary: "
                    f"{row['detail']}",
                )
        with self.subTest(aspect="raisers"):
            errors = {tool for tool, row in self.results.items() if row["arm"] == "error"}
            self.assertEqual(
                set(ENVELOPE_LOSING_RAISERS),
                errors,
                "tools that raise instead of answering (L111 pins these preconditions)",
            )
        with self.subTest(aspect="unmarked"):
            self.assertEqual(
                set(UNMARKED_NOT_OK),
                {tool for tool, row in self.results.items() if row["arm"] == "bare-not-ok"},
                "a payload reporting ok:false with no refuse code and no next action appeared or "
                "disappeared: update UNMARKED_NOT_OK in the same change",
            )
        with self.subTest(aspect="census"):
            self.assertEqual(
                len(PUBLIC_TOOLS),
                len(self.results),
                "the sweep did not invoke every public tool",
            )

    def test_the_pinned_raisers_are_exactly_the_ones_that_lose_the_envelope(
        self,
    ) -> None:
        """The lifecycle pin is exact; T34's original nine must remain repaired.

        Equality checks the observed error arm in BOTH directions. An unpinned raiser or an
        entry whose tool no longer raises fails here; L111 removes each entry with its repair.
        """

        observed = {tool for tool, row in self.results.items() if row["arm"] == "error"}
        self.assertEqual(
            set(ENVELOPE_LOSING_RAISERS),
            observed,
            "a raiser appeared or disappeared: update ENVELOPE_LOSING_RAISERS in the same "
            "change that repairs or introduces it (lifecycle preconditions, owner L111)",
        )
        self.assertEqual(
            set(),
            observed & T34_REPAIRED_TOOLS,
            "an original T34 repaired tool lost its refusal envelope again (owner L6)",
        )

    def test_the_t34_family_answers_with_a_named_refusal_instead_of_raising(
        self,
    ) -> None:
        """`T34`'s positive control: the nine repaired tools answer, and the answer is typed.

        Before the repair each of these nine raised `ToolError` with ``envelope_keys=[]``, so
        the caller lost ``ok``/``status``/``nextAction``. This case is the durable form of that
        finding: every one of the nine is still advertised, still answers, and the answer names
        **what refused** (a refusal identity), **why** (a non-empty detail) and **the next
        action**.

        Its control is not a comment. The same assertions are run against an `ok: false` payload
        with the refusal keys stripped, and that control must fail them -- so the case proves it
        can see a refusal that stopped naming anything rather than describing one.
        """

        def assert_named_refusal(tool: str, payload: dict[str, Any]) -> None:
            self.assertEqual(False, payload.get("ok"), f"{tool} did not refuse")
            identity = refusal_marks(payload)
            self.assertTrue(
                [key for key in REFUSAL_IDENTITY_KEYS if key in identity],
                f"{tool} refused without a refusal identity: {sorted(payload)}",
            )
            self.assertTrue(
                str(payload.get("detail") or "").strip(),
                f"{tool} refused without saying why (no detail)",
            )
            self.assertTrue(
                [key for key in NAVIGATION_KEYS if key in identity],
                f"{tool} refused without a machine-readable next action: {sorted(payload)}",
            )

        missing = T34_REPAIRED_TOOLS - set(PUBLIC_TOOLS)
        self.assertEqual(set(), missing, "a repaired T34 tool is no longer advertised")
        self.assertEqual(
            set(),
            T34_REPAIRED_TOOLS - set(self.results),
            "the sweep did not invoke a repaired T34 tool",
        )
        for tool in sorted(T34_REPAIRED_TOOLS):
            row = self.results[tool]
            self.assertNotEqual("error", row["arm"], f"{tool} lost the envelope again")
            assert_named_refusal(tool, row["payload"])

        # The control: the same predicate, on the shape a bare raise would have produced.
        with self.assertRaises(AssertionError):
            assert_named_refusal(
                "memory_baseline_adopt",
                {"ok": False, "operation": "memory_baseline_adopt"},
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

    def test_the_sweep_reads_the_worktree_instead_of_performing_the_operations(
        self,
    ) -> None:
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
            [],
            boundary["removed"],
            "the sweep deleted a file from its own coordination root",
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
        cls.addClassCleanup(cls.world.close)
        world = cls.world
        declare_test_process()
        cls.good = (world.call("server_info", {})[1]) or {}

    def test_a_tool_that_never_answers_fails_by_name_and_closes_its_handler(
        self,
    ) -> None:
        handler_closed: list[bool] = []

        async def no_response(request):
            try:
                await anyio.sleep_forever()
            finally:
                handler_closed.append(True)

        with (
            mock.patch.dict(
                self.world.server._mcp_server.request_handlers,
                {types.CallToolRequest: no_response},
            ),
            mock.patch(f"{__name__}.TOOL_RESPONSE_WAIT_SECONDS", 3),
            self.assertRaisesRegex(
                _ToolCallTimeout,
                "'server_info' tool response did not finish within 3 seconds",
            ),
        ):
            self.world.call("server_info", {})
        self.assertEqual(handler_closed, [True])
        self.assertEqual("payload", classify("server_info", self.world.call("server_info", {}))[0])

    def test_a_payload_its_model_forbids_is_refused_by_the_choke_point(self) -> None:
        self.assertTrue(self.good, "the control needs a real payload to corrupt")
        self.assertEqual("payload", classify("server_info", ("RETURNED", self.good))[0])
        corrupted = {**self.good, "undeclaredControlKey": 1}
        with self.assertRaises(ValidationError):
            finalize_tool_response("server_info", corrupted)
        self.assertEqual(
            "unvalidatable",
            classify("server_info", ("RETURNED", corrupted))[0],
            "the choke point validated but the sweep's classifier did not notice",
        )

    def test_a_raising_entry_point_is_classified_as_losing_the_envelope(self) -> None:
        self.assertEqual("error", classify("server_info", ("RAISED ToolError", None))[0])
        self.assertEqual("unvalidatable", classify("server_info", ("RETURNED", None))[0])
        bare = classify("server_info", ("RETURNED", {**self.good, "ok": False}))[0]
        self.assertNotEqual("refusal", bare, "a bare ok:false must not pass as a typed refusal")
        self.assertEqual("bare-not-ok", bare)

    def test_a_producer_key_its_model_forbids_arrives_as_boundary_validation(
        self,
    ) -> None:
        """A `T7`-class break, driven through the entry point, lands in its own named arm.

        The arm is not decoration: because the choke point validates before returning, this is
        the shape such a break actually takes for a caller, so the sweep asserts THIS arm empty
        by name rather than letting the break appear as an anonymous raiser.
        """

        # Patch where the handler LOOKS IT UP: the registrar imports the builder by name, so
        # patching the defining module would leave the registered handler untouched and the
        # case would pass on an uncorrupted payload.
        original = core_registration.server_info_payload

        def through_the_choke_point(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return _tool_payload(
                "server_info", {**original(*args, **kwargs), "undeclaredControlKey": 1}
            )

        with mock.patch.object(core_registration, "server_info_payload", through_the_choke_point):
            kind, payload = self.world.call("server_info", {})
        arm, detail = classify("server_info", (kind, payload))
        self.assertEqual("boundary-validation", arm, detail)
        self.assertIn("ServerInfoResponse", detail, "the detail must name the model that refused")
        self.assertEqual(
            "payload",
            classify("server_info", self.world.call("server_info", {}))[0],
            "the patch leaked",
        )

    def test_a_handler_that_bypasses_the_choke_point_arrives_as_unvalidatable(
        self,
    ) -> None:
        """The `unvalidatable` arm IS reachable at the entry point — by the bypass shape.

        ``mcp/tools/base.py::_tool_payload`` is the only route from a handler to the wire and
        the only place a payload meets its model, so a handler that returns a raw dict past it
        hands the consumer a payload nothing validated. That is the shape
        ``test_tool_response_conformance``'s structural case guards against statically; this
        case measures what it costs the caller, and is why the sweep asserts the arm empty.
        """

        original = core_registration.server_info_payload

        def bypassing(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {**original(*args, **kwargs), "undeclaredControlKey": 1}

        with mock.patch.object(core_registration, "server_info_payload", bypassing):
            kind, payload = self.world.call("server_info", {})
        arm, detail = classify("server_info", (kind, payload))
        self.assertEqual("unvalidatable", arm, detail)
        self.assertIn("undeclaredControlKey", detail)

    def test_an_invalid_argument_lands_in_its_own_arm_and_names_the_callers_side(
        self,
    ) -> None:
        """The two validation refusals share a message shape; only the model separates them.

        ``citation_fix`` requires a ``repo_id`` (``contract_path`` is optional since the
        MIK-R45 pre-baseline admission), so calling it without one is refused by
        the generated ``citation_fixArguments`` input model. That must NOT be reported as the
        tool's own response model refusing its payload — the fix exists so the diagnosis stays
        right the first time a benign argument stops being accepted.
        """

        kind, payload = self.world.call("citation_fix", {})
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

    def test_the_coordination_boundary_notices_an_undeclared_add_rewrite_and_delete(
        self,
    ) -> None:
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


# Paths a read-marked tool may touch without failing the rule-7 case: the
# tool log and its detail files (MIK-R108), and the derived knowledge index
# under runtime/knowledge-index/ (MIK-R74) -- and nothing else. The exemption
# names the index directory exactly: any other runtime/ write (provider
# state, temp files promoted sideways) fails the case. Coordination census
# paths are relative to the coordination root.
_READ_SWEEP_IGNORED_PREFIXES = ("logs/", "runtime/knowledge-index/")
_READ_SWEEP_IGNORED_SEGMENTS = ("/logs/",)


def _read_sweep_kept(census: dict[str, str]) -> dict[str, str]:
    """One coordination census without the log and index paths."""

    return {
        path: digest
        for path, digest in census.items()
        if not path.startswith(_READ_SWEEP_IGNORED_PREFIXES)
        and not any(segment in path for segment in _READ_SWEEP_IGNORED_SEGMENTS)
    }


class ReadOnlyMarkTests(unittest.TestCase):
    """MIK-R109 rule 7: a tool that only reads says so, and only reads."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.addClassCleanup(cls.world.close)
        cls.world.build()
        # The fixture author's old delegation-policy file (coord/system/)
        # is an upgrade-pending layout the new base refuses to read beside
        # a user file; the read sweep addresses no delegation policy, so
        # the world presents the new layout and no call can trip the
        # upgrade collision mid-sweep.
        old_system = cls.world.coord / "system" / "settings.json"
        old_system.unlink(missing_ok=True)

    def test_every_registered_tool_stands_in_exactly_one_part_of_the_list(self) -> None:
        self.assertEqual(
            frozenset(PUBLIC_TOOLS),
            READ_ONLY_TOOLS | MUTATING_TOOLS,
            "a registered tool stands in neither part of the read-only list",
        )
        self.assertEqual(
            frozenset(),
            READ_ONLY_TOOLS & MUTATING_TOOLS,
            "a tool stands in both parts of the read-only list",
        )
        with self.assertRaises(UnknownReadOnlyMark):
            read_only_hint("no-such-tool")
        # L94 rule 2: the direct-execution grant writes the grant record
        # only, so it stands with the mutating tools, not the reads.
        self.assertFalse(read_only_hint("direct_execution_grant"))

    def test_every_advertised_mark_matches_the_one_list(self) -> None:
        by_name = {tool.name: tool for tool in anyio.run(self.world.server.list_tools)}
        self.assertEqual(set(by_name), set(PUBLIC_TOOLS))
        for tool in sorted(PUBLIC_TOOLS):
            with self.subTest(tool=tool):
                annotations = by_name[tool].annotations
                self.assertIsNotNone(annotations, f"{tool} is advertised unmarked")
                assert annotations is not None
                self.assertEqual(
                    tool in READ_ONLY_TOOLS,
                    annotations.readOnlyHint,
                    f"{tool} advertises readOnlyHint={annotations.readOnlyHint}",
                )

    def test_a_tool_marked_as_a_read_changes_no_file_of_the_world(self) -> None:
        # The derived index and the logs do not count (MIK-R74, MIK-R108);
        # every other file of the world must not move under a marked read --
        # worktree files, Git metadata, and every coordination file outside
        # the log and the index directory. Each call must answer (a refusal
        # inside the envelope still counts); a call that raises instead of
        # answering proves nothing about writes and fails the case.
        arguments = self.world.benign()
        variants = self._read_variants()
        for tool in sorted(READ_ONLY_TOOLS):
            cases = [arguments[tool], *variants.get(tool, [])]
            for index, args in enumerate(cases):
                with self.subTest(tool=tool, case=index):
                    before_code = self.world.repository_state(self.world.code)
                    before_memory = self.world.repository_state(self.world.memory)
                    before_coord = _read_sweep_kept(self.world.coordination_census())
                    outcome, payload = self.world.call(tool, args)
                    self.assertEqual(outcome, "RETURNED", f"{tool} raised: {payload}")
                    self.assertIsInstance(payload, dict, tool)
                    self.assertEqual(before_code, self.world.repository_state(self.world.code))
                    self.assertEqual(before_memory, self.world.repository_state(self.world.memory))
                    self.assertEqual(
                        before_coord,
                        _read_sweep_kept(self.world.coordination_census()),
                        f"{tool} changed a file of its world outside the log and index",
                    )

    def _read_variants(self) -> dict[str, list[dict[str, Any]]]:
        """Non-default invocations of the marked reads, still read-only.

        Legacy-argument refusals, unknown views, and absent contracts all
        answer inside the envelope (RETURNED): the sweep watches each for
        writes too, so a refusal path cannot hide a mutation.
        """

        absent_contract = (
            self.world.coord / "tasks" / REPO / MASTER / "enclosures" / "absent"
        ).as_posix()
        return {
            "read_ar_files": [
                {"repo_id": REPO, "files": [{"path": "not-covered.md"}]},
                {"repo_id": REPO, "files": ["not-covered.md"], "refresh": True},
                {"repo_id": REPO, "files": ["not-covered.md"], "refresh": False},
            ],
            "knowledge_read": [
                {
                    "memoryRoot": (self.world.coord / "temp" / "absent-memory").as_posix(),
                    "view": "no-such-view",
                },
            ],
            "knowledge_integrity_check": [
                {"memoryRoot": (self.world.coord / "temp" / "absent-memory").as_posix()},
            ],
            "worktree_status": [
                {
                    "repo_id": REPO,
                    "contract_path": self.world.contract.as_posix(),
                    "leaf_id": "no-such-leaf",
                },
            ],
            "worktree_closeout_preview": [{"contract_path": absent_contract}],
        }

    def test_the_sweep_catches_a_git_metadata_write(self) -> None:
        # Control: a write to Git metadata under a marked read must fail
        # the comparison. The write is injected (no registered read writes
        # .git/); the monitor must see it.
        arguments = self.world.benign()
        before = self.world.repository_state(self.world.code)
        probe = self.world.code / ".git" / "sweep-control-probe"
        probe.write_text("control\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, self.world.repository_state(self.world.code))
        finally:
            probe.unlink(missing_ok=True)
        self.assertEqual(before, self.world.repository_state(self.world.code))
        self.assertIn("server_info", arguments)

    def test_the_sweep_catches_a_non_index_runtime_write(self) -> None:
        # Control: a write to runtime/ outside the derived-index directory
        # must fail the comparison -- the exemption names the index
        # directory exactly, not all of runtime/.
        before = _read_sweep_kept(self.world.coordination_census())
        probe_dir = self.world.coord / "runtime" / "sweep-control"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / "probe.json"
        probe.write_text("{}\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, _read_sweep_kept(self.world.coordination_census()))
        finally:
            probe.unlink(missing_ok=True)
            probe_dir.rmdir()

    def test_the_sweep_catches_an_ordinary_write(self) -> None:
        # Control: an ordinary worktree write must fail the comparison.
        before = self.world.repository_state(self.world.code)
        probe = self.world.code / "sweep-control-probe.md"
        probe.write_text("control\n", encoding="utf-8")
        try:
            self.assertNotEqual(before, self.world.repository_state(self.world.code))
        finally:
            probe.unlink(missing_ok=True)

    def test_a_freshness_fetch_writes_only_under_a_false_mark(self) -> None:
        # context_packet admits include_freshness, which fetches the
        # configured remotes and writes FETCH_HEAD: the registered call
        # must perform the write, and the advertised mark must be false.
        self.assertFalse(read_only_hint("context_packet"))
        remote = self.world.coord / "sweep-remote.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(remote)],
            check=True,
            capture_output=True,
        )
        self.addCleanup(shutil.rmtree, remote, True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.world.code),
                "remote",
                "add",
                "sweep-remote",
                str(remote),
            ],
            check=True,
            capture_output=True,
        )
        # The remote must advertise a ref: fetching an empty remote writes
        # nothing, so seed it with the world's own HEAD first.
        subprocess.run(
            [
                "git",
                "-C",
                str(self.world.code),
                "push",
                "-q",
                "sweep-remote",
                "HEAD:refs/heads/seed",
            ],
            check=True,
            capture_output=True,
        )
        # The fetch only runs for a branch with an upstream: track the
        # seeded ref from the checked-out branch.
        subprocess.run(
            [
                "git",
                "-C",
                str(self.world.code),
                "branch",
                "--set-upstream-to=sweep-remote/seed",
            ],
            check=True,
            capture_output=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", str(self.world.code), "remote", "remove", "sweep-remote"],
            check=False,
            capture_output=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", str(self.world.code), "branch", "--unset-upstream"],
            check=False,
            capture_output=True,
        )
        fetch_head = self.world.code / ".git" / "FETCH_HEAD"
        if fetch_head.exists():
            fetch_head.unlink()
        outcome, payload = self.world.call(
            "context_packet", {"repo_id": REPO, "include_freshness": True}
        )
        self.assertEqual(outcome, "RETURNED", payload)
        self.assertTrue(fetch_head.is_file(), payload)
