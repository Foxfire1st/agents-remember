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
            self.assertAlmostEqual(by_name["branchy"].coverage_ratio, 2 / 6)
            self.assertGreater(by_name["branchy"].crap, by_name["simple"].crap)

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
            coverage_json.write_text(json.dumps({"files": {}}), encoding="utf-8")

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
            {
                "files": {
                    "sample.py": {
                        "executed_lines": [1, 2, 4, 5],
                        "missing_lines": [6, 7, 8, 9],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return source, coverage_json


if __name__ == "__main__":
    unittest.main()
