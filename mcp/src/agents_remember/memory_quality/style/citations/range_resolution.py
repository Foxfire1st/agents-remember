"""Validate anchored citations in memory tables and prose.

Both ``| Finding | Anchor | Source |`` rows and ``cit:`` prose use explicit anchors and
``path:start-end`` sources. Every resolved range must be in bounds, and every anchor must
occur in at least one pooled range. Both finding classes gate across the whole memory tree.

False-positive boundaries:

1. Identifier matching uses whole-name boundaries.
2. Non-identifier backtick spans are unchecked; use a quoted-literal anchor.
3. Presence is sufficient; an anchor may be cited at a use rather than a definition.
4. Multiple ranges are pooled for anchor presence but bounds-check independently.
5. A claim with no resolved source reports that count and is not measured against another
   file.
6. A row with neither anchor nor source is an empty-state row, not a citation; either half
   alone is malformed.
7. Fenced tables/prose and ``cit:`` inside code spans are skipped.
8. Parenthesized leaf shorthand such as ``(L4)`` is not a citation.

Dependency sources that resolve in neither tree are counted but cannot be range-verified.
Presence also cannot prove that an anchor was well chosen. Repair findings in the memory
document by correcting the explicit source/range or anchor; never widen the checker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import rel
from agents_remember.memory_quality.style.citations import (
    cells,
    extents,
    model,
    prose,
    source_index,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees, operation_trees
from agents_remember.memory_quality.style.finding import QualityFinding, check_result

CHECK_NAME = "style.citations.range_resolution"
NEAR_MISS_LIMIT = 3

CURATOR_REMEDIATION = (
    "This is a memory document, not code: nothing is broken in the build, the CARD is now "
    "wrong about the source, and a reader it misleads gets no other warning because a "
    "stale citation renders exactly like a correct one. Clear it in the memory repository."
)
ANCHOR_GRAMMAR = (
    "An anchor is a backticked code identifier, a backticked `#`-prefixed markdown "
    "heading, or a double-quoted literal string, and its text must occur inside one of the "
    "claim's ranges."
)
SOURCE_GRAMMAR = (
    "A source is `path:start-end` in plain text, repo-relative to the code repository or "
    "to the memory repository -- never a markdown link, never absolute, never with `../`. "
    "Separate several with ';'."
)


@dataclass
class Sources:
    """Line-cached reads of the code and memory files a run touches.

    ``view`` is the same cache one level up: an anchor's extent needs the file PARSED, and a
    run asks about the same file once per anchor. Measured over this repository, re-parsing
    per anchor cost 10.5s of a migration pass and all but 2s of it was the same few hundred
    files being read again.
    """

    cache: dict[Path, list[str]] = field(default_factory=dict)
    views: dict[Path, extents.FileView] = field(default_factory=dict)

    def lines(self, path: Path) -> list[str]:
        if path not in self.cache:
            self.cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return self.cache[path]

    def view(self, path: Path, relative: str) -> extents.FileView:
        if path not in self.views:
            self.views[path] = extents.FileView(path=relative, lines=self.lines(path))
        return self.views[path]

    def count(self, path: Path) -> int:
        return len(self.lines(path))

    def body(self, path: Path, start: int, end: int) -> str:
        return "\n".join(self.lines(path)[max(0, start - 1) : end])


@dataclass
class Tally:
    """What the run measured, beside the findings -- see modes 2, 5 and the ceiling."""

    documents: int = 0
    tables: int = 0
    superseded_tables: int = 0
    rows: int = 0
    prose_citations: int = 0
    citations: int = 0
    unresolved: int = 0
    rows_without_citation: int = 0
    unchecked_spans: int = 0
    unchecked_prose_ranges: int = 0


@dataclass
class Run:
    """Everything one document sweep carries, including its immutable source index.

    ``absent`` is held to the end so its anchors are queried as one index batch. The shared
    generation already contains every source location; no document-scoped run acquires or
    tokenizes the source tree again.
    """

    trees: Trees
    index: source_index.RepositoryIndex
    sources: Sources
    tally: Tally
    absent: list[tuple[ClaimScope, model.Anchor]] = field(default_factory=list)


@dataclass(frozen=True)
class Resolved:
    """One citation and the file it named."""

    citation: model.Citation
    file: Path


@dataclass(frozen=True)
class ClaimScope:
    """One row, what it resolved to, and how to read those files."""

    document: str
    claim: model.Claim
    resolved: tuple[Resolved, ...]
    sources: Sources

    def bodies(self) -> list[tuple[Resolved, str]]:
        return [
            (one, self.sources.body(one.file, one.citation.start, one.citation.end))
            for one in self.resolved
        ]


def containing_identifiers(symbol: str, body: str) -> list[str]:
    """Longer identifiers in ``body`` that carry ``symbol`` -- mode 1's evidence."""
    pattern = re.compile(rf"[A-Za-z0-9_$]*{re.escape(symbol)}[A-Za-z0-9_$]*")
    found = {match.group(0) for match in pattern.finditer(body)} - {symbol}
    return sorted(found)[:NEAR_MISS_LIMIT]


def elsewhere_in_file(anchor: model.Anchor, lines: list[str]) -> list[int]:
    """Every line of the file holding the anchor, capped -- the fix, usually."""
    return [index + 1 for index, line in enumerate(lines) if model.occurs_in(anchor, line)][
        :NEAR_MISS_LIMIT
    ]


def anchor_evidence(anchor: model.Anchor, scope: ClaimScope) -> str:
    """Where the anchor actually is, or what the range holds instead.

    The measured defect shape is a range that starts correctly and stops short of the
    construct its own claim names, so naming the lines that DO carry it puts the fix in the
    finding. Every file the row resolved is searched, not only the first.
    """
    for one in scope.resolved:
        found = elsewhere_in_file(anchor, scope.sources.lines(one.file))
        if found:
            return f"{one.citation.path} carries it at line(s) {found}"
    if anchor.kind == model.SYMBOL:
        inside = containing_identifiers(
            anchor.text, "\n".join(body for _one, body in scope.bodies())
        )
        if inside:
            return f"the range holds {inside}, which carry the name but are not it"
    paths = ", ".join(one.citation.path for one in scope.resolved)
    return f"{paths} does not carry it anywhere"


def finding(scope_document: str, line: int, code: str, message: str) -> QualityFinding:
    return QualityFinding(
        check=CHECK_NAME,
        path=scope_document,
        line=line,
        severity="error",
        code=code,
        message=message,
    )


def table_format_finding(document: str, table: cells.CitationTable) -> QualityFinding:
    """A table still in the superseded shape -- the whole migration, named in one message.

    All three edits together, because the first two alone stop GFM treating the construct
    as a table at all: the findings then drop to zero and the halfway state is
    indistinguishable from success.
    """
    return finding(
        document,
        table.line,
        "citation_table_columns_wrong",
        f"Evidence table has columns {list(table.columns)}; the format is "
        f"`Finding | Anchor | Source`. Rewrite it, and do all three parts in one edit "
        f"because the first two alone stop GFM treating this as a table: (1) the header "
        f"row becomes `| Finding | Anchor | Source |`; (2) the DELIMITER row directly "
        f"beneath it becomes `| --- | --- | --- |`, matching that cell count; (3) each of "
        f"the {table.row_count} body row(s) becomes "
        f"`| <claim> | <anchor> | <path>:<start>-<end> |`. {ANCHOR_GRAMMAR} {SOURCE_GRAMMAR} "
        f"{CURATOR_REMEDIATION}",
    )


def malformed_findings(document: str, claim: model.Claim) -> list[QualityFinding]:
    return [
        finding(
            document,
            claim.line,
            "citation_source_malformed",
            f"Source cell holds '{one}', which is not a citation. {SOURCE_GRAMMAR} "
            f"{CURATOR_REMEDIATION}",
        )
        for one in claim.malformed
    ]


def pairing_findings(document: str, claim: model.Claim) -> list[QualityFinding]:
    """A claim holding one half of a citation. Neither half means anything alone.

    A missing anchor is knowable from the Anchor cell even when Source is malformed, so
    those two independently true defects are both reported in one pass (L6-R15). Bounds
    and containment are different: neither is knowable until a Source parses and resolves.

    A malformed source is not also reported as MISSING. It exists and needs rewriting; the
    anchor is the separate half that may independently be absent.
    """
    if (claim.citations or claim.malformed) and not claim.anchors:
        sources = [one.text for one in claim.citations] + list(claim.malformed)
        return [
            finding(
                document,
                claim.line,
                "citation_anchor_missing",
                f"This row has Source content {sources} and names no anchor, "
                f"so nothing says what those lines are supposed to contain. {ANCHOR_GRAMMAR} "
                f"A backticked span that is neither identifier-shaped nor `#`-prefixed is "
                f"not an anchor -- write it as a double-quoted literal. {CURATOR_REMEDIATION}",
            )
        ]
    if claim.anchors and not claim.citations and not claim.malformed:
        return [
            finding(
                document,
                claim.line,
                "citation_source_missing",
                f"This claim names {[one.written for one in claim.anchors]} and cites no lines. "
                f"There is no rangeless row: a claim that cannot be anchored to a range is "
                f"deleted. {SOURCE_GRAMMAR} {CURATOR_REMEDIATION}",
            )
        ]
    return []


def repeated_sources(claim: model.Claim) -> tuple[tuple[str, int], ...]:
    """Exact repeated source texts within this Claim, in first-seen order."""
    counts: dict[str, int] = {}
    for citation in claim.citations:
        counts[citation.text] = counts.get(citation.text, 0) + 1
    return tuple((source, count) for source, count in counts.items() if count > 1)


def duplicate_source_findings(document: str, claim: model.Claim) -> list[QualityFinding]:
    """Exact repeated sources within this Claim; separate Claims remain independent."""
    return [
        finding(
            document,
            claim.line,
            "citation_source_duplicate",
            f"This one claim repeats exact Source {source!r} {count} times. Keep one copy: "
            f"repetition adds no pooled evidence and makes scoped normalization grow. The same "
            f"source may still appear once in each separate prose claim. {CURATOR_REMEDIATION}",
        )
        for source, count in repeated_sources(claim)
    ]


def out_of_bounds(scope: ClaimScope) -> list[Resolved]:
    """Every citation of this claim whose range runs past the end of its own file."""
    return [one for one in scope.resolved if one.citation.end > scope.sources.count(one.file)]


def unsatisfied(scope: ClaimScope) -> list[model.Anchor]:
    """Every anchor of this claim that no resolved range holds.

    With nothing resolved, no anchor is KNOWN to be unheld, so the answer is empty rather than
    every anchor. Returning every anchor made ``fixer.failing`` treat a third-party citation --
    a quoted literal against a dependency whose path resolves in neither tree by design -- as
    permanently repairable; since ``ok`` is ``not refused and not remaining``, ``--fix`` could
    then never report ok on a tree using the very form this leaf mandates for those claims.
    The checker has always guarded this at its own call site; the guard belongs here instead,
    so a third caller cannot reintroduce the bug.
    """
    if not scope.resolved:
        return []
    bodies = [body for _one, body in scope.bodies()]
    return [
        anchor
        for anchor in scope.claim.anchors
        if not any(model.occurs_in(anchor, body) for body in bodies)
    ]


def bounds_findings(scope: ClaimScope) -> list[QualityFinding]:
    """A range past the end of the file its own citation names."""
    return [
        finding(
            scope.document,
            scope.claim.line,
            "citation_range_out_of_bounds",
            f"Citation {one.citation.text} ends "
            f"{one.citation.end - scope.sources.count(one.file)} line(s) past the end of "
            f"{one.citation.path}, which has {scope.sources.count(one.file)}. Re-read the "
            f"cited construct and write the range it occupies now, or delete the citation "
            f"if the construct is gone. {CURATOR_REMEDIATION}",
        )
        for one in out_of_bounds(scope)
    ]


def absent_findings(run: Run) -> list[QualityFinding]:
    """The anchors no range held, each naming EVERY location in the tree that does hold it.

    One indexed batch for the whole sweep (L6-R28): a finding that only says the anchor is
    missing leaves the reader to find where it went, which is the thing models are worst at.
    Naming the locations turns "figure out where this went" into "pick from these three".
    """
    anchors = tuple(anchor for _scope, anchor in run.absent)
    seen = symbol_index.locate(anchors, run.trees, index=run.index)
    return [
        finding(
            scope.document,
            scope.claim.line,
            "citation_anchor_absent_from_range",
            f"The claim names {anchor.written} and cites "
            f"{', '.join(one.citation.text for one in scope.resolved)}, but no line in those "
            f"ranges holds it -- {anchor_evidence(anchor, scope)}. In the tree, "
            f"{symbol_index.described(anchor, seen[anchor])}. Two edits clear this and both "
            f"state something the author knows: widen the range to the lines that carry it, "
            f"or name the anchor the range actually holds. {CURATOR_REMEDIATION}",
        )
        for scope, anchor in run.absent
    ]


def vanished_finding(document: str, claim: model.Claim, citation: model.Citation) -> QualityFinding:
    """A source into THIS repository at a path that no longer exists.

    The failure a resolver exists for, and the one the bounds half cannot see: a file with
    no lines is not out of bounds, it is gone. One package split left 18 citations pointing
    at a file that no longer existed and nothing noticed for months. A source into a
    dependency is the other reason a path resolves to nothing and is deliberately not this
    -- see ``Trees.ours``.
    """
    return finding(
        document,
        claim.line,
        "citation_source_vanished",
        f"Citation {citation.text} names {citation.path}, which does not exist in either "
        f"tree, although '{citation.path.split('/')[0]}' does -- so this points INTO this "
        f"repository at a location that has moved or been deleted. Run the citation fixer: "
        f"it repoints a pure move, where the anchor kept its name and changed file. If it "
        f"declines, the anchor was renamed or deleted and the CLAIM is what needs re-reading. "
        f"{CURATOR_REMEDIATION}",
    )


def claim_findings(document: str, claim: model.Claim, run: Run) -> list[QualityFinding]:
    found = (
        malformed_findings(document, claim)
        + pairing_findings(document, claim)
        + duplicate_source_findings(document, claim)
    )
    resolved: list[Resolved] = []
    for citation in claim.citations:
        target = run.trees.resolve(citation.path)
        if target is None:
            run.tally.unresolved += 1
            if run.trees.ours(citation.path):
                found.append(vanished_finding(document, claim, citation))
            continue
        resolved.append(Resolved(citation=citation, file=target))
    run.tally.citations += len(resolved)
    if not resolved:
        run.tally.rows_without_citation += bool(claim.citations)
        return found
    scope = ClaimScope(
        document=document, claim=claim, resolved=tuple(resolved), sources=run.sources
    )
    run.absent.extend((scope, anchor) for anchor in unsatisfied(scope))
    return found + bounds_findings(scope)


def prose_findings(document: str, scan: prose.ProseScan, run: Run) -> list[QualityFinding]:
    """The prose serialisation: ``cit:`` constructs, and the spelling that preceded them."""
    run.tally.prose_citations += len(scan.claims)
    run.tally.unchecked_prose_ranges += scan.unchecked_ranges
    found = [
        finding(
            document,
            one.line,
            "citation_prose_malformed",
            f"'{one.text}' opens a `cit:` citation that does not parse. {prose.GRAMMAR} "
            f"{CURATOR_REMEDIATION}",
        )
        for one in scan.unparsed
    ]
    found += [
        finding(
            document,
            one.line,
            "citation_prose_not_in_cit_form",
            f"'{one.text}' cites lines in the superseded prose spelling. Rewrite it as "
            + (
                "`cit:([<the anchor beside it>], <path>:<start>-<end>)`. "
                if one.anchored
                else "`cit:([<anchor>], <path>:<start>-<end>)` -- this one names NO anchor "
                "and no file, so it denotes nothing until both are written. "
            )
            + f"{prose.GRAMMAR} {CURATOR_REMEDIATION}",
        )
        for one in scan.superseded
    ]
    for claim in scan.claims:
        run.tally.unchecked_spans += claim.unchecked_spans
        found.extend(claim_findings(document, claim, run))
    return found


def misplaced_findings(
    document: str, lines: list[str], occupied: set[int] | None = None
) -> list[QualityFinding]:
    """The prose form written into a table cell -- the wrong serialisation, not a defect-free row.

    It can never fire once the tree is migrated, and that is fine: it fires on the way there
    and on the next author who mixes the two.
    """
    return [
        finding(
            document,
            one.line,
            "citation_prose_form_in_table_cell",
            f"'{one.text}' writes the prose citation form into a table cell. A table row "
            f"carries its citation in the Anchor and Source columns; delete the `cit:` and "
            f"fill the columns. {SOURCE_GRAMMAR} {CURATOR_REMEDIATION}",
        )
        for one in prose.misplaced_in_tables(lines, occupied)
    ]


def check_document(document: Path, onboarding_root: Path, run: Run) -> list[QualityFinding]:
    relative = rel(document, onboarding_root)
    lines = run.sources.lines(document)
    scanned = cells.scan_tables(lines)
    occupied = cells.table_lines(lines, scanned)
    found: list[QualityFinding] = []
    for table in cells.citation_tables(lines, scanned):
        run.tally.tables += 1
        run.tally.rows += table.row_count
        if not table.conforming:
            run.tally.superseded_tables += 1
            found.append(table_format_finding(relative, table))
            continue
        for claim in table.rows:
            run.tally.unchecked_spans += claim.unchecked_spans
            found.extend(claim_findings(relative, claim, run))
    found += misplaced_findings(relative, lines, occupied)
    return found + prose_findings(relative, prose.scan(lines, occupied), run)


def overshoot(one: QualityFinding) -> int:
    """How far past the end of the file a bounds finding reaches -- for worst-first order."""
    match = re.search(r"ends (\d+) line\(s\) past", one.message)
    return int(match.group(1)) if match else 0


def worst_first(findings: list[QualityFinding]) -> list[QualityFinding]:
    """The complete offender list, deepest overrun first (L6-R15).

    A shape check that reports in file order makes the reader who has budget for three
    fixes pick the first three rather than the worst three.
    """
    return sorted(findings, key=lambda one: (-overshoot(one), one.code, one.path, one.line))


def check_onboarding_root(
    onboarding_root: Path,
    code_repository_root: Path | Trees | None = None,
    *,
    only: str | None = None,
    index: source_index.RepositoryIndex | None = None,
    expected_snapshot: str | None = None,
) -> dict[str, Any]:
    """Every citation in the memory tree, resolved against both repositories.

    ``code_repository_root`` is not optional in substance -- without the code tree there is
    no file to be in bounds of, and every caller in the product supplies it from the same
    place the drift check gets it. The ``None`` arm exists so a caller with no code checkout
    gets a result that SAYS the citations were not resolved, rather than a green tick that
    means nothing.
    """
    source_index.validate_operation_scope(
        only,
        expected_snapshot,
        leased_index=index is not None,
    )
    if code_repository_root is None:
        return {
            **check_result(check=CHECK_NAME, files_checked=0, findings=[]),
            "status": "no-code-repository-root",
        }
    documents = model.documents_in(onboarding_root, only)
    trees = operation_trees(onboarding_root, code_repository_root)
    if index is None:
        with source_index.open_repository_index(
            trees, expected_snapshot=expected_snapshot
        ) as acquired:
            return _check_documents(documents, onboarding_root, trees, acquired)
    return _check_documents(documents, onboarding_root, trees, index)


def _check_documents(
    documents: list[Path],
    onboarding_root: Path,
    trees: Trees,
    index: source_index.RepositoryIndex,
) -> dict[str, Any]:
    """Check the selected documents against one already-validated source generation."""
    run = Run(
        trees=trees,
        index=index,
        sources=Sources(),
        tally=Tally(),
    )
    findings: list[QualityFinding] = []
    for document in documents:
        run.tally.documents += 1
        findings.extend(check_document(document, onboarding_root, run))
    findings.extend(absent_findings(run))
    return {
        **check_result(
            check=CHECK_NAME, files_checked=run.tally.documents, findings=worst_first(findings)
        ),
        "status": "checked",
        "citationTables": run.tally.tables,
        "tablesNotInCurrentFormat": run.tally.superseded_tables,
        "citationRows": run.tally.rows,
        "proseCitations": run.tally.prose_citations,
        "resolvedCitations": run.tally.citations,
        "unresolvedSources": run.tally.unresolved,
        "rowsWithNoResolvedCitation": run.tally.rows_without_citation,
        "uncheckedSpans": run.tally.unchecked_spans,
        "uncheckedProseRanges": run.tally.unchecked_prose_ranges,
        "sourceIndex": index.telemetry(),
    }
