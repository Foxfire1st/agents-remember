"""MIK-R80 round-1 repair: actual Markdown headings and the trailing citation region.

L80-CODE-R1-F01 (nested and indented leaf/date headings refuse; closing-hash purpose
and escape/code controls pass) and L80-CODE-R1-F02 (only a trailing contiguous
citation region passes). Each test names the sealed ID it proves. Trees are the
MIK-R21 Doc14 fixtures (``knowledge_validator_test_support``) with the route overview
replaced; a test changes one thing and checks that exactly the owning rule answers,
with the file, the field and the rule named.
"""

from __future__ import annotations

from typing import Any

from agents_remember.memory_quality.knowledge_validator import (
    KnowledgeValidationError,
    ValidationReport,
    require_valid_commit,
    validate_tree,
)
from knowledge_validator_test_support import (
    ROUTE_CARD,
    code,
    fixture_tree_files,
    tree,
)

PURPOSE_FIRST = "R80.1-purpose-first"
NO_LEAF_SECTIONS = "R80.2-no-leaf-sections"
CITATIONS_AT_END = "R80.7-citations-at-end"

CONFORMING = (
    "# worktrees\n"
    "\n"
    "## Governing Overview\n"
    "\n"
    "[mcp overview](../overview.md)\n"
    "\n"
    "## What This Area Is\n"
    "\n"
    "The route's files pair each delivery with its memory candidate.\n"
    "\n"
    "## Evidence\n"
    "\n"
    "- The route's files are listed with their anchors [1].\n"
)


def _overview(text: str) -> dict[str, bytes]:
    files = fixture_tree_files()
    files[ROUTE_CARD] = text.encode("utf-8")
    return files


def _validate(files: dict[str, bytes], **kwargs: Any) -> ValidationReport:
    kwargs.setdefault("code", code())
    kwargs.setdefault("leaf_publication", True)
    return validate_tree(tree(files), **kwargs)


def _only(report: ValidationReport, rule: str) -> list[str]:
    assert {violation.rule for violation in report.refusals} == {rule}, report.render()
    return [violation.render() for violation in report.refusals]


# --------------------------------------------------------------------------------------------------
# L80-CODE-R1-F01: nested and indented headings are headings; closing hashes are syntax
# --------------------------------------------------------------------------------------------------


def test_order_rules_are_silent_without_leaf_publication() -> None:
    """Landing check: scaffold stubs adopt; the order refuses at publication routes."""

    for text in (
        "# Overview\n",
        "# overview\n\nA thin root overview.\n",
        "# Repository overview\n\n## Domain terms\n\n| Term | Meaning |\n",
    ):
        report = validate_tree(tree(_overview(text)), code=code())

        assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_h3_leaf_subsection_is_refused() -> None:
    """F01: a ``###`` section named after a leaf refuses R80.2."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        "## How It Is Built\n"
        "\n"
        "The route pairs each delivery with its memory candidate.\n"
        "\n"
        "### 260928-MIK-L99\n"
        "\n"
        "Leaf history.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- The route's files are listed with their anchors [1].\n",
    )

    [line] = _only(_validate(files), NO_LEAF_SECTIONS)

    assert line.startswith(f"{ROUTE_CARD}: ### 260928-MIK-L99: [{NO_LEAF_SECTIONS}]")
    assert "named after a leaf" in line


def test_h3_date_subsection_is_refused() -> None:
    """F01: a ``###`` section named after a date refuses R80.2."""

    files = _overview(
        CONFORMING.replace("## Evidence", "### 2026-10-11\n\nDated history.\n\n## Evidence")
    )

    [line] = _only(_validate(files), NO_LEAF_SECTIONS)

    assert "named after a date" in line and "2026-10-11" in line


def test_indented_leaf_heading_is_refused() -> None:
    """F01: one legal ATX indenting space does not hide a leaf section."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        " ## 260928-MIK-L99\n"
        "\n"
        "Leaf history.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- The route's files are listed with their anchors [1].\n",
    )

    [line] = _only(_validate(files), NO_LEAF_SECTIONS)

    assert line.startswith(f"{ROUTE_CARD}: ## 260928-MIK-L99: [{NO_LEAF_SECTIONS}]")


def test_tab_indented_heading_is_code_not_a_section() -> None:
    """F01 control: a tab indent is a code block, never a heading."""

    files = _overview(
        CONFORMING.replace("## Evidence", "\t## 260928-MIK-L99\n\nLeaf history.\n\n## Evidence")
    )

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_escaped_heading_is_prose_not_a_section() -> None:
    """F01 control: a backslash-escaped ``\\##`` is prose."""

    files = _overview(
        CONFORMING.replace("## Evidence", "\\## 260928-MIK-L99\n\nLeaf history.\n\n## Evidence")
    )

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_closing_hash_purpose_passes() -> None:
    """F01 control: ``## What This Area Is ##`` names the purpose."""

    files = _overview(CONFORMING.replace("## What This Area Is\n", "## What This Area Is ##\n"))

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_closing_hash_governing_passes() -> None:
    """F01 control: ``## Governing Overview ##`` still skips to the purpose."""

    files = _overview(CONFORMING.replace("## Governing Overview\n", "## Governing Overview ##\n"))

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_document_title_is_not_a_section() -> None:
    """F01 reading: the packet forbids sections, not a title; the first H1 is skipped."""

    files = _overview(CONFORMING.replace("# worktrees\n", "# 260928-MIK-L99 notes\n", 1))

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


# --------------------------------------------------------------------------------------------------
# L80-CODE-R1-F02: the evidence region is trailing and contiguous
# --------------------------------------------------------------------------------------------------


def test_prose_after_final_citation_refuses() -> None:
    """F02: implementation prose after the last citation refuses R80.7 with its line."""

    files = _overview(
        CONFORMING + "\nThis describes further implementation after the citation.\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")
    assert "trailing citation region" in line


def test_subsection_after_final_citation_refuses() -> None:
    """F02: a subsection and its prose after the citations refuse R80.7."""

    files = _overview(
        CONFORMING + "\n### More Implementation\n"
        "\n"
        "This describes further implementation after the citation.\n",
    )

    lines = _only(_validate(files), CITATIONS_AT_END)

    assert len(lines) == 2
    assert any(": line 15: " in line for line in lines)
    assert any(": line 17: " in line for line in lines)


def test_split_citations_refuse_and_closeout_refuses() -> None:
    """F02: prose separating two citations refuses R80.7, and the closeout is refused."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- First citation [1].\n"
        "\n"
        "Implementation prose separates the citations.\n"
        "\n"
        "- Second citation [1].\n",
    )

    report = _validate(files)

    assert {violation.rule for violation in report.refusals} == {CITATIONS_AT_END}
    try:
        require_valid_commit(tree(files), bases=[], code=code(), leaf_publication=True)
    except KnowledgeValidationError as error:
        rendered = str(error)
    else:  # pragma: no cover - the refusal is the asserted behaviour
        raise AssertionError(f"closeout passed: {report.render()}")
    assert CITATIONS_AT_END in rendered


def test_intro_prose_before_first_citation_passes() -> None:
    """F02 control: the region starts at the first citation, not at the heading."""

    files = _overview(
        CONFORMING.replace(
            "## Evidence\n",
            "## Evidence\n\nThe following lines carry the route's evidence.\n",
        )
    )

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_fenced_example_after_citations_passes() -> None:
    """F02 control: fenced code is not prose, even with marker-shaped text."""

    files = _overview(
        CONFORMING + '\n```markdown\njournal[1] = "fenced code holds no marker"\n```\n',
    )

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_shown_escaped_example_bullet_in_region_refuses() -> None:
    """F02 (architect ruling): an explanation bullet inside the region is refused.

    The retained allowed value changes here by ruling point 4: inside the
    trailing region only citation lines, blanks and fenced code are legal.
    """

    files = _overview(
        CONFORMING + "\n- Write it as \\[2] if it is not a marker.\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_escaped_example_bullet_before_region_passes() -> None:
    """F02 (architect ruling): the same line before the region stays allowed."""

    files = _overview(
        CONFORMING.replace(
            "## What This Area Is\n",
            "## What This Area Is\n\n- Write it as \\[2] if it is not a marker.\n",
        )
    )

    report = _validate(files)

    assert [one.rule for one in report.violations if one.rule.startswith("R80.")] == []


def test_marker_example_digit_variant_in_region_refuses() -> None:
    """F02 (architect ruling): digit variants inside the region refuse as well."""

    files = _overview(
        CONFORMING + "\n- Write them as \\[3] when they are not markers.\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_implementation_sentence_as_bullet_with_marker_narration_refuses() -> None:
    """F02: narration supplying the word marker does not make prose an example."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- First citation [1].\n"
        "\n"
        "- Implementation prose separates the citations. The marker engine runs. \\[2]\n"
        "\n"
        "- Second citation [1].\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_trailing_sentence_as_bullet_with_marker_narration_refuses() -> None:
    """F02: trailing narration with the word marker plus code span still refuses."""

    files = _overview(
        CONFORMING + "\n- This describes further implementation after the citation. "
        "The marker engine runs. `signals[0]`\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_implementation_sentence_as_bullet_with_escaped_suffix_refuses() -> None:
    """F02: a `- ` prefix does not turn implementation prose into an explanation."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- First citation [1].\n"
        "\n"
        "- Implementation prose separates the citations. \\[2]\n"
        "\n"
        "- Second citation [1].\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_implementation_sentence_as_bullet_with_codespan_suffix_refuses() -> None:
    """F02: a `- ` prefix plus code span does not make prose an explanation either."""

    files = _overview(
        CONFORMING + "\n- This describes further implementation after the citation. `signals[0]`\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_interleaved_prose_with_shown_escaped_suffix_refuses() -> None:
    """F02: an appended escaped bracket does not turn prose into an example."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- First citation [1].\n"
        "\n"
        "Implementation prose separates the citations. \\[2]\n"
        "\n"
        "- Second citation [1].\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_trailing_prose_with_codespan_suffix_refuses() -> None:
    """F02: an appended code span does not turn trailing prose into an example."""

    files = _overview(
        CONFORMING + "\nThis describes further implementation after the citation. `signals[0]`\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_lone_trailing_subsection_refuses() -> None:
    """F02 control: a lone trailing subsection refuses R80.7 with its line."""

    files = _overview(CONFORMING + "\n### More Implementation\n")

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_trailing_subsection_with_escaped_marker_refuses() -> None:
    """F02: an escaped bracket does not turn a trailing subsection into a citation."""

    files = _overview(CONFORMING + "\n### More Implementation \\[2]\n")

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")
    assert "trailing citation region" in line


def test_trailing_subsection_with_codespan_marker_refuses() -> None:
    """F02: a code span does not turn a trailing subsection into a citation."""

    files = _overview(CONFORMING + "\n### More Implementation `signals[0]`\n")

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")


def test_interleaved_prose_with_hidden_escaped_marker_refuses() -> None:
    """F02: bracket text hidden in an HTML comment is not a shown example."""

    files = _overview(
        "# worktrees\n"
        "\n"
        "## Governing Overview\n"
        "\n"
        "[mcp overview](../overview.md)\n"
        "\n"
        "## What This Area Is\n"
        "\n"
        "The route's files pair each delivery with its memory candidate.\n"
        "\n"
        "## Evidence\n"
        "\n"
        "- First citation [1].\n"
        "\n"
        "Implementation prose separates the citations. <!-- \\[2] -->\n"
        "\n"
        "- Second citation [1].\n",
    )

    [line] = _only(_validate(files), CITATIONS_AT_END)

    assert line.startswith(f"{ROUTE_CARD}: line 15: [{CITATIONS_AT_END}]")
