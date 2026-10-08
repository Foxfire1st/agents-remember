"""The hermetic world the tool entry-point sweep runs in, and the classifier of its answers.

Moved unchanged out of ``test_tool_entry_point_sweep.py`` so that module stays well under the
file-size limit: the world builds a repository pair, a coordination root and a server, sweeps every
public tool through a real in-memory MCP client session and classifies each answer into one arm
(see that module's table). The pins on what the sweep may observe stay in the sweep module.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import anyio
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.server import create_server
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import ambient
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

REPO = "repo-a"
ADOPT_REPO = "adopt-repo"
MASTER = "m"
LEAF_ID = "L1"
WORKTREE_NAME = "w"
SESSION_WAIT_SECONDS = 60
TOOL_RESPONSE_WAIT_SECONDS = 60


class _ToolCallTimeout(AssertionError):
    """A sweep deadline failed; it is not a product exception to classify as a raiser."""


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
# roster grew since, the `knowledge_*` tools this table's fixture rows add answer inside the
# envelope and write nothing, and the boundary case below re-measures the census at run time
# rather than comparing against this number.
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
        # Match the repository root the persisted settings resolve from workspaceRoot and id.
        self.code = self.coord / REPO
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
                    "workspaceRoot": self.coord.as_posix(),
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
                workspace_root=self.coord,
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
        # The knowledge family's benign branch: a memory root this world does not have. The
        # surface answers a selection it cannot read inside its own envelope rather than raising
        # (see the knowledge block at the end of this table), so the branch is reachable and
        # hermetic without a memory tree -- and the path is inside the coordination root so
        # nothing is touched outside it even if a handler resolved it.
        absent_memory_root = (self.coord / "temp" / "knowledge" / "absent-memory-root").as_posix()
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
            # This server is unbound: role tools refuse before reading, starting or sending.
            "role_start": {
                "role": "worker",
                "request_id": "00000000-0000-4000-8000-000000000001",
                "sprint_document_ref": self.task_ref,
                "master_document_ref": self.task_ref,
                "task_document_ref": self.leaf_ref,
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
            # -- knowledge ----------------------------------------------------------------
            # The three `knowledge_*` operations over a memory tree (MIK-R26 rule 5). This fixture
            # has no memory tree: `knowledge_read` and `knowledge_integrity_check` name an absent
            # root, which the surface answers inside the envelope (`state: "refused"` with a
            # shipped refusal code); `knowledge_diff` refuses the same absent root.
            "knowledge_read": {"memoryRoot": absent_memory_root, "view": "source_context"},
            "knowledge_diff": {"memoryRoot": absent_memory_root, "beforeRevision": "HEAD"},
            "knowledge_integrity_check": {
                "memoryRoot": absent_memory_root,
                "codeRoot": self.coord.as_posix(),
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
