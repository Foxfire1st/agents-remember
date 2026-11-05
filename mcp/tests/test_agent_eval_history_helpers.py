"""Good/bad artifact judgments and source-led helper observations, not actor claims."""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.capture import capture_claude_jsonl, capture_codex_jsonl
from agent_evals.adapters.hermes import MESSAGE_COLUMNS, SESSION_COLUMNS, capture_hermes_db
from agent_evals.adapters.host_events import HostCaptureWindow, merge_host_events
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.artifacts import EvidenceFile, sha256_file, write_object
from agent_evals.graders.helpers import HelperEvent, HelperObservation, grade_helpers
from agent_evals.graders.history import HistoryObservation, grade_history

INV = "INV-7K3F9Q"
FAM = "FAM-2M8HXR"
ENTRY = "RLZ-0QYX992Q"
RECORD = f"knowledge/invariants/{INV}-test.json"
HISTORY = "knowledge/history/260928-MIK-L62.json"
CARD = "onboarding/source.py.json"
OWNER = "fixture-owner"


def invariant() -> dict[str, Any]:
    return {
        "schema": "ar-invariant/v1",
        "id": INV,
        "revision": 1,
        "status": "accepted",
        "statement": "A result retains its source.",
        "applicability": "Every result.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "origin": {"task": "fixture", "leaf": "260928-MIK-L01"},
        "admission": "legacy-unassessed",
    }


def anchor(name: str = "source", *, path: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "locator": {"kind": "symbol", "name": name},
        "blob": "0" * 40,
        "content": "sha256:" + "1" * 64,
    }
    if path:
        result["path"] = "source.py"
    return result


def sidecar(name: str = "source", extra: bool = False) -> dict[str, Any]:
    entries = [
        {
            "id": ENTRY,
            "invariant": INV,
            "anchor": anchor(name, path=False),
            "role": "support",
            "rationale": "Carries the observed result.",
        }
    ]
    if extra:
        entries.append({**entries[0], "id": "RLZ-9Q7K3F9Q"})
    return {
        "schema": "ar-onboarding-file/v1",
        "path": "source.py",
        "references": {},
        "realizes": entries,
    }


def row(disposition: str = "no_impact") -> dict[str, Any]:
    return {
        "id": "ROW-3F9Q7K",
        "subject": INV,
        "disposition": disposition,
        "reason": "The actual source guarantee and entries remain supported.",
        "items": [],
        "revision": 1,
        "covers": [{"id": ENTRY, "before": anchor(), "after": anchor()}],
    }


def helper_update(status: str) -> dict[str, Any]:
    return {
        "type": "agent.provider_subagents.update",
        "payload": {
            "kind": "upsert",
            "subagent": {
                "id": "helper-one",
                "parentAgentId": OWNER,
                "status": status,
                "createdAt": "2026-10-29T10:00:00+00:00",
            },
        },
    }


def turn_end(text: str = "Work complete.") -> dict[str, Any]:
    return {
        "type": "agent.turn_ended",
        "agent": {"id": OWNER},
        "turnId": "turn-one",
        "timeline": [{"type": "assistant_message", "turnId": "turn-one", "text": text}],
    }


def wait_log(kind: str, minute: int = 0) -> dict[str, Any]:
    return {
        "at": f"2026-10-29T10:{minute:02}:00+00:00",
        "agent": OWNER,
        "role": "curator",
        "task": "fixture/leaf.json",
        "kind": kind,
        "count": 1,
        "reason": "Observed native wait lifecycle.",
    }


class HistoryHelperGraderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.before, self.after = self.root / "before", self.root / "after"
        self.before.mkdir()
        self.after.mkdir()
        native = self.root / "native.jsonl"
        native.write_text(
            "\n".join(
                json.dumps(value)
                for value in (
                    {"type": "session_meta", "payload": {"id": "fixture-session"}},
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "write-one",
                            "name": "mcp__agents_remember_task__knowledge_write",
                            "arguments": '{"operation":"author"}',
                        },
                    },
                )
            )
            + "\n"
        )
        self.capture = capture_codex_jsonl([native], "fixture-session")

    def history_observation(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        judgment: dict[str, Any],
        before_card: dict[str, Any] | None = None,
        after_card: dict[str, Any] | None = None,
    ) -> HistoryObservation:
        write_object(self.before / RECORD, before)
        write_object(self.after / RECORD, after)
        write_object(self.before / CARD, before_card or sidecar())
        write_object(self.after / CARD, after_card or sidecar())
        write_object(
            self.after / HISTORY,
            {
                "schema": "ar-history/v1",
                "leaf": "260928-MIK-L62",
                "closed": False,
                "rows": [judgment],
            },
        )
        return HistoryObservation(
            INV,
            self.before,
            self.after,
            RECORD,
            EvidenceFile.capture(self.before, RECORD),
            EvidenceFile.capture(self.after, RECORD),
            EvidenceFile.capture(self.after, HISTORY),
            self.capture,
            (1,),
            (EvidenceFile.capture(self.before, CARD),),
            (EvidenceFile.capture(self.after, CARD),),
        )

    def native_helpers(
        self, rows: list[dict[str, Any]], *, complete: bool = True
    ) -> HelperObservation:
        path = self.root / "host-events.jsonl"
        path.write_text("\n".join(json.dumps(value) for value in rows) + "\n")
        digest = sha256_file(path)
        events = tuple(
            NativeEvent(
                "message" if value.get("role") == "user" else "native",
                "fixture-session",
                number,
                value,
                str(path),
                digest,
            )
            for number, value in enumerate(rows)
        )
        capture = NativeCapture(
            "codex", "fixture-session", events, (SourceFile(str(path), digest),), tuple(rows), ()
        )
        return HelperObservation(capture, OWNER, complete=complete)

    def dated_native(self, harness: str, finish: str) -> NativeCapture:
        path = self.root / f"{harness}-dated.jsonl"
        if harness == "hermes":
            path = self.root / "state.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute(f"CREATE TABLE sessions ({','.join(SESSION_COLUMNS)})")
                db.execute(f"CREATE TABLE messages ({','.join(MESSAGE_COLUMNS)})")
                session: dict[str, Any] = dict.fromkeys(SESSION_COLUMNS)
                session.update(
                    id="h",
                    model="deepseek",
                    started_at=1793268000,
                    ended_at=1793268005,
                    message_count=1,
                    api_call_count=0,
                )
                message: dict[str, Any] = dict.fromkeys(MESSAGE_COLUMNS)
                message.update(
                    id=1,
                    session_id="h",
                    role="assistant",
                    content="Complete",
                    timestamp=1793268004.5,
                    finish_reason="stop",
                    active=1,
                )
                db.execute(
                    f"INSERT INTO sessions VALUES ({','.join('?' for _ in SESSION_COLUMNS)})",
                    [session[key] for key in SESSION_COLUMNS],
                )
                db.execute(
                    f"INSERT INTO messages VALUES ({','.join('?' for _ in MESSAGE_COLUMNS)})",
                    [message[key] for key in MESSAGE_COLUMNS],
                )
                db.commit()
            return capture_hermes_db(path, "h")
        if harness == "claude":
            rows = [
                {
                    "timestamp": finish,
                    "sessionId": "c",
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "Consider artifacts."},
                            {"type": "text", "text": "Complete"},
                        ],
                    },
                }
            ]
            path.write_text("\n".join(json.dumps(value) for value in rows) + "\n")
            return capture_claude_jsonl([path], "c")
        rows = [
            {"timestamp": "2026-10-29T10:00:00Z", "type": "session_meta", "payload": {"id": "s"}}
        ]
        for timestamp, kind, payload in (
            ("00.1", "event_msg", {"type": "task_started", "turn_id": "turn-one"}),
            ("00.2", "event_msg", {"type": "agent_reasoning", "text": "Inspect actual artifacts."}),
            (
                "00.3",
                "response_item",
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Check the source."}],
                },
            ),
            ("00.4", "event_msg", {"type": "agent_message", "message": "Inspecting."}),
            (
                "00.5",
                "response_item",
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Working."}],
                },
            ),
            (
                "00.6",
                "event_msg",
                {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 5}}},
            ),
        ):
            rows.append(
                {"timestamp": f"2026-10-29T10:00:{timestamp}Z", "type": kind, "payload": payload}
            )
        rows.append(
            {
                "timestamp": finish,
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "Complete"},
            }
        )
        path.write_text("\n".join(json.dumps(value) for value in rows) + "\n")
        return capture_codex_jsonl([path], "s")

    def test_complete_native_adapters_ignore_only_known_non_helper_shapes(self) -> None:
        host = self.root / "captured-host.jsonl"
        rows = [helper_update("running"), helper_update("completed"), turn_end()]
        for second, value in zip((1, 4, 5), rows, strict=True):
            value["timestamp"] = f"2026-10-29T10:00:{second:02}Z"
        host.write_text("\n".join(json.dumps(value) for value in rows) + "\n")
        window = HostCaptureWindow("2026-10-29T10:00:00Z", "2026-10-29T10:00:06Z", True)
        for harness in ("codex", "hermes", "claude"):
            with self.subTest(harness=harness):
                native = self.dated_native(harness, "2026-10-29T10:00:04.5Z")
                merged = merge_host_events(
                    native, owner_id=OWNER, host_event_paths=[host], window=window
                )
                self.assertEqual(merged.hw1_coverage, "complete", merged.capture.unknowns)
                result = grade_helpers(HelperObservation(merged.capture, OWNER, complete=True))
                self.assertEqual(result.status, "pass", result.evidence)
        native = self.dated_native("codex", "2026-10-29T10:00:02Z")
        merged = merge_host_events(native, owner_id=OWNER, host_event_paths=[host], window=window)
        result = grade_helpers(HelperObservation(merged.capture, OWNER, complete=True))
        self.assertEqual(result.status, "fail")
        self.assertTrue(
            any("silent turn end with running helpers" in item for item in result.evidence)
        )
        for harness in ("codex", "hermes", "claude"):
            opaque = NativeEvent(
                "native",
                merged.capture.session_id,
                999,
                {"type": "unknown_helper_protocol", "text": "All helpers done"},
                merged.capture.events[0].source,
                merged.capture.events[0].source_sha256,
            )
            good_capture = self.native_helpers(
                [helper_update("running"), helper_update("completed"), turn_end()]
            ).capture
            opaque = replace(
                opaque,
                session_id=good_capture.session_id,
                source=good_capture.events[0].source,
                source_sha256=good_capture.events[0].source_sha256,
            )
            capture = replace(good_capture, harness=harness, events=(*good_capture.events, opaque))
            self.assertEqual(
                grade_helpers(HelperObservation(capture, OWNER, complete=True)).status, "unknown"
            )

    def test_five_invariant_dispositions_use_actual_rows_trees_and_pending_rubric(self) -> None:
        # Expected cases independently follow R07v2 rules 2-4, not an actor verdict.
        for disposition in ("changed", "moved", "deleted", "extended", "no_impact"):
            with self.subTest(disposition=disposition):
                before, after, judgment = invariant(), invariant(), row(disposition)
                old_card, new_card = sidecar(), sidecar()
                if disposition == "changed":
                    after.update(statement="Every result retains its complete source.", revision=2)
                    judgment.update(revision=2, effect="clarify")
                elif disposition == "moved":
                    new_card = sidecar("relocated")
                    judgment["covers"][0]["after"] = anchor("relocated")
                elif disposition == "deleted":
                    new_card["realizes"] = []
                    judgment["covers"][0]["after"] = "absent"
                elif disposition == "extended":
                    new_card = sidecar(extra=True)
                    judgment["covers"].append(
                        {"id": "RLZ-9Q7K3F9Q", "before": "absent", "after": anchor()}
                    )
                else:
                    after["admission"] = {
                        "criteria": ["guarded_by_test"],
                        "justification": "A proof was assessed.",
                    }
                result = grade_history(
                    self.history_observation(before, after, judgment, old_card, new_card)
                )
                self.assertEqual(result.status, "needs_rubric", result.evidence)
                self.assertTrue(result.questions)
                self.assertTrue(any("#event-1@sha256:" in ref for ref in result.evidence))

    def test_history_cannot_hide_meaning_revision_or_actual_anchor_change(self) -> None:
        before, after = invariant(), invariant()
        after.update(statement="A changed guarantee.", revision=2)
        result = grade_history(self.history_observation(before, after, row()))
        self.assertEqual(result.status, "fail")
        observation = self.history_observation(
            before, before, row(), after_card=sidecar("different")
        )
        result = grade_history(observation)
        self.assertEqual(result.status, "fail")
        self.assertTrue(any("actual captured sidecar" in ref for ref in result.evidence))

    def test_admission_only_changed_row_does_not_invent_revision_or_disposition(self) -> None:
        before, after, judgment = invariant(), invariant(), row("changed")
        after["admission"] = {
            "criteria": ["guarded_by_test"],
            "justification": "Existing proof assessed.",
        }
        judgment["effect"] = "clarify"
        result = grade_history(self.history_observation(before, after, judgment))
        self.assertEqual(result.status, "fail")
        self.assertTrue(any("plus one" in ref for ref in result.evidence))

    def test_family_schema_is_separate_and_actual_examined_revisions_are_checked(self) -> None:
        family = {
            "schema": "ar-family/v1",
            "id": FAM,
            "revision": 1,
            "status": "accepted",
            "title": "Result family",
            "guarantee": "Results retain their source.",
            "members": [INV],
            "routes": ["."],
            "origin": {"task": "fixture"},
            "admission": "legacy-unassessed",
        }
        changed = {**family, "guarantee": "Results retain their entire source.", "revision": 2}
        judgment = {
            "id": "ROW-3F9Q7K",
            "subject": FAM,
            "disposition": "changed",
            "reason": "Joint guarantee was clarified.",
            "items": [],
            "revision": 2,
            "effect": "clarify",
            "examined": [{"id": INV, "revision": 1}],
        }
        observation = self.history_observation(family, changed, judgment)
        write_object(self.after / "member.json", invariant())
        observation = replace(
            observation,
            subject=FAM,
            after_member_files=(EvidenceFile.capture(self.after, "member.json"),),
        )
        self.assertEqual(grade_history(observation).status, "needs_rubric")
        bad = copy.deepcopy(judgment)
        bad["examined"][0]["revision"] = 2
        observation = replace(
            self.history_observation(family, changed, bad),
            subject=FAM,
            after_member_files=observation.after_member_files,
        )
        self.assertEqual(grade_history(observation).status, "fail")
        bad = {**judgment, "disposition": "moved"}
        observation = replace(self.history_observation(family, changed, bad), subject=FAM)
        self.assertEqual(grade_history(observation).status, "fail")

    def test_missing_artifacts_capture_or_member_observation_stays_unknown(self) -> None:
        observation = self.history_observation(invariant(), invariant(), row())
        self.assertEqual(
            grade_history(replace(observation, native_call_sequences=())).status, "unknown"
        )
        self.assertEqual(
            grade_history(replace(observation, after_entry_files=())).status, "unknown"
        )
        self.assertEqual(
            grade_history(
                replace(
                    observation, capture=replace(self.capture, unknowns=("missing native segment",))
                )
            ).status,
            "unknown",
        )
        (self.after / RECORD).write_text("changed since capture")
        self.assertEqual(grade_history(observation).status, "unknown")

    def test_same_revision_restatement_requires_actual_prior_frozen_governing_row(self) -> None:
        # Product history.py invariant_revision_violation documents this L37 exception.
        record, judgment = invariant(), row("changed")
        record["revision"] = judgment["revision"] = 2
        judgment["effect"] = "clarify"
        observation = self.history_observation(record, record, judgment)
        prior = {
            "schema": "ar-history/v1",
            "leaf": "260928-MIK-L62",
            "closed": True,
            "rows": [copy.deepcopy(judgment)],
        }
        current_path = "knowledge/history/260928-MIK-L62-attempt-2.json"
        write_object(self.after / current_path, {**prior, "attempt": 2, "closed": False})
        write_object(self.after / HISTORY, prior)
        observation = replace(
            observation, history_file=EvidenceFile.capture(self.after, current_path)
        )
        self.assertEqual(grade_history(observation).status, "unknown")
        observation = replace(
            observation, prior_history_files=(EvidenceFile.capture(self.after, HISTORY),)
        )
        self.assertEqual(grade_history(observation).status, "needs_rubric")
        for invalid in ({**prior, "closed": False}, {**prior, "leaf": "260928-MIK-L99"}):
            write_object(self.after / HISTORY, invalid)
            no_witness = replace(
                observation, prior_history_files=(EvidenceFile.capture(self.after, HISTORY),)
            )
            self.assertEqual(grade_history(no_witness).status, "unknown")
        write_object(self.after / HISTORY, prior)
        changed = {**record, "statement": "A different meaning at the same revision."}
        write_object(self.after / RECORD, changed)
        changed_observation = replace(
            observation,
            after_record=EvidenceFile.capture(self.after, RECORD),
            prior_history_files=(EvidenceFile.capture(self.after, HISTORY),),
        )
        self.assertEqual(grade_history(changed_observation).status, "fail")

    def test_closed_history_bytes_are_checked_and_actor_success_cannot_override(self) -> None:
        observation = self.history_observation(invariant(), invariant(), row())
        write_object(
            self.before / HISTORY,
            {"schema": "ar-history/v1", "leaf": "260928-MIK-L62", "closed": True, "rows": []},
        )
        observation = replace(
            observation, before_history=EvidenceFile.capture(self.before, HISTORY)
        )
        self.assertEqual(grade_history(observation).status, "fail")

    def test_helper_collect_before_end_passes_and_silent_end_running_fails(self) -> None:
        good = self.native_helpers(
            [helper_update("running"), helper_update("completed"), turn_end()]
        )
        self.assertEqual(grade_helpers(good).status, "pass")
        bad = self.native_helpers(
            [helper_update("running"), turn_end(), helper_update("completed")]
        )
        self.assertEqual(grade_helpers(bad).status, "fail")

    def test_declared_wait_needs_actual_received_progress_check(self) -> None:
        rows = [
            helper_update("running"),
            turn_end("WAITING FOR SUBAGENTS: helper-one is running."),
            wait_log("subagent-wait-declared"),
            wait_log("subagent-wait-check-sent", 3),
            {
                "role": "user",
                "content": f"[ar-plugin] subagent wait: Curator {OWNER} · fixture/leaf.json\nReport your helpers' progress here.",
            },
            helper_update("completed"),
            turn_end(),
        ]
        self.assertEqual(grade_helpers(self.native_helpers(rows)).status, "pass")
        missing = [value for value in rows if value.get("role") != "user"]
        self.assertEqual(grade_helpers(self.native_helpers(missing)).status, "fail")
        self.assertEqual(
            grade_helpers(self.native_helpers(missing, complete=False)).status, "unknown"
        )

    def test_skipped_check_fails_but_early_canceled_wait_is_not_missing_check(self) -> None:
        rows = [
            helper_update("running"),
            turn_end("WAITING FOR SUBAGENTS: helper-one running."),
            wait_log("subagent-wait-declared"),
        ]
        canceled = [
            *rows,
            wait_log("subagent-wait-ended", 1),
            helper_update("completed"),
            turn_end(),
        ]
        self.assertEqual(grade_helpers(self.native_helpers(canceled)).status, "pass")
        skipped = [*rows, wait_log("subagent-wait-check-skipped", 3), helper_update("completed")]
        self.assertEqual(grade_helpers(self.native_helpers(skipped)).status, "fail")

    def test_complete_assertion_and_unmatched_normalized_helper_events_do_not_create_proof(
        self,
    ) -> None:
        self.assertEqual(grade_helpers(self.native_helpers([turn_end()])).status, "unknown")
        good = self.native_helpers(
            [helper_update("running"), helper_update("completed"), turn_end()]
        )
        unmatched = replace(
            good, events=(HelperEvent(0, "helper_stopped", helper_id="fabricated"),)
        )
        self.assertEqual(grade_helpers(unmatched).status, "unknown")
        accepted = replace(good, events=(HelperEvent(0, "helper_running", helper_id="helper-one"),))
        self.assertEqual(grade_helpers(accepted).status, "pass")
        wrong_session = replace(good.capture.events[0], session_id="unrelated")
        capture = replace(good.capture, events=(wrong_session, *good.capture.events[1:]))
        self.assertEqual(grade_helpers(replace(good, capture=capture)).status, "unknown")

    def test_unscoped_turn_or_future_schema_remains_unknown_without_prose_guessing(self) -> None:
        unscoped = turn_end()
        unscoped.pop("turnId")
        rows = [helper_update("running"), helper_update("completed"), unscoped]
        self.assertEqual(grade_helpers(self.native_helpers(rows)).status, "unknown")
        rows[-1] = {
            "type": "unrecognized_native_event",
            "text": "All helpers completed; checks passed.",
        }
        self.assertEqual(grade_helpers(self.native_helpers(rows)).status, "unknown")
