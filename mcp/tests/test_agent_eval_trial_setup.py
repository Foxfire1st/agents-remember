"""Concrete fixture settings and native admission, without hosts or model execution."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import (
    CODEX_ACTOR,
    HERMES_ACTOR,
    HERMES_GPT_ACTOR,
    HERMES_KIMI_ACTOR,
)
from agent_evals.artifacts import EvidenceFile, read_object, write_object
from agent_evals.fixtures import CONSTRUCTION_LABEL, SETUP_FILES, CaseBundle, reset_case
from agent_evals.hermes_profiles import absolute_compaction, effort_declaration, hermes_route
from agent_evals.hermes_runtime import HermesRuntimeBinding, derive_hermes_runtime
from agent_evals.trial_native_setup import (
    citation_write_preflight,
    configured_intake,
    resolve_hermes_auxiliary,
    resolve_hermes_destination,
)
from agent_evals.trial_setup import ProvisionedTrial, TrialLaunch, provision_batch, provision_trial
from agents_remember.application.knowledge_bootstrap_admission import (
    BootstrapRefusal,
    admit_bootstrap_context,
)
from agents_remember.application.memory_quality.controller import run_memory_quality_request
from agents_remember.application.memory_tools import citation_fix_tool, route_index_refresh_tool
from agents_remember.errors import AuthorityError
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.serving.paseo.paseo_command import CommandResult, PaseoCli
from agents_remember.worktrees.worktree_contract import load_contract
from agents_remember_test_support.testing.agent_eval_checkout_provisioning import (
    assert_native_leaf_checkout_provisioning,
    assert_scratch_cli_config_binding,
)


class RecorderDaemon:
    """Native CLI boundary: install refuses existing IDs; reload reads current source."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.entry = None
        self.commands = []
        self.loaded = ""
        self.fail_reload = False

    def __call__(self, argv, _timeout):
        assert argv[-2:] == ["--home", str(self.home)]
        arguments = list(argv[2:-2])
        assert arguments[0] == "plugin" and arguments[-1] == "--json"
        action = arguments[1]
        self.commands.append(action)
        if action == "ls":
            return CommandResult(0, json.dumps([self.entry] if self.entry else []), "")
        if action == "install":
            if self.entry:
                return CommandResult(1, "", 'Plugin ID "ar-eval-recorder" is already configured')
            self.entry = {
                "id": "ar-eval-recorder",
                "path": arguments[2],
                "enabled": True,
                "status": "running",
            }
        elif action == "enable":
            assert self.entry is not None
            self.entry["enabled"] = True
        elif action == "reload" and self.fail_reload:
            return CommandResult(1, "", "fixture reload failure")
        elif action != "reload":
            raise AssertionError(arguments)
        assert self.entry is not None
        self.loaded = (Path(self.entry["path"]) / "index.server.ts").read_text()
        return CommandResult(0, json.dumps(self.entry), "")


def case_bundle(root: Path) -> CaseBundle:
    root.mkdir()
    setup = {key: key + ".txt" for key in SETUP_FILES}
    for key, filename in setup.items():
        (root / filename).write_text("fixture " + key)
    (root / "code.txt").write_text("def answer():\n    return 42\n")
    (root / "memory.txt").write_text("# Current answer\n\nThe function returns 42.\n")
    write_object(
        root / "case.json",
        {
            "schema": "ar-curator-eval-case/v1",
            "id": "CE01",
            "state": "admitted",
            "construction": {
                "kind": CONSTRUCTION_LABEL,
                "authority": "D637",
                "sourceEpisode": "test-only",
                "startingTrees": {"code": {"tree": "a" * 40}, "memory": {"tree": "b" * 40}},
            },
            "setup": setup,
            "specification": {
                "id": "CE01",
                "prospectiveGrade": {
                    "expected": "The fixture memory describes answer() as returning 42.",
                    "deterministicChecks": ["Check actual fixture material"],
                    "semanticRubric": ["The memory follows its source"],
                },
            },
            "effectRoots": ["projects", "reports", "fixture-state"],
            "files": [EvidenceFile.capture(root, item.name).to_dict() for item in root.iterdir()],
            "actorInputs": [
                {"source": "code.txt", "destination": "projects/code/answer.py"},
                {"source": "memory.txt", "destination": "projects/memory/onboarding/answer.py.md"},
                *[
                    {"source": setup[key], "destination": "inputs/" + setup[key]}
                    for key in ("prompt", "authority", "queue")
                ],
            ],
        },
    )
    return CaseBundle.read(root)


def runtime_manifest(root: Path) -> Path:
    """Owned native wrappers/APIs/SDK doubles, never the real installation."""
    owner = root / "owned-hermes"
    launcher = owner / "hermes_cli/_launchers.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        "def runtime_command(root,args=(),*,python=None):\n"
        "    return [str(python),'-I','-c','UNIT_OWNER_ONLY:'+str(root),*args]\n"
    )
    for name in ("hermes_cli/venv_sync.py", "pyproject.toml"):
        (owner / name).write_text("# unit-only source\n")
    (owner / "hermes_bootstrap.py").write_text("raise RuntimeError('actor bootstrap ran')\n")
    (owner / "hermes_cli/__init__.py").write_text("__release_date__='unit'\n")
    (owner / "hermes_cli/version_info.py").write_text(
        "from types import SimpleNamespace\n"
        "def get_version_info(): return SimpleNamespace(derived_version='0.0.unit',source='build')\n"
    )
    (owner / "hermes_cli/_early_recovery.py").write_text(
        "def interrupted_pull_marker(root): return root/'.git/hermes-update-pull'\n"
    )
    (owner / "hermes_cli/update_lock.py").write_text(
        "def update_marker_path():\n"
        " import os\n"
        " from pathlib import Path\n"
        " return Path(os.environ['HERMES_HOME'])/'unit-update-marker'\n"
    )
    venv = root / "prepared-hermes"
    python = venv / "bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (venv / "pyvenv.cfg").write_text(
        f"home = {Path(sys.base_prefix) / 'bin'}\ninclude-system-site-packages = false\n"
    )
    sdk = venv / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/openai"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").write_text("__version__='0.0.unit-test'\n")
    (owner / "pm").mkdir()
    (owner / "pm/environments.py").write_text(
        "from pathlib import Path\n"
        f"def committed_venv(root): return Path({str(venv)!r})\n"
        "def venv_python(venv): return venv/'bin/python'\n"
    )
    vendor = owner / ".hermes/bin/hermes"
    vendor.parent.mkdir(parents=True)
    body = (
        f"import sys\nsys.path.insert(0,{str(owner)!r})\nraise RuntimeError('actor bootstrap ran')"
    )
    vendor.write_text(
        f'#!/bin/sh\nexec {shlex.quote(str(Path(sys.executable).resolve()))} -I -c {shlex.quote(body)} "$@"\n'
    )
    vendor.chmod(0o755)
    published = root / "ambient-bin/hermes"
    published.parent.mkdir()
    published.write_text(f'#!/bin/sh\nexec {shlex.quote(str(vendor))} "$@"\n')
    published.chmod(0o755)
    metadata = root / "unit-runtime-inputs.json"
    write_object(
        metadata,
        {
            "sourceRoot": str(owner),
            "preparedPython": str(python),
            "ambientPath": str(published.parent),
        },
    )
    return metadata


class HermesDestinationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = HermesRuntimeBinding(
            (), self.root / "source", self.root / "python", (), {}, {}, ()
        )
        self.environment = {
            "HERMES_HOME": str(self.root / "private-home"),
            "OPENCODE_GO_BASE_URL": "https://synthetic-override.invalid/v1",
        }
        self.receipt = self.root / "destination.json"

    def test_native_resolved_host_is_persisted_without_sanitizing_actor_environment(self):
        done = CompletedProcess(
            [], 0, json.dumps({"status": "resolved", "resolvedHost": "opencode.ai"}), ""
        )
        with patch("agent_evals.trial_native_setup.subprocess.run", return_value=done) as run:
            result = resolve_hermes_destination(
                self.runtime, self.environment, receipt_path=self.receipt
            )
        self.assertEqual(read_object(self.receipt), result)
        self.assertTrue(result["matches"])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["resolvedHost"], "opencode.ai")
        arguments = run.call_args.kwargs
        self.assertEqual(
            arguments["env"]["OPENCODE_GO_BASE_URL"], self.environment["OPENCODE_GO_BASE_URL"]
        )
        self.assertEqual(arguments["env"]["HERMES_DISABLE_LAZY_INSTALLS"], "true")
        self.assertEqual(arguments["cwd"], self.environment["HERMES_HOME"])

    def test_foreign_and_failed_native_results_stop_with_sanitized_persisted_receipt(self):
        diagnostics = "SYNTHETIC-PRIVATE-DIAGNOSTIC"
        cases = [
            (
                CompletedProcess(
                    [],
                    0,
                    json.dumps({"status": "resolved", "resolvedHost": "foreign.invalid"}),
                    diagnostics,
                ),
                "host-mismatch",
                "foreign.invalid",
            ),
            (CompletedProcess([], 0, diagnostics, diagnostics), "native-resolution-failed", None),
            (CompletedProcess([], 0, "[]", diagnostics), "native-resolution-failed", None),
            (CompletedProcess([], 9, diagnostics, diagnostics), "native-resolution-failed", None),
            (
                CompletedProcess(
                    [],
                    0,
                    json.dumps({"status": "native-resolution-failed", "errorType": "AuthError"}),
                    diagnostics,
                ),
                "native-resolution-failed",
                None,
            ),
            (
                TimeoutExpired("unit-native-resolver", 60, diagnostics, diagnostics),
                "native-resolution-timeout",
                None,
            ),
        ]
        for response, status, host in cases:
            with self.subTest(status=status, response=type(response).__name__):
                effect = response if isinstance(response, BaseException) else None
                with (
                    patch(
                        "agent_evals.trial_native_setup.subprocess.run",
                        return_value=response,
                        side_effect=effect,
                    ),
                    patch(
                        "agent_evals.trial_native_setup.time.monotonic", side_effect=[100, 160.125]
                    ),
                    self.assertRaisesRegex(
                        AuthorityError, "binding-unavailable.*Hermes provider destination"
                    ) as error,
                ):
                    resolve_hermes_destination(
                        self.runtime, self.environment, receipt_path=self.receipt
                    )
                result = read_object(self.receipt)
                self.assertFalse(result["matches"])
                self.assertEqual(result["status"], status)
                self.assertEqual(result["resolvedHost"], host)
                self.assertEqual(result["limitSeconds"], 60)
                self.assertEqual(result["elapsedSeconds"], 60.125)
                self.assertIn("limit=60s; elapsed=60.125s", str(error.exception))
                self.assertNotIn(diagnostics, str(error.exception) + self.receipt.read_text())

    def test_auxiliary_host_cannot_pass_with_another_task_identity(self):
        for task in ("approval", "compression", "mcp"):
            with self.subTest(task=task):
                write_object(
                    Path(self.environment["HERMES_HOME"]) / "config.yaml",
                    {
                        "auxiliary": {
                            task: {
                                "provider": "deepseek",
                                "model": "deepseek-flash",
                                "reasoning_effort": "high",
                            }
                        }
                    },
                )
                payload = {
                    "status": "resolved",
                    "resolvedHost": "api.deepseek.com",
                    "taskSelectionObserved": "another-task",
                }
                with (
                    patch(
                        "agent_evals.trial_native_setup.subprocess.run",
                        return_value=CompletedProcess([], 0, json.dumps(payload), ""),
                    ),
                    self.assertRaisesRegex(AuthorityError, "task-binding-unavailable"),
                ):
                    resolve_hermes_auxiliary(
                        self.runtime, self.environment, receipt_root=self.root / "tasks"
                    )
                receipt = read_object(self.root / "tasks" / (task + ".json"))
                self.assertEqual(receipt["taskBinding"], "asserted by config, not observed")

    def test_auxiliary_receipts_keep_all_tasks_and_config_effort_distinct_from_main(self):
        tasks = ("approval", "compression", "mcp")
        write_object(
            Path(self.environment["HERMES_HOME"]) / "config.yaml",
            {
                "auxiliary": {
                    task: {
                        "provider": "deepseek",
                        "model": "deepseek-flash",
                        "reasoning_effort": "high",
                    }
                    for task in tasks
                }
            },
        )

        def answer(argv, **_kwargs):
            return CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "status": "resolved",
                        "resolvedHost": "api.deepseek.com",
                        "taskSelectionObserved": argv[-1],
                        "configuredReasoningEffort": "high",
                        "outgoingEffort": "high",
                        "taskInvocationObserved": False,
                    }
                ),
                "",
            )

        with patch("agent_evals.trial_native_setup.subprocess.run", side_effect=answer):
            rows = resolve_hermes_auxiliary(
                self.runtime, self.environment, receipt_root=self.root / "tasks"
            )
        self.assertEqual([row["task"] for row in rows], list(tasks))
        for row in rows:
            self.assertEqual(row["configuredReasoningEffort"], "high")
            self.assertEqual(row["outgoingEffort"], "high")
            self.assertFalse(row["taskInvocationObserved"])
            self.assertIn("unobserved", row["reachability"])
        self.assertFalse(rows[-1]["configHasMcpServers"])


class TrialSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = case_bundle(self.root / "case")
        self.candidate = self.root / "candidate"
        python = self.candidate / "mcp/.venv/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    def test_codex_batch_has_no_implicit_hermes_command(self) -> None:
        with patch("agent_evals.trial_setup._free_port", side_effect=[43121, 43122]):
            owner = provision_batch(self.root / "codex-batch", self.candidate)
        providers = read_object(owner.settings_path)["paseoRuntime"]["providers"]
        self.assertNotIn("hermes", providers)
        owner.configure_trial({"PATH": "/unit/actor-path", "HOME": str(self.root / "home")})
        self.assertNotIn("hermes", read_object(owner.settings_path)["paseoRuntime"]["providers"])

    def derive(self, metadata):
        return derive_hermes_runtime(
            ambient_path=metadata["ambientPath"],
            source_home=self.root / "source-home",
            probe_root=self.root / "runtime-probe",
        )

    def test_default_prepared_hermes_runtime_uses_owner_api_without_actor_bootstrap(self) -> None:
        selected = read_object(runtime_manifest(self.root))
        binding = self.derive(selected)
        self.assertEqual(binding.command[0], selected["preparedPython"])
        self.assertEqual(binding.command[1:4], ("-I", "-B", "-c"))
        self.assertEqual(binding.command[-1], "acp")
        self.assertEqual(binding.command[4], "UNIT_OWNER_ONLY:" + selected["sourceRoot"])
        self.assertEqual(binding.official_command[1:3], ("-I", "-c"))
        self.assertEqual(
            binding.environment,
            {"HERMES_DISABLE_LAZY_INSTALLS": "true"},
        )
        self.assertEqual(binding.receipt["probe"]["openaiVersion"], "0.0.unit-test")
        self.assertEqual(
            binding.receipt["ambient"]["versionString"], "Hermes Agent v0.0.unit (unit)"
        )
        self.assertTrue(binding.receipt["ambient"]["versionEqualsPrepared"])
        with (
            patch.dict(os.environ, {"PATH": selected["ambientPath"]}),
            patch("agent_evals.trial_setup._free_port", side_effect=[43121, 43122]),
        ):
            owner = provision_batch(self.root / "hermes-batch", self.candidate, hermes=True)
        owner.configure_trial({"PATH": "/unit/actor-shell-path", "HERMES_HOME": "/fresh/unit/home"})
        provider = read_object(owner.settings_path)["paseoRuntime"]["providers"]["hermes"]
        self.assertEqual(provider["command"], list(binding.command))
        self.assertEqual(provider["env"]["PATH"], "/unit/actor-shell-path")
        self.assertEqual(provider["env"]["HERMES_HOME"], "/fresh/unit/home")
        self.assertEqual(provider["env"]["HERMES_DISABLE_LAZY_INSTALLS"], "true")

    def test_unknown_or_mutated_native_runtime_fails_without_path_fallback(self) -> None:
        selected = read_object(runtime_manifest(self.root))
        with self.assertRaises(AuthorityError):
            self.derive({**selected, "ambientPath": str(self.root / "missing-bin")})
        outer = Path(selected["ambientPath"]) / "hermes"
        original = outer.read_text()
        outer.write_text("#!/bin/sh\nexec hermes acp\n")
        with self.assertRaisesRegex(AuthorityError, "outer-wrapper"):
            self.derive(selected)
        outer.write_text(original)
        binding = self.derive(selected)
        (Path(selected["sourceRoot"]) / "hermes_bootstrap.py").write_text("# changed owner\n")
        with self.assertRaises(AuthorityError):
            binding.verify()

    def test_prepared_runtime_missing_openai_is_refused_before_any_actor_bootstrap(self) -> None:
        selected = read_object(runtime_manifest(self.root))
        venv = Path(selected["preparedPython"]).parent.parent
        sdk = (
            venv
            / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/openai/__init__.py"
        )
        sdk.unlink()
        with self.assertRaisesRegex(AuthorityError, "OpenAI dependency"):
            self.derive(selected)

    def test_pending_native_repair_is_refused_without_running_recovery(self) -> None:
        selected = read_object(runtime_manifest(self.root))
        binding = self.derive(selected)
        marker = Path(selected["sourceRoot"]) / ".git/hermes-update-pull"
        marker.parent.mkdir()
        marker.write_text("unit-owned interrupted update; preserve this marker")
        with self.assertRaisesRegex(AuthorityError, "repair/update pending"):
            binding.verify()
        with self.assertRaisesRegex(AuthorityError, "repair/update pending"):
            self.derive(selected)
        self.assertEqual(marker.read_text(), "unit-owned interrupted update; preserve this marker")

    def test_uncertain_creation_fixture_waits_for_successful_supported_host_stop(self) -> None:
        with patch("agent_evals.trial_setup._free_port", side_effect=[43121, 43122]):
            owner = provision_batch(self.root / "deferred-batch", self.candidate)
        events = []
        owner.defer_cleanup(lambda: events.append("fixture-released"))
        self.assertEqual(events, [])
        with (
            patch.object(owner, "_command", side_effect=AuthorityError("stop failed")),
            self.assertRaisesRegex(AuthorityError, "stop failed"),
        ):
            owner.cleanup()
        self.assertEqual(events, [])
        self.assertEqual(len(owner.deferred_cleanups), 1)

        def stopped(operation, timeout):
            self.assertEqual((operation, timeout), ("stop", 120))
            events.append("host-stopped")
            return {"ok": True}

        with patch.object(owner, "_command", side_effect=stopped):
            owner.cleanup()
        self.assertEqual(events, ["host-stopped", "fixture-released"])
        self.assertFalse(owner.deferred_cleanups)

    def test_batch_sweeps_abnormal_trial_credentials_at_start_and_failed_stop(self) -> None:
        home = self.root / "abnormal-batch"
        old = home / "trials/old/hermes"
        old.mkdir(parents=True)
        names = (".env", "auth.json", "auth.lock")
        for name in names:
            (old / name).write_bytes(b"UNIT-ONLY-CREDENTIAL")
        with patch("agent_evals.trial_setup._free_port", side_effect=[43121, 43122]):
            owner = provision_batch(home, self.candidate)
        self.assertEqual(list(old.iterdir()), [], "batch start retained abnormal credentials")
        start = read_object(home / "agent-eval/credential-cleanup-start.json")
        self.assertEqual(start["removedPaths"], sorted(str(old / name) for name in names))
        for name in names:
            (old / name).write_bytes(b"UNIT-ONLY-CREDENTIAL")
        owner.requested = True
        with (
            patch.object(owner, "_command", side_effect=AuthorityError("stop failed")),
            self.assertRaisesRegex(AuthorityError, "stop failed"),
        ):
            owner.cleanup()
        self.assertEqual(list(old.iterdir()), [], "failed stop left credential files")
        end = read_object(home / "agent-eval/credential-cleanup-end.json")
        self.assertEqual(end["removedPaths"], start["removedPaths"])
        self.assertNotIn("UNIT-ONLY-CREDENTIAL", json.dumps([start, end]))

    def test_normal_cleanup_observably_removes_entire_private_hermes_home(self) -> None:
        fixture = reset_case(self.case, self.root / "cleanup-trials")
        home = fixture.root / "hermes"
        home.mkdir()
        for name in (".env", "auth.json", "auth.lock"):
            (home / name).write_bytes(b"UNIT-ONLY-CREDENTIAL")
        provisioned = cast(ProvisionedTrial, SimpleNamespace(fixture=fixture))
        self.assertTrue(home.is_dir())
        ProvisionedTrial.cleanup(provisioned)
        self.assertFalse(home.exists())
        self.assertFalse(fixture.root.exists())

    def provision(
        self,
        batch=None,
        *,
        destination=None,
        profile=HERMES_ACTOR,
        window=1_000_000,
        access=(None, None),
    ):
        def identity(definition):
            settings = read_object(Path(definition["args"][-1]))
            return {
                "coordinationRoot": settings["coordinationRoot"],
                "workspaceRoot": settings["workspaceRoot"],
                "servingBuild": {
                    "sourceDigest": "sha256:actual-test-candidate",
                    "packageRoot": str(
                        Path(definition["command"]).parents[3] / "mcp/src/agents_remember"
                    ),
                },
                "allowedRepoIds": ["code"],
            }

        with (
            patch("agent_evals.trial_setup.probe_tool_server", side_effect=identity),
            patch("agent_evals.trial_setup._free_port", side_effect=[43121, 43122]),
            patch(
                "agent_evals.trial_setup.resolve_hermes_destination",
                side_effect=destination,
                return_value={
                    "status": "pass",
                    "resolvedHost": "opencode.ai",
                    "matches": True,
                    "resolvedWindow": 1_000_000,
                },
            ),
            patch(
                "agent_evals.trial_setup.resolve_hermes_window",
                return_value={
                    "status": "resolved",
                    "resolvedWindow": window,
                    "windowSource": {"kind": "resolver", "basis": "unit fixture"},
                },
            ),
        ):
            runtime_root = self.root / "runtime-owner"
            runtime = runtime_root / "unit-runtime-inputs.json"
            if not runtime.exists():
                runtime = runtime_manifest(runtime_root)
            with patch.dict(
                os.environ,
                {
                    "PATH": read_object(runtime)["ambientPath"]
                    + os.pathsep
                    + os.environ.get("PATH", "")
                },
            ):
                owner = batch or provision_batch(
                    self.root / "batch-home", self.candidate, hermes=True
                )
            trial = provision_trial(
                self.case,
                self.root / "trials",
                self.candidate,
                batch_host=owner,
                launch=TrialLaunch(profile, *access),
            )
        self.addCleanup(lambda: trial.cleanup() if trial.fixture.root.exists() else None)
        return trial

    def test_kimi_compaction_is_400000_from_the_native_resolved_window(self):
        trial = self.provision(
            profile=HERMES_KIMI_ACTOR,
            window=1_048_576,
            destination=lambda *_args, **_kwargs: {
                "status": "pass",
                "resolvedHost": "openrouter.ai",
                "matches": True,
                "resolvedWindow": 1_048_576,
            },
        )
        config = read_object(trial.fixture.root / "hermes/config.yaml")
        self.assertEqual(config["compression"]["threshold"], 0.3814697265625)
        self.assertEqual(int(config["compression"]["threshold"] * 1_048_576), 400_000)
        binding = read_object(trial.fixture.root / "native-binding.json")["providerIsolation"]
        self.assertEqual(binding["compaction"]["resolvedWindow"], 1_048_576)
        self.assertEqual(binding["compaction"]["absoluteThreshold"], 400_000)

    def test_window_below_absolute_target_refuses_before_native_setup(self):
        with (
            patch("agent_evals.trial_setup.import_native_sides") as worklist,
            patch.object(ProvisionedTrial, "start_host") as host,
            self.assertRaisesRegex(AuthorityError, "cannot hold 400000"),
        ):
            self.provision(profile=HERMES_KIMI_ACTOR, window=399_999)
        worklist.assert_not_called()
        host.assert_not_called()

    def test_destination_is_checked_before_native_setup_and_never_starts_actor(self):
        events = []

        def destination(*_args, **_kwargs):
            events.append("destination")
            return {"status": "pass", "resolvedHost": "opencode.ai", "matches": True}

        def auxiliary(*_args, **_kwargs):
            events.append("auxiliary")
            return [{"task": task, "status": "pass"} for task in ("approval", "compression", "mcp")]

        with (
            patch("agent_evals.trial_setup.resolve_hermes_auxiliary", side_effect=auxiliary),
            patch(
                "agent_evals.trial_setup.import_native_sides",
                side_effect=lambda *_: events.append("worklist"),
            ),
            patch(
                "agent_evals.trial_setup.prepare_native_leaf",
                side_effect=lambda *_: events.append("leaf"),
            ),
            patch.object(
                ProvisionedTrial, "start_host", side_effect=AssertionError("actor boundary reached")
            ),
        ):
            trial = self.provision(destination=destination)
        self.assertEqual(events, ["destination", "auxiliary", "worklist", "leaf"])
        persisted = read_object(trial.fixture.root / "native-binding.json")
        self.assertEqual(persisted["providerIsolation"]["resolvedDestination"]["status"], "pass")

        self.assertEqual(
            [row["task"] for row in persisted["providerIsolation"]["resolvedAuxiliaryTasks"]],
            ["approval", "compression", "mcp"],
        )

    def test_gpt_access_pool_is_installed_before_native_destination_probe(self):
        source = self.root / "synthetic-source-auth.json"
        events = []

        def install(selected_source, home, expected_end):
            self.assertEqual(selected_source, source)
            self.assertTrue(home.is_dir())
            self.assertEqual(expected_end, 2000.0)
            events.append("access-pool")
            return {"status": "pass", "refreshCapability": False}

        def destination(_runtime, environment, **_kwargs):
            events.append("destination")
            self.assertTrue(Path(environment["CODEX_HOME"]).is_dir())
            self.assertEqual(list(Path(environment["CODEX_HOME"]).iterdir()), [])
            return {"status": "pass", "resolvedHost": "chatgpt.com", "matches": True}

        with patch("agent_evals.trial_setup.install_access_pool", side_effect=install, create=True):
            trial = self.provision(
                profile=HERMES_GPT_ACTOR,
                destination=destination,
                access=(source, 2000.0),
            )
        self.assertEqual(events, ["access-pool", "destination"])
        receipt = read_object(trial.fixture.root / "native-binding.json")["providerIsolation"]
        self.assertEqual(receipt["accessOnlyPool"], {"status": "pass", "refreshCapability": False})
        self.assertEqual(trial.native_capture_roots["codex"], trial.fixture.root / "code-home")
        with patch.object(trial.batch_host, "start", return_value={"ok": True}):
            trial.start_host()
        providers = read_object(trial.batch_host.settings_path)["paseoRuntime"]["providers"]
        self.assertEqual(
            providers["hermes"]["env"]["CODEX_HOME"], str(trial.fixture.root / "code-home")
        )
        self.assertNotIn("CODEX_HOME", providers["codex"]["env"])

    def test_access_pool_binding_is_required_only_for_the_gpt_profile(self):
        source = self.root / "synthetic-source-auth.json"
        cases = (
            (HERMES_GPT_ACTOR, None, None),
            (HERMES_GPT_ACTOR, source, None),
            (HERMES_GPT_ACTOR, None, 2000.0),
            (HERMES_ACTOR, source, 2000.0),
            (CODEX_ACTOR, source, 2000.0),
        )
        for profile, selected, expected_end in cases:
            with self.subTest(profile=profile.model, source=selected, expectedEnd=expected_end):
                with (
                    patch("agent_evals.trial_setup.install_access_pool", create=True) as install,
                    patch("agent_evals.trial_setup.resolve_hermes_destination") as destination,
                    self.assertRaisesRegex(AuthorityError, "access.*binding|access.*only"),
                ):
                    self.provision(
                        profile=profile,
                        access=(selected, expected_end),
                    )
                install.assert_not_called()
                destination.assert_not_called()

    def test_destination_failure_prevents_native_setup_and_actor(self):
        with (
            patch("agent_evals.trial_setup.import_native_sides") as worklist,
            patch("agent_evals.trial_setup.prepare_native_leaf") as leaf,
            patch.object(ProvisionedTrial, "start_host") as host,
            self.assertRaisesRegex(AuthorityError, "binding-unavailable.*host-mismatch"),
        ):
            self.provision(destination=AuthorityError("binding-unavailable: host-mismatch"))
        worklist.assert_not_called()
        leaf.assert_not_called()
        host.assert_not_called()

    def test_native_repository_wave_admission_and_material_alias(self) -> None:
        trial = self.provision()
        configured = trial.config.repositories["code"]
        self.assertEqual(configured.path, trial.code_root)
        assert configured.memory_root is not None
        self.assertEqual(configured.memory_root.resolve(), trial.memory_root)
        self.assertEqual(
            trial.memory_root, trial.project_root / "coordination/memory-repos/ar-code"
        )
        self.assertFalse(trial.memory_root.is_symlink())
        alias = trial.project_root / "projects/memory"
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.resolve(), trial.memory_root)
        self.assertEqual(
            (trial.code_root / "answer.py").read_text(), (self.case.path / "code.txt").read_text()
        )
        with trial.activated():
            admission = admit_bootstrap_context(trial.config, "code")
        self.assertNotIsInstance(admission, BootstrapRefusal, str(admission))
        self.assertEqual(trial.tool_build, "sha256:actual-test-candidate")
        self.assertFalse((trial.project_root / self.case.manifest["setup"]["expected"]).exists())
        self.assertIn(trial.identity, trial.binding_prompt())
        self.assertTrue(trial.scratch_root.is_dir())
        self.assertTrue(trial.scratch_root.is_relative_to(trial.project_root / "reports"))
        receipt = read_object(trial.fixture.root / "native-binding.json")
        self.assertEqual(receipt["hermesRuntime"], receipt["providerIsolation"]["runtime"])
        self.assertEqual(
            receipt["hermesRuntime"]["environment"],
            {"HERMES_DISABLE_LAZY_INSTALLS": "true"},
        )
        self.assertFalse((trial.native_capture_roots["hermes"] / "installs").exists())

    def native_leaf_case(self):
        document = read_object(self.case.path / "case.json")
        for name, content in (
            ("retired-base.py", "def retired(): return 1\n"),
            ("retired-base.md", "# Retired source\n"),
            ("overview.md", "# Governing overview\n"),
        ):
            (self.case.path / name).write_text(content)
            document["files"].append(EvidenceFile.capture(self.case.path, name).to_dict())
        document["actorInputs"].append(
            {"source": "overview.md", "destination": "projects/memory/onboarding/overview.md"}
        )
        document["setup"]["nativeLeaf"] = {
            "schema": "ar-eval-native-leaf/v1",
            "memoryTopology": "distinct-official-linked-leaf",
            "taskId": "CE11",
            "leafId": "CE11-L1",
            "workflowKind": "light-task",
            "code": "projects/code",
            "memory": "projects/memory",
            "authorization": "inputs/authority.txt",
            "requiredSubjects": ["onboarding:overview"],
            "citationProbeDocument": "answer.py.md",
            "baseline": {
                "schema": "ar-eval-native-baseline/v1",
                "code": [{"source": "retired-base.py", "path": "retired.py"}],
                "memory": [{"source": "retired-base.md", "path": "onboarding/retired.py.md"}],
            },
        }
        for side in ("code", "memory"):
            for entry in document["setup"]["nativeLeaf"]["baseline"][side]:
                data = (self.case.path / entry["source"]).read_bytes()
                entry["sourceTree"] = "1" * 40
                entry["sourceBlob"] = hashlib.sha1(
                    f"blob {len(data)}\0".encode() + data
                ).hexdigest()
        write_object(self.case.path / "case.json", document)
        self.case = CaseBundle.read(self.case.path)

    def test_native_leaf_publishes_canonical_intake_and_preserves_candidate_absence(self):

        self.native_leaf_case()
        trial = self.provision()
        binding = trial.native_setup["leaf"]
        contract = load_contract(Path(binding["contractPath"]))
        self.assertEqual(Path(binding["contractPath"]).name, "series-contract.md")
        self.assertEqual(Path(binding["contractPath"]).parent.name, "ce11-l1")
        self.assertEqual(trial.code_root.parent, trial.memory_root.parent)
        self.assertEqual(str(trial.code_root.parent), binding["worktreeGroup"])
        self.assertEqual(contract.code_base_commit, binding["codeBase"])
        self.assertFalse((trial.code_root / "retired.py").exists())
        self.assertFalse((trial.memory_root / "onboarding/retired.py.md").exists())

        self.assertTrue(Path(binding["publication"]["locatorPath"]).is_file())
        with trial.activated():
            result = configured_intake(trial.config, binding, trial.fixture.root / "intake")
        self.assertTrue(result["intakeAccepted"], result)
        self.assertEqual(result["missingSubjects"], [])
        self.assertIn("onboarding:overview", result["coveredSubjects"])
        self.assertFalse((trial.code_root / "retired.py").exists())
        self.assertTrue(all(Path(item["path"]).is_file() for item in result["reports"]))

    def test_native_leaf_publication_declares_its_own_checkout_test_mode(self):
        assert_native_leaf_checkout_provisioning(self, CODEX_ACTOR)

    def test_batch_cli_declares_fresh_child_test_mode_and_uses_trial_settings(self):
        assert_scratch_cli_config_binding(self, CODEX_ACTOR)

    def test_native_citation_probe_keeps_absent_candidate_subjects_absent(self):
        self.native_leaf_case()
        trial = self.provision()
        with trial.activated():
            receipt = citation_write_preflight(
                trial.config, trial.native_setup["leaf"], trial.fixture.root / "citation-probe"
            )
        self.assertTrue(receipt["toolOk"], receipt)
        self.assertFalse(receipt["writePassed"], "a no-op is not an actual write proof")
        self.assertEqual(receipt["writeOutcome"], "no-op")
        self.assertTrue(receipt["distinctRoots"])
        self.assertTrue(receipt["sameGitRepository"])
        self.assertTrue(receipt["absencePreserved"])
        self.assertEqual(receipt["restoredAbsentSubjects"], [])
        self.assertEqual(
            receipt["absentBefore"],
            [
                {"side": "code", "path": "retired.py"},
                {"side": "memory", "path": "onboarding/retired.py.md"},
            ],
        )

    def test_native_leaf_missing_locator_is_an_intake_refusal_not_quality_failure(self):

        self.native_leaf_case()
        trial = self.provision()
        binding = trial.native_setup["leaf"]
        Path(binding["publication"]["locatorPath"]).unlink()
        with trial.activated():
            result = configured_intake(trial.config, binding, trial.fixture.root / "intake")
        self.assertFalse(result["intakeAccepted"], result)
        self.assertEqual(result["qualityStatus"], "scope-refused")
        self.assertEqual(result["qualityCode"], "operation-location-adoption-required")
        self.assertEqual(result["missingSubjects"], ["onboarding:overview"])

    def test_native_leaf_requires_recorded_baseline_bindings(self):
        self.native_leaf_case()
        document = read_object(self.case.path / "case.json")
        document["setup"]["nativeLeaf"]["baseline"]["code"][0].pop("sourceBlob")
        write_object(self.case.path / "case.json", document)
        self.case = CaseBundle.read(self.case.path)
        with self.assertRaisesRegex(AuthorityError, "exact recorded tree and blob"):
            self.provision()

    def test_native_leaf_official_memory_differs_and_real_citation_write_is_admitted(self):
        self.native_leaf_case()
        trial = self.provision()
        binding = trial.native_setup["leaf"]
        with trial.activated():
            result = citation_fix_tool(
                trial.config, repo_id="code", contract_path=binding["contractPath"], dry_run=False
            )
        self.assertEqual(result["status"], "converted", result)
        official = trial.project_root / "coordination/memory-repos/ar-code"
        self.assertFalse(official.is_symlink())
        self.assertNotEqual(official.resolve(), trial.memory_root.resolve())
        self.assertEqual(str(official), binding["officialMemoryRoot"])
        self.assertTrue((trial.memory_root / ".git").is_file())
        self.assertFalse((trial.code_root / "retired.py").exists())
        self.assertFalse((trial.memory_root / "onboarding/retired.py.md").exists())

    def test_configured_official_quality_findings_do_not_masquerade_as_intake_refusal(self):

        trial = self.provision()
        with trial.activated():
            result = configured_intake(trial.config, None, trial.fixture.root / "intake")
        self.assertTrue(result["intakeAccepted"], result)
        self.assertEqual(result["mode"], "official-diagnostic")
        self.assertEqual(result["requiredSubjects"], [])
        self.assertIsNone(result["worklist"])

    def test_retained_edit_prompts_get_a_trial_local_scratch_binding(self) -> None:
        path = (
            Path(__file__).resolve().parents[2]
            / "mcp/tests/fixtures/hermes-edit-binding-retained.json"
        )
        rows = read_object(path)["rows"]
        self.assertEqual(len(rows), 8)
        for row in rows:
            with self.subTest(attempt=row["attempt"]):
                project = Path(row["projectRoot"])
                rejected = Path(row["rejectedWrite"]["path"])
                self.assertFalse(rejected.is_relative_to(project))
                self.assertEqual(row["lastWait"], {"state": "permission", "permission": "edit"})
                self.assertNotIn("writable scratch root", row["oldBinding"])
                instance = SimpleNamespace(
                    project_root=project,
                    code_root=project / "projects/code",
                    memory_root=project / "coordination/memory-repos/ar-code",
                    scratch_root=project / "reports/scratch",
                    identity=row["attempt"],
                    fixture=SimpleNamespace(case_id=row["case"]),
                    native_setup={},
                )
                prompt = ProvisionedTrial.binding_prompt(cast(ProvisionedTrial, instance))
                self.assertIn(str(instance.scratch_root), prompt)
                self.assertNotIn(str(rejected), prompt)
                self.assertTrue(instance.scratch_root.is_relative_to(project / "reports"))

    def test_native_quality_and_fixers_admit_the_physical_memory_topology(self) -> None:
        trial = self.provision()
        with trial.activated():
            quality = run_memory_quality_request(
                trial.config, MemoryQualitySyncRequest(repo_id="code", mode="sync")
            )
            citations = citation_fix_tool(trial.config, repo_id="code", dry_run=False)
            routes = route_index_refresh_tool(trial.config, repo_id="code", dry_run=False)
        # The tiny material can have content findings; a topology refusal is a setup fault.
        self.assertIn("checks", quality)
        self.assertNotEqual(quality.get("state"), "refused", quality)
        self.assertEqual(citations.get("status"), "converted", citations)
        self.assertTrue(routes.get("ok"), routes)
        self.assertEqual(
            (trial.project_root / "projects/memory/onboarding/answer.py.md").read_bytes(),
            (trial.memory_root / "onboarding/answer.py.md").read_bytes(),
        )

    def test_persisted_server_config_never_copies_auth_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "fixture-secret", "CODEX_HOME": str(self.root / "native-codex")},
        ):
            trial = self.provision()
        serialized = trial.tool_config_path.read_text()
        self.assertNotIn("fixture-secret", serialized)
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("CODEX_HOME", serialized)
        self.assertEqual(trial.environment()["OPENAI_API_KEY"], "fixture-secret")
        self.assertEqual(trial.native_capture_roots["codex"], self.root / "native-codex")
        self.assertEqual(trial.environment()["CODEX_HOME"], str(self.root / "native-codex"))
        self.assertFalse((trial.fixture.root / "code-home").exists())
        trial.batch_host.configure_trial(trial.environment())
        providers = read_object(trial.batch_host.settings_path)["paseoRuntime"]["providers"]
        self.assertNotIn("CODEX_HOME", providers["hermes"]["env"])
        self.assertNotIn("CODEX_HOME", providers["codex"]["env"])
        self.assertFalse((trial.fixture.root / "auth.json").exists())

    def test_old_hermes_arms_preserve_native_codex_selector_for_probes(self):
        original = self.root / "original-codex"
        observed = []

        def destination(_runtime, environment, **_kwargs):
            observed.append(environment["CODEX_HOME"])
            return {"status": "pass", "resolvedHost": "opencode.ai", "matches": True}

        with patch.dict("os.environ", {"CODEX_HOME": str(original)}):
            hermes = self.provision(destination=destination)
            kimi = self.provision(
                batch=hermes.batch_host, profile=HERMES_KIMI_ACTOR, destination=destination
            )
        self.assertEqual(observed, [str(original), str(original)])
        self.assertEqual(kimi.environment()["CODEX_HOME"], str(original))
        self.assertEqual(kimi.native_capture_roots["codex"], original)

    def test_host_lifecycle_uses_exact_fixture_authority_and_stops_before_release(self) -> None:
        trial = self.provision()
        second = self.provision(batch=trial.batch_host)
        self.assertNotEqual(trial.fixture.root, second.fixture.root)
        self.assertEqual(trial.paseo_home, second.paseo_home)
        completed = CompletedProcess([], 0, json.dumps({"ok": True}), "")
        provider_homes = []

        def native_provision(*_args, **_kwargs):
            provider_homes.append(
                read_object(trial.batch_host.settings_path)["paseoRuntime"]["providers"]["hermes"][
                    "env"
                ]["HERMES_HOME"]
            )
            return completed

        with patch("agent_evals.trial_setup.subprocess.run", side_effect=native_provision) as run:
            trial.start_host()
            second.start_host()
            trial.cleanup()
            self.assertTrue(trial.batch_host.started)
            second.cleanup()
            trial.batch_host.cleanup()
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual([arguments[5] for arguments in calls], ["provision", "provision", "stop"])
        self.assertEqual(
            provider_homes[:2],
            [str(trial.native_capture_roots["hermes"]), str(second.native_capture_roots["hermes"])],
        )
        self.assertTrue(
            all(arguments[-1] == str(trial.batch_host.settings_path) for arguments in calls)
        )
        self.assertFalse(trial.fixture.root.exists())
        self.assertTrue(trial.paseo_home.is_dir())

    def test_foreign_server_identity_is_refused_and_own_reset_released(self) -> None:
        with (
            patch(
                "agent_evals.trial_setup.probe_tool_server",
                return_value={"coordinationRoot": "/foreign"},
            ),
            patch("agent_evals.trial_setup._free_port", side_effect=[43121, 43122]),
            self.assertRaisesRegex(AuthorityError, "foreign coordinationRoot"),
        ):
            owner = provision_batch(self.root / "batch-home", self.candidate)
            provision_trial(
                self.case,
                self.root / "trials",
                self.candidate,
                batch_host=owner,
                launch=TrialLaunch(HERMES_ACTOR),
            )
        self.assertEqual(list((self.root / "trials").iterdir()), [])

    def recorder_source(self) -> Path:
        source = self.root / "recorder-source"
        source.mkdir()
        (source / "paseo-plugin.json").write_text('{"id":"ar-eval-recorder"}')
        (source / "index.server.ts").write_text("// initial source")
        return source

    def install_recorder(self, trial, source, daemon):
        def cli(settings):
            return PaseoCli(settings, runner=daemon)

        trial.batch_host.started = True
        with (
            patch("agent_evals.trial_setup.PaseoCli", side_effect=cli),
            patch(
                "agents_remember.serving.paseo.paseo_running_node.product_node",
                return_value=SimpleNamespace(node=Path(sys.executable)),
            ),
        ):
            trial.install_plugin(source)

    def test_recorder_reused_home_reloads_same_identity_after_source_copy(self) -> None:
        first = self.provision()
        source = self.recorder_source()
        daemon = RecorderDaemon(first.paseo_home)
        self.install_recorder(first, source, daemon)
        self.assertEqual(daemon.loaded, "// initial source")
        second = self.provision()  # A new batch owner of the same explicitly supplied home.
        (source / "index.server.ts").write_text("// current candidate source")
        self.install_recorder(second, source, daemon)
        self.assertEqual(daemon.loaded, "// current candidate source")
        self.assertEqual(daemon.commands, ["ls", "install", "ls", "ls", "reload", "ls"])
        assert daemon.entry is not None
        self.assertEqual(daemon.entry["id"], "ar-eval-recorder")
        self.assertEqual(daemon.entry["path"], str(first.paseo_home / "agent-eval/recorder-plugin"))

    def test_recorder_foreign_path_is_refused_without_lifecycle_mutation(self) -> None:
        trial = self.provision()
        daemon = RecorderDaemon(trial.paseo_home)
        daemon.entry = {"id": "ar-eval-recorder", "path": "/foreign", "enabled": True}
        with self.assertRaisesRegex(AuthorityError, "exact owned source path"):
            self.install_recorder(trial, self.recorder_source(), daemon)
        self.assertEqual(daemon.commands, ["ls"])
        self.assertFalse(trial.batch_host.recorder_installed)

    def test_recorder_reload_failure_retains_native_command_diagnostics(self) -> None:
        trial = self.provision()
        daemon = RecorderDaemon(trial.paseo_home)
        daemon.entry = {
            "id": "ar-eval-recorder",
            "path": str(trial.paseo_home / "agent-eval/recorder-plugin"),
            "enabled": False,
            "status": "disabled",
        }
        daemon.fail_reload = True
        with self.assertRaisesRegex(AuthorityError, "recorder command failed"):
            self.install_recorder(trial, self.recorder_source(), daemon)
        receipt = read_object(trial.fixture.root / "recorder-install.json")["commands"][-1]
        self.assertEqual(receipt["arguments"], ["reload", "ar-eval-recorder"])
        self.assertEqual(receipt["exitCode"], 1)
        self.assertEqual(receipt["stderr"], "fixture reload failure")
        self.assertEqual(daemon.commands, ["ls", "enable", "reload"])
        self.assertFalse(trial.batch_host.recorder_installed)


if __name__ == "__main__":
    unittest.main()


class HermesRouteTests(unittest.TestCase):
    def test_absolute_threshold_is_derived_from_each_resolved_window(self):
        for window, fraction in ((1_000_000, 0.40), (1_048_576, 0.3814697265625)):
            with self.subTest(window=window):
                result = absolute_compaction(window)
                self.assertEqual(result["resolvedWindow"], window)
                self.assertEqual(result["fraction"], fraction)
                self.assertEqual(int(window * fraction), 400_000)
                self.assertEqual(result["absoluteThreshold"], 400_000)
        self.assertNotEqual(absolute_compaction(1_200_000)["fraction"], 0.40)
        with self.assertRaisesRegex(AuthorityError, "cannot hold 400000"):
            absolute_compaction(399_999)

    def test_kimi_go_default_has_no_catalog_or_max_claim(self):
        route = hermes_route(HERMES_KIMI_ACTOR)
        self.assertEqual(route["provider"], "opencode-go")
        self.assertEqual(route["model"], "kimi-k3.1-pro")
        self.assertEqual(route["effortPolicy"], "provider-default")
        self.assertNotIn("catalogDeclaration", route)
        declaration = effort_declaration(HERMES_KIMI_ACTOR)
        assert declaration is not None
        self.assertEqual(declaration["effort"], "provider default")
        self.assertIn("unresolved", declaration["effortParity"])
        self.assertIsNone(effort_declaration(HERMES_ACTOR))

    def test_both_cross_destinations_refuse_and_both_own_destinations_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = HermesRuntimeBinding((), root / "source", root / "python", (), {}, {}, ())
            for profile, foreign in (
                (HERMES_ACTOR, "openrouter.ai"),
                (HERMES_KIMI_ACTOR, "openrouter.ai"),
                (HERMES_GPT_ACTOR, "opencode.ai"),
            ):
                route = hermes_route(profile)
                for host in (foreign, "api.deepseek.com", route["expectedHost"]):
                    with self.subTest(model=profile.model, host=host):
                        receipt = root / "destination.json"
                        response = CompletedProcess(
                            [], 0, json.dumps({"status": "resolved", "resolvedHost": host}), ""
                        )
                        with patch(
                            "agent_evals.trial_native_setup.subprocess.run",
                            return_value=response,
                        ):
                            if host != route["expectedHost"]:
                                with self.assertRaisesRegex(AuthorityError, "host-mismatch"):
                                    resolve_hermes_destination(
                                        runtime,
                                        {"HERMES_HOME": str(root)},
                                        receipt_path=receipt,
                                        profile=profile,
                                    )
                            else:
                                resolve_hermes_destination(
                                    runtime,
                                    {"HERMES_HOME": str(root)},
                                    receipt_path=receipt,
                                    profile=profile,
                                )
                        persisted = read_object(receipt)
                        self.assertEqual(persisted["expectedHost"], route["expectedHost"])
                        self.assertEqual(persisted["matches"], host == route["expectedHost"])
