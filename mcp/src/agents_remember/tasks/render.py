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

import re

from .document import (
    CodeExample,
    Decision,
    RouteReviewRecord,
    Section,
    SprintExecutionEndpoint,
    SprintExecutionGraph,
    SprintExecutionNode,
    SprintSeat,
    Step,
    StepDisposition,
    SubTaskRef,
    TaskDocument,
    TaskDocumentRef,
)
from .execution_graph_titles import SprintGraphTitles

_MASTER_INDEX_HEADING = "Master Index"


def render_markdown(doc: TaskDocument, *, graph_titles: SprintGraphTitles | None = None) -> str:
    if doc.kind == "master":
        return _render_master(doc, graph_titles=graph_titles)
    title = f"# Task: {doc.title}"
    if doc.kind == "subTask":
        title = f"{title} (Sub-task {doc.id})"
    parts: list[str] = [title, "", *_header_lines(doc)]
    parts += _section("Objective", [doc.objective or "_To be defined._"])
    parts += _section("Requirements", _bullets(doc.requirements))
    parts += _section("Design", [doc.design or "No design reasoning needed."])
    parts += _section("Implementation Steps", _step_lines(doc.steps))
    parts += _section("Route Review", _route_review_lines(doc.routeReview))
    parts += _section(
        "Proposed Code Examples",
        _code_example_lines(doc.codeExamples, doc.codeExamplesNote),
    )
    parts += _section("Decision Log", _decision_lines(doc.decisions))
    parts += _section("Open Questions", _bullets(doc.openQuestions, empty="- None."))
    parts += _section("References", _bullets(doc.references))
    for section in doc.sections:  # R4: freeform extra sections appended after the standard template
        parts += _section(section.heading, section.body.split("\n"))
    return "\n".join(parts) + "\n"


_MARKER: dict[str, str] = {"Completed": "✅", "inProgress": "🔨", "planning": "⬜"}


def _render_master(doc: TaskDocument, *, graph_titles: SprintGraphTitles | None = None) -> str:
    """Render a series ``master``: header + the ordered ``sections`` plan.

    Each section is freeform prose (a verbatim ``body``) or a structured block --
    the ``subTasks`` series index or the ``sharedDecisions`` table -- with the
    section ``body`` rendered first as optional intro prose. A sprint with
    ``orchestrates`` + rows but no ``subTasks`` section still gets its master index
    rendered (L14-R1): the durable markdown must show the sprint → master list.
    """
    parts: list[str] = [f"# Task: {doc.title}", "", *_header_lines(doc)]
    if doc.executionGraph is not None:
        parts += _section(
            "Execution Graph", _execution_graph_lines(doc.executionGraph, graph_titles)
        )
    for section in doc.sections:
        parts += _section(section.heading, _master_body(doc, section))
    if (
        doc.orchestrates
        and doc.subTasks
        and not any(section.kind == "subTasks" for section in doc.sections)
    ):
        parts += _section(
            _MASTER_INDEX_HEADING,
            [
                "_Generated master index (L14): the sprint declares no subTasks section._",
                "",
                *_subtask_lines(doc.subTasks),
            ],
        )
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
        # A typed masterRef row links the master document directly (L14-R1); a row
        # without one keeps the plain bold name + file code span.
        name = f"[**{ref.name}**]({_master_ref_link(ref)})" if ref.masterRef else f"**{ref.name}**"
        file_suffix = f" · `{ref.file}`" if ref.file else ""
        scope_suffix = f" — {ref.scope}" if ref.scope else ""
        marker = _MARKER[ref.status]
        lines.append(f"{ref.number}. {marker} {name}{file_suffix}{scope_suffix}")
    return lines


def _master_ref_link(ref: SubTaskRef) -> str:
    """The sprint task.md-relative link to the row's master document.

    Task documents live at ``tasks/<repo>/<folder>/task.md``; the master's
    ``task.md`` sits in the sibling folder named by ``masterRef.path``.
    """
    assert ref.masterRef is not None  # guarded by the caller
    folder = ref.masterRef.path.rsplit("/", 1)[0] if "/" in ref.masterRef.path else ""
    target = f"{folder}/task.md" if folder else "task.md"
    return f"../{target}"


def _header_lines(doc: TaskDocument) -> list[str]:
    status = f"**Status:** {doc.status}"
    if doc.statusNote:  # descriptive suffix beside the strict enum (R4)
        status = f"{status} — {doc.statusNote}"
    lines = [
        status,
        f"**Repo:** {doc.repo}",
        f"**Type:** {doc.type}",
        f"**Created:** {doc.createdAt}",
    ]
    if doc.master:
        lines.append(f"**Master:** `{doc.master}`")
    if doc.orchestrates:  # the orchestration-command relation (L14); master-only by schema
        lines.append("**Orchestrates:** " + ", ".join(f"`{name}`" for name in doc.orchestrates))
    if doc.integrationBranch:
        lines.append(f"**Integration branch:** `{doc.integrationBranch}`")
    if doc.executionNature:
        lines.append(f"**Execution nature:** `{doc.executionNature}`")
    if doc.seats:  # the sprint's first-class seats (L14-R3); sprint-only by schema
        lines += _seat_lines(doc.seats)
    lines += [
        f"**{note.label}:** {note.value}" for note in doc.headerNotes
    ]  # extra header lines (R4)
    return lines


def _seat_lines(seats: list[SprintSeat]) -> list[str]:
    """The seats header block: one line per seat under a ``**Seats:**`` banner."""
    lines = ["**Seats:**"]
    for seat in seats:
        label = f" — {seat.label}" if seat.label else ""
        identity = f" · `{seat.identity}`" if seat.identity else ""
        lines.append(f"- **{seat.role}** ({seat.state}){label}{identity}")
    return lines


def _execution_graph_lines(
    graph: SprintExecutionGraph, titles: SprintGraphTitles | None = None
) -> list[str]:
    """The ``## Execution Graph`` section body: mermaid diagram + machine lists.

    The deterministic mermaid ``flowchart TD`` renders one subgraph per master
    box (labeled with the master title), one node per leaf (truncated title),
    atomic masters as single lump nodes, and labeled edges -- ordered by
    derived wave then node order so re-renders are byte-stable (L12-R1). The
    compact machine-readable list form (Nodes / Dependencies / Derived Waves)
    stays alongside the diagram.
    """
    nodes = [f"- {_graph_node_label(node)}" for node in graph.nodes]
    edges = [
        f"- {_graph_node_label(graph.resolve_endpoint(edge.predecessor))} → "
        f"{_graph_node_label(graph.resolve_endpoint(edge.successor))} — {edge.reason}"
        for edge in graph.edges
    ] or ["- _None._"]
    waves = [
        f"- Wave {index}: " + ", ".join(_graph_node_label(node) for node in wave)
        for index, wave in enumerate(graph.derived_waves(), start=1)
    ]
    return [
        "```mermaid",
        "flowchart TD",
        *_mermaid_node_lines(graph, titles),
        *_mermaid_edge_lines(graph),
        "```",
        "",
        "### Nodes",
        "",
        *nodes,
        "",
        "### Dependencies",
        "",
        *edges,
        "",
        "### Derived Waves",
        "",
        *waves,
    ]


def _graph_node_label(node: SprintExecutionNode) -> str:
    """Backticked node key; a segment adds its leaf list as the qualifier (L11-R9)."""
    if node.kind != "segment":
        return f"`{node.ref.key}`"
    leafs = ", ".join(f"`{leaf}`" for leaf in node.leafIds)
    return f"`{node.ref.key}` (leafs: {leafs})"


_MERMAID_MASTER_TITLE_MAX = 80
_MERMAID_LEAF_TITLE_MAX = 56
_MERMAID_REASON_MAX = 120


def _mermaid_label(text: str, limit: int) -> str:
    """One mermaid-quoted label: whitespace-collapsed, truncated, entity-escaped.

    Quotes and pipes are escaped as HTML character references so a reason or
    title containing them cannot break the ``-->|label|`` or ``["label"]``
    syntax; the rendered meaning is preserved.
    """

    cleaned = " ".join(text.split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned.replace('"', "&#34;").replace("|", "&#124;")


def _mermaid_node_id(index: int) -> str:
    return f"n{index}"


def _mermaid_subgraph_id(first_index: int) -> str:
    return f"sg{first_index}"


def _mermaid_leaf_id(leaf: str) -> str:
    return "leaf_" + re.sub(r"[^A-Za-z0-9_-]", "_", leaf)


def _mermaid_master_order(graph: SprintExecutionGraph) -> list[TaskDocumentRef]:
    """Masters in emission order: first derived-wave appearance, then declaration.

    A master whose segments span several waves is emitted once, at the wave of
    its first segment, so its subgraph reads as one box.
    """

    ordered: list[TaskDocumentRef] = []
    for wave in graph.derived_waves():
        for node in wave:
            if node.ref not in ordered:
                ordered.append(node.ref)
    return ordered


def _mermaid_node_lines(graph: SprintExecutionGraph, titles: SprintGraphTitles | None) -> list[str]:
    index_of = {node: index for index, node in enumerate(graph.nodes)}
    wave_of = {node: index for index, wave in enumerate(graph.derived_waves()) for node in wave}
    lines: list[str] = []
    for ref in _mermaid_master_order(graph):
        master_nodes = graph.nodes_for(ref)
        master_title = titles.master_titles.get(ref.key, ref.key) if titles else ref.key
        label = _mermaid_label(master_title, _MERMAID_MASTER_TITLE_MAX)
        if all(node.kind == "master" for node in master_nodes):
            lines.append(f'{_mermaid_node_id(index_of[master_nodes[0]])}["{label}"]')
            continue
        lines.append(f'subgraph {_mermaid_subgraph_id(index_of[master_nodes[0]])}["{label}"]')
        lines.extend(_mermaid_segment_lines(master_nodes, wave_of, index_of, titles))
        lines.append("end")
    return lines


def _mermaid_segment_lines(
    master_nodes: list[SprintExecutionNode],
    wave_of: dict[SprintExecutionNode, int],
    index_of: dict[SprintExecutionNode, int],
    titles: SprintGraphTitles | None,
) -> list[str]:
    """One leaf node line per segment leaf, ordered by wave then declaration."""
    lines: list[str] = []
    for segment in sorted(master_nodes, key=lambda node: (wave_of[node], index_of[node])):
        for leaf in segment.leafIds:
            leaf_title = titles.leaf_titles.get(leaf, leaf) if titles else leaf
            leaf_label = _mermaid_label(f"{leaf} — {leaf_title}", _MERMAID_LEAF_TITLE_MAX)
            lines.append(f'{_mermaid_leaf_id(leaf)}["{leaf_label}"]')
    return lines


def _mermaid_edge_lines(graph: SprintExecutionGraph) -> list[str]:
    index_of = {node: index for index, node in enumerate(graph.nodes)}
    lines: list[str] = []
    for edge in graph.edges:
        predecessor = graph.resolve_endpoint(edge.predecessor)
        successor = graph.resolve_endpoint(edge.successor)
        reason = _mermaid_label(edge.reason, _MERMAID_REASON_MAX)
        lines.append(
            f"{_mermaid_endpoint_id(index_of, predecessor, edge.predecessor)} -->|{reason}| "
            f"{_mermaid_endpoint_id(index_of, successor, edge.successor)}"
        )
    return lines


def _mermaid_endpoint_id(
    index_of: dict[SprintExecutionNode, int],
    resolved: SprintExecutionNode,
    endpoint: TaskDocumentRef | SprintExecutionEndpoint,
) -> str:
    """The diagram id an edge endpoint points at.

    A leaf-sampling endpoint names its exact leaf node; a bare ref resolves to
    the lump node, or to the master's subgraph when the master is segmented.
    """

    if isinstance(endpoint, SprintExecutionEndpoint) and endpoint.leafId is not None:
        return _mermaid_leaf_id(endpoint.leafId)
    if resolved.kind == "master":
        return _mermaid_node_id(index_of[resolved])
    return _mermaid_subgraph_id(index_of[resolved])


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
        if step.outcome or step.substeps or step.disposition:
            block += [
                "",
                f"- [{_checkbox(step.status)}] {step.outcome or step.title}"
                f"{_disposition_suffix(step.disposition)}",
            ]
            for sub in step.substeps:
                suffix = f" — {sub.note}" if sub.note else ""
                block.append(
                    f"  - [{_checkbox(sub.status)}] {sub.title}{suffix}"
                    f"{_disposition_suffix(sub.disposition)}"
                )
        blocks.append(block)
    return _join_blocks(blocks)


def _disposition_suffix(disposition: StepDisposition | None) -> str:
    return f" — SKIPPED: {disposition.reason}" if disposition is not None else ""


def _code_example_lines(examples: list[CodeExample], note: str | None = None) -> list[str]:
    if not examples:
        # An explicit note (e.g. "Drafted at the plan gate.") says examples are deferred, not
        # unneeded; with no note the section reads as genuinely needing none (R3).
        return [note or "No code examples are needed for this task."]
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


def _route_review_lines(review: RouteReviewRecord | None) -> list[str]:
    if review is None:
        return ["_No candidate-bound route review recorded._"]
    rows = [
        f"| {_cell(route.route)} | {route.verdict} | `{_cell(route.evidenceRef)}` |"
        for route in review.routes
    ]
    return [
        f"**Candidate tree:** `{review.candidateTree}`",
        f"**Overall verdict:** {review.verdict}",
        f"**Verdict artifact:** `{review.verdictRef}`",
        f"**Reviewed:** {review.reviewedAt}",
        "",
        "| Major route | Verdict | Evidence |",
        "| --- | --- | --- |",
        *rows,
    ]


def _cell(text: str) -> str:
    return text.replace("\n", " ").replace("|", "\\|").strip()
