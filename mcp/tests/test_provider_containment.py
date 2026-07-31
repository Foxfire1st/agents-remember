"""Provider containment (260707-HFX-L1): unconfigured on disk ⇒ no launch, ever.

The 2026-07-07 WSL OOM proved two bypasses of the settings gate: the boot
snapshot (running servers never re-read the authority file) and benchmark
self-arming (the case manifest synthesized+persisted its own providers map).
These tests pin the containment layer that closes both, plus the aggregate
setup lock and the metrics feed.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.benchmarks.runner_modules.constants import (
    BENCHMARK_MCP_SETTINGS_NAME,
    CODEX_HARNESS_DIR,
)
from agents_remember.benchmarks.runner_modules.mcp_registration import (
    disarm_stale_benchmark_registrations,
)
from agents_remember.benchmarks.runner_modules.workspace import (
    UNFILTERED_PROVIDERS_ENV,
    filter_benchmark_provider_ids,
)
from agents_remember.controllers import provider_tools, worktree_tools
from agents_remember.mcp.config import (
    ConfigError,
    McpRuntimeConfig,
    ProviderScope,
    RepositoryScope,
    reload_provider_authority,
    require_provider_launch_authority,
)
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.metrics import (
    MetricsSnapshot,
    ProviderMetricsStore,
    _parse_bytes,
    _parse_labels,
    _parse_mem_usage,
    _parse_percent,
    sample_provider_containers,
)
from agents_remember.providers.provider_setup import _fleet_setup_lock, fleet_setup_lock_path
from agents_remember.providers.settings import lifecycle_settings_from_config


def _armed_boot_config(tmp: Path, *, disk_providers: dict) -> McpRuntimeConfig:
    """A config whose BOOT SNAPSHOT is armed while the disk says ``disk_providers``."""
    authority = tmp / "authority.json"
    authority.write_text(json.dumps({"version": 1, "providers": disk_providers}), encoding="utf-8")
    coordination_root = tmp / "coord"
    workspace_root = tmp / "ws"
    return McpRuntimeConfig(
        config_path=authority,
        coordination_root=coordination_root,
        workspace_root=workspace_root,
        transcript_root=coordination_root / "logs" / "mcp",
        repositories={
            "repo": RepositoryScope(repo_id="repo", path=workspace_root / "repo"),
        },
        providers={
            "grepai-memory": ProviderScope(
                provider_id="grepai-memory",
                runtime_root=coordination_root / "providers" / "runners" / "grepai" / "i1",
                log_root=coordination_root / "logs" / "providers" / "grepai" / "i1",
                instance_id="i1",
            ),
        },
    )


class ReloadProviderAuthorityTests(unittest.TestCase):
    def test_disk_disabled_yields_empty_map_without_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            authority = reload_provider_authority(config)
        self.assertEqual(authority.providers, {})
        self.assertIsNone(authority.error)

    def test_disk_armed_yields_live_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={"codegraphcontext-code": {}})
            authority = reload_provider_authority(config)
        self.assertEqual(sorted(authority.providers), ["codegraphcontext-code"])
        self.assertIsNone(authority.error)

    def test_missing_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            config.config_path.unlink()
            authority = reload_provider_authority(config)
        self.assertEqual(authority.providers, {})
        self.assertIsNotNone(authority.error)

    def test_invalid_json_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            config.config_path.write_text("{torn", encoding="utf-8")
            authority = reload_provider_authority(config)
        self.assertEqual(authority.providers, {})
        self.assertIsNotNone(authority.error)

    def test_require_launch_authority_refuses_disk_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            with self.assertRaises(ConfigError) as ctx:
                require_provider_launch_authority(config, operation="test-op")
        self.assertIn("containment R1", str(ctx.exception))
        self.assertIn("grepai-memory", str(ctx.exception))  # names the stale snapshot

    def test_require_launch_authority_returns_live_config_when_armed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={"codegraphcontext-code": {}})
            live = require_provider_launch_authority(config, operation="test-op")
        self.assertEqual(sorted(live.providers), ["codegraphcontext-code"])


class WorktreeStartVetoTests(unittest.TestCase):
    def test_stale_armed_snapshot_is_vetoed_by_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            captured: dict[str, object] = {}

            def fake_start(
                args: worktree_tools.git_worktree_manager.WorktreeArgs,
            ) -> worktree_tools.git_worktree_manager.WorktreeCommandResult:
                captured["provider_setup_config"] = args.provider_setup_config
                return worktree_tools.git_worktree_manager.WorktreeCommandResult(
                    returncode=0, payload={"state": "blocked"}
                )

            with mock.patch.object(worktree_tools.git_worktree_manager, "start_result", fake_start):
                result = worktree_tools.worktree_start_tool(
                    config,
                    worktree_tools.TaskIdentity(repo_id="repo", task_name="t", worktree_name="w"),
                )
        # The launch side-channel never materializes: no settings file, no setup config.
        self.assertIsNone(captured["provider_setup_config"])
        veto = result["providersAuthority"]
        self.assertEqual(veto["bootSnapshotProviders"], ["grepai-memory"])

    def test_disk_armed_snapshot_launches_with_live_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={"grepai-memory": {}})
            captured: dict[str, object] = {}

            def fake_start(
                args: worktree_tools.git_worktree_manager.WorktreeArgs,
            ) -> worktree_tools.git_worktree_manager.WorktreeCommandResult:
                setup_config = args.provider_setup_config
                captured["provider_setup_config"] = setup_config
                if setup_config is not None:
                    # Read while the temp file exists — the controller's finally
                    # unlinks it once no background setup owns it.
                    captured["settings"] = json.loads(
                        Path(setup_config.settings_path).read_text(encoding="utf-8")
                    )
                return worktree_tools.git_worktree_manager.WorktreeCommandResult(
                    returncode=0, payload={"state": "blocked"}
                )

            with mock.patch.object(worktree_tools.git_worktree_manager, "start_result", fake_start):
                result = worktree_tools.worktree_start_tool(
                    config,
                    worktree_tools.TaskIdentity(repo_id="repo", task_name="t", worktree_name="w"),
                )
        self.assertIsNotNone(captured["provider_setup_config"])
        settings = captured["settings"]
        assert isinstance(settings, dict)
        self.assertTrue(settings["contextProviders"]["enabled"])
        self.assertNotIn("providersAuthority", result)


class QueryFunnelGateTests(unittest.TestCase):
    def test_query_funnel_requires_its_specific_provider(self) -> None:
        # Review note: an armed grepai must not authorize a cgc one-shot runner.
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={"grepai-memory": {}})
            run = mock.Mock()
            with self.assertRaises(ConfigError) as ctx:
                provider_tools._provider_operation_result(
                    config,
                    provider_tools.ProviderOperation(
                        operation="cgc_symbol_search",
                        required_provider="codegraphcontext-code",
                        run=run,
                    ),
                )
        self.assertIn("codegraphcontext-code", str(ctx.exception))
        run.assert_not_called()


class RuntimeRebindDerivationTests(unittest.TestCase):
    def test_live_disabled_disables_rebind_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _armed_boot_config(Path(tmp), disk_providers={})
            settings = lifecycle_settings_from_config(
                reload_provider_authority(config).apply(config)
            )
        self.assertFalse(settings["contextProviders"]["enabled"])


class BenchmarkProviderFilterTests(unittest.TestCase):
    def test_manifest_cannot_arm_outside_authority(self) -> None:
        kept = filter_benchmark_provider_ids(
            "case-1",
            ("grepai-memory", "codegraphcontext-code"),
            ("codegraphcontext-code",),
        )
        self.assertEqual(kept, ("codegraphcontext-code",))

    def test_empty_authority_filters_everything(self) -> None:
        kept = filter_benchmark_provider_ids(
            "case-1", ("grepai-memory", "codegraphcontext-code"), ()
        )
        self.assertEqual(kept, ())

    def test_none_authority_context_is_fail_closed(self) -> None:
        # Review B4: the direct-script path must not be an implicit bypass.
        with mock.patch.dict("os.environ", {}, clear=False):
            kept = filter_benchmark_provider_ids("case-1", ("grepai-memory",), None)
        self.assertEqual(kept, ())

    def test_env_escape_allows_unfiltered_direct_script_use(self) -> None:
        with mock.patch.dict("os.environ", {UNFILTERED_PROVIDERS_ENV: "1"}):
            kept = filter_benchmark_provider_ids("case-1", ("grepai-memory",), None)
        self.assertEqual(kept, ("grepai-memory",))

    def test_stale_registration_sweep_narrows_to_authority(self) -> None:
        # Review B3: persisted workspace registrations are the authority file
        # for sessions booted there — the sweep must strip disabled providers.
        with tempfile.TemporaryDirectory() as tmp:
            benchmarks_root = Path(tmp)
            settings_path = (
                benchmarks_root
                / "workspaces"
                / "case-1"
                / "with-memory"
                / CODEX_HARNESS_DIR
                / "mcp"
                / BENCHMARK_MCP_SETTINGS_NAME
            )
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "providers": {
                            "grepai-memory": {"scope": "benchmark"},
                            "codegraphcontext-code": {"scope": "benchmark"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            rewritten = disarm_stale_benchmark_registrations(
                benchmarks_root, ("codegraphcontext-code",)
            )
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            untouched = disarm_stale_benchmark_registrations(
                benchmarks_root, ("codegraphcontext-code",)
            )
            no_context = disarm_stale_benchmark_registrations(benchmarks_root, None)
        self.assertEqual(rewritten, [settings_path.as_posix()])
        self.assertEqual(sorted(data["providers"]), ["codegraphcontext-code"])
        self.assertEqual(untouched, [])  # idempotent
        self.assertEqual(no_context, [])  # None = no authority context: untouched


class FleetSetupLockTests(unittest.TestCase):
    def test_second_setup_waits_and_times_out_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "setup.lock"
            entered = threading.Event()
            release = threading.Event()

            def holder() -> None:
                with _fleet_setup_lock(root, timeout=30):
                    entered.set()
                    release.wait(timeout=30)

            thread = threading.Thread(target=holder, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(timeout=10))
            try:
                with self.assertRaises(RuntimeError) as ctx, _fleet_setup_lock(root, timeout=1):
                    pass  # pragma: no cover - must not be reached
                self.assertIn("containment R2", str(ctx.exception))
            finally:
                release.set()
                thread.join(timeout=10)
            # Released lock is re-acquirable.
            with _fleet_setup_lock(root, timeout=1):
                pass

    def test_lock_is_noop_when_uncontended(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            _fleet_setup_lock(Path(tmp) / "setup.lock", timeout=1),
        ):
            pass

    def test_lock_path_is_host_scoped_outside_prunable_roots(self) -> None:
        # Review B1/B2: runtime_install prunes providers/ under the coordination
        # root, and benchmark prepares run against workspace-local roots — the
        # lock must live outside every prunable/per-workspace tree.
        path = fleet_setup_lock_path()
        self.assertEqual(path.parent, Path(tempfile.gettempdir()))
        self.assertIn("agents-remember-provider-setup", path.name)


class MetricsTests(unittest.TestCase):
    def test_parsers(self) -> None:
        self.assertEqual(_parse_bytes("512MiB"), 512 * 1024**2)
        self.assertEqual(_parse_bytes("2GiB"), 2 * 1024**3)
        self.assertIsNone(_parse_bytes(""))
        self.assertEqual(_parse_mem_usage("100MiB / 2GiB"), (100 * 1024**2, 2 * 1024**3))
        self.assertEqual(_parse_percent("12.5%"), 12.5)
        self.assertIsNone(_parse_percent(""))
        labels = _parse_labels("agents-remember.provider=grepai-memory,a=b")
        self.assertEqual(labels["agents-remember.provider"], "grepai-memory")

    def test_sampler_reports_docker_ps_failure(self) -> None:
        with (
            mock.patch("agents_remember.providers.metrics.docker_command", return_value="docker"),
            mock.patch(
                "agents_remember.providers.metrics.run_command",
                return_value={"returncode": 1, "stdout": "", "stderr": "daemon down"},
            ),
        ):
            snapshot = sample_provider_containers(cwd=Path("."))
        self.assertEqual(snapshot.containers, [])
        assert snapshot.error is not None
        self.assertIn("daemon down", snapshot.error)

    def test_sampler_bounds_docker_ps_timeout_into_error_sample(self) -> None:
        # 260718-CHATS-L5F R6: a slow/hung docker daemon must not escape as a subprocess timeout
        # traceback every sampling interval; the sampler bounds it into an error-annotated snapshot.
        with (
            mock.patch("agents_remember.providers.metrics.docker_command", return_value="docker"),
            mock.patch(
                "agents_remember.providers.metrics.run_command",
                return_value={"returncode": None, "stdout": "", "stderr": "", "timedOut": True},
            ) as run,
        ):
            snapshot = sample_provider_containers(cwd=Path("."), timeout=20)
        # The sampler asks run_command to bound the timeout rather than raise it.
        assert run.call_args is not None and run.call_args.kwargs.get("allow_timeout") is True
        self.assertEqual(snapshot.containers, [])
        assert snapshot.error is not None
        self.assertIn("timed out", snapshot.error)

    def test_sampler_dockerless_host_yields_error_sample(self) -> None:
        with mock.patch(
            "agents_remember.providers.metrics.docker_command",
            side_effect=ContextProviderError("docker command not found"),
        ):
            snapshot = sample_provider_containers(cwd=Path("."))
        self.assertEqual(snapshot.containers, [])
        assert snapshot.error is not None
        self.assertIn("docker command not found", snapshot.error)

    def test_sampler_collects_labeled_containers_with_stats(self) -> None:
        ps_line = json.dumps(
            {
                "Names": "ar-grepai-postgres-i1",
                "State": "running",
                "Status": "Up 2 hours",
                "Labels": ("agents-remember.provider=grepai-memory,agents-remember.instance-id=i1"),
            }
        )
        stats_line = json.dumps(
            {
                "Name": "ar-grepai-postgres-i1",
                "MemUsage": "100MiB / 512MiB",
                "CPUPerc": "3.5%",
            }
        )

        def fake_run(
            command: list[str], *, cwd: Path, timeout: int, allow_timeout: bool = False
        ) -> dict[str, object]:
            if "stats" in command:
                return {"returncode": 0, "stdout": stats_line + "\n"}
            return {"returncode": 0, "stdout": ps_line + "\nnot-json\n"}

        with (
            mock.patch("agents_remember.providers.metrics.docker_command", return_value="docker"),
            mock.patch("agents_remember.providers.metrics.run_command", side_effect=fake_run),
        ):
            snapshot = sample_provider_containers(cwd=Path("."))
        self.assertEqual(snapshot.running_count, 1)
        [container] = snapshot.containers
        self.assertEqual(container.provider, "grepai-memory")
        self.assertEqual(container.instance, "i1")
        self.assertEqual(container.mem_bytes, 100 * 1024**2)
        self.assertEqual(container.mem_limit_bytes, 512 * 1024**2)
        self.assertEqual(container.cpu_percent, 3.5)
        self.assertEqual(container.restarts, 0)

    def test_sampler_tolerates_stats_failure_and_flags_restarting(self) -> None:
        ps_line = json.dumps(
            {
                "Names": "ar-cgc-falkordb-i1",
                "State": "restarting",
                "Status": "Restarting (137) 5 seconds ago",
                "Labels": "agents-remember.provider=codegraphcontext-code",
            }
        )

        def fake_run(
            command: list[str], *, cwd: Path, timeout: int, allow_timeout: bool = False
        ) -> dict[str, object]:
            if "stats" in command:
                return {"returncode": 1, "stdout": "", "stderr": ""}
            return {"returncode": 0, "stdout": ps_line + "\n"}

        with (
            mock.patch("agents_remember.providers.metrics.docker_command", return_value="docker"),
            mock.patch("agents_remember.providers.metrics.run_command", side_effect=fake_run),
        ):
            snapshot = sample_provider_containers(cwd=Path("."))
        [container] = snapshot.containers
        self.assertFalse(container.running)
        self.assertEqual(container.restarts, 1)
        self.assertIsNone(container.mem_bytes)
        self.assertIsNone(container.cpu_percent)

    def test_store_roundtrip_and_torn_line_tolerance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProviderMetricsStore(Path(tmp))
            snapshot = MetricsSnapshot(
                schema="ar-provider-metrics-sample/v1", sampledAt="2026-07-07T00:00:00+00:00"
            )
            store.record(snapshot)
            current = store.read_current()
            assert current is not None
            self.assertEqual(current["runningCount"], 0)
            # A torn append line (the crash class this master exists for) is skipped.
            with store.log_path.open("a", encoding="utf-8") as handle:
                handle.write('YAE"}\n')
            store.record(snapshot)
            rows = store.read_recent()
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
