"""Old-format cards found at adoption time (MIK-R45 rule 3).

A converted tree's first baseline must hold no old-format card: a card carrying a
metadata-table row ``doc_type``, ``lastVerifiedCommitHash`` or ``lastVerifiedCommitDate``,
or a heading ``## Update History``. The validator has no rule against such a card, so the
check lives on the adoption path instead: ``memory_baseline_status`` and
``memory_baseline_adopt`` report ``blocked-legacy-cards`` with the cards, and ``accept_drift``
does not lift it. It is not a validator rule, so it can never refuse a repository that
already has a baseline (an adopted tree answers ``already-adopted`` before this runs).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

__all__ = ["find_legacy_cards"]

_LEGACY_TABLE_ROW: Final = re.compile(r"^\s*\|")
"""A Markdown table row; only the first cell is ever a metadata key (below)."""

_LEGACY_METADATA_KEYS: Final = frozenset(
    {"doc_type", "lastVerifiedCommitHash", "lastVerifiedCommitDate"}
)
"""The old-format metadata-table keys (MIK-R45 rule 3)."""


def _first_cell(line: str) -> str:
    """The row's first cell, stripped of surrounding backticks and whitespace."""

    cells = line.strip().removeprefix("|").split("|")
    return cells[0].strip().strip("`").strip() if cells else ""


_LEGACY_HISTORY_HEADING: Final = re.compile(r"^\s*##\s+Update History\s*$")
"""The old-format append-only history heading."""


def _is_legacy_card(text: str) -> bool:
    """Whether the card text carries any of the four old-format signs.

    Only a metadata-key first cell counts as a stamp row: a prose or code mention
    of a key in a later cell (a glossary explaining ``doc_type``, a finding about
    ``lastVerifiedCommitHash``) is a valid converted card, not a legacy one.
    """

    for line in text.splitlines():
        if _LEGACY_TABLE_ROW.match(line) is not None and _first_cell(line) in _LEGACY_METADATA_KEYS:
            return True
        if _LEGACY_HISTORY_HEADING.match(line) is not None:
            return True
    return False


def find_legacy_cards(memory_root: Path) -> list[str]:
    """Memory-relative paths of onboarding cards holding old-format signs, sorted.

    Every ``*.md`` file under ``onboarding/`` is read as authored (fences included: a card
    quoting the old format still carries it into the baseline). An unreadable card is
    skipped here; the validator and the drift summary own unreadable files.
    """

    onboarding = memory_root / "onboarding"
    if not onboarding.is_dir():
        return []
    found: list[str] = []
    for path in sorted(onboarding.rglob("*.md")):
        relative = path.relative_to(memory_root)
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_legacy_card(text):
            found.append(relative.as_posix())
    return found
