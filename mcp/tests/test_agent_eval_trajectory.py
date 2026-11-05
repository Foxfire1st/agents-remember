"""R62 GT1/3/4/5 inspect actions and preserve missing observations as unknown."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.capture import (
    capture_claude_cli_jsonl,
    capture_claude_jsonl,
    capture_codex_jsonl,
)
from agent_evals.adapters.judges import JudgeRequest
from agent_evals.artifacts import EvidenceFile, json_digest, write_object
from agent_evals.fixtures import CaseBundle
from agent_evals.graders.cases import (
    CaseEvidence,
    RubricSubmission,
    case_judge_request,
    grade_case_code,
    parse_native_verdict,
    resolve_rubric,
)
from agent_evals.graders.ladder import LadderEvidence, grade_ladder
from agent_evals.graders.routes import RouteAuthority, grade_routes
from agent_evals.graders.trajectory import grade_continuation, grade_encountered_fault
from agents_remember.errors import AuthorityError
from agents_remember.models.knowledge_files.canonical import canonical_text


def capture_at(root: Path, payloads: list[dict]):
    path = root / "native.jsonl"
    rows = [{"type": "session_meta", "payload": {"id": "native-case"}}]
    rows.extend({"type": "response_item", "payload": payload} for payload in payloads)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return capture_codex_jsonl([path], "native-case")


class AgentEvalTrajectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_path_commands_use_admitted_routes_without_requiring_historical_builds(self) -> None:
        for command, required, current_build, expected in (
            ("agents-remember knowledge-ingest --handoff data.json", True, "source-digest", "fail"),
            ("echo agents-remember knowledge-ingest", True, "source-digest", "pass"),
            ("agents-remember --version", True, None, "pass"),
            ("/bound/python -m agents_remember knowledge-ingest", True, "source-digest", "pass"),
            ("/old/python -m agents_remember knowledge-ingest", True, "source-digest", "fail"),
            ("agents-remember knowledge-read --view record", False, "source-digest", "unknown"),
            ("agents-remember knowledge-read --view record", False, None, "pass"),
            ("/old/python -m agents_remember knowledge-ingest", False, None, "pass"),
            ("/bound/python -m agents_remember knowledge-ingest", True, None, "unknown"),
        ):
            with self.subTest(command=command, required=required):
                capture = capture_at(
                    self.root,
                    [
                        {
                            "type": "function_call",
                            "name": "exec_command",
                            "arguments": json.dumps({"cmd": command}),
                        }
                    ],
                )
                result = grade_routes(
                    capture,
                    RouteAuthority(
                        current_build,
                        required,
                        "episode-authority",
                        {"/bound/python": "source-digest", "/old/python": "different-source"},
                    ),
                )
                self.assertEqual(result.status, expected)
        self.assertEqual(
            grade_routes(
                replace(capture, unknowns=("capture gap",)),
                RouteAuthority("digest", False, "authority"),
            ).status,
            "unknown",
        )

    def test_success_prose_does_not_prove_continuation_or_healing(self) -> None:
        capture = capture_at(
            self.root,
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Everything succeeded. All memory healed."}
                    ],
                }
            ],
        )
        self.assertEqual(grade_continuation(capture).status, "needs_rubric")
        self.assertEqual(grade_encountered_fault(capture, ()).status, "unknown")
        self.assertEqual(
            grade_encountered_fault(capture, ("actual-after-tree",)).status, "needs_rubric"
        )

    def test_routes_grade_source_native_command_argv_and_cwd(self) -> None:
        for argv, cwd, expected in (
            (["agents-remember", "knowledge-ingest"], "/bound", "fail"),
            (["/bin/bash", "-lc", "agents-remember knowledge-ingest"], "file:///bound", "fail"),
            (["./python", "-m", "agents_remember", "knowledge-ingest"], "file:///bound", "pass"),
            (["./python", "-m", "agents_remember.cli", "knowledge-ingest"], "/old", "fail"),
            (["/bound/python", "-m", "agents_remember", "knowledge-ingest"], "relative", "unknown"),
            (["/bin/bash", "-l", "-c", "agents-remember knowledge-ingest"], "/bound", "unknown"),
            (
                ["/bin/bash", "-lc", "cd /old && ./python -m agents_remember knowledge-ingest"],
                "/bound",
                "unknown",
            ),
            (["/bound/python", None, "knowledge-ingest"], "/bound", "unknown"),
            (["/bin/echo", "agents-remember knowledge-ingest"], "/bound", "pass"),
        ):
            with self.subTest(argv=argv, cwd=cwd):
                path = self.root / "command-items.jsonl"
                rows = [
                    {"type": "session_meta", "payload": {"id": "native-command"}},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "item_completed",
                            "thread_id": "native-command",
                            "turn_id": "turn-one",
                            "item": {
                                "type": "CommandExecution",
                                "id": "exec-route",
                                "command": argv,
                                "cwd": cwd,
                                "status": "completed",
                                "aggregated_output": "agents-remember knowledge-ingest",
                            },
                        },
                    },
                ]
                path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                capture = capture_codex_jsonl([path], "native-command")
                self.assertFalse(capture.unknowns)
                calls = [event for event in capture.events if event.kind == "tool_call"]
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0].payload["name"], "CommandExecution")
                grade = grade_routes(
                    capture,
                    RouteAuthority(
                        "current",
                        True,
                        "actual-route-authority",
                        {"/bound/python": "current", "/old/python": "older"},
                    ),
                )
                self.assertEqual(grade.status, expected, grade)
                if expected == "unknown":
                    self.assertTrue(grade.questions)
                if expected == "fail":
                    self.assertTrue(any("#event-" in ref for ref in grade.evidence))

    def ladder(self) -> LadderEvidence:
        capture = capture_at(
            self.root,
            [
                {
                    "type": "function_call_output",
                    "call_id": "refusal",
                    "output": '{"state":"refused"}',
                },
                {
                    "type": "function_call",
                    "name": "mcp__agents_remember_task__server_info",
                    "arguments": "{}",
                },
                {
                    "type": "function_call",
                    "name": "mcp__agents_remember_task__role_message",
                    "arguments": '{"body":"owner notice"}',
                },
                {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": '{"patch":"disclosed repair"}',
                },
                {"type": "function_call_output", "call_id": "checks", "output": '{"exit_code":0}'},
            ],
        )
        files = []
        for name in ("disclosure.json", "defect.json", "actual-card.md"):
            path = self.root / name
            path.write_text("actual captured artifact")
            files.append(EvidenceFile.capture(self.root, name))
        return LadderEvidence(
            self.root,
            capture,
            "knowledge_write",
            "card",
            "knowledge_write",
            "card",
            (1,),
            (2,),
            3,
            4,
            (5,),
            files[0],
            files[1],
            (files[2],),
        )

    def test_disclosed_manual_write_requires_rubric_and_is_not_itself_failed(self) -> None:
        result = grade_ladder(self.ladder())
        self.assertEqual(result.status, "needs_rubric")
        self.assertTrue(result.evidence)

    def test_ladder_excess_and_missing_records_fail_but_missing_capture_is_unknown(self) -> None:
        observation = self.ladder()
        for changed in (
            replace(observation, written_class="unrelated-machine-state"),
            replace(observation, owner_notice_event=5),
            replace(observation, defect=None),
            replace(observation, check_events=()),
            replace(observation, refusal_events=(3,)),
        ):
            with self.subTest(changed=changed):
                self.assertEqual(grade_ladder(changed).status, "fail")
        incomplete = replace(
            observation,
            capture=replace(observation.capture, unknowns=("native input truncated",)),
            disclosure=None,
        )
        self.assertEqual(grade_ladder(incomplete).status, "unknown")

    def case_evidence(self) -> CaseEvidence:
        manifest = {
            "specification": {
                "prospectiveGrade": {
                    "deterministicChecks": ["The actual card is true"],
                    "semanticRubric": ["No unrelated repair"],
                }
            }
        }
        case = CaseBundle(self.root, "CE01", manifest, ())
        (self.root / "after").mkdir()
        (self.root / "after/card.md").write_text("Actual supported claim")
        artifact = EvidenceFile.capture(self.root, "after/card.md")
        native = capture_at(
            self.root,
            [
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": '{"cmd":"write actual card"}',
                }
            ],
        )
        attempt = {
            "case": "CE01",
            "caseDigest": json_digest(manifest),
            "status": "captured-ungraded",
            "unknowns": [],
            "installedServerCalls": 0,
            "capture": native.to_dict(),
            "before": [],
            "after": [{"path": "card.md", "sha256": artifact.sha256}],
        }
        return CaseEvidence(case, attempt, frozenset({"card.md"}), self.root, (artifact,))

    def test_case_code_uses_actual_effects_and_preserves_absent_capture(self) -> None:
        evidence = self.case_evidence()
        self.assertEqual(grade_case_code(evidence).status, "needs_rubric")
        self.assertEqual(
            grade_case_code(replace(evidence, admitted_paths=frozenset())).status, "fail"
        )
        native = capture_at(
            self.root,
            [
                {
                    "type": "function_call",
                    "name": "mcp__agents_remember__knowledge_write",
                    "arguments": '{"handoff_path":"card.json"}',
                }
            ],
        )
        self.assertEqual(len(native.installed_server_calls), 1)
        server_call = {
            **evidence.attempt,
            "installedServerCalls": len(native.installed_server_calls),
            "capture": native.to_dict(),
        }
        self.assertEqual(
            grade_case_code(replace(evidence, attempt=server_call)).status, "needs_rubric"
        )
        incomplete = {**evidence.attempt, "unknowns": ["native missing"]}
        self.assertEqual(grade_case_code(replace(evidence, attempt=incomplete)).status, "unknown")

    def test_only_complete_other_model_native_rubric_settles_case(self) -> None:
        evidence = self.case_evidence()
        code = grade_case_code(evidence)
        request = case_judge_request(evidence, ("Observed actual native actions",))
        self.assertNotIn("CE01", request.prompt())
        self.assertIn("Actual supported claim", request.prompt())
        path = self.root / "judge.jsonl"
        for outcome, model, expected in (
            ("pass", "claude-opus-5-5", "right"),
            ("fail", "claude-opus-5-5", "wrong"),
            ("unknown", "claude-opus-5-5", "missing_capture"),
            ("pass", "wrong-model", "missing_capture"),
        ):
            with self.subTest(outcome=outcome, model=model):
                verdict = {
                    "criteria": [
                        {
                            "criterion": criterion,
                            "outcome": outcome,
                            "evidence": "Actual supplied file and native action",
                        }
                        for criterion in request.rubric
                    ]
                }
                row = {
                    "sessionId": "judge-native",
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": model,
                        "usage": {},
                        "content": [{"type": "text", "text": json.dumps(verdict)}],
                    },
                }
                path.write_text(json.dumps(row) + "\n")
                capture = capture_claude_jsonl([path], "judge-native")
                result = resolve_rubric(code, RubricSubmission(request, "gpt-6.1-sol", capture))
                self.assertEqual(result, expected)
        row["message"]["model"] = "claude-opus-5-5"
        row["message"]["content"][0]["text"] = '{"criteria":[]}'
        path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(AuthorityError, "omitted"):
            resolve_rubric(
                code,
                RubricSubmission(
                    request, "gpt-6.1-sol", capture_claude_jsonl([path], "judge-native")
                ),
            )

    def history_case(self, *, family: bool = False) -> CaseEvidence:
        memory = "projects/memory/"
        subject = "INV-7K3F9Q"
        record = {
            "schema": "ar-invariant/v1",
            "id": subject,
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
        anchor = {
            "locator": {"kind": "symbol", "name": "source"},
            "blob": "0" * 40,
            "content": "sha256:" + "1" * 64,
        }
        card = {
            "schema": "ar-onboarding-file/v1",
            "path": "source.py",
            "references": {},
            "realizes": [
                {
                    "id": "RLZ-0QYX992Q",
                    "invariant": subject,
                    "anchor": anchor,
                    "role": "support",
                    "rationale": "Carries the observed result.",
                }
            ],
        }
        record_path = memory + f"knowledge/invariants/{subject}-test.json"
        card_path = memory + "onboarding/source.py.json"
        before = {record_path: record, card_path: card}
        after = copy.deepcopy(before)
        after[record_path]["admission"] = {
            "criteria": ["guarded_by_test"],
            "justification": "Existing proof assessed.",
        }
        judgment = {
            "id": "ROW-3F9Q7K",
            "subject": subject,
            "disposition": "no_impact",
            "reason": "The guarantee and entries remain supported.",
            "items": [],
            "revision": 1,
            "covers": [
                {
                    "id": "RLZ-0QYX992Q",
                    "before": {**anchor, "path": "source.py"},
                    "after": {**anchor, "path": "source.py"},
                }
            ],
        }
        if family:
            family_path = memory + "knowledge/families/FAM-2M8HXR-test.json"
            before[family_path] = {
                "schema": "ar-family/v1",
                "id": "FAM-2M8HXR",
                "revision": 1,
                "status": "accepted",
                "title": "Result family",
                "guarantee": "Results retain their source.",
                "members": [subject],
                "routes": ["."],
                "origin": {"task": "fixture"},
                "admission": "legacy-unassessed",
            }
            after[family_path] = {
                **before[family_path],
                "revision": 2,
                "guarantee": "Results retain their entire source.",
            }
            after[record_path] = record
            judgment = {key: value for key, value in judgment.items() if key != "covers"}
            judgment.update(
                subject="FAM-2M8HXR",
                disposition="changed",
                revision=2,
                effect="clarify",
                examined=[{"id": subject, "revision": 1}],
            )
        history_path = memory + "knowledge/history/260928-MIK-L62.json"
        after[history_path] = {
            "schema": "ar-history/v1",
            "leaf": "260928-MIK-L62",
            "closed": False,
            "rows": [judgment],
        }
        after[memory + "onboarding/overview.index.json"] = {"version": 1, "entries": []}
        manifest = {
            "actorInputs": [{"destination": path} for path in before],
            "specification": {
                "prospectiveGrade": {
                    "deterministicChecks": ["The judgment fits actual records"],
                    "semanticRubric": ["The guarantee is true"],
                }
            },
        }
        inventories = {}
        for phase, values in (("before", before), ("after", after)):
            for path, value in values.items():
                write_object(self.root / phase / path, value)
                (self.root / phase / path).write_text(canonical_text(value))
            inventories[phase] = [
                EvidenceFile.capture(self.root / phase, path).to_dict() for path in values
            ]
        native = capture_at(
            self.root,
            [
                {
                    "type": "function_call",
                    "name": "mcp__agents_remember_task__knowledge_write",
                    "arguments": "{}",
                }
            ],
        )
        attempt = {
            "case": "CE14",
            "caseDigest": json_digest(manifest),
            "unknowns": [],
            "capture": native.to_dict(),
            "status": "captured-ungraded",
            **inventories,
        }
        return CaseEvidence(
            CaseBundle(self.root, "CE14", manifest, ()),
            attempt,
            frozenset(before.keys() | after.keys()),
            self.root,
            (),
        )

    def test_retained_native_json_fence_and_final_message_grammar(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "scripts/agent_evals/graders/fixtures/claude-json-fence-retained.json"
        )
        fixture = json.loads(fixture_path.read_text())
        row = fixture["nativeRow"]
        text = row["message"]["content"][0]["text"]
        self.assertEqual(len(text), 4882)
        self.assertEqual(
            hashlib.sha256(text.encode()).hexdigest(),
            "9cfd3562c24dc8341dbfc27cb7bb3cb8623280ed28d2f4ae4c06e7f7f1c43c90",
        )
        self.assertEqual(fixture["responseSha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(
            fixture["sourceSha256"],
            "0e425cf0eb11f5dc88555ab3251922f40da399168cedca774862ae8906b49ea0",
        )
        rubric = (
            "The already supported proof and invariant remain valid.",
            "No product code change, invented test requirement or debt.",
            "No unnecessary rewrite/narrowing; harmless equivalent prose is not failed if support remains intact.",
            "The cited witness actually demonstrates the claimed effect.",
            "Preserve a valid claim; D627 is no reason to manufacture a tool problem or additional work.",
        )
        request = JudgeRequest(rubric, (), ())
        code = grade_case_code(self.case_evidence())
        path = self.root / "retained-native.jsonl"

        def capture_response(response: str):
            native = copy.deepcopy(row)
            native["message"]["content"][0]["text"] = response
            path.write_text(json.dumps(native) + "\n")
            return capture_claude_cli_jsonl([path], native["session_id"])

        captured = capture_response(text)
        verdict, parse_path = parse_native_verdict(captured)
        self.assertEqual(parse_path, "whole_response_json_fence")
        self.assertEqual([item["criterion"] for item in verdict["criteria"]], list(rubric))
        self.assertEqual(
            resolve_rubric(code, RubricSubmission(request, "gpt-6.1-sol", captured)), "right"
        )
        raw = text.removeprefix("```json\n").removesuffix("\n```")
        for response, expected in (
            (raw, "raw_json"),
            (" \n" + text + "\n\t", "whole_response_json_fence"),
            ("Here is the verdict:\n" + text, "final_message_json_fence"),
            (text + "\nThe verdict is complete.", "final_message_json_fence"),
            (text + "\n" + text, "invalid"),
            (text.replace("```json", "```JSON", 1), "invalid"),
            (text.replace("```json", "```", 1), "invalid"),
            ("```json\n[" + raw + "]\n```", "invalid"),
            ("[" + raw + "]", "invalid"),
            (raw + raw, "invalid"),
        ):
            with self.subTest(response=response[:35], expected=expected):
                captured = capture_response(response)
                parsed, actual = parse_native_verdict(captured)
                self.assertEqual(actual, expected)
                self.assertEqual(bool(parsed), expected != "invalid")
                result = resolve_rubric(code, RubricSubmission(request, "gpt-6.1-sol", captured))
                self.assertEqual(result, "missing_capture" if expected == "invalid" else "right")
        for field, value, error in (
            ("criterion", "Invented criterion", "omitted, reordered or invented"),
            ("outcome", "right", None),
            ("evidence", " ", None),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(verdict)
                changed["criteria"][0][field] = value
                captured = capture_response("```json\n" + json.dumps(changed) + "\n```")
                submission = RubricSubmission(request, "gpt-6.1-sol", captured)
                if error is None:
                    self.assertEqual(resolve_rubric(code, submission), "missing_capture")
                else:
                    with self.assertRaisesRegex(AuthorityError, error):
                        resolve_rubric(code, submission)

    def change_after(self, evidence: CaseEvidence, path: str, value: dict) -> None:
        write_object(self.root / "after" / path, value)
        (self.root / "after" / path).write_text(canonical_text(value))
        evidence.attempt["after"] = [
            item for item in evidence.attempt["after"] if item["path"] != path
        ]
        evidence.attempt["after"].append(EvidenceFile.capture(self.root / "after", path).to_dict())

    def remove_history(self, evidence: CaseEvidence) -> None:
        path = "projects/memory/knowledge/history/260928-MIK-L62.json"
        (self.root / "after" / path).unlink()
        evidence.attempt["after"] = [
            item for item in evidence.attempt["after"] if item["path"] != path
        ]

    def require_history(self, evidence: CaseEvidence) -> CaseEvidence:
        path = "expected.json"
        write_object(
            self.root / path,
            {
                "subject": "INV-7K3F9Q",
                "revision": 1,
                "disposition": "no_impact",
                "reasonMustName": "admission change",
            },
        )
        expected = EvidenceFile.capture(self.root, path)
        manifest = copy.deepcopy(evidence.case.manifest)
        manifest["setup"] = {"expected": path}
        manifest["specification"]["prospectiveGrade"]["semanticRubric"].append(
            "The no_impact reason names the admission change."
        )
        case = replace(evidence.case, manifest=manifest, files=(expected,))
        evidence.attempt["caseDigest"] = json_digest(manifest)
        return replace(evidence, case=case)

    def test_stated_history_outcome_remains_required_for_admission_only_change(self) -> None:
        evidence = self.require_history(self.history_case())
        path = "projects/memory/knowledge/history/260928-MIK-L62.json"
        raw = json.loads((self.root / "after" / path).read_text())
        raw["rows"][0]["reason"] = "Admission was reassessed; meaning and entries stay the same."
        self.change_after(evidence, path, raw)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "needs_rubric", grade)
        request = case_judge_request(evidence, ())
        self.assertIn("The no_impact reason names the admission change.", request.rubric)
        self.remove_history(evidence)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "fail", grade)
        self.assertTrue(any("stated outcome requires INV-7K3F9Q" in ref for ref in grade.evidence))

    def test_stated_history_outcome_rejects_wrong_row_and_unbound_expected_bytes(self) -> None:
        evidence = self.require_history(self.history_case())
        path = "projects/memory/knowledge/history/260928-MIK-L62.json"
        raw = json.loads((self.root / "after" / path).read_text())
        raw["rows"][0].update(disposition="changed", revision=2, effect="clarify")
        self.change_after(evidence, path, raw)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "fail", grade)
        self.assertTrue(any("actual row differs" in ref for ref in grade.evidence))
        (self.root / "expected.json").write_text("{}")
        self.assertEqual(grade_case_code(evidence).status, "unknown")

    def test_admission_only_record_change_needs_no_implicit_history_row(self) -> None:
        evidence = self.history_case()
        self.remove_history(evidence)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "needs_rubric", grade)

    def test_family_admission_only_change_needs_no_implicit_history_row(self) -> None:
        evidence = self.history_case(family=True)
        self.remove_history(evidence)
        path = "projects/memory/knowledge/families/FAM-2M8HXR-test.json"
        raw = json.loads((self.root / "before" / path).read_text())
        raw["admission"] = {
            "criteria": ["joint_guarantee"],
            "justification": "The existing members jointly retain the source.",
        }
        self.change_after(evidence, path, raw)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "needs_rubric", grade)

    def test_meaning_revision_and_status_changes_still_require_history(self) -> None:
        cases = (
            {"statement": "A result retains its entire source.", "revision": 2},
            {"revision": 2},
            {"status": "retired"},
            {"conditions": ["The source is available."]},
        )
        record = "projects/memory/knowledge/invariants/INV-7K3F9Q-test.json"
        for change in cases:
            with self.subTest(change=change):
                evidence = self.history_case()
                self.remove_history(evidence)
                raw = json.loads((self.root / "after" / record).read_text())
                raw.update(change)
                self.change_after(evidence, record, raw)
                grade = grade_case_code(evidence)
                self.assertEqual(grade.status, "fail", grade)
                self.assertTrue(
                    any("no captured judgment row" in ref for ref in grade.evidence), grade
                )

    def test_entry_and_family_changes_still_require_history(self) -> None:
        evidence = self.history_case()
        self.remove_history(evidence)
        card = "projects/memory/onboarding/source.py.json"
        raw = json.loads((self.root / "after" / card).read_text())
        raw["realizes"][0]["anchor"]["locator"]["name"] = "changed"
        self.change_after(evidence, card, raw)
        self.assertEqual(grade_case_code(evidence).status, "fail")
        evidence = self.history_case(family=True)
        self.remove_history(evidence)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "fail", grade)
        self.assertTrue(any("FAM-2M8HXR" in ref for ref in grade.evidence), grade)

    def test_admission_change_does_not_hide_an_explicit_stale_judgment(self) -> None:
        evidence = self.history_case()
        path = "projects/memory/knowledge/history/260928-MIK-L62.json"
        raw = json.loads((self.root / "after" / path).read_text())
        raw["rows"][0]["revision"] = 2
        self.change_after(evidence, path, raw)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "fail", grade)
        self.assertTrue(any("revision" in ref for ref in grade.evidence), grade)

    def test_case_history_checks_actual_invariant_rows_and_sidecars(self) -> None:
        evidence = self.history_case()
        self.assertEqual(grade_case_code(evidence).status, "needs_rubric")
        history = "projects/memory/knowledge/history/260928-MIK-L62.json"
        raw = json.loads((self.root / "after" / history).read_text())
        raw["rows"][0].update(disposition="changed", effect="clarify")
        self.change_after(evidence, history, raw)
        grade = grade_case_code(evidence)
        self.assertEqual(grade.status, "fail", grade)
        self.assertTrue(any("plus one" in ref for ref in grade.evidence))
        raw["rows"][0].update(disposition="no_impact")
        del raw["rows"][0]["effect"]
        self.change_after(evidence, history, raw)
        card = "projects/memory/onboarding/source.py.json"
        raw = json.loads((self.root / "after" / card).read_text())
        raw["realizes"][0]["anchor"]["locator"]["name"] = "another"
        self.change_after(evidence, card, raw)
        self.assertEqual(grade_case_code(evidence).status, "fail")

    def test_case_history_distinguishes_missing_capture_from_observed_deletion(self) -> None:
        evidence = self.history_case()
        record = evidence.attempt["before"][0]["path"]
        original = evidence.attempt["before"]
        evidence.attempt["before"] = [item for item in original if item["path"] != record]
        self.assertEqual(grade_case_code(evidence).status, "unknown")
        evidence.attempt["before"] = original
        evidence.attempt["after"] = [
            item for item in evidence.attempt["after"] if item["path"] != record
        ]
        (self.root / "after" / record).unlink()
        self.assertEqual(grade_case_code(evidence).status, "fail")

    def test_case_history_checks_actual_family_examined_revisions(self) -> None:
        evidence = self.history_case(family=True)
        self.assertEqual(grade_case_code(evidence).status, "needs_rubric")
        history = "projects/memory/knowledge/history/260928-MIK-L62.json"
        raw = json.loads((self.root / "after" / history).read_text())
        raw["rows"][0]["examined"][0]["revision"] = 2
        self.change_after(evidence, history, raw)
        self.assertEqual(grade_case_code(evidence).status, "fail")

    def test_case_history_keeps_closed_baseline_and_checks_new_owner_row(self) -> None:
        evidence = self.history_case()
        current = "projects/memory/knowledge/history/260928-MIK-L62.json"
        baseline = "projects/memory/knowledge/history/260928-MIK-L185.json"
        raw = json.loads((self.root / "after" / current).read_text())
        raw.update(leaf="260928-MIK-L185", closed=True)
        for phase in ("before", "after"):
            write_object(self.root / phase / baseline, raw)
            evidence.attempt[phase].append(
                EvidenceFile.capture(self.root / phase, baseline).to_dict()
            )
        self.assertEqual(grade_case_code(evidence).status, "needs_rubric")
        raw["rows"][0]["reason"] = "A rewritten closed row."
        self.change_after(evidence, baseline, raw)
        evidence = replace(evidence, admitted_paths=evidence.admitted_paths | {baseline})
        self.assertEqual(grade_case_code(evidence).status, "fail")

    def test_case_history_missing_bytes_are_unknown_but_malformed_candidate_fails(self) -> None:
        evidence = self.history_case()
        history = "projects/memory/knowledge/history/260928-MIK-L62.json"
        raw = json.loads((self.root / "after" / history).read_text())
        raw["rows"][0]["revision"] = "invalid revision"
        self.change_after(evidence, history, raw)
        self.assertEqual(grade_case_code(evidence).status, "fail")
        (self.root / "after" / history).unlink()
        self.assertEqual(grade_case_code(evidence).status, "unknown")
