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
duty. A whole source file added after the stamp follows the same rule: if its anchor resolves
exactly once inside a cited range it surfaces report-only; otherwise it stays hard. Only a
changed construct whose citation is not current -- or that never resolves uniquely -- is an
enforced reopened claim.

That review surface distinguishes WHERE the range came from, because a range written by the
mechanical projection is indistinguishable in the document from a curator's edit and only the
generated Update History bullet records it. When the bullet names this claim's anchors the
item stops asserting the citation is current and asks the support question instead: the
projection resolves an exact NAME, never the claim's subject, so a range that arrived that way
can point at a declaration the claim was never about. That item is ENFORCED rather than
report-only, because a mechanically projected range is unverified evidence: nothing in the
tree records whether anyone reviewed the projection, the check cannot prove that a review
happened, and evidence it cannot verify has to force an explicit disposition instead of
offering a note a curator may simply read past. The cost is deliberate and total -- every
mechanically projected range blocks until somebody disposes of it.

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
from agents_remember.kernel.memory_attribution import MemoryAttributionError
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    parse_table_metadata,
    rel,
)
from agents_remember.memory_quality.style.citations import (
    cells,
    claim_change_router,
    deterministic_projection,
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
# The generated Update History bullet a mechanical anchor-range projection writes into the
# document it rewrote -- see ``deterministic_projection.history_bullet``. Its name list runs
# from this header to the ``repointed to`` clause; everything after that clause is a range,
# and a range's path can share its file name with an unrelated anchor.
PROJECTION_BULLET = "Generated citation repair"
REPOINTED_TO = " repointed to "
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


def generated_repair_bullets(claim: model.Claim, lines: list[str]) -> list[str]:
    """The Update History bullets recording that a mechanical repair moved THIS claim's range.

    ``history_section_line`` bounds the scan to the canonical section, and the anchor list is
    read between the bullet's header and its ``repointed to`` clause: the ranges after that
    clause carry file paths, which would otherwise match an anchor that merely shares its name
    with a file the repair wrote. Every named anchor is matched by exact text, so the bullet
    for a different claim in the same document is not evidence about this one.
    """
    heading = deterministic_projection.history_section_line(lines)
    if heading is None:
        return []
    return [line.strip() for line in lines[heading:] if _names_an_anchor(claim, line)]


def _names_an_anchor(claim: model.Claim, line: str) -> bool:
    if PROJECTION_BULLET not in line or REPOINTED_TO not in line:
        return False
    clause = line.split(PROJECTION_BULLET, 1)[1].split(REPOINTED_TO, 1)[0]
    return any(anchor.written in clause for anchor in claim.anchors)


def _repointed_ranges(bullet: str) -> str:
    tail = bullet.split(REPOINTED_TO, 1)[1]
    return tail.split(deterministic_projection.NO_IMPACT_MARKER, 1)[0].strip().rstrip(".").strip()


def _projected_review_message(details: list[str], bullets: list[str]) -> str:
    """The review item for a range that arrived by mechanical projection.

    The old text asserted the citation was current because the anchor resolves and a cited
    range covers its declaration -- a test the projection satisfies BY CONSTRUCTION, since it
    picked the declaration it wrote. A curator asked that one question could only answer it
    yes, so the item now asks the question the projection cannot answer: whether the construct
    at the new location supports the claim's own words, and whether the range was projected
    rather than rebound from the mention the claim was verified against.
    """
    ranges = "; ".join(dict.fromkeys(_repointed_ranges(one) for one in bullets))
    return (
        f"This claim's evidence changed after verification, and a generated citation repair "
        f"has already rewritten its range mechanically, so the citation is NOT shown to be "
        f"current: {'; '.join(details)}. Update History records the range change as "
        f"{' | '.join(bullets)} -- it now reads {ranges}, and unchanged claim bytes are not "
        "evidence that the claim still holds there. Two questions, and the first is not about "
        "wording: (1) does the construct the new range covers support what this claim's own "
        "words state? (2) did that range arrive by mechanical anchor-range projection, or was "
        "it rebound from a mention the claim was verified against to the anchor's declaration "
        "elsewhere? If the range was projected, the correct action is to re-cite the location "
        "the claim is about -- or re-word the claim -- and only then advance the stamp: a "
        "declaration elsewhere does not evidence a claim about a mention here."
    )


def surfaced_finding(
    document: str,
    claim: model.Claim,
    details: list[str],
    lines: list[str],
) -> QualityFinding:
    """One changed claim's review item, at the severity its evidence actually supports.

    An evidence change whose citation is CURRENT is the report-only surface: the anchor still
    resolves exactly once and some cited range still contains the current construct's
    declaration line, so the pointer provably points at the new content. Whether the prose
    still describes it correctly is the curator's review duty, relayed in the check's
    ``surfacedFindings`` bucket -- the check detects change, it cannot prove a review, and it
    does not pretend one happened.

    A range written by the mechanical projection passes that currency test BY CONSTRUCTION,
    so the document's generated repair bullets are read first. Where they name this claim's
    anchors the item asks the support question instead of asserting currency, AND it comes
    back ``error`` so ``_gate_result`` leaves it in the enforced set. The projection is
    unverified evidence, and the check cannot prove a review happened, so it must force an
    explicit disposition rather than offer an ignorable note. The trade is deliberate: every
    mechanically projected range now blocks until a curator disposes of it.
    """
    bullets = generated_repair_bullets(claim, lines)
    message = (
        _projected_review_message(details, bullets)
        if bullets
        else (
            f"This claim's evidence changed after verification, and the citation is current "
            f"(anchor resolves and some cited range still holds the changed construct's "
            f"declaration): {'; '.join(details)}. "
            "Curator review confirms the wording still holds; no citation repair is needed."
        )
    )
    return QualityFinding(
        check=CHECK_NAME,
        path=document,
        line=claim.line,
        # Enforced, not surfaced, for the projected variant only: its currency test is
        # satisfied by the projection itself, so it is not evidence that a review happened.
        # The ordinary evidence-change item keeps its warning -- its currency test IS evidence.
        severity="error" if bullets else "warning",
        code=REOPENED,
        message=message,
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
    if any(source.current is None for source in sources):
        missing = sorted(
            (
                f"{source.citation.path} did not exist at {source.provenance_label} "
                "and does not exist in the working tree"
                if source.historical is None
                else f"{source.citation.path} no longer exists in the working tree"
            )
            for source in sources
            if source.current is None
        )
        return missing, [], invalid

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
                # The cited evidence did not exist at the stamp -- either a construct added to
                # an existing file or a whole source file added after the stamp. Both are a
                # change like any other, judged by the same currency rule: an exactly-once
                # current resolution inside a cited range is the report-only surface; anything
                # else is enforced.
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
    modified = _modified_onboarding_paths(memory_root)
    if modified is None:
        # No git view, no demotion: fail closed and leave every finding enforced.
        return findings, []
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


def _modified_onboarding_paths(memory_root: Path) -> set[str] | None:
    """Return dirty sidecar paths relative to ``onboarding/``, or ``None`` without Git truth."""
    completed = run_git(memory_root, ["status", "--porcelain", "-uall"])
    if completed.returncode != 0:
        return None
    modified: set[str] = set()
    for line in completed.stdout.splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith("onboarding/"):
            modified.add(path[len("onboarding/") :])
    return modified


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


def _mapping_pending_for_code_head(error: str, evaluation: Evaluation) -> bool:
    """The current code output has no attributed memory-content commit yet.

    Verification stamps are written by the closeout refresh, so a stamp naming the code
    worktree's HEAD can exist before memory publication. Pending requires HEAD to be unmapped
    and an actual memory commit to attribute an ancestor of HEAD. A missing cache supplies
    neither evidence nor a refusal.
    """
    if "no attribution for code commit" not in error:
        return False
    code_root = evaluation.trees.code_root
    head = run_git(code_root, ["rev-parse", "HEAD"])
    if head.returncode != 0 or head.stdout.strip() not in error:
        return False
    try:
        mappings = evaluation.histories.memory_mappings
    except MemoryAttributionError:
        return False
    return any(
        run_git(
            code_root, ["merge-base", "--is-ancestor", row.code_commit, head.stdout.strip()]
        ).returncode
        == 0
        for row in mappings
        if row.code_commit != head.stdout.strip()
    )


def _route_error_finding(
    document: str, claim: model.Claim, error: str, evaluation: Evaluation
) -> QualityFinding | None:
    if _mapping_pending_for_code_head(error, evaluation):
        # The stamp names the code worktree's HEAD -- a commit the in-flight closeout made
        # (or recorded) before publishing attributed memory content. Faulting claims for
        # attribution that this publication supplies would deadlock its own preparation.
        return None
    return provenance_finding(
        document,
        claim,
        INVALID,
        f"exact-path change routing failed: {error}",
    )


def evaluate_claim(
    document: str,
    claim: model.Claim,
    evaluation: Evaluation,
    lines: list[str],
) -> QualityFinding | None:
    route = evaluation.router.route_claim(claim.citations, evaluation.code_commit)
    if route.status == "error":
        return _route_error_finding(document, claim, route.error or "", evaluation)
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
        return surfaced_finding(document, claim, surfaced, lines)
    return None


def check_onboarding_root(
    onboarding_root: Path,
    code_repository_root: Path | None = None,
    *,
    unstamped_code_commit: str | None = None,
    retained_code_history_commits: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Compare every complete claim against its own historical provenance.

    Closeout may supply the leaf base as temporary provenance for a dirty, unstamped card.
    That breaks the new-card deadlock without inventing a verification stamp: the preflight
    still resolves every claim against the base and working tree, and the post-refresh pass
    supplies no fallback, so metadata refresh must write the real code commit before memory
    can commit. A selected prepared code proof may retain verified code commits from its
    explicit predecessor chain as reachability anchors while this check still reads current
    working-tree bytes. Standalone checks remain strict about every missing stamp and have no
    retained anchor.
    """
    documents = model.documents_in(onboarding_root)
    if code_repository_root is None:
        return {
            **check_result(check=CHECK_NAME, files_checked=0, findings=[]),
            "status": "no-code-repository-root",
            "claimsChecked": 0,
        }

    memory_root = onboarding_root.parent
    histories = provenance.Histories(
        code_repository_root,
        memory_root,
        retained_code_history_commits=retained_code_history_commits,
    )
    trees = Trees(code_root=code_repository_root, memory_root=memory_root)
    router = claim_change_router.ClaimChangeRouter(trees, histories)
    current_files = CurrentFiles()
    source_views = SourceViews()
    findings: list[QualityFinding] = []
    claims_checked = 0
    modified = _modified_onboarding_paths(memory_root)
    grouped: dict[str, list[tuple[str, list[str], tuple[model.Claim, ...]]]] = {}
    for document in documents:
        lines, claims = claims_in(document)
        if not claims:
            continue
        claims_checked += len(claims)
        relative = rel(document, onboarding_root)
        stamp = parse_table_metadata(document).get("lastVerifiedCommitHash", "").strip()
        if not stamp:
            if unstamped_code_commit is not None and modified is not None and relative in modified:
                stamp = unstamped_code_commit
            else:
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
        grouped.setdefault(resolved.text, []).append((relative, lines, claims))
    for code_commit, document_claims in grouped.items():
        evaluation = Evaluation(
            code_commit=code_commit,
            trees=trees,
            histories=histories,
            current_files=current_files,
            source_views=source_views,
            router=router,
        )
        for relative, lines, claims in document_claims:
            findings.extend(
                found
                for claim in claims
                if (found := evaluate_claim(relative, claim, evaluation, lines)) is not None
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
