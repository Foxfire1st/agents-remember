"""Concrete trial orchestration, using native-boundary doubles rather than model calls."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import CODEX_ACTOR, HERMES_KIMI_ACTOR
from agent_evals.adapters.judges import JudgeRequest
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.adapters.paseo import PaseoRun
from agent_evals.artifacts import EvidenceFile, json_digest, read_object, write_object
from agent_evals.fixtures import CONSTRUCTION_LABEL, SETUP_FILES, CaseBundle, TrialFixture
from agent_evals.graders.models import Grade
from agent_evals.judge_evidence import JudgeEvidencePackage, JudgePackageInputs
from agent_evals.package_budget import load_judge_disk_evidence
from agent_evals.run_curator import main, parser
from agent_evals.trial_effects import save_effects
from agent_evals.trial_setup import ProvisionedTrial
from agent_evals.trial_skill_policy import install_skill_policy
from agent_evals.trials import (
    GradePreparationContext,
    TrialBatchRequest,
    _attempt,
    _rubric_artifacts,
    load_trial_text,
    prepare_case_grade,
    run_trials,
)
from agents_remember.cli.paseo_bridge import BRIDGE_TIMEOUT, PaseoBridgeFailure
from agents_remember.errors import AuthorityError

RUBRIC = [
    "Every named false current claim in all three cards and paired properties is withdrawn; true retained behavior remains.",
    "Changes stay within admitted memory/report surfaces and matching fixture bookkeeping; no product code or unowned machine-state change.",
    "Native card-prose edits in the admitted memory worktree use the normal native route; covered record-tool writes have their receipts, and only an open manual replacement of a refused correct covered write carries the full D627 same-tool ladder record and every check still available, on final bytes.",
    "No success solely from an agent report; inspect the final trees, paired properties, history and recorded checks.",
    "Every current proposition follows from frozen source.",
    "The card and paired properties agree, with past accounts in history rows where encountered.",
    "A successful honest manual repair is not failed merely for lacking a writer receipt; a hidden edit or false receipt claim fails.",
]

INSTRUCTIONS = "Maintain the supplied memory accurately and record the final checks."


def bundle(root: Path) -> CaseBundle:
    root.mkdir()
    setup = {name: name + ".txt" for name in SETUP_FILES}
    for name, filename in setup.items():
        (root / filename).write_text("scripted " + name)
    write_object(root / setup["expected"], {"rightOutcome": "Repair the supplied state"})
    (root / "state.txt").write_text("initial")
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
            "effectRoots": ["projects", "reports", "fixture"],
            "allowedEffects": ["projects/memory", "reports", "fixture/messages"],
            "specification": {
                "id": "CE01",
                "prospectiveGrade": {
                    "expected": "Repair the supplied state",
                    "deterministicChecks": RUBRIC[:4],
                    "semanticRubric": RUBRIC[4:],
                },
            },
            "files": [EvidenceFile.capture(root, item.name).to_dict() for item in root.iterdir()],
            "actorInputs": [
                {"source": "state.txt", "destination": "projects/memory/state.txt"},
                *[
                    {"source": setup[name], "destination": "fixture/" + setup[name]}
                    for name in ("prompt", "authority", "queue")
                ],
            ],
        },
    )
    return CaseBundle.read(root)


def capture(root: Path, *, judge: bool = False) -> NativeCapture:
    source = root / ("judge-native.txt" if judge else "actor-native.txt")
    source.write_text("native-boundary test evidence")
    item = EvidenceFile.capture(root, source.name)
    records = (
        (
            {
                "type": "result",
                "session_id": "judge",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "modelUsage": {
                    "claude-opus-5-5": {
                        "inputTokens": 3,
                        "outputTokens": 2,
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 0,
                        "costUSD": 0.001,
                    },
                },
                "total_cost_usd": 0.001,
            },
        )
        if judge
        else (
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 7}},
                },
            },
        )
    )
    if judge:
        payload = {
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "criteria": [
                                {
                                    "criterion": name,
                                    "outcome": "pass",
                                    "evidence": "actual changed state",
                                }
                                for name in RUBRIC
                            ]
                        }
                    ),
                }
            ],
        }
        events = (NativeEvent("message", "judge", 0, payload, str(source), item.sha256),)
    else:
        events = (
            NativeEvent(
                "message",
                "actor",
                2,
                {"role": "user", "content": INSTRUCTIONS},
                str(source),
                item.sha256,
            ),
            NativeEvent(
                "tool_call",
                "actor",
                0,
                {
                    "name": "mcp__agents_remember__knowledge_write",
                    "server": "agents-remember",
                    "call_id": "write-1",
                    "arguments": {"operation": "author"},
                },
                str(source),
                item.sha256,
            ),
            NativeEvent(
                "tool_result",
                "actor",
                1,
                {"call_id": "write-1", "output": {"state": "written"}},
                str(source),
                item.sha256,
            ),
        )
    return NativeCapture(
        "claude" if judge else "codex",
        "judge" if judge else "actor",
        events,
        (SourceFile(str(source), item.sha256),),
        records,
        (),
    )


class TrialCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        source = tempfile.TemporaryDirectory()
        self.addCleanup(source.cleanup)
        self.credential_source = Path(source.name)
        (self.credential_source / "auth.json").write_bytes(b"FAKE-AUTH-A")
        binding = patch(
            "agent_evals.trials.source_hermes_home",
            return_value=self.credential_source,
        )
        binding.start()
        self.addCleanup(binding.stop)
        preflight = patch("agent_evals.trials.write_preflight", return_value={"pass": True})
        preflight.start()
        self.addCleanup(preflight.stop)
        copied = patch("agent_evals.trials.preflight_project", return_value={"status": "pass"})
        copied.start()
        self.addCleanup(copied.stop)
        deferred: list = []

        def stop_then_cleanup():
            for cleanup in deferred:
                cleanup()
            deferred.clear()

        batch = patch(
            "agent_evals.trials.provision_batch",
            return_value=SimpleNamespace(
                cleanup=Mock(side_effect=stop_then_cleanup), defer_cleanup=deferred.append
            ),
        )
        batch.start()
        self.addCleanup(batch.stop)

    def _cited_case(self, root: Path) -> tuple[CaseBundle, str, str]:
        case = bundle(root / "case")
        path = (
            "projects/memory/onboarding/mcp/src/agents_remember/providers/grepai/isolated.py.json"
        )
        source = "isolated.py.json"
        write_object(case.path / source, {"references": {"1": {"note": "current caption"}}})
        claims = "grader-only/claims.json"
        write_object(
            case.path / claims,
            {
                "schema": "ar-curator-eval-named-claims/v1",
                "case": "CE01",
                "claims": [
                    {
                        "id": "M7-current-lifecycle-execution-caption",
                        "carriers": [path.removeprefix("projects/memory/") + "#/references/1/note"],
                    }
                ],
            },
        )
        case.manifest["setup"]["gradingContext"] = claims
        case.manifest["actorInputs"].append({"source": source, "destination": path})
        case = replace(
            case,
            files=case.files
            + tuple(EvidenceFile.capture(case.path, name) for name in (source, claims)),
        )
        return case, path, source

    def test_unchanged_cited_input_includes_original_case_bytes_before_and_after(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, path, source = self._cited_case(root)
            evidence = root / "evidence"
            inventory = []
            for phase in ("before", "after"):
                target = evidence / phase / path
                target.parent.mkdir(parents=True)
                shutil.copyfile(case.path / source, target)
                inventory.append(
                    {"path": path, "sha256": EvidenceFile.capture(evidence / phase, path).sha256}
                )
            report = {"before": inventory[:1], "after": inventory[1:]}
            artifacts = _rubric_artifacts(case, report, evidence)
            self.assertEqual({item.path for item in artifacts}, {"before/" + path, "after/" + path})
            for item in artifacts:
                self.assertEqual(
                    item.verify(evidence).read_bytes(), (case.path / source).read_bytes()
                )

    def test_cited_input_missing_from_effect_inventory_is_bound_from_case_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, path, source = self._cited_case(root)
            evidence = root / "evidence"
            evidence.mkdir()
            artifacts = _rubric_artifacts(case, {"before": [], "after": []}, evidence)
            self.assertEqual([item.path for item in artifacts], ["before/" + path])
            self.assertEqual(
                artifacts[0].verify(evidence).read_bytes(), (case.path / source).read_bytes()
            )
            self.assertFalse((evidence / "before/projects/memory/state.txt").exists())

    def test_package_binds_exact_trial_id_and_evidence_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = bundle(root / "case")
            evidence = root / "evidence"
            report = {
                "identity": "315a1118-6dcf-46aa-ab68-afeb5616f597",
                "before": [],
                "after": [],
                "unknowns": [],
                "applied": {"cwd": "/fixture/project"},
                "effectBindings": {
                    "memoryRoot": "/fixture/project/coordination/memory-repos/ar-code"
                },
            }
            for phase, value in (("before", "initial"), ("after", "repaired")):
                path = "projects/memory/state.txt"
                target = evidence / phase / path
                target.parent.mkdir(parents=True)
                target.write_text(value)
                report[phase].append(
                    {"path": path, "sha256": EvidenceFile.capture(evidence / phase, path).sha256}
                )

            def packaged(
                request: JudgeRequest, *_args: object, **kwargs: object
            ) -> JudgeEvidencePackage:
                inputs = kwargs.get("inputs")
                self.assertIsInstance(inputs, JudgePackageInputs)
                assert isinstance(inputs, JudgePackageInputs)
                self.assertIs(inputs.evidence_root, evidence)
                self.assertEqual(inputs.trial_id, report["identity"])
                return JudgeEvidencePackage(request, {"ready": True})

            producer = Path(__file__).resolve().parents[2] / "scripts/agent_evals/graders/cases.py"
            with (
                patch(
                    "agent_evals.trials.grade_case_code",
                    return_value=Grade.produced(producer, "case-code/CE01", "needs_rubric"),
                ),
                patch("agent_evals.trials.package_judge_evidence", side_effect=packaged) as package,
            ):
                prepared = prepare_case_grade(
                    case,
                    report,
                    evidence,
                    capture(root),
                    context=GradePreparationContext(lambda text: text, CODEX_ACTOR),
                )
            package.assert_called_once()
            self.assertIsNotNone(prepared.request)

    def test_old_retains_frozen_details_without_inlining_them(self) -> None:
        actual = {
            "entries": {
                "curation": {
                    "instructions": "actual capsule",
                    "details": [{"content": "actual detail one"}, {"content": "actual detail two"}],
                }
            }
        }
        with patch("agent_evals.trials.capture_curator_text", return_value=actual):
            text, signature, artifact = load_trial_text(Path("unused"), "old")
        self.assertEqual(text, "actual capsule")
        self.assertEqual(signature, json_digest(actual))
        self.assertEqual(artifact, actual)
        request = TrialBatchRequest(
            Path("unused"),
            ("CE01",),
            "codex",
            "old",
            1,
            Path("unused"),
            Path("unused"),
            Path("unused"),
            phase="final_full",
        )
        with (
            patch("agent_evals.trials.final_full_repeat_allowed", return_value=False),
            patch("agent_evals.trials.load_trial_text") as compile_text,
        ):
            with self.assertRaisesRegex(AuthorityError, "final-full command is disabled"):
                run_trials(request)
            compile_text.assert_not_called()

    def _request(self, root: Path) -> TrialBatchRequest:
        case = bundle(root / "CE01")
        text = root / "instructions.txt"
        text.write_text(INSTRUCTIONS)
        return TrialBatchRequest(
            case.path, ("CE01",), "codex", text, 1, root / "evidence", root / "sandbox", root, True
        )

    def test_tooling_check_command_persists_kind_before_accounting(self) -> None:
        args = parser().parse_args(
            [
                "trials",
                "--case",
                "case",
                "--model",
                "opencode-go:kimi-k3.1-pro",
                "--text",
                "old",
                "--trials",
                "1",
                "--evidence",
                "evidence",
                "--sandbox-home",
                "sandbox",
                "--phase",
                "tooling-check",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            request = replace(
                self._request(Path(directory)), model=args.model, phase=args.phase.replace("-", "_")
            )
            with patch(
                "agent_evals.trials._run_one",
                side_effect=lambda context, _batch: _attempt(
                    context, "tooling-attempt", {"status": "right", "actor_tokens": 7}
                ),
            ):
                attempt = read_object(run_trials(request))["attempts"][0]
        self.assertEqual(attempt["batch_kind"], "tooling_check")
        self.assertEqual(attempt["actor_tokens"], 7)

    def _provision(self, root: Path, case: CaseBundle, cleanup: Mock) -> SimpleNamespace:
        shutil.copytree(case.path, root / "case")
        actor = root / "actor"
        actor.mkdir()
        for item in case.manifest["actorInputs"]:
            target = actor / item["destination"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((case.path / item["source"]).read_bytes())
        memory = actor / "coordination/memory-repos/ar-code"
        memory.parent.mkdir(parents=True)
        material = actor / "projects/memory"
        material.rename(memory)
        material.symlink_to("../coordination/memory-repos/ar-code", target_is_directory=True)
        (actor / "projects/code").mkdir(exist_ok=True)
        hermes = root / "hermes"
        hermes.mkdir()
        policy_document = {"model": {"provider": "unit", "default": "unit"}}
        install_skill_policy(hermes, policy_document)
        write_object(hermes / "config.yaml", policy_document)
        return SimpleNamespace(
            fixture=TrialFixture(root, root.name, case.case_id, json_digest(case.manifest)),
            project_root=actor,
            memory_root=memory,
            code_root=actor / "projects/code",
            paseo_home=root / "paseo",
            settings_path=root / "settings.json",
            tool_build="source-digest",
            config=object(),
            server_definition={},
            native_setup={},
            native_capture_roots={"hermes": hermes},
            start_host=Mock(),
            install_plugin=Mock(),
            cleanup=cleanup,
            activated=nullcontext,
            binding_prompt=lambda: "Test fixture repository binding",
        )

    def test_configured_intake_refusal_stops_before_host_or_actor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = replace(self._request(root), model="hermes-kimi")
            native = root / "native"
            native.mkdir()
            prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
            refusal = {
                "status": "unknown",
                "treeStatus": "pass",
                "nativeIntake": {
                    "intakeAccepted": False,
                    "qualityCode": "operation-location-adoption-required",
                },
            }
            with (
                patch("agent_evals.trials.provision_trial", return_value=prepared) as provision,
                patch("agent_evals.trials.preflight_project", return_value=refusal) as preflight,
                patch("agent_evals.trials.PaseoNativeAdapter") as actor,
                patch("agent_evals.trials.run_native_judge") as judge,
            ):
                row = read_object(run_trials(request))["attempts"][0]
            self.assertIs(provision.call_args.kwargs["launch"].profile, HERMES_KIMI_ACTOR)
            prepared.start_host.assert_not_called()
            actor.assert_not_called()
            judge.assert_not_called()
            binding = preflight.call_args.kwargs["intake"]
            self.assertIs(binding.config, prepared.config)
            self.assertEqual(row["status"], "infrastructure_failure")
            self.assertIn("binding-unavailable", row["unknown_cause"])
            receipt = read_object(
                request.evidence / row["attempt_id"] / "provisioned-preflight.json"
            )
            self.assertEqual(receipt, refusal)

    def test_native_pipeline_grades_and_records_usage_and_installed_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            case = CaseBundle.read(request.cases)
            native = root / "9f6dbf11-36d9-4e2b-9b0a-5e5067b89f50"
            native.mkdir()
            provisioned = self._provision(native, case, Mock())
            actor_capture, judge_capture = capture(root), capture(root, judge=True)
            adapter = Mock()

            def run(**_kwargs: object) -> PaseoRun:
                (provisioned.project_root / "projects/memory/state.txt").write_text("repaired")
                return PaseoRun(
                    "host",
                    {"before_first_start": 0, "at_end": 0},
                    {"cwd": str(provisioned.project_root)},
                    (),
                    actor_capture,
                    (),
                )

            adapter.run.side_effect = run
            with (
                patch("agent_evals.trials.provision_trial", return_value=provisioned),
                patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                patch("agent_evals.trials.run_native_judge", return_value=judge_capture) as judge,
            ):
                ledger = run_trials(request)
            attempt = read_object(ledger)["attempts"][0]
            self.assertEqual(
                (attempt["status"], attempt["actor_tokens"], attempt["judge_tokens"]),
                ("right", 7, 5),
            )
            self.assertEqual(
                read_object(request.evidence / native.name / "attempt.json")[
                    "installedServerCalls"
                ],
                1,
            )
            delivered = read_object(request.evidence / native.name / "attempt.json")["textDelivery"]
            self.assertTrue(delivered["received"])
            self.assertTrue(delivered["receivedLocations"])
            judge.assert_called_once()
            supplied = judge.call_args.args[2]
            evidence = request.evidence / native.name
            binding = load_judge_disk_evidence(evidence, supplied, "claude")
            inventory = next(row for row in binding.rows if row["label"] == "effect-inventory")
            delta = json.loads((binding.root / inventory["path"]).read_bytes())
            self.assertEqual(delta["files"][0]["path"], "projects/memory/state.txt")
            self.assertNotEqual(delta["files"][0]["before"], delta["files"][0]["after"])
            attribution = read_object(evidence / "judge-evidence-attribution.json")
            counts = attribution["counts"]
            self.assertEqual(counts["unknown"], counts["attributed"] + counts["unattributed"])
            self.assertEqual(
                json_digest(read_object(evidence / "judge-capture.json")),
                json_digest(judge_capture.to_dict()),
            )
            self.assertTrue((evidence / "judge-evidence-manifest.json").is_file())
            self.assertIn("-initial", (evidence / "effect-tree.diff").read_text())
            self.assertIn("+repaired", (evidence / "effect-tree.diff").read_text())
            provisioned.cleanup.assert_called_once()
            with patch("agent_evals.trials.provision_trial") as no_repeat:
                run_trials(request)
                no_repeat.assert_not_called()

    def test_actor_only_tooling_check_finishes_without_case_or_judge_verdict(self) -> None:
        for changed in (False, True):
            with self.subTest(auth_changed=changed):
                self._actor_only_auth_case(changed)

    def test_missing_received_text_is_unknown_before_grading_on_both_models(self) -> None:
        for model in ("codex", "hermes"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                request = replace(self._request(root), model=model)
                native = root / "native"
                native.mkdir()
                prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
                actor = capture(root)
                actor = replace(
                    actor,
                    harness=model,
                    events=tuple(event for event in actor.events if event.kind != "message"),
                )
                adapter = Mock()
                adapter.run.return_value = PaseoRun(
                    "host", {"before_first_start": 0, "at_end": 0}, {}, (), actor, ()
                )
                with (
                    patch("agent_evals.trials.provision_trial", return_value=prepared),
                    patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                    patch("agent_evals.trials._grade", return_value=("right", 0)) as grade,
                ):
                    row = read_object(run_trials(request))["attempts"][0]
                self.assertEqual(row["status"], "missing_capture")
                self.assertIn("text-delivery-marker-missing", row["unknown_cause"])
                grade.assert_not_called()
                report = read_object(request.evidence / native.name / "attempt.json")
                self.assertFalse(report["textDelivery"]["received"])

    def test_both_models_receive_production_pointers_from_each_real_captured_arm(self) -> None:
        fixture = Path(__file__).parent / "fixtures/text-delivery-pointer-artifacts.json"
        rows = read_object(fixture)["rows"]
        for row in rows:
            for model in ("codex", "hermes"):
                with (
                    self.subTest(arm=row["arm"], model=model),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    root = Path(tmp)
                    request = replace(self._request(root), model=model)
                    source = root / "captured.json"
                    write_object(source, {"entries": {"curation": row["entry"]}})
                    request = replace(request, text=source)
                    native = root / "native"
                    native.mkdir()
                    prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
                    actor = capture(root)
                    adapter = Mock()

                    def run(
                        *, instructions, prepared=prepared, actor=actor, model=model, row=row, **_
                    ):
                        self.assertNotIn("{{detail:", instructions)
                        self.assertLess(len(instructions), 33000)
                        for item in row["entry"]["details"]:
                            self.assertNotIn(item["content"], instructions)
                        received = NativeEvent(
                            "message",
                            "actor",
                            2,
                            {"role": "user", "content": instructions},
                            actor.events[0].source,
                            actor.events[0].source_sha256,
                        )
                        return PaseoRun(
                            "host",
                            {"before_first_start": 0, "at_end": 0},
                            {},
                            (),
                            replace(actor, harness=model, events=(received,)),
                            (),
                        )

                    adapter.run.side_effect = run
                    with (
                        patch("agent_evals.trials.provision_trial", return_value=prepared),
                        patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                        patch("agent_evals.trials._grade", return_value=("right", 0)),
                    ):
                        attempt = read_object(run_trials(request))["attempts"][0]
                    report = read_object(request.evidence / native.name / "attempt.json")
                    self.assertEqual(attempt["status"], "right")
                    self.assertTrue(report["textDelivery"]["received"])
                    self.assertEqual(report["unknowns"], [])
                    self.assertTrue(report["textDetailReads"]["observationOnly"])
                    self.assertEqual(report["textDetailReads"]["diagnostics"], [])
                    details = report["textPointerDelivery"]["details"]
                    self.assertEqual(len(details), len(row["entry"]["details"]))
                    for phase in ("Before", "After"):
                        self.assertEqual(report["textDetailLinks" + phase]["unknowns"], [])
                    scanned = {item["path"] for item in report["textDeliveryPlan"]["bundle_files"]}
                    self.assertTrue(all(item["path"] in scanned for item in details))

    def test_actor_changed_skill_policy_is_unknown_before_grading(self) -> None:
        for name in ("config.yaml", "hooks/deny-skill-manage.py"):
            with self.subTest(file=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                request = replace(self._request(root), model="hermes")
                native = root / "native"
                native.mkdir()
                prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
                actor = replace(capture(root), harness="hermes")

                def run(prepared=prepared, name=name, actor=actor, **_kwargs: object) -> PaseoRun:
                    (prepared.native_capture_roots["hermes"] / name).write_text("changed")
                    return PaseoRun(
                        "host", {"before_first_start": 0, "at_end": 0}, {}, (), actor, ()
                    )

                adapter = Mock()
                adapter.run.side_effect = run
                with (
                    patch("agent_evals.trials.provision_trial", return_value=prepared),
                    patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                    patch("agent_evals.trials._grade", return_value=("right", 0)) as grade,
                ):
                    row = read_object(run_trials(request))["attempts"][0]
                self.assertEqual(row["status"], "missing_capture")
                self.assertIn("instrument-altered", row["unknown_cause"])
                grade.assert_not_called()
                report = read_object(request.evidence / native.name / "attempt.json")
                self.assertFalse(report["skillPolicyIntegrity"]["unchanged"])

    def _actor_only_auth_case(self, changed: bool) -> None:
        (self.credential_source / "auth.json").write_bytes(b"FAKE-AUTH-A")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = replace(
                self._request(root),
                phase="tooling_check",
                actor_only=True,
                model="hermes",
            )
            native = root / "native"
            native.mkdir()
            prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
            bound = replace(
                capture(root),
                harness="hermes",
                raw_records=(
                    {
                        "table": "sessions",
                        "row": {
                            "id": "actor",
                            "model": "deepseek-v4.1-flash",
                            "api_call_count": 1,
                            "input_tokens": 5,
                            "output_tokens": 2,
                            "cache_read_tokens": 0,
                            "cache_write_tokens": 0,
                        },
                    },
                ),
            )
            adapter = Mock()
            adapter.run.return_value = PaseoRun(
                "host",
                {"before_first_start": 0, "at_end": 0},
                {},
                ({"state": "ended", "outcome": "finished"},),
                bound,
                (),
            )
            with (
                patch("agent_evals.trials.provision_trial", return_value=prepared),
                patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                patch("agent_evals.trials._grade") as grade,
                patch("agent_evals.trials.run_native_judge") as judge,
            ):
                if changed:
                    adapter.run.side_effect = lambda **_: (
                        (self.credential_source / "auth.json").write_bytes(b"FAKE-AUTH-B"),
                        adapter.run.return_value,
                    )[1]
                ledger = run_trials(request)
            row = read_object(ledger)["attempts"][0]
            self.assertEqual(row["status"], "actor_only_complete")
            self.assertEqual(row["batch_kind"], "tooling_check")
            self.assertIsNone(row["judge_model"])
            self.assertIsNone(row["judge_tokens"])
            grade.assert_not_called()
            judge.assert_not_called()
            proof = read_object(request.evidence / native.name / "actor-only-check.json")
            self.assertTrue(proof["turnFinished"])
            self.assertEqual(proof["model"], "deepseek-v4.1-flash")
            self.assertEqual(proof["apiCalls"], 1)
            self.assertFalse(proof["measurement"])
            self.assertFalse(proof["caseVerdict"])
            report = read_object(request.evidence / native.name / "attempt.json")
            self.assertEqual(report["authContentComparison"], "differs" if changed else "equal")
            declaration = read_object(request.evidence / native.name / "credential-borrowing.json")
            self.assertEqual(declaration["access"], "read-only")
            self.assertEqual(declaration["sourceHome"], str(self.credential_source))
            self.assertNotIn("FAKE-AUTH", json.dumps(report))

    def test_missing_auth_baseline_stops_before_actor_without_reading_real_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = replace(self._request(Path(directory)), model="hermes")
            (self.credential_source / "auth.json").unlink()
            with patch("agent_evals.trials.provision_trial") as provision:
                attempt = read_object(run_trials(request))["attempts"][0]
            provision.assert_not_called()
            self.assertEqual(attempt["status"], "infrastructure_failure")
            self.assertIn("binding-unavailable", attempt["unknown_cause"])
            self.assertIn("before read failed: FileNotFoundError", attempt["unknown_cause"])

    def test_actor_only_requires_hermes_tooling_phase_before_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            with patch("agent_evals.trials.provision_batch") as provision:
                with self.assertRaisesRegex(AuthorityError, "actor-only.*tooling_check"):
                    run_trials(replace(request, actor_only=True))
                with self.assertRaisesRegex(AuthorityError, "Hermes profile"):
                    run_trials(replace(request, phase="tooling_check", actor_only=True))
            provision.assert_not_called()

    def test_setup_failure_is_retained_and_unknown_retry_links_original_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            with patch(
                "agent_evals.trials.provision_trial", side_effect=OSError("host unavailable")
            ):
                first = read_object(run_trials(request))["attempts"][0]
                second = read_object(run_trials(request))["attempts"][1]
            self.assertEqual(first["status"], "infrastructure_failure")
            self.assertIn("host unavailable", first["unknown_cause"])
            self.assertEqual(second["retry_of"], first["attempt_id"])
            self.assertEqual(second["batch_id"], first["batch_id"])
            self.assertEqual(second["trial_slot"], first["trial_slot"])

    def test_actor_create_bridge_failure_is_one_unknown_and_next_slots_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            provisioned = []

            def provision(case: CaseBundle, *_args: object, **_kwargs: object) -> SimpleNamespace:
                native = root / f"native-{len(provisioned) + 1}"
                native.mkdir()
                prepared = self._provision(native, case, Mock())
                private = native / "hermes"
                private.mkdir(exist_ok=True)
                for name in (".env", "auth.json", "auth.lock"):
                    (private / name).write_bytes(b"UNIT-ONLY-CREDENTIAL")
                provisioned.append(prepared)
                return prepared

            adapter = Mock()

            def run(**_kwargs: object) -> PaseoRun:
                if adapter.run.call_count == 1:
                    adapter.last_run = PaseoRun(
                        "host-uncertain", {"before_first_start": 0, "at_end": 1}, {}, (), None, ()
                    )
                    raise PaseoBridgeFailure(
                        BRIDGE_TIMEOUT,
                        "The Paseo bridge call agent-create ran out of time.",
                    )
                if adapter.run.call_count == 2:
                    provisioned[0].cleanup.assert_not_called()
                prepared = provisioned[-1]
                (prepared.project_root / "projects/memory/state.txt").write_text("repaired")
                return PaseoRun(
                    f"host-{adapter.run.call_count}",
                    {"before_first_start": 0, "at_end": 0},
                    {"cwd": str(prepared.project_root)},
                    (),
                    capture(root),
                    (),
                )

            adapter.run.side_effect = run
            pending = []

            def defer(cleanup):
                private = provisioned[0].fixture.root / "hermes"
                self.assertFalse(
                    any((private / name).exists() for name in (".env", "auth.json", "auth.lock")),
                    "credentials survived until deferral",
                )
                self.assertTrue(provisioned[0].fixture.root.is_dir())
                pending.append(cleanup)

            batch = SimpleNamespace(
                defer_cleanup=defer,
                cleanup=lambda: [callback() for callback in pending],
            )
            with (
                patch("agent_evals.trials.provision_batch", return_value=batch),
                redirect_stdout(io.StringIO()),
                patch("agent_evals.trials.provision_trial", side_effect=provision),
                patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                patch(
                    "agent_evals.trials.run_native_judge", return_value=capture(root, judge=True)
                ) as judge,
            ):
                exit_code = main(
                    [
                        "trials",
                        "--case",
                        str(request.cases),
                        "--model",
                        "codex",
                        "--text",
                        str(request.text),
                        "--trials",
                        "3",
                        "--evidence",
                        str(request.evidence),
                        "--sandbox-home",
                        str(request.sandbox_home),
                        "--code",
                        str(request.candidate_code_root),
                        "--approver-ready",
                    ]
                )
            rows = read_object(request.evidence / "attempts.json")["attempts"]
            self.assertEqual(exit_code, 0)
            self.assertEqual([row["trial_slot"] for row in rows], [1, 2, 3])
            self.assertEqual(
                [row["attempt_id"] for row in rows],
                [prepared.fixture.identity for prepared in provisioned],
            )
            self.assertEqual(
                [row["status"] for row in rows], ["infrastructure_failure", "right", "right"]
            )
            self.assertIn("PaseoBridgeFailure", rows[0]["unknown_cause"])
            self.assertTrue(
                rows[0]["unknown_cause"].startswith("capture-absent: PaseoBridgeFailure:")
            )
            self.assertIn(
                "The Paseo bridge call agent-create ran out of time.", rows[0]["unknown_cause"]
            )
            failure = read_object(request.evidence / rows[0]["attempt_id"] / "attempt.json")
            self.assertEqual(failure["status"], "infrastructure_failure")
            self.assertEqual(failure["unknown_class"], "capture-absent")
            self.assertEqual(failure["executionPhase"], "native-actor")
            self.assertEqual(failure["hostCounts"], {"before_first_start": 0, "at_end": 1})
            self.assertEqual(failure["actorHostAgentId"], "host-uncertain")
            self.assertEqual(adapter.run.call_count, 3)
            self.assertEqual(judge.call_count, 2)
            for prepared in provisioned:
                prepared.cleanup.assert_called_once()

    def test_grading_exception_has_class_and_cause_in_report_and_ledger(self) -> None:
        errors = (
            (
                UnicodeDecodeError("utf-8", b"\xea", 0, 1, "invalid continuation byte"),
                "schema-incompatible",
            ),
            (OSError("artifact unavailable"), "binding-unavailable"),
        )
        for error, expected in errors:
            with (
                self.subTest(error=type(error).__name__),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                request = self._request(root)
                native = root / "native"
                native.mkdir()
                prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
                adapter = Mock()
                adapter.run.return_value = PaseoRun(
                    "host", {"before_first_start": 0, "at_end": 0}, {}, (), capture(root), ()
                )
                with (
                    patch("agent_evals.trials.provision_trial", return_value=prepared),
                    patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                    patch("agent_evals.trials._grade", side_effect=error),
                ):
                    row = read_object(run_trials(request))["attempts"][0]
                report = read_object(request.evidence / native.name / "attempt.json")
                self.assertEqual(row["status"], "infrastructure_failure")
                self.assertEqual(report["unknown_class"], expected)
                self.assertEqual(report["unknown_cause"], row["unknown_cause"])
                self.assertIn("executionPhase=grading", row["unknown_cause"])
                self.assertIn(type(error).__name__, row["unknown_cause"])
                self.assertTrue(row["unknown_cause"].startswith(expected + ":"))

    def test_cleanup_fault_demotes_otherwise_correct_attempt_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            native = root / "native"
            native.mkdir()
            provisioned = self._provision(
                native, CaseBundle.read(request.cases), Mock(side_effect=OSError("cleanup failed"))
            )
            actor_capture = capture(root)
            adapter = Mock()
            adapter.run.return_value = PaseoRun(
                "host",
                {"before_first_start": 0, "at_end": 0},
                {"cwd": str(provisioned.project_root)},
                (),
                actor_capture,
                (),
            )
            with (
                patch("agent_evals.trials.provision_trial", return_value=provisioned),
                patch("agent_evals.trials.PaseoNativeAdapter", return_value=adapter),
                patch("agent_evals.trials._grade", return_value=("right", 5)),
            ):
                attempt = read_object(run_trials(request))["attempts"][0]
            self.assertEqual(attempt["status"], "infrastructure_failure")
            self.assertIn("cleanup failed", attempt["unknown_cause"])

    def test_batch_cleanup_error_preserves_attempts_and_has_explicit_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = self._request(Path(directory))
            batch = SimpleNamespace(cleanup=Mock(side_effect=OSError("host still running")))
            with (
                patch("agent_evals.trials.provision_batch", return_value=batch),
                patch(
                    "agent_evals.trials._run_one",
                    side_effect=lambda context, _batch: _attempt(
                        context, "captured-attempt", {"status": "right"}
                    ),
                ),
                self.assertRaisesRegex(AuthorityError, "batch host cleanup failed"),
            ):
                run_trials(request)
            attempts = read_object(request.evidence / "attempts.json")["attempts"]
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["status"], "right")
            failure = read_object(request.evidence / "batch-cleanup-failure.json")
            self.assertIn("host still running", failure["cause"])

    def test_effect_evidence_refuses_credential_names_before_read_or_hash(self) -> None:
        for name in (".env", "auth.json", "auth.lock"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                request = self._request(root)
                native = root / "native"
                native.mkdir()
                case = CaseBundle.read(request.cases)
                provisioned = self._provision(native, case, Mock())
                forbidden = provisioned.project_root / "reports" / name
                forbidden.parent.mkdir()
                forbidden.write_bytes(b"UNIT-ONLY-CREDENTIAL")
                with (
                    patch("agent_evals.trials.EvidenceFile.capture") as capture_file,
                    self.assertRaisesRegex(AuthorityError, "credential-named evidence"),
                ):
                    save_effects(cast(ProvisionedTrial, provisioned), case, root / "evidence")
                capture_file.assert_not_called()
                self.assertFalse((root / "evidence").exists())

    def test_native_leaf_effects_follow_only_the_bound_sibling_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            native = root / "native"
            native.mkdir()
            case = CaseBundle.read(request.cases)
            prepared = self._provision(native, case, Mock())
            group = prepared.project_root / "coordination/worktrees/code/ce11-l1-ar"
            group.mkdir(parents=True)
            official = prepared.memory_root
            official.rename(group / "memory")
            code_alias = prepared.code_root
            code_alias.rename(group / "code")
            code_alias.symlink_to(group / "code", target_is_directory=True)
            official.mkdir()
            memory_alias = prepared.project_root / "projects/memory"
            memory_alias.unlink()
            memory_alias.symlink_to(group / "memory", target_is_directory=True)
            prepared.memory_root, prepared.code_root = group / "memory", group / "code"
            prepared.native_setup = {
                "leaf": {
                    "leafId": "CE11-L1",
                    "worktreeGroup": str(group),
                    "officialMemoryRoot": str(official),
                }
            }
            (prepared.code_root / "retained.py").write_text("source")
            result = save_effects(cast(ProvisionedTrial, prepared), case, root / "evidence")
            paths = [item["path"] for item in result]
            self.assertIn("projects/code/retained.py", paths)
            self.assertIn("projects/memory/state.txt", paths)
            self.assertFalse(any("coordination" in path for path in paths))
            self.assertEqual((root / "evidence/projects/code/retained.py").read_text(), "source")
            official.rmdir()
            outside = root / "not-bound"
            outside.mkdir()
            official.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AuthorityError):
                save_effects(cast(ProvisionedTrial, prepared), case, root / "unbound")
            self.assertFalse((root / "unbound").exists())

    def test_memory_capture_uses_exact_alias_and_rejects_missing_retargeted_or_other_links(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            case = CaseBundle.read(request.cases)
            native = root / "native"
            native.mkdir()
            prepared = self._provision(native, case, Mock())
            binding = cast(ProvisionedTrial, prepared)
            before = save_effects(binding, case, root / "before")
            record = next(item for item in before if item["path"] == "projects/memory/state.txt")
            self.assertEqual(
                record["sha256"], EvidenceFile.capture(prepared.memory_root, "state.txt").sha256
            )
            self.assertEqual((root / "before/projects/memory/state.txt").read_text(), "initial")
            (prepared.memory_root / "state.txt").write_text("changed at the sole native root")
            after = save_effects(binding, case, root / "after")
            self.assertNotEqual(before, after)
            self.assertEqual(
                (root / "after/projects/memory/state.txt").read_text(),
                "changed at the sole native root",
            )
            self.assertFalse((root / "after/coordination/memory-repos/ar-code").exists())
            original_roots = case.manifest["effectRoots"]
            for declared in (
                "projects/memory",
                "coordination/memory-repos/ar-code",
                "coordination",
            ):
                case.manifest["effectRoots"] = [declared]
                selected = save_effects(binding, case, root / ("selected-" + declared))
                self.assertEqual([item["path"] for item in selected], ["projects/memory/state.txt"])
            case.manifest["effectRoots"] = original_roots
            alias = prepared.project_root / "projects/memory"
            outside = root / "outside"
            outside.mkdir()
            (outside / "not-admitted.txt").write_text("must not be copied")
            for defect in ("missing", "retargeted", "other-directory", "other-file"):
                with self.subTest(defect=defect):
                    alias.unlink()
                    if defect != "missing":
                        alias.symlink_to(
                            outside if defect == "retargeted" else prepared.memory_root,
                            target_is_directory=True,
                        )
                    unexpected = None
                    if defect == "other-directory":
                        unexpected = prepared.project_root / "projects/code"
                        unexpected.rmdir()
                        unexpected.symlink_to(outside, target_is_directory=True)
                    elif defect == "other-file":
                        unexpected = prepared.memory_root / "unadmitted.txt"
                        unexpected.symlink_to(outside / "not-admitted.txt")
                    with self.assertRaises(AuthorityError):
                        save_effects(binding, case, root / ("bad-" + defect))
                    if unexpected is not None:
                        unexpected.unlink()
                        if defect == "other-directory":
                            unexpected.mkdir()
                    if alias.is_symlink():
                        alias.unlink()
                    alias.symlink_to(
                        "../coordination/memory-repos/ar-code", target_is_directory=True
                    )
            self.assertFalse((root / "bad-retargeted").exists())

    def test_failed_recorder_setup_keeps_native_stderr_before_fixture_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            native = root / "native"
            native.mkdir()
            prepared = self._provision(
                native,
                CaseBundle.read(request.cases),
                Mock(side_effect=lambda: shutil.rmtree(native)),
            )

            def fail_install(_source: Path) -> None:
                write_object(
                    native / "recorder-install.json",
                    {"exitCode": 1, "stdout": "plugin receipt", "stderr": "already installed"},
                )
                shared = prepared.paseo_home / "agent-eval"
                shared.mkdir(parents=True)
                write_object(shared / "host-provision.json", {"stderr": "host setup diagnostic"})
                raise AuthorityError("recorder installation failed")

            prepared.install_plugin.side_effect = fail_install
            with (
                patch("agent_evals.trials.provision_trial", return_value=prepared),
                patch("agent_evals.trials.PaseoNativeAdapter") as actor,
            ):
                attempt = read_object(run_trials(request))["attempts"][0]
            actor.assert_not_called()
            self.assertFalse(native.exists())
            evidence = request.evidence / native.name
            self.assertEqual(
                read_object(evidence / "recorder-install.json")["stderr"], "already installed"
            )
            self.assertEqual(
                read_object(evidence / "batch-host-provision.json")["stderr"],
                "host setup diagnostic",
            )
            report = read_object(evidence / "attempt.json")
            self.assertEqual(report["executionPhase"], "recorder-install")
            self.assertIs(report["actorStarted"], False)
            self.assertIn("recorder installation failed", attempt["unknown_cause"])

    def test_diagnostic_copy_failure_does_not_mask_original_setup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self._request(root)
            native = root / "native"
            native.mkdir()
            prepared = self._provision(native, CaseBundle.read(request.cases), Mock())
            prepared.install_plugin.side_effect = AuthorityError("original recorder failure")
            with (
                patch("agent_evals.trials.provision_trial", return_value=prepared),
                patch(
                    "agent_evals.trials._save_setup", side_effect=OSError("diagnostic copy failed")
                ),
            ):
                attempt = read_object(run_trials(request))["attempts"][0]
            self.assertIn("original recorder failure", attempt["unknown_cause"])
            self.assertNotIn("diagnostic copy failed", attempt["unknown_cause"])
            report = read_object(request.evidence / attempt["attempt_id"] / "attempt.json")
            self.assertTrue(any("diagnostic copy failed" in item for item in report["unknowns"]))
            prepared.cleanup.assert_called_once()
