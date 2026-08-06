from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeGuard
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import check, crap_calculator

COMPLEXITY_RULES = ("C901", "PLR0911", "PLR0912", "PLR0915")

ARGUMENT_COUNT_RULE = "PLR0913"
# The one path exempt from PLR0913, spelled twice on purpose. The pattern is what
# `pyproject.toml` must say verbatim; the directory is what that pattern must resolve to.
# Widening the exemption has to defeat both, and `ToolSignatureExemptionTests` still walks
# whatever the pyproject pattern actually matches rather than what these constants claim.
TOOL_DECLARATION_DIRECTORY = "mcp/src/agents_remember/mcp/registration"
TOOL_DECLARATION_PATTERN = "mcp/src/agents_remember/mcp/registration/*.py"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = Path(__file__).resolve().parent
ENVIRONMENT_NAME = re.compile(r"\b(?:AR|AGENTS_REMEMBER)_[A-Z0-9_]+\b")
SKIP_DECORATORS = ("skipUnless", "skipIf", "skipif")


class CodeQualityCheckTests(unittest.TestCase):
    def test_quality_check_runs_fixed_suite_and_crap_calculator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            commands: list[list[str]] = []
            output: list[str] = []

            exit_code = check.run_quality_check(
                sample_config(root, source),
                runner=fake_runner(commands, root / "coverage.json"),
                printer=output.append,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                command_modules(commands),
                ["ruff", "ruff", "pyright", "radon", "radon", "pytest"],
            )
            pyright_command = commands[2]
            self.assertIn("--pythonpath", pyright_command)
            self.assertIn(sys.executable, pyright_command)
            self.assertIn(source.as_posix(), pyright_command)
            self.assertIn((root / "tests").as_posix(), commands[5])
            self.assertTrue(any("CRAP-Calculator" in line for line in output))
            # Both post-pytest rails score the one coverage report the run produced.
            self.assertTrue(any("## diff-coverage" in line for line in output))

    def test_quality_check_fails_when_a_fixed_step_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            coverage_json = root / "coverage.json"

            exit_code = check.run_quality_check(
                sample_config(root, source),
                runner=fake_runner([], coverage_json, failing_step="ruff"),
                printer=lambda message: None,
            )

            self.assertEqual(exit_code, 1)

    def test_quality_check_fails_when_coverage_json_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)

            exit_code = check.run_quality_check(
                sample_config(root, source, coverage_json=root / "missing-coverage.json"),
                runner=lambda name, command, cwd, env: check.StepResult(name, 0, command),
                printer=lambda message: None,
            )

            self.assertEqual(exit_code, 1)

    def test_crap_threshold_fails_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            coverage_json = root / "coverage.json"

            exit_code = check.run_quality_check(
                sample_config(root, source, threshold=1.0),
                runner=fake_runner([], coverage_json),
                printer=lambda message: None,
            )

            self.assertEqual(exit_code, 1)

    def test_cli_has_no_report_only_or_strict_opt_in_mode(self) -> None:
        help_text = unwrapped_help()

        self.assertNotIn("report-only", help_text)
        self.assertNotIn("fail-on-crap-threshold", help_text)
        self.assertIn("mandatory CRAP threshold enforcement", help_text)

    def test_repository_gates_use_default_strict_wrapper(self) -> None:
        # The git hooks no longer inline the wrapper command: both delegate to the
        # shared tiered body, and the pre-push tier runs the change-set-scoped wrapper.
        # Follow the
        # indirection rather than dropping the assertion -- every repository gate must
        # still reach the wrapper with no threshold opt-out.
        gate_files = [
            REPOSITORY_ROOT / ".githooks" / "_gate.sh",
            REPOSITORY_ROOT / ".github" / "workflows" / "quality-checks.yml",
        ]

        for gate_file in gate_files:
            content = gate_file.read_text(encoding="utf-8")
            with self.subTest(gate_file=gate_file):
                self.assertIn("agents_remember.code_quality.check", content)
                self.assertNotIn("fail-on-crap-threshold", content)

    def test_git_hooks_delegate_to_the_shared_tiered_gate(self) -> None:
        hook_tiers = {"pre-commit": "fast", "pre-push": "targeted"}

        for hook_name, tier in hook_tiers.items():
            hook = REPOSITORY_ROOT / ".githooks" / hook_name
            with self.subTest(hook=hook_name):
                content = hook.read_text(encoding="utf-8")
                self.assertIn(f'exec "$hook_dir/_gate.sh" {tier}', content)
                self.assertNotIn("fail-on-crap-threshold", content)

    def test_the_pre_push_tier_runs_the_targeted_contract(self) -> None:
        gate = (REPOSITORY_ROOT / ".githooks" / "_gate.sh").read_text(encoding="utf-8")

        self.assertIn("run_targeted_checks", gate)
        self.assertIn("code_quality.check --targeted", gate)
        # The full wrapper stays available only as a manual tier; the ladder moves it
        # to the master integration gate.
        self.assertIn("usage: _gate.sh <fast|targeted|full>", gate)

    def test_run_fixed_checks_threads_checkout_source_onto_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            seen_env: list[Mapping[str, str]] = []

            def runner(
                name: str, command: list[str], cwd: Path, env: Mapping[str, str]
            ) -> check.StepResult:
                seen_env.append(env)
                return check.StepResult(name, 0, command)

            with mock.patch.dict(os.environ, {"PYTHONPATH": "/pre-existing"}):
                check.run_fixed_checks(
                    sample_config(root, source),
                    root / "coverage.json",
                    runner=runner,
                    printer=lambda message: None,
                )

            self.assertTrue(seen_env)
            entries = seen_env[0]["PYTHONPATH"].split(os.pathsep)
            # The checkout's own source import root comes first; a pre-existing
            # PYTHONPATH is preserved at the end.
            self.assertEqual(entries[0], str(source.resolve().parent))
            self.assertEqual(entries[-1], "/pre-existing")


class RadonIsAReportNotAGateTests(unittest.TestCase):
    """Radon exits 0 whatever it finds, so it never could fail this gate.

    The wrapper used to list it alongside the checks that can. These tests hold the
    correction in place: the two Radon steps are labelled reports, their findings are
    incapable of failing the run, and the help text no longer claims otherwise.
    """

    def test_radon_steps_are_declared_reports_and_the_rest_enforce(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)

            steps = check.quality_steps(sample_config(root, source), root / "coverage.json")

            reporting = {step.name for step in steps if not step.enforcing}
            enforcing = {step.name for step in steps if step.enforcing}
            self.assertEqual(reporting, {"radon-cc", "radon-mi"})
            self.assertEqual(enforcing, {"ruff", "ruff-format", "pyright", "pytest"})

    def test_report_section_header_says_it_cannot_fail(self) -> None:
        enforcing = check.step_header(check.Step("ruff", ["ruff"]))
        reporting = check.step_header(
            check.Step("radon-cc", ["radon"], report_note=check.RADON_REPORT_NOTE)
        )

        self.assertEqual(enforcing, "\n## ruff")
        self.assertIn("report only", reporting)
        self.assertIn("fail the gate", reporting)

    def test_help_text_does_not_present_radon_as_enforcement(self) -> None:
        help_text = unwrapped_help()

        self.assertIn("Radon", help_text)
        self.assertIn("report only", help_text)
        # The old description read "Ruff, Pyright, Radon, pytest coverage, and mandatory
        # CRAP threshold enforcement", which put Radon inside the enforcing list.
        self.assertNotIn("Pyright, Radon", help_text)

    def test_a_report_step_that_breaks_still_fails_the_gate(self) -> None:
        # A non-zero exit from a tool that exits 0 on every finding means the tool
        # itself is broken. Swallowing that would be a silent skip.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            output: list[str] = []

            failures = check.run_fixed_checks(
                sample_config(root, source),
                root / "coverage.json",
                runner=fake_runner([], root / "coverage.json", failing_step="radon-cc"),
                printer=output.append,
            )

            self.assertEqual(failures, 1)
            self.assertTrue(any("radon-cc could not run" in line for line in output))


class EveryEnforcingStepCanFailTests(unittest.TestCase):
    """The two steps this leaf added, and the complexity rules at full strength.

    `ruff format` and the complexity rules were both configured-but-unenforced before
    260731-EFA-L2: the formatter ran in no gate at all, and `max-complexity = 10` was set
    while `C901` was unselected. Arming them produced 67 complexity offenders, which were
    first parked behind a shrink-only baseline and then -- on the developer's correction --
    refactored outright. These assertions hold the wiring at full strength: the `ruff` step
    routes no rule away from itself, the four codes are selected and unignored, and a real
    Ruff run at this repository's configuration rejects an over-complex function.
    """

    def test_the_ruff_step_routes_no_rule_away_from_itself(self) -> None:
        # This step used to carry `--extend-ignore C901,PLR0911,PLR0912,PLR0915` so a
        # separate baseline step could own those four. Any narrowing flag here is a rule
        # the gate silently stops enforcing, so the command is asserted whole rather than
        # by the absence of one spelling.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)

            command = quality_steps_by_name(root)["ruff"].command

            self.assertEqual(command, [sys.executable, "-m", "ruff", "check", source.as_posix()])

    def test_the_complexity_rules_are_selected_and_nothing_ignores_them(self) -> None:
        lint = ruff_lint_configuration()
        selected = set(lint.get("select", []))
        ignored = set(lint.get("ignore", []))
        per_file: dict[str, list[str]] = lint.get("per-file-ignores", {})

        # `PL` selects the PLR09xx family; `C901` is named on its own line.
        self.assertIn("C901", selected)
        self.assertIn("PL", selected)
        for rule in COMPLEXITY_RULES:
            with self.subTest(rule=rule):
                self.assertNotIn(rule, ignored)
                for pattern, codes in per_file.items():
                    self.assertNotIn(rule, codes, f"{pattern} exempts {rule}")

    def test_ruff_rejects_an_over_complex_function_at_this_repository_configuration(
        self,
    ) -> None:
        # The rules biting is the whole point of removing the baseline, so it is proved
        # against the real configuration rather than inferred from the flag list above.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "over_complex.py"
            source.write_text(over_complex_function(), encoding="utf-8")

            completed = run_ruff_with_repository_configuration(source)

            self.assertEqual(completed.returncode, 1, completed.stdout)
            reported = {entry["code"] for entry in json.loads(completed.stdout)}
            self.assertEqual(reported & set(COMPLEXITY_RULES), set(COMPLEXITY_RULES))

    def test_no_suppression_directive_in_the_tree_holds_a_complexity_rule_down(self) -> None:
        # A line-level suppression naming one of these codes is a per-function baseline
        # with a shorter name, and it is the one exemption the gate itself cannot see:
        # Ruff honours the directive, so the wrapper goes green either way.
        # `--ignore-noqa` asks the question the gate cannot -- if the tree is still clean
        # with every directive disregarded, no directive is holding a rule down.
        #
        # Asserted against Ruff rather than by reading the source text. A comment that
        # merely discusses a suppression is indistinguishable from a real one to a grep,
        # and writing the directive's own spelling here to say so would make Ruff parse
        # this very comment as one.
        completed = run_ruff_over_tracked_python(
            "--ignore-noqa", "--select", ",".join(COMPLEXITY_RULES)
        )

        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_ruff_format_is_checked_over_the_whole_derived_scope(self) -> None:
        # `ruff format --check` was in no gate before this leaf, and 206 files in the
        # wrapper's scope failed it. The debt was paid outright, so this step needs no
        # baseline -- but it does need the same tree-derived scope as the lint rail,
        # because 10 of the reformatted files were outside the old hand-written constant.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_sample_source(root)
            steps = quality_steps_by_name(root)
            step = steps["ruff-format"]

            self.assertTrue(step.enforcing)
            self.assertEqual(step.command[2:5], ["ruff", "format", "--check"])
            self.assertIn(source.as_posix(), step.command)
            self.assertEqual(
                [argument for argument in step.command if argument.endswith(".py")],
                [argument for argument in steps["ruff"].command if argument.endswith(".py")],
            )

    def test_the_complexity_baseline_and_its_gate_step_are_gone(self) -> None:
        # The ratchet is not merely emptied, it is deleted: an empty exemption list is a
        # place to put the next offender. The module, its file and the wrapper step that
        # ran it all go together, and the two local gates lose the routing that fed it.
        self.assertFalse((REPOSITORY_ROOT / "quality").exists())
        self.assertFalse(
            (MCP_SRC / "agents_remember" / "code_quality" / "complexity_baseline.py").exists()
        )
        for gate_file in (
            REPOSITORY_ROOT / ".githooks" / "_gate.sh",
            REPOSITORY_ROOT / ".github" / "workflows" / "quality-checks.yml",
        ):
            with self.subTest(gate_file=gate_file.name):
                self.assertNotIn("complexity_baseline", gate_file.read_text(encoding="utf-8"))
        # The fast tier's lint invocation, asserted as the command it runs rather than as
        # the absence of a spelling: the comment above it names `--extend-ignore` to say
        # why it is gone, and a substring search cannot tell that from the flag itself.
        self.assertEqual(
            shell_command_lines(REPOSITORY_ROOT / ".githooks" / "_gate.sh", "-m ruff check"),
            ['if over_tracked_python "$py" -m ruff check; then'],
        )


class ToolSignatureExemptionTests(unittest.TestCase):
    """PLR0913's one exemption covers published MCP tool declarations and nothing else.

    ``mcp/src/agents_remember/mcp/registration/`` is exempt because FastMCP derives each
    tool's published JSON input schema from the Python signature, so collapsing a parameter
    list into an object is a breaking wire change rather than a refactor. That reason holds
    only for `@server.tool()` declarations. These tests are what stops the exemption from
    becoming a place to park ordinary code: the moment a plain function appears under that
    path, or a second path is exempted, or the pattern is widened, one of them fails.
    """

    def test_plr0913_is_armed_and_nothing_globally_ignores_it(self) -> None:
        lint = ruff_lint_configuration()

        # `PL` selects the PLR09xx family, PLR0913 included.
        self.assertIn("PL", set(lint.get("select", [])))
        self.assertNotIn(ARGUMENT_COUNT_RULE, set(lint.get("ignore", [])))
        # Ruff's default max-args is 5, which is the number the memory root's
        # system/coding-guidelines.md states ("Function arguments | <= 5 normal args").
        # Configuring the knob at all can only weaken the rule to the size of whatever
        # offender prompted the edit, so its absence is asserted rather than its value.
        self.assertNotIn("max-args", lint.get("pylint", {}))

    def test_the_registration_modules_are_the_only_path_exempt_from_plr0913(self) -> None:
        # The pattern is asserted verbatim rather than "some pattern ending in
        # registration". Widening it -- to the package above, to `*.py`, to a second
        # directory -- is exactly the failure this exists to catch, and a widened pattern
        # still satisfies any assertion loose enough to describe it.
        exempted = {
            pattern
            for pattern, codes in ruff_lint_configuration().get("per-file-ignores", {}).items()
            if ARGUMENT_COUNT_RULE in codes
        }

        self.assertEqual(exempted, {TOOL_DECLARATION_PATTERN})

    def test_every_function_in_the_exempted_path_is_a_published_tool_declaration(self) -> None:
        # Read through the pattern, not through a second hand-written path: a pattern that
        # reaches further immediately drags more files into this walk, so widening the
        # exemption fails here too and not only in the assertion above.
        modules = exempted_tool_modules()
        self.assertEqual(
            modules,
            sorted((REPOSITORY_ROOT / TOOL_DECLARATION_DIRECTORY).glob("*.py")),
            "the exemption pattern no longer matches exactly the tool declaration modules",
        )

        for module in modules:
            with self.subTest(module=module.name):
                self.assertEqual(ordinary_code_in_tool_module(module), [])

    def test_no_suppression_directive_in_the_tree_holds_an_argument_count_finding_down(
        self,
    ) -> None:
        # The path exemption is visible in `pyproject.toml` and is held to one category by
        # the tests above. A line-level suppression is neither: Ruff honours it, so the gate
        # goes green whatever it covers. `--ignore-noqa` asks the question the gate cannot.
        completed = run_ruff_over_tracked_python("--ignore-noqa", "--select", ARGUMENT_COUNT_RULE)

        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_ruff_rejects_a_seven_parameter_function_at_this_repository_configuration(
        self,
    ) -> None:
        # Proved against the real configuration rather than inferred from the flag list.
        # A rule can be selected and still not bite -- an ignore entry, a `max-args`
        # override or a per-file pattern that reaches too far all read as "armed" to
        # anything that only inspects `select`.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "seven_parameters.py"
            source.write_text(seven_parameter_function(), encoding="utf-8")

            completed = run_ruff_with_repository_configuration(source)

            self.assertEqual(completed.returncode, 1, completed.stdout)
            reported = {entry["code"] for entry in json.loads(completed.stdout)}
            self.assertIn(ARGUMENT_COUNT_RULE, reported)


class CrapThresholdEnforcementTests(unittest.TestCase):
    """CRAP is enforced by the threshold alone -- there is no exemption list beside it."""

    def test_a_failing_gate_names_every_offender_not_only_the_reported_top(self) -> None:
        # The rendered table is capped at `--top`. The failure list is the work, so it is
        # not: a truncated finding list sends the reader off to re-run the tool by hand.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_many_branchy_functions(root, count=8)
            output: list[str] = []

            failures = check.run_crap_calculator(
                sample_config(root, source, threshold=5.0, top=2),
                root / "coverage.json",
                root,
                printer=output.append,
            )

            text = "\n".join(output)
            self.assertEqual(failures, 1)
            self.assertIn("8 function(s) meet or exceed the CRAP threshold", text)
            for index in range(8):
                self.assertIn(f"branchy_{index}", text)

    def test_an_offender_is_told_the_branch_coverage_that_would_clear_it(self) -> None:
        score = crap_calculator.FunctionScore(
            path=Path("pkg/sample.py"),
            function="f",
            kind="function",
            start_line=12,
            end_line=30,
            complexity=10,
            covered_lines=1,
            missing_lines=1,
            executable_lines=2,
            covered_branches=1,
            missing_branches=9,
            coverage_ratio=0.2,
            crap=crap_calculator.crap_score(10, 0.2),
        )

        line = check.crap_failure_line(score, Path("."), 25.0)

        self.assertIn("pkg/sample.py:12", line)
        # crap(10, c) < 25 needs (1 - c)**3 < 0.15, i.e. coverage above 46.9%.
        self.assertIn("above 46.9%", line)

    def test_an_offender_that_no_test_can_clear_is_told_to_split_instead(self) -> None:
        score = crap_calculator.FunctionScore(
            path=Path("pkg/sample.py"),
            function="f",
            kind="function",
            start_line=12,
            end_line=30,
            complexity=26,
            covered_lines=1,
            missing_lines=0,
            executable_lines=1,
            covered_branches=1,
            missing_branches=0,
            coverage_ratio=1.0,
            crap=26.0,
        )

        line = check.crap_failure_line(score, Path("."), 25.0)

        self.assertIn("cannot clear 25.0 at any coverage", line)
        self.assertIn("split it", line)

    def test_the_clearing_coverage_inverts_the_crap_formula(self) -> None:
        for complexity, threshold in ((6, 30.0), (10, 25.0), (15, 22.0)):
            with self.subTest(complexity=complexity, threshold=threshold):
                clearing = crap_calculator.coverage_clearing(complexity, threshold)
                assert clearing is not None
                self.assertAlmostEqual(crap_calculator.crap_score(complexity, clearing), threshold)
        self.assertIsNone(crap_calculator.coverage_clearing(30, 30.0))

    def test_no_repository_gate_carries_a_crap_exemption_file(self) -> None:
        # The threshold is the whole policy. A baseline, allowlist or ignore file beside it
        # would be an exemption the gate could not see past, which is the failure mode the
        # CRAP rail exists to avoid. `quality/` is where such a file would live -- it held
        # the complexity baseline until that was deleted -- so its absence is the assertion,
        # not a glob inside a directory that no longer exists.
        self.assertFalse((REPOSITORY_ROOT / "quality").exists())
        self.assertEqual(sorted(REPOSITORY_ROOT.glob("*crap*")), [])
        self.assertEqual(
            [
                path
                for path in check.git_ls_files(REPOSITORY_ROOT, "*")
                if "crap" in path.name.casefold() and path.suffix != ".py"
            ],
            [],
        )


class GateScopeDerivationTests(unittest.TestCase):
    def test_module_declares_no_hand_written_scope_constant(self) -> None:
        source = (MCP_SRC / "agents_remember" / "code_quality" / "check.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("DEFAULT_SOURCE_PATHS", source)
        self.assertNotIn("DEFAULT_TEST_PATHS", source)

    def test_scope_derived_from_this_checkout_reaches_the_whole_tree(self) -> None:
        scope = check.derive_scope(REPOSITORY_ROOT)

        self.assertGreater(len(scope.lint_paths), 500)
        self.assertEqual(scope.lint_paths, scope.type_paths)
        self.assertEqual(scope.coverage_paths, [Path("mcp/src/agents_remember")])
        self.assertEqual(scope.test_paths, [Path("mcp/tests")])

    def test_a_script_outside_every_package_reaches_ruff_and_pyright(self) -> None:
        # The case the old hand-written constants missed for 1,882 lines: a script that
        # is neither inside the source package nor inside the test tree. Nobody has to
        # remember to add it -- the derivation finds it because git tracks it, and the
        # assertion is made on the argument vectors the tools actually receive.
        with tempfile.TemporaryDirectory() as tmp:
            root = write_sample_repository(Path(tmp))

            scope = check.derive_scope(root)
            steps = {
                step.name: step
                for step in check.quality_steps(
                    check.CheckConfig(
                        project_root=root,
                        scope=scope,
                        coverage_json=None,
                        threshold=30.0,
                        top=5,
                    ),
                    root / "coverage.json",
                )
            }

            self.assertIn(Path("scripts/sync.py"), scope.lint_paths)
            self.assertIn("scripts/sync.py", steps["ruff"].command)
            self.assertIn("scripts/sync.py", steps["pyright"].command)
            self.assertEqual(scope.coverage_paths, [Path("pkg")])
            self.assertIn("--cov=pkg", steps["pytest"].command)

    def test_scope_is_the_index_so_an_unadded_file_is_not_yet_part_of_the_tree(self) -> None:
        # `git ls-files` reads the index, so `git add`-ing a file puts it in scope
        # immediately -- which is what the pre-commit tier certifies. A file that has
        # never been added is not part of the tree yet, and this records that boundary
        # rather than leaving it to be discovered.
        with tempfile.TemporaryDirectory() as tmp:
            root = write_sample_repository(Path(tmp))
            (root / "scripts" / "scratch.py").write_text("value = 2\n", encoding="utf-8")

            self.assertNotIn(Path("scripts/scratch.py"), check.derive_scope(root).lint_paths)

            run_git(root, "add", "scripts/scratch.py")

            self.assertIn(Path("scripts/scratch.py"), check.derive_scope(root).lint_paths)

    def test_top_level_packages_ignores_nested_packages(self) -> None:
        tracked = [
            Path("mcp/src/agents_remember/__init__.py"),
            Path("mcp/src/agents_remember/kernel/__init__.py"),
            Path("scripts/sync-skills.py"),
        ]

        self.assertEqual(check.top_level_packages(tracked), [Path("mcp/src/agents_remember")])

    def test_missing_testpaths_is_an_error_rather_than_a_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")

            with self.assertRaises(check.ScopeError) as raised:
                check.pytest_testpaths(root)

            self.assertIn("testpaths", str(raised.exception))

    def test_a_project_with_no_pyproject_at_all_cannot_derive_where_the_suite_lives(self) -> None:
        # The neighbouring case to a missing `testpaths` key: there is no file to read it
        # from. Both must refuse, because the fallback a reader might expect -- "just run
        # everything" or "run nothing" -- is how a gate reports success for an empty run.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(check.ScopeError) as raised:
                check.pytest_testpaths(root)

            self.assertIn("no pyproject.toml at", str(raised.exception))
            self.assertIn((root / "pyproject.toml").as_posix(), str(raised.exception))

    def test_a_pytest_table_that_is_not_a_table_reads_as_absent_rather_than_crashing(self) -> None:
        # `[tool.pytest] = "..."` is a typo a person makes, not a shape TOML forbids. Walking
        # into a scalar must yield "no such section" so the missing-testpaths refusal fires,
        # rather than an AttributeError from the wrapper's own scope derivation.
        data: Mapping[str, object] = {"tool": {"pytest": "tests"}}

        self.assertEqual(check.toml_section(data, ("tool", "pytest", "ini_options")), {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text('[tool]\npytest = "tests"\n', encoding="utf-8")

            with self.assertRaises(check.ScopeError) as raised:
                check.pytest_testpaths(root)

            self.assertIn("testpaths is missing or empty", str(raised.exception))

    def test_a_repository_tracking_no_python_is_refused_instead_of_scoped_to_nothing(self) -> None:
        # An empty scope would make ruff, pyright and pytest each run over zero paths and
        # exit 0, so the gate would pass by measuring nothing at all.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_git(root, "init", "--quiet")
            (root / "README.md").write_text("no code here\n", encoding="utf-8")
            run_git(root, "add", "-A")

            with self.assertRaises(check.ScopeError) as raised:
                check.derive_scope(root)

            self.assertIn("git tracks no Python files", str(raised.exception))

    def test_python_that_belongs_to_no_package_leaves_coverage_nothing_to_measure(self) -> None:
        # Tracked Python, but no directory holding `__init__.py`: lint and type-check have
        # paths, `--cov=` would have none. Coverage over an empty set reports 100% of
        # nothing, and the CRAP step scores an empty report, so this refuses instead.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_git(root, "init", "--quiet")
            (root / "pyproject.toml").write_text(
                '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
            )
            (root / "sync.py").write_text("value = 1\n", encoding="utf-8")
            run_git(root, "add", "-A")

            self.assertEqual(check.top_level_packages([Path("sync.py")]), [])

            with self.assertRaises(check.ScopeError) as raised:
                check.derive_scope(root)

            self.assertIn("no tracked top-level Python package", str(raised.exception))
            self.assertIn("coverage and CRAP would have nothing to measure", str(raised.exception))

    def test_scope_failure_exits_non_zero_with_an_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output: list[str] = []
            with mock.patch.object(check, "print_line", output.append):
                exit_code = check.main(["--project-root", tmp])

            self.assertEqual(exit_code, 1)
            self.assertTrue(any("gate scope could not be derived" in line for line in output))

    def test_a_derivable_scope_runs_the_gate_and_main_reports_its_verdict(self) -> None:
        """``main`` owns no verdict of its own: it derives the scope, then hands back
        whatever the gate decided. The scope it hands over is the one derived from the
        project root on the command line, and the threshold and diff base are the parsed
        arguments -- not defaults re-invented here."""
        handed: list[check.CheckConfig] = []

        def gate(config: check.CheckConfig) -> int:
            handed.append(config)
            return 7

        with tempfile.TemporaryDirectory() as tmp:
            root = write_sample_repository(Path(tmp))
            with mock.patch.object(check, "run_quality_check", gate):
                exit_code = check.main(
                    [
                        "--project-root",
                        str(root),
                        "--threshold",
                        "12.5",
                        "--diff-base",
                        "HEAD~1",
                    ]
                )

        self.assertEqual(exit_code, 7)
        [config] = handed
        self.assertEqual(config.project_root, root.resolve())
        self.assertEqual(config.threshold, 12.5)
        self.assertEqual(config.diff_base, "HEAD~1")
        self.assertEqual(config.scope.coverage_paths, [Path("pkg")])
        self.assertEqual(config.scope.test_paths, [Path("tests")])
        self.assertIn(Path("scripts/sync.py"), config.scope.lint_paths)


class PytestConfigurationTests(unittest.TestCase):
    """The pytest configuration this repository had none of until 260731-EFA-L2."""

    def test_strictness_switches_are_on(self) -> None:
        self.assertIn("--strict-markers", ini_strings("addopts"))
        self.assertIn("--strict-config", ini_strings("addopts"))
        self.assertIs(pytest_ini_options()["xfail_strict"], True)
        self.assertEqual(ini_strings("testpaths"), ["mcp/tests"])

    def test_python_classes_covers_the_house_naming_convention(self) -> None:
        # `<Subject>Tests` is what this suite actually writes; pytest's default only
        # matches `Test<Subject>`. Every such class reaches unittest.TestCase today, so
        # this is prospective -- a plain `PlainTests` class would be silently skipped.
        self.assertEqual(ini_strings("python_classes"), ["Test*", "*Tests"])

    def test_filterwarnings_errors_by_default(self) -> None:
        entries = ini_strings("filterwarnings")

        self.assertEqual(entries[0], "error")
        for entry in entries[1:]:
            with self.subTest(entry=entry):
                self.assertTrue(entry.startswith("ignore"))

    def test_the_warning_ignore_list_is_capped(self) -> None:
        # `error` first means any warning not named below fails the suite, so the list
        # cannot grow silently. This cap is the other half: it makes *shrinking* the
        # list a required edit rather than an optional one. An exact count, not a
        # ceiling -- paying one entry off forces the number down in the same commit.
        # Ignore entries at 2026-07-31: 3, all third party. The two that were ours were
        # ResourceWarnings for a subprocess pipe and a TemporaryDirectory finalised by GC
        # instead of closed; both leaks are fixed, so both entries are gone. What is left
        # is a starlette testclient notice and two websockets deprecations reached through
        # uvicorn: no action exists here, and they go when uvicorn moves off the deprecated
        # websockets API.
        ignores = [entry for entry in ini_strings("filterwarnings") if entry.startswith("ignore")]

        self.assertEqual(len(ignores), 3)

    def test_registered_markers_and_the_suite_environment_gates_agree(self) -> None:
        # Registering a marker for a gate that no longer exists leaves a stale line;
        # adding a gate without a marker leaves a path nothing can select. Both are
        # failures, so the registry cannot drift in either direction.
        registered = {
            name for marker in ini_strings("markers") for name in ENVIRONMENT_NAME.findall(marker)
        }

        self.assertEqual(registered, suite_environment_gates())


def run_git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )


def write_sample_repository(root: Path) -> Path:
    """A throwaway repository shaped like this one: a package, a test tree, a script."""
    run_git(root, "init", "--quiet")
    (root / "pyproject.toml").write_text(
        "\n".join(
            (
                "[tool.ruff]",
                "line-length = 100",
                "[tool.pyright]",
                'include = ["."]',
                "[tool.radon]",
                'cc_min = "B"',
                "[tool.coverage.run]",
                "branch = true",
                "[tool.pytest.ini_options]",
                'testpaths = ["tests"]',
                "",
            )
        ),
        encoding="utf-8",
    )
    for directory in ("pkg", "tests", "scripts"):
        (root / directory).mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_pkg.py").write_text(
        "def test_nothing() -> None: ...\n", encoding="utf-8"
    )
    (root / "scripts" / "sync.py").write_text("value = 1\n", encoding="utf-8")
    run_git(root, "add", "-A")
    return root


def unwrapped_help() -> str:
    """The parser's help with argparse's line wrapping undone.

    argparse rewraps the description to the terminal width, so asserting on a phrase
    means asserting on where the wrap happened to fall.
    """
    return re.sub(r"\s+", " ", check.build_parser().format_help())


def ruff_lint_configuration() -> dict[str, Any]:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["ruff"]["lint"]


def exempted_tool_modules() -> list[Path]:
    """Every file the PLR0913 per-file-ignore actually reaches.

    Resolved from the pattern `pyproject.toml` carries, not from a path written here, so a
    widened pattern drags its new files into the AST walk instead of quietly escaping it.
    """
    patterns = [
        pattern
        for pattern, codes in ruff_lint_configuration().get("per-file-ignores", {}).items()
        if ARGUMENT_COUNT_RULE in codes
    ]
    return sorted({match for pattern in patterns for match in REPOSITORY_ROOT.glob(pattern)})


def ordinary_code_in_tool_module(module: Path) -> list[str]:
    """Everything in ``module`` that is not a published tool declaration or a registrar.

    The exemption's justification -- a signature that IS the published MCP input schema --
    covers `@server.tool()` declarations and the thin `register_*_tools(server, config)`
    that hosts them. Nothing else in these files has that excuse, so anything else is named
    here and the caller fails on it.

    260731-EFA-L6 taught this two more shapes, because a registrar body grows one tool at a
    time and six of them had reached 127-163 lines under the 100-line cap that leaf armed.
    A registrar may now delegate to another registrar DEFINED IN THE SAME MODULE, and it
    may carry a docstring saying what it groups. That is the whole widening, and it stays
    tight where it matters: a call to anything this module does not define as a registrar
    is still ordinary code, so delegation cannot become a route for arbitrary calls, and a
    private `_register_*_tools` helper is held to the same body rule as the public one.

    Read from the AST rather than the source text. A grep for the decorator cannot tell a
    real declaration from the same characters in a docstring, and cannot see that a helper
    two levels down is undecorated at all.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    registrars = {node for node in tree.body if is_tool_registrar(node)}
    registrar_names = {registrar.name for registrar in registrars}
    findings: list[str] = []
    for node in ast.walk(tree):
        where = f"{module.name}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.ClassDef):
            # A class body is a second place to put methods, and a seven-parameter method
            # inside one would inherit the exemption without inheriting its reason.
            findings.append(f"{where} class {node.name}")
        elif isinstance(node, ast.Lambda):
            findings.append(f"{where} lambda")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node in registrars:
                findings.extend(registrar_body_findings(node, registrar_names, module.name))
            elif not any(is_server_tool_decorator(decorator) for decorator in node.decorator_list):
                findings.append(f"{where} function {node.name} is not a @server.tool()")
    return findings


def is_tool_registrar(node: ast.stmt) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    """A module-level, undecorated ``[_]register_<something>_tools`` host."""
    return (
        isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.lstrip("_").startswith("register_")
        and node.name.endswith("_tools")
        and not node.decorator_list
    )


def registrar_body_findings(
    registrar: ast.FunctionDef | ast.AsyncFunctionDef,
    registrar_names: set[str],
    module_name: str,
) -> list[str]:
    """Registrar statements that are neither a tool declaration nor a delegation."""
    findings: list[str] = []
    for index, statement in enumerate(registrar.body):
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if index == 0 and is_docstring(statement):
            continue
        if is_registrar_delegation(statement, registrar_names):
            continue
        findings.append(
            f"{module_name}:{statement.lineno} registrar {registrar.name} contains "
            f"{type(statement).__name__}"
        )
    return findings


def is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def is_registrar_delegation(statement: ast.stmt, registrar_names: set[str]) -> bool:
    """A bare call to a registrar this module defines -- and to nothing else."""
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id in registrar_names
    )


def is_server_tool_decorator(decorator: ast.expr) -> bool:
    """True for exactly `@server.tool()` -- matched on the syntax tree, not on its spelling."""
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "server"
    )


def seven_parameter_function() -> str:
    """An ordinary function two parameters over Ruff's default `max-args` of 5."""
    names = [f"value_{index}" for index in range(7)]
    signature = ", ".join(f"{name}: int" for name in names)
    return f"def ordinary({signature}) -> int:\n    return {' + '.join(names)}\n"


def over_complex_function() -> str:
    """A function that trips all four complexity rules at once.

    Sixty guarded returns: 60 branches (PLR0912 allows 12), 61 returns (PLR0911 allows
    6), 121 statements (PLR0915 allows 50) and a cyclomatic complexity of 61 (C901 is
    configured at 10). Nothing subtle -- the point is that the configuration rejects it.
    """
    lines = ["def deliberately_over_complex(value: int) -> int:"]
    for index in range(60):
        lines.append(f"    if value == {index}:")
        lines.append(f"        return {index}")
    lines.append("    return -1")
    return "\n".join(lines) + "\n"


def shell_command_lines(script: Path, needle: str) -> list[str]:
    """Executable lines of ``script`` containing ``needle``, comments excluded."""
    return [
        stripped
        for line in script.read_text(encoding="utf-8").splitlines()
        if needle in (stripped := line.strip()) and not stripped.startswith("#")
    ]


def run_ruff_over_tracked_python(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Ruff with ``arguments`` over every tracked Python file in this checkout."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            *arguments,
            *(path.as_posix() for path in check.git_ls_files(REPOSITORY_ROOT, "*.py")),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def run_ruff_with_repository_configuration(source: Path) -> subprocess.CompletedProcess[str]:
    """Ruff over one file, at this repository's real lint configuration.

    `--config` is what makes the run meaningful: the sample lives in a temporary directory
    where Ruff would otherwise discover no `pyproject.toml` and fall back to its defaults,
    which is a different question from the one being asked.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--config",
            str(REPOSITORY_ROOT / "pyproject.toml"),
            "--output-format",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def pytest_ini_options() -> dict[str, object]:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return data["tool"]["pytest"]["ini_options"]


def ini_strings(key: str) -> list[str]:
    """One ini option as the list of strings pytest's schema says it is.

    A wrong shape raises here rather than reading as an empty list, which would let a
    mistyped option pass every assertion below it.
    """
    value = pytest_ini_options()[key]
    if not isinstance(value, list):
        raise TypeError(f"[tool.pytest.ini_options] {key} must be a list, got {type(value)!r}")
    return [str(entry) for entry in value]


def suite_environment_gates() -> set[str]:
    """Environment variables that gate a whole test path in this suite.

    Read from the skip decorators themselves rather than from a list somebody keeps by
    hand. Module-level constants are substituted first, because the installed-runtime
    modules spell their gate as ``LIVE_OPT_IN`` and the real name only appears in the
    assignment. Only the *condition* is inspected: the reason strings also mention
    variables that merely parameterise a run (``AR_CLAUDE_STREAM_BINARY``), and those
    are not gates.
    """
    found: set[str] = set()
    for module in sorted(TESTS_ROOT.glob("test_*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        constants = module_level_constants(tree)
        for decorator in skip_decorators(tree):
            if not decorator.args:
                continue
            condition = substitute(ast.unparse(decorator.args[0]), constants)
            found.update(ENVIRONMENT_NAME.findall(condition))
    return found


def module_level_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            constants[target.id] = ast.unparse(node.value)
    return constants


def skip_decorators(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and is_skip_call(decorator.func):
                calls.append(decorator)
    return calls


def is_skip_call(func: ast.expr) -> bool:
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name in SKIP_DECORATORS


def substitute(expression: str, constants: dict[str, str]) -> str:
    """Inline module-level constants until the expression stops changing."""
    for _ in range(3):
        replaced = re.sub(
            r"\b[A-Za-z_][A-Za-z_0-9]*\b",
            lambda match: constants.get(match.group(0), match.group(0)),
            expression,
        )
        if replaced == expression:
            break
        expression = replaced
    return expression


def sample_scope(root: Path, source: Path) -> check.GateScope:
    return check.GateScope(
        lint_paths=[source],
        type_paths=[source],
        coverage_paths=[source],
        test_paths=[root / "tests"],
    )


def sample_config(
    root: Path,
    source: Path,
    *,
    coverage_json: Path | None = None,
    threshold: float = 30.0,
    top: int = 5,
) -> check.CheckConfig:
    return check.CheckConfig(
        project_root=root,
        scope=sample_scope(root, source),
        coverage_json=coverage_json if coverage_json is not None else root / "coverage.json",
        threshold=threshold,
        top=top,
    )


def write_many_branchy_functions(root: Path, *, count: int) -> Path:
    """`count` uncovered three-path functions, with the coverage report that scores them.

    Only each `def` line is executed -- the shape of a module that is imported and never
    called -- so every function carries two untaken branches and scores well above any
    threshold these tests use.
    """
    body = [
        "def branchy_{index}(value):",
        "    if value > 0:",
        "        return 1",
        "    if value < 0:",
        "        return 2",
        "    return 3",
        "",
    ]
    lines: list[str] = []
    executed: list[int] = []
    missing: list[int] = []
    missing_branches: list[list[int]] = []
    for index in range(count):
        start = len(lines) + 1
        lines.extend(line.format(index=index) for line in body)
        executed.append(start)
        missing.extend(range(start + 1, start + 6))
        missing_branches.extend(
            [[start + 1, start + 2], [start + 1, start + 3], [start + 3, start + 4]]
        )

    source = root / "sample.py"
    source.write_text("\n".join(lines), encoding="utf-8")
    (root / "coverage.json").write_text(
        json.dumps(
            {
                "meta": {"format": 3, "branch_coverage": True},
                "files": {
                    "sample.py": {
                        "executed_lines": executed,
                        "missing_lines": missing,
                        "executed_branches": [],
                        "missing_branches": missing_branches,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return source


def quality_steps_by_name(root: Path) -> dict[str, check.Step]:
    """The wrapper's steps for a throwaway root, keyed by step name."""
    source = write_sample_source(root)
    return {
        step.name: step
        for step in check.quality_steps(sample_config(root, source), root / "coverage.json")
    }


def write_sample_source(root: Path) -> Path:
    """One branchy module in a real repository.

    The repository is not decoration. Every rail of the wrapper reads the tree through
    git -- `derive_scope` from `git ls-files`, the changed-lines coverage floor from
    `git diff` against the merge base -- so a sample project that is not a repository
    exercises the wrapper in a state it can never actually be run from.
    """
    source = root / "sample.py"
    source.write_text(
        "\n".join(
            [
                "def simple(value):",
                "    if value:",
                "        return value + 1",
                "    return 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    if not (root / ".git").exists():
        run_git(root, "init", "--quiet", "--initial-branch=main")
        run_git(root, "add", "-A")
        run_git(
            root,
            "-c",
            "user.email=gate@agents-remember.invalid",
            "-c",
            "user.name=Gate Tests",
            "commit",
            "--quiet",
            "-m",
            "sample",
        )
    return source


def fake_runner(
    commands: list[list[str]],
    coverage_json: Path,
    *,
    failing_step: str | None = None,
) -> check.CommandRunner:
    def run(name: str, command: list[str], cwd: Path, env: Mapping[str, str]) -> check.StepResult:
        commands.append(command)
        if name == "pytest":
            coverage_json.write_text(
                json.dumps(
                    {
                        # `meta.branch_coverage` is what the CRAP reader checks before it
                        # will score anything, so the stand-in report has to carry it.
                        "meta": {"format": 3, "branch_coverage": True},
                        "files": {
                            "sample.py": {
                                "executed_lines": [1, 2, 3, 4],
                                "missing_lines": [],
                                "executed_branches": [[2, 3], [2, 4]],
                                "missing_branches": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
        return check.StepResult(name, 1 if name == failing_step else 0, command)

    return run


def command_modules(commands: list[list[str]]) -> list[str]:
    return [command[2] for command in commands]


if __name__ == "__main__":
    unittest.main()
