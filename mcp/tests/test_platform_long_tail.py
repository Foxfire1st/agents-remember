"""Refusals, defaults and degraded paths across the platform's small helpers.

None of these are big enough to earn a module of their own, and every one of them is a
guard: the argument shape a tool refuses, the settings key it rejects by name, the
already-aborted operation it reports instead of raising, the inbox row it renews without
losing the fields the caller did not restate.

They are collected here rather than scattered because they share a shape -- a specific
bad or unusual input, and the exact verdict it produces -- and because a guard nobody
exercises is a guard nobody can show is right.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import memory_tools, read_files
from agents_remember.application.memory_tools import CarryoverSelection
from agents_remember.application.orchestration_tools import (
    NudgeSubject,
    NudgeTarget,
    orchestration_nudge_manager_tool,
)
from agents_remember.application.terminal_tools import (
    OpenTerminalResult,
    _open_terminal_refusal,
    _requested_harness,
)
from agents_remember.benchmarks.runner_modules import execution as benchmark_execution
from agents_remember.benchmarks.runner_modules import mcp_registration as benchmark_mcp
from agents_remember.cli import dashboard as dashboard_cli
from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.operator_inbox_records import (
    InboxSubject,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import InboxRenewal
from agents_remember.install import runtime as runtime_install
from agents_remember.install import skills as skills_install
from agents_remember.install.runtime import (
    InstallSummary,
    ProviderDependencyInstall,
    install_provider_dependencies,
)
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.kernel.harnesses import Harness
from agents_remember.kernel.primitives.gate_policy import (
    coerce_decision_role,
)
from agents_remember.memory import baseline as memory_baseline
from agents_remember.memory.carryover import _validate_entity_fingerprints
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.reducer import _paused_updates
from agents_remember.serving.projections.projection_store import ProviderStateRefresher


class DecisionRoleTests(unittest.TestCase):
    def test_an_unknown_decision_role_is_refused_by_name(self) -> None:
        """Roles come from settings text; a typo must not quietly become a role that no
        policy rule matches."""
        with self.assertRaises(ValueError) as raised:
            coerce_decision_role("mangaer")

        self.assertIn("unknown decision role 'mangaer'", str(raised.exception))

    def test_a_known_decision_role_passes_through(self) -> None:
        self.assertEqual(coerce_decision_role("manager"), "manager")


class ReadFilesRangeTests(unittest.TestCase):
    """``read_ar_files`` line ranges are caller-supplied JSON, so each field is checked."""

    def request(self, source: Any) -> Any:
        return read_files._parse_file_request({"path": "a.py", "source": source})

    def test_a_non_integer_line_range_is_refused(self) -> None:
        with self.assertRaises(read_files.AuthorityError) as raised:
            self.request({"startLine": "1", "endLine": 10})

        self.assertIn("needs integer startLine and endLine", str(raised.exception))

    def test_a_missing_end_line_is_refused(self) -> None:
        with self.assertRaises(read_files.AuthorityError) as raised:
            self.request({"startLine": 1})

        self.assertIn("needs integer startLine and endLine", str(raised.exception))

    def test_a_valid_range_is_accepted(self) -> None:
        request = self.request({"startLine": 2, "endLine": 5})

        self.assertEqual((request.start_line, request.end_line), (2, 5))
        self.assertIs(request.full, False)


class CarryoverRequestTests(unittest.TestCase):
    def test_a_repo_without_a_memory_root_cannot_carry_memory_over(self) -> None:
        """Carryover writes into official memory; a repo with no memory root has nowhere
        to write, and saying so names the repo rather than failing on a None path later."""
        repo = SimpleNamespace(path=Path("/tmp/repo"), memory_root=None)
        config = cast(Any, SimpleNamespace(coordination_root=Path("/tmp/coord")))
        selection = CarryoverSelection(
            repo_id="repo-a",
            source_memory="/tmp/coord/source",
            official_code_ref="main",
            source_code_ref="task",
            old_base="abc123",
        )

        with (
            mock.patch.object(memory_tools, "require_repo", return_value=repo),
            self.assertRaises(ValueError) as raised,
        ):
            memory_tools._carryover_request(config, selection)

        self.assertEqual(str(raised.exception), "repo_id 'repo-a' does not have a memory root")


class NudgeTargetTests(unittest.TestCase):
    def test_a_nudge_with_no_manager_address_is_refused_before_it_is_recorded(self) -> None:
        """A nudge with no mailbox key would be written to the log and delivered nowhere."""
        config = cast(Any, SimpleNamespace(coordination_root=Path("/tmp/coord")))

        with self.assertRaises(ValueError) as raised:
            orchestration_nudge_manager_tool(
                config,
                reason="inactive",
                target=NudgeTarget(),
                subject=NudgeSubject(subject="worker stalled"),
            )

        self.assertIn("manager_agent_id or manager_lifecycle_id", str(raised.exception))


class ReducerPausedTests(unittest.TestCase):
    def test_a_paused_lifecycle_keeps_whatever_ask_it_was_carrying(self) -> None:
        """Unlike resume, pausing only moves the state: a lifecycle paused mid-gate must
        still show the ask it is blocked on when it comes back."""
        updates = _paused_updates(cast(Any, object()), cast(Any, object()))

        self.assertEqual(updates, {"state": "paused"})


class ProviderStateCacheTests(unittest.TestCase):
    """The projection's pre-read provider refresh is best-effort by design."""

    def test_a_failing_refresh_leaves_the_last_snapshot_in_place(self) -> None:
        """Provider status reads touch docker; if that throws, reading the projection must
        still succeed on the previous snapshot rather than failing the whole dashboard."""
        config = cast(Any, SimpleNamespace(providers={"grepai-memory": object()}))
        cache = ProviderStateRefresher(
            refresh=mock.Mock(side_effect=RuntimeError("docker is not running")),
            ttl_seconds=0.0,
        )

        with self.assertLogs(
            "agents_remember.serving.projections.projection_store", level="WARNING"
        ) as logs:
            cache.maybe_refresh(config, now=datetime(2026, 7, 31, 12, 0, tzinfo=UTC))

        self.assertTrue(any("using last snapshot" in line for line in logs.output))

    def test_no_configured_providers_means_no_refresh_at_all(self) -> None:
        refresh = mock.Mock()
        cache = ProviderStateRefresher(refresh=refresh, ttl_seconds=0.0)

        cache.maybe_refresh(
            cast(Any, SimpleNamespace(providers={})),
            now=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        )

        refresh.assert_not_called()


class EntityFingerprintValidationTests(unittest.TestCase):
    def test_an_unsupported_fingerprint_algorithm_is_reported_not_recomputed(self) -> None:
        """A carried catalog is re-derived against the official ref. A row whose algorithm
        this build cannot compute is reported as an error row, and never silently accepted
        as if it had been verified."""
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "entities.md"
            catalog.write_text(
                "\n".join(
                    [
                        "## Entity Fingerprints",
                        "",
                        "| Entity | Algorithm | Fingerprint | Evidence Paths |",
                        "| --- | --- | --- | --- |",
                        "| Widget | sha256-of-vibes | abc123 | `src/widget.py` |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = _validate_entity_fingerprints(Path(tmp), "main", catalog)

        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(
            result["errors"],
            [{"entity": "Widget", "reason": "unsupported algorithm 'sha256-of-vibes'"}],
        )


class MemoryBaselineParserTests(unittest.TestCase):
    def test_the_status_command_accepts_a_drift_report_path(self) -> None:
        """The memory-quality drift report is what proves onboarding current, so the
        baseline CLI must be able to be handed one rather than re-deriving it."""
        parser = memory_baseline.build_parser()

        args = parser.parse_args(
            [
                "status",
                "--code-repository-root",
                "/tmp/repo",
                "--report",
                "/tmp/drift.json",
                "--topology",
                "external",
            ]
        )

        self.assertEqual(args.report, Path("/tmp/drift.json"))
        self.assertEqual(args.topology, "external")


class RetiredEscalationSettingsTests(unittest.TestCase):
    """The ``orchestration.escalation`` ladder family is demolished: any file that sets it
    fails loud as an unknown key."""

    def settings_root(self, escalation: dict[str, Any]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "system").mkdir(parents=True)
        (root / "system" / "settings.json").write_text(
            json.dumps({"version": 1, "orchestration": {"escalation": escalation}}),
            encoding="utf-8",
        )
        return root

    def test_escalation_family_is_refused_loud(self) -> None:
        root = self.settings_root({})

        with self.assertRaises(AgenticSettingsError) as raised:
            load_agentic_settings(root)

        self.assertIn("escalation", str(raised.exception))

    def test_respawn_after_rung_is_refused_with_the_family(self) -> None:
        root = self.settings_root({"respawnAfterRung": 2})

        with self.assertRaises(AgenticSettingsError) as raised:
            load_agentic_settings(root)

        self.assertIn("escalation", str(raised.exception))


class InboxRenewalTests(unittest.TestCase):
    """Renewing a pending row bumps its date without dropping what it already carried."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = OperatorInboxStore(Path(self._tmp.name))

    def seed(self) -> OperatorInboxEntry:
        leaf_ref = TaskDocumentRef(repository="agents-remember", path="master/260731-EFA-L2.json")
        entry = OperatorInboxEntry(
            id="entry-1",
            ts="2026-07-31T10:00:00Z",
            state="pending",
            ask="status?",
            response="green",
            createdAt="2026-07-31T10:00:00Z",
            createdBy="model",
            createdVia="cli",
            agentId="agent-1",
            subjectTaskDocumentRef=leaf_ref,
            seatRole="worker",
            subjectAgentId="agent-2",
        )
        self.store.append(entry)
        return entry

    def test_a_bare_renewal_bumps_the_date_and_keeps_every_other_field(self) -> None:
        """The coalescing primitive exists so a re-firing condition updates one row. A
        renewal that restates nothing must not blank the subject the row already names."""
        seeded = self.seed()

        renewed = inbox_transitions.renew(
            self.store, "entry-1", InboxRenewal(), now="2026-07-31T11:00:00Z"
        )

        self.assertEqual(renewed.id, seeded.id)
        self.assertEqual(renewed.ts, "2026-07-31T11:00:00Z")
        self.assertEqual(renewed.response, "green")
        self.assertEqual(
            renewed.subjectTaskDocumentRef,
            TaskDocumentRef(repository="agents-remember", path="master/260731-EFA-L2.json"),
        )
        self.assertEqual(renewed.seatRole, "worker")
        self.assertEqual(renewed.subjectAgentId, "agent-2")
        self.assertEqual(renewed.state, "pending")

    def test_a_renewal_that_restates_fields_overwrites_only_those(self) -> None:
        self.seed()

        renewed = inbox_transitions.renew(
            self.store,
            "entry-1",
            InboxRenewal(
                response="amber",
                subject=InboxSubject(
                    task_document_ref=TaskDocumentRef(
                        repository="agents-remember", path="master/260731-EFA-L3.json"
                    ),
                    seat_role="reviewer",
                ),
            ),
            now="2026-07-31T11:00:00Z",
        )

        self.assertEqual(renewed.response, "amber")
        self.assertEqual(
            renewed.subjectTaskDocumentRef,
            TaskDocumentRef(repository="agents-remember", path="master/260731-EFA-L3.json"),
        )
        self.assertEqual(renewed.seatRole, "reviewer")
        self.assertEqual(renewed.subjectAgentId, "agent-2")

    def test_renewing_an_unknown_entry_raises(self) -> None:
        with self.assertRaises(KeyError):
            inbox_transitions.renew(
                self.store, "missing", InboxRenewal(), now="2026-07-31T11:00:00Z"
            )

    def test_a_row_that_is_no_longer_pending_is_returned_untouched(self) -> None:
        """Coalescing is for a row still waiting on someone. Once a row has landed, a
        re-firing condition must not reanimate it by bumping its date -- it appends nothing,
        and the caller sees the terminal row back."""
        self.seed()
        inbox_transitions.mark_landed(
            self.store,
            "entry-1",
            now="2026-07-31T10:30:00Z",
            reason="adapter-accepted-at-turn-boundary",
        )
        before = len(self.store.read())

        renewed = inbox_transitions.renew(
            self.store,
            "entry-1",
            InboxRenewal(response="amber"),
            now="2026-07-31T11:00:00Z",
        )

        self.assertEqual(renewed.state, "landed")
        self.assertEqual(renewed.response, "green")
        self.assertEqual(len(self.store.read()), before)


class RequestedHarnessTests(unittest.TestCase):
    """A caller-named harness must be both a known id and actually installed."""

    def registry(self) -> tuple[Harness, ...]:
        return (Harness(id="claude", name="Claude", command="claude", argv=("claude",)),)

    def test_an_unknown_harness_id_refuses_and_points_at_the_registry(self) -> None:
        harness, refusal = _requested_harness("clod", self.registry(), None)

        self.assertIsNone(harness)
        assert refusal is not None
        self.assertEqual(refusal["status"], "harness-unknown")
        self.assertIn("clod", str(refusal["detail"]))

    def test_a_known_but_uninstalled_harness_refuses_separately(self) -> None:
        """ "unknown" and "not installed" are different problems with different fixes, so
        they must not collapse into one status."""
        harness, refusal = _requested_harness("claude", self.registry(), lambda _name: None)

        self.assertIsNone(harness)
        assert refusal is not None
        self.assertEqual(refusal["status"], "harness-not-detected")
        self.assertIn("harness not installed: 'claude'", str(refusal["detail"]))

    def test_a_known_and_installed_harness_is_returned_with_no_refusal(self) -> None:
        harness, refusal = _requested_harness(
            "claude", self.registry(), lambda _name: "/usr/bin/claude"
        )

        self.assertIsNone(refusal)
        assert harness is not None
        self.assertEqual(harness.id, "claude")


class OpenTerminalRefusalTests(unittest.TestCase):
    """Terminal-open outcomes translated into the spawn tool's public statuses."""

    def refuse(self, status: Any, detail: str = "detail text") -> dict[str, Any] | None:
        return _open_terminal_refusal(
            OpenTerminalResult(status=status, detail=detail),
            harness="claude",
            kind="harness",
            session_id="sess-1",
            task_document_ref=TaskDocumentRef(
                repository="agents-remember", path="master/260731-EFA-L2.json"
            ),
        )

    def test_a_bad_kind_is_reported_as_bad_kind(self) -> None:
        refusal = self.refuse("bad-kind")

        assert refusal is not None
        self.assertEqual(refusal["status"], "bad-kind")
        self.assertEqual(refusal["detail"], "detail text")

    def test_a_launch_conflict_is_reported_as_an_invalid_launch_selection(self) -> None:
        """The caller chose launch options that cannot be satisfied together; the public
        status names the selection, not the internal conflict."""
        refusal = self.refuse("launch-conflict")

        assert refusal is not None
        self.assertEqual(refusal["status"], "launch-selection-invalid")

    def test_an_opened_terminal_produces_no_refusal(self) -> None:
        self.assertIsNone(self.refuse("opened", detail=""))


class SkillsInstallRequestTests(unittest.TestCase):
    """The skills install validates its own request before touching the harness root."""

    def test_a_relative_install_root_is_refused(self) -> None:
        """The install root is resolved against nothing; a relative path would write
        wherever the process happens to be running."""
        with self.assertRaises(ValueError) as raised:
            skills_install.SkillsInstallRequest(install_root=Path(".claude/skills")).validated()

        self.assertEqual(str(raised.exception), "install_root must be an absolute path")

    def test_overwrite_and_archive_together_are_refused(self) -> None:
        """They are opposite answers to the same question -- what happens to an existing
        skill dir -- so asking for both has no meaning to satisfy."""
        with self.assertRaises(ValueError) as raised:
            skills_install.SkillsInstallRequest(
                install_root=Path("/tmp/.claude/skills"), overwrite=True, archive_existing=True
            ).validated()

        self.assertEqual(
            str(raised.exception), "overwrite and archive_existing are mutually exclusive"
        )

    def test_an_absolute_root_with_one_collision_rule_validates(self) -> None:
        request = skills_install.SkillsInstallRequest(
            install_root=Path("/tmp/.claude/skills"), overwrite=True
        ).validated()

        self.assertIs(request.overwrite, True)


class CopySkillTreeCollisionTests(unittest.TestCase):
    """What happens to a skill directory that is already installed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "packaged" / "c-01"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: c-01\n---\n", encoding="utf-8")
        self.install_root = self.root / "skills"
        self.install_root.mkdir()
        self.destination = self.install_root / "c-01"
        self.destination.mkdir()
        (self.destination / "SKILL.md").write_text("old\n", encoding="utf-8")

    def copy(self, **flags: bool) -> skills_install.SkillsInstallSummary:
        summary = skills_install.SkillsInstallSummary()
        skills_install._copy_skill_tree(
            skills_install.SkillsInstallRequest(install_root=self.install_root, **flags),
            summary,
            source=self.source,
            destination=self.destination,
        )
        return summary

    def test_an_existing_skill_is_archived_before_the_new_one_lands(self) -> None:
        """archive_existing exists so a hand-edited skill is recoverable; the archived
        location is reported so the developer can find it."""
        summary = self.copy(archive_existing=True)

        self.assertEqual(len(summary.archived), 1)
        archived = Path(summary.archived[0])
        self.assertTrue(archived.exists())
        self.assertEqual((archived / "SKILL.md").read_text(encoding="utf-8"), "old\n")
        self.assertEqual(
            (self.destination / "SKILL.md").read_text(encoding="utf-8"),
            "---\nname: c-01\n---\n",
        )

    def test_an_existing_skill_with_no_collision_rule_refuses(self) -> None:
        """Neither flag means the caller has not said what to do with the existing skill;
        overwriting it silently would destroy hand edits."""
        with self.assertRaises(FileExistsError) as raised:
            self.copy()

        self.assertIn("set overwrite or archive_existing", str(raised.exception))
        self.assertEqual((self.destination / "SKILL.md").read_text(encoding="utf-8"), "old\n")

    def test_overwrite_removes_the_existing_tree_and_reports_it(self) -> None:
        summary = self.copy(overwrite=True)

        self.assertEqual(summary.removed, [self.destination.as_posix()])
        self.assertEqual(
            (self.destination / "SKILL.md").read_text(encoding="utf-8"),
            "---\nname: c-01\n---\n",
        )


class ProviderDependencyInstallTests(unittest.TestCase):
    """Settings that enable no provider mean there is no dependency install to run."""

    def _install(self, *, dry_run: bool) -> tuple[InstallSummary, list[str]]:
        summary = InstallSummary()
        deps = ProviderDependencyInstall(
            settings={"contextProviders": {"providers": {}}}, timeout=1
        )

        # `grepai_install` / `cgc_install_all` build docker images. Reaching either one is
        # the failure this early return exists to prevent, so both raise if they are called.
        def never(_args: object) -> dict[str, Any]:
            raise AssertionError("no provider is enabled; nothing may be installed")

        with (
            mock.patch.object(runtime_install.lifecycle, "grepai_install", never),
            mock.patch.object(runtime_install.lifecycle, "cgc_install_all", never),
            mock.patch("builtins.print") as printed,
        ):
            install_provider_dependencies(Path("/tmp/coord"), deps, summary, dry_run=dry_run)

        return summary, [str(call.args[0]) for call in printed.call_args_list]

    def test_no_enabled_providers_skips_the_dependency_install_and_says_so_on_a_preview(
        self,
    ) -> None:
        """A dry run exists to report the plan; "nothing to do" is part of the plan, so
        the skip is printed rather than being an invisible early return."""
        summary, printed = self._install(dry_run=True)

        self.assertEqual(printed, ["Would skip provider dependency install; no providers enabled"])
        self.assertEqual(summary.dependency_runs, 0)

    def test_no_enabled_providers_installs_nothing_and_stays_silent_on_a_real_install(
        self,
    ) -> None:
        """A real install prints only what it did, and it did nothing.

        The skip line belongs to the preview: printing it here would put a "would skip" in
        the transcript of a run that was not a preview at all."""
        summary, printed = self._install(dry_run=False)

        self.assertEqual(printed, [])
        self.assertEqual(summary.dependency_runs, 0)
        self.assertEqual(summary.report(), InstallSummary().report())


class DashboardReloadServerTests(unittest.TestCase):
    """``--reload`` hands its knobs to the re-imported app through the environment."""

    def args(self, **over: Any) -> argparse.Namespace:
        base: dict[str, Any] = {
            "sim": False,
            "interval": 2.0,
            "heartbeat": None,
            "host": "127.0.0.1",
            "no_access_log": False,
        }
        base.update(over)
        return argparse.Namespace(**base)

    def test_an_explicit_heartbeat_is_passed_to_the_reimported_app(self) -> None:
        """The reloader re-imports the app in a fresh process, so anything the CLI
        resolved has to travel in the environment or it is lost on every reload."""
        environ: dict[str, str] = {}
        with (
            mock.patch.object(dashboard_cli, "uvicorn") as uvicorn,
            mock.patch.dict(dashboard_cli.os.environ, environ, clear=False),
        ):
            dashboard_cli._run_reload_server(
                self.args(heartbeat=7.5), "/tmp/mcp-settings.json", 8765
            )
            heartbeat = dashboard_cli.os.environ.get(dashboard_cli._DEV_HEARTBEAT_ENV)

        self.assertEqual(heartbeat, "7.5")
        self.assertEqual(uvicorn.run.call_args.kwargs["port"], 8765)

    def test_reload_is_refused_with_sim(self) -> None:
        with mock.patch.object(dashboard_cli, "uvicorn") as uvicorn:
            code = dashboard_cli._run_reload_server(
                self.args(sim=True), "/tmp/mcp-settings.json", 8765
            )

        self.assertEqual(code, 1)
        uvicorn.run.assert_not_called()


class BenchmarkBatchExecutionTests(unittest.TestCase):
    """Parallel benchmark task execution collects failures instead of aborting the run."""

    def task(self, prompt_id: str, repetition: int = 1) -> Any:
        return SimpleNamespace(
            prompt={"id": prompt_id}, variant={"id": "v1"}, repetition=repetition
        )

    def test_every_task_in_a_batch_is_submitted_to_the_executor(self) -> None:
        batch = [self.task("p1"), self.task("p2")]
        ran: list[str] = []

        with (
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
            mock.patch.object(
                benchmark_execution,
                "run_one",
                side_effect=lambda _run, t: ran.append(str(t.prompt["id"])),
            ),
        ):
            future_to_task = benchmark_execution.submit_task_batch(
                executor, cast(Any, object()), batch
            )
            concurrent.futures.wait(list(future_to_task))

        self.assertEqual(sorted(ran), ["p1", "p2"])
        self.assertEqual(sorted(t.prompt["id"] for t in future_to_task.values()), ["p1", "p2"])

    def test_a_failing_task_is_reported_with_its_identity_not_raised(self) -> None:
        """One agent subprocess failing must not abandon the other repetitions; the run
        finishes and names every failure by prompt/variant/repetition."""
        good = self.task("p1")
        bad = self.task("p2", repetition=3)

        def run_one(_run: Any, task: Any) -> None:
            if task is bad:
                raise RuntimeError("codex exited 1")

        with (
            concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
            mock.patch.object(benchmark_execution, "run_one", side_effect=run_one),
        ):
            future_to_task = benchmark_execution.submit_task_batch(
                executor, cast(Any, object()), [good, bad]
            )
            failures = benchmark_execution.completed_task_failures(future_to_task)

        self.assertEqual(failures, ["p2/v1/run-003: codex exited 1"])

    def test_run_task_batches_walks_every_batch_and_returns_all_failures(self) -> None:
        batches = [[self.task("p1")], [self.task("p2", repetition=2)]]

        def run_one(_run: Any, task: Any) -> None:
            raise RuntimeError(f"boom {task.prompt['id']}")

        with mock.patch.object(benchmark_execution, "run_one", side_effect=run_one):
            failures = benchmark_execution.run_task_batches(
                cast(Any, object()), batches, max_workers=2
            )

        self.assertEqual(sorted(failures), ["p1/v1/run-001: boom p1", "p2/v1/run-002: boom p2"])


class BenchmarkMcpRegistrationTests(unittest.TestCase):
    """The benchmark MCP registration insists on the layout its settings hard-code."""

    def workspace(self, *, source_name: str, memory_repo: Path, coordination_root: Path) -> Any:
        return SimpleNamespace(
            case=SimpleNamespace(case_id="case-1", repo_id="repo-a"),
            coordination_root=coordination_root,
            source_repo_root=coordination_root.parent / source_name,
            memory_repo=memory_repo,
            workspace_root=coordination_root.parent,
            provider_ids=(),
        )

    def test_a_source_directory_that_does_not_match_the_repo_id_is_refused(self) -> None:
        """The generated settings address the repo by id, so a directory named anything
        else would register a repo the tools cannot resolve."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            workspace = self.workspace(
                source_name="some-other-name",
                memory_repo=coordination_root / "memory-repos" / "ar-repo-a",
                coordination_root=coordination_root,
            )

            with (
                mock.patch.object(benchmark_mcp, "benchmark_repo_id", return_value="repo-a"),
                self.assertRaises(RuntimeError) as raised,
            ):
                benchmark_mcp.write_benchmark_mcp_registration(
                    workspace, provider_timeout=1, dry_run=True
                )

            self.assertIn("to match the repository id 'repo-a'", str(raised.exception))

    def test_a_memory_repo_outside_the_expected_location_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            coordination_root = root / "ar-coordination"
            workspace = self.workspace(
                source_name="repo-a",
                memory_repo=root / "elsewhere" / "ar-repo-a",
                coordination_root=coordination_root,
            )

            with (
                mock.patch.object(benchmark_mcp, "benchmark_repo_id", return_value="repo-a"),
                self.assertRaises(RuntimeError) as raised,
            ):
                benchmark_mcp.write_benchmark_mcp_registration(
                    workspace, provider_timeout=1, dry_run=True
                )

            self.assertIn("requires memory repository", str(raised.exception))

    def _prepare_without_providers(self, *, dry_run: bool) -> list[str]:
        """Run the provider preparation for a case whose variants request nothing.

        ``run_provider_setup`` starts FalkorDB, Ollama and the watchers for the case. A case
        that asked for no provider must not pay for any of that, so the setup call is the
        thing that must not happen -- it raises here rather than being recorded, because a
        recorded call would still have been a call.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.workspace(
                source_name="repo-a",
                memory_repo=root / "ar-coordination" / "memory-repos" / "ar-repo-a",
                coordination_root=root / "ar-coordination",
            )

            def never(_request: object) -> dict[str, Any]:
                raise AssertionError("no variant requested a provider; setup must not run")

            with (
                mock.patch.object(benchmark_mcp.provider_setup, "run_provider_setup", never),
                mock.patch("builtins.print") as printed,
            ):
                benchmark_mcp.prepare_configured_providers(
                    workspace, dry_run=dry_run, provider_timeout=1
                )

            # The generated settings file is written only past the early return, so the
            # temporary tree is left exactly as the workspace builder made it.
            self.assertEqual(list(root.glob("*provider-settings*")), [])
            return [str(call.args[0]) for call in printed.call_args_list]

    def test_a_case_requesting_no_providers_reports_the_skip_on_a_preview(self) -> None:
        self.assertEqual(
            self._prepare_without_providers(dry_run=True),
            ["Would skip benchmark provider setup for case-1; no variants request providers"],
        )

    def test_a_case_requesting_no_providers_is_silent_on_a_real_run(self) -> None:
        """The preview's job is to describe the plan; a real run has nothing to describe.

        Printing the "would skip" line outside a preview would put a hypothetical into the
        transcript of a run that really executed."""
        self.assertEqual(self._prepare_without_providers(dry_run=False), [])


if __name__ == "__main__":
    unittest.main()
