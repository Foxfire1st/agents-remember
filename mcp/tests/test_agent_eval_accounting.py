"""Curator measurement accounting does not turn missing evidence into reliability."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.accounting import (
    Attempt,
    Outcome,
    compare_reports,
    summarize_attempts,
    summarize_usage,
    validate_attempt_sequence,
)
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.trials import _grade_actor_capture, _record_helper_boundary, _TrialRuntime
from agents_remember.errors import AgentsRememberError

CODEX = "gpt-6.1-sol"
DEEPSEEK = "opencode-go:deepseek-v4.1-flash"
KIMI = "opencode-go:kimi-k3.1-pro"
HERMES_GPT = "openai-codex:gpt-6.1-sol-900k"


def attempt(slot: int = 1, **changes: object) -> Attempt:
    fields: dict[str, object] = {
        "attempt_id": f"CE01-{slot}",
        "case_id": "CE01",
        "model": CODEX,
        "text_signature": "text-old",
        "batch_id": "old-full",
        "trial_slot": slot,
        "status": "right",
    }
    fields.update(changes)
    return Attempt.from_dict(fields)


def full_batch(signature: str, batch: str, model: str = CODEX) -> list[Attempt]:
    return [
        attempt(
            slot,
            attempt_id=f"{batch}-{model}-{case}-{slot}",
            case_id=f"CE{case:02}",
            model=model,
            text_signature=signature,
            batch_id=batch,
            actor_tokens=0,
            judge_tokens=0,
        )
        for case in range(1, 15)
        for slot in range(1, 4)
    ]


def report(attempts: list[Attempt], signature: str = "text-old", batch: str = "old-full"):
    return summarize_attempts(attempts, model=CODEX, text_signature=signature, batch_id=batch)


class AccountingTests(unittest.TestCase):
    def test_real_dispatch_with_controlled_actor_deadline_records_no_liveness_claim(self) -> None:
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/helper-wait-real-dispatches.json").read_text()
        )
        for row in fixture["rows"]:
            events = tuple(NativeEvent(**item["record"]) for item in row["selectedNativeEvents"])
            sources = tuple(dict.fromkeys(SourceFile(e.source, e.source_sha256) for e in events))
            capture = NativeCapture("hermes", row["sessionId"], events, sources, (), ())
            for deadline in (False, True):
                with self.subTest(attempt=row["attemptId"], controlled_deadline=deadline):
                    report = {
                        "waits": [
                            {
                                "state": "running" if deadline else "ended",
                                "actorDeadlineReached": deadline,
                            }
                        ],
                        "unknowns": ["native measurement timed out"] if deadline else [],
                    }
                    runtime = cast(
                        _TrialRuntime,
                        SimpleNamespace(
                            context=SimpleNamespace(
                                request=SimpleNamespace(timeout_seconds=3600, actor_only=False)
                            ),
                            report=report,
                        ),
                    )
                    _record_helper_boundary(runtime, capture)
                    boundary = report["helperBoundary"]
                    self.assertFalse(boundary["helperWaitPerformed"])
                    self.assertFalse(boundary["actorFault"])
                    self.assertEqual(boundary["running_ids"], [])
                    self.assertEqual(boundary["pending_ids"], row["expectedHelperIds"])
                    self.assertEqual(boundary["resultDelivery"], "not delivered")
                    with patch("agent_evals.trials._grade", return_value=("right", 0)) as grade:
                        result = _grade_actor_capture(runtime, capture)
                    if deadline:
                        self.assertEqual(result, ("helper-liveness-unobservable-at-deadline", None))
                        grade.assert_not_called()
                    else:
                        self.assertEqual(result, ("right", 0))
                        self.assertNotIn("helperDeadlineState", report)
                        grade.assert_called_once()

    def test_helper_liveness_deadline_is_its_own_ungraded_state(self) -> None:
        state = "helper-liveness-unobservable-at-deadline"
        stopped = attempt(
            3, status=state, unknown_cause="trial deadline ended; helper liveness unobservable"
        )
        result = report([attempt(), attempt(2), stopped])
        case = result.cases[0]
        self.assertEqual(case.status, state)
        self.assertEqual((case.right, case.wrong, case.unknown_attempts), (2, 0, 0))
        self.assertEqual(case.helper_deadline_attempts, 1)
        self.assertEqual(result.helper_deadline_trials, (stopped,))
        self.assertEqual(result.unknown_trials, ())
        self.assertFalse(case.complete)
        self.assertIsNone(case.at_least_two_of_three)
        replacement = attempt(3, attempt_id="replacement", retry_of=stopped.attempt_id)
        settled = report([attempt(), attempt(2), stopped, replacement])
        self.assertEqual(settled.cases[0].status, "pass")
        self.assertEqual(settled.cases[0].helper_deadline_attempts, 1)
        self.assertEqual(settled.helper_deadline_trials, (stopped,))
        self.assertEqual(settled.graded_outcomes, 3)

    def test_tooling_smoke_retains_usage_without_becoming_measurement_evidence(self) -> None:
        smoke = [
            replace(item, batch_kind="tooling_check", actor_tokens=5, judge_tokens=None)
            for item in full_batch("text-old", "smoke")
        ]
        smoke[0] = replace(
            smoke[0], status="missing_capture", unknown_cause="capture hookup failed"
        )
        smoke[1] = replace(smoke[1], status="wrong")
        measured = full_batch("text-old", "old-full")
        result = report(smoke + measured)
        assert result.complete and result.all_three_right_cases == 14
        assert result.unknown_attempts == 0 and result.unknown_trials == ()
        tooling = report(smoke + measured, batch="smoke")
        assert tooling.batch_kind == "tooling_check" and not tooling.complete
        assert tooling.graded_outcomes == 0 and tooling.all_three_right_cases is None
        assert all(case.right == case.wrong == 0 for case in tooling.cases)
        assert all(theme.at_least_two_of_three_cases is None for theme in tooling.themes)
        assert tooling.usage.actor.observed_attempts == 0
        assert tooling.tooling_usage.actor.known_tokens == 210
        assert tooling.tooling_usage.judge.unknown_attempts == 42
        assert tooling.tooling_unknown_trials[0].cause == "capture hookup failed"
        assert Attempt.from_dict(smoke[0].to_dict()).batch_kind == "tooling_check"
        for old, final in ((tooling, result), (result, tooling)):
            with self.assertRaisesRegex(AgentsRememberError, "excluded from measurement"):
                compare_reports(old, final)

    def test_tooling_checks_cannot_clear_a_failure_or_consume_a_final_full(self) -> None:
        measured = full_batch("text-old", "old-full")
        smoke = [
            replace(item, batch_kind="tooling_check") for item in full_batch("text-old", "smoke")
        ]
        more_smoke = [
            replace(item, batch_id="smoke2", attempt_id=f"more-{item.attempt_id}") for item in smoke
        ]
        final = [
            replace(item, batch_kind="final_full") for item in full_batch("text-old", "final-full")
        ]
        validate_attempt_sequence(measured + smoke + more_smoke + final)
        measured[0] = replace(measured[0], status="wrong")
        measured[1] = replace(measured[1], status="wrong")
        with self.assertRaisesRegex(AgentsRememberError, "previously measured cases to pass"):
            validate_attempt_sequence(measured + smoke + more_smoke + final)

    def test_complete_model_metrics_keep_actor_models_separate_and_fixed_pairs(self) -> None:
        codex = full_batch("text-old", "old-full")
        codex[0] = replace(codex[0], status="wrong")
        deepseek = full_batch("text-old", "old-full", DEEPSEEK)
        kimi = [replace(item, actor_tokens=7) for item in full_batch("text-old", "old-full", KIMI)]
        kimi[0] = replace(kimi[0], status="wrong")
        kimi[1] = replace(kimi[1], status="wrong")
        gpt = full_batch("text-old", "old-full", HERMES_GPT)
        attempts = codex + deepseek + kimi + gpt
        result = report(attempts)
        other = summarize_attempts(
            attempts, model=DEEPSEEK, text_signature="text-old", batch_id="old-full"
        )
        kimi_result = summarize_attempts(
            attempts, model=KIMI, text_signature="text-old", batch_id="old-full"
        )
        gpt_result = summarize_attempts(
            attempts, model=HERMES_GPT, text_signature="text-old", batch_id="old-full"
        )
        assert gpt_result.complete and gpt_result.graded_outcomes == 42
        assert gpt_result.all_three_right_cases == 14
        assert json.loads(json.dumps(gpt_result.to_dict()))["model"] == HERMES_GPT
        assert result.complete and result.graded_outcomes == 42
        assert result.all_three_right_cases == 13
        assert result.at_least_two_of_three_cases == 14
        assert result.cases[0].status == "pass"
        assert result.cases[0].wrong == 1 and result.cases[0].graded_outcomes == 3
        assert result.themes[0].case_ids == ("CE01", "CE02")
        assert result.themes[0].all_three_right_cases == 1
        assert result.themes[0].at_least_two_of_three_cases == 2
        assert other.all_three_right_cases == 14
        assert kimi_result.complete and kimi_result.graded_outcomes == 42
        assert kimi_result.cases[0].status == "fail" and kimi_result.cases[0].wrong == 2
        assert kimi_result.all_three_right_cases == kimi_result.at_least_two_of_three_cases == 13
        assert kimi_result.usage.actor.known_tokens == 294
        assert result.usage.actor.known_tokens == other.usage.actor.known_tokens == 0
        assert len(result.cases) == 14 and len(result.themes) == 7
        assert json.loads(json.dumps(result.to_dict()))["model"] == CODEX
        assert json.loads(json.dumps(other.to_dict()))["model"] == DEEPSEEK
        assert json.loads(json.dumps(kimi_result.to_dict()))["model"] == KIMI
        assert Attempt.from_dict(json.loads(json.dumps(kimi[0].to_dict()))) == kimi[0]
        with self.assertRaisesRegex(AgentsRememberError, "admitted model"):
            attempt(model="openrouter:moonshotai/kimi-k3.1-pro")
        with self.assertRaisesRegex(AgentsRememberError, "admitted model"):
            summarize_attempts(
                attempts,
                model="openrouter:moonshotai/kimi-k3.1-pro",
                text_signature="text-old",
                batch_id="old-full",
            )

    def test_unknowns_are_outside_three_graded_outcomes_and_reliability_denominator(self) -> None:
        result = report(
            [
                attempt(),
                attempt(2),
                attempt(3, status="missing_capture", unknown_cause="native capture absent"),
            ]
        )
        case = result.cases[0]
        assert case.graded_outcomes == 2 and case.unknown_attempts == 1
        assert case.status == "unknown" and not case.complete
        assert case.all_three_right is None and case.at_least_two_of_three is None
        assert result.graded_outcomes == 2 and result.unknown_attempts == 1
        assert result.all_three_right_cases is None
        assert result.themes[0].all_three_right_cases is None
        assert result.cases[1].status == "unknown"  # absent is not an observed failure
        assert case.unknown_trials[0].cause == "native capture absent"
        assert case.unknown_trials[0].owner == "Architect"
        assert case.unknown_trials[0].retry_required
        assert result.to_dict()["unknown_trials"]

    def test_retained_semantic_unknown_is_counted_without_rerun_disposition(self) -> None:
        # Actual final-new-Hermes CE03 judgment, all pass/unknown (no failed criteria);
        # judge-capture.json sha256 0bd61e5d3a8723de0b6f7e82f695ec38cc84255753b51ed1cf0608d90e35bf92.
        criterion = (
            "Use the amended ruling, not its misleading filename or superseded initial text."
        )
        observed = attempt(
            3,
            attempt_id="17de304e-9e4b-4809-bb94-8dbdf5daf86c",
            case_id="CE03",
            model=DEEPSEEK,
            text_signature="d2844ec61bc4feea714f03d05fadb5b6416dc9f40c4a23568d1cbde07c99e5c6",
            batch_id="d31ebdb4-72ae-4a12-b7fa-a68c915b6a90",
            status="semantic_unknown",
            actor_tokens=4973731,
            judge_tokens=813663,
            judge_model=CODEX,
            unknown_cause="judge explicitly reported unknown criteria: " + criterion,
        )
        result = summarize_attempts(
            [observed],
            model=observed.model,
            text_signature=observed.text_signature,
            batch_id=observed.batch_id,
        )
        case = result.cases[2]
        assert case.status == "semantic_unknown" and case.unknown_attempts == 1
        assert not case.complete and case.graded_outcomes == case.right == case.wrong == 0
        assert result.unknown_attempts == 1
        unknown = result.unknown_trials[0]
        assert unknown.status == "semantic_unknown" and unknown.cause == observed.unknown_cause
        assert not unknown.retry_required and unknown.replacement_attempt_id is None
        assert case.usage.actor.known_tokens == 4973731
        assert case.usage.judge.known_tokens == 813663
        replacement = replace(
            observed, attempt_id="unnecessary-rerun", status="right", retry_of=observed.attempt_id
        )
        with self.assertRaisesRegex(AgentsRememberError, "semantic_unknown.*rerun"):
            validate_attempt_sequence([observed, replacement])

    def test_infrastructure_replacement_retains_original_and_its_token_usage(self) -> None:
        first = attempt(
            status="infrastructure_failure",
            actor_tokens=9,
            judge_tokens=None,
            unknown_cause="transport disconnected",
        )
        retry = replace(first, attempt_id="replacement", status="right", retry_of=first.attempt_id)
        result = report([first, retry, attempt(2), attempt(3)])
        case = result.cases[0]
        assert case.status == "pass" and case.graded_outcomes == 3
        assert case.attempts == 4 and case.unknown_attempts == 1
        assert case.usage.actor.known_tokens == 18
        assert case.usage.actor.observed_attempts == 2
        assert case.usage.actor.unknown_attempts == 2
        assert case.unknown_trials[0].replacement_attempt_id == "replacement"
        assert not case.unknown_trials[0].retry_required

    def test_unknown_retries_must_link_latest_unknown_in_same_slot(self) -> None:
        first = attempt(status="unanswered_prompt", unknown_cause="AR prompt was not answered")
        replacement = replace(first, attempt_id="retry", retry_of=first.attempt_id)
        final = replace(first, attempt_id="recovered", status="right", retry_of="retry")
        validate_attempt_sequence([first, replacement, final])
        observed = report([first, replacement, final]).unknown_trials
        assert [item.cause for item in observed] == [first.unknown_cause, replacement.unknown_cause]
        assert [item.replacement_attempt_id for item in observed] == ["retry", "recovered"]
        for invalid in (
            replace(final, retry_of=None),
            replace(final, retry_of=first.attempt_id),
            replace(final, trial_slot=2),
        ):
            with self.assertRaisesRegex(AgentsRememberError, r"latest unknown|no earlier"):
                validate_attempt_sequence([first, replacement, invalid])

    def test_original_three_slots_complete_after_first_wrong_but_fourth_grade_is_rejected(
        self,
    ) -> None:
        attempts = [attempt(status="wrong"), attempt(2), attempt(3)]
        validate_attempt_sequence(attempts)
        assert report(attempts).cases[0].status == "pass"
        assert report(attempts).cases[0].at_least_two_of_three is True
        for slot in (1, 2, 3):
            extra = attempt(slot, attempt_id=f"extra-{slot}", retry_of=f"CE01-{slot}")
            with self.assertRaisesRegex(AgentsRememberError, "graded slot cannot be resampled"):
                validate_attempt_sequence([*attempts, extra])

    def test_unchanged_text_cannot_start_another_behavioral_batch_even_after_passing(
        self,
    ) -> None:
        first = full_batch("text-first-new", "first-new")
        unchanged = full_batch("text-first-new", "lucky-retry")
        with self.assertRaisesRegex(AgentsRememberError, "Unchanged text"):
            validate_attempt_sequence(first + unchanged)
        first[0] = replace(first[0], status="wrong")
        first[1] = replace(first[1], status="wrong")
        assert report(first, "text-first-new", "first-new").cases[0].status == "fail"
        with self.assertRaisesRegex(AgentsRememberError, "Unchanged text"):
            validate_attempt_sequence(first + unchanged)
        targeted = full_batch("text-repaired", "targeted")
        final = full_batch("text-repaired", "final-full")
        validate_attempt_sequence(first + targeted)
        with self.assertRaisesRegex(AgentsRememberError, "Unchanged text"):
            validate_attempt_sequence(first + targeted + final)
        result = report(first + targeted, "text-repaired", "targeted")
        assert result.complete and result.all_three_right_cases == 14
        assert report(first + targeted, "text-first-new", "first-new").cases[0].status == "fail"

    def test_fixed_theme_worsening_uses_two_of_three_and_keeps_both_metrics(self) -> None:
        old = full_batch("text-old", "old-full")
        final = full_batch("text-final", "final-full")
        final[0] = replace(final[0], status="wrong")  # 2/3 does not make a theme worse.
        final[6] = replace(final[6], status="wrong")
        final[7] = replace(final[7], status="wrong")  # 1/3 does make CT2 worse.
        old[12] = replace(old[12], status="wrong")
        old[13] = replace(old[13], status="wrong")  # CT3 improves.
        comparison = compare_reports(report(old), report(final, "text-final", "final-full"))
        assert comparison.complete
        assert comparison.themes[0].status == "unchanged"
        assert comparison.themes[0].old_all_three_right_cases == 2
        assert comparison.themes[0].final_all_three_right_cases == 1
        assert comparison.themes[0].old_at_least_two_of_three_cases == 2
        assert comparison.themes[0].final_at_least_two_of_three_cases == 2
        assert comparison.themes[1].status == "worsened"
        assert comparison.themes[2].status == "improved"
        assert comparison.reopens_loop
        assert json.loads(json.dumps(comparison.to_dict()))["complete"] is True

    def test_named_final_full_respects_a_closed_authority_gate(self) -> None:
        measured = full_batch("text-repaired", "targeted")
        final = [
            replace(item, batch_kind="final_full")
            for item in full_batch("text-repaired", "final-full")
        ]
        with patch("agent_evals.accounting.final_full_repeat_allowed", return_value=False):
            with self.assertRaisesRegex(AgentsRememberError, "closed pending the Architect"):
                validate_attempt_sequence(measured + final)
            with self.assertRaisesRegex(AgentsRememberError, "closed pending the Architect"):
                validate_attempt_sequence(final)

    def test_final_full_is_a_named_step_after_passing_measurements_only(self) -> None:
        measured = full_batch("text-repaired", "targeted")
        measured[0] = replace(measured[0], status="wrong")  # Completed 2/3 is passing.
        final = [
            replace(item, batch_kind="final_full")
            for item in full_batch("text-repaired", "final-full")
        ]
        result = report(measured + final, "text-repaired", "final-full")
        assert result.complete and result.batch_kind == "final_full"
        assert result.to_dict()["batch_kind"] == "final_full"
        assert Attempt.from_dict(final[0].to_dict()).batch_kind == "final_full"
        ordinary = [replace(item, batch_kind="measurement") for item in final]
        with self.assertRaisesRegex(AgentsRememberError, "Unchanged text"):
            validate_attempt_sequence(measured + ordinary)
        for prior in (
            measured[:1],
            [replace(item, status="wrong") for item in measured],
            [replace(item, batch_kind="final_full") for item in measured],
        ):
            with self.assertRaisesRegex(
                AgentsRememberError, "pass first|completed passing measurement"
            ):
                validate_attempt_sequence(prior + final)
        changed_final = [replace(item, text_signature="text-final") for item in final]
        validate_attempt_sequence(measured + changed_final)
        failed = [replace(item, status="wrong") for item in measured]
        with self.assertRaisesRegex(AgentsRememberError, "previously measured cases to pass"):
            validate_attempt_sequence(failed + changed_final)
        mixed = [replace(final[0], batch_kind="measurement"), *final[1:]]
        with self.assertRaisesRegex(AgentsRememberError, "different batch kinds"):
            validate_attempt_sequence(mixed)

    def test_incomplete_comparison_is_unknown_for_affected_fixed_pair(self) -> None:
        old = full_batch("text-old", "old-full")
        final = full_batch("text-final", "final-full")
        final[0] = replace(
            final[0], status="missing_capture", unknown_cause="native export incomplete"
        )
        comparison = compare_reports(report(old), report(final, "text-final", "final-full"))
        assert not comparison.complete
        assert comparison.themes[0].status == "unknown"
        assert comparison.themes[0].final_all_three_right_cases is None
        assert comparison.themes[1].status == "unchanged"
        assert not comparison.reopens_loop

    def test_only_worse_themes_reopen_loop_while_other_final_failures_are_reported(self) -> None:
        old = full_batch("text-old", "old-full")
        final = full_batch("text-final", "final-full")
        for attempts in (old, final):
            attempts[0] = replace(attempts[0], status="wrong")
            attempts[1] = replace(attempts[1], status="wrong")
        final_report = report(final, "text-final", "final-full")
        assert final_report.cases[0].status == "fail"
        comparison = compare_reports(report(old), final_report)
        assert comparison.themes[0].status == "unchanged"
        assert not comparison.reopens_loop

    def test_comparing_existing_reports_is_not_a_rerun_gate_and_keeps_models_separate(self) -> None:
        old = report(full_batch("text-old", "old-full"))
        assert not compare_reports(old, replace(old, batch_id="other-observation")).reopens_loop
        with self.assertRaisesRegex(AgentsRememberError, "one model at a time"):
            compare_reports(old, replace(old, model=DEEPSEEK, text_signature="new"))

    def test_token_accounting_keeps_actor_judge_unknown_and_zero_distinct(self) -> None:
        usage = summarize_usage(
            [
                attempt(actor_tokens=0, judge_tokens=17),
                attempt(2, actor_tokens=None, judge_tokens=0),
                attempt(3, actor_tokens=12, judge_tokens=None),
            ]
        )
        assert usage.actor.known_tokens == 12 and usage.actor.observed_attempts == 2
        assert usage.judge.known_tokens == 17 and usage.judge.observed_attempts == 2
        assert usage.actor.unknown_attempts == usage.judge.unknown_attempts == 1
        assert json.loads(json.dumps(usage.to_dict()))["judge"]["known_tokens"] == 17

    def test_json_boundary_roundtrip_and_invalid_identity_usage_status(self) -> None:
        original = attempt(judge_model="claude-opus-5-5", actor_tokens=0)
        assert Attempt.from_dict(json.loads(json.dumps(original.to_dict()))) == original
        invalid_fields = (
            {"trial_slot": True},
            {"trial_slot": 4},
            {"case_id": "CE15"},
            {"model": "Codex"},
            {"text_signature": ""},
            {"actor_tokens": -1},
            {"judge_tokens": True},
            {"actor_tokens": "0"},
            {"status": "pass"},
            {"status": "missing_capture"},
            {"status": "infrastructure_failure", "unknown_cause": ""},
            {"status": "unanswered_prompt", "unknown_cause": 7},
            {"batch_kind": "ordinary_retry"},
        )
        for invalid in invalid_fields:
            with self.assertRaises(AgentsRememberError):
                Attempt.from_dict(original.to_dict() | invalid)
        with self.assertRaises(AgentsRememberError):
            replace(original, status=cast(Outcome, "not-a-status"))

    def test_batch_and_attempt_identity_conflicts_do_not_double_count(self) -> None:
        first = attempt()
        with self.assertRaisesRegex(AgentsRememberError, "Duplicate attempt identity"):
            validate_attempt_sequence([first, first])
        with self.assertRaisesRegex(AgentsRememberError, "conflicts with the captured batch"):
            report([first], signature="wrong-signature")
        with self.assertRaisesRegex(AgentsRememberError, "different text signatures"):
            validate_attempt_sequence([first, attempt(2, text_signature="different")])
