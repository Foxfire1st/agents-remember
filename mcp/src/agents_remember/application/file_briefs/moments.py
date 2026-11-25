"""What the product tells a Reviewer and a Curator when it starts on a change.

At each of the two moments the product composes one message. For converted memory it writes a file
over the files the change touches. The file goes into ``briefs/_task/`` when Git ignores that
folder, or into the recipient's task scratch otherwise. The message carries:

* the counts: changed files, the rules recorded at them, their families, the rules without a
  proving test, and the documents that stand on changed files;
* the rules whose anchor moved (the currentness check over the task's code as it stands now), by ID and
  title as far as the message limit allows, with the number left out;
* the path of the moment's file, which names the moved rules first, each with its text, and then
  every touched rule in words, file by file.

For unconverted memory the message says there is no moment file. The changed files are those that
differ between the task's base commit and its code worktree, including uncommitted edits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from agents_remember.application.file_briefs.facts import BriefSource
from agents_remember.application.file_briefs.folder import BRIEFS, TASK_FOLDER
from agents_remember.application.file_briefs.model import FileBrief, RuleBlock
from agents_remember.application.file_briefs.render_brief import (
    counted,
    head_line,
    rule_markdown,
    tree_line,
)
from agents_remember.application.file_briefs.triggers import WRITING_FAILURES, brief_source
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import run_git_bytes
from agents_remember.kernel.memory_ignore import briefs_ignored
from agents_remember.memory.knowledge_index import KnowledgeIndex

Moment = Literal["review-start", "curator-start"]
MOMENTS: Final[tuple[Moment, ...]] = ("review-start", "curator-start")
_OPENING: Final[dict[Moment, str]] = {
    "review-start": "Review start",
    "curator-start": "Curator start, your list of work",
}


@dataclass(frozen=True)
class MomentReport:
    """What one moment's file and message say about a change."""

    moment: Moment
    leaf: str
    changed: tuple[str, ...]
    briefs: tuple[FileBrief, ...]
    rules: tuple[RuleBlock, ...]
    moved: tuple[RuleBlock, ...]
    families: int
    without_proof: int
    documents: int


@dataclass(frozen=True)
class MomentMessage:
    """The message text and the file it names."""

    text: str
    file: Path
    report: MomentReport

    @property
    def leaf(self) -> str:
        return self.report.leaf


@dataclass(frozen=True)
class MomentNotice:
    """The product message for a start whose memory is unconverted."""

    leaf: str
    text: str


def changed_files(code_worktree: Path, base_commit: str) -> list[str]:
    """Files that differ between ``base_commit`` and the worktree as it stands, untracked included."""

    # ``-z`` gives each path as Git stores it: without it Git quotes a name with characters outside
    # ASCII, and the quoted form names no source file.
    tracked = run_git_bytes(
        code_worktree, ["diff", "--name-only", "-z", "--no-renames", base_commit, "--"]
    )
    untracked = run_git_bytes(code_worktree, ["ls-files", "-z", "--others", "--exclude-standard"])
    names: set[str] = set()
    for result in (tracked, untracked):
        if result.returncode == 0:
            names.update(
                one.decode("utf-8", "surrogateescape") for one in result.stdout.split(b"\0") if one
            )
    return sorted(names)


def moment_report(
    source: BriefSource, moment: Moment, leaf: str, changed: Sequence[str]
) -> MomentReport:
    """The briefs of the changed files and what the moment counts over them."""

    source.measure_paths(changed)
    briefs = tuple(one for one in map(source.assemble, changed) if one is not None)
    rules = _touched_rules(briefs)
    return MomentReport(
        moment=moment,
        leaf=leaf,
        changed=tuple(changed),
        briefs=briefs,
        rules=rules,
        moved=tuple(one for one in rules if one.head.moved),
        families=_family_count(briefs),
        without_proof=sum(1 for one in rules if one.head.proofs == 0),
        documents=_document_count(briefs),
    )


def _touched_rules(briefs: Sequence[FileBrief]) -> tuple[RuleBlock, ...]:
    """Each rule of the briefs once, by ID; a moved block of a rule wins over an unmoved one."""

    seen: dict[str, RuleBlock] = {}
    for brief in briefs:
        for rule in brief.rules:
            if rule.head.id not in seen or rule.head.moved:
                seen[rule.head.id] = rule
    return tuple(seen[key] for key in sorted(seen))


def _family_count(briefs: Sequence[FileBrief]) -> int:
    return len({family.id for brief in briefs for family in brief.families})


def _document_count(briefs: Sequence[FileBrief]) -> int:
    return len({one.id for brief in briefs for one in brief.documents})


def moment_file(memory_root: Path, moment: Moment) -> Path:
    return memory_root / BRIEFS / TASK_FOLDER / f"{moment}.md"


def write_moment_file(target: Path, report: MomentReport, memory_tree: str) -> None:
    """Write the moment's file: the moved rules first with their text, then every touched rule."""

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, moment_markdown(report, memory_tree))


def moment_markdown(report: MomentReport, memory_tree: str) -> str:
    lines = [
        tree_line(memory_tree),
        f"# {_OPENING[report.moment]}: {report.leaf}",
        "",
        f"- {_counts(report)}",
        "- Derived file, written by the product when the seat started. Never edit it.",
        "",
        f"## Rules whose anchor moved ({len(report.moved)})",
    ]
    lines += _moved_rules(report.moved)
    lines += ["", f"## Every touched rule, by changed file ({len(report.rules)} rules)"]
    shown: set[str] = set()
    for brief in report.briefs:
        lines += _changed_file(brief, shown)
    without = [one for one in report.changed if one not in {b.head.path for b in report.briefs}]
    lines += ["", f"## Changed files with nothing recorded ({len(without)})", ""]
    lines += [f"- `{one}`" for one in without] or ["None."]
    return "\n".join(lines) + "\n"


def _moved_rules(moved: Sequence[RuleBlock]) -> list[str]:
    """Each moved rule with its standing and text, or ``None.`` when no anchor moved."""

    lines: list[str] = []
    for rule in moved:
        lines += ["", f"### {rule.head.title.text} · `{rule.head.id}`", "", head_line(rule.head)]
        lines += ["", f"**Rule.** {rule.statement}"]
    return lines or ["", "None."]


def _changed_file(brief: FileBrief, shown: set[str]) -> list[str]:
    """One changed file's part; a rule already in ``shown`` is pointed to, every other is added."""

    lines = ["", f"### `{brief.head.path}`"]
    if not brief.rules:
        lines += ["", "No rule is recorded at this file."]
    for number, rule in enumerate(brief.rules, 1):
        if rule.head.id in shown:
            lines += ["", f"- `{rule.head.id}` {rule.head.title.text}: shown above."]
            continue
        shown.add(rule.head.id)
        lines += ["", *rule_markdown(number, rule, level="####")]
    if brief.documents:
        lines += [
            "",
            "Documents that stand on it: "
            + ", ".join(f"`{one.document}`" for one in brief.documents),
        ]
    return lines


def moment_message(
    report: MomentReport, file_location: str, limit: int, *, relative_location: str | None = None
) -> str:
    """The message: counts, the file, and moved rules by ID and title while ``limit`` allows.

    The counts, the number of moved rules and a pointer to the file are what the message must hold.
    The moved rules' names go first when room is short. The caller can supply a shorter location
    that identifies the same file relative to the recipient's workspace.
    """

    head = f"{_OPENING[report.moment]} for {report.leaf}. {_counts(report)}"
    locations = (
        (file_location, relative_location) if relative_location is not None else (file_location,)
    )
    for location in locations:
        tail = f" Every touched rule in words: {location}"
        if not report.moved:
            text = f"{head} No rule's anchor moved.{tail}"
            if len(text) <= limit:
                return text
            continue
        named: list[str] = []
        for position, rule in enumerate(report.moved):
            left = len(report.moved) - position - 1
            candidate = [*named, f"{rule.head.id} {rule.head.title.text}"]
            if len(_moved_text(head, candidate, left, tail)) > limit:
                break
            named = candidate
        text = _moved_text(head, named, len(report.moved) - len(named), tail)
        if len(text) <= limit:
            return text
    tail = f" Every touched rule in words: {locations[-1]}"
    if not report.moved:
        return _within(f"{head} No rule's anchor moved.{tail}", limit)
    return _within(_moved_text(head, [], len(report.moved), tail), limit)


@dataclass(frozen=True)
class StartRequest:
    """A start moment bound to a leaf's worktrees, base and the recipient's scratch."""

    moment: Moment
    leaf: str
    memory_root: Path
    code_root: Path
    base_commit: str
    coordination_root: Path
    limit: int
    scratch_root: Path | None = None


def compose_start_message(request: StartRequest) -> MomentMessage | MomentNotice | str:
    """Compose a deliverable message or return a diagnostic if composition fails."""

    from agents_remember.application.published_intent import (  # noqa: PLC0415 - imports triggers
        converted_memory_tree,
        select_knowledge_dataset,
    )

    try:
        if converted_memory_tree(request.memory_root) is None:
            text = (
                f"{_OPENING[request.moment]} for {request.leaf}. "
                "The memory is unconverted, so there is no moment file."
            )
            return MomentNotice(request.leaf, _within(text, request.limit))
        if briefs_ignored(request.memory_root):
            file = moment_file(request.memory_root, request.moment)
            relative_location = file.relative_to(request.memory_root).as_posix()
        elif request.scratch_root is not None:
            file = request.scratch_root / f"{request.moment}.md"
            relative_location = f"{file.name} in your task scratch folder"
        else:
            return f"the {request.moment} message needs the seat's recorded task scratch folder"
        selected = select_knowledge_dataset(
            request.memory_root, coordination_root=request.coordination_root
        )
        tree = selected.memory_tree
        if tree is None:
            return f"the memory at {request.memory_root} has no converted tree; no message"
        index = KnowledgeIndex(selected.database_path, expected_key=tree.tree_key)
        with index, brief_source(request.memory_root, index, request.code_root) as source:
            changed = changed_files(request.code_root, request.base_commit)
            report = moment_report(source, request.moment, request.leaf, changed)
            write_moment_file(file, report, tree.tree_key)
    except WRITING_FAILURES as error:
        return f"the {request.moment} message could not be made ({type(error).__name__}: {error})"
    return MomentMessage(
        text=moment_message(
            report, file.as_posix(), request.limit, relative_location=relative_location
        ),
        file=file,
        report=report,
    )


def _moved_text(head: str, named: Sequence[str], left: int, tail: str) -> str:
    if not named:
        return f"{head} All {left} are named in the file.{tail}"
    listed = "; ".join(named)
    more = f" ({left} more in the file)" if left else ""
    return f"{head} Anchors moved at: {listed}{more}.{tail}"


def _counts(report: MomentReport) -> str:
    return (
        f"The change touches {counted(len(report.changed), 'file')} with "
        f"{counted(len(report.rules), 'rule')} in "
        f"{counted(report.families, 'family', 'families')}; "
        f"{report.without_proof} of the rules have no proving test; "
        f"{counted(report.documents, 'document')} stand on changed files; "
        f"{counted(len(report.moved), 'rule')} with a moved anchor."
    )


def _within(text: str, limit: int) -> str:
    """The message itself never passes the route's limit: the file holds everything."""

    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


__all__ = [
    "MOMENTS",
    "Moment",
    "MomentMessage",
    "MomentNotice",
    "MomentReport",
    "StartRequest",
    "changed_files",
    "compose_start_message",
    "moment_file",
    "moment_markdown",
    "moment_message",
    "moment_report",
    "write_moment_file",
]
