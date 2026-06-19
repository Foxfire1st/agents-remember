"""Render a :class:`TaskDocument` to its ``task.md`` markdown form.

The JSON document is the source of truth; this module is the **only** writer of
the rendered markdown. It mirrors ``worktrees.worktree_contract.contract_to_text``:
small per-section helpers assemble lines from the model, so a re-render fully
regenerates the document and freeform markdown prose is never round-tripped back.
The ``w-02-light-task-workflow`` ``template.md`` is the spec these helpers follow.

Output is deterministic: the same document renders byte-for-byte identically.
Section bodies carry no leading/trailing blank lines and join their own blocks
with single blanks, so blank lines are exact by construction (no global
normalization that would corrupt blank lines inside code fences).
"""

from __future__ import annotations

from .document import CodeExample, Decision, Section, Step, SubTaskRef, TaskDocument


def render_markdown(doc: TaskDocument) -> str:
    if doc.kind == "master":
        return _render_master(doc)
    title = f"# Task: {doc.title}"
    if doc.kind == "subTask":
        title = f"{title} (Sub-task {doc.id})"
    parts: list[str] = [title, "", *_header_lines(doc)]
    parts += _section("Objective", [doc.objective or "_To be defined._"])
    parts += _section("Requirements", _bullets(doc.requirements))
    parts += _section("Design", [doc.design or "No design reasoning needed."])
    parts += _section("Implementation Steps", _step_lines(doc.steps))
    parts += _section("Proposed Code Examples", _code_example_lines(doc.codeExamples))
    parts += _section("Decision Log", _decision_lines(doc.decisions))
    parts += _section("Open Questions", _bullets(doc.openQuestions, empty="- None."))
    parts += _section("References", _bullets(doc.references))
    return "\n".join(parts) + "\n"


_MARKER: dict[str, str] = {"Completed": "✅", "inProgress": "🔨", "planning": "⬜"}


def _render_master(doc: TaskDocument) -> str:
    """Render a series ``master``: header + the ordered ``sections`` plan.

    Each section is freeform prose (a verbatim ``body``) or a structured block --
    the ``subTasks`` series index or the ``sharedDecisions`` table -- with the
    section ``body`` rendered first as optional intro prose.
    """
    parts: list[str] = [f"# Task: {doc.title}", "", *_header_lines(doc)]
    for section in doc.sections:
        parts += _section(section.heading, _master_body(doc, section))
    return "\n".join(parts) + "\n"


def _master_body(doc: TaskDocument, section: Section) -> list[str]:
    if section.kind == "subTasks":
        return _intro(section.body) + _subtask_lines(doc.subTasks)
    if section.kind == "sharedDecisions":
        return _intro(section.body) + _decision_lines(doc.decisions)
    return section.body.split("\n")


def _intro(body: str) -> list[str]:
    return [*body.split("\n"), ""] if body else []


def _subtask_lines(subtasks: list[SubTaskRef]) -> list[str]:
    if not subtasks:
        return ["_No sub-tasks defined yet._"]
    lines: list[str] = []
    for ref in subtasks:
        file_suffix = f" · `{ref.file}`" if ref.file else ""
        scope_suffix = f" — {ref.scope}" if ref.scope else ""
        marker = _MARKER[ref.status]
        lines.append(f"{ref.number}. {marker} **{ref.name}**{file_suffix}{scope_suffix}")
    return lines


def _header_lines(doc: TaskDocument) -> list[str]:
    lines = [
        f"**Status:** {doc.status}",
        f"**Repo:** {doc.repo}",
        f"**Type:** {doc.type}",
        f"**Created:** {doc.createdAt}",
    ]
    if doc.master:
        lines.append(f"**Master:** `{doc.master}`")
    return lines


def _section(heading: str, body: list[str]) -> list[str]:
    return ["", "---", "", f"## {heading}", "", *body]


def _bullets(items: list[str], *, empty: str = "- _None._") -> list[str]:
    return [f"- {item}" for item in items] if items else [empty]


def _checkbox(status: str) -> str:
    return "x" if status == "done" else " "


def _join_blocks(blocks: list[list[str]]) -> list[str]:
    out: list[str] = []
    for index, block in enumerate(blocks):
        if index:
            out.append("")
        out.extend(block)
    return out


def _step_lines(steps: list[Step]) -> list[str]:
    if not steps:
        return ["_No steps defined yet._"]
    blocks: list[list[str]] = []
    for step in steps:
        # The heading is the step title; the checkbox carries the distinct outcome (R2). A bare step
        # (no outcome, no substeps) is just its heading -- no redundant title echo.
        block = [f"### {step.id} — {step.title}"]
        if step.outcome or step.substeps:
            block += ["", f"- [{_checkbox(step.status)}] {step.outcome or step.title}"]
            for sub in step.substeps:
                suffix = f" — {sub.note}" if sub.note else ""
                block.append(f"  - [{_checkbox(sub.status)}] {sub.title}{suffix}")
        blocks.append(block)
    return _join_blocks(blocks)


def _code_example_lines(examples: list[CodeExample]) -> list[str]:
    if not examples:
        return ["No code examples are needed for this task."]
    blocks: list[list[str]] = []
    for example in examples:
        block = [
            f"### {example.id} — {example.title}",
            "",
            f"Distinct change covered: {example.distinctChange}",
            "",
            f"Why this example is included: {example.why}",
            "",
            f"```{example.language}",
            *example.snippet.split("\n"),
            "```",
        ]
        blocks.append(block)
    return _join_blocks(blocks)


def _decision_lines(decisions: list[Decision]) -> list[str]:
    if not decisions:
        return ["_None recorded._"]
    rows = [
        f"| {_cell(item.at)} | {_cell(item.decision)} | {_cell(item.rationale)} |"
        for item in decisions
    ]
    return ["| Date-Time | Decision | Rationale |", "| --- | --- | --- |", *rows]


def _cell(text: str) -> str:
    return text.replace("\n", " ").replace("|", "\\|").strip()
