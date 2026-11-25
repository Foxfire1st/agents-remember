"""A folder's ``INDEX.md`` under ``briefs/``.

One line per file of the folder that has a brief: its name, how many rules, open weaknesses and
standing documents it has, and the titles of its rules. It says what ``ls`` cannot and makes a
``grep`` over ``briefs/`` useful. Subfolders are listed by name so a reader can walk down.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agents_remember.application.file_briefs.model import FileBrief
from agents_remember.application.file_briefs.render_brief import counted


@dataclass(frozen=True)
class IndexRow:
    """What one file's line in its folder's index says."""

    name: str
    rules: int
    weaknesses: int
    documents: int
    titles: tuple[str, ...]


def index_row(brief: FileBrief) -> IndexRow:
    counts = brief.head.counts
    return IndexRow(
        name=brief.head.path.rsplit("/", 1)[-1],
        rules=counts.rules,
        weaknesses=counts.weaknesses,
        documents=counts.documents,
        titles=tuple(one.head.title.text for one in brief.rules),
    )


def index_markdown(folder: str, rows: Iterable[IndexRow], subfolders: Iterable[str]) -> str:
    """The index of one folder: its files with a brief, then its subfolders."""

    lines = [
        f"# Briefs in `{folder or '.'}`",
        "",
        "Derived by the product; never edit it. One line per file: rules, open weaknesses, "
        "documents that stand on it, and the rules' titles. Each file's brief is `<name>.md` here.",
        "",
    ]
    for row in sorted(rows, key=lambda one: one.name):
        titles = "; ".join(row.titles) or "no rule"
        lines.append(
            f"- `{row.name}` — {counted(row.rules, 'rule')}, "
            f"{counted(row.weaknesses, 'weakness', 'weaknesses')}, "
            f"{counted(row.documents, 'document')} — {titles}"
        )
    names = sorted(subfolders)
    if names:
        lines += ["", "Folders:", *[f"- `{name}/`" for name in names]]
    return "\n".join(lines) + "\n"


__all__ = ["IndexRow", "index_markdown", "index_row"]
