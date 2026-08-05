from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import crap_calculator


class CrapCalculatorTests(unittest.TestCase):
    def test_crap_score_formula_uses_coverage_ratio(self) -> None:
        self.assertAlmostEqual(crap_calculator.crap_score(10, 0.8), 10.8)
        self.assertEqual(crap_calculator.crap_score(10, 1.0), 10.0)

    def test_calculates_function_scores_from_radon_and_coverage_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, coverage_json = write_sample_coverage_fixture(root)

            scores = crap_calculator.calculate_scores(
                [source],
                coverage_json=coverage_json,
                project_root=root,
            )

            by_name = {score.function: score for score in scores}
            self.assertEqual(by_name["simple"].complexity, 1)
            self.assertEqual(by_name["simple"].coverage_ratio, 1.0)
            self.assertEqual(by_name["branchy"].complexity, 3)
            # 2 of 6 statements plus 1 of 2 branch arcs: 3/8, where the statement-only
            # reader produced 2/6. The untaken false arc of `if value > 0` is a unit of the
            # denominator that did not exist before. It does not always move the ratio
            # down -- here a mostly-unexecuted function gains from its one taken arc; the
            # test below covers the case that matters, a function whose statements all run.
            self.assertAlmostEqual(by_name["branchy"].coverage_ratio, 3 / 8)
            self.assertEqual(by_name["branchy"].covered_branches, 1)
            self.assertEqual(by_name["branchy"].missing_branches, 1)
            self.assertGreater(by_name["branchy"].crap, by_name["simple"].crap)

    def test_a_partially_taken_branch_lowers_the_score_a_statement_reader_calls_perfect(
        self,
    ) -> None:
        """Count an untaken branch even when every statement executed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.py"
            source.write_text(
                "def branchy(value):\n    if value > 0:\n        return 1\n    return 0\n",
                encoding="utf-8",
            )
            coverage_json = root / "coverage.json"
            coverage_json.write_text(
                json.dumps(
                    branch_report(
                        {
                            "sample.py": {
                                "executed_lines": [1, 2, 3, 4],
                                "missing_lines": [],
                                "executed_branches": [[2, 3]],
                                "missing_branches": [[2, 4]],
                            }
                        }
                    )
                ),
                encoding="utf-8",
            )

            score = crap_calculator.calculate_scores(
                [source], coverage_json=coverage_json, project_root=root
            )[0]

            self.assertEqual(score.complexity, 2)
            self.assertAlmostEqual(score.coverage_ratio, 5 / 6)
            self.assertAlmostEqual(score.crap, 2**2 * (1 / 6) ** 3 + 2)
            self.assertGreater(score.crap, crap_calculator.crap_score(2, 1.0))

    def test_a_function_without_branches_is_scored_by_the_same_division(self) -> None:
        """The zero-branch case: no special path, no metric switch, no division by zero."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.py"
            source.write_text("def flat(value):\n    x = value + 1\n    return x\n", "utf-8")
            coverage_json = root / "coverage.json"
            coverage_json.write_text(
                json.dumps(
                    branch_report(
                        {
                            "sample.py": {
                                "executed_lines": [1],
                                "missing_lines": [2, 3],
                                "executed_branches": [],
                                "missing_branches": [],
                            }
                        }
                    )
                ),
                encoding="utf-8",
            )

            score = crap_calculator.calculate_scores(
                [source], coverage_json=coverage_json, project_root=root
            )[0]

            self.assertEqual((score.covered_branches, score.missing_branches), (0, 0))
            self.assertAlmostEqual(score.coverage_ratio, 1 / 3)

    def test_a_report_without_branch_measurement_is_refused(self) -> None:
        """No silent fallback: statement-only input fails loudly instead of scoring low."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.py"
            source.write_text("def flat(value):\n    return value\n", encoding="utf-8")
            coverage_json = root / "coverage.json"
            coverage_json.write_text(
                json.dumps(
                    {
                        "meta": {"format": 3, "branch_coverage": False},
                        "files": {"sample.py": {"executed_lines": [1, 2], "missing_lines": []}},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError) as raised:
                crap_calculator.calculate_scores(
                    [source], coverage_json=coverage_json, project_root=root
                )

            self.assertIn("branch_coverage", str(raised.exception))

    def test_a_malformed_branch_arc_raises_rather_than_being_dropped(self) -> None:
        # Dropping an arc it cannot read would leave that branch looking taken, which moves
        # every score using it in the forgiving direction. Each malformation is refused by
        # name so the failure says which entry of the report is wrong.
        for raw, expected in (
            ("not-a-list", "expected a list of branch arcs, got str"),
            ([[1, 2], [3]], "expected a [source, destination] branch arc, got [3]"),
            ([[1, 2, 3]], "expected a [source, destination] branch arc, got [1, 2, 3]"),
            # Coverage.py writes `[-1, 7]` for an exit arc, so an endpoint really can be
            # something other than a line number -- but never a non-integer. A `"7"` here
            # would hash apart from the `7` in the executed set and read as never taken.
            ([[1, 2], ["3", 4]], "branch arc endpoints must be integers, got ['3', 4]"),
            ([[1, None]], "branch arc endpoints must be integers, got [1, None]"),
        ):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError) as raised:
                crap_calculator.parse_branch_arcs(raw)
            self.assertEqual(str(raised.exception), expected)

    def test_well_formed_arcs_survive_including_the_negative_exit_endpoint(self) -> None:
        self.assertEqual(
            crap_calculator.parse_branch_arcs([[1, 2], [1, -1], [1, 2]]),
            {(1, 2), (1, -1)},
        )

    def test_rollup_and_rendering_report_function_scores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, coverage_json = write_sample_coverage_fixture(root)
            scores = crap_calculator.calculate_scores(
                [source],
                coverage_json=coverage_json,
                project_root=root,
            )

            rollups = crap_calculator.rollup_by_file(scores, threshold=2.0)
            table = crap_calculator.render_table(scores, root, threshold=2.0, top=10)
            payload = json.loads(crap_calculator.render_json(scores, root, threshold=2.0))

            self.assertEqual(rollups[0].function_count, 2)
            self.assertIn("# CRAP-Calculator", table)
            self.assertIn("branchy", table)
            self.assertEqual(payload["tool"], "CRAP-Calculator")
            self.assertEqual(payload["files"][0]["overThreshold"], 1)

    def test_cli_renders_table_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, coverage_json = write_sample_coverage_fixture(root)

            table_stdout = StringIO()
            with redirect_stdout(table_stdout):
                exit_code = crap_calculator.main(
                    [
                        str(source),
                        "--coverage-json",
                        str(coverage_json),
                        "--project-root",
                        str(root),
                        "--top",
                        "1",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertIn("# CRAP-Calculator", table_stdout.getvalue())

            json_stdout = StringIO()
            with redirect_stdout(json_stdout):
                exit_code = crap_calculator.main(
                    [
                        str(source),
                        "--coverage-json",
                        str(coverage_json),
                        "--project-root",
                        str(root),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(json_stdout.getvalue())["tool"], "CRAP-Calculator")

    def test_missing_coverage_file_counts_as_zero_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.py"
            source.write_text("def simple(value):\n    return value + 1\n", encoding="utf-8")
            coverage_json = root / "coverage.json"
            coverage_json.write_text(json.dumps(branch_report({})), encoding="utf-8")

            score = crap_calculator.calculate_scores(
                [source],
                coverage_json=coverage_json,
                project_root=root,
            )[0]

            self.assertTrue(score.missing_coverage_data)
            self.assertEqual(score.coverage_ratio, 0.0)
            self.assertEqual(score.crap, 2.0)


def write_sample_coverage_fixture(root: Path) -> tuple[Path, Path]:
    source = root / "sample.py"
    source.write_text(
        "\n".join(
            [
                "def simple(value):",
                "    return value + 1",
                "",
                "def branchy(value):",
                "    if value > 0:",
                "        return 'positive'",
                "    if value < 0:",
                "        return 'negative'",
                "    return 'zero'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    coverage_json = root / "coverage.json"
    coverage_json.write_text(
        json.dumps(
            branch_report(
                {
                    "sample.py": {
                        "executed_lines": [1, 2, 4, 5],
                        "missing_lines": [6, 7, 8, 9],
                        # `if value > 0` was only ever evaluated true, so the arc to line 7
                        # is a branch the suite never took.
                        "executed_branches": [[5, 6]],
                        "missing_branches": [[5, 7]],
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    return source, coverage_json


def branch_report(files: dict[str, object]) -> dict[str, object]:
    """A Coverage.py JSON report that declares branch measurement, as the reader requires."""
    return {"meta": {"format": 3, "branch_coverage": True}, "files": files}


if __name__ == "__main__":
    unittest.main()
