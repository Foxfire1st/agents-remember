"""The curator's two awarded planes, read once, and the coverage one report carries for them.

A curator run reads two things from the same hand-off list before it plans a single entry: the
**family plane** (the guarantees, memberships and deliberate no-family outcomes the list authors) and
the **external-source plane** (the sources the curator inspected, retained in a bounded manifest whose
digest every authored record's origin reference names). This module owns reading them together and
reporting what became of them, so :mod:`agents_remember.application.knowledge_curator_ingest` stays the
operation -- resolve, verify, commit, report -- and neither plane's vocabulary is restated there.

Three properties are load-bearing:

* **Reading is not writing.** Identities are allocated in memory and the manifest is computed as bytes;
  the journal and the manifest file are written by the operation only after admission has produced the
  candidate they belong to.
* **Each plane keeps its own state.** ``recorded`` means the rows or the manifest exist and every count
  was read back from the candidate; ``projected`` means a planning run wrote nothing and the lists are
  what it would write; ``not-recorded`` means nothing was written, with the sentence naming why. A
  plane that did not record reports null counts, never zeroes that would read as measured.
* **Every entry is accounted for, once.** A plane's refusal is reported through the entry's own refusal;
  the entries whose outcome the run could not establish are named as unresolved, and the committed
  entries the curator examined nothing for are named as unexamined.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.curator_family_authoring import (
    FamilyPlaneRead,
    read_family_plane,
)
from agents_remember.application.curator_family_coverage import (
    CoverageScope,
    FamilyCoverage,
    family_coverage,
)
from agents_remember.application.curator_family_planning import (
    CuratorFamilyAuthoring,
    DeclarationPlan,
    StoredFamilyFacts,
    plan_declarations,
    read_family_allocations,
    read_stored_family_facts,
)
from agents_remember.application.curator_source_manifest import (
    SOURCE_MANIFEST_NAME,
    SourceCoverage,
    SourceCoverageScope,
    SourceManifest,
    SourcePlaneRead,
    origin_refs,
    read_source_plane,
    source_coverage,
    source_manifest,
)
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.snapshot import candidate_database_path

__all__ = [
    "CuratorPlanes",
    "PlaneCoverageInputs",
    "plane_coverage",
    "read_curator_planes",
]


@dataclass(frozen=True)
class PlaneCoverageInputs:
    """Everything the two planes' coverage needs, as one value rather than seven arguments.

    The run's own outcome is carried as facts rather than as the operation's private objects: which
    entries committed, which did not, whether this was a planning run, and the post-batch family read
    that exists only when the batch committed.
    """

    candidate: Path
    entry_ids: tuple[str, ...]
    placed: tuple[str, ...]
    authoring: Mapping[str, CuratorFamilyAuthoring]
    planes: CuratorPlanes
    dry_run: bool
    after: StoredFamilyFacts | None = None

    @property
    def unresolved(self) -> tuple[str, ...]:
        """Every entry this run carried whose outcome the two planes did not establish."""

        return tuple(one for one in self.entry_ids if one not in set(self.placed))


@dataclass(frozen=True)
class CuratorPlanes:
    """The two awarded planes one run reads before it plans a single entry.

    Both are read from the same list and resolved against the same candidate, and they are kept in one
    value because the report needs them together: the family plane carries the declarations this run
    resolves once for the whole list, and the source plane carries the bounded manifest whose digest
    every row's origin references name. ``stored`` is ``None`` when the candidate does not exist yet,
    which is a different fact from a candidate that records no family.
    """

    list_digest: str
    family: FamilyPlaneRead
    declarations: DeclarationPlan
    stored: StoredFamilyFacts | None
    sources: SourcePlaneRead
    manifest: SourceManifest | None
    # Whether the operation actually wrote the manifest into the candidate. It is a separate fact
    # from ``manifest is not None``, which only says the list declared something: a run whose batch
    # refused still wrote the file it had already named, and a report that said otherwise would be
    # false about the store.
    manifest_written: bool = False

    @property
    def refs(self) -> tuple[str, ...]:
        """The origin references every row this run writes carries."""

        return origin_refs(self.list_digest, self.manifest)

    def refusal_of(self, entry_id: str) -> tuple[str, str] | None:
        """Why this entry cannot be planned at all, from either plane, as ``(code, reason)``.

        The two planes refuse different things -- a family decision that cannot be resolved to exact
        revisions, and an external source that cannot be found again -- and both are facts about one
        entry, so they are reported through the entry's own refusal rather than only in the plane's
        coverage.
        """

        family = self.family.refusal_of(entry_id)
        if family is not None:
            return family.code, family.reason
        declaration = self.declarations.refusals.get(entry_id)
        if declaration is not None:
            return declaration.code, declaration.reason
        source = self.sources.refusal_of(entry_id)
        if source is not None:
            return source.code, source.reason
        return None


def read_curator_planes(
    raw: tuple[Mapping[str, Any], ...],
    candidate: Path,
    retry_scope: str,
    *,
    fork_point: Path | None = None,
) -> CuratorPlanes:
    """Read both awarded planes once, before a single entry is planned.

    Everything here is read and resolved against the destination this run will write into, and
    nothing is written: the family identities are allocated in memory and journalled only after
    admission has produced the candidate they belong to. ``read_stored_family_facts`` answers
    ``None`` for a candidate that does not exist yet, which is how a first run's "this dataset
    records no family" stays distinguishable from "there is no dataset here".

    ``fork_point`` is the database an **absent** candidate will be forked from, and it is the whole
    reason this read is not simply "the candidate's own bytes yet". A run whose destination does not
    exist yet and that selected a baseline does not start empty: admission clones that baseline, so
    the family revisions the candidate holds on its first write are the baseline's. Reading only the
    candidate answered ``None`` — "there is no dataset here" — for exactly that run, and a membership
    naming a revision the *baseline* stores was then refused ``family_revision_not_stored`` while
    sitting one step away from a candidate that would hold it. That is why the fallback is here
    rather than after admission: the CLI's **planning run is the default**, it writes nothing by
    contract, and it asks the same question — so a fix that only covered the committing run would
    leave the ordinary dry run refusing the same stored revision.

    The caller supplies only a database that reads as a dataset of this code (see
    :func:`…knowledge_curator_ingest._fork_point`), because the admission answers an unreadable fork
    point with its own typed refusal and that refusal must not become a storage error raised here.

    ``retry_scope`` is the enclosure's own idempotency scope, passed in rather than derived here: the
    operation owns that derivation and this module must not hold a second spelling of it.
    """

    list_digest = sha256_digest([dict(one) for one in raw])
    family = read_family_plane(raw)
    stored = read_stored_family_facts(candidate_database_path(candidate))
    if stored is None and fork_point is not None:
        stored = read_stored_family_facts(Path(fork_point))
    declarations = plan_declarations(
        family,
        retry_scope=retry_scope,
        allocations=read_family_allocations(candidate),
        stored=stored,
    )
    sources = read_source_plane(raw)
    return CuratorPlanes(
        list_digest=list_digest,
        family=family,
        declarations=declarations,
        stored=stored,
        sources=sources,
        manifest=source_manifest(sources, list_digest),
    )


def plane_coverage(inputs: PlaneCoverageInputs) -> tuple[FamilyCoverage, SourceCoverage]:
    """Both planes' coverage for one report, each with the state this run actually reached.

    Three states are kept apart for each plane and never merged. ``recorded`` means the rows or the
    manifest exist and every count was read back from the candidate; ``projected`` means this was a
    planning run, which wrote nothing, and the lists are what it would write; ``not-recorded`` means
    this run wrote nothing at all, with the sentence naming why. The last two report no measured
    member count at all, so a null stands where a zero would have read as a measured empty family.

    The two entry sets a report divides are derived here from the outcomes themselves: ``placed`` is
    what committed, and ``unresolved`` is every other entry the list carried -- refused, ruled on, or
    refused at one of these two planes -- so a plane's coverage names the entries whose outcome this
    run did not establish instead of leaving them out of an otherwise complete-looking list.
    """

    planes = inputs.planes
    placed, unresolved = inputs.placed, inputs.unresolved
    family_state, family_detail, source_state, source_detail = _plane_states(
        planes, dry_run=inputs.dry_run, after=inputs.after
    )
    return (
        family_coverage(
            planes.declarations,
            inputs.authoring,
            CoverageScope(
                state=family_state,
                detail=family_detail,
                placed=placed,
                unresolved=unresolved,
                after=inputs.after,
            ),
        ),
        source_coverage(
            planes.sources,
            planes.manifest,
            SourceCoverageScope(
                state=source_state,
                detail=source_detail,
                refs=planes.refs,
                placed=placed,
                unresolved=unresolved,
                path=(
                    inputs.candidate / SOURCE_MANIFEST_NAME if source_state == "recorded" else None
                ),
            ),
        ),
    )


def _source_state(planes: CuratorPlanes) -> tuple[str, str]:
    """The source plane's state and sentence, read from what this run actually wrote.

    The manifest is written *before* the batch it belongs to, because the origin references that batch
    stamps name its digest -- so "the manifest is not there" and "the batch refused" are two different
    facts, and a run whose batch refused still has a written manifest to report. The state therefore
    follows the write, not the commit, and says which of the two happened.
    """

    if planes.manifest is None:
        return (
            "not-recorded",
            "this list declared no external source, so no manifest was written",
        )
    if planes.manifest_written:
        return (
            "recorded",
            "the manifest was written into the candidate and named by its own digest; the batch's own "
            "outcome is reported separately",
        )
    return (
        "not-recorded",
        "the run stopped before the manifest was written, so the declared sources are reported but not "
        "retained",
    )


def _plane_states(
    planes: CuratorPlanes, *, dry_run: bool, after: StoredFamilyFacts | None
) -> tuple[str, str, str, str]:
    """Each plane's state and its own sentence, read from what this run actually reached.

    A sentence that claimed more than the run established would be the defect this whole report
    exists to prevent, so the cases are separate pairs of sentences rather than one sentence
    parameterised by a word.
    """

    if dry_run:
        return (
            "projected",
            "planning run: nothing was written, and the family rows below are what this list would "
            "author",
            "not-recorded",
            "planning run: no manifest was written, and the digest below is what it would name",
        )
    family_state, family_detail = (
        ("not-recorded", "the batch did not commit, so no family row was written here")
        if after is None
        else (
            "recorded",
            "the committed candidate holds these family rows, and every count below was read back "
            "from it",
        )
    )
    source_state, source_detail = _source_state(planes)
    return (family_state, family_detail, source_state, source_detail)
