"""The R71 role-choice cases: what a user, a repository and the host each decide."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.cli import (
    paseo_catalog,
    role_launch_preparation,
    role_launch_receipts,
)
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import (
    LaunchSelectionRefused,
    forget_launcher_catalogs,
    resolve_agent_selection,
)
from agents_remember.cli.paseo_launch import RoleLaunch, build_launch_call
from agents_remember.cli.role_instructions import compose_role_instructions
from agents_remember.cli.role_launch_preparation import (
    RoleHandoverRequest,
    _compile_handover,
    _prepare_role_folders,
)
from agents_remember.cli.role_launch_receipts import _observe_launch_choices, _public_execution
from agents_remember.cli.role_launch_workspace import workspace_folder
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    load_agentic_settings,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.registration.core import _register_identity_tools
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.role_launcher import (
    LauncherRole,
    RoleAgentOverride,
    RoleSelection,
)
from agents_remember.worktrees import role_reports
from agents_remember.worktrees.modules import cleanup as worktree_cleanup
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.role_reports import collect_group_reports, remove_group_role_folders
from agents_remember.worktrees.worktree_contract import WorktreeContract
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from test_paseo_catalog import (
    CATALOG,
    HARNESS_ORDER,
    PaseoCatalogTestCase,
    defaults,
)
from test_paseo_launch import ROLE_REFS, resolved_document


class R71RoleChoicesTests(PaseoCatalogTestCase):
    def write_user_settings(self, orchestration: dict[str, Any]) -> None:
        """Change the tested family in the user's own file; the host lives in the authority file."""
        path = user_settings_path_for_write()
        document = json.loads(path.read_text()) if path.is_file() else {}
        document["orchestration"] = orchestration
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))

    def setUp(self) -> None:
        super().setUp()
        catalog = copy.deepcopy(CATALOG)
        provider = catalog["providers"][0]
        provider["modes"] = [
            {"id": "auto", "label": "Default Permissions"},
            {"id": "full-access", "label": "Full Access"},
        ]
        provider["defaultModeId"] = "full-access"
        provider["models"][0]["features"] = [
            {
                "id": "service_tier",
                "label": "Speed",
                "type": "select",
                "value": "default",
                "options": [
                    {"id": "default", "label": "Default"},
                    {"id": "priority", "label": "Fast"},
                ],
            },
            {"id": "plan_mode", "label": "Plan", "type": "toggle", "value": False},
        ]
        provider["models"][0]["serviceTiers"] = [
            {"id": "default", "label": "Default"},
            {"id": "priority", "label": "Fast"},
        ]
        catalog["providers"].append(
            {
                "id": "claude",
                "label": "Claude",
                "modes": [
                    {"id": "default", "label": "Always Ask"},
                    {"id": "auto", "label": "Auto"},
                ],
                "models": [
                    {
                        "id": "opus",
                        "label": "Opus",
                        "isDefault": True,
                        "efforts": [],
                        "features": [],
                    }
                ],
            }
        )
        self.bridge(catalog)
        self.role = {
            **defaults("codex", "gpt-a", "high"),
            "role": "worker",
            "mode": None,
            "notes": None,
            "featureValues": {},
        }

    def select(self, role=None, override=None):
        return resolve_agent_selection(self.config, role or self.role, HARNESS_ORDER, override)

    def test_r71_host_fields_win_and_keep_each_source(self) -> None:
        role = {
            **self.role,
            "serviceTier": "fast",
            "hostRole": {
                "profile": {
                    "id": "first",
                    "name": "worker",
                    "provider": "codex",
                    "model": "gpt-a",
                    "modeId": "full-access",
                    "featureValues": {"plan_mode": True},
                    "notes": "German findings",
                }
            },
        }
        agent_id, applied = self.select(
            role, RoleAgentOverride(featureValues={"service_tier": "default"})
        )
        self.assertEqual(
            (
                agent_id,
                applied["model"],
                applied["effort"],
                applied["mode"],
                applied["featureValues"],
            ),
            (
                "codex",
                "gpt-a",
                "high",
                "full-access",
                {"service_tier": "default", "plan_mode": True},
            ),
        )
        choices = applied["choices"]
        self.assertEqual(
            [choices[key]["source"] for key in ("agent", "model", "effort", "mode", "notes")],
            ["host role", "host role", "our settings", "host role", "host role"],
        )
        self.assertEqual(choices["featureValues"]["service_tier"]["source"], "start call")
        self.assertEqual(choices["featureValues"]["plan_mode"]["source"], "host role")

    def test_r71_explicit_safe_mode_is_not_catalog_default_or_inherited_user_policy(self) -> None:
        self.assertEqual(self.select()[1]["mode"], "auto")
        self.assertEqual(self.select()[1]["choices"]["mode"]["source"], "least permissive mode")
        self.assertEqual(
            self.select(override=RoleAgentOverride(agentId="claude"))[1]["mode"], "default"
        )
        self.assertNotIn("mode", self.select(override=RoleAgentOverride(agentId="eve"))[1])

    def test_r71_provider_and_model_overrides_do_not_inherit_foreign_permission_choices(
        self,
    ) -> None:
        role = {**self.role, "mode": "full-access", "featureValues": {"plan_mode": True}}
        result = self.select(role, RoleAgentOverride(agentId="claude"))[1]
        self.assertEqual(result["mode"], "default")
        self.assertEqual(result["featureValues"], {})
        self.assertEqual(result["choices"]["omitted"][0]["key"], "featureValues.plan_mode")
        result = self.select(role, RoleAgentOverride(agentId="codex", modelId="gpt-b"))[1]
        self.assertEqual(result["featureValues"], {})
        self.assertNotIn("effort", result)

    def test_r71_unoffered_mode_feature_and_value_refuse_then_corrected_selection_succeeds(
        self,
    ) -> None:
        cases = [
            {"mode": "typo"},
            {"featureValues": {"missing": True}},
            {"featureValues": {"service_tier": "missing"}},
            {"featureValues": {"plan_mode": "true"}},
        ]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(LaunchSelectionRefused):
                self.select({**self.role, **bad})
        self.assertEqual(
            self.select({**self.role, "featureValues": {"plan_mode": False}})[1]["featureValues"],
            {"plan_mode": False},
        )
        self.assertFalse((self.config.coordination_root / "notes").exists())

    def test_r71_start_calls_cannot_supply_permission_authority(self) -> None:
        for key in ("plan_mode", "auto_accept", "unknown"):
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(LaunchSelectionRefused, "user's settings"),
            ):
                self.select(override=RoleAgentOverride(featureValues={key: False}))
        with self.assertRaises(ValidationError):
            RoleAgentOverride.model_validate({"mode": "full-access"})

    def test_r71_repository_authority_refusal_both_families_and_alias_merge(self) -> None:
        user = user_settings_path_for_write()
        repository = self.root / "code"
        local = repository / "system" / "settings.json"
        user.parent.mkdir(parents=True, exist_ok=True)
        local.parent.mkdir(parents=True)
        self.write_user_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "codex",
                        "serviceTier": "fast",
                        "mode": "auto",
                        "notes": "User text",
                    }
                }
            }
        )
        for family in ("roles", "rolesPerLevel"):
            for forbidden in (
                {"mode": "auto"},
                {"notes": "repo instruction"},
                {"featureValues": {"plan_mode": False}},
                {"featureValues": {"unknown": True}},
            ):
                roles = {"worker": forbidden}
                raw = roles if family == "roles" else {"leaf": roles}
                local.write_text(json.dumps({"orchestration": {family: raw}}))
                with (
                    self.subTest(family=family, key=forbidden),
                    self.assertRaisesRegex(AgenticSettingsError, "user-layer only") as raised,
                ):
                    load_agentic_settings(self.config.coordination_root, repository)
                self.assertIn(str(local), str(raised.exception))
        local.write_text(
            json.dumps(
                {
                    "orchestration": {
                        "roles": {
                            "worker": {
                                "model": "gpt-a",
                                "effort": "high",
                                "featureValues": {"service_tier": "priority"},
                            }
                        }
                    }
                }
            )
        )
        knobs = load_agentic_settings(
            self.config.coordination_root, repository
        ).resolved_role_knobs("worker")
        self.assertEqual(
            (knobs.model, knobs.effort, knobs.mode, knobs.notes),
            ("gpt-a", "high", "auto", "User text"),
        )
        local.write_text(
            json.dumps(
                {
                    "orchestration": {
                        "roles": {"worker": {"featureValues": {"service_tier": "default"}}}
                    }
                }
            )
        )
        with self.assertRaisesRegex(AgenticSettingsError, "conflict"):
            load_agentic_settings(self.config.coordination_root, repository)

    def test_r71_profile_exact_name_first_order_and_unavailable_reason(self) -> None:
        profiles = [
            {"id": "wrong", "name": "Worker"},
            {"id": "one", "name": "worker", "provider": "codex"},
            {"id": "two", "name": "worker", "provider": "claude"},
        ]
        with patch.object(
            paseo_catalog, "bridge_call", return_value={"profiles": profiles, "readAt": "now"}
        ):
            read = paseo_catalog.read_host_role(self.config, "worker")
        self.assertEqual(
            (read["profile"]["id"], read["passedOver"], read["readAt"]), ("one", ["two"], "now")
        )
        with patch.object(
            paseo_catalog, "bridge_call", side_effect=PaseoBridgeFailure("no-answer", "offline")
        ):
            read = paseo_catalog.read_host_role(self.config, "worker")
        self.assertEqual(read["state"], "unavailable")
        self.assertEqual(self.select({**self.role, "hostRole": read})[0], "codex")

    def test_r71_plain_text_overwrite_blocks_raw_digest_and_no_file_loading(self) -> None:
        named_file = self.root / "instructions.md"
        named_file.write_text("secret file text must not be loaded")
        text = f"Read {named_file} and use a missing skill."
        appended, record = compose_role_instructions(
            "BASELINE",
            "baseline-digest",
            {"value": text, "source": "our settings", "key": "roles.worker.notes"},
        )
        self.assertIn("BASELINE", appended)
        self.assertNotIn(named_file.read_text(), appended)
        self.assertFalse(record["baselineReplaced"])
        replacement, replaced = compose_role_instructions(
            "BASELINE",
            "baseline-digest",
            {"value": "\n  <overwrite>  \nNew instructions", "source": "host role", "key": "notes"},
        )
        self.assertNotIn("BASELINE", replacement)
        self.assertNotIn("<overwrite>", replacement)
        self.assertTrue(replaced["baselineReplaced"])
        self.assertEqual(replaced["baselineDigest"], "baseline-digest")
        self.assertNotEqual(record["semanticDigest"], replaced["semanticDigest"])
        self.assertEqual(
            compose_role_instructions("BASELINE", "baseline-digest", {})[1]["semanticDigest"],
            "baseline-digest",
        )
        self.assertIn(
            "<overwrite>",
            compose_role_instructions(
                "BASELINE", "baseline-digest", {"value": "Hello\n<overwrite>"}
            )[0],
        )
        with self.assertRaisesRegex(LaunchSelectionRefused, "without instructions"):
            self.select({**self.role, "notes": "<overwrite>\n "})

    def test_r71_reports_collect_and_survive_removal_without_following_legacy_alias(self) -> None:
        for count in (2, 9):
            group = self.root / f"group-{count}"
            local = group / "role-reports" / "role-launch"
            canonical = self.root / f"task-{count}" / "notes" / "reports"
            local.mkdir(parents=True)
            canonical.mkdir(parents=True)
            for index in range(count):
                (local / f"{index}.md").write_text(f"report-{index}")
            (group / "scratch").mkdir()
            (group / "scratch" / "temporary").write_text("discard")
            (group / "task-reports").symlink_to(canonical, target_is_directory=True)
            contract = cast(
                WorktreeContract,
                SimpleNamespace(worktree_group=group, task_root=canonical.parent.parent),
            )
            self.assertEqual(collect_group_reports(contract, dry_run=True)["files"], count)
            self.assertFalse((canonical / "role-launch").exists())
            remove_group_role_folders(contract, dry_run=False)
            self.assertEqual(len(list((canonical / "role-launch").glob("*.md"))), count)
            self.assertFalse((group / "role-reports").exists())
            self.assertFalse((group / "task-reports").is_symlink())
            self.assertFalse((group / "scratch").exists())
            self.assertEqual((canonical / "role-launch" / "0.md").read_text(), "report-0")

    def test_r71_failed_collection_preserves_source_and_link_target(self) -> None:
        group = self.root / "group"
        source = group / "role-reports"
        source.mkdir(parents=True)
        (source / "report.md").write_text("must survive")
        canonical = self.root / "task" / "notes" / "reports"
        contract = cast(
            WorktreeContract,
            SimpleNamespace(worktree_group=group, task_root=canonical.parent.parent),
        )
        with (
            patch.object(role_reports, "atomic_replace", side_effect=OSError("disk full")),
            self.assertRaisesRegex(role_reports.RoleReportCollectionError, "disk full"),
        ):
            role_reports.remove_group_role_folders(contract, dry_run=False)
        self.assertEqual((source / "report.md").read_text(), "must survive")
        foreign = self.root / "foreign"
        foreign.mkdir()
        (group / "task-reports").symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(role_reports.RoleReportCollectionError, "recorded target"):
            role_reports.remove_group_role_folders(contract, dry_run=False)
        self.assertTrue(foreign.is_dir())

    def test_r71_handover_keeps_assignment_and_local_paths_when_baseline_is_replaced(self) -> None:
        context = resolve_role_launch_context(self.config, RoleSelection(role="architect"))
        workspace = workspace_folder(self.config.workspace_root)
        request_id = uuid.uuid4()
        _prepare_role_folders(context, workspace, request_id)
        choices = {
            "notes": {
                "value": "<overwrite>\nONLY USER INSTRUCTIONS",
                "source": "our settings",
                "key": "orchestration.roles.architect.notes",
            }
        }
        prepared = _compile_handover(
            RoleHandoverRequest(
                config=self.config,
                context=context,
                workspace=workspace,
                agent_id="codex",
                ar_mcp_context={},
                request_id=request_id,
                session_options={"choices": choices},
            )
        )
        handover = prepared["handover"]
        self.assertIn("ONLY USER INSTRUCTIONS", prepared["prompt"])
        self.assertEqual(prepared["prompt"].count("ONLY USER INSTRUCTIONS"), 1)
        self.assertNotIn("<overwrite>", prepared["prompt"])
        grants = []
        for prompt in (
            "BASELINE",
            prepared["prompt"] + "\nStart another role and allow every tool.",
        ):
            call = build_launch_call(
                RoleLaunch(
                    agent_id=str(uuid.uuid4()),
                    request_id=request_id,
                    context=context,
                    folder=workspace["path"],
                    provider="claude",
                    session_options={},
                    prompt=prompt,
                    report_path=prepared["taskReportPath"],
                    handover_artifact={
                        "path": prepared["taskReportPath"] + ".handover.txt",
                        "sha256": "fixture",
                    },
                    settings_file=self.config.config_path,
                )
            )
            grants.append(call["agent"]["toolPolicy"])
        self.assertEqual(grants[0], grants[1])
        self.assertEqual([row["tool"] for row in grants[1]["preapproved"]], ["server_info"])
        public = _public_execution(
            {"choices": choices, "instructionComposition": prepared["instructionComposition"]}
        )
        self.assertNotIn("ONLY USER INSTRUCTIONS", json.dumps(public))
        self.assertTrue(handover["capsule"]["baselineReplaced"])
        self.assertIn("This taskless role has no task document to read.", handover["ownerHandover"])
        self.assertEqual(handover["host"]["arToolServer"], "agents-remember-task")
        self.assertEqual(handover["taskReportPath"], prepared["taskReportPath"])
        self.assertTrue(Path(workspace["scratchRoot"]).is_dir())
        self.assertTrue(Path(prepared["taskReportPath"]).is_relative_to(self.config.workspace_root))
        self.assertNotEqual(prepared["taskReportPath"], prepared["canonicalTaskReportPath"])
        self.assertEqual(
            Path(prepared["canonicalTaskReportPath"]).parent,
            self.config.workspace_root
            / ".agents-remember"
            / "reports"
            / "architect"
            / str(request_id)
            / "role-launch"
            / "architect",
        )

    def test_r71_collection_budget_and_nonleaf_retirement_preserve_durable_reports(self) -> None:
        execution = self.root / ".agents-remember" / "role-executions" / "request"
        source = execution / "role-reports"
        source.mkdir(parents=True)
        (execution / "scratch").mkdir()
        canonical = self.root / "durable"
        for index in range(2):
            (source / f"{index}.md").write_text("report")
        receipt = {
            "requestId": "request",
            "workspace": {
                "path": str(self.root),
                "taskReportAccessRoot": str(source),
                "taskReportRoot": str(canonical),
                "scratchRoot": str(execution / "scratch"),
            },
        }
        with (
            patch.object(role_reports, "MAX_COLLECTION_FILES", 1),
            self.assertRaisesRegex(role_reports.RoleReportCollectionError, "budget"),
        ):
            role_reports.close_execution_role_folders(receipt)
        self.assertTrue(source.is_dir())
        role_reports.close_execution_role_folders(receipt)
        self.assertFalse(execution.exists())
        self.assertEqual((canonical / "1.md").read_text(), "report")
        self.assertEqual(receipt["reportCollection"]["state"], "collected-closed")
        self.assertNotIn("reportCollectionFiles", receipt)
        # A recreated source is new evidence, even after an earlier terminal collection.
        source.mkdir(parents=True)
        (source / "later.md").write_text("later evidence")
        role_reports.collect_execution_reports(receipt)
        self.assertEqual((canonical / "later.md").read_text(), "later evidence")

    def test_r71_unknown_modes_refuse_and_fully_listed_empty_modes_remain_distinct(self) -> None:
        row = {"id": "codex", "models": [], "listingError": "still loading"}
        for stated in (None, "auto"):
            with (
                self.subTest(stated=stated),
                self.assertRaisesRegex(LaunchSelectionRefused, "codex.*still loading"),
            ):
                paseo_catalog._resolve_mode(
                    stated, {"source": "our settings"}, paseo_catalog._agent_row(row), "worker"
                )
        mode, choice = paseo_catalog._resolve_mode(
            None, {}, paseo_catalog._agent_row({"id": "codex", "models": [], "modes": []}), "worker"
        )
        self.assertIsNone(mode)
        self.assertIn("no named", choice["reason"])

    def test_r71_collection_reuses_unchanged_file_metadata_for_two_evidence_sizes(self) -> None:
        for size in (1024, 64 * 1024**2):
            local = self.root / f"reports-{size}"
            local.mkdir()
            with (local / "evidence.bin").open("wb") as stream:
                stream.truncate(size)
            receipt = {
                "workspace": {
                    "taskReportAccessRoot": str(local),
                    "taskReportRoot": str(self.root / f"canonical-{size}"),
                }
            }
            self.assertEqual(role_reports.collect_execution_reports(receipt)["copiedFiles"], 1)
            with patch.object(
                role_reports,
                "_file_digest",
                side_effect=AssertionError("unchanged bytes must not be read"),
            ):
                self.assertEqual(role_reports.collect_execution_reports(receipt)["copiedFiles"], 0)
            with patch.object(
                role_launch_receipts,
                "collect_execution_reports",
                side_effect=AssertionError("read must not collect"),
            ):
                self.assertEqual(
                    _public_execution(receipt)["reportCollection"]["state"], "collected"
                )

    def test_r71_refusals_name_exact_settings_and_host_profile_sources(self) -> None:
        for extra, words in [
            (
                {
                    "mode": "bad",
                    "fieldOrigins": {
                        "mode": {
                            "source": "user.json",
                            "key": "orchestration.roles.worker.mode",
                            "provider": "codex",
                        }
                    },
                },
                ("user.json", "orchestration.roles.worker.mode"),
            ),
            (
                {
                    "hostRole": {
                        "profile": {
                            "id": "profile-one",
                            "name": "worker",
                            "provider": "codex",
                            "modeId": "bad",
                        }
                    }
                },
                ("profile-one", "worker", "modeId"),
            ),
        ]:
            with self.subTest(extra=extra), self.assertRaises(LaunchSelectionRefused) as error:
                self.select({**self.role, **extra})
            for word in words:
                self.assertIn(word, str(error.exception))

    def test_r71_observed_mode_and_feature_mismatch_is_publicly_warned(self) -> None:
        receipt = {
            "choices": {
                "mode": {"value": "auto", "source": "our settings"},
                "featureValues": {"plan_mode": {"value": True, "source": "host role"}},
            }
        }
        _observe_launch_choices(receipt, {"mode": "auto", "featureValues": {"plan_mode": True}})
        self.assertNotIn("warning", _public_execution(receipt))
        _observe_launch_choices(
            receipt, {"mode": "full-access", "featureValues": {"plan_mode": False}}
        )
        self.assertIn("mode stated 'auto'", _public_execution(receipt)["warning"])
        self.assertIn("featureValues.plan_mode", receipt["warning"])

    def test_r71_each_role_grant_excludes_a_second_configured_ar_server(self) -> None:
        foreign = "agents-remember-foreign"
        harness_config = self.root / "harness-config.json"
        configured = {"mcpServers": {foreign: {"command": "never-started", "args": []}}}
        harness_config.write_text(json.dumps(configured))
        before = harness_config.read_bytes()
        # ROLE_REFS is keyed by plain str; the grant is built per LauncherRole,
        # in the catalogue's own role order.
        roles: tuple[LauncherRole, ...] = (
            "architect",
            "investigator",
            "orchestrator",
            "manager",
            "worker",
            "reviewer",
            "curator",
        )
        for role in roles:
            references = ROLE_REFS[role]
            documents = {
                key: resolved_document(self.config, ref, key) for key, ref in references.items()
            }
            context = RoleLaunchContext(
                role,
                documents.get("sprintDocumentRef"),
                documents.get("masterDocumentRef"),
                documents.get("taskDocumentRef"),
                documents.get("taskDocumentRef")
                or documents.get("masterDocumentRef")
                or documents.get("sprintDocumentRef"),
            )
            for provider in ("claude", "codex", "pi"):
                with self.subTest(role=role, provider=provider):
                    call = build_launch_call(
                        RoleLaunch(
                            agent_id=str(uuid.uuid4()),
                            request_id=uuid.uuid4(),
                            context=context,
                            folder=str(self.config.workspace_root),
                            provider=provider,
                            session_options={},
                            prompt="Read your own task file.",
                            report_path=str(self.root / "report.md"),
                            handover_artifact={
                                "path": str(self.root / "handover"),
                                "sha256": "fixture",
                            },
                            settings_file=self.config.config_path,
                        )
                    )
                    agent = call["agent"]
                    self.assertEqual(set(agent["mcpServers"]), {"agents-remember-task"})
                    grants = agent.get("toolPolicy", {}).get("preapproved", [])
                    expected = (
                        [{"kind": "mcp", "server": "agents-remember-task", "tool": "server_info"}]
                        if provider == "claude"
                        else []
                    )
                    self.assertEqual(grants, expected)
                    self.assertNotIn(foreign, json.dumps(grants))
                    self.assertNotIn("*", json.dumps(grants))
        self.assertEqual(harness_config.read_bytes(), before)
        self.assertIn(foreign, json.loads(before)["mcpServers"])

    def test_r71_completed_cleanup_collects_before_the_same_role_folder_removal(self) -> None:
        group = self.root / "completed-group"
        local = group / "role-reports" / "worker" / "request"
        local.mkdir(parents=True)
        (local / "report.md").write_text("Completed report with retained evidence")
        canonical = self.root / "task" / "notes" / "reports" / "worker" / "request" / "report.md"
        contract = cast(
            WorktreeContract,
            SimpleNamespace(
                kind="leaf",
                worktree_group=group,
                task_root=self.root / "task",
                coordination_root=self.config.coordination_root,
                code_worktree=group / "code",
                code_work_branch="ar/fixture",
            ),
        )
        events = []

        def collect(contract, *, dry_run):
            events.append("collect")
            return collect_group_reports(contract, dry_run=dry_run)

        def remove(contract, *, dry_run):
            self.assertEqual(events, ["collect"])
            self.assertEqual(canonical.read_text(), "Completed report with retained evidence")
            events.append("remove")
            return remove_group_role_folders(contract, dry_run=dry_run)

        services = SimpleNamespace(
            provider_lifecycle=SimpleNamespace(
                remove_tree=lambda path, **_kwargs: {"path": str(path), "removed": False}
            )
        )
        with (
            patch.object(worktree_cleanup, "collect_group_reports", side_effect=collect),
            patch.object(worktree_cleanup, "remove_group_role_folders", side_effect=remove),
            patch.object(worktree_cleanup, "_terminal_mutation_authority", return_value=object()),
            patch.object(worktree_cleanup, "_removed_worktrees", return_value={}),
            patch.object(worktree_cleanup, "_deleted_branches", return_value={}),
            patch.object(worktree_cleanup, "remove_drift_snapshot", return_value={"removed": True}),
            patch.object(worktree_cleanup, "worktree_services", return_value=services),
        ):
            worktree_cleanup._cleanup_terminal_outputs(
                WorktreeArgs(dry_run=False, teardown_providers=False),
                contract,
                cast(TerminalPreflight, SimpleNamespace(branches={})),
            )
        self.assertEqual(events, ["collect", "remove"])
        self.assertFalse((group / "role-reports").exists())
        self.assertEqual(canonical.read_text(), "Completed report with retained evidence")

    def test_r71_public_reader_has_no_caller_path_or_mutating_operation(self) -> None:
        server = FastMCP("fixture")
        _register_identity_tools(
            server,
            self.config,
            ServingBuildPayload(version="fixture", bootedAt="2026-10-06T00:00:00Z"),
        )
        listed = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        for name in ("server_info",):
            tool = listed[name]
            self.assertEqual(tool.inputSchema.get("properties", {}), {})
            assert tool.annotations is not None
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
        self.assertEqual(set(listed), {"server_info"})

    def test_r71_layer_binding_keeps_codex_auto_from_becoming_claude_auto(self) -> None:
        user = user_settings_path_for_write()
        code = self.config.workspace_root / "demo"
        local = code / "system" / "settings.json"
        user.parent.mkdir(parents=True, exist_ok=True)
        local.parent.mkdir(parents=True)
        self.write_user_settings(
            {
                "roles": {
                    "worker": {
                        "harness": "codex",
                        "mode": "auto",
                        "model": "gpt-a",
                        "effort": "high",
                        "featureValues": {"plan_mode": True},
                        "notes": "User-role instructions",
                    }
                }
            }
        )
        local.write_text(
            json.dumps({"orchestration": {"roles": {"worker": {"harness": "claude"}}}})
        )
        settings_path = self.root / "layer-reader.json"
        settings_path.write_text(
            json.dumps(
                {
                    "coordinationRoot": str(self.config.coordination_root),
                    "workspaceRoot": str(self.config.workspace_root),
                    "repositories": {"demo": {"path": str(code)}},
                }
            )
        )
        config = load_config(settings_path)
        context = cast(
            Any,
            SimpleNamespace(
                role="worker",
                effective_task=SimpleNamespace(ref=SimpleNamespace(repository="demo")),
            ),
        )
        role, order = role_launch_preparation._role_defaults(config, context)
        # Discovery remains the test's offline host catalog, never a live process.
        agent_id, options = resolve_agent_selection(self.config, role, order, None)
        self.assertEqual((agent_id, options["mode"]), ("claude", "default"))
        self.assertNotIn("model", options)
        self.assertNotIn("effort", options)
        self.assertEqual(options["featureValues"], {})
        self.assertEqual(options["choices"]["notes"]["value"], "User-role instructions")
        ignored = [
            row
            for row in options["choices"]["omitted"]
            if row.get("key") == "mode" and row.get("origin")
        ]
        self.assertEqual(ignored[0]["origin"]["provider"], "codex")
        self.assertEqual(ignored[0]["origin"]["source"], str(user))
        self.assertEqual(ignored[0]["origin"]["key"], "orchestration.roles.worker.mode")
        self.assertEqual(options["choices"]["mode"]["source"], "least permissive mode")
        # A higher user-level choice binds to its own effective provider, without a new key.
        raw = json.loads(user.read_text())
        raw["orchestration"]["rolesPerLevel"] = {
            "leaf": {"worker": {"harness": "claude", "mode": "auto"}}
        }
        user.write_text(json.dumps(raw))
        role, order = role_launch_preparation._role_defaults(config, context)
        self.assertEqual(resolve_agent_selection(self.config, role, order, None)[1]["mode"], "auto")

    def test_r71_per_level_origins_bind_at_the_authored_layer_and_keep_provider_controls(
        self,
    ) -> None:
        user = user_settings_path_for_write()
        code = self.config.workspace_root / "demo"
        local = code / "system/settings.json"
        user.parent.mkdir(parents=True, exist_ok=True)
        local.parent.mkdir(parents=True, exist_ok=True)
        settings_path = self.root / "level-origin-runtime.json"
        settings_path.write_text(
            json.dumps(
                {
                    "coordinationRoot": str(self.config.coordination_root),
                    "workspaceRoot": str(self.config.workspace_root),
                    "repositories": {"demo": {}},
                }
            )
        )
        config = load_config(settings_path)
        context = cast(
            Any,
            SimpleNamespace(
                role="worker",
                effective_task=SimpleNamespace(ref=SimpleNamespace(repository="demo")),
            ),
        )
        cases = [
            (
                "spawn-cross-provider",
                {"spawn": {"harness": "codex"}},
                {"spawn": {"harness": "claude"}},
                {},
                "claude",
                "default",
            ),
            (
                "spawn-same-provider",
                {"spawn": {"harness": "codex"}},
                {"spawn": {"harness": "codex"}},
                {},
                "codex",
                "auto",
            ),
            (
                "role-inheritance",
                {"roles": {"worker": {"harness": "codex"}}},
                {"roles": {"worker": {"harness": "claude"}}},
                {},
                "claude",
                "default",
            ),
            (
                "explicit-level-provider",
                {"roles": {"worker": {"harness": "claude"}}},
                {"spawn": {"harness": "claude"}},
                {"harness": "codex"},
                "codex",
                "auto",
            ),
            (
                "later-level-model",
                {"roles": {"worker": {"harness": "codex"}}},
                {
                    "roles": {"worker": {"harness": "claude"}},
                    "rolesPerLevel": {"leaf": {"worker": {"model": "opus"}}},
                },
                {},
                "claude",
                "default",
            ),
        ]
        for name, global_values, repo_values, own_values, expected_agent, expected_mode in cases:
            with self.subTest(case=name):
                authored_values = {
                    **global_values,
                    "rolesPerLevel": {"leaf": {"worker": {"mode": "auto", **own_values}}},
                }
                self.write_user_settings(authored_values)
                local.write_text(json.dumps({"orchestration": repo_values}))
                with patch.object(
                    role_launch_preparation,
                    "read_host_role",
                    return_value={"profile": {}, "state": "no-match"},
                ):
                    role, order = role_launch_preparation._role_defaults(config, context)
                agent, options = resolve_agent_selection(self.config, role, order, None)
                self.assertEqual((agent, options["mode"]), (expected_agent, expected_mode))
                self.assertEqual(role["fieldOrigins"]["mode"]["provider"], "codex")
                self.assertEqual(role["fieldOrigins"]["mode"]["source"], str(user))
                self.assertEqual(
                    role["fieldOrigins"]["mode"]["key"],
                    "orchestration.rolesPerLevel.leaf.worker.mode",
                )
                if name == "later-level-model":
                    self.assertEqual(role["fieldOrigins"]["model"]["provider"], "claude")
                    self.assertEqual(options["model"], "opus")
                if expected_agent == "claude":
                    self.assertEqual(options["choices"]["mode"]["source"], "least permissive mode")
                    self.assertTrue(
                        any(
                            row.get("origin", {}).get("provider") == "codex"
                            and row.get("key") == "mode"
                            for row in options["choices"]["omitted"]
                        )
                    )

    def test_r71_collection_cache_churn_reaches_a_bounded_fixed_point_and_retires(self) -> None:
        for turns in (4, 12):
            with self.subTest(turns=turns):
                source = self.root / f"churn-{turns}"
                source.mkdir()
                canonical = self.root / f"collected-{turns}"
                receipt = {
                    "workspace": {
                        "taskReportAccessRoot": str(source),
                        "taskReportRoot": str(canonical),
                    }
                }
                with patch.object(role_reports, "MAX_COLLECTION_FILES", 4):
                    for index in range(turns):
                        path = source / f"{index}.md"
                        path.write_text("report")
                        self.assertEqual(
                            role_reports.collect_execution_reports(receipt)["files"], 1
                        )
                        self.assertEqual(len(receipt["reportCollectionFiles"]), 1)
                        with patch.object(
                            role_reports,
                            "_file_digest",
                            side_effect=AssertionError("reuse must skip unchanged bytes"),
                        ):
                            self.assertEqual(
                                role_reports.collect_execution_reports(receipt)["copiedFiles"], 0
                            )
                        path.unlink()
                    self.assertEqual(role_reports.collect_execution_reports(receipt)["files"], 0)
                self.assertEqual(receipt["reportCollectionFiles"], {})
                self.assertEqual(len(list(canonical.iterdir())), turns)
                source.rmdir()
                role_reports.collect_execution_reports(receipt)
                self.assertNotIn("reportCollectionFiles", receipt)
                self.assertTrue((canonical / "0.md").is_file())
                stored = {
                    "schema": role_launch_receipts.RECEIPT_SCHEMA,
                    "requestId": f"churn-{turns}",
                    "reportCollectionFiles": {"obsolete.md": {}},
                    "reportCollection": {"state": "collected-closed"},
                }
                path = self.root / f"receipt-{turns}" / "current.json"
                role_launch_receipts._write_receipt(path, stored)
                role_launch_receipts._archive_receipt(path, stored)
                archived = json.loads((path.parent / "history" / f"churn-{turns}.json").read_text())
                self.assertNotIn("reportCollectionFiles", archived)
                self.assertEqual(archived["reportCollection"]["state"], "collected-closed")

    def test_r71_host_custom_provider_requires_no_ar_declaration(self) -> None:
        user = user_settings_path_for_write()
        user.parent.mkdir(parents=True, exist_ok=True)
        self.write_user_settings({"roles": {"worker": {"harness": "hermes"}}})
        settings = load_agentic_settings(self.config.coordination_root)
        self.assertEqual(settings.role_knobs("worker").harness, "hermes")
        self.assertNotIn("hermes", settings.harness_ids)
        catalog = copy.deepcopy(CATALOG)
        catalog["providers"].append({"id": "hermes", "label": "Hermes", "models": [], "modes": []})
        forget_launcher_catalogs()
        self.bridge(catalog)
        self.assertEqual(self.select({**defaults("hermes"), "role": "worker"})[0], "hermes")
        forget_launcher_catalogs()
        self.bridge(CATALOG)
        with self.assertRaisesRegex(LaunchSelectionRefused, "hermes"):
            self.select({**defaults("hermes"), "role": "worker"})
