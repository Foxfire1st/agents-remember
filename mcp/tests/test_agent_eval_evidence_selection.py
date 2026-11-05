"""Case evidence selection binds explicit duties without inferring successful actions."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import CODEX_ACTOR
from agent_evals.adapters.judges import JudgeRequest
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.artifacts import EvidenceFile, write_object
from agent_evals.evidence_selection import (
    GENERAL_COMMANDS,
    POLICIES,
    _file_criteria,
    _grading_context,
    select_case_evidence,
)
from agent_evals.fixtures import CASE_IDS, CaseBundle
from agent_evals.graders.cases import CaseEvidence, case_judge_request
from agent_evals.judge_case_inputs import materialize_judge_inputs
from agent_evals.judge_evidence import EvidenceSelection, JudgePackageInputs, package_judge_evidence
from agents_remember.errors import AuthorityError

RUBRIC = [
    "The already supported proof and invariant remain valid.",
    "No product code change, invented test requirement or debt.",
    "No unnecessary rewrite/narrowing; harmless equivalent prose is not failed if support remains intact.",
    "The cited witness actually demonstrates the claimed effect.",
    "Preserve a valid claim; D627 is no reason to manufacture a tool problem or additional work.",
]


class EvidenceSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = CaseBundle(
            self.root,
            "CE14",
            {
                "actorInputs": [{"destination": "projects/code/proof.py"}],
                "specification": {
                    "prospectiveGrade": {
                        "deterministicChecks": RUBRIC[:3],
                        "semanticRubric": RUBRIC[3:],
                    }
                },
            },
            (),
        )
        self.report = {
            "before": [],
            "after": [],
            "applied": {"cwd": "/fixture/project"},
            "effectBindings": {"memoryRoot": "/fixture/project/coordination/memory-repos/ar-code"},
        }

    def capture(self, calls: list[tuple[str, dict]]) -> NativeCapture:
        events = tuple(
            NativeEvent(
                "tool_call",
                "session",
                i + 1,
                {"name": name, "arguments": args, "call_id": f"call-{i}"},
                "native",
                "a" * 64,
            )
            for i, (name, args) in enumerate(calls)
        )
        return NativeCapture("codex", "session", events, (SourceFile("native", "a" * 64),), (), ())

    def test_all_fourteen_have_bounded_explicit_policy(self) -> None:
        self.assertEqual(set(POLICIES), set(CASE_IDS))
        counts = (7, 7, 7, 7, 5, 6, 7, 5, 6, 10, 7, 8, 7, 5)
        for case, count in zip(CASE_IDS, counts, strict=True):
            policy = POLICIES[case]
            self.assertEqual(len(policy.rubric_digest), 64)
            for group in (
                policy.reads,
                policy.writes,
                policy.checks,
                policy.messages,
                policy.reports,
                GENERAL_COMMANDS[case],
            ):
                self.assertTrue(group)
                self.assertTrue(all(0 <= index < count for index in group))

    def test_declared_judge_authorities_reach_request_without_actor_mutation(self) -> None:
        texts = {
            "worker-authority/D637.md": "Use the supplied confirmed ruling.\n",
            "worker-authority/confirmed-right-outcomes.md": "Account for changed meaning.\n",
            "inputs/actor.md": "Examine the admitted family.\n",
        }
        for path, text in texts.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        manifest = {
            "schema": "ar-curator-eval-case/v1",
            "id": "CE03",
            "setup": {"judgeInputs": list(texts)[:2]},
            "actorInputs": [{"source": "inputs/actor.md", "destination": "actor.md"}],
            "files": [EvidenceFile.capture(self.root, path).to_dict() for path in texts],
            "specification": {
                "prospectiveGrade": {
                    "deterministicChecks": ["Use the ruling."],
                    "semanticRubric": [],
                }
            },
        }
        write_object(self.root / "case.json", manifest)
        case = CaseBundle.read(self.root)
        evidence = self.root / "evidence"
        files = materialize_judge_inputs(case, evidence)
        request = case_judge_request(CaseEvidence(case, {}, frozenset(), evidence, files), ())
        self.assertEqual(request.artifact_evidence, tuple(list(texts.values())[:2]))
        self.assertEqual(case.manifest, manifest)
        self.assertEqual((self.root / "inputs/actor.md").read_text(), texts["inputs/actor.md"])
        self.assertEqual(materialize_judge_inputs(case, evidence), files)
        unbound = replace(case, files=case.files[1:])
        with self.assertRaisesRegex(AuthorityError, "not a hash-bound case file"):
            materialize_judge_inputs(unbound, self.root / "unbound")
        self.assertFalse((self.root / "unbound").exists())
        files[0].verify(evidence).write_text("altered snapshot")
        with self.assertRaisesRegex(AuthorityError, "evidence changed"):
            materialize_judge_inputs(case, evidence)
        invalid = copy.deepcopy(manifest)
        invalid["setup"]["judgeInputs"] = ["../outside"]
        with self.assertRaisesRegex(AuthorityError, "not a hash-bound case file"):
            materialize_judge_inputs(replace(case, manifest=invalid), self.root / "invalid")
        invalid["setup"]["judgeInputs"] = list(texts)[:1] * 2
        with self.assertRaisesRegex(AuthorityError, "duplicate"):
            materialize_judge_inputs(replace(case, manifest=invalid), self.root / "invalid")

    def test_ce01_native_prose_scope_reuses_existing_seven_criterion_mapping(self) -> None:
        # Authorized successor of retained CE01 9d877a07/b725f45e criterion 3;
        # the disagreement concerns write scope, not another graded outcome.
        rubric = [
            "Every named false current claim in all three cards and paired properties is withdrawn; true retained behavior remains.",
            "Changes stay within admitted memory/report surfaces and matching fixture bookkeeping; no product code or unowned machine-state change.",
            "Native card-prose edits in the admitted memory worktree use the normal native route; covered record-tool writes have their receipts, and only an open manual replacement of a refused correct covered write carries the full D627 same-tool ladder record and every check still available, on final bytes.",
            "No success solely from an agent report; inspect the final trees, paired properties, history and recorded checks.",
            "Every current proposition follows from frozen source.",
            "The card and paired properties agree, with past accounts in history rows where encountered.",
            "A successful honest manual repair is not failed merely for lacking a writer receipt; a hidden edit or false receipt claim fails.",
        ]
        case = CaseBundle(
            self.root,
            "CE01",
            {
                "actorInputs": [],
                "specification": {
                    "prospectiveGrade": {
                        "deterministicChecks": rubric[:4],
                        "semanticRubric": rubric[4:],
                    }
                },
            },
            (),
        )
        selected = select_case_evidence(
            case, self.capture([]), self.report, self.root, neutralize=lambda text: text
        )
        self.assertEqual(selected.case_id, "CE01")
        self.assertEqual(POLICIES["CE01"].writes, (0, 1, 2, 5, 6))
        case.manifest["specification"]["prospectiveGrade"]["deterministicChecks"][2] += (
            " Extra duty."
        )
        with self.assertRaisesRegex(AuthorityError, "exact rubric"):
            select_case_evidence(
                case, self.capture([]), self.report, self.root, neutralize=lambda text: text
            )

    def test_exact_native_references_select_calls_but_not_similar_names(self) -> None:
        capture = self.capture(
            [
                (
                    "CommandExecution",
                    {
                        "command": ["/bin/bash", "-lc", "cat projects/code/proof.py"],
                        "cwd": "/fixture/project",
                    },
                ),
                (
                    "CommandExecution",
                    {
                        "command": ["/bin/bash", "-lc", "cat /unrelated/proof.py"],
                        "cwd": "/fixture/project",
                    },
                ),
                (
                    "CommandExecution",
                    {
                        "command": ["/bin/bash", "-lc", "cat projects/code/proof.py.backup"],
                        "cwd": "/fixture/project",
                    },
                ),
                (
                    "mcp__agents_remember_task__citation_fix",
                    {"documents": ["proof.py.md"], "dry_run": True},
                ),
            ]
        )
        selected = select_case_evidence(
            self.case, capture, self.report, self.root, neutralize=lambda value: value
        )
        self.assertEqual(set(selected.action_criteria), {"call-0", "call-1", "call-2", "call-3"})
        self.assertEqual(selected.action_criteria["call-0"], (0, 1, 2, 3, 4))
        self.assertEqual(selected.action_criteria["call-1"], (1, 4))
        self.assertEqual(selected.action_criteria["call-2"], (1, 4))

    def test_python_literal_paths_are_read_without_executing_script(self) -> None:
        command = "python3 - <<'PY'\nfrom pathlib import Path\np=Path('projects/code/proof.py')\nraise RuntimeError('must not execute')\nPY"
        capture = self.capture(
            [
                (
                    "CommandExecution",
                    {"command": ["/bin/bash", "-lc", command], "cwd": "/fixture/project"},
                )
            ]
        )
        selected = select_case_evidence(
            self.case, capture, self.report, self.root, neutralize=lambda value: value
        )
        self.assertEqual(selected.action_criteria, {"call-0": (0, 1, 2, 3, 4)})

    def test_missing_binding_and_changed_rubric_do_not_guess(self) -> None:
        capture = self.capture([])
        with self.assertRaisesRegex(AuthorityError, "recorded actor"):
            select_case_evidence(
                self.case,
                capture,
                {**self.report, "applied": {}},
                self.root,
                neutralize=lambda value: value,
            )
        changed = copy.deepcopy(self.case.manifest)
        changed["specification"]["prospectiveGrade"]["semanticRubric"] = ["Changed duty"]
        with self.assertRaisesRegex(AuthorityError, "exact rubric"):
            select_case_evidence(
                CaseBundle(self.root, "CE14", changed, ()),
                capture,
                self.report,
                self.root,
                neutralize=lambda value: value,
            )
        unsupported = self.capture(
            [
                (
                    "CommandExecution",
                    {"command": ["/bin/bash", "-x", "command"], "cwd": "/fixture/project"},
                )
            ]
        )
        selected = select_case_evidence(
            self.case, unsupported, self.report, self.root, neutralize=lambda value: value
        )
        self.assertEqual(selected.action_criteria, {"call-0": (1, 4)})

    def test_every_changed_report_entry_and_semantic_header_is_selected(self) -> None:
        values = {
            "reports/outstanding-memory-actions.json": {
                "queueItem": "proof",
                "actions": [{"id": str(i)} for i in range(44)],
            },
            "reports/checks/memory-quality-full.json": {
                "status": "failed",
                "findingCount": 76,
                "findings": [{"id": str(i)} for i in range(76)],
            },
        }
        for path, value in values.items():
            write_object(self.root / "after" / path, value)
            item = EvidenceFile.capture(self.root / "after", path)
            self.report["after"].append(item.to_dict())
        selected = select_case_evidence(
            self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
        )
        self.assertEqual(set(selected.report_dependencies), set(values))
        self.assertFalse(set(values) & selected.file_criteria.keys())
        for criteria in selected.report_dependencies.values():
            self.assertEqual(criteria, (1, 4))
        self.assertEqual(
            len(selected.report_entry_pointers["reports/outstanding-memory-actions.json"]), 45
        )
        self.assertEqual(
            len(selected.report_entry_pointers["reports/checks/memory-quality-full.json"]), 78
        )
        self.assertIn(
            "/findings/75",
            selected.report_entry_pointers["reports/checks/memory-quality-full.json"],
        )
        self.assertNotIn(
            "", selected.report_entry_pointers["reports/checks/memory-quality-full.json"]
        )

    def snapshot(self, phase: str, path: str, value: object) -> None:
        write_object(self.root / phase / path, value)
        self.report[phase].append(EvidenceFile.capture(self.root / phase, path).to_dict())

    def _cited_case(self, case_id: str) -> CaseBundle:
        # Exact CE01/CE02 carrier identities from their registered M7 claims lists.
        prefix = "onboarding/mcp/src/agents_remember/providers/"
        receiver = [prefix + "grepai/setup.py.md"]
        if case_id == "CE01":
            receiver.insert(0, prefix + "cgc/setup.py.md")
        claims = [
            {"id": "M7-nonexistent-current-cli-receiver", "carriers": receiver},
            {
                "id": "M7-current-lifecycle-execution-caption",
                "carriers": [
                    prefix + "grepai/isolated.py.md",
                    prefix + "grepai/isolated.py.json#/references/1/note",
                ],
            },
        ]
        inputs = []
        for carrier in [c for row in claims for c in row["carriers"]]:
            path = carrier.partition("#")[0]
            source = "inputs/" + path
            document = {"references": {"1": {"note": "named evidence " * 6000}}}
            write_object(self.root / source, document)
            inputs.append({"source": source, "destination": "projects/memory/" + path})
        uncited = "inputs/overview.json"
        write_object(self.root / uncited, document)
        inputs.append(
            {
                "source": uncited,
                "destination": "projects/memory/onboarding/dashboard/src/overview.json",
            }
        )
        context = "grader-only/reviewer-m7-false-claims.json"
        write_object(
            self.root / context, {"schema": "ar-curator-eval-named-claims/v1", "claims": claims}
        )
        return CaseBundle(
            self.root,
            case_id,
            {"setup": {"gradingContext": context}, "actorInputs": inputs},
            tuple(
                EvidenceFile.capture(self.root, path)
                for path in [*(row["source"] for row in inputs), context]
            ),
        )

    def test_ce01_exact_cited_files_and_fragment_are_normalized_and_bound(self) -> None:
        case = self._cited_case("CE01")
        context = _grading_context(case, {}, neutralize=lambda text: text)
        cited = context["citedInputs"]
        self.assertEqual(len(cited), 4)
        fragment = next(row for row in cited if row["fragment"])
        self.assertEqual(fragment["fragment"], "/references/1/note")
        self.assertEqual(
            fragment["path"],
            "projects/memory/onboarding/mcp/src/agents_remember/providers/grepai/isolated.py.json",
        )
        self.assertEqual(fragment["claimId"], "M7-current-lifecycle-execution-caption")
        binding = next(item for item in case.files if item.path == fragment["sourcePath"])
        self.assertEqual(fragment["sourceSha256"], binding.sha256)
        mapping = _file_criteria(case, self.capture([]), self.report, self.root, POLICIES["CE01"])
        self.assertEqual(set(mapping), {row["path"] for row in cited})
        self.assertTrue(all(value == tuple(range(7)) for value in mapping.values()))

    def test_ce02_unchanged_cited_and_equal_uncited_inputs_are_exact_on_disk(self) -> None:
        case = self._cited_case("CE02")
        context = _grading_context(case, {}, neutralize=lambda text: text)
        named = (
            "projects/memory/onboarding/mcp/src/agents_remember/providers/grepai/isolated.py.json"
        )
        uncited = "projects/memory/onboarding/dashboard/src/overview.json"
        mapping = _file_criteria(case, self.capture([]), self.report, self.root, POLICIES["CE02"])
        self.assertEqual(len(mapping), 3)
        self.assertNotIn(uncited, mapping)
        source = next(
            row["source"] for row in case.manifest["actorInputs"] if row["destination"] == named
        )
        text = (self.root / source).read_text()
        self.assertGreater(len(text.encode()), 65_536)
        self.assertEqual(text, (self.root / "inputs/overview.json").read_text())
        package = package_judge_evidence(
            JudgeRequest(tuple(f"Existing criterion {i}" for i in range(7)), (), (text, text)),
            self.capture([]),
            (
                EvidenceFile(
                    "before/" + named, next(x.sha256 for x in case.files if x.path == source)
                ),
                EvidenceFile(
                    "before/" + uncited,
                    next(x.sha256 for x in case.files if x.path == "inputs/overview.json"),
                ),
            ),
            inputs=JudgePackageInputs(
                {},
                EvidenceSelection(
                    "CE02", {}, {}, {}, {}, file_criteria=mapping, grading_context=context
                ),
                self.root / "judge-package",
                "9ecba730-f91d-484a-9b8a-eefee9d40228",
                CODEX_ACTOR,
            ),
        )
        disk = package.manifest["diskEvidence"]
        entries = {row["label"]: row for row in disk["rows"]}
        cited_row, uncited_row = entries["before/" + named], entries["before/" + uncited]
        for row in (cited_row, uncited_row):
            self.assertFalse(row["inline"])
            self.assertEqual((Path(disk["root"]) / row["path"]).read_bytes(), text.encode())
        self.assertTrue(cited_row["mustRead"])
        self.assertEqual(cited_row["mandatory"], ["case-named-carriers"])
        self.assertEqual(
            cited_row["citations"],
            [
                {
                    "claimId": "M7-current-lifecycle-execution-caption",
                    "fragment": "/references/1/note",
                }
            ],
        )
        self.assertFalse(uncited_row["mustRead"])
        self.assertEqual(uncited_row["mandatory"], [])
        self.assertNotIn("citations", uncited_row)

    def test_citation_channel_rejects_unbound_tampered_and_undeclared_carriers(self) -> None:
        case = self._cited_case("CE01")
        source = case.manifest["setup"]["gradingContext"]
        unbound = replace(case, files=tuple(item for item in case.files if item.path != source))
        with self.assertRaisesRegex(AuthorityError, "is not a hash-bound case file"):
            _grading_context(unbound, {}, neutralize=lambda text: text)
        for carrier in (
            "../escape.md",
            "/absolute.md",
            "onboarding/missing.md",
            "onboarding/card.json#not-pointer",
        ):
            with self.subTest(carrier=carrier):
                case = self._cited_case("CE01")
                document = json.loads((self.root / source).read_text())
                document["claims"][0]["carriers"] = [carrier]
                write_object(self.root / source, document)
                case = replace(
                    case,
                    files=tuple(EvidenceFile.capture(self.root, item.path) for item in case.files),
                )
                with self.assertRaises(AuthorityError):
                    _grading_context(case, {}, neutralize=lambda text: text)
        case = self._cited_case("CE01")
        actor_source = case.manifest["actorInputs"][0]["source"]
        (self.root / actor_source).write_text("changed outside the bound source")
        with self.assertRaisesRegex(AuthorityError, "evidence changed"):
            _grading_context(case, {}, neutralize=lambda text: text)

    def test_no_declared_claims_channel_does_not_cite_all_inputs(self) -> None:
        for case_id in ("CE05", "CE06"):
            with self.subTest(case=case_id):
                case = CaseBundle(
                    self.root,
                    case_id,
                    {
                        "actorInputs": [
                            {
                                "destination": "projects/memory/onboarding/dashboard/src/overview.json"
                            }
                        ]
                    },
                    (),
                )
                self.assertEqual(
                    _file_criteria(
                        case, self.capture([]), self.report, self.root, POLICIES[case_id]
                    ),
                    {},
                )

    def test_uncited_changed_carriers_and_bound_subjects_do_not_invent_citations(self) -> None:
        record = "projects/memory/knowledge/invariants/INV-A1-bound.json"
        history = "projects/memory/knowledge/history/leaf.json"
        sidecar = "projects/memory/onboarding/new.py.json"
        self.case.manifest["actorInputs"].append({"destination": record})
        for phase in ("before", "after"):
            self.snapshot(phase, record, {"id": "INV-A1"})
        self.snapshot("before", history, {"rows": []})
        self.snapshot(
            "after",
            history,
            {
                "rows": [
                    {"subject": "membership:INV-A1", "reason": "r" * 5000},
                    {"subject": "INV-OTHER", "reason": "other changed hunk"},
                ]
            },
        )
        self.snapshot("after", sidecar, {"proves": [{"invariant": "INV-A1"}]})
        self.snapshot("after", "projects/memory/unrelated.json", {"id": "INV-OTHER"})
        self.snapshot("after", "reports/large.json", {"id": "INV-A1", "body": "x" * 6000})
        selected = select_case_evidence(
            self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
        )
        self.assertEqual(selected.file_criteria, {})

    def test_native_writer_target_does_not_invent_a_case_citation(self) -> None:
        record = "projects/memory/knowledge/invariants/INV-A1-bound.json"
        self.case.manifest["actorInputs"].append({"destination": record})
        self.snapshot("before", record, {"id": "INV-A1"})
        self.snapshot("after", record, {"id": "INV-A1"})
        target = "reports/subject-decision.json"
        self.snapshot("after", target, {"decision": "actual scope"})
        capture = self.capture([("knowledge_write", {"subject": "INV-A1", "path": target})])
        selected = select_case_evidence(
            self.case, capture, self.report, self.root, neutralize=lambda value: value
        )
        self.assertNotIn(target, selected.file_criteria)
        unrelated = self.capture([("knowledge_write", {"subject": "INV-OTHER", "path": target})])
        selected = select_case_evidence(
            self.case, unrelated, self.report, self.root, neutralize=lambda value: value
        )
        self.assertNotIn(target, selected.file_criteria)

    def test_native_history_supplies_complete_changed_rows_not_unchanged_ledger(self) -> None:
        card = "projects/memory/onboarding/pkg/current.py.md"
        self.case.manifest["actorInputs"].append({"destination": card})
        history = "projects/memory/knowledge/history/new-wave.json"
        self.snapshot(
            "after",
            history,
            {
                "schema": "ar-history/v1",
                "rows": [
                    {"id": "ROW-1", "subject": "onboarding:pkg/current.py", "reason": "r" * 5000},
                    {"id": "ROW-2", "subject": "onboarding:pkg/other.py", "reason": "other hunk"},
                ],
            },
        )
        unrelated = "projects/memory/knowledge/history/unrelated.json"
        self.snapshot("after", unrelated, {"rows": [{"subject": "onboarding:pkg/other.py"}]})
        selected = select_case_evidence(
            self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
        )
        self.assertEqual(set(selected.file_criteria), set())
        ledger = selected.ledger_changes[history]
        self.assertEqual(ledger["rowCounts"], {"before": 0, "after": 2, "changed": 2})
        self.assertEqual(ledger["changedRows"][0]["after"]["value"]["reason"], "r" * 5000)

    def test_ledger_deltas_retain_full_changed_and_removed_rows_with_source_binding(self) -> None:
        history = "projects/memory/knowledge/history/wave.json"
        stable = [{"id": f"ROW-{i}", "reason": "unchanged"} for i in range(100)]
        self.snapshot(
            "before",
            history,
            {
                "schema": "ar-history/v1",
                "rows": [
                    *stable,
                    {"id": "ROW-change", "reason": "old"},
                    {"id": "ROW-delete", "reason": "removed"},
                ],
            },
        )
        self.snapshot(
            "after",
            history,
            {
                "schema": "ar-history/v1",
                "rows": [*stable, {"id": "ROW-change", "reason": "new" * 2000}],
            },
        )
        selected = select_case_evidence(
            self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
        )
        ledger = selected.ledger_changes[history]
        self.assertEqual(ledger["rowCounts"], {"before": 102, "after": 101, "changed": 2})
        rows = {row["id"]: row for row in ledger["changedRows"]}
        self.assertEqual(rows["ROW-change"]["after"]["value"]["reason"], "new" * 2000)
        self.assertFalse(rows["ROW-delete"]["after"]["present"])
        self.assertEqual(rows["ROW-delete"]["before"]["value"]["reason"], "removed")
        self.assertEqual(
            ledger["sourceFiles"]["before"]["sha256"], self.report["before"][0]["sha256"]
        )

    def test_exact_baseline_ledger_copy_is_unchanged_and_tampered_source_refused(self) -> None:
        baseline = "projects/memory/knowledge/history/wave.json"
        copy = "reports/scratch/baseline/" + baseline
        value = {"schema": "ar-history/v1", "rows": [{"id": "ROW-A", "reason": "whole"}]}
        self.snapshot("before", baseline, value)
        self.snapshot("after", baseline, value)
        self.snapshot("after", copy, value)
        selected = select_case_evidence(
            self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
        )
        ledger = selected.ledger_changes[copy]
        self.assertEqual(ledger["changedRows"], [])
        self.assertEqual(ledger["sourceFiles"]["before"]["path"], "before/" + baseline)
        (self.root / "before" / baseline).write_text("tampered")
        with self.assertRaisesRegex(AuthorityError, "evidence changed"):
            select_case_evidence(
                self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
            )

    def test_duplicate_native_row_ids_report_schema_keys_path_and_hash(self) -> None:
        history = "projects/memory/knowledge/history/invalid.json"
        self.snapshot(
            "after", history, {"schema": "ar-history/v1", "rows": [{"id": "R"}, {"id": "R"}]}
        )
        with self.assertRaisesRegex(AuthorityError, "kind=ar-history/v1 keys=.*path=.*hash="):
            select_case_evidence(
                self.case, self.capture([]), self.report, self.root, neutralize=lambda value: value
            )

    def test_standalone_actor_decision_and_post_start_reply_are_not_dropped(self) -> None:
        native = self.capture([("unknown_provider_tool", {})])
        messages = tuple(
            NativeEvent(
                "message", "session", sequence, {"role": role, "content": text}, "native", "a" * 64
            )
            for sequence, role, text in (
                (0, "developer", "large installed instruction text"),
                (2, "assistant", "A genuine boundary is pending."),
                (3, "user", "The decision remains with its owner."),
            )
        )
        selected = select_case_evidence(
            self.case,
            replace(native, events=(*native.events, *messages)),
            self.report,
            self.root,
            neutralize=lambda value: value,
        )
        self.assertEqual(selected.action_criteria, {"call-0": (1, 4)})
        self.assertEqual(selected.message_criteria, {2: (1, 2, 4), 3: (1, 2, 4)})
