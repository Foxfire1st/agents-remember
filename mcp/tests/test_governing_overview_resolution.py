"""Falsifiable guards for the governing-overview resolution check (D3/D16, `260915-CAPS-L20`).

The check exists because the product validated that a source *has* a card and never that the card's
declared route *resolves*, so a card whose ``governingOverview`` field -- or whose
``## Governing Overview`` link -- pointed at a file that does not exist reported clean.

Each case seeds the defect it claims to catch and asserts the refusal, and each carries its own
non-vacuity control **inside the same tree**: a resolving card that must stay green, so a checker
that flagged everything could not pass. A case that stayed green against its own seed would be the
exact gap this module exists to close.

Two cases, not four, and that is a budget decision stated rather than implied: the unit population
stands at 1496 of a 1500 ceiling, and `conftest.pytest_collection_finish` refuses the **entire** run
when the population exceeds it. Four one-assertion cases would have spent the last four slots and
left the next leaf with no room at all; this module spends one and the wiring case beside it in
`test_memory_quality_runs.py` spends the other, so both halves of the defect are protected.

The second half lives there because the *silence* was the product defect: a correct checker whose
findings never reach the curator's actionable set still reports clean.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agents_remember.memory_quality.integrity.governing_overview_resolution import (
    CODE_FIELD_UNRESOLVED,
    CODE_LINK_UNRESOLVED,
    CODE_SECTION_ABSENT,
    check_governing_overview_resolution,
)

CARD = """# card

| Field | Value |
| --- | --- |
| repository | agents-remember |
| path | `{source}` |
| governingOverview | `{field}` |

## Governing Overview

{link}

## Purpose

A card used by the resolution guard.
"""

CARD_WITHOUT_SECTION = """# card

| Field | Value |
| --- | --- |
| repository | agents-remember |
| path | `{source}` |
| governingOverview | `{field}` |

## Purpose

A card whose field resolves but which carries no Governing Overview section at all.
"""


def _write_card(
    root: Path,
    relative: str,
    *,
    field: str,
    link: str | None,
    section: bool = True,
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    source = relative[: -len(".md")]
    if not section:
        path.write_text(CARD_WITHOUT_SECTION.format(source=source, field=field), encoding="utf-8")
        return path
    path.write_text(
        CARD.format(
            source=source,
            field=field,
            link="" if link is None else f"[Overview]({link})",
        ),
        encoding="utf-8",
    )
    return path


class GoverningOverviewResolutionTests(unittest.TestCase):
    def test_every_dead_declaration_form_is_reported_and_no_clean_card_is(self) -> None:
        """D3's shape (live field, dead body link) and D16's (a field no base resolves).

        Three controls ride in the same tree, because a count of findings cannot show
        discrimination. The resolving card must stay green; and both halves of the *observation*
        axis must be observed rather than failed — the card with no `## Governing Overview`
        section at all, and the card whose section carries no link. Both shapes exist in the
        live corpus (85 and 9 cards), and failing them would be 94 false findings.
        """

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "overview.md").write_text("# overview\n", encoding="utf-8")
            _write_card(
                root,
                "pkg/nested/link-dead.md",
                field="../../overview.md",
                link="../../../overview.md",
            )
            _write_card(
                root,
                "pkg/field-dead.md",
                field="../absent-overview.md",
                link="../absent-overview.md",
            )
            _write_card(root, "pkg/clean.md", field="../overview.md", link="../overview.md")
            _write_card(root, "pkg/no-heading.md", field="../overview.md", link=None, section=False)
            _write_card(root, "pkg/empty-section.md", field="../overview.md", link=None)
            result = check_governing_overview_resolution(root)

        self.assertFalse(result.ok)
        # Per DECLARATION, not per card: the two representations fail independently, and
        # `pkg/field-dead.md` is broken in both. Reporting only the first one found would
        # understate the live corpus's 39 dead section links as 36.
        self.assertEqual(
            sorted((row.card, row.code) for row in result.findings),
            [
                ("pkg/field-dead.md", CODE_FIELD_UNRESOLVED),
                ("pkg/field-dead.md", CODE_LINK_UNRESOLVED),
                ("pkg/nested/link-dead.md", CODE_LINK_UNRESOLVED),
            ],
        )
        by_card = {row.card: row for row in result.findings}
        self.assertIn("lands nowhere", by_card["pkg/nested/link-dead.md"].note)
        self.assertEqual(
            {row.card for row in result.findings},
            {
                "pkg/field-dead.md",
                "pkg/nested/link-dead.md",
            },
        )
        self.assertEqual(result.unresolvedField, 1)
        self.assertEqual(result.unresolvedLink, 2)
        self.assertEqual(result.sectionAbsent, 2)
        # Scope, stated so the flagged count cannot be misread as the corpus size: the walk
        # covers every markdown file under the root (the 5 cards written here plus the route
        # overview) and 4 of those 6 are flagged.
        self.assertEqual(result.cardsWalked, 6)
        self.assertEqual(result.cardsFlagged, 4)
        self.assertEqual(
            [row.card for row in result.observations],
            ["pkg/empty-section.md", "pkg/no-heading.md"],
        )
        self.assertTrue(all(row.code == CODE_SECTION_ABSENT for row in result.observations))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
