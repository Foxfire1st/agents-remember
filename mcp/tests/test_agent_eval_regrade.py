"""Regrading freezes source rows/packages and never repeats the actor."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.accounting import Attempt
from agent_evals.adapters.capture import capture_codex_jsonl
from agent_evals.adapters.execution import HERMES_ACTOR, HERMES_KIMI_ACTOR
from agent_evals.adapters.hermes import MESSAGE_COLUMNS, SESSION_COLUMNS, capture_hermes_db
from agent_evals.adapters.hermes_dispatch import normalize_hermes_dispatches
from agent_evals.adapters.judges import JudgeRequest
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile, raw_export_digest
from agent_evals.artifacts import EvidenceFile, json_digest, read_object, sha256_file, write_object
from agent_evals.graders.cases import RubricSubmission
from agent_evals.graders.models import Grade
from agent_evals.judge_evidence import JudgePackageTooLarge
from agent_evals.judge_outcomes import grading_exception_cause, resolve_judge_outcome
from agent_evals.package_budget import load_judge_disk_evidence
from agent_evals.regrade import (
    RegradeRequest,
    _candidate_attempts,
    _census_row,
    _JudgeSources,
    _mode_projection,
    _previous_regrade,
    _prior_summary,
    _record_supersessions,
    _retained_capture,
    run_regrades,
)
from agent_evals.result_identity import deterministic_identity, judged_identity
from agent_evals.run_curator import parser
from agents_remember.errors import AuthorityError
from test_agent_eval_trials import bundle, capture


def _grade(*args, **kwargs) -> Grade:
    producer = Path(__file__).resolve().parents[2] / "scripts/agent_evals/graders/cases.py"
    return Grade.produced(producer, *args, **kwargs)


def deterministic_key() -> dict:
    source = {"files": [{"path": "grader.py", "sha256": "a" * 64}]}
    return deterministic_identity(
        grader_id="case-code/CE01",
        source={**source, "sha256": json_digest(source)},
        capture={"observations": []},
        effects={"before": [], "after": []},
        admission={"case": "CE01"},
    )


def preparation_source(digest: str = "a" * 64) -> dict:
    return {
        "schema": "ar-regrade-preparation-source/v1",
        "sha256": digest,
        "snapshot": {"path": "grader-source.json", "sha256": "b" * 64},
    }


def source_attempt(
    root: Path, identity: str = "original", slot: int = 1, model: str = "gpt-6.1-sol"
) -> tuple[Path, Path]:
    batch = root / "batch"
    source = batch / "CE01" / identity
    source.mkdir(parents=True)
    case = bundle(source / "case")
    effects = {}
    for phase, text in (("before", "initial"), ("after", "repaired")):
        file = source / phase / "projects/memory/state.txt"
        file.parent.mkdir(parents=True)
        file.write_text(text)
        effects[phase] = [
            EvidenceFile.capture(source / phase, "projects/memory/state.txt").to_dict()
        ]
    native = source / "native.jsonl"
    native.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "type": "session_meta",
                    "payload": {"id": "native-original", "cwd": "/old/project"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "The captured repair is complete."}
                        ],
                    },
                },
            )
        )
        + "\n"
    )
    original = Attempt(
        identity,
        "CE01",
        model,
        "text",
        "batch",
        slot,
        "infrastructure_failure",
        unknown_cause="EvidencePackageTooLarge: original bound",
    )
    ledger = batch / "CE01/attempts.json"
    previous = read_object(ledger)["attempts"] if ledger.exists() else []
    write_object(ledger, {"attempts": [*previous, original.to_dict()]})
    native_capture = capture_codex_jsonl((native,), "native-original")
    if model in (HERMES_ACTOR.model, HERMES_KIMI_ACTOR.model, "openai-codex:gpt-6.1-sol-900k"):
        native_capture = hermes_fixture_capture(source / "native-state.db", model.partition(":")[2])
    write_object(
        source / "native-binding.json",
        {"toolBuild": "source-build", "projectRoot": "/old/fixture/project"},
    )
    write_object(
        source / "judge-evidence-manifest.json",
        {"packagedRequestPromptSha256": "a" * 64, "sizes": {"packagedPromptUtf8Bytes": 2000000}},
    )
    write_object(
        source / "attempt.json",
        {
            "identity": identity,
            "case": "CE01",
            "caseDigest": json_digest(case.manifest),
            "applied": {"cwd": "/old/fixture/project"},
            "effectBindings": {
                "memoryRoot": "/old/fixture/project/coordination/memory-repos/ar-code"
            },
            "capture": native_capture.to_dict(),
            "unknowns": [original.unknown_cause],
            "status": "infrastructure_failure",
            **effects,
        },
    )
    return batch, source


def hermes_fixture_capture(path: Path, model: str = "deepseek-v4.1-flash") -> NativeCapture:
    """A synthetic complete native schema, parsed through the production reader."""
    session: dict[str, Any] = dict.fromkeys(SESSION_COLUMNS)
    session.update(
        id="native-original",
        model=model,
        started_at=1,
        message_count=1,
        cwd="/old/project",
    )
    message: dict[str, Any] = dict.fromkeys(MESSAGE_COLUMNS)
    message.update(
        id=1,
        session_id="native-original",
        role="assistant",
        content="The captured repair is complete.",
        timestamp=2,
        active=1,
        finish_reason="stop",
    )
    with closing(sqlite3.connect(path)) as database:
        database.execute(f"CREATE TABLE sessions ({','.join(SESSION_COLUMNS)})")
        database.execute(f"CREATE TABLE messages ({','.join(MESSAGE_COLUMNS)})")
        database.execute(
            f"INSERT INTO sessions VALUES ({','.join('?' for _ in SESSION_COLUMNS)})",
            tuple(session[key] for key in SESSION_COLUMNS),
        )
        database.execute(
            f"INSERT INTO messages VALUES ({','.join('?' for _ in MESSAGE_COLUMNS)})",
            tuple(message[key] for key in MESSAGE_COLUMNS),
        )
        database.commit()
    return capture_hermes_db(path, "native-original")


class RegradeTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.executable = self.root / "native-judge"
        self.executable.write_text("test executable boundary")

    def plan(self, batch: Path) -> Path:
        with redirect_stdout(io.StringIO()) as stream:
            result = run_regrades(
                RegradeRequest(
                    evidence=self.root / "plan", batch=batch, judge_executable=self.executable
                )
            )
        self.assertIn("regrade-plan-before-spending", stream.getvalue())
        return result

    def test_local_package_guard_is_zero_judge_invocations(self) -> None:
        batch, _ = source_attempt(self.root)
        manifest = self.plan(batch)
        diagnostic = {
            "packagedRequestPromptSha256": "a" * 64,
            "sizes": {
                "packagedPromptUtf8Bytes": 2_900_000,
                "finalCounts": {"utf8Bytes": 2_900_000, "characters": 2_900_000},
                "appliedLimits": {"utf8Bytes": 1_692_965},
                "topContributors": [],
            },
        }
        with (
            redirect_stdout(io.StringIO()),
            patch(
                "agent_evals.regrade.run_native_judge", side_effect=JudgePackageTooLarge(diagnostic)
            ),
        ):
            result = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
        record = read_object(result)["regrades"][0]
        self.assertEqual(record["judgeInvocations"], 0)
        self.assertEqual(record["unknownClass"], "schema-incompatible")
        self.assertIn("input-package-over-budget", record["unknowns"][0])
        self.assertEqual(read_object(Path(record["output"]) / "judge-send-guard.json"), diagnostic)

    def test_unexpected_grading_decode_failure_records_class_phase_and_cause(self) -> None:
        batch, _source = source_attempt(self.root)
        manifest = self.plan(batch)
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        for phase, failure in (("native-actor", error), ("grading", RuntimeError("bridge"))):
            untouched = {"executionPhase": phase}
            self.assertEqual(
                grading_exception_cause(untouched, failure), f"{type(failure).__name__}: {failure}"
            )
            self.assertEqual(untouched, {"executionPhase": phase})
        for failure in (OSError("missing artifact"), AuthorityError("unavailable binding")):
            classified = {"executionPhase": "grading"}
            grading_exception_cause(classified, failure)
            self.assertEqual(classified["unknown_class"], "binding-unavailable")
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", side_effect=error),
        ):
            result = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
        record = read_object(result)["regrades"][0]
        self.assertEqual(record.get("unknown_class"), "schema-incompatible")
        self.assertEqual(record.get("executionPhase"), "grading")
        self.assertIn("UnicodeDecodeError: " + str(error), record["unknown_cause"])
        self.assertIn("executionPhase=grading", record["unknown_cause"])
        self.assertEqual(record["unknowns"], [record["unknown_cause"]])

    def test_unexpected_preparation_decode_failure_is_classified_before_judge(self) -> None:
        batch, _source = source_attempt(self.root)
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with (
            patch("agent_evals.regrade.prepare_case_grade", side_effect=error),
            patch("agent_evals.regrade.run_native_judge") as judge,
        ):
            manifest = self.plan(batch)
        row = read_object(manifest)["attempts"][0]
        self.assertFalse(row["ready"])
        self.assertEqual(row.get("unknown_class"), "schema-incompatible")
        self.assertEqual(row.get("executionPhase"), "grading")
        self.assertEqual(row["preparationCauses"], [row["unknown_cause"]])
        judge.assert_not_called()

    def test_hermes_census_uses_its_model_and_opposite_family_judge(self) -> None:
        batch, source = source_attempt(self.root, model=HERMES_KIMI_ACTOR.model)
        original_hash = sha256_file(source / "attempt.json")
        self.assertEqual(len(_candidate_attempts(batch)), 1)
        with patch("agent_evals.regrade.run_native_judge") as no_judge:
            manifest = self.plan(batch)
        no_judge.assert_not_called()
        row = read_object(manifest)["attempts"][0]
        self.assertTrue(row["ready"], row.get("preparationCauses"))
        self.assertEqual(row["resultIdentity"]["components"]["judgeModel"], "gpt-6.1-sol")
        retained = read_object(manifest.parent / row["prepared"] / "actor-capture.json")
        self.assertEqual(retained, read_object(source / "attempt.json")["capture"])
        manifest_hash = sha256_file(manifest)
        judge_capture = capture(self.root, judge=True)
        judge_capture = replace(
            judge_capture,
            harness="codex",
            events=tuple(
                replace(event, payload={**event.payload, "model": "gpt-6.1-sol"})
                for event in judge_capture.events
            ),
        )
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", return_value=judge_capture) as judge,
        ):
            result = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
        self.assertEqual(judge.call_args.args[1], HERMES_KIMI_ACTOR)
        self.assertEqual(read_object(result)["regrades"][0]["status"], "right")
        with (
            redirect_stdout(io.StringIO()) as dry_output,
            patch("agent_evals.regrade.run_native_judge") as skipped,
        ):
            again = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
            dry_output.seek(0)
            dry_output.truncate()
            run_regrades(RegradeRequest(manifest=manifest, dry_run=True))
        skipped.assert_not_called()
        self.assertEqual(read_object(again)["regrades"][0]["skipMode"], "tooling_check")
        self.assertEqual(
            json.loads(dry_output.getvalue())["modeProjection"]["measurement"][
                "projectedJudgeInvocations"
            ],
            1,
        )
        self.assertEqual(sha256_file(manifest), manifest_hash)
        self.assertEqual(sha256_file(source / "attempt.json"), original_hash)

    def test_hermes_capture_requires_its_model_family_and_frozen_row_digest(self) -> None:
        _batch, source = source_attempt(self.root, model=HERMES_ACTOR.model)
        raw = read_object(source / "attempt.json")["capture"]
        self.assertEqual(json_digest(_retained_capture(raw, "hermes").to_dict()), json_digest(raw))
        with self.assertRaisesRegex(AuthorityError, "family"):
            _retained_capture(raw, "codex")
        raw["raw_export_sha256"] = None
        with self.assertRaisesRegex(ValueError, "digest"):
            _retained_capture(raw, "hermes")

    def test_retained_real_hermes_dispatch_is_normalized_without_rewriting_refusal(self) -> None:
        fixture = read_object(Path(__file__).parent / "fixtures/hermes-nested-authoring.json")
        selected = fixture["rows"][-1]
        raw = {
            **selected["capture"],
            "capture_version": "native-capture/v1",
            "raw_export_sha256": raw_export_digest(
                "hermes", selected["capture"]["session_id"], selected["capture"]["raw_records"]
            ),
        }
        # This fixture deliberately contains only selected authoring pairs,
        # not a whole session. Exercise the dispatcher component, never invent
        # a missing sessions row to admit it as complete retained capture.
        normalized = normalize_hermes_dispatches(
            NativeCapture(
                "hermes",
                raw["session_id"],
                tuple(NativeEvent(**item) for item in raw["events"]),
                tuple(SourceFile(**item) for item in raw["source_files"]),
                tuple(raw["raw_records"]),
                tuple(raw["unknowns"]),
                raw_export_sha256=raw["raw_export_sha256"],
            )
        )
        call, result = normalized.events
        self.assertEqual(call.payload["name"], "mcp__agents_remember_task__knowledge_write")
        self.assertEqual(
            call.payload["arguments"],
            json.loads(raw["events"][0]["payload"]["arguments"])["calls"][0]["arguments"],
        )
        self.assertEqual(call.payload["call_id"], result.payload["call_id"])
        self.assertEqual(result.payload, raw["events"][1]["payload"])
        self.assertIn("refused", result.payload["output"])
        self.assertIn("plannedBeforeRefusal", result.payload["output"])
        self.assertEqual(normalized.raw_records, tuple(raw["raw_records"]))
        self.assertEqual(normalized.raw_export_sha256, raw["raw_export_sha256"])
        self.assertEqual(normalize_hermes_dispatches(normalized), normalized)
        with self.assertRaisesRegex(ValueError, "exactly one selected session"):
            _retained_capture(raw, "hermes")

    def test_hermes_judge_discovers_the_bound_codex_capture_home(self) -> None:
        home = self.root / "native-codex-home"
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            sources = _JudgeSources(self.root, "candidate")
            self.assertEqual(sources.native_capture_roots["codex"], home)
            self.assertFalse(home.exists())

    def test_census_keeps_original_and_regrade_git_fails_with_exact_causes(self) -> None:
        batch, original = source_attempt(self.root)
        _batch, non_git = source_attempt(self.root, "real-fail", 2)
        _batch, repeated = source_attempt(self.root, "repeated", 3)
        ledger = batch / "CE01/attempts.json"
        rows = read_object(ledger)["attempts"]
        for row in rows[:2]:
            row.update(status="wrong", unknown_cause=None)
        write_object(ledger, {"attempts": rows})
        cause = "Unadmitted changed path: projects/memory/.git/index"
        write_object(original / "code-grade.json", _grade("CE01", "fail", (), (cause,)).to_dict())
        write_object(
            non_git / "code-grade.json",
            _grade(
                "CE01", "fail", (), ("Unadmitted changed path: projects/code/product.py",)
            ).to_dict(),
        )
        prior = repeated / "regrades/prior/regrade.json"
        write_object(
            prior,
            {
                "originalAttemptId": "repeated",
                "status": "wrong",
                "judgeInvocations": 0,
                "unknowns": [cause],
            },
        )
        old_hash = sha256_file(ledger)
        selected = _candidate_attempts(batch)
        self.assertEqual([row["originalAttemptId"] for row in selected], ["original", "repeated"])
        self.assertEqual(selected[0]["gitDependentVerdicts"][0]["kind"], "original")
        self.assertIsNone(selected[0]["originalCause"])
        self.assertEqual(
            selected[1]["gitDependentVerdicts"][0]["verdict"]["sha256"], sha256_file(prior)
        )
        self.assertEqual(selected[0]["gitDependentVerdicts"][0]["causes"], [cause])
        self.assertEqual(sha256_file(ledger), old_hash)

    def test_empty_questions_keep_fail_and_unknown_evidence_with_provenance(self) -> None:
        for status in ("fail", "unknown"):
            with self.subTest(status=status):
                scenario = self.root / status
                batch, _source = source_attempt(scenario)
                evidence = (
                    "attempt:bound-native-inputs",
                    "changed existing subject has no captured judgment row: INV-1RWT3JRR",
                )
                code = _grade("CE01", status, evidence, ())
                with (
                    redirect_stdout(io.StringIO()),
                    patch("agent_evals.trials.grade_case_code", return_value=code),
                    patch("agent_evals.regrade.run_native_judge") as judge,
                ):
                    manifest = run_regrades(
                        RegradeRequest(
                            evidence=scenario / "plan",
                            batch=batch,
                            judge_executable=self.executable,
                        )
                    )
                judge.assert_not_called()
                row = read_object(manifest)["attempts"][0]
                self.assertFalse(row["ready"])
                self.assertEqual(row["preparationCauses"], list(evidence))
                provenance = row["preparationCauseProvenance"]
                self.assertEqual(provenance["field"], "evidence")
                self.assertEqual(sha256_file(Path(provenance["path"])), provenance["sha256"])
                self.assertEqual(_census_row(row)["preparationCauseProvenance"], provenance)

    def test_supersession_preserves_old_false_fail_and_records_unknown_correction(self) -> None:
        batch, source = source_attempt(self.root)
        prior = source / "regrades/prior/regrade.json"
        cause = "Unadmitted changed path: projects/memory/.git/objects/aa/object"
        write_object(
            prior,
            {
                "originalAttemptId": "original",
                "status": "wrong",
                "judgeInvocations": 0,
                "unknowns": [cause],
                "tooling_check": True,
                "newPackageHash": None,
            },
        )
        before = prior.read_bytes()
        row = _candidate_attempts(batch)[0]
        target = self.root / "correction"
        code = _grade("CE01", "unknown", (), ("Historical ref evidence is unavailable",))
        write_object(target / "code-grade.json", code.to_dict())
        _record_supersessions(row, target, code)
        row.update(
            ready=False,
            newPackageHash=None,
            resultIdentity=deterministic_key(),
            preparationSource=preparation_source(),
        )
        _prior_summary(row)
        entry = read_object(target / "supersessions.json")["supersededVerdicts"][0]
        self.assertEqual(entry["label"], "superseded-false-fail")
        self.assertEqual(entry["correctedDeterministicStatus"], "unknown")
        self.assertEqual(entry["causingPaths"], ["projects/memory/.git/objects/aa/object"])
        self.assertEqual(prior.read_bytes(), before)
        self.assertEqual(row["existingRegrades"][0]["status"], "superseded-false-fail")
        self.assertEqual(row["existingRegrades"][0]["identityStatus"], "invalid")
        self.assertTrue(row["wouldRunByMode"]["tooling_check"])
        self.assertIsNone(_previous_regrade(row, True, 0))

    def test_explicit_completed_attempt_can_be_rechecked_without_an_actor(self) -> None:
        batch, source = source_attempt(self.root)
        ledger = batch / "CE01/attempts.json"
        data = read_object(ledger)
        data["attempts"][0].update(status="right", unknown_cause=None)
        write_object(ledger, data)
        write_object(source / "code-grade.json", _grade("CE01", "needs_rubric").to_dict())
        self.assertEqual(_candidate_attempts(batch), [])
        with (
            redirect_stdout(io.StringIO()),
            patch(
                "agent_evals.regrade.run_native_judge", side_effect=AssertionError("no inference")
            ),
        ):
            path = run_regrades(
                RegradeRequest(
                    evidence=self.root / "explicit-plan",
                    batch=batch,
                    judge_executable=self.executable,
                    attempt_ids=("original",),
                )
            )
        plan = read_object(path)
        self.assertEqual(plan["attempts"][0]["originalAttempt"]["status"], "right")
        self.assertEqual(plan["attempts"][0]["source"], str(source.resolve()))
        self.assertEqual(read_object(ledger)["attempts"][0]["status"], "right")

    def test_census_selects_stale_right_and_wrong_without_a_case_list(self) -> None:
        batch, right = source_attempt(self.root, "stale-right")
        _batch, wrong = source_attempt(self.root, "stale-wrong", 2)
        _batch, current = source_attempt(self.root, "current", 3)
        ledger = batch / "CE01/attempts.json"
        data = read_object(ledger)
        for row, status in zip(data["attempts"], ("right", "wrong", "right"), strict=True):
            row.update(status=status, unknown_cause=None)
        write_object(ledger, data)
        write_object(right / "code-grade.json", {"grader_id": "CE01", "status": "needs_rubric"})
        stale = _grade("CE01", "fail").to_dict()
        producer = stale["producer"]
        assert isinstance(producer, dict)
        producer["sha256"] = "0" * 64
        write_object(wrong / "code-grade.json", stale)
        write_object(current / "code-grade.json", _grade("CE01", "needs_rubric").to_dict())
        before = {path: sha256_file(path / "code-grade.json") for path in (right, wrong, current)}
        selected = _candidate_attempts(batch)
        self.assertEqual(
            [row["originalAttemptId"] for row in selected], ["stale-right", "stale-wrong"]
        )
        self.assertTrue(all(row["originalGradeSources"]["status"] == "stale" for row in selected))
        self.assertTrue(all(row["originalGradeSources"]["reasons"] for row in selected))
        self.assertEqual(before, {path: sha256_file(path / "code-grade.json") for path in before})

    def test_current_helper_producer_is_current_in_census_and_execution(self) -> None:
        batch, source = source_attempt(self.root)
        helper = Grade.produced(
            Path(__file__).resolve().parents[2] / "scripts/agent_evals/graders/helpers.py",
            "HW1",
            "fail",
            ("helper failed its explicit task",),
        )
        write_object(source / "code-grade.json", helper.to_dict())
        ledger = read_object(batch / "CE01/attempts.json")
        ledger["attempts"][0].update(status="wrong", unknown_cause=None)
        write_object(batch / "CE01/attempts.json", ledger)
        self.assertEqual(_candidate_attempts(batch), [])
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.trials.grade_case_code", return_value=helper),
        ):
            manifest = run_regrades(
                RegradeRequest(
                    evidence=self.root / "helper-plan",
                    batch=batch,
                    attempt_ids=("original",),
                    judge_executable=self.executable,
                )
            )
        with redirect_stdout(io.StringIO()), patch("agent_evals.regrade.run_native_judge") as judge:
            result = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
        judge.assert_not_called()
        self.assertEqual(read_object(result)["regrades"][0]["status"], "wrong")

    def test_equal_size_different_disk_values_cannot_reuse_the_judged_key(self) -> None:
        identities = (
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        )
        for slot, (identity, value) in enumerate(zip(identities, ("one", "two"), strict=True), 1):
            batch, source = source_attempt(self.root, identity=identity, slot=slot)
            target = source / "after/projects/memory/state.txt"
            target.write_text(value)
            original = read_object(source / "attempt.json")
            original["after"] = [
                EvidenceFile.capture(source / "after", "projects/memory/state.txt").to_dict()
            ]
            write_object(source / "attempt.json", original)
        manifest = self.plan(batch)
        rows = read_object(manifest)["attempts"]
        self.assertEqual(len(rows), 2)
        bindings = []
        for row in rows:
            prepared = manifest.parent / row["prepared"]
            payload = read_object(prepared / "judge-request.json")
            request = JudgeRequest(
                tuple(payload["rubric"]),
                tuple(payload["observations"]),
                tuple(payload["artifact_evidence"]),
            )
            bindings.append(load_judge_disk_evidence(prepared, request, "claude"))
        self.assertEqual(rows[0]["newPackageHash"], rows[1]["newPackageHash"])
        self.assertNotEqual(bindings[0].package_identity, bindings[1].package_identity)
        # This is the old false reuse, reproduced by the production constructor.
        self.assertEqual(
            judged_identity(rows[0]["newPackageHash"], "claude-opus-5-5"),
            judged_identity(rows[1]["newPackageHash"], "claude-opus-5-5"),
        )
        self.assertNotEqual(rows[0]["resultIdentity"], rows[1]["resultIdentity"])
        for row, binding in zip(rows, bindings, strict=True):
            self.assertEqual(row["newPackageIdentity"], binding.package_identity)
            self.assertEqual(
                row["resultIdentity"]["components"]["packageSha256"], binding.package_identity
            )

    def test_regrade_rebinds_every_local_folder_occurrence_without_identity_change(self) -> None:
        batch, _source = source_attempt(self.root)
        plan = self.plan(batch)
        row = read_object(plan)["attempts"][0]
        original = plan.parent / row["prepared"]
        old_manifest = read_object(original / "judge-evidence-manifest.json")
        old_hash = sha256_file(original / "judge-evidence-manifest.json")
        native = capture(self.root, judge=True)
        checked = []

        def inspect(_sources, _actor, request, target, _options):
            current = read_object(target / "judge-evidence-manifest.json")
            self.assertNotIn(str(original), json.dumps(current))
            for binding in (current["diskEvidence"], current["packageBudget"]["diskEvidence"]):
                self.assertEqual(binding["root"], str(target / "rubric-judge/evidence"))
                self.assertEqual(
                    binding["packageIdentity"], old_manifest["diskEvidence"]["packageIdentity"]
                )
            binding = load_judge_disk_evidence(target, request, "claude")
            self.assertEqual(
                binding.package_identity, old_manifest["diskEvidence"]["packageIdentity"]
            )
            self.assertEqual(current["sourceFiles"], old_manifest["sourceFiles"])
            checked.append(target)
            return native

        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", side_effect=inspect),
        ):
            result = run_regrades(RegradeRequest(manifest=plan, execute=True, tooling_check=True))
        self.assertEqual(len(checked), 1)
        self.assertEqual(read_object(result)["regrades"][0]["status"], "right")
        self.assertEqual(sha256_file(original / "judge-evidence-manifest.json"), old_hash)
        receipt = read_object(checked[0] / "judge-local-binding.json")
        self.assertEqual(receipt["preparedManifestSha256"], old_hash)
        self.assertEqual(receipt["remainingPreparedRootOccurrences"], 0)

    def test_regrade_retains_explicit_unknown_judge_criterion(self) -> None:
        batch, _source = source_attempt(self.root)
        manifest = self.plan(batch)
        native = capture(self.root, judge=True)
        event = native.events[0]
        verdict = json.loads(event.payload["content"][0]["text"])
        criterion = verdict["criteria"][0]["criterion"]
        verdict["criteria"][0]["outcome"] = "unknown"
        native = replace(
            native,
            events=(
                replace(
                    event,
                    payload={
                        **event.payload,
                        "content": [{"type": "text", "text": json.dumps(verdict)}],
                    },
                ),
            ),
        )
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", return_value=native),
        ):
            result = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
        record = read_object(result)["regrades"][0]
        self.assertEqual(record["status"], "semantic_unknown")
        self.assertEqual(
            record["unknowns"], ["judge explicitly reported unknown criteria: " + criterion]
        )
        attribution = read_object(Path(record["output"]) / "judge-evidence-attribution.json")
        counts = attribution["counts"]
        self.assertEqual(counts["unknown"], counts["attributed"] + counts["unattributed"])
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(attribution["criteria"][0]["evidence"], verdict["criteria"][0]["evidence"])
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge") as forbidden,
            self.assertRaisesRegex(AuthorityError, "semantic_unknown"),
        ):
            run_regrades(RegradeRequest(manifest=manifest, execute=True, tooling_check=True))
        forbidden.assert_not_called()

    def test_semantic_unknown_is_not_selected_for_another_judge(self) -> None:
        batch, source = source_attempt(self.root)
        ledger = batch / "CE01/attempts.json"
        original = read_object(ledger)["attempts"][0]
        cause = "judge explicitly reported unknown criteria: supplied D627 ladder is absent"
        write_object(
            ledger,
            {"attempts": [{**original, "status": "semantic_unknown", "unknown_cause": cause}]},
        )
        self.assertEqual(_candidate_attempts(batch), [])
        with self.assertRaisesRegex(AuthorityError, "semantic_unknown.*D627"):
            _candidate_attempts(batch, frozenset({"original"}))
        # Regrading preserves the original ledger: its retained semantic result
        # must stop automatic selection even if the old row says infrastructure.
        write_object(ledger, {"attempts": [original]})
        write_object(
            source / "regrades/semantic/regrade.json",
            {
                "originalAttemptId": "original",
                "status": "semantic_unknown",
                "unknowns": [cause],
            },
        )
        self.assertEqual(_candidate_attempts(batch), [])
        with self.assertRaisesRegex(AuthorityError, "semantic_unknown.*D627"):
            _candidate_attempts(batch, frozenset({"original"}))

    def test_semantic_label_preserves_capture_failures_and_rubric_fail_precedence(self) -> None:
        native = capture(self.root, judge=True)
        event = native.events[0]
        verdict = json.loads(event.payload["content"][0]["text"])
        request = JudgeRequest(tuple(row["criterion"] for row in verdict["criteria"]), (), ())
        code = Grade("test-code", "needs_rubric")
        verdict["criteria"][0]["outcome"] = "unknown"

        def submitted(value: dict) -> RubricSubmission:
            changed = replace(
                native,
                events=(
                    replace(
                        event,
                        payload={
                            **event.payload,
                            "content": [{"type": "text", "text": json.dumps(value)}],
                        },
                    ),
                ),
            )
            return RubricSubmission(request, "gpt-6.1-sol", changed)

        unknown = submitted(verdict)
        self.assertEqual(resolve_judge_outcome(code, unknown), "semantic_unknown")
        incomplete = replace(
            unknown, judge_capture=replace(unknown.judge_capture, unknowns=("capture incomplete",))
        )
        wrong_model = replace(
            unknown,
            judge_capture=replace(
                unknown.judge_capture,
                events=(
                    replace(
                        unknown.judge_capture.events[0],
                        payload={**unknown.judge_capture.events[0].payload, "model": "wrong-model"},
                    ),
                ),
            ),
        )
        for case in (incomplete, wrong_model):
            self.assertEqual(resolve_judge_outcome(code, case), "missing_capture")
        self.assertEqual(
            resolve_judge_outcome(replace(code, status="unknown"), unknown), "missing_capture"
        )
        self.assertEqual(resolve_judge_outcome(replace(code, status="fail"), unknown), "wrong")
        mixed = json.loads(json.dumps(verdict))
        mixed["criteria"][1]["outcome"] = "fail"
        self.assertEqual(resolve_judge_outcome(code, submitted(mixed)), "wrong")
        absent_evidence = json.loads(json.dumps(verdict))
        absent_evidence["criteria"][0]["evidence"] = ""
        self.assertEqual(resolve_judge_outcome(code, submitted(absent_evidence)), "missing_capture")
        missing_criterion = {"criteria": verdict["criteria"][1:]}
        with self.assertRaisesRegex(AuthorityError, "omitted, reordered or invented"):
            resolve_judge_outcome(code, submitted(missing_criterion))

    def test_identical_package_with_new_preparation_source_appends_a_new_judgment(self) -> None:
        batch, _source = source_attempt(self.root)
        manifest = self.plan(batch)
        initial = read_object(manifest)["attempts"][0]
        native = capture(self.root, judge=True)
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", return_value=native),
        ):
            first = run_regrades(
                RegradeRequest(manifest=manifest, execute=True, tooling_check=True)
            )
        old_record = read_object(first)["regrades"][0]
        old_path = Path(old_record["output"]) / "regrade.json"
        old_hash = sha256_file(old_path)
        changed_source = read_object(manifest.parent / "grader-source.json")
        changed_source["generation"] = "corrected deterministic grader"
        changed_source["sha256"] = json_digest(
            {key: value for key, value in changed_source.items() if key != "sha256"}
        )
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.source_snapshot", return_value=changed_source),
        ):
            fresh = run_regrades(
                RegradeRequest(
                    evidence=self.root / "fresh-plan", batch=batch, judge_executable=self.executable
                )
            )
        fresh_row = read_object(fresh)["attempts"][0]
        self.assertEqual(fresh_row["resultIdentity"], initial["resultIdentity"])
        self.assertNotEqual(
            fresh_row["preparationSource"]["sha256"], initial["preparationSource"]["sha256"]
        )
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", return_value=native) as judge,
        ):
            second = run_regrades(RegradeRequest(manifest=fresh, execute=True, tooling_check=True))
        judge.assert_called_once()
        new_record = read_object(second)["regrades"][0]
        self.assertNotEqual(new_record["output"], old_record["output"])
        self.assertEqual(sha256_file(old_path), old_hash)
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge") as skipped,
        ):
            again = run_regrades(RegradeRequest(manifest=fresh, execute=True, tooling_check=True))
        skipped.assert_not_called()
        self.assertTrue(read_object(again)["regrades"][0]["skippedAlreadyRegraded"])

    def test_unready_deterministic_failure_still_needs_measurement_record_without_judge_cost(
        self,
    ) -> None:
        source = self.root / "original"
        write_object(
            source / "regrades/tooling/regrade.json",
            {
                "originalAttemptId": "original",
                "originalRowHash": "row-hash",
                "newPackageHash": None,
                "tooling_check": True,
                "status": "wrong",
                "resultIdentity": deterministic_key(),
                "preparationSource": preparation_source(),
            },
        )
        row = {
            "source": str(source),
            "originalAttemptId": "original",
            "originalRowHash": "row-hash",
            "ready": False,
            "codeStatus": "fail",
            "newPackageHash": None,
            "newPackageSizes": None,
            "resultIdentity": deterministic_key(),
            "preparationSource": preparation_source(),
        }
        _prior_summary(row)
        self.assertEqual(row["wouldRunByMode"], {"tooling_check": False, "measurement": True})
        self.assertEqual(row["wouldJudgeByMode"], {"tooling_check": False, "measurement": False})
        self.assertEqual(_mode_projection([row])["measurement"]["totalEstimatedTokens"], 0)
        projected = _mode_projection([row])["measurement"]
        self.assertEqual(projected["judgeInvocationsThisRun"], 0)
        self.assertEqual(projected["projectedJudgeInvocations"], 0)
        self.assertEqual(projected["projectedDeterministicResults"], 1)
        self.assertEqual(projected["projectedUnknownResults"], 0)
        self.assertTrue(_census_row(row)["wouldRunByMode"]["measurement"])
        self.assertFalse(_census_row(row)["wouldJudgeByMode"]["measurement"])

    def test_plan_then_explicit_execution_preserves_original_and_allows_later_ledger_append(
        self,
    ) -> None:
        batch, source = source_attempt(self.root)
        original_hash = sha256_file(source / "attempt.json")
        with patch(
            "agent_evals.regrade.run_native_judge", side_effect=AssertionError("plan invoked judge")
        ) as judge:
            plan_path = self.plan(batch)
        judge.assert_not_called()
        plan = read_object(plan_path)
        self.assertEqual(len(plan["attempts"]), 1)
        row = plan["attempts"][0]
        self.assertTrue(row["ready"], row.get("preparationCauses"))
        self.assertEqual(row["oldPackageHash"], "a" * 64)
        self.assertGreater(plan["projectedTokens"], 0)
        ledger = batch / "CE01/attempts.json"
        appended = replace(
            Attempt.from_dict(row["originalAttempt"]), attempt_id="later", trial_slot=2
        )
        write_object(ledger, {"attempts": [row["originalAttempt"], appended.to_dict()]})
        native = capture(self.root, judge=True)
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", return_value=native) as judge,
            patch(
                "agent_evals.adapters.paseo.PaseoNativeAdapter.run",
                side_effect=AssertionError("actor forbidden"),
            ) as actor,
        ):
            result = run_regrades(
                RegradeRequest(manifest=plan_path, execute=True, tooling_check=True)
            )
        actor.assert_not_called()
        judge.assert_called_once()
        record = read_object(result)["regrades"][0]
        self.assertEqual(record["originalAttemptId"], "original")
        self.assertEqual(record["originalCause"], row["originalCause"])
        self.assertEqual(record["newPackageHash"], row["newPackageIdentity"])
        self.assertFalse(record["actor"])
        self.assertEqual(record["status"], "right")
        self.assertEqual(sha256_file(source / "attempt.json"), original_hash)
        self.assertEqual(read_object(ledger)["attempts"][0]["status"], "infrastructure_failure")
        self.assertFalse(record["measurement"])
        self.assertTrue(record["tooling_check"])
        self.assertTrue(Path(record["output"]).is_relative_to(source / "regrades"))
        manifest_hash = sha256_file(plan_path)
        with (
            redirect_stdout(io.StringIO()) as dry_output,
            patch("agent_evals.regrade.run_native_judge") as skipped,
        ):
            run_regrades(RegradeRequest(manifest=plan_path, dry_run=True))
            dry_run = json.loads(dry_output.getvalue())
            self.assertNotIn("judgeInvocations", dry_run)
            self.assertEqual(dry_run["judgeInvocationsThisRun"], 0)
            measurement_plan = dry_run["modeProjection"]["measurement"]
            self.assertEqual(
                {
                    key: measurement_plan[key]
                    for key in (
                        "judgeInvocationsThisRun",
                        "projectedJudgeInvocations",
                        "projectedDeterministicResults",
                    )
                },
                {
                    "judgeInvocationsThisRun": 0,
                    "projectedJudgeInvocations": 1,
                    "projectedDeterministicResults": 0,
                },
            )
            self.assertEqual(dry_run["modeProjection"]["tooling_check"]["skippedRecords"], 1)
            repeated = run_regrades(
                RegradeRequest(manifest=plan_path, execute=True, tooling_check=True)
            )
        skipped.assert_not_called()
        self.assertEqual(sha256_file(plan_path), manifest_hash)
        self.assertTrue(read_object(repeated)["regrades"][0]["skippedAlreadyRegraded"])
        self.assertEqual(read_object(repeated)["regrades"][0]["skipMode"], "tooling_check")
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", return_value=native) as measurement,
        ):
            measured = run_regrades(RegradeRequest(manifest=plan_path, execute=True))
        measurement.assert_called_once()
        self.assertTrue(read_object(measured)["regrades"][0]["measurement"])
        self.assertEqual(len(list((source / "regrades").glob("*/regrade.json"))), 2)

    def test_interrupted_pending_or_competing_claim_never_invokes_second_judge(self) -> None:
        batch, _source = source_attempt(self.root)
        plan_path = self.plan(batch)
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_regrades(RegradeRequest(manifest=plan_path, execute=True, tooling_check=True))
        with redirect_stdout(io.StringIO()), patch("agent_evals.regrade.run_native_judge") as judge:
            result = run_regrades(
                RegradeRequest(manifest=plan_path, execute=True, tooling_check=True)
            )
        judge.assert_not_called()
        record = read_object(result)["regrades"][0]
        self.assertTrue(record["stalled"])
        self.assertEqual(record["skipMode"], "tooling_check")
        self.assertEqual(read_object(result)["exitCode"], 1)
        with redirect_stdout(io.StringIO()) as projection:
            run_regrades(RegradeRequest(manifest=plan_path, dry_run=True))
        projected = json.loads(projection.getvalue())
        self.assertEqual(projected["attempts"][0]["existingRegrades"][0]["status"], "pending")
        self.assertEqual(projected["modeProjection"]["tooling_check"]["stalledRecords"], 1)
        self.assertEqual(projected["modeProjection"]["tooling_check"]["skippedRecords"], 0)
        self.assertEqual(
            projected["modeProjection"]["measurement"]["previouslyToolingCheckedEstimatedTokens"], 0
        )
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade._previous_regrade", return_value=None),
            patch("agent_evals.regrade.run_native_judge") as racing,
        ):
            competing = run_regrades(
                RegradeRequest(manifest=plan_path, execute=True, tooling_check=True)
            )
        racing.assert_not_called()
        self.assertEqual(read_object(competing)["exitCode"], 1)

    def test_changed_selected_row_or_package_rejected_before_judge(self) -> None:
        batch, _source = source_attempt(self.root)
        plan_path = self.plan(batch)
        plan = read_object(plan_path)
        package = plan_path.parent / plan["attempts"][0]["prepared"] / "judge-request.json"
        package.write_text(package.read_text() + " ")
        with (
            patch("agent_evals.regrade.run_native_judge") as judge,
            self.assertRaisesRegex(AuthorityError, "evidence changed"),
        ):
            run_regrades(
                RegradeRequest(
                    manifest=plan_path,
                    execute=True,
                    tooling_check=True,
                )
            )
        judge.assert_not_called()
        self.assertFalse((self.root / "denied").exists())

    def test_changed_selected_ledger_row_is_not_an_allowed_append(self) -> None:
        batch, _source = source_attempt(self.root)
        plan_path = self.plan(batch)
        ledger = batch / "CE01/attempts.json"
        content = read_object(ledger)
        content["attempts"][0]["unknown_cause"] = "changed"
        write_object(ledger, content)
        with (
            patch("agent_evals.regrade.run_native_judge") as judge,
            self.assertRaisesRegex(AuthorityError, "original attempt row changed"),
        ):
            run_regrades(
                RegradeRequest(
                    manifest=plan_path,
                    execute=True,
                    tooling_check=True,
                )
            )
        judge.assert_not_called()

    def test_changed_bytes_during_copy_are_unavailable_before_any_judge(self) -> None:
        batch, _source = source_attempt(self.root)
        copy_file = shutil.copyfile

        def damaged_copy(source, destination):
            result = copy_file(source, destination)
            Path(destination).write_bytes(b"changed during copy")
            return result

        with (
            patch("agent_evals.regrade.shutil.copyfile", side_effect=damaged_copy),
            patch("agent_evals.regrade.run_native_judge") as judge,
        ):
            manifest = self.plan(batch)
        row = read_object(manifest)["attempts"][0]
        self.assertFalse(row["ready"])
        self.assertIn("evidence changed", row["preparationCauses"][0])
        judge.assert_not_called()

    def test_unavailable_capture_stays_in_plan_without_request(self) -> None:
        batch, _good = source_attempt(self.root)
        _batch, source = source_attempt(self.root, "missing", 2)
        report = read_object(source / "attempt.json")
        report.pop("capture")
        write_object(source / "attempt.json", report)
        plan_path = self.plan(batch)
        row = next(
            row
            for row in read_object(plan_path)["attempts"]
            if row["originalAttemptId"] == "missing"
        )
        self.assertEqual(row["originalAttemptId"], "missing")
        self.assertFalse(row["ready"])
        self.assertIn("capture", row["preparationCauses"][0])
        self.assertEqual(row["resultIdentityStatus"], "invalid")
        with (
            redirect_stdout(io.StringIO()),
            patch("agent_evals.regrade.run_native_judge") as judge,
            self.assertRaisesRegex(AuthorityError, "Null is not a result identity"),
        ):
            run_regrades(
                RegradeRequest(
                    manifest=plan_path,
                    execute=True,
                    tooling_check=True,
                )
            )
        judge.assert_not_called()
        self.assertFalse((source / "regrades").exists())

    def test_legacy_null_identity_cannot_skip_new_deterministic_result(self) -> None:
        source = self.root / "legacy"
        previous = source / "regrades/old/regrade.json"
        write_object(
            previous,
            {
                "originalAttemptId": "original",
                "tooling_check": True,
                "newPackageHash": None,
                "status": "wrong",
            },
        )
        original = previous.read_bytes()
        row = {
            "source": str(source),
            "originalAttemptId": "original",
            "ready": False,
            "codeStatus": "fail",
            "resultIdentity": deterministic_key(),
            "preparationSource": preparation_source(),
        }
        _prior_summary(row)
        self.assertEqual(row["existingRegrades"][0]["identityStatus"], "invalid")
        self.assertTrue(row["wouldRunByMode"]["tooling_check"])
        self.assertIsNone(_previous_regrade(row, True, 0))
        self.assertEqual(previous.read_bytes(), original)

    def test_legacy_plan_and_stale_judge_route_rejected_before_skip(self) -> None:
        batch, source = source_attempt(self.root)
        plan_path = self.plan(batch)
        original_plan = read_object(plan_path)
        for identity in (
            None,
            judged_identity(original_plan["attempts"][0]["newPackageHash"], "obsolete-judge"),
        ):
            with self.subTest(identity=identity):
                plan = read_object(plan_path)
                plan["attempts"][0]["resultIdentity"] = identity
                write_object(plan_path, plan)
                with (
                    patch("agent_evals.regrade._previous_regrade") as previous,
                    patch("agent_evals.regrade.run_native_judge") as judge,
                    self.assertRaises(AuthorityError),
                ):
                    run_regrades(
                        RegradeRequest(manifest=plan_path, execute=True, tooling_check=True)
                    )
                previous.assert_not_called()
                judge.assert_not_called()
                self.assertFalse((source / "regrades").exists())

    def test_frozen_grade_source_change_refuses_before_skip_or_judge(self) -> None:
        batch, source = source_attempt(self.root)
        manifest = self.plan(batch)
        with (
            patch(
                "agent_evals.regrade.inspect_grade_sources",
                return_value={"status": "stale", "reasons": ["grade source changed"]},
            ),
            patch("agent_evals.regrade._previous_regrade") as previous,
            patch("agent_evals.regrade.run_native_judge") as judge,
            self.assertRaisesRegex(AuthorityError, "Frozen code grade is stale"),
        ):
            run_regrades(RegradeRequest(manifest=manifest, execute=True))
        previous.assert_not_called()
        judge.assert_not_called()
        self.assertFalse((source / "regrades").exists())

    def test_explicit_plan_ids_exclude_other_unknowns_from_preparation(self) -> None:
        batch, _source = source_attempt(self.root)
        source_attempt(self.root, "not-selected", 2)
        with redirect_stdout(io.StringIO()):
            path = run_regrades(
                RegradeRequest(
                    evidence=self.root / "selected",
                    batch=batch,
                    attempt_ids=("original",),
                    judge_executable=self.executable,
                )
            )
        plan = read_object(path)
        self.assertEqual([row["originalAttemptId"] for row in plan["attempts"]], ["original"])
        self.assertEqual(
            [row["originalAttemptId"] for row in plan["excludedUnknownAttempts"]], ["not-selected"]
        )
        self.assertFalse((path.parent / "prepared/not-selected").exists())

    def test_cli_requires_explicit_execution_and_separate_output(self) -> None:
        args = parser().parse_args(
            [
                "census",
                "--batch",
                "batch",
                "--evidence",
                "out",
                "--judge-executable",
                "/native-judge",
            ]
        )
        self.assertEqual(args.operation, "census")
        batch, _source = source_attempt(self.root)
        with self.assertRaisesRegex(AuthorityError, "separate"):
            run_regrades(
                RegradeRequest(
                    evidence=batch / "overwrite", batch=batch, judge_executable=self.executable
                )
            )
        with self.assertRaisesRegex(AuthorityError, "Planning requires"):
            run_regrades(RegradeRequest(manifest=self.root / "missing.json"))
