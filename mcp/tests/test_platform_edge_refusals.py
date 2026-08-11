"""Provider-lifecycle, seed, task-document and dispatch refusals.

Everything here sits behind something the test suite cannot have: a docker daemon, a
second coordination root with a live provider stack, a ready hosted session. The code
under test is the part that decides *not* to proceed -- the settings that disqualify a
seed, the compose command that came back non-zero, the edit that names an operation but
carries no object to apply. Those decisions are pure given their inputs, so the docker
and compose seams are doubled and the decision itself is asserted.

The point of each is the payload or exception a caller acts on: a benign skip that
reports ``ok`` (nothing to seed is not a failure), a hard failure that carries the
failing command, an error message naming the field the caller left out.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import gate_tools as gates
from agents_remember.application import provider_tools, task_doc_tools
from agents_remember.application.gate_tools import GateWait, InboxWatch
from agents_remember.application.task_doc_tools import TaskDocEdit, TaskDocError
from agents_remember.benchmarks.runner_modules import execution as benchmark_execution
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.records import GateRecord, GateVerdict
from agents_remember.controlplane.store import GateStore
from agents_remember.install import provider_watchers
from agents_remember.install import runtime as runtime_install
from agents_remember.install.provider_watchers import (
    ProviderWatcherRebind,
    ProviderWatcherRebindReport,
)
from agents_remember.providers.cgc import seed as cgc_seed
from agents_remember.providers.cgc.lifecycle import backend as cgc_backend
from agents_remember.providers.grepai import seed as grepai_seed
from agents_remember.providers.grepai.lifecycle import backend as grepai_backend
from agents_remember.providers.grepai.lifecycle import embedder as grepai_embedder
from agents_remember.providers.grepai.lifecycle import runner as grepai_runner
from agents_remember.serving.dispatch_brief import HostedDelivery, require_dispatch_target

FAILED_COMMAND = {"returncode": 1, "stdout": "", "stderr": "no such image"}


def cgc_backend_settings(root: Path) -> dict[str, Any]:
    return {
        "id": "cgc-falkordb",
        "type": "falkordb-remote",
        "mode": "docker",
        "image": "falkordb/falkordb:latest",
        "imageLockFile": root / "image.lock.json",
        "containerName": "ar-falkordb",
        "networkName": "ar-net",
        "falkordbHost": "127.0.0.1",
        "browserHost": "127.0.0.1",
        "falkordbHostPort": "auto",
        "browserHostPort": "auto",
        "falkordbContainerPort": 6379,
        "browserContainerPort": 3000,
        "dataDestination": "/data",
    }


class CgcBackendPortsTests(unittest.TestCase):
    """Which host ports the CGC backend reports, and where each number comes from."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.backend = cgc_backend_settings(self.root)

    def test_a_running_container_reports_the_ports_it_already_published(self) -> None:
        """Re-allocating would hand back numbers nothing is listening on; the ports of a
        live container are read off the container itself."""
        with mock.patch.object(
            cgc_backend, "cgc_existing_backend_host_ports", return_value=(6399, 3099)
        ):
            ports = cgc_backend.cgc_backend_host_ports(
                argparse.Namespace(dry_run=False), self.backend, {"Id": "abc"}
            )

        self.assertEqual((ports.falkordb, ports.browser), (6399, 3099))

    def test_a_real_start_with_no_container_allocates_free_host_ports(self) -> None:
        allocated = iter([6401, 3401])
        with (
            mock.patch.object(cgc_backend, "cgc_existing_backend_host_ports", return_value=None),
            mock.patch.object(
                cgc_backend, "allocate_host_port", side_effect=lambda *_a: next(allocated)
            ),
        ):
            ports = cgc_backend.cgc_backend_host_ports(
                argparse.Namespace(dry_run=False), self.backend, None
            )

        self.assertEqual((ports.falkordb, ports.browser), (6401, 3401))

    def test_a_dry_run_reports_the_documented_defaults_without_allocating(self) -> None:
        """A preview must not take a port from the host: 'auto' is shown as the default
        each service would land on."""
        with (
            mock.patch.object(cgc_backend, "cgc_existing_backend_host_ports", return_value=None),
            mock.patch.object(cgc_backend, "allocate_host_port") as allocate,
        ):
            ports = cgc_backend.cgc_backend_host_ports(
                argparse.Namespace(dry_run=True), self.backend, None
            )

        allocate.assert_not_called()
        self.assertEqual((ports.falkordb, ports.browser), (6379, 3000))


class CgcBackendStartContextTests(unittest.TestCase):
    def test_a_backend_that_is_not_managed_docker_falkordb_is_refused(self) -> None:
        """The compose file this module renders only describes a dockerised FalkorDB; any
        other backend would be started by a plan that does not match it."""
        backend = {**cgc_backend_settings(Path("/tmp")), "mode": "host"}
        context = SimpleNamespace(layout=object(), backend=backend)

        with (
            mock.patch.object(cgc_backend, "cgc_primary_backend_context", return_value=context),
            mock.patch.object(cgc_backend, "ensure_cgc_runtime_layout"),
            self.assertRaises(cgc_backend.ContextProviderError) as raised,
        ):
            cgc_backend.cgc_backend_start_context(argparse.Namespace())

        self.assertIn("must be falkordb-remote docker", str(raised.exception))

    def test_a_managed_docker_falkordb_backend_is_accepted(self) -> None:
        context = SimpleNamespace(layout=object(), backend=cgc_backend_settings(Path("/tmp")))

        with (
            mock.patch.object(cgc_backend, "cgc_primary_backend_context", return_value=context),
            mock.patch.object(cgc_backend, "ensure_cgc_runtime_layout") as ensure,
        ):
            resolved = cgc_backend.cgc_backend_start_context(argparse.Namespace())

        self.assertIs(resolved, context)
        ensure.assert_called_once()


class CgcBackendStateTests(unittest.TestCase):
    def test_the_recorded_state_pins_the_image_digest_alongside_the_tag(self) -> None:
        """The state file is what a later start compares against, so it records the exact
        digest behind the tag -- a moving ``:latest`` is otherwise invisible."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = cgc_backend_settings(root)
            context = cast(
                Any,
                SimpleNamespace(
                    settings_path=root / "settings.json",
                    layout=SimpleNamespace(
                        coordination_root=root,
                        backend_root=root / "backend",
                        backend_data_root=root / "backend" / "data",
                    ),
                    backend=backend,
                ),
            )

            state = cgc_backend.cgc_backend_state(
                context,
                status="running",
                ports=cgc_backend.CgcHostPorts(falkordb=6399, browser=3099),
                image_digest="sha256:deadbeef",
                container_id="container-1",
            )

        self.assertEqual(state["provider"], "codegraphcontext")
        self.assertEqual(state["backend"]["id"], "cgc-falkordb")
        self.assertEqual(
            state["backend"]["imageLock"],
            {"image": "falkordb/falkordb:latest", "repoDigest": "sha256:deadbeef"},
        )


class ProviderComposeFailureTests(unittest.TestCase):
    """A failing ``docker compose up`` is reported, never raised."""

    def test_a_failed_cgc_backend_compose_reports_the_failing_command(self) -> None:
        """The caller needs the command and its stderr to act; an exception here would
        lose both behind a traceback."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = cgc_backend_settings(root)
            context = cast(
                Any,
                SimpleNamespace(
                    provider_settings={},
                    layouts=[SimpleNamespace(coordination_root=root)],
                    layout=SimpleNamespace(coordination_root=root),
                    backend=backend,
                ),
            )
            reconciliation = cgc_backend.BackendStartReconciliation(
                network={"ok": True}, migration={"state": "none"}, forced_remove=None
            )

            with (
                mock.patch.object(
                    cgc_backend,
                    "cgc_backend_host_ports",
                    return_value=cgc_backend.CgcHostPorts(falkordb=6379, browser=3000),
                ),
                mock.patch.object(cgc_backend, "cgc_compose_render", return_value=object()),
                mock.patch.object(
                    cgc_backend, "cgc_compose_summary", return_value={"file": "compose.yaml"}
                ),
                mock.patch.object(
                    cgc_backend, "run_compose", return_value=FAILED_COMMAND
                ) as run_compose,
            ):
                result = cgc_backend.cgc_backend_create_start_result(
                    argparse.Namespace(dry_run=False, timeout=5),
                    context,
                    reconciliation,
                    inspect_data=None,
                )

        self.assertIs(result["ok"], False)
        self.assertEqual(result["action"], "backend-start")
        self.assertEqual(result["command"], FAILED_COMMAND)
        self.assertEqual(run_compose.call_args.args[1], ["up", "-d", "falkordb"])


class GrepaiBackendStateTests(unittest.TestCase):
    def test_the_recorded_state_names_the_provider_and_its_image_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = {
                "id": "grepai-postgres",
                "type": "postgres",
                "mode": "docker",
                "image": "pgvector/pgvector:pg16",
                "imageLockFile": root / "grepai-image.lock.json",
                "containerName": "ar-grepai-postgres",
                "networkName": "ar-net",
                "postgresHost": "127.0.0.1",
                "postgresContainerPort": 5432,
                "dataDestination": "/var/lib/postgresql/data",
            }
            context = cast(
                Any,
                SimpleNamespace(
                    settings_path=root / "settings.json",
                    layout=SimpleNamespace(
                        coordination_root=root,
                        backend_root=root / "backend",
                        backend_data_root=root / "backend" / "data",
                    ),
                    backend=backend,
                ),
            )

            state = grepai_backend.grepai_backend_state(
                context,
                status="running",
                postgres_port=5599,
                image_digest="sha256:cafe",
                container_id="container-2",
            )

        self.assertEqual(state["provider"], "grepai")
        self.assertEqual(state["backend"]["id"], "grepai-postgres")
        self.assertEqual(
            state["backend"]["imageLock"],
            {"image": "pgvector/pgvector:pg16", "repoDigest": "sha256:cafe"},
        )


class GrepaiMismatchedContainerTests(unittest.TestCase):
    """A container bound to the wrong data directory is removed, and a failed removal is
    reported rather than being silently treated as success."""

    def test_a_failed_forced_removal_stops_the_backend_start(self) -> None:
        layout = SimpleNamespace(coordination_root=Path("/tmp"), backend_data_root=Path("/tmp/d"))
        backend = {"containerName": "ar-grepai-postgres", "dataDestination": "/var/lib/pg"}

        with (
            mock.patch.object(grepai_backend, "docker_data_mount_source", return_value="/other"),
            mock.patch.object(grepai_backend, "docker_host_path_matches", return_value=False),
            mock.patch.object(grepai_backend, "run_command", return_value=FAILED_COMMAND),
        ):
            inspect_data, removal, error = (
                grepai_backend.grepai_backend_remove_mismatched_container(
                    argparse.Namespace(timeout=5),
                    cast(Any, layout),
                    backend,
                    {"Id": "abc"},
                )
            )

        self.assertEqual(inspect_data, {"Id": "abc"})
        self.assertEqual(removal, FAILED_COMMAND)
        assert error is not None
        self.assertIs(error["ok"], False)
        self.assertEqual(error["action"], "backend-start")
        self.assertEqual(error["command"], FAILED_COMMAND)

    def test_a_failed_embedder_removal_is_reported_against_the_embedder_action(self) -> None:
        """The status is scoped to the component that failed; a backend-start error here
        would send the reader to the wrong container."""
        layout = SimpleNamespace(coordination_root=Path("/tmp"))
        embedder = {
            "containerName": "ar-ollama",
            "dataDestination": "/root/.ollama",
            "dataRoot": "/tmp/d",
        }

        with (
            mock.patch.object(grepai_embedder, "docker_data_mount_source", return_value="/other"),
            mock.patch.object(grepai_embedder, "docker_host_path_matches", return_value=False),
            mock.patch.object(grepai_embedder, "run_command", return_value=FAILED_COMMAND),
        ):
            _inspect, removal, error = grepai_embedder.grepai_embedder_remove_mismatched_container(
                argparse.Namespace(timeout=5),
                cast(Any, layout),
                embedder,
                {"Id": "abc"},
            )

        self.assertEqual(removal, FAILED_COMMAND)
        assert error is not None
        self.assertEqual(error["action"], "embedder-start")


class GrepaiWatcherStartPreconditionTests(unittest.TestCase):
    """The watcher's image build must succeed before compose is asked to start it."""

    def test_a_failed_image_build_refuses_the_watcher_start(self) -> None:
        layout = SimpleNamespace(coordination_root=Path("/tmp"))
        image = {"ok": False, "error": "build failed"}

        with (
            mock.patch.object(
                grepai_runner, "grepai_layout_from_args", return_value=(None, None, layout)
            ),
            mock.patch.object(grepai_runner, "grepai_runner_image_build", return_value=image),
        ):
            start, refusal = grepai_runner.grepai_watcher_start_prerequisites(
                argparse.Namespace(),
                runner={"containerName": "ar-grepai-watcher"},
                network_name="ar-net",
            )

        self.assertIs(start.image, image)
        assert refusal is not None
        self.assertIs(refusal["ok"], False)
        self.assertEqual(refusal["action"], "watcher-start")
        self.assertEqual(refusal["image"], image)

    def test_a_successful_image_build_carries_no_refusal(self) -> None:
        layout = SimpleNamespace(coordination_root=Path("/tmp"))

        with (
            mock.patch.object(
                grepai_runner, "grepai_layout_from_args", return_value=(None, None, layout)
            ),
            mock.patch.object(
                grepai_runner, "grepai_runner_image_build", return_value={"ok": True}
            ),
        ):
            start, refusal = grepai_runner.grepai_watcher_start_prerequisites(
                argparse.Namespace(),
                runner={"containerName": "ar-grepai-watcher"},
                network_name="ar-net",
            )

        self.assertIsNone(refusal)
        self.assertEqual(start.network["name"], "ar-net")


class SeedRefusalTests(unittest.TestCase):
    """A seed clone that cannot be described is skipped benignly, naming the reason."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def grepai_settings(self, *, enabled: bool) -> dict[str, Any]:
        return {
            "contextProviders": {
                "enabled": True,
                "providers": {"grepai-memory": {"enabled": enabled, "workspace": "ws"}},
            }
        }

    def resolve_grepai(
        self, *, source: dict[str, Any] | None, target: dict[str, Any]
    ) -> dict[str, Any]:
        inputs = SimpleNamespace(
            source_coordination_root=self.root / "source",
            target_settings_path=self.root / "target-settings.json",
        )
        with (
            mock.patch.object(grepai_seed, "_clone_inputs", return_value=inputs),
            mock.patch.object(
                grepai_seed,
                "grepai_seed_source_settings_path",
                return_value=self.root / "source-settings.json",
            ),
            mock.patch.object(grepai_seed, "load_settings", return_value=source),
        ):
            return cast(
                dict[str, Any],
                grepai_seed._resolve_clone_context(
                    argparse.Namespace(coordination_root=self.root), target
                ),
            )

    def test_missing_source_settings_skip_the_clone(self) -> None:
        """Nothing to clone from is a benign outcome -- a fresh coordination root has no
        seed source -- so it reports ok with the missing file named."""
        result = self.resolve_grepai(source=None, target=self.grepai_settings(enabled=True))

        self.assertIs(result["ok"], True)
        self.assertIs(result["skipped"], True)
        self.assertIn("source settings missing", result["reason"])

    def test_a_disabled_source_provider_skips_the_clone(self) -> None:
        """Cloning requires the source provider to be explicitly enabled: a merely-present
        entry describes a stack that was never indexed."""
        result = self.resolve_grepai(
            source=self.grepai_settings(enabled=False), target=self.grepai_settings(enabled=True)
        )

        self.assertIs(result["skipped"], True)
        self.assertEqual(result["reason"], "source grepai-memory provider is not configured")

    def test_a_disabled_target_provider_skips_the_clone(self) -> None:
        result = self.resolve_grepai(
            source=self.grepai_settings(enabled=True), target=self.grepai_settings(enabled=False)
        )

        self.assertIs(result["skipped"], True)
        self.assertEqual(result["reason"], "target grepai-memory provider is not configured")

    def test_a_cgc_seed_stops_at_unreadable_source_settings(self) -> None:
        skip = {"ok": False, "skipped": True, "reason": "source settings missing: x"}

        with (
            mock.patch.object(cgc_seed, "_seed_precondition_skip", return_value=None),
            mock.patch.object(cgc_seed, "_load_seed_source_settings", return_value=skip),
            mock.patch.object(cgc_seed, "_seed_locations") as locations,
        ):
            result = cgc_seed._resolve_seed_context(
                argparse.Namespace(cgc_seed_source_coordination_root=self.root), {}
            )

        self.assertIs(result, skip)
        locations.assert_not_called()

    def test_a_cgc_seed_stops_at_an_unresolvable_end(self) -> None:
        """``_seed_locations`` reports the first side it cannot place; that payload is the
        result, so a half-resolved source/target pair never reaches the export."""
        located = {"ok": False, "skipped": True, "reason": "source repo root missing"}

        with (
            mock.patch.object(cgc_seed, "_seed_precondition_skip", return_value=None),
            mock.patch.object(cgc_seed, "_load_seed_source_settings", return_value={"x": 1}),
            mock.patch.object(cgc_seed, "_seed_locations", return_value=located),
            mock.patch.object(cgc_seed, "_validated_seed_context") as validated,
        ):
            result = cgc_seed._resolve_seed_context(
                argparse.Namespace(cgc_seed_source_coordination_root=self.root), {}
            )

        self.assertIs(result, located)
        validated.assert_not_called()


class WorktreeGrepaiSettingsTests(unittest.TestCase):
    """A worktree's isolated grepai settings must actually describe the provider."""

    def settings_file(self, payload: dict[str, Any]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "settings.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_settings_without_a_grepai_provider_are_refused_by_path(self) -> None:
        """The worktree stack is addressed through this entry; without it the query would
        be silently routed at the workspace stack instead."""
        path = self.settings_file({"contextProviders": {"providers": {}}})

        with self.assertRaises(ValueError) as raised:
            provider_tools._load_worktree_grepai_provider(path)

        self.assertIn("missing grepai-memory provider", str(raised.exception))
        self.assertIn(str(path), str(raised.exception))

    def test_a_non_object_context_providers_block_is_refused(self) -> None:
        path = self.settings_file({"contextProviders": []})

        with self.assertRaises(ValueError) as raised:
            provider_tools._load_worktree_grepai_provider(path)

        self.assertIn("missing grepai-memory provider", str(raised.exception))

    def test_a_present_provider_is_returned(self) -> None:
        path = self.settings_file(
            {"contextProviders": {"providers": {"grepai-memory": {"workspace": "wt"}}}}
        )

        self.assertEqual(provider_tools._load_worktree_grepai_provider(path), {"workspace": "wt"})


class ProviderWatcherStopTests(unittest.TestCase):
    """The runtime install stops watchers before replacing their runtime tree."""

    def _rebind(self, report: ProviderWatcherRebindReport) -> ProviderWatcherRebind:
        return ProviderWatcherRebind(
            coordination_root=Path("/tmp/coord"),
            settings={},
            report=report,
            dry_run=False,
            timeout=5,
        )

    def test_a_failed_stop_marks_the_report_and_aborts_the_refresh(self) -> None:
        """Refreshing provider runtime under running watchers is what corrupts them, so a
        stop that did not succeed must stop the install -- with the recovery actions the
        developer needs recorded on the report."""
        report = ProviderWatcherRebindReport()
        failure = {"ok": False, "provider": "grepai", "error": "docker not running"}

        # Only the process seam is faked: the phase bookkeeping and the ok/partial verdict
        # are the code under test, so a `partial` result must be read as a failed stop.
        with (
            mock.patch.object(
                provider_watchers,
                "run_provider_watcher_lifecycle",
                return_value={**failure, "ok": True, "partial": True},
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            provider_watchers.stop_provider_watchers_before_refresh(self._rebind(report))

        self.assertIs(report.ok, False)
        self.assertIn("provider watcher stop failed", str(raised.exception))
        self.assertIn("docker not running", str(raised.exception))
        # The developer is told which provider to restart by hand, and how.
        self.assertEqual(
            [(action["provider"], action["action"]) for action in report.recovery_actions],
            [("grepai", "restart")],
        )
        self.assertEqual(
            report.recovery_actions[0]["recoveryAction"],
            provider_watchers.PROVIDER_WATCHER_RESTART_RECOVERY,
        )
        # The attempt is on the report either way, so the install's payload shows what ran.
        self.assertEqual([run["phase"] for run in report.runs], ["pre-provider-refresh-stop"])

    def test_a_successful_stop_records_the_attempt_and_leaves_the_install_free_to_proceed(
        self,
    ) -> None:
        # `ok` stays None rather than becoming True: this function only ever reports failure,
        # and the rebind's overall verdict is decided later by the post-install status read.
        report = ProviderWatcherRebindReport()
        actions: list[str] = []

        def lifecycle(_rebind: object, action: str) -> dict[str, Any]:
            actions.append(action)
            return {"ok": True, "provider": "grepai"}

        with mock.patch.object(provider_watchers, "run_provider_watcher_lifecycle", lifecycle):
            provider_watchers.stop_provider_watchers_before_refresh(self._rebind(report))

        self.assertEqual(actions, ["stop"])
        self.assertEqual(
            report.runs, [{"phase": "pre-provider-refresh-stop", "ok": True, "provider": "grepai"}]
        )
        self.assertIsNone(report.ok)
        self.assertEqual(report.recovery_actions, [])


class TaskDocEditRequirementTests(unittest.TestCase):
    """Each edit operation names the object it needs when the caller omits it."""

    def apply(self, operation: str, *, kind: str = "light", **edit: Any) -> Any:
        doc = SimpleNamespace(
            kind=kind,
            model_dump=lambda **_kw: {"kind": kind},
        )
        return task_doc_tools._apply(operation, cast(Any, doc), TaskDocEdit(**edit))

    def test_set_step_without_a_step_object_says_so(self) -> None:
        with self.assertRaises(TaskDocError) as raised:
            self.apply("set_step")

        self.assertEqual(str(raised.exception), "set_step requires a step object")

    def test_set_subtask_without_a_subtask_object_says_so(self) -> None:
        with self.assertRaises(TaskDocError) as raised:
            self.apply("set_subtask", kind="master")

        self.assertEqual(str(raised.exception), "set_subtask requires a subtask object")

    def test_set_section_without_a_section_object_says_so(self) -> None:
        with self.assertRaises(TaskDocError) as raised:
            self.apply("set_section")

        self.assertEqual(str(raised.exception), "set_section requires a section object")

    def test_append_decision_without_a_decision_object_says_so(self) -> None:
        with self.assertRaises(TaskDocError) as raised:
            self.apply("append_decision")

        self.assertEqual(str(raised.exception), "append_decision requires a decision object")

    def test_a_non_mutating_operation_revalidates_the_document_unchanged(self) -> None:
        """``get``/``create``/``replace`` are handled before ``_apply``; reaching it with
        one must re-validate what is there, not fall through to an arbitrary mutation."""
        doc = SimpleNamespace(kind="light", model_dump=lambda **_kw: {"kind": "light"})
        sentinel = object()

        with mock.patch.object(task_doc_tools, "_validate", return_value=sentinel) as validate:
            result = task_doc_tools._apply("get", cast(Any, doc), TaskDocEdit())

        self.assertIs(result, sentinel)
        self.assertEqual(validate.call_args.args[0], {"kind": "light"})


class DispatchTargetTests(unittest.TestCase):
    """A dispatch-brief refuses before the durable inbox row exists."""

    def test_a_dispatch_brief_without_runtime_configuration_is_refused(self) -> None:
        """Without a catalog there is no way to check the target session is the one the
        caller means; posting the row anyway would claim a delivery that cannot happen."""
        with self.assertRaises(ValueError) as raised:
            require_dispatch_target(
                message_kind="dispatch-brief",
                agent_id="agent-1",
                delivery=HostedDelivery(enabled=True, catalog=None),
            )

        self.assertEqual(str(raised.exception), "dispatch-brief requires runtime configuration")

    def test_a_dispatch_brief_without_an_exact_agent_is_refused(self) -> None:
        with self.assertRaises(ValueError) as raised:
            require_dispatch_target(
                message_kind="dispatch-brief",
                agent_id=None,
                delivery=HostedDelivery(enabled=True, catalog=cast(Any, object())),
            )

        self.assertIn("requires exact agent_id and deliver_to_hosted=true", str(raised.exception))

    def test_a_dispatch_brief_with_hosted_delivery_off_is_refused(self) -> None:
        with self.assertRaises(ValueError) as raised:
            require_dispatch_target(
                message_kind="dispatch-brief",
                agent_id="agent-1",
                delivery=HostedDelivery(enabled=False, catalog=cast(Any, object())),
            )

        self.assertIn("deliver_to_hosted=true", str(raised.exception))

    def test_an_ordinary_message_needs_no_hosted_target(self) -> None:
        self.assertIsNone(
            require_dispatch_target(
                message_kind="message",
                agent_id=None,
                delivery=HostedDelivery(enabled=False, catalog=None),
            )
        )


class WorkspaceGateResponseWaitTests(unittest.TestCase):
    """A gate raised with no lifecycle and no agent mailbox has no inbox to poll."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = GateStore(self.root)
        self.inbox = OperatorInboxStore(self.root)
        for name, value in (("_store", self.store), ("_inbox_store", self.inbox)):
            patcher = mock.patch.object(gates, name, return_value=value)
            self.addCleanup(patcher.stop)
            patcher.start()

    def test_a_workspace_gate_is_answered_without_reading_the_inbox(self) -> None:
        """With neither a lifecycle id nor an agent id there is no mailbox key, so the
        pending-entry scan is skipped rather than run over every workspace entry."""
        self.store.append(
            GateRecord(
                id="G1",
                ts="2026-07-31T10:00:00+00:00",
                kind="agent-question",
                state="open",
                lifecycleId=None,
            )
        )
        gate_id = "G1"
        gates.gate_decide_tool(
            cast(Any, None),
            gate_id=gate_id,
            lifecycle_id=None,
            verdict=GateVerdict(decision="approve", by="developer", via="dashboard"),
        )

        with mock.patch.object(self.inbox, "list_pending") as list_pending:
            result = gates.gate_response_wait_tool(
                cast(Any, None),
                gate_id=gate_id,
                lifecycle_id=None,
                inbox=InboxWatch(agent_id=None),
                wait=GateWait(timeout_seconds=10.0, sleep=lambda _s: None),
            )

        list_pending.assert_not_called()
        self.assertIs(result["timedOut"], False)
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["entryCount"], 0)


class BenchmarkRunCaseTests(unittest.TestCase):
    """A real (non-preview) benchmark run picks its worker count and reports failures."""

    def request(self, **over: Any) -> Any:
        base: dict[str, Any] = {
            "preparation": SimpleNamespace(benchmarks_root=Path("/tmp/benchmarks"), dry_run=False),
            "prompt": None,
            "variant": None,
            "repetitions": None,
            "jobs": None,
            "skip_prepare": True,
            "codex_sandbox": "default",
        }
        base.update(over)
        return SimpleNamespace(**base)

    def run_case(self, request: Any, failures: list[str]) -> tuple[Any, dict[str, Any]]:
        seen: dict[str, Any] = {}

        def run_task_batches(_run: Any, _batches: Any, *, max_workers: int) -> list[str]:
            seen["max_workers"] = max_workers
            return failures

        with (
            mock.patch.object(benchmark_execution, "maybe_prepare_case"),
            mock.patch.object(
                benchmark_execution, "create_output_root", return_value=Path("/tmp/out")
            ),
            mock.patch.object(
                benchmark_execution, "benchmark_task_batches", return_value=([[object()]], 4)
            ),
            mock.patch.object(benchmark_execution, "run_task_batches", run_task_batches),
            mock.patch.object(benchmark_execution, "analyze_run_root", return_value={"runs": 1}),
            mock.patch.object(benchmark_execution, "write_summary") as write_summary,
        ):
            try:
                output_root = benchmark_execution.run_case(request, cast(Any, object()))
            except RuntimeError as error:
                seen["error"] = str(error)
                output_root = None
        seen["write_summary_calls"] = write_summary.call_count
        return output_root, seen

    def test_the_cases_own_default_job_count_is_used_when_none_is_requested(self) -> None:
        output_root, seen = self.run_case(self.request(), [])

        self.assertEqual(seen["max_workers"], 4)
        self.assertEqual(output_root, Path("/tmp/out"))

    def test_an_explicit_job_count_overrides_the_cases_default(self) -> None:
        _output_root, seen = self.run_case(self.request(jobs=2), [])

        self.assertEqual(seen["max_workers"], 2)

    def test_the_summary_is_written_before_failures_are_raised(self) -> None:
        """The run's own analysis is the record of what happened; raising first would
        discard it exactly when a failed run makes it most useful."""
        _output_root, seen = self.run_case(self.request(), ["p1/v1/run-001: boom"])

        self.assertEqual(seen["write_summary_calls"], 1)
        self.assertIn("p1/v1/run-001: boom", seen["error"])


class GrepaiComposeFailureTests(unittest.TestCase):
    """Each grepai service reports a failed ``compose up`` under its own action name.

    All three take the same shape, and the shape is the point: the caller is told which
    service failed and is handed the command that failed, so a stack that came up
    partially can be read off one payload rather than a traceback.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.args = argparse.Namespace(dry_run=False, timeout=5)
        self.layout = SimpleNamespace(
            coordination_root=self.root,
            backend_root=self.root / "backend",
            backend_data_root=self.root / "backend" / "data",
        )
        self.reconciliation = cgc_backend.BackendStartReconciliation(
            network={"ok": True}, migration={"state": "none"}, forced_remove=None
        )

    def grepai_common_patches(self, module: Any) -> list[Any]:
        return [
            mock.patch.object(module, "grepai_runner_settings", return_value={"id": "runner"}),
            mock.patch.object(module, "grepai_compose_render", return_value=object()),
            mock.patch.object(
                module, "grepai_compose_summary", return_value={"file": "compose.yaml"}
            ),
            mock.patch.object(module, "run_compose", return_value=FAILED_COMMAND),
        ]

    def test_a_failed_postgres_up_is_reported_as_a_backend_start_failure(self) -> None:
        context = cast(
            Any,
            SimpleNamespace(
                provider_settings={},
                layout=self.layout,
                backend={"id": "grepai-postgres", "containerName": "ar-grepai-postgres"},
            ),
        )
        patches = [
            *self.grepai_common_patches(grepai_backend),
            mock.patch.object(grepai_backend, "grepai_backend_host_port", return_value=5432),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        result = grepai_backend.grepai_backend_create_start_result(
            self.args, context, self.reconciliation, inspect_data=None
        )

        self.assertIs(result["ok"], False)
        self.assertEqual(result["action"], "backend-start")
        self.assertEqual(result["command"], FAILED_COMMAND)

    def test_a_failed_ollama_up_is_reported_as_an_embedder_start_failure(self) -> None:
        context = cast(
            Any,
            SimpleNamespace(
                provider_settings={},
                layout=self.layout,
                embedder={"id": "grepai-ollama", "containerName": "ar-ollama"},
            ),
        )
        patches = [
            *self.grepai_common_patches(grepai_embedder),
            mock.patch.object(grepai_embedder, "grepai_embedder_host_port", return_value=11434),
            mock.patch.object(grepai_embedder, "grepai_backend_settings", return_value={}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        result = grepai_embedder.grepai_embedder_create_start_result(
            self.args, context, self.reconciliation, inspect_data=None
        )

        self.assertIs(result["ok"], False)
        self.assertEqual(result["action"], "embedder-start")
        self.assertEqual(result["command"], FAILED_COMMAND)
        self.assertEqual(result["migration"], {"state": "none"})

    def test_a_watcher_that_does_not_come_up_reports_not_ok_with_its_commands(self) -> None:
        """The watcher's verdict is compose *and* the container actually running, so a
        non-zero up must land as ok:false with the up command kept on the payload."""
        start = grepai_runner.GrepaiWatcherStart(
            layout=cast(Any, self.layout),
            runner={"containerName": "ar-grepai-watcher"},
            network={"ok": True, "name": "ar-net"},
            image={"ok": True},
        )
        patches = [
            *self.grepai_common_patches(grepai_runner),
            mock.patch.object(
                grepai_runner, "grepai_layout_from_args", return_value=(None, {}, self.layout)
            ),
            mock.patch.object(grepai_runner, "grepai_backend_settings", return_value={}),
            mock.patch.object(
                grepai_runner, "grepai_project_migration", return_value={"state": "none"}
            ),
            mock.patch.object(grepai_runner, "docker_inspect_container", return_value=None),
            mock.patch.object(grepai_runner, "docker_container_running", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        result = grepai_runner.grepai_watcher_create_start_result(
            self.args, start, grepai_runner.UNRESOLVED_SERVICE_PORTS
        )

        self.assertIs(result["ok"], False)
        self.assertEqual(result["action"], "watcher-start")
        self.assertEqual(result["commands"]["up"], FAILED_COMMAND)


class RuntimeInstallFailureTests(unittest.TestCase):
    """What a runtime install does when the copy fails, and where it installs from."""

    def runtime_tree(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source_root = Path(tmp.name) / "source"
        runtime = source_root / "runtime"
        (runtime / "agents-md-files").mkdir(parents=True)
        (runtime / "skills").mkdir(parents=True)
        requirements = runtime / "providers" / "requirements"
        requirements.mkdir(parents=True)
        (requirements / "codegraphcontext.txt").write_text("", encoding="utf-8")
        (requirements / "grepai.txt").write_text("", encoding="utf-8")
        return source_root

    def test_a_failure_with_no_watcher_rebind_propagates_unchanged(self) -> None:
        """The wrapped RuntimeError exists only to report an attempted watcher recovery.
        With provider deps disabled no watcher was ever stopped, so there is nothing to
        recover and the original error must not be reworded into a recovery report."""
        source_root = self.runtime_tree()
        coordination_root = source_root.parent / "coord"
        original = OSError("disk full")

        with (
            mock.patch.object(runtime_install, "prune_tree", side_effect=[None, original]),
            self.assertRaises(OSError) as raised,
        ):
            runtime_install.install_runtime(
                source_root,
                coordination_root,
                dry_run=True,
                provider_deps=runtime_install.ProviderDependencyInstall(
                    settings={}, timeout=1, enabled=False
                ),
            )

        self.assertIs(raised.exception, original)

    def test_an_explicit_source_root_is_installed_from_instead_of_the_packaged_tree(
        self,
    ) -> None:
        """``source_root`` is the developer-supplied checkout; taking the packaged tree
        anyway would install a different build than the one under test."""
        source_root = self.runtime_tree()
        config = cast(
            Any,
            SimpleNamespace(
                coordination_root=Path("/tmp/coord"),
                timeout_caps={"providerSetupSeconds": 60},
            ),
        )
        summary = runtime_install.InstallSummary()

        with (
            mock.patch.object(runtime_install, "reload_provider_authority") as reload_authority,
            mock.patch.object(runtime_install, "lifecycle_settings_from_config", return_value={}),
            mock.patch.object(runtime_install, "install_runtime", return_value=summary) as install,
            mock.patch.object(runtime_install, "packaged_source_root") as packaged,
        ):
            reload_authority.return_value.apply.return_value = config
            payload = runtime_install.install_runtime_from_config(
                config,
                runtime_install.RuntimeInstallRequest(dry_run=True, source_root=source_root),
            )

        packaged.assert_not_called()
        self.assertEqual(install.call_args.args[0], source_root.resolve())
        self.assertIs(payload["ok"], True)
        self.assertIs(payload["dryRun"], True)


if __name__ == "__main__":
    unittest.main()
