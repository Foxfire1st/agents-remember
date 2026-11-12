"""Fresh case copies, actor isolation and actual execution evidence."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import (
    CODEX_ACTOR,
    NativeBinding,
    NativeInvocation,
    execute_native,
)
from agent_evals.adapters.models import NativeCapture
from agent_evals.artifacts import EvidenceFile, read_object, write_object
from agent_evals.fixtures import (
    CONSTRUCTION_LABEL,
    SETUP_FILES,
    CaseBundle,
    release_fixture,
    reset_case,
)
from agent_evals.runner import TrialHooks, TrialPaths, TrialRequest, run_trial
from agents_remember.errors import AuthorityError


def make_case(root: Path, case_id: str = "CE01") -> CaseBundle:
    root.mkdir(parents=True)
    payload = root / "payload.txt"
    payload.write_text("initial\n")
    settings = root / "settings.json"
    settings.write_text("{}\n")
    setup = {name: name + ".txt" for name in SETUP_FILES}
    for name, file in setup.items():
        (root / file).write_text("test-only scripted " + name)
    write_object(
        root / "case.json",
        {
            "schema": "ar-curator-eval-case/v1",
            "id": case_id,
            "state": "admitted",
            "construction": {
                "kind": CONSTRUCTION_LABEL,
                "authority": "D637",
                "sourceEpisode": "test-only-fixture",
                "startingTrees": {"code": {"tree": "a" * 40}, "memory": {"tree": "b" * 40}},
            },
            "setup": setup,
            "specification": {
                "id": case_id,
                "prospectiveGrade": {
                    "expected": "Repair the test-only payload",
                    "deterministicChecks": ["Inspect the resulting actual payload"],
                    "semanticRubric": ["The result follows the given test-only task"],
                },
            },
            "effectRoots": ["projects", "reports"],
            "files": [
                EvidenceFile.capture(root, path.name).to_dict()
                for path in (payload, settings, *(root / name for name in setup.values()))
            ],
            "actorInputs": [
                {"source": "payload.txt", "destination": "projects/state.txt"},
                {"source": "settings.json", "destination": "config/settings.json"},
                *[
                    {"source": setup[name], "destination": "projects/" + setup[name]}
                    for name in ("prompt", "authority", "queue")
                ],
            ],
        },
    )
    return CaseBundle.read(root)


class AgentEvalRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_full_case_copy_is_fresh_and_separate_from_actor_inputs(self) -> None:
        case = make_case(self.root / "cases/CE01")
        first = reset_case(case, self.root / "trials")
        second = reset_case(case, self.root / "trials")
        self.addCleanup(release_fixture, first)
        self.addCleanup(release_fixture, second)
        self.assertEqual(CaseBundle.read(first.case_copy).manifest, case.manifest)
        self.assertEqual(
            read_object(first.actor_root / "agent-eval-fixture.json")["identity"], first.identity
        )
        answer = case.manifest["setup"]["expected"]
        self.assertTrue((first.case_copy / answer).is_file())
        self.assertFalse((first.actor_root / answer).exists())
        (first.case_copy / answer).write_text("isolated change")
        self.assertNotEqual(
            (first.case_copy / answer).read_bytes(), (second.case_copy / answer).read_bytes()
        )
        self.assertEqual(
            (second.case_copy / answer).read_bytes(), (case.path / answer).read_bytes()
        )

    def test_fresh_resets_reject_changed_inputs_and_reclaim_at_two_sizes(self) -> None:
        case = make_case(self.root / "case")
        fixtures = self.root / "trials"
        for size in (2, 9):
            with self.subTest(size=size):
                trials = [reset_case(case, fixtures) for _ in range(size)]
                self.assertEqual(len({trial.identity for trial in trials}), size)
                for trial in trials:
                    self.assertEqual(
                        (trial.actor_root / "projects/state.txt").read_text(), "initial\n"
                    )
                    (trial.actor_root / "projects/state.txt").write_text("actor wrote this")
                    release_fixture(trial)
                self.assertEqual(list(fixtures.iterdir()), [])
        (case.path / "payload.txt").write_text("unrecorded change")
        with self.assertRaisesRegex(AuthorityError, "evidence changed"):
            reset_case(case, fixtures)

    def test_full_copy_refuses_unbound_files_and_symlinks(self) -> None:
        case = make_case(self.root / "case")
        extra = case.path / "unbound.txt"
        extra.write_text("not in manifest")
        with self.assertRaisesRegex(AuthorityError, "undeclared"):
            reset_case(case, self.root / "trials")
        extra.unlink()
        extra.symlink_to(self.root / "outside")
        with self.assertRaises(AuthorityError):
            reset_case(case, self.root / "trials")
        self.assertFalse((self.root / "trials").exists())

    def test_old_recovery_state_cannot_admit_without_d637_construction(self) -> None:
        case = make_case(self.root / "case")
        value = dict(case.manifest)
        value["state"] = "unrecovered"
        value.pop("construction")
        write_object(case.path / "case.json", value)
        incomplete = CaseBundle.read(case.path)
        with self.assertRaisesRegex(AuthorityError, "not admitted under D637"):
            reset_case(incomplete, self.root / "trials")
        self.assertFalse((self.root / "trials").exists())

    def test_admitted_flag_cannot_hide_missing_setup_or_leak_grader_answers(self) -> None:
        case = make_case(self.root / "case")
        original = json.loads(json.dumps(case.manifest))
        for defect in ("missing-tree", "missing-setup", "missing-grade", "answer-leak"):
            with self.subTest(defect=defect):
                manifest = json.loads(json.dumps(original))
                if defect == "missing-tree":
                    del manifest["construction"]["startingTrees"]["memory"]
                elif defect == "missing-setup":
                    manifest["setup"]["authority"] = "unbound.txt"
                elif defect == "missing-grade":
                    del manifest["specification"]
                else:
                    manifest["actorInputs"].append(
                        {
                            "source": manifest["setup"]["expected"],
                            "destination": "projects/answers.txt",
                        }
                    )
                write_object(case.path / "case.json", manifest)
                with self.assertRaisesRegex(AuthorityError, "not admitted under D637"):
                    reset_case(CaseBundle.read(case.path), self.root / "trials")
                self.assertFalse((self.root / "trials").exists())

    def test_escaped_input_destination_cannot_touch_outside_fixture(self) -> None:
        case = make_case(self.root / "case")
        case.manifest["actorInputs"][0]["destination"] = "../../escaped"
        with self.assertRaises(AuthorityError):
            reset_case(case, self.root / "trials")
        self.assertFalse((self.root / "escaped").exists())
        self.assertEqual(list((self.root / "trials").iterdir()), [])

    def test_environment_is_local_and_drops_foreign_host_and_seat(self) -> None:
        fixture = reset_case(make_case(self.root / "case"), self.root / "trials")
        self.addCleanup(release_fixture, fixture)
        env = fixture.environment(
            {"PASEO_HOME": "/foreign", "AR_SPAWN_ROLE": "worker", "PATH": "/bin"}
        )
        self.assertEqual(env["PASEO_HOME"], str(fixture.root / "paseo"))
        self.assertNotIn("AR_SPAWN_ROLE", env)
        self.assertEqual(env["XDG_CONFIG_HOME"], str(fixture.root / "config"))
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["PWD"], str(fixture.actor_root))

    def test_trial_captures_actual_subprocess_and_effects_then_reclaims(self) -> None:
        case = make_case(self.root / "cases/CE01")

        def execute(fixture, prompt):
            binding = NativeBinding(
                fixture.actor_root / "projects",
                fixture.root / "paseo",
                fixture.actor_root / "config/settings.json",
                "fixture-build",
                ("agents-remember-task",),
                CODEX_ACTOR,
            )
            script = "from pathlib import Path; import sys; Path('state.txt').write_text(sys.stdin.read()); print('native-output')"
            invocation = NativeInvocation(
                (sys.executable, "-c", script),
                fixture.actor_root / "projects",
                fixture.environment({}),
                fixture.root,
                binding,
            )
            return execute_native(invocation, resolver=lambda _: binding, prompt=prompt)

        hooks = TrialHooks(
            prepare=lambda _: None,
            execute=execute,
            capture=lambda _: NativeCapture(
                "codex", "fixture-native-session", (), (), (), ("fixture capture gap",)
            ),
            host_count=lambda: 0,
            cleanup=lambda _: None,
        )
        output = run_trial(
            TrialRequest(
                case,
                TrialPaths(self.root / "trials", self.root / "runs"),
                "actual input",
            ),
            hooks,
        )
        report = read_object(output)
        self.assertEqual(CaseBundle.read(output.parent / "case").manifest, case.manifest)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["unknowns"], ["fixture capture gap"])
        self.assertIsNone(report["actorUsage"]["total_tokens"])
        self.assertEqual((output.parent / "before/projects/state.txt").read_text(), "initial\n")
        self.assertEqual((output.parent / "after/projects/state.txt").read_text(), "actual input")
        self.assertIn("native-output", (output.parent / "native.stdout").read_text())
        self.assertEqual(list((self.root / "trials").iterdir()), [])
