"""Resolve every card's declared governing overview, and report the ones that do not.

This closes the gap the onboarding checker had until `260915-CAPS-L20`: ``check_missing_onboarding``
validated that a source *has* a card and never that the card's declared route *resolves*, so a card
whose ``governingOverview`` field or whose ``## Governing Overview`` link pointed at a file that does
not exist passed every check the product runs (D3/D16).

Two declarations are graded, and only for a card that declares the field at all:

``field``
    The ``| governingOverview | `<target>` |`` metadata row. Because two conventions ship, the target
    is tried relative to the card's own directory first and then relative to the onboarding root and
    its parent; the field is reported only when none of those bases resolves it.

``link``
    The markdown link inside the card's own ``## Governing Overview`` section, resolved
    card-relative -- which is what a reader clicking it gets.

A card with a live field and no such section is *observed*, not failed: it declares no body link, so
there is no link that cannot be resolved. The observation is returned separately so the product can
report the coverage shape without making it a repair obligation.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel import filesystem
from agents_remember.kernel.onboarding_doc import table_metadata

CHECK_NAME = "integrity.governing_overview_resolution"
FIELD = "governingOverview"
SECTION_HEADING = "## governing overview"
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

CODE_FIELD_UNRESOLVED = "governing-overview-field-unresolved"
CODE_LINK_UNRESOLVED = "governing-overview-link-unresolved"
CODE_SECTION_ABSENT = "governing-overview-section-absent"


@dataclass(frozen=True)
class UnresolvedOverview:
    """One card's declaration that does not resolve, with the line a reader can open."""

    card: str
    code: str
    declared: str
    line: int
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "check": CHECK_NAME,
            "code": self.code,
            "path": self.card,
            "line": self.line,
            "declared": self.declared,
            "message": self.note,
        }


@dataclass(frozen=True)
class GoverningOverviewResolution:
    ok: bool
    check: str
    cardsWalked: int
    cardsFlagged: int
    unresolvedField: int
    unresolvedLink: int
    sectionAbsent: int
    findings: tuple[UnresolvedOverview, ...]
    observations: tuple[UnresolvedOverview, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "check": self.check,
            "cardsWalked": self.cardsWalked,
            "cardsFlagged": self.cardsFlagged,
            "unresolvedFieldCount": self.unresolvedField,
            "unresolvedLinkCount": self.unresolvedLink,
            "sectionAbsentCount": self.sectionAbsent,
            "findings": [row.to_dict() for row in self.findings],
            "observations": [row.to_dict() for row in self.observations],
        }


def _section_body(text: str) -> tuple[int, str] | None:
    """Return (1-based heading line, body) for the card's own Governing Overview section."""

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() != SECTION_HEADING:
            continue
        body: list[str] = []
        for later in lines[index + 1 :]:
            if later.startswith("## "):
                break
            body.append(later)
        return index + 1, "\n".join(body)
    return None


def _target_exists(base: Path, target: str) -> bool:
    cleaned = target.split("#", 1)[0].strip()
    if not cleaned:
        return False
    return filesystem.exists(base / cleaned)


def _field_resolves(card_path: Path, onboarding_root: Path, target: str) -> bool:
    bases = (card_path.parent, onboarding_root, onboarding_root.parent)
    return any(_target_exists(base, target) for base in bases)


def resolve_card(
    card_path: Path, onboarding_root: Path
) -> tuple[tuple[UnresolvedOverview, ...], UnresolvedOverview | None]:
    """Return (findings, observation) for one card.

    The two declarations fail INDEPENDENTLY -- a card can carry a resolving field and a dead
    section link, or a dead field and a link -- so both are reported rather than the first one
    found. Measured on the live corpus: 3 cards are broken in both representations, and
    reporting only the field would understate D3's 39 dead section links as 36.
    """

    text = filesystem.read_text(card_path, encoding="utf-8")
    rel = card_path.relative_to(onboarding_root).as_posix()
    metadata = table_metadata(card_path)
    field_target = metadata.get(FIELD, "").strip()
    section = _section_body(text)
    if not field_target:
        return (), None

    findings: list[UnresolvedOverview] = []
    if not _field_resolves(card_path, onboarding_root, field_target):
        findings.append(
            UnresolvedOverview(
                card=rel,
                code=CODE_FIELD_UNRESOLVED,
                declared=field_target,
                line=_field_line(text),
                note=(
                    f"the card's declared governingOverview `{field_target}` does not exist "
                    "relative to the card, the onboarding root, or its parent"
                ),
            )
        )

    if section is None:
        return (
            tuple(findings),
            UnresolvedOverview(
                card=rel,
                code=CODE_SECTION_ABSENT,
                declared=field_target,
                line=0,
                note=(
                    "the card declares a governingOverview field but carries no "
                    "'## Governing Overview' section, so it declares no body link to resolve"
                ),
            ),
        )

    heading_line, body = section
    match = LINK_PATTERN.search(body)
    if match is None:
        return (
            tuple(findings),
            UnresolvedOverview(
                card=rel,
                code=CODE_SECTION_ABSENT,
                declared=field_target,
                line=heading_line,
                note=(
                    "the card's '## Governing Overview' section carries no markdown link, "
                    "so it declares no body link to resolve"
                ),
            ),
        )

    link_target = match.group(1).strip()
    if not _target_exists(card_path.parent, link_target):
        findings.append(
            UnresolvedOverview(
                card=rel,
                code=CODE_LINK_UNRESOLVED,
                declared=link_target,
                line=heading_line + body[: match.start()].count("\n") + 1,
                note=(
                    f"the card's own `[{link_target}]` link resolves card-relative to nothing, "
                    "so a reader clicking it lands nowhere"
                ),
            )
        )
    return tuple(findings), None


def _field_line(text: str) -> int:
    for index, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("|") and line.split("|")[1].strip() == FIELD:
            return index
    return 0


def check_governing_overview_resolution(onboarding_root: Path) -> GoverningOverviewResolution:
    """Resolve every card's declared governing overview under one onboarding root."""

    findings: list[UnresolvedOverview] = []
    observations: list[UnresolvedOverview] = []
    cards_walked = 0
    cards_flagged = 0
    if not onboarding_root.is_dir():
        return GoverningOverviewResolution(
            ok=True,
            check=CHECK_NAME,
            cardsWalked=0,
            cardsFlagged=0,
            unresolvedField=0,
            unresolvedLink=0,
            sectionAbsent=0,
            findings=(),
            observations=(),
        )
    for path in sorted(onboarding_root.rglob("*.md")):
        if not path.is_file():
            continue
        cards_walked += 1
        card_findings, observation = resolve_card(path, onboarding_root)
        if not card_findings and observation is None:
            continue
        cards_flagged += 1
        findings.extend(card_findings)
        if observation is not None:
            observations.append(observation)
    return GoverningOverviewResolution(
        ok=not findings,
        check=CHECK_NAME,
        cardsWalked=cards_walked,
        cardsFlagged=cards_flagged,
        unresolvedField=sum(1 for row in findings if row.code == CODE_FIELD_UNRESOLVED),
        unresolvedLink=sum(1 for row in findings if row.code == CODE_LINK_UNRESOLVED),
        sectionAbsent=len(observations),
        findings=tuple(findings),
        observations=tuple(observations),
    )


def main(argv: list[str] | None = None) -> int:
    """Standalone runner: exits non-zero while any declared overview does not resolve."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onboarding-root", required=True, type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    result = check_governing_overview_resolution(args.onboarding_root.resolve())
    rendered: dict[str, Any] = result.to_dict()
    if args.format == "json":
        print(json.dumps(rendered, indent=2))
    else:
        print(f"check={result.check}")
        print(f"cardsWalked={result.cardsWalked}")
        print(f"cardsFlagged={result.cardsFlagged}")
        print(f"unresolvedFieldCount={result.unresolvedField}")
        print(f"unresolvedLinkCount={result.unresolvedLink}")
        print(f"sectionAbsentCount={result.sectionAbsent}")
        for row in result.findings:
            print(f"\t{row.code}\t{row.card}:{row.line}\t{row.declared}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
