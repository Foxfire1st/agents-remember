"""The hermetic world and vocabulary classifier the entry-point sweep runs in.

Moved text-identical out of test_tool_entry_point_sweep.py (MIK-R196 rule 10):
the world, the response classifier, the arm helpers and the constants every
sweep module names. The cases stay in the sibling test_tool_entry_point_*.py
modules that import this one."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Any

import anyio
from _user_settings_guard import user_settings_path_for_write
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError
from tool_answer_table_support import ROW_CASE_FRAMEWORK_KEYS

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
        self.settings = self.root / "mcp" / "settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coord.as_posix(),
                    "workspaceRoot": self.root.as_posix(),
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
                    # MIK-R189 rule 2: a leaf created now starts with its intent review
                    # ``pending``, which refuses every start. The sweep is about the start and
                    # its enclosure, not about the review, so its world records the developer's
                    # answer as the go the start requires.
                    "intentReview": {"state": "go", "decisionRef": "D369"},
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
            # A dry run against bytes the case does not hold: the answer is the product's own
            # stale-input refusal, and nothing is written (MIK-R198 rule 6a).
            "citation_note_revision": {
                "repo_id": REPO,
                "contract_path": contract,
                "card": "onboarding/example.py.md",
                "reference": 1,
                "note": "entry-point sweep",
                "base_digest": "0" * 64,
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
            # The architect's explainer tool answers only a hosted architect seat for `claim`,
            # and only the seat the master's record names for `update` and `retire`. This server
            # was not started by a launch, so the claim refuses before anything is written.
            "explainer_control": {
                "action": "claim",
                "repo_id": REPO,
                "task_name": MASTER,
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
            "user_settings_init": {"target": "host-authority", "dry_run": True},
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
