"""How one ``knowledge-ingest`` run is rendered: the report IS the product.

The ingest's outcome is a report, not an exit code -- a refusal per entry is a *result*, and a
caller that branches on the process status would lose it -- so what this module prints is the run's
whole answer rather than a courtesy summary. It was extracted from
:mod:`agents_remember.cli.knowledge_ingest` when the ordinary publication route added two more
facts to that answer (the destination the run selected, and the identity a read of it found back):
the surface that decides *whether* a run may proceed stays in the command module, and the shape of
what it says stays here, so neither grows past the rail by absorbing the other.

Two renderings of one report, and the JSON one is the contract: every tuple becomes a list, every
dataclass a mapping, and nothing is summarised away, because a caller that has to re-read the
database to learn what happened has been given a message rather than a result.

Two fields are *strings* rather than booleans or objects, and for the same reason: ``reviewBaseline``
and ``publicationRoute`` both state what this run did about a handoff that can also have several
reasons for not happening. "Not placed" and "not selected" are not ``false``; they are facts whose
reason a caller acts on, and a caller that has to guess which reason is being handed a message
instead of a result.
"""

from __future__ import annotations

from typing import Any

from agents_remember.application.knowledge_curator_ingest import EntryOutcome, IngestReport
from agents_remember.application.knowledge_publication_route import PublishedIdentityReadBack

__all__ = ["payload", "summary"]


def summary(
    report: IngestReport,
    review_baseline: str | None,
    *,
    publication_route: str,
    published_identity: PublishedIdentityReadBack | None,
) -> str:
    """The report as a reader scans it: the mode, the counts, and one line per outcome."""

    lines = [
        f"knowledge-ingest {'dry run' if report.dry_run else 'commit'} on {report.contract_path}",
        f"  candidate: {report.candidate_directory}",
        f"  lane: {report.lane}  repository: {report.repository_id}",
        f"  code tree: {report.code_tree_id} ({report.code_tree_source} at "
        f"{report.code_base_commit})",
        f"  memory tree: {report.memory_tree_id}",
        f"  batch: {report.batch_state}"
        + (f"  refusal: {report.batch_refusal.code}" if report.batch_refusal else ""),
        "  counts: " + ", ".join(f"{name}={value}" for name, value in _counts(report).items()),
        f"  publication route: {publication_route}",
    ]
    if report.publication is not None:
        publication = report.publication
        published_to = f" -> {publication.destination_ref}" if publication.destination_ref else ""
        refusal = (
            f"  refusal: {publication.refusal.code}" if publication.refusal is not None else ""
        )
        lines.append(f"  publication: {publication.state}{published_to}{refusal}")
    if published_identity is not None:
        lines.append(f"  published identity: {_identity_line(published_identity)}")
    if review_baseline is not None:
        lines.append(f"  review baseline: {review_baseline}")
    for outcome in report.committed:
        lines.append(f"  committed {outcome.entry_id}: {_targets(outcome)}")
    for outcome in report.rulings:
        lines.append(f"  ruling {outcome.entry_id}: {outcome.kind}/{outcome.disposition}")
    for outcome in report.refused:
        lines.append(f"  refused {outcome.entry_id}: {outcome.refusal}")
    return "\n".join(lines)


def payload(
    report: IngestReport,
    review_baseline: str | None,
    *,
    publication_route: str,
    published_identity: PublishedIdentityReadBack | None,
) -> dict[str, Any]:
    """The whole report as one JSON object, so a caller branches on facts and not on prose.

    ``publicationRoute`` is the run's own line about the destination it selected: the declared
    published location, a caller-named path, or the fact that it named none. ``publishedIdentity``
    is the read-back of that location -- what an independent read found after the publication
    reported success -- and it is ``None`` when this run published nothing to read back, which is a
    different fact from a read-back that found the wrong dataset.
    """

    return {
        "contractPath": report.contract_path,
        "candidateDirectory": report.candidate_directory,
        "reviewBaseline": review_baseline,
        "candidateReceipt": report.candidate_receipt,
        "lane": report.lane,
        "repositoryId": report.repository_id,
        "codeTreeId": report.code_tree_id,
        "memoryTreeId": report.memory_tree_id,
        "codeBaseCommit": report.code_base_commit,
        "codeTreeSource": report.code_tree_source,
        "derivedIdentities": report.derived_identities,
        "dryRun": report.dry_run,
        "entriesRead": list(report.entries_read),
        "batchState": report.batch_state,
        "batchDigestBefore": report.batch_digest_before,
        "batchDigestAfter": report.batch_digest_after,
        "batchRefusal": (
            None if report.batch_refusal is None else report.batch_refusal.model_dump(mode="json")
        ),
        "publicationRoute": publication_route,
        "publication": (
            None if report.publication is None else report.publication.model_dump(mode="json")
        ),
        "publishedIdentity": _read_back_block(published_identity),
        "counts": _counts(report),
        "committed": [_outcome(one) for one in report.committed],
        "rulings": [_outcome(one) for one in report.rulings],
        "refused": [_outcome(one) for one in report.refused],
    }


def _identity_line(read_back: PublishedIdentityReadBack) -> str:
    """One read-back as the single line a reader scans it on."""

    identity = read_back.identity
    digest = "-" if identity is None else identity.logical_digest
    code = "" if read_back.refusal_code is None else f" refusal: {read_back.refusal_code}"
    return f"{read_back.state} {digest} at {read_back.dataset_path}{code}"


def _read_back_block(read_back: PublishedIdentityReadBack | None) -> dict[str, Any] | None:
    """The read-back as the smallest owned object, carrying the reader's own sentence."""

    if read_back is None:
        return None
    identity = read_back.identity
    return {
        "state": read_back.state,
        "datasetPath": str(read_back.dataset_path),
        "repositoryId": None if identity is None else identity.repository_id,
        "schemaVersion": None if identity is None else identity.schema_version,
        "logicalDigest": None if identity is None else identity.logical_digest,
        "refusalCode": read_back.refusal_code,
        "detail": read_back.detail,
    }


def _targets(outcome: EntryOutcome) -> str:
    return "; ".join(
        f"{target.completed_path} [{target.locator_kind}] {target.observation}"
        for target in outcome.targets
    )


def _counts(report: IngestReport) -> dict[str, int]:
    return {
        "entriesRead": report.counts.entries_read,
        "rulings": report.counts.rulings,
        "targetsCompleted": report.counts.targets_completed,
        "locatorsResolved": report.counts.locators_resolved,
        "anchorsObservedExact": report.counts.anchors_observed_exact,
        "routePaths": report.counts.route_paths,
        "routesAuthored": report.counts.routes_authored,
        "routesReused": report.counts.routes_reused,
        "commandsSent": report.counts.commands_sent,
        "recordsWritten": report.counts.records_written,
        "committed": len(report.committed),
        "refused": len(report.refused),
    }


def _outcome(outcome: EntryOutcome) -> dict[str, Any]:
    """One entry's whole outcome, including the state the operation itself assigned it."""

    return {
        "entryId": outcome.entry_id,
        "kind": outcome.kind,
        "disposition": outcome.disposition,
        "dispositionSource": outcome.disposition_source,
        "state": outcome.state,
        "refusal": outcome.refusal,
        "routes": [
            {
                "routePath": route.route_path,
                "routeId": route.route_id,
                "state": route.state,
                "refusal": route.refusal,
            }
            for route in outcome.routes
        ],
        "targets": [
            {
                "path": target.path,
                "step": target.step,
                "completedPath": target.completed_path,
                "locatorKind": target.locator_kind,
                "locator": target.locator,
                "observation": target.observation,
                "sourceIdentity": target.source_identity,
                "routePath": target.route_path,
                "routeState": target.route_state,
                "refusal": target.refusal,
            }
            for target in outcome.targets
        ],
    }
