"""Behavioural coverage for eight serving helpers whose error and edge arms were untested.

Every class here drives the real helper over real inputs and asserts the value it returns, the
side effect it leaves, or the error it raises. Fakes stop at the process/network seam only: the
terminal host (tmux kill), the daemon's TCP probe and pid probe, and the Claude stream transport.
The git repositories, the terminal catalog, the FastAPI app and the contracts are all real.

The arms these add, per helper:

* ``app._looks_like_image`` -- every accepted signature, the cross-format mismatches (a body of
  one format under another's extension), the too-short WEBP, and the rejected-extension case.
* ``app._retire_response`` -- unknown target, unknown actor, already-retired, the policy refusal,
  and the granted retire with its tmux kill + persisted provenance.
* ``changeset.leaf_file_diff`` -- the memory side, the missing-worktree and no-head refusals, and
  the added/deleted files where one side of the diff is absent.
* ``claude_stream_capabilities._select_current_model`` -- exact-key precedence, a requested key
  that resolves to nothing, the sole-alias fallback, and both unresolvable shapes.
* ``claude_stream_state.ClaudeStreamState._status_activity`` -- compacting, requesting, blocked
  behind a pending interaction, idle with no turn, and running with one accepted.
* ``daemon._wait_ready`` -- the wildcard-bind rewrite, a dead child, the retry cadence, the
  expired budget, and a real listening socket for the ready case.
* ``harness_control_client._evidence_page`` -- every malformed-response refusal plus the empty
  page and the optional per-frame identity fields.
* ``heap_diag._frames`` -- the default, the override (through the tracer it configures), garbage,
  a non-positive value, and the ambient-environment read.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import tracemalloc
import types
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest import mock

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessBridgeEpochMismatchError, HarnessControlError
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving import daemon, heap_diag
from agents_remember.serving.app import (
    LiveProjectionInputs,
    ServingCollaborators,
    _looks_like_image,
    create_app,
)
from agents_remember.serving.changeset import ChangesetFileRef, leaf_file_diff
from agents_remember.serving.claude_stream_capabilities import _select_current_model
from agents_remember.serving.claude_stream_limits import ClaudeAdapterLimits
from agents_remember.serving.claude_stream_state import ClaudeStreamSession, ClaudeStreamState
from agents_remember.serving.claude_stream_transport import ClaudeStreamTransport
from agents_remember.serving.harness_capabilities import ModelCapability
from agents_remember.serving.harness_control_client import _evidence_page
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    ControlOperationRef,
    PendingInteraction,
    PromptRequest,
    ShutdownMode,
)
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract

NOW = "2026-07-31T10:00:00+00:00"
SESSION_ID = "33333333-3333-4333-8333-333333333333"
CORRELATION_ID = "44444444-4444-4444-8444-444444444444"

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
GIF87 = b"GIF87a\x01\x00\x01\x00\x80\x00\x00"
GIF89 = b"GIF89a\x01\x00\x01\x00\x80\x00\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
BMP = b"BM\x8a\x00\x00\x00\x00\x00\x00\x00"


def _config(tmp: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


class ImageSniffTests(unittest.TestCase):
    """The magic-byte half of the paste-upload gate: the extension alone never admits a body."""

    def test_every_accepted_extension_admits_its_own_signature(self) -> None:
        cases = (
            ("png", PNG),
            ("jpg", JPEG),
            ("jpeg", JPEG),
            ("gif", GIF87),
            ("gif", GIF89),
            ("webp", WEBP),
        )
        for ext, body in cases:
            self.assertTrue(_looks_like_image(body, ext), f"{ext} rejected its own signature")

    def test_a_body_of_one_format_is_refused_under_another_extension(self) -> None:
        # Defence in depth is the point of the sniff: renaming a GIF to .png must not pass.
        bodies = {"png": PNG, "jpeg": JPEG, "gif": GIF89, "webp": WEBP}
        for name, body in bodies.items():
            for ext in bodies:
                if ext == name:
                    continue
                self.assertFalse(_looks_like_image(body, ext), f"{name} body admitted as .{ext}")

    def test_a_truncated_webp_header_is_refused(self) -> None:
        # The RIFF container needs 12 bytes before the WEBP tag can be read at all.
        self.assertFalse(_looks_like_image(b"RIFF\x24\x00\x00\x00WEB", "webp"))
        self.assertFalse(_looks_like_image(b"RIFF", "webp"))
        # Right container, wrong payload tag (e.g. a WAV) -- refused.
        self.assertFalse(_looks_like_image(b"RIFF\x24\x00\x00\x00WAVEfmt ", "webp"))

    def test_an_empty_body_is_refused_for_every_accepted_extension(self) -> None:
        for ext in ("png", "jpg", "jpeg", "gif", "webp"):
            self.assertFalse(_looks_like_image(b"", ext), ext)

    def test_an_extension_outside_the_vision_set_is_refused_even_with_a_valid_header(self) -> None:
        # BMP is the documented WSL clipboard-paste failure; a real BMP body still gets no sniff.
        self.assertFalse(_looks_like_image(BMP, "bmp"))
        self.assertFalse(_looks_like_image(PNG, "svg"))
        self.assertFalse(_looks_like_image(PNG, ""))


class _RecordingTerminalHost:
    """The one member the retire path reaches on the host: the tmux kill."""

    def __init__(self) -> None:
        self.terminated: list[tuple[str, str | None]] = []

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        self.terminated.append((sid, tmux_name))


def _seat(session_id: str, *, role: str, leaf_key: str | None, cwd: Path) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"seat {session_id}",
        kind="harness",
        harness="claude",
        lifecycle_id=None,
        cwd=cwd,
        tmux_name=f"ar-{session_id}",
        command=("claude",),
        created_at=NOW,
        last_attached_at=NOW,
        status="running",
        leaf_key=leaf_key,
        seat_role=role,
    )


class RetireResponseTests(unittest.TestCase):
    """The server-authoritative retire surface, driven over the real route and catalog."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)
        self.host = _RecordingTerminalHost()
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.client = TestClient(
            create_app(
                _config(self.tmp),
                cadence=ProjectionCadence(interval=100),
                live_inputs=LiveProjectionInputs(change_watch=False),
                collaborators=ServingCollaborators(
                    terminal_host=cast(TerminalHost, self.host), terminal_catalog=self.catalog
                ),
            )
        )
        self.addCleanup(self.client.close)
        self.catalog.upsert(
            _seat("mgr", role="manager", leaf_key="repo/master-a/master", cwd=self.tmp)
        )
        self.catalog.upsert(
            _seat("worker", role="worker", leaf_key="repo/master-a/leaf-1", cwd=self.tmp)
        )
        self.catalog.upsert(
            _seat("foreign", role="worker", leaf_key="repo/master-b/leaf-9", cwd=self.tmp)
        )

    def _retire(self, session: str, actor: str, reason: str = "manual retire"):
        return self.client.post(
            f"/api/terminal/{session}/retire",
            json={"actorSession": actor, "reason": reason},
        )

    def test_unknown_target_is_404_and_kills_nothing(self) -> None:
        response = self._retire("ghost", "mgr")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "unknown-session"})
        self.assertEqual(self.host.terminated, [])

    def test_unknown_actor_is_404_naming_the_actor_and_kills_nothing(self) -> None:
        response = self._retire("worker", "ghost-actor")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(), {"status": "unknown-actor", "actorSession": "ghost-actor"}
        )
        self.assertEqual(self.host.terminated, [])
        entry = self.catalog.get("worker")
        assert entry is not None
        self.assertEqual(entry.status, "running")

    def test_a_seat_never_retires_itself(self) -> None:
        response = self._retire("mgr", "mgr")
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["status"], "retire-refused")
        self.assertIn("never retires itself", body["detail"])
        self.assertEqual(self.host.terminated, [])
        entry = self.catalog.get("mgr")
        assert entry is not None
        self.assertEqual((entry.status, entry.retired_at), ("running", None))

    def test_a_manager_cannot_retire_a_seat_of_another_master(self) -> None:
        response = self._retire("foreign", "mgr")
        self.assertEqual(response.status_code, 403)
        detail = response.json()["detail"]
        self.assertIn("master-a", detail)
        self.assertIn("master-b", detail)
        self.assertEqual(self.host.terminated, [])
        entry = self.catalog.get("foreign")
        assert entry is not None
        self.assertEqual(entry.status, "running")

    def test_a_granted_retire_kills_tmux_and_persists_the_provenance(self) -> None:
        response = self._retire("worker", "mgr", reason="leaf landed")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["session"], "worker")
        self.assertEqual(body["status"], "retired")
        self.assertEqual(body["retiredBySession"], "mgr")
        self.assertEqual(body["retiredReason"], "leaf landed")
        self.assertEqual(body["retiredEdge"], "manual")
        self.assertTrue(body["retiredAt"])
        # The tmux session is killed by name, and the row carries the terminal mark.
        self.assertEqual(self.host.terminated, [("worker", "ar-worker")])
        entry = self.catalog.get("worker")
        assert entry is not None
        self.assertEqual(entry.status, "terminated")
        self.assertEqual(entry.retired_by_session, "mgr")
        self.assertEqual(entry.retired_reason, "leaf landed")
        self.assertEqual(entry.retired_at, body["retiredAt"])

    def test_retiring_an_already_retired_seat_is_idempotent_and_kills_nothing_twice(self) -> None:
        first = self._retire("worker", "mgr", reason="leaf landed")
        self.assertEqual(first.status_code, 200)
        second = self._retire("worker", "mgr", reason="second try")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            second.json(),
            {
                "session": "worker",
                "status": "already-retired",
                "retiredAt": first.json()["retiredAt"],
            },
        )
        # The second call must not re-kill tmux nor overwrite the first retirement's reason.
        self.assertEqual(self.host.terminated, [("worker", "ar-worker")])
        entry = self.catalog.get("worker")
        assert entry is not None
        self.assertEqual(entry.retired_reason, "leaf landed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.invalid")
    _git(repo, "config", "user.name", "T")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repo.as_posix()}", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class CodeSide:
    """A leaf contract's ``code:`` side as a file diff reads it.

    The repository, the commit it forked from, the worktree the leaf works in, and the commit
    the leaf recorded there. A diff never needs one without the others -- ``committed`` reads
    base..commit, ``working`` reads the worktree -- so the fixture states the side once.
    """

    repo: Path
    base: str
    worktree: Path
    commit: str = ""


@dataclass(frozen=True)
class MemorySide:
    """A leaf contract's ``memory:`` side: the repository, its fork point, and the content
    commit the leaf recorded. Absent entirely on a memory-less leaf, which is why it is one
    optional object rather than three arguments that could disagree."""

    repo: Path
    base: str
    commit: str = ""


class LeafFileDiffTests(unittest.TestCase):
    """BEFORE/AFTER for one file of a leaf change-set, over real git repositories."""

    MASTER = "t"

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = Path(self._dir.name)
        self.config = _config(self.tmp / "coord")

    def _write_leaf(
        self,
        leaf_id: str,
        *,
        code: CodeSide,
        memory: MemorySide | None = None,
    ) -> None:
        task_root = self.config.coordination_root / "tasks" / "R" / self.MASTER
        path = task_root / "enclosures" / leaf_id / "series-contract.md"
        write_contract(
            path,
            WorktreeContract(
                task_id="T",
                task_name=self.MASTER,
                repo_name="R",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=self.config.coordination_root,
                task_root=task_root,
                contract_path=path,
                task_artifact=task_root / "task.md",
                worktree_group=path.parent,
                code_repo_path=code.repo,
                code_source_branch="main",
                code_work_branch="work",
                code_base_commit=code.base,
                code_commit=code.commit,
                code_worktree=code.worktree,
                memory_repo_path=memory.repo if memory else None,
                memory_base_commit=memory.base if memory else "",
                memory_content_commit=memory.commit if memory else "",
                kind="leaf",
                leaf_id=leaf_id,
                parent_task_name=self.MASTER,
            ),
        )

    def _code_repo(self) -> tuple[Path, str, str]:
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "f.py").write_text("a\nb\n", encoding="utf-8")
        commit = _commit_all(code, "leaf work")
        return code, base, commit

    def test_an_unknown_leaf_names_the_leaf_it_could_not_resolve(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            leaf_file_diff(
                self.config,
                ChangesetFileRef(
                    repo="R",
                    master=self.MASTER,
                    leaf="nope",
                    kind="code",
                    path="f.py",
                    mode="committed",
                ),
            )
        self.assertIn("no leaf contract", str(ctx.exception))
        self.assertIn("nope", str(ctx.exception))

    def test_working_mode_refuses_a_leaf_whose_worktree_is_gone(self) -> None:
        code, base, commit = self._code_repo()
        self._write_leaf("l-gone", code=CodeSide(code, base, self.tmp / "x", commit))
        with self.assertRaises(FileNotFoundError) as ctx:
            leaf_file_diff(
                self.config,
                ChangesetFileRef(
                    repo="R",
                    master=self.MASTER,
                    leaf="l-gone",
                    kind="code",
                    path="f.py",
                    mode="working",
                ),
            )
        self.assertIn("no live worktree", str(ctx.exception))

    def test_committed_mode_refuses_when_there_is_no_head_to_diff_against(self) -> None:
        # In-flight leaf: nothing committed yet AND no live worktree to read a HEAD from, so
        # there is no second commit to diff -- the file is reported missing, not diffed empty.
        code, base, _ = self._code_repo()
        self._write_leaf("l-nohead", code=CodeSide(code, base, self.tmp / "x"))
        with self.assertRaises(FileNotFoundError) as ctx:
            leaf_file_diff(
                self.config,
                ChangesetFileRef(
                    repo="R",
                    master=self.MASTER,
                    leaf="l-nohead",
                    kind="code",
                    path="f.py",
                    mode="committed",
                ),
            )
        self.assertEqual(str(ctx.exception), "f.py")

    def test_the_memory_side_refuses_when_the_leaf_has_no_memory_repository(self) -> None:
        code, base, commit = self._code_repo()
        self._write_leaf("l-nomem", code=CodeSide(code, base, code, commit))
        with self.assertRaises(FileNotFoundError) as ctx:
            leaf_file_diff(
                self.config,
                ChangesetFileRef(
                    repo="R",
                    master=self.MASTER,
                    leaf="l-nomem",
                    kind="memory",
                    path="m.md",
                    mode="committed",
                ),
            )
        self.assertEqual(str(ctx.exception), "m.md")

    def test_the_memory_side_diffs_the_memory_repository_not_the_code_one(self) -> None:
        code, code_base, code_commit = self._code_repo()
        memory = self.tmp / "memory"
        _init_repo(memory)
        (memory / "notes.md").write_text("before\n", encoding="utf-8")
        memory_base = _commit_all(memory, "memory base")
        (memory / "notes.md").write_text("before\nafter\n", encoding="utf-8")
        memory_commit = _commit_all(memory, "memory work")
        self._write_leaf(
            "l-mem",
            code=CodeSide(code, code_base, code, code_commit),
            memory=MemorySide(memory, memory_base, memory_commit),
        )
        body = leaf_file_diff(
            self.config,
            ChangesetFileRef(
                repo="R",
                master=self.MASTER,
                leaf="l-mem",
                kind="memory",
                path="notes.md",
                mode="committed",
            ),
        )
        self.assertEqual(body["kind"], "memory")
        self.assertEqual(body["path"], "notes.md")
        self.assertEqual(body["before"]["content"], "before\n")
        self.assertEqual(body["after"]["content"], "before\nafter\n")
        self.assertEqual(body["scope"], "l-mem")

    def test_working_mode_reports_an_absent_side_for_added_and_deleted_files(self) -> None:
        code = self.tmp / "code"
        _init_repo(code)
        (code / "f.py").write_text("a\n", encoding="utf-8")
        (code / "doomed.py").write_text("gone soon\n", encoding="utf-8")
        base = _commit_all(code, "base")
        (code / "doomed.py").unlink()  # deleted in the dirty tree -> no AFTER
        (code / "fresh.py").write_text("new\n", encoding="utf-8")  # untracked -> no BEFORE
        self._write_leaf("l-work", code=CodeSide(code, base, code))

        deleted = leaf_file_diff(
            self.config,
            ChangesetFileRef(
                repo="R",
                master=self.MASTER,
                leaf="l-work",
                kind="code",
                path="doomed.py",
                mode="working",
            ),
        )
        self.assertEqual(deleted["before"]["content"], "gone soon\n")
        self.assertIsNone(deleted["after"])

        added = leaf_file_diff(
            self.config,
            ChangesetFileRef(
                repo="R",
                master=self.MASTER,
                leaf="l-work",
                kind="code",
                path="fresh.py",
                mode="working",
            ),
        )
        self.assertIsNone(added["before"])
        self.assertEqual(added["after"]["content"], "new\n")


def _model(key: str, *, resolved: str | None = None, is_default: bool = False) -> ModelCapability:
    return ModelCapability(
        key=key,
        display_name=key,
        effort_options=(),
        default_effort=None,
        resolved_model=resolved,
        is_default=is_default,
    )


class SelectCurrentModelTests(unittest.TestCase):
    """Mapping the model id the running harness echoes back onto one catalog key."""

    def test_an_exact_key_match_wins_over_an_alias_that_resolves_to_it(self) -> None:
        models = (
            _model("sonnet", resolved="claude-sonnet-5"),
            _model("default", resolved="sonnet", is_default=True),
        )
        self.assertEqual(_select_current_model(models, "sonnet").key, "sonnet")

    def test_a_requested_key_that_resolves_elsewhere_falls_back_to_the_default_collapse(
        self,
    ) -> None:
        models = (
            _model("default", resolved="claude-opus-4-8[1m]", is_default=True),
            _model("opus[1m]", resolved="claude-opus-4-8[1m]"),
            _model("haiku", resolved="claude-haiku-4"),
        )
        # ``haiku`` does not resolve to the echoed id, so it cannot claim this launch.
        selected = _select_current_model(models, "claude-opus-4-8[1m]", requested_key="haiku")
        self.assertEqual(selected.key, "default")

    def test_a_sole_alias_is_selected_even_though_it_is_not_the_default(self) -> None:
        models = (
            _model("default", resolved="claude-sonnet-5", is_default=True),
            _model("opus[1m]", resolved="claude-opus-4-8[1m]"),
        )
        selected = _select_current_model(models, "claude-opus-4-8[1m]")
        self.assertEqual(selected.key, "opus[1m]")

    def test_an_echoed_model_absent_from_the_catalog_is_refused(self) -> None:
        models = (_model("default", resolved="claude-sonnet-5", is_default=True),)
        with self.assertRaises(HarnessControlError) as ctx:
            _select_current_model(models, "claude-something-else")
        self.assertIn("claude-something-else", str(ctx.exception))
        self.assertIn("absent from list_models", str(ctx.exception))

    def test_two_non_default_aliases_with_no_requested_key_are_refused_rather_than_guessed(
        self,
    ) -> None:
        models = (
            _model("opus[1m]", resolved="claude-opus-4-8[1m]"),
            _model("opus", resolved="claude-opus-4-8[1m]"),
        )
        with self.assertRaises(HarnessControlError):
            _select_current_model(models, "claude-opus-4-8[1m]")


class _FakeClaudeTransport:
    """The stream-json process seam: a frame queue in, a recorded frame list out."""

    def __init__(self) -> None:
        self.frames: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.writes: list[dict[str, object]] = []
        self.launches: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
        self.stop_modes: list[ShutdownMode] = []
        self._written = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return None

    async def start(self, argv: tuple[str, ...], *, cwd: Path, env: Mapping[str, str]) -> None:
        self.launches.append((argv, cwd, dict(env)))

    async def read_frame(self) -> dict[str, object] | None:
        return await self.frames.get()

    async def write_frame(self, frame: Mapping[str, object], *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        self.writes.append(dict(frame))
        self._written.set()

    async def stop(self, mode: ShutdownMode) -> None:
        self.stop_modes.append(mode)
        self.frames.put_nowait(None)

    def feed(self, frame: dict[str, object]) -> None:
        self.frames.put_nowait(frame)

    async def wait_for_writes(self, count: int) -> None:
        while len(self.writes) < count:
            self._written.clear()
            if len(self.writes) < count:
                await asyncio.wait_for(self._written.wait(), timeout=2.0)


def _status_frame(status: str) -> dict[str, object]:
    return {"type": "system", "subtype": "status", "status": status, "session_id": SESSION_ID}


class ClaudeStatusActivityTests(unittest.IsolatedAsyncioTestCase):
    """A native ``system/status`` frame decides the seat's activity; each arm is distinct."""

    async def _state(
        self, *, pending: PendingInteraction | None = None
    ) -> tuple[ClaudeStreamState, _FakeClaudeTransport]:
        transport = _FakeClaudeTransport()
        identity = ControlIdentity("ar-session", "ar-tmux", NOW)
        state = ClaudeStreamState(
            ClaudeStreamSession(
                identity=identity,
                snapshot=AdapterSnapshot(
                    identity=identity,
                    control="ready",
                    activity="idle",
                    acceptance="unknown",
                    vendor_session_id=SESSION_ID,
                    pending_interaction=pending,
                ),
                transport=cast(ClaudeStreamTransport, transport),
                supported_commands=frozenset(),
            ),
            clock=lambda: NOW,
            correlation_factory=lambda: CORRELATION_ID,
            limits=ClaudeAdapterLimits(),
        )
        state.start_reader()
        self.addAsyncCleanup(state.finish_reader, "forced")
        return state, transport

    async def _settle(self) -> None:
        for _ in range(6):
            await asyncio.sleep(0)

    async def test_a_compacting_status_settles_the_seat(self) -> None:
        state, transport = await self._state()
        transport.feed(_status_frame("compacting"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "settling")

    async def test_a_requesting_status_runs_the_seat(self) -> None:
        state, transport = await self._state()
        transport.feed(_status_frame("requesting"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "running")

    async def test_an_unremarkable_status_with_no_turn_in_flight_is_idle(self) -> None:
        state, transport = await self._state()
        transport.feed(_status_frame("compacting"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "settling")
        transport.feed(_status_frame("ready"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "idle")

    async def test_a_pending_interaction_keeps_the_seat_blocked_but_compaction_still_wins(
        self,
    ) -> None:
        pending = PendingInteraction(
            interaction_id="int-1",
            kind="permission",
            prompt="allow?",
            created_at=NOW,
        )
        state, transport = await self._state(pending=pending)
        transport.feed(_status_frame("ready"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "blocked")
        # compacting/requesting are checked before the block, so a compaction still reads settling.
        transport.feed(_status_frame("compacting"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "settling")

    async def test_an_accepted_turn_makes_an_unremarkable_status_read_running(self) -> None:
        state, transport = await self._state()
        submission = asyncio.create_task(
            state.submit(
                PromptRequest(
                    request_id="req-1",
                    source="cockpit",
                    text="hello",
                    submitted_at=NOW,
                    operation=ControlOperationRef(
                        bridge_epoch="epoch-1",
                        sequence=1,
                        operation_id="op-1",
                        kind="prompt",
                    ),
                )
            )
        )
        await transport.wait_for_writes(1)
        # The harness echoes the submitted frame back as its replay acknowledgement.
        transport.feed({**transport.writes[0], "isReplay": True, "timestamp": NOW})
        receipt = await asyncio.wait_for(submission, timeout=5.0)
        self.assertEqual(receipt.acceptance, "immediate")
        # Reducing frames never touches process lifecycle -- that belongs to the adapter.
        self.assertEqual((transport.launches, transport.stop_modes), ([], []))

        transport.feed(_status_frame("compacting"))
        await self._settle()
        self.assertEqual(state.snapshot.activity, "settling")
        transport.feed(_status_frame("ready"))
        await self._settle()
        # The accepted turn is still in flight, so the seat returns to running -- never idle.
        self.assertEqual(state.snapshot.activity, "running")


class _Connector:
    """A ``socket.create_connection`` stand-in: records addresses, refuses the first N calls."""

    def __init__(self, *, failures: int = 0) -> None:
        self.addresses: list[tuple[str, int]] = []
        self.timeouts: list[float | None] = []
        self._failures = failures

    def __call__(self, address: tuple[str, int], timeout: float | None = None):
        self.addresses.append(address)
        self.timeouts.append(timeout)
        if self._failures != 0:
            if self._failures > 0:
                self._failures -= 1
            raise OSError("connection refused")
        return _NullConnection()


class _NullConnection:
    def __enter__(self) -> _NullConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _StepClock:
    """A monotonic clock that only advances when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _state(host: str, port: int) -> daemon.DaemonState:
    return daemon.DaemonState(
        pid=os.getpid(),
        host=host,
        port=port,
        version="3.0.0",
        config_path="/abs/settings.json",
        log_path="/abs/dashboard.log",
        started_at=NOW,
    )


class WaitReadyTests(unittest.TestCase):
    """The spawn readiness poll: alive child AND an accepting bind, within the budget."""

    def test_a_listening_bind_is_ready(self) -> None:
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        # No patching at all: a real pid, a real TCP connect.
        self.assertTrue(daemon._wait_ready(_state("127.0.0.1", port), timeout=5.0))

    def test_a_wildcard_bind_is_probed_on_loopback(self) -> None:
        for host, expected in (
            ("0.0.0.0", "127.0.0.1"),
            ("::", "127.0.0.1"),
            ("192.0.2.7", "192.0.2.7"),
        ):
            connector = _Connector()
            with mock.patch.object(
                daemon, "socket", types.SimpleNamespace(create_connection=connector)
            ):
                self.assertTrue(daemon._wait_ready(_state(host, 8765), timeout=5.0))
            self.assertEqual(connector.addresses, [(expected, 8765)], host)
            # The connect is bounded, so a black-holed bind cannot hang the poll.
            self.assertEqual(connector.timeouts, [0.5], host)

    def test_a_dead_child_is_never_ready_and_is_never_probed(self) -> None:
        connector = _Connector()
        with (
            mock.patch.object(daemon, "_pid_alive", return_value=False),
            mock.patch.object(daemon, "socket", types.SimpleNamespace(create_connection=connector)),
        ):
            self.assertFalse(daemon._wait_ready(_state("127.0.0.1", 8765), timeout=5.0))
        self.assertEqual(connector.addresses, [])

    def test_a_refused_bind_is_retried_on_a_quarter_second_cadence(self) -> None:
        connector = _Connector(failures=1)
        clock = _StepClock()
        with (
            mock.patch.object(daemon, "socket", types.SimpleNamespace(create_connection=connector)),
            mock.patch.object(daemon, "time", clock),
        ):
            self.assertTrue(daemon._wait_ready(_state("127.0.0.1", 8765), timeout=5.0))
        self.assertEqual(len(connector.addresses), 2)
        self.assertEqual(clock.slept, [0.25])

    def test_a_bind_that_never_accepts_gives_up_at_the_budget(self) -> None:
        connector = _Connector(failures=-1)  # never accepts
        clock = _StepClock()
        with (
            mock.patch.object(daemon, "socket", types.SimpleNamespace(create_connection=connector)),
            mock.patch.object(daemon, "time", clock),
        ):
            self.assertFalse(daemon._wait_ready(_state("127.0.0.1", 8765), timeout=0.5))
        # 0.5s of budget spent as two 0.25s waits, then the loop exits rather than polling on.
        self.assertEqual(clock.slept, [0.25, 0.25])
        self.assertEqual(len(connector.addresses), 2)

    def test_an_expired_budget_returns_not_ready_without_touching_the_network(self) -> None:
        connector = _Connector()
        with mock.patch.object(
            daemon, "socket", types.SimpleNamespace(create_connection=connector)
        ):
            self.assertFalse(daemon._wait_ready(_state("127.0.0.1", 8765), timeout=0.0))
        self.assertEqual(connector.addresses, [])


def _evidence_response(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "bridgeEpoch": "epoch-1",
        "latestSequence": 2,
        "evictedBeforeSequence": 0,
        "truncated": False,
        "frames": [
            {"sequence": 1, "kind": "state", "createdAt": NOW, "raw": {"a": 1}},
            {"sequence": 2, "kind": "state", "createdAt": NOW, "raw": {"a": 2}},
        ],
    }
    body.update(overrides)
    return body


class EvidencePageTests(unittest.TestCase):
    """Parsing one bounded evidence page: every malformed shape fails loudly and typed."""

    def test_a_well_formed_page_carries_its_coordinates_and_frames(self) -> None:
        page = _evidence_page(_evidence_response(), expected_bridge_epoch="epoch-1")
        self.assertEqual(page.bridge_epoch, "epoch-1")
        self.assertEqual(page.latest_sequence, 2)
        self.assertEqual(page.evicted_before_sequence, 0)
        self.assertFalse(page.truncated)
        self.assertEqual([frame.sequence for frame in page.frames], [1, 2])
        self.assertEqual(page.frames[0].raw, {"a": 1})
        self.assertIsNone(page.frames[0].native_method)
        self.assertIsNone(page.frames[0].thread_id)

    def test_an_empty_page_is_accepted_and_keeps_the_high_water_mark(self) -> None:
        page = _evidence_page(
            _evidence_response(
                frames=[], latestSequence=9, evictedBeforeSequence=9, truncated=True
            ),
            expected_bridge_epoch=None,
        )
        self.assertEqual(page.frames, ())
        self.assertEqual((page.latest_sequence, page.evicted_before_sequence), (9, 9))
        self.assertTrue(page.truncated)
        self.assertEqual(page.bridge_epoch, "epoch-1")

    def test_a_non_object_response_is_refused(self) -> None:
        with self.assertRaises(HarnessControlError) as ctx:
            _evidence_page(["not", "a", "mapping"], expected_bridge_epoch=None)
        self.assertIn("must be an object", str(ctx.exception))

    def test_a_replaced_bridge_generation_is_refused_by_epoch(self) -> None:
        with self.assertRaises(HarnessBridgeEpochMismatchError) as ctx:
            _evidence_page(_evidence_response(), expected_bridge_epoch="epoch-2")
        self.assertEqual((ctx.exception.expected, ctx.exception.actual), ("epoch-2", "epoch-1"))

    def test_a_non_boolean_truncated_flag_is_refused(self) -> None:
        with self.assertRaises(HarnessControlError) as ctx:
            _evidence_page(_evidence_response(truncated="yes"), expected_bridge_epoch=None)
        self.assertIn("truncated must be boolean", str(ctx.exception))

    def test_frames_that_are_not_a_list_are_refused(self) -> None:
        with self.assertRaises(HarnessControlError) as ctx:
            _evidence_page(_evidence_response(frames={"sequence": 1}), expected_bridge_epoch=None)
        self.assertIn("requires frames", str(ctx.exception))

    def test_a_non_object_frame_is_refused(self) -> None:
        with self.assertRaises(HarnessControlError) as ctx:
            _evidence_page(_evidence_response(frames=["frame"]), expected_bridge_epoch=None)
        self.assertIn("frame must be an object", str(ctx.exception))

    def test_repeated_or_reversed_frame_sequences_are_refused(self) -> None:
        repeated = _evidence_response(
            frames=[
                {"sequence": 4, "kind": "state", "createdAt": NOW},
                {"sequence": 4, "kind": "state", "createdAt": NOW},
            ],
            latestSequence=4,
        )
        with self.assertRaises(HarnessControlError) as ctx:
            _evidence_page(repeated, expected_bridge_epoch=None)
        self.assertIn("increase monotonically", str(ctx.exception))

    def test_a_latest_sequence_behind_the_last_frame_is_refused(self) -> None:
        with self.assertRaises(HarnessControlError) as ctx:
            _evidence_page(_evidence_response(latestSequence=1), expected_bridge_epoch=None)
        self.assertIn("latestSequence precedes its last frame", str(ctx.exception))

    def test_the_optional_identity_fields_are_carried_verbatim_or_refused_when_blank(self) -> None:
        carried = _evidence_page(
            _evidence_response(
                frames=[
                    {
                        "sequence": 1,
                        "kind": "native",
                        "createdAt": NOW,
                        "nativeMethod": "session/update",
                        "threadId": "thread-7",
                    }
                ],
                latestSequence=1,
            ),
            expected_bridge_epoch=None,
        )
        self.assertEqual(carried.frames[0].native_method, "session/update")
        self.assertEqual(carried.frames[0].thread_id, "thread-7")
        self.assertEqual(carried.frames[0].raw, {})

        for field, message in (
            ("nativeMethod", "nativeMethod must be non-empty text"),
            ("threadId", "threadId must be non-empty text"),
        ):
            for blank in ("", 7):
                body = _evidence_response(
                    frames=[{"sequence": 1, "kind": "native", "createdAt": NOW, field: blank}],
                    latestSequence=1,
                )
                with self.assertRaises(HarnessControlError) as ctx:
                    _evidence_page(body, expected_bridge_epoch=None)
                self.assertIn(message, str(ctx.exception))


class HeapDiagFramesTests(unittest.TestCase):
    """``AR_HEAP_DIAG_FRAMES`` is the tracemalloc traceback depth; garbage never breaks it."""

    def test_the_default_depth_is_used_when_unset_or_empty(self) -> None:
        self.assertEqual(heap_diag._frames({}), heap_diag.DEFAULT_FRAMES)
        self.assertEqual(heap_diag._frames({"AR_HEAP_DIAG_FRAMES": ""}), heap_diag.DEFAULT_FRAMES)

    def test_a_positive_override_is_honoured(self) -> None:
        self.assertEqual(heap_diag._frames({"AR_HEAP_DIAG_FRAMES": "3"}), 3)

    def test_garbage_and_non_positive_depths_fall_back_to_the_default(self) -> None:
        for bad in ("abc", "2.5", "0", "-4"):
            self.assertEqual(
                heap_diag._frames({"AR_HEAP_DIAG_FRAMES": bad}), heap_diag.DEFAULT_FRAMES, bad
            )

    def test_the_ambient_environment_is_read_when_no_mapping_is_passed(self) -> None:
        with mock.patch.dict(os.environ, {"AR_HEAP_DIAG_FRAMES": "5"}):
            self.assertEqual(heap_diag._frames(), 5)

    def test_the_configured_depth_reaches_the_tracer_it_starts(self) -> None:
        self.assertFalse(tracemalloc.is_tracing())
        self.addCleanup(tracemalloc.stop)
        started = heap_diag.start_heap_tracing({"AR_HEAP_DIAG": "1", "AR_HEAP_DIAG_FRAMES": "4"})
        self.assertTrue(started)
        self.assertTrue(tracemalloc.is_tracing())
        self.assertEqual(tracemalloc.get_traceback_limit(), 4)


if __name__ == "__main__":
    unittest.main()
