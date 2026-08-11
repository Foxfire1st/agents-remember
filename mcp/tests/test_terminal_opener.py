"""Tests for the shared hosted-session opener (``serving.terminal_opener``, slice L2).

The opener is the ONE spawn path both the dashboard route and the agent-facing ``spawn_agent_session``
MCP tool compose over. These tests drive it against a fake host (records the ``ensure`` call, no real
tmux) + a real JSON catalog, asserting the leaf-claim / provenance / env-seed behaviour that both call
paths inherit.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember
from agents_remember.kernel.harnesses import Harness
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.harness_control_runner import parse_runner_config
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.terminal import TerminalSessionBinding, TerminalSessionSpec
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_opener import (
    HOSTED_SESSION_ENV,
    ControlRunnerRequest,
    SpawnKnobs,
    SpawnProvenance,
    TerminalLaunchRequest,
    open_terminal_session,
)
from agents_remember.tasks import TaskDocument, write_task_doc

# The source root of the agents_remember package this test process imported (``.../mcp/src``) --
# what the opener must seed onto the runner spawn's PYTHONPATH.
_DAEMON_PACKAGE_ROOT = str(Path(agents_remember.__file__).resolve().parent.parent)
LEAF_REF = TaskDocumentRef(repository="repo", path="master/leaf-1.json")
MASTER_REF = TaskDocumentRef(repository="repo", path="master/task.json")


def _write_task_tree(root: Path) -> None:
    for directory, body in (
        (
            "sprint",
            {
                "id": "SPRINT",
                "slug": "task",
                "title": "Sprint",
                "kind": "master",
                "repo": "repo",
                "createdAt": "2026-07-04T00:00",
                "orchestrates": ["master"],
            },
        ),
        (
            "master",
            {
                "id": "MASTER",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": "repo",
                "createdAt": "2026-07-04T00:01",
                "subTasks": [
                    {
                        "number": "leaf-1",
                        "name": "Leaf",
                        "file": "leaf-1.md",
                        "status": "inProgress",
                    }
                ],
            },
        ),
    ):
        write_task_doc(root / "tasks/repo" / directory, TaskDocument.model_validate(body))
    write_task_doc(
        root / "tasks/repo/master",
        TaskDocument.model_validate(
            {
                "id": "leaf-1",
                "slug": "leaf-1",
                "title": "Leaf",
                "kind": "subTask",
                "repo": "repo",
                "createdAt": "2026-07-04T00:02",
                "master": "task.md",
            }
        ),
    )


class _FakeHost:
    """A `TerminalHost` duck-type: ``ensure`` records its call and creates a detached-session binding."""

    def __init__(self) -> None:
        self.ensured: list[dict[str, object]] = []
        self.known: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        tmux_name = spec.tmux_name_for(sid)
        self.ensured.append(
            {
                "sid": sid,
                "cwd": spec.cwd,
                "command": spec.command,
                "lifecycle_id": spec.lifecycle_id,
                "suspend_unsafe": spec.suspend_unsafe,
                "env": dict(spec.env or {}),
            }
        )
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


# The opener takes concepts (a launch, its knobs, its control wiring, the seat provenance); these
# fixtures let each test keep naming the ONE field it is about and route it to the right object.
_KNOB_FIELDS = frozenset({"launch_args", "prompt_keywords", "session_commands"})
_CONTROL_FIELDS = {
    "resolved_launch": "resolved_launch",
    "resume_thread_id": "resume_thread_id",
    "control_endpoint": "endpoint",
    "control_root": "endpoint_root",
}
_PROVENANCE_FIELDS = frozenset(
    {
        "label",
        "lifecycle_id",
        "task_document_ref",
        "replacement_for_task_document_ref",
        "spawned_by_session",
        "spawned_by_lifecycle",
        "spawn_level",
        "spawn_level_source",
    }
)


def _open_session(
    catalog: TerminalCatalog,
    host: _FakeHost,
    session_id: str,
    **fields: object,
):
    knobs = {name: fields.pop(name) for name in list(fields) if name in _KNOB_FIELDS}
    control = {
        _CONTROL_FIELDS[name]: fields.pop(name) for name in list(fields) if name in _CONTROL_FIELDS
    }
    provenance = {name: fields.pop(name) for name in list(fields) if name in _PROVENANCE_FIELDS}
    return open_terminal_session(
        runtime=HostedSessionRuntime(catalog=catalog, host=host),  # type: ignore[arg-type]
        session_id=session_id,
        launch=TerminalLaunchRequest(
            knobs=SpawnKnobs(**knobs),  # type: ignore[arg-type]
            control=ControlRunnerRequest(**control),  # type: ignore[arg-type]
            **fields,  # type: ignore[arg-type]
        ),
        provenance=SpawnProvenance(**provenance),  # type: ignore[arg-type]
    )


def _runner_config(host: _FakeHost):
    command = host.ensured[0]["command"]
    assert isinstance(command, tuple)
    assert command[1:3] == ("-m", "agents_remember.serving.harness_control_runner")
    return parse_runner_config(command[3])


def _running_chat(
    session_id: str,
    *,
    task_document_ref: TaskDocumentRef,
    spawn_role: str | None = None,
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label="Claude Code",
        kind="harness",
        harness="claude",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("claude",),
        created_at="2026-07-04T00:00:00Z",
        last_attached_at="2026-07-04T00:00:00Z",
        status="running",
        task_document_ref=task_document_ref,
        spawn_role=spawn_role,
    )


class OpenTerminalSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        _write_task_tree(self.tmp)
        self.catalog = TerminalCatalog(self.tmp / "logs/dashboard/terminal-sessions.json")
        self.host = _FakeHost()

    def _open(self, **kwargs: object):
        base: dict[str, object] = {
            "kind": "harness",
            "workspace_root": self.tmp,
            "shell": "/bin/bash",
            "harness": "claude",
            "which": _detected,
        }
        base.update(kwargs)
        session_id = str(base.pop("session_id", "worker-1"))
        return _open_session(self.catalog, self.host, session_id, **base)

    def test_opened_records_provenance_env_and_task_document(self) -> None:
        result = self._open(
            task_document_ref=LEAF_REF,
            env={"AR_SPAWN_MODEL": "opus", "AR_SPAWN_EFFORT": "high", "AR_SPAWN_ROLE": "worker"},
            spawned_by_session="manager-9",
            spawned_by_lifecycle="LC-manager",
        )
        self.assertEqual(result.status, "opened")
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.task_document_ref, LEAF_REF)
        self.assertEqual(entry.spawned_by_session, "manager-9")
        self.assertEqual(entry.spawned_by_lifecycle, "LC-manager")
        self.assertEqual(entry.harness, "claude")
        # The AR_SPAWN_ROLE riding the spawn env is recorded on the durable row (L14).
        self.assertEqual(entry.spawn_role, "worker")
        self.assertEqual(entry.binding_role, "worker")
        # The knob env was seeded into the detached tmux spawn, plus the daemon's own package
        # source root on PYTHONPATH so the runner imports the same agents_remember code.
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                "AR_SPAWN_MODEL": "opus",
                "AR_SPAWN_EFFORT": "high",
                "AR_SPAWN_ROLE": "worker",
                HOSTED_SESSION_ENV: "worker-1",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )
        # Provenance survives the catalog round-trip (migration-safe camelCase keys).
        self.assertEqual(entry.to_json()["spawnedBySession"], "manager-9")
        self.assertEqual(entry.to_json()["spawnedByLifecycle"], "LC-manager")
        self.assertEqual(entry.to_json()["spawnRole"], "worker")
        self.assertEqual(entry.to_json()["seatRole"], "worker")
        self.assertEqual(entry.control_state, "starting")
        self.assertIsNotNone(entry.control_endpoint)
        self.assertEqual(entry.control_protocol, "ar-harness-control/v1")

    def test_runner_spawn_env_without_caller_env_is_exactly_the_package_root(self) -> None:
        self._open()
        self.assertEqual(
            self.host.ensured[0]["env"],
            {HOSTED_SESSION_ENV: "worker-1", "PYTHONPATH": _DAEMON_PACKAGE_ROOT},
        )

    def test_runner_spawn_env_prepends_to_a_caller_seeded_pythonpath(self) -> None:
        self._open(env={"PYTHONPATH": "/custom/seed"})
        env = self.host.ensured[0]["env"]
        assert isinstance(env, dict)
        self.assertEqual(env["PYTHONPATH"], f"{_DAEMON_PACKAGE_ROOT}{os.pathsep}/custom/seed")

    def test_plain_terminal_spawn_env_is_left_untouched(self) -> None:
        # Only the harness-control runner needs the daemon's code root; a shell spawn keeps the
        # caller env byte-identical.
        result = self._open(kind="terminal", harness=None, env={"AR_SPAWN_ROLE": "worker"})
        self.assertEqual(result.status, "opened")
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"AR_SPAWN_ROLE": "worker", HOSTED_SESSION_ENV: "worker-1"},
        )

    def test_spawn_env_scrubs_daemon_inherited_identity_residue(self) -> None:
        result = self._open(
            env={
                "AR_SPAWN_MODEL": "opus",
                "AR_SPAWN_EFFORT": "high",
                "CODEX_THREAD_ID": "thread-9",
                "CODEX_CI": "1",
                "CLAUDE_DOC_FOCUS_PATHS": "/docs/a.md",
                "GAME_DATA_SAVES": "/games/saves",
                "KEEP_ME": "value",
            }
        )
        self.assertEqual(result.status, "opened")
        # No AR_SPAWN_ROLE marker: the AR_SPAWN_* knobs and every vendor identity var are the
        # daemon session's residue, not this child's. Only unrelated env survives (+PYTHONPATH).
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                "KEEP_ME": "value",
                HOSTED_SESSION_ENV: "worker-1",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )

    def test_role_spawn_keeps_its_explicit_ar_spawn_values_but_not_vendor_identity(self) -> None:
        result = self._open(
            task_document_ref=LEAF_REF,
            env={
                "AR_SPAWN_ROLE": "worker",
                "AR_SPAWN_MODEL": "opus",
                "AR_SPAWN_EFFORT": "high",
                "CODEX_THREAD_ID": "thread-9",
                "CLAUDE_DOC_FOCUS_PATHS": "/docs/a.md",
                "GAME_DATA_LEVEL": "7",
            },
        )
        self.assertEqual(result.status, "opened")
        # AR_SPAWN_ROLE marks an explicit role spawn: its AR_SPAWN_* values survive; the vendor
        # identity names are still daemon residue and are scrubbed.
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                "AR_SPAWN_ROLE": "worker",
                "AR_SPAWN_MODEL": "opus",
                "AR_SPAWN_EFFORT": "high",
                HOSTED_SESSION_ENV: "worker-1",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )

    def test_plain_terminal_spawn_scrubs_daemon_identity_too(self) -> None:
        result = self._open(
            kind="terminal",
            harness=None,
            env={
                "CODEX_THREAD_ID": "thread-9",
                "CODEX_CI": "1",
                "GAME_DATA_SAVES": "/games/saves",
                "KEEP_ME": "value",
            },
        )
        self.assertEqual(result.status, "opened")
        # The scrub is not runner-specific: a plain shell spawn also drops daemon identity.
        self.assertEqual(
            self.host.ensured[0]["env"],
            {"KEEP_ME": "value", HOSTED_SESSION_ENV: "worker-1"},
        )

    def test_future_bridge_endpoint_is_additive_control_metadata(self) -> None:
        endpoint = self.tmp / "control" / "worker.sock"
        self._open(control_endpoint=endpoint)
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.control_endpoint, endpoint)
        self.assertEqual(entry.to_json()["controlEndpoint"], str(endpoint))

    def test_reopen_preserves_spawn_role_and_hand_open_records_none(self) -> None:
        # Non-named role provenance is set once at first spawn and survives a role-less re-open.
        self._open(task_document_ref=LEAF_REF, env={"AR_SPAWN_ROLE": "worker"})
        self._open()  # re-open with no env — must not drop the recorded role
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.spawn_role, "worker")

        self._open(session_id="hand-opened")
        hand_opened = self.catalog.get("hand-opened")
        assert hand_opened is not None
        self.assertIsNone(hand_opened.spawn_role)
        self.assertNotIn("spawnRole", hand_opened.to_json())

    def test_seat_taken_surfaces_owner_without_spawning(self) -> None:
        self.catalog.upsert(
            _running_chat("owner-1", task_document_ref=LEAF_REF, spawn_role="worker")
        )
        self.host.known.add("ar-owner-1")
        result = self._open(
            session_id="intruder",
            task_document_ref=LEAF_REF,
            env={"AR_SPAWN_ROLE": "worker"},
        )
        self.assertEqual(result.status, "seat-taken")
        self.assertEqual(result.owner_session_id, "owner-1")
        # Never spawned, never upserted the intruder.
        self.assertEqual(self.host.ensured, [])
        self.assertIsNone(self.catalog.get("intruder"))

    def test_different_roles_share_leaf_and_dead_same_role_is_replaced(self) -> None:
        self.catalog.upsert(
            _running_chat("worker", task_document_ref=LEAF_REF, spawn_role="worker")
        )
        self.host.known.add("ar-worker")

        reviewer = self._open(
            session_id="reviewer",
            task_document_ref=LEAF_REF,
            env={"AR_SPAWN_ROLE": "reviewer"},
        )
        self.assertEqual(reviewer.status, "opened")

        self.host.known.discard("ar-worker")
        replacement = self._open(
            session_id="worker-2",
            task_document_ref=LEAF_REF,
            env={"AR_SPAWN_ROLE": "worker"},
        )
        self.assertEqual(replacement.status, "opened")
        prior_worker = self.catalog.get("worker")
        next_worker = self.catalog.get("worker-2")
        reviewer_entry = self.catalog.get("reviewer")
        assert prior_worker is not None and next_worker is not None and reviewer_entry is not None
        self.assertEqual(prior_worker.status, "exited")
        self.assertEqual(next_worker.binding_role, "worker")
        self.assertEqual(reviewer_entry.binding_role, "reviewer")

    def test_pipeline_roles_bind_to_their_canonical_document_altitudes(self) -> None:
        first_worker = self._open(
            session_id="worker-1",
            task_document_ref=LEAF_REF,
            env={"AR_SPAWN_ROLE": "worker"},
        )
        self.assertEqual(first_worker.status, "opened")
        self.catalog.mark_exited("worker-1")

        for session_id, role in (
            ("curator", "curator"),
            ("worker-2", "worker"),
            ("reviewer", "reviewer"),
            ("manager", "manager"),
        ):
            result = self._open(
                session_id=session_id,
                task_document_ref=MASTER_REF if role == "manager" else LEAF_REF,
                env={"AR_SPAWN_ROLE": role},
            )
            self.assertEqual(result.status, "opened")

        live = {
            entry.id: entry.binding_role
            for entry in self.catalog.list()
            if entry.task_document_ref == LEAF_REF and entry.status == "running"
        }
        self.assertEqual(
            live,
            {
                "curator": "curator",
                "worker-2": "worker",
                "reviewer": "reviewer",
            },
        )
        manager = self.catalog.get("manager")
        assert manager is not None
        self.assertEqual(manager.task_document_ref, MASTER_REF)

    def test_bad_kind_reports_detail(self) -> None:
        result = self._open(kind="bogus")
        self.assertEqual(result.status, "bad-kind")
        self.assertIsNotNone(result.detail)
        self.assertEqual(self.host.ensured, [])

    def test_undetected_harness_is_bad_kind(self) -> None:
        result = self._open(which=lambda _cmd: None)
        self.assertEqual(result.status, "bad-kind")
        self.assertEqual(self.host.ensured, [])


class KnobApplicationTests(unittest.TestCase):
    """The opener carries one typed launch to the runner and preserves spawn provenance."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _FakeHost()

    def _open(self, **kwargs: object):
        base: dict[str, object] = {
            "kind": "harness",
            "workspace_root": self.tmp,
            "shell": "/bin/bash",
            "harness": "claude",
            "which": _detected,
        }
        base.update(kwargs)
        session_id = str(base.pop("session_id", "worker-1"))
        return _open_session(self.catalog, self.host, session_id, **base)

    def test_claude_resolved_launch_rides_the_runner_payload(self) -> None:
        resolved = ResolvedLaunch("claude", "claude-fable-5", "max", self.tmp)
        result = self._open(
            env={"AR_SPAWN_MODEL": "claude-fable-5", "AR_SPAWN_EFFORT": "max"},
            resolved_launch=resolved,
        )
        self.assertEqual(result.status, "opened")
        runner = _runner_config(self.host)
        self.assertEqual(runner.argv, ("claude",))
        self.assertEqual(runner.resolved_launch, resolved)
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                HOSTED_SESSION_ENV: "worker-1",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )

    def test_live_reopen_preserves_actual_pair_command_and_endpoint(self) -> None:
        selected = ResolvedLaunch("claude", "model-a", "high", self.tmp)
        first = self._open(resolved_launch=selected)
        assert first.entry is not None
        actual = first.entry

        reopened = self._open(
            resolved_launch=selected,
            label="attempted metadata rewrite",
        )

        self.assertEqual(reopened.status, "opened")
        self.assertEqual(reopened.entry, actual)
        self.assertEqual(self.catalog.get("worker-1"), actual)
        self.assertEqual(len(self.host.ensured), 1)
        self.assertEqual(_runner_config(self.host).resolved_launch, selected)

    def test_live_reopen_changed_pair_or_identity_conflicts_without_mutation(self) -> None:
        original = ResolvedLaunch("claude", "model-a", "high", self.tmp)
        changed = ResolvedLaunch("claude", "model-b", "max", self.tmp)
        first = self._open(resolved_launch=original)
        assert first.entry is not None
        actual = first.entry

        pair_conflict = self._open(resolved_launch=changed)
        identity_conflict = self._open(kind="terminal", harness=None)

        self.assertEqual(pair_conflict.status, "launch-conflict")
        self.assertEqual(identity_conflict.status, "launch-conflict")
        self.assertEqual(pair_conflict.entry, actual)
        self.assertEqual(identity_conflict.entry, actual)
        self.assertEqual(self.catalog.get("worker-1"), actual)
        self.assertEqual(len(self.host.ensured), 1)
        self.assertEqual(_runner_config(self.host).resolved_launch, original)

    def test_live_reopen_from_another_workspace_root_conflicts_on_cwd(self) -> None:
        # A durable tmux session keeps the directory it was created in. Reopening the same session
        # id from a different workspace root is a different process identity, not a relocation, so
        # the opener refuses and names both directories rather than rewriting the row.
        first = self._open()
        assert first.entry is not None
        elsewhere = self.tmp / "other-workspace"
        elsewhere.mkdir()

        conflict = self._open(workspace_root=elsewhere)

        self.assertEqual(conflict.status, "launch-conflict")
        self.assertEqual(conflict.entry, first.entry)
        assert conflict.detail is not None
        self.assertIn(str(self.tmp), conflict.detail)
        self.assertIn(str(elsewhere), conflict.detail)
        self.assertEqual(self.catalog.get("worker-1"), first.entry)
        self.assertEqual(len(self.host.ensured), 1)

    def test_live_reopen_whose_resolved_launch_names_another_harness_conflicts(self) -> None:
        # The resolved launch is the authority the runner applies, so a request whose launch names
        # a different harness than the live row disagrees about what the process IS -- even though
        # the request's own harness id still matches. It is refused before the runner is touched.
        first = self._open()
        assert first.entry is not None

        conflict = self._open(resolved_launch=ResolvedLaunch("codex", "model-a", "high", self.tmp))

        self.assertEqual(conflict.status, "launch-conflict")
        self.assertEqual(conflict.entry, first.entry)
        assert conflict.detail is not None
        self.assertIn("resolved launch requested 'codex'", conflict.detail)
        self.assertEqual(len(self.host.ensured), 1)

    def test_a_dead_pre_bridge_row_is_replaced_by_a_controlled_spawn(self) -> None:
        # A harness row with no control endpoint predates the bridge. Its recorded argv is only
        # authoritative while its process still runs; once tmux no longer has the session there is
        # nothing to preserve, so the replacement is an ordinary controlled spawn under the runner.
        self.catalog.upsert(
            TerminalCatalogEntry(
                id="worker-1",
                label="Claude Code",
                kind="harness",
                harness="claude",
                lifecycle_id=None,
                cwd=self.tmp,
                tmux_name="ar-worker-1",
                command=("claude", "--legacy-flag"),
                created_at="2026-07-04T00:00:00Z",
                last_attached_at="2026-07-04T00:00:00Z",
                status="running",
                control_endpoint=None,
            )
        )

        result = self._open()

        assert result.entry is not None
        self.assertEqual(result.status, "opened")
        self.assertEqual(len(self.host.ensured), 1)
        spawned = self.host.ensured[0]["command"]
        assert isinstance(spawned, tuple)
        self.assertNotEqual(spawned, ("claude", "--legacy-flag"))
        self.assertEqual(_runner_config(self.host).argv, ("claude",))
        self.assertIsNotNone(result.entry.control_endpoint)

    def test_dead_replacement_uses_new_pair_and_fresh_control_generation(self) -> None:
        original = ResolvedLaunch("claude", "model-a", "high", self.tmp)
        changed = ResolvedLaunch("claude", "model-b", "max", self.tmp)
        with mock.patch(
            "agents_remember.serving.terminal_opener.now_iso",
            side_effect=(
                "2026-07-16T08:00:00+00:00",
                "2026-07-16T08:01:00+00:00",
            ),
        ):
            first = self._open(resolved_launch=original)
            assert first.entry is not None
            self.host.known.discard(first.entry.tmux_name)
            replacement = self._open(resolved_launch=changed)

        assert replacement.entry is not None
        self.assertEqual(replacement.status, "opened")
        self.assertEqual(len(self.host.ensured), 2)
        second_command = self.host.ensured[1]["command"]
        assert isinstance(second_command, tuple)
        second_runner = parse_runner_config(second_command[3])
        self.assertEqual(second_runner.resolved_launch, changed)
        self.assertEqual(
            (replacement.entry.resolved_model, replacement.entry.resolved_effort),
            ("model-b", "max"),
        )
        self.assertNotEqual(replacement.entry.control_endpoint, first.entry.control_endpoint)
        self.assertEqual(replacement.entry.created_at, "2026-07-16T08:01:00+00:00")
        self.assertEqual(replacement.entry.control_state, "starting")
        self.assertIsNone(replacement.entry.control_vendor_session_id)
        self.assertIsNone(replacement.entry.control_raw)

    def test_concurrent_different_pair_opens_keep_one_process_and_one_truth(self) -> None:
        selections = (
            ResolvedLaunch("claude", "model-a", "high", self.tmp),
            ResolvedLaunch("claude", "model-b", "max", self.tmp),
        )
        start = threading.Barrier(2)

        def open_after_barrier(selection: ResolvedLaunch):
            start.wait(timeout=2)
            return self._open(resolved_launch=selection)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(open_after_barrier, selection) for selection in selections]
            results = [future.result(timeout=3) for future in futures]

        self.assertEqual({result.status for result in results}, {"opened", "launch-conflict"})
        self.assertEqual(len(self.host.ensured), 1)
        actual = self.catalog.get("worker-1")
        assert actual is not None
        opened = next(result.entry for result in results if result.status == "opened")
        self.assertEqual(actual, opened)
        self.assertTrue(all(result.entry == actual for result in results))
        self.assertEqual(
            _runner_config(self.host).resolved_launch,
            ResolvedLaunch(
                "claude", actual.resolved_model or "", actual.resolved_effort or "", self.tmp
            ),
        )

    def test_no_effort_is_synthesized_as_a_session_command(self) -> None:
        resolved = ResolvedLaunch("claude", "claude-fable-5", "ultracode", self.tmp)
        result = self._open(resolved_launch=resolved)
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).session_commands, ())

    def test_codex_selection_is_structured_not_a_tui_argv_override(self) -> None:
        resolved = ResolvedLaunch("codex", "gpt-5.6-sol", "xhigh", self.tmp)
        result = self._open(
            harness="codex",
            env={"AR_SPAWN_MODEL": "gpt-5.6-sol", "AR_SPAWN_EFFORT": "xhigh"},
            resolved_launch=resolved,
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).argv, ("codex",))
        self.assertEqual(_runner_config(self.host).resolved_launch, resolved)
        self.assertEqual(
            self.host.ensured[0]["env"],
            {
                HOSTED_SESSION_ENV: "worker-1",
                "PYTHONPATH": _DAEMON_PACKAGE_ROOT,
            },
        )

    def test_launch_args_remain_on_the_base_command_before_adapter_preparation(self) -> None:
        result = self._open(
            launch_args=["--dangerously-skip-permissions", "--foo", "bar"],
        )
        self.assertEqual(result.status, "opened")
        self.assertEqual(
            _runner_config(self.host).argv,
            ("claude", "--dangerously-skip-permissions", "--foo", "bar"),
        )

    def test_free_form_provenance_is_recorded_and_round_trips(self) -> None:
        self._open(
            launch_args=["--foo"],
            prompt_keywords=["ultracode"],
            session_commands=["/effort ultracode"],
        )
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.launch_args, ("--foo",))
        self.assertEqual(entry.prompt_keywords, ("ultracode",))
        self.assertEqual(entry.session_commands, ("/effort ultracode",))
        as_json = entry.to_json()
        self.assertEqual(as_json["launchArgs"], ["--foo"])
        self.assertEqual(as_json["promptKeywords"], ["ultracode"])
        self.assertEqual(as_json["sessionCommands"], ["/effort ultracode"])
        # Preserved across a free-form-less re-open (the write-once-preserve provenance rule).
        self._open()
        entry = self.catalog.get("worker-1")
        assert entry is not None
        self.assertEqual(entry.launch_args, ("--foo",))
        # A hand-opened row records none of them (migration-safe absent keys).
        self._open(session_id="hand-opened")
        hand_opened = self.catalog.get("hand-opened")
        assert hand_opened is not None
        self.assertIsNone(hand_opened.launch_args)
        self.assertNotIn("launchArgs", hand_opened.to_json())

    def test_effective_registry_resolves_settings_defined_harness(self) -> None:
        hermes = Harness(
            id="hermes",
            name="Hermes",
            command="hermes",
            argv=("hermes", "--tui"),
            defined_in="settings",
        )
        result = self._open(harness="hermes", harnesses=(hermes,))
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).argv, ("hermes", "--tui"))
        self.assertEqual(result.entry.control_state if result.entry else None, "unsupported")

    def test_unknown_everywhere_harness_refuses_pointing_at_the_manual(self) -> None:
        result = self._open(harness="hermes")
        self.assertEqual(result.status, "bad-kind")
        assert result.detail is not None
        self.assertIn("'hermes'", result.detail)
        self.assertIn("claude, codex, pi", result.detail)
        self.assertIn("orchestration.harnesses", result.detail)
        self.assertIn("docs/reference/harnesses.md", result.detail)
        self.assertEqual(self.host.ensured, [])

    def test_custom_harness_launch_mapping_is_not_guessed_by_the_native_opener(self) -> None:
        hermes = Harness(
            id="hermes", name="Hermes", command="hermes", argv=("hermes",), defined_in="settings"
        )
        result = self._open(harness="hermes", harnesses=(hermes,), env={"AR_SPAWN_EFFORT": "high"})
        self.assertEqual(result.status, "opened")
        self.assertEqual(_runner_config(self.host).argv, ("hermes",))


if __name__ == "__main__":
    unittest.main()
