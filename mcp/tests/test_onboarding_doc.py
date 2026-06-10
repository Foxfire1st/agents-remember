"""Tests for kernel onboarding-document body/history helpers."""

from __future__ import annotations

import unittest

from agents_remember.kernel.onboarding_doc import (
    has_no_impact_marker,
    meaningful_body,
    meaningful_body_changed,
    new_history_lines,
    normalize_route,
    route_contains_changed_path,
    update_history_section,
)

SIDECAR = """# src/app.py

| Field | Value |
| --- | --- |
| repository | demo-repo |
| path | `src/app.py` |
| doc_type | `file-level-onboarding` |
| lastUpdated | 2026-06-01T10:00+02:00 |
| lastVerifiedCommitHash | `aaaa` |
| lastVerifiedCommitDate | 2026-06-01T10:00:00+02:00 |

## Purpose

Demo purpose.

## Code Commentary

Original commentary.

## Update History

- 2026-06-01T10:00 — Created.
"""


class MeaningfulBodyTests(unittest.TestCase):
    def test_strips_metadata_rows_and_update_history(self) -> None:
        body = meaningful_body(SIDECAR)
        self.assertNotIn("lastVerifiedCommitHash", body)
        self.assertNotIn("lastUpdated", body)
        self.assertNotIn("Update History", body)
        self.assertNotIn("Created.", body)
        self.assertIn("Original commentary.", body)
        self.assertIn("| repository | demo-repo |", body)

    def test_metadata_only_edit_is_not_a_body_change(self) -> None:
        updated = SIDECAR.replace("`aaaa`", "`bbbb`").replace(
            "2026-06-01T10:00+02:00", "2026-06-02T11:00+02:00"
        )
        self.assertFalse(meaningful_body_changed(SIDECAR, updated))

    def test_history_only_edit_is_not_a_body_change(self) -> None:
        updated = SIDECAR + "- 2026-06-02T11:00 — Another entry.\n"
        self.assertFalse(meaningful_body_changed(SIDECAR, updated))

    def test_body_edit_is_a_body_change(self) -> None:
        updated = SIDECAR.replace("Original commentary.", "Updated commentary.")
        self.assertTrue(meaningful_body_changed(SIDECAR, updated))

    def test_new_document_is_a_body_change(self) -> None:
        self.assertTrue(meaningful_body_changed(None, SIDECAR))

    def test_section_after_update_history_is_body(self) -> None:
        with_trailing = SIDECAR + "\n## Docs References\n\nNone.\n"
        body = meaningful_body(with_trailing)
        self.assertIn("Docs References", body)


class UpdateHistoryTests(unittest.TestCase):
    def test_extracts_history_lines_without_heading(self) -> None:
        lines = update_history_section(SIDECAR)
        self.assertEqual(lines, ["- 2026-06-01T10:00 — Created."])

    def test_new_history_lines_against_old_text(self) -> None:
        updated = SIDECAR + "- 2026-06-02T11:00 — Reworked retries.\n"
        self.assertEqual(
            new_history_lines(SIDECAR, updated),
            ["- 2026-06-02T11:00 — Reworked retries."],
        )

    def test_new_history_lines_for_new_document(self) -> None:
        self.assertEqual(
            new_history_lines(None, SIDECAR),
            ["- 2026-06-01T10:00 — Created."],
        )

    def test_no_new_lines_when_history_unchanged(self) -> None:
        body_only = SIDECAR.replace("Original commentary.", "Updated commentary.")
        self.assertEqual(new_history_lines(SIDECAR, body_only), [])


class NoImpactMarkerTests(unittest.TestCase):
    def test_detects_content_marker(self) -> None:
        self.assertTrue(
            has_no_impact_marker(["- 2026-06-02T11:00 — No content impact: version bump."])
        )

    def test_detects_route_marker_case_insensitive(self) -> None:
        self.assertTrue(
            has_no_impact_marker(["- 2026-06-02T11:00 — no route impact: reviewed, unchanged."])
        )

    def test_rejects_unmarked_entry(self) -> None:
        self.assertFalse(has_no_impact_marker(["- 2026-06-02T11:00 — Reworked retries."]))

    def test_rejects_marker_without_colon(self) -> None:
        self.assertFalse(has_no_impact_marker(["- 2026-06-02T11:00 — No content impact at all."]))


class RouteHelperTests(unittest.TestCase):
    def test_normalize_route_root_forms(self) -> None:
        for raw in ("", ".", "<repo-root>", "/", "`.`"):
            self.assertEqual(normalize_route(raw), ".")

    def test_normalize_route_strips_slashes_and_backticks(self) -> None:
        self.assertEqual(normalize_route("`mcp/src/`"), "mcp/src")

    def test_route_contains_changed_path(self) -> None:
        self.assertTrue(route_contains_changed_path("mcp/src", ["mcp/src/app.py"]))
        self.assertTrue(route_contains_changed_path(".", ["anything.py"]))
        self.assertFalse(route_contains_changed_path("mcp/src", ["runtime/app.py"]))
        self.assertFalse(route_contains_changed_path("mcp/src", []))


if __name__ == "__main__":
    unittest.main()
