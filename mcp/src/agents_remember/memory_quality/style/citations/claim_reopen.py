"""Reopen citation claims whose anchored evidence changed since verification.

Each claim resolves its anchor at ``lastVerifiedCommitHash`` and in the current tree, then
compares structural identities that omit layout, comments, line numbers, and delimiters.
Code sources use code history; memory-relative sources use the ledger-mapped memory
history; dependency sources use one concrete Python requirement version or exact npm
lockfile version selected by source ecosystem.

A detected change splits three ways. Absent or ambiguous anchors and unverifiable provenance
are hard findings -- the citation itself is broken. A construct that changed while its
citation stays CURRENT (the anchor resolves exactly once and the cited range covers the
current construct) is the report-only review surface: the pointer provably points at the new
content, clearing needs no commit, and whether the prose still holds is the curator's review
duty. Only a changed construct whose citation is not current -- or that never resolves
uniquely -- is an enforced reopened claim.

Known limit -- dishonest stamp: if verification metadata is advanced without reviewing
the changed construct, historical and current resolution see the same body. This check
detects changes after an honest verification point; it cannot prove that the stated
verification occurred.

False-positive boundaries:

1. Format-only Python or TypeScript reflow does not reopen.
2. Unrelated constructs in the same file are outside the claim.
3. Code-first source resolution matches the citation resolver; memory history is used
   only for a memory-resolved path.
4. A changed resolved dependency version reopens even when it remains inside a permissive
   installation range.
5. Zero or multiple anchor matches report invalid/ambiguous provenance and are never
   selected by location, similarity, or order.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    parse_table_metadata,
    rel,
)
from agents_remember.memory_quality.style.citations import (
    cells,
    claim_change_router,
    extents,
    model,
    prose,
    provenance,
    structures,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.finding import QualityFinding, check_result

CHECK_NAME = "style.citations.claim_reopen"
STAMP = re.compile(r"[0-9a-fA-F]{7,40}")
MISSING = "citation_provenance_missing"
INVALID = "citation_provenance_invalid"
REOPENED = "citation_claim_reopened"
# The unbounded first implementation completed this 1,168-document tree at 615,448 KiB RSS.
# A parsed revision is useful while nearby cards cite it, not for the lifetime of the sweep.
SOURCE_VIEW_CACHE_LIMIT = 128

CURATOR_REMEDIATION = (
    "Re-read this claim against the current anchored construct, correct or retain its wording, "
    "regenerate its range, and only then advance the card's verification stamp. Other claims "
    "in the same document are not reopened on this claim's account."
)
PROVENANCE_REMEDIATION = (
    "Restore a verifiable provenance before stamping the card: a real code commit, a ledger-"
    "mapped memory commit, or an exact resolved dependency version. Do not replace missing "
    "evidence with a plausible range or a permissive package pin."
)


@dataclass(frozen=True)
class LocalSource:
    citation: model.Citation
    kind: str
    current: list[str] | None
    historical: list[str] | None
    provenance_label: str


@dataclass(frozen=True)
class Candidate:
    source: LocalSource
    extent: extents.Extent
    fingerprint: str


@dataclass
class CurrentFiles:
    cache: dict[Path, list[str]] = field(default_factory=dict)

    def lines(self, path: Path) -> list[str]:
        if path not in self.cache:
            self.cache[path] = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return self.cache[path]


@dataclass
class SourceViews:
    """Parsed source revisions shared by every claim in one gate run."""

    cache: OrderedDict[tuple[str, str], tuple[extents.FileView, structures.StructuralView]] = field(
        default_factory=OrderedDict
    )

    def candidates(
        self,
        anchor: model.Anchor,
        source: LocalSource,
        *,
        historical: bool,
    ) -> list[Candidate]:
        lines = source.historical if historical else source.current
        if lines is None:
            return []
        revision = source.provenance_label if historical else f"working {source.kind}"
        key = (revision, source.citation.path)
        views = self.cache.get(key)
        if views is None:
            views = (
                extents.FileView(path=source.citation.path, lines=lines),
                structures.StructuralView(path=source.citation.path, lines=lines),
            )
            self.cache[key] = views
            if len(self.cache) > SOURCE_VIEW_CACHE_LIMIT:
                self.cache.popitem(last=False)
        else:
            self.cache.move_to_end(key)
        extent_view, structural = views
        return [
            Candidate(
                source=source,
                extent=extent,
                fingerprint=structural.fingerprint(anchor, extent),
            )
            for extent in extent_view.extents(anchor)
        ]


@dataclass
class Evaluation:
    code_commit: str
    trees: Trees
    histories: provenance.Histories
    current_files: CurrentFiles
    source_views: SourceViews
    router: claim_change_router.ClaimChangeRouter
    _memory_commit: provenance.Read | None = None

    def memory_commit(self) -> provenance.Read:
        if self._memory_commit is None:
            self._memory_commit = self.router.memory_commit(self.code_commit)
        return self._memory_commit

    def source(self, citation: model.Citation) -> tuple[LocalSource | None, str | None]:
        classified, error = claim_change_router.classify_citation(self.trees, citation)
        if classified is None:
            return None, error
        if classified.repository == "code":
            return self._code_source(citation, classified.target), None
        return self._memory_source(citation, classified.target)

    def _code_source(self, citation: model.Citation, target: Path | None) -> LocalSource:
        previous = self.histories.code.file(self.code_commit, citation.path)
        current = None if target is None else self.current_files.lines(target)
        return LocalSource(
            citation=citation,
            kind="code commit",
            current=current,
            historical=None if previous.text is None else previous.text.splitlines(),
            provenance_label=f"code commit {self.code_commit}",
        )

    def _memory_source(
        self, citation: model.Citation, target: Path | None
    ) -> tuple[LocalSource | None, str | None]:
        mapped = self.memory_commit()
        if mapped.text is None:
            return None, mapped.error
        previous = self.histories.memory.file(mapped.text, citation.path)
        current = None if target is None else self.current_files.lines(target)
        return (
            LocalSource(
                citation=citation,
                kind="memory commit",
                current=current,
                historical=None if previous.text is None else previous.text.splitlines(),
                provenance_label=f"memory commit {mapped.text}",
            ),
            previous.error,
        )


def claims_in(document: Path) -> tuple[list[str], tuple[model.Claim, ...]]:
    lines = document.read_text(encoding="utf-8", errors="replace").splitlines()
    scanned = cells.scan_tables(lines)
    occupied = cells.table_lines(lines, scanned)
    claims = [
        claim
        for table in cells.citation_tables(lines, scanned)
        if table.conforming
        for claim in table.rows
        if claim.anchors and claim.citations
    ]
    claims.extend(
        claim for claim in prose.scan(lines, occupied).claims if claim.anchors and claim.citations
    )
    return lines, tuple(claims)


def finding(
    document: str,
    claim: model.Claim,
    code: str,
    message: str,
) -> QualityFinding:
    return QualityFinding(
        check=CHECK_NAME,
        path=document,
        line=claim.line,
        severity="error",
        code=code,
        message=message,
    )


def provenance_finding(
    document: str,
    claim: model.Claim,
    code: str,
    detail: str,
) -> QualityFinding:
    return finding(
        document,
        claim,
        code,
        f"This claim cannot be compared with its verification provenance: {detail}. "
        f"Anchors: {[anchor.written for anchor in claim.anchors]}. "
        f"Sources: {[citation.text for citation in claim.citations]}. "
        f"{PROVENANCE_REMEDIATION}",
    )


def changed_finding(
    document: str,
    claim: model.Claim,
    details: list[str],
) -> QualityFinding:
    return finding(
        document,
        claim,
        REOPENED,
        f"This claim's evidence changed after verification: {'; '.join(details)}. "
        f"{CURATOR_REMEDIATION}",
    )


def surfaced_finding(
    document: str,
    claim: model.Claim,
    details: list[str],
) -> QualityFinding:
    """The review surface, never a blocker: the evidence changed AND the citation is current.

    Currency means the anchor still resolves exactly once and some cited range still contains
    the current construct's declaration line, so the pointer provably points at the new
    content. Whether the prose still describes it correctly is the curator's review duty,
    surfaced here for the relay in the check's ``surfacedFindings`` bucket -- the check detects
    change, it cannot prove a review, and it does not pretend one happened.
    """
    return QualityFinding(
        check=CHECK_NAME,
        path=document,
        line=claim.line,
        severity="warning",
        code=REOPENED,
        message=(
            f"This claim's evidence changed after verification, and the citation is current "
            f"(anchor resolves and some cited range still holds the changed construct's "
            f"declaration): {'; '.join(details)}. "
            "Curator review confirms the wording still holds; no citation repair is needed."
        ),
    )


def selected_current(
    anchor: model.Anchor,
    sources: list[LocalSource],
    views: SourceViews,
) -> list[Candidate]:
    found: dict[tuple[str, int, int, str], Candidate] = {}
    for source in sources:
        for candidate in views.candidates(anchor, source, historical=False):
            extent = candidate.extent
            key = (source.citation.path, extent.start, extent.end, extent.kind)
            found[key] = candidate
    return list(found.values())


def selected_historical(
    anchor: model.Anchor,
    sources: list[LocalSource],
    views: SourceViews,
) -> list[Candidate]:
    found: dict[tuple[str, int, int, str], Candidate] = {}
    for source in sources:
        for candidate in views.candidates(anchor, source, historical=True):
            extent = candidate.extent
            key = (source.citation.path, extent.start, extent.end, extent.kind)
            found[key] = candidate
    return list(found.values())


def local_changes(
    anchors: tuple[model.Anchor, ...],
    sources: list[LocalSource],
    views: SourceViews,
    *,
    dependency_sources: bool,
) -> tuple[list[str], list[str], list[str]]:
    changed: list[str] = []
    surfaced: list[str] = []
    invalid: list[str] = []
    for source in sources:
        if source.historical is None:
            invalid.append(f"{source.citation.path} did not exist at {source.provenance_label}")
    if invalid:
        return changed, surfaced, invalid
    if any(source.current is None for source in sources):
        missing = sorted({source.citation.path for source in sources if source.current is None})
        return [f"{path} no longer exists in the working tree" for path in missing], [], invalid

    for anchor in anchors:
        anchor_changed, anchor_surfaced, anchor_invalid = anchor_change(
            anchor,
            sources,
            views,
            dependency_sources=dependency_sources,
        )
        changed.extend(anchor_changed)
        surfaced.extend(anchor_surfaced)
        invalid.extend(anchor_invalid)
    return changed, surfaced, invalid


def anchor_change(
    anchor: model.Anchor,
    sources: list[LocalSource],
    views: SourceViews,
    *,
    dependency_sources: bool,
) -> tuple[list[str], list[str], list[str]]:
    before = selected_historical(anchor, sources, views)
    now = selected_current(anchor, sources, views)
    changed: list[str] = []
    surfaced: list[str] = []
    invalid: list[str] = []
    if before or now or not dependency_sources:
        if len(before) != 1:
            if before:
                invalid.append(
                    f"{anchor.written} resolved {len(before)} times at verification; exact "
                    "historical resolution must be unique"
                )
            elif now:
                # The file existed at the stamp but the construct did not: citing newly added
                # content is a change like any other, judged by the same currency rule.
                if len(now) != 1:
                    invalid.append(
                        f"{anchor.written} resolves {len(now)} times now; no exact candidate is unique"
                    )
                else:
                    detail = (
                        f"{anchor.written} did not exist at {sources[0].provenance_label} and "
                        "resolves in the working tree"
                    )
                    (surfaced if _anchor_in_cited_range(now[0], sources) else changed).append(
                        detail
                    )
            else:
                changed.append(
                    f"{anchor.written} no longer resolves in its cited current source(s)"
                )
        elif len(now) != 1:
            if now:
                invalid.append(
                    f"{anchor.written} resolves {len(now)} times now; no exact candidate is unique"
                )
            else:
                changed.append(
                    f"{anchor.written} no longer resolves in its cited current source(s)"
                )
        elif before[0].fingerprint != now[0].fingerprint:
            detail = (
                f"{anchor.written} changed structurally from "
                f"{before[0].source.provenance_label} to the working tree"
            )
            (surfaced if _anchor_in_cited_range(now[0], sources) else changed).append(detail)
    return changed, surfaced, invalid


def _anchor_in_cited_range(candidate: Candidate, sources: list[LocalSource]) -> bool:
    """Any cited range still contains the changed construct's declaration line.

    The clearing condition for a changed claim: the anchor resolves exactly once (checked by
    the caller) AND at least one cited range still contains the current construct's
    declaration line, so the pointer lands on the new content. A claim may cite the same file
    in several ranges; the de-duplicated winning candidate can carry a range that never held
    the construct, so coverage is judged across every cited range, not only the winner's.
    Clearing needs no commit.
    """
    start = candidate.extent.start
    return any(source.citation.start <= start <= source.citation.end for source in sources)


def _demote_preexisting_provenance_debt(
    findings: list[QualityFinding],
    memory_root: Path,
) -> tuple[list[QualityFinding], list[QualityFinding]]:
    """Ambiguous provenance in documents the task did not touch is debt, not a gate failure.

    A ``citation_provenance_invalid`` finding predates the task whenever its document is
    unmodified in the memory tree: the anchor was ambiguous at the stamp long before this run,
    and ambushing a passing leaf with somebody else's correction wave is how a gate loses its
    teeth. Those move to the ``debtFindings`` bucket (the standing check lists them for the
    deliberate correction wave that owns them). Findings in documents the task DID touch stay
    enforced -- touch it, own it. Missing stamps stay enforced everywhere: a new or stamp-less
    document has no pre-existing anything. Returns ``(enforced, debt)``.
    """
    completed = run_git(memory_root, ["status", "--porcelain", "-uall"])
    if completed.returncode != 0:
        # No git view, no demotion: fail closed and leave every finding enforced.
        return findings, []
    modified: set[str] = set()
    for line in completed.stdout.splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith("onboarding/"):
            modified.add(path[len("onboarding/") :])
    enforced: list[QualityFinding] = []
    debt: list[QualityFinding] = []
    for finding in findings:
        if (
            finding.code == INVALID
            and finding.severity != "warning"
            and finding.path not in modified
        ):
            debt.append(
                QualityFinding(
                    check=finding.check,
                    path=finding.path,
                    line=finding.line,
                    severity="warning",
                    code=finding.code,
                    message=finding.message,
                )
            )
        else:
            enforced.append(finding)
    return enforced, debt


def dependency_changes(
    citations: list[model.Citation],
    evaluation: Evaluation,
) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    invalid: list[str] = []
    packages: set[tuple[str, str]] = set()
    for citation in citations:
        ecosystem = provenance.ecosystem_from_path(citation.path)
        package = provenance.package_from_path(citation.path)
        if ecosystem is None:
            invalid.append(
                f"{citation.path} does not identify a Python or npm dependency source, so "
                f"{package} has no resolved-version namespace"
            )
            continue
        packages.add((package, ecosystem))
    for package, ecosystem in sorted(packages):
        before, now, error = evaluation.histories.dependency_versions(
            package, ecosystem, evaluation.code_commit
        )
        if error or before is None or now is None:
            invalid.append(error or f"{package} has no resolved version provenance")
            continue
        if before.version != now.version:
            changed.append(
                f"dependency {package} {before.version} -> {now.version} "
                f"({before.surface} -> {now.surface})"
            )
    return changed, invalid


def evaluate_claim(
    document: str,
    claim: model.Claim,
    evaluation: Evaluation,
) -> QualityFinding | None:
    route = evaluation.router.route_claim(claim.citations, evaluation.code_commit)
    if route.status == "error":
        return provenance_finding(
            document,
            claim,
            INVALID,
            f"exact-path change routing failed: {route.error}",
        )
    local: list[LocalSource] = []
    dependencies = list(route.dependencies)
    invalid: list[str] = []
    local_citations = (
        (source.citation for source in route.local) if route.status == "semantic-required" else ()
    )
    for citation in local_citations:
        source, error = evaluation.source(citation)
        if error:
            invalid.append(error)
        elif source is not None:
            local.append(source)
    changed: list[str] = []
    surfaced: list[str] = []
    if local:
        local_changed, local_surfaced, local_invalid = local_changes(
            claim.anchors,
            local,
            evaluation.source_views,
            dependency_sources=bool(dependencies),
        )
        changed.extend(local_changed)
        surfaced.extend(local_surfaced)
        invalid.extend(local_invalid)
    if dependencies:
        dependency_changed, dependency_invalid = dependency_changes(dependencies, evaluation)
        changed.extend(dependency_changed)
        invalid.extend(dependency_invalid)
    if invalid:
        return provenance_finding(document, claim, INVALID, "; ".join(dict.fromkeys(invalid)))
    if changed:
        return changed_finding(document, claim, changed)
    if surfaced:
        return surfaced_finding(document, claim, surfaced)
    return None


def check_onboarding_root(
    onboarding_root: Path,
    code_repository_root: Path | None = None,
) -> dict[str, Any]:
    """Compare every complete claim against its own historical provenance."""
    documents = model.documents_in(onboarding_root)
    if code_repository_root is None:
        return {
            **check_result(check=CHECK_NAME, files_checked=0, findings=[]),
            "status": "no-code-repository-root",
            "claimsChecked": 0,
        }

    memory_root = onboarding_root.parent
    histories = provenance.Histories(code_repository_root, memory_root)
    trees = Trees(code_root=code_repository_root, memory_root=memory_root)
    router = claim_change_router.ClaimChangeRouter(trees, histories)
    current_files = CurrentFiles()
    source_views = SourceViews()
    findings: list[QualityFinding] = []
    claims_checked = 0
    grouped: dict[str, list[tuple[str, tuple[model.Claim, ...]]]] = {}
    for document in documents:
        _lines, claims = claims_in(document)
        if not claims:
            continue
        claims_checked += len(claims)
        relative = rel(document, onboarding_root)
        stamp = parse_table_metadata(document).get("lastVerifiedCommitHash", "").strip()
        if not stamp:
            findings.extend(
                provenance_finding(
                    relative,
                    claim,
                    MISSING,
                    "the document has no lastVerifiedCommitHash",
                )
                for claim in claims
            )
            continue
        if STAMP.fullmatch(stamp) is None:
            findings.extend(
                provenance_finding(
                    relative,
                    claim,
                    INVALID,
                    f"lastVerifiedCommitHash {stamp!r} is not a hexadecimal Git commit",
                )
                for claim in claims
            )
            continue
        resolved = histories.code.commit(stamp)
        if resolved.text is None:
            findings.extend(
                provenance_finding(relative, claim, INVALID, resolved.error or "invalid commit")
                for claim in claims
            )
            continue
        grouped.setdefault(resolved.text, []).append((relative, claims))
    for code_commit, document_claims in grouped.items():
        evaluation = Evaluation(
            code_commit=code_commit,
            trees=trees,
            histories=histories,
            current_files=current_files,
            source_views=source_views,
            router=router,
        )
        for relative, claims in document_claims:
            findings.extend(
                found
                for claim in claims
                if (found := evaluate_claim(relative, claim, evaluation)) is not None
            )
    return _gate_result(documents, findings, memory_root, claims_checked, router)


def _gate_result(
    documents: list[Path],
    findings: list[QualityFinding],
    memory_root: Path,
    claims_checked: int,
    router: claim_change_router.ClaimChangeRouter,
) -> dict[str, Any]:
    """Assemble the three-bucket gate result: enforced findings plus the two review buckets."""
    enforced, debt = _demote_preexisting_provenance_debt(findings, memory_root)
    surfaced = [finding for finding in enforced if finding.severity == "warning"]
    enforced = [finding for finding in enforced if finding.severity != "warning"]
    ordered = sorted(enforced, key=lambda one: (one.code, one.path, one.line))
    result = check_result(
        check=CHECK_NAME,
        files_checked=len(documents),
        findings=ordered,
    )
    result["surfacedFindings"] = [
        finding.to_dict()
        for finding in sorted(surfaced, key=lambda one: (one.code, one.path, one.line))
    ]
    result["debtFindings"] = [
        finding.to_dict()
        for finding in sorted(debt, key=lambda one: (one.code, one.path, one.line))
    ]
    return {**result, "claimsChecked": claims_checked, "changeRouting": router.telemetry()}
