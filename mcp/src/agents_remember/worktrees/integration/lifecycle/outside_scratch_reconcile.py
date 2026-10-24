"""Who proves an outside scratch root's generation has ended, and who sweeps (MIK-R207).

The terminal owner is the only place that can answer the proof question, and it answers two
ends. A generation at the live address is finished when its locator is ``terminal-archived``
*and* the surviving contract has moved through the sanctioned cleanup progression to
completed/abandoned: an archive is published before the destructive outputs, so an archive that
merely exists proves nothing. A generation a successor replaced is finished when the successor's
retained predecessor link validates its archive and receipt at their exact addresses and the
predecessor's worktree group is gone. Either way the archived contract must record the same root,
generation and marker the marker on disk carries.

This module is separate from ``lifecycle_enclosure_terminal`` because that module is imported
by the location publisher, and the sweep needs the publisher's locator reading. Cleanup,
abandon, finalize and the tool-server start all reach the sweep through here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    LifecycleEnclosureManifest,
    TerminalEnclosureArchive,
    TerminalEnclosurePredecessor,
    TerminalEnclosureReceipt,
)
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    terminal_cleanup_contract_authority,
    validate_terminal_proof,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.terminal_cleanup_completion import (
    completion_agrees,
    read_completion_record,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    parse_contract_text,
)

MAX_PREDECESSOR_HOPS = 8
"""How far back one address's retained predecessor links are followed."""


def reconcile_outside_scratch_roots() -> outside_scratch.ReconcileReport:
    """Delete every outside scratch root whose generation is proven ended.

    This is the named caller the terminal owner owes: cleanup, abandon, finalize and the
    tool-server start all reach it. Deletion needs the marker, the retained contract record and
    a terminal-archived locator to agree; every other root is reported with one reason and left
    alone. A root is never taken by its name.
    """

    return outside_scratch.reconcile(ended=generation_ended)


def generation_ended(marker: dict[str, Any]) -> bool:
    """Whether one marker's generation is proven ended by retained records alone."""

    contract_path = Path(str(marker.get("contractPath") or ""))
    coordination_root = Path(str(marker.get("coordinationRoot") or ""))
    root = Path(str(marker.get("path") or ""))
    if not (contract_path.is_absolute() and coordination_root.is_absolute()):
        return False
    return any(
        recorded.root.as_posix() == root.as_posix()
        and recorded.generation == str(marker.get("generation") or "")
        and recorded.marker_sha256 == outside_scratch.marker_identity(root)
        for recorded in _proven_generations(coordination_root, contract_path)
    )


def _proven_generations(
    coordination_root: Path,
    contract_path: Path,
) -> list[outside_scratch.OutsideScratchRoot]:
    """Every root whose generation is proven ended at this address, live or superseded.

    Two ends are proved, and nothing else is. The live address is ended when its locator is
    ``terminal-archived`` *and* the surviving contract has moved through the sanctioned cleanup
    progression to completed/abandoned -- an archive that merely exists is published before the
    destructive outputs, so it is not proof on its own. A generation a successor replaced is
    proved through the successor's own retained predecessor link: the archive and receipt at
    their exact addresses validate, the predecessor's worktree group is gone, and the archived
    contract records the root.
    """

    try:
        observed = inspect_lifecycle_operation_locator(coordination_root, contract_path)
    except Exception:
        return []
    if observed.locator is None:
        return []
    proven: list[outside_scratch.OutsideScratchRoot] = []
    if observed.state == "terminal-archived":
        live = _ended_at_the_live_address(coordination_root, contract_path, observed.locator)
        if live is not None:
            proven.append(live)
    proven.extend(_ended_predecessors(coordination_root, contract_path, observed.locator))
    return proven


def _ended_at_the_live_address(
    coordination_root: Path,
    contract_path: Path,
    locator: LifecycleEnclosureLocator,
) -> outside_scratch.OutsideScratchRoot | None:
    """The root the live address records, once its own cleanup progression is complete."""

    try:
        authority = terminal_cleanup_contract_authority(
            coordination_root,
            load_contract(contract_path),
            locator,
        )
    except Exception:
        return None
    if authority.state != "cleanup-completed":
        return None
    return outside_scratch.recorded_root(authority.archived_contract)


def _ended_predecessors(
    coordination_root: Path,
    contract_path: Path,
    locator: LifecycleEnclosureLocator,
) -> list[outside_scratch.OutsideScratchRoot]:
    """The roots of every generation this address's manifest links as a collected predecessor."""

    proven: list[outside_scratch.OutsideScratchRoot] = []
    link = _manifest_predecessor(locator.manifestPath, coordination_root)
    for _ in range(MAX_PREDECESSOR_HOPS):
        if link is None:
            break
        archive = _retained_archive(coordination_root, contract_path, link)
        if archive is None:
            break
        recorded = outside_scratch.recorded_root(
            parse_contract_text(archive.contractText, path=contract_path)
        )
        if recorded is not None:
            proven.append(recorded)
        link = archive.manifest.predecessorTerminal
    return proven


def _manifest_predecessor(
    manifest_path: str,
    coordination_root: Path,
) -> TerminalEnclosurePredecessor | None:
    """The predecessor link the manifest at this address carries, or ``None``."""

    try:
        manifest = LifecycleEnclosureManifest.model_validate_json(
            Path(manifest_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError):
        return None
    link = manifest.predecessorTerminal
    if link is None or not _inside(Path(link.terminalArchivePath), coordination_root):
        return None
    return link


def _retained_archive(
    coordination_root: Path,
    contract_path: Path,
    link: TerminalEnclosurePredecessor,
) -> TerminalEnclosureArchive | None:
    """The predecessor's archive, only when bytes, receipt, completion and deletion all agree."""

    evidence = _retained_evidence(coordination_root, link)
    if evidence is None:
        return None
    archive, receipt = evidence
    if not _identities_agree(archive, receipt, link, contract_path):
        return None
    if not _generation_completed(coordination_root, archive, link):
        return None
    return archive


def _retained_evidence(
    coordination_root: Path,
    link: TerminalEnclosurePredecessor,
) -> tuple[TerminalEnclosureArchive, TerminalEnclosureReceipt] | None:
    """The predecessor's archive and receipt once their addresses, bytes and models agree."""

    archive_path, receipt_path, group = _linked_addresses(link)
    if not (
        _addresses_agree(archive_path, receipt_path, group, coordination_root)
        and not group.exists()
    ):
        return None
    try:
        archive_text = archive_path.read_text(encoding="utf-8")
        receipt_text = receipt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if outside_scratch.sha256_text(archive_text) != link.terminalArchiveSha256:
        return None
    try:
        archive = TerminalEnclosureArchive.model_validate_json(archive_text)
        receipt = TerminalEnclosureReceipt.model_validate_json(receipt_text)
    except ValueError:
        return None
    return archive, receipt


def _generation_completed(
    coordination_root: Path,
    archive: TerminalEnclosureArchive,
    link: TerminalEnclosurePredecessor,
) -> bool:
    """Whether this predecessor's generation is proven *completed*, not merely archived.

    The archive is published before the destructive outputs, so it proves nothing on its own and
    a vanished worktree group proves only that something removed it. A generation still standing
    at its own address is asked the live question -- the sanctioned cleanup progression must have
    reached ``cleanup-completed``. A replaced generation has no address left to ask, so its proof
    is the completion record its own finished cleanup wrote beside the archive; without that
    record the generation is reported, never treated as ended.
    """

    recorded = read_completion_record(coordination_root, link.publicationRequestId)
    if recorded is not None:
        return completion_agrees(
            recorded,
            publication_request_id=link.publicationRequestId,
            operation=str(archive.cleanupOperation),
            contract_path=archive.contractPath,
        )
    return _completed_at_its_own_address(coordination_root, archive)


def _completed_at_its_own_address(
    coordination_root: Path,
    archive: TerminalEnclosureArchive,
) -> bool:
    """Ask the live address of a predecessor still in place whether its cleanup completed."""

    contract_path = Path(archive.contractPath)
    try:
        observed = inspect_lifecycle_operation_locator(coordination_root, contract_path)
        if observed.state != "terminal-archived" or observed.locator is None:
            return False
        authority = terminal_cleanup_contract_authority(
            coordination_root,
            load_contract(contract_path),
            observed.locator,
        )
    except Exception:
        return False
    return authority.state == "cleanup-completed"


def _linked_addresses(
    link: TerminalEnclosurePredecessor,
) -> tuple[Path, Path, Path]:
    """The archive, receipt and old group a predecessor link names, resolved but not trusted."""

    return (
        Path(link.terminalArchivePath).resolve(strict=False),
        Path(link.terminalReceiptPath).resolve(strict=False),
        Path(link.worktreeGroup).resolve(strict=False),
    )


def _addresses_agree(
    archive_path: Path,
    receipt_path: Path,
    group: Path,
    coordination_root: Path,
) -> bool:
    """Whether both retained addresses are ours and stand outside the group they outlived."""

    return (
        _inside(archive_path, coordination_root)
        and _inside(receipt_path, coordination_root)
        and not _inside(archive_path, group)
        and not _inside(receipt_path, group)
    )


def _identities_agree(
    archive: TerminalEnclosureArchive,
    receipt: TerminalEnclosureReceipt,
    link: TerminalEnclosurePredecessor,
    contract_path: Path,
) -> bool:
    """Whether the archive, its receipt, its manifest and the link describe one generation.

    Every address and digest the link carries is compared against the receipt, and the archive's
    own locator and manifest against the same link: a receipt that differs from the link in even
    one field describes a different generation, and a partial comparison would let a stale link
    claim a newer archive.
    """

    return (
        receipt.publicationRequestId == link.publicationRequestId
        and receipt.bindingFingerprint == link.bindingFingerprint
        and receipt.worktreeGroup == link.worktreeGroup
        and receipt.manifestPath == link.manifestPath
        and receipt.expectedManifestSha256 == link.expectedManifestSha256
        and receipt.expectedInitialContractSha256 == link.expectedInitialContractSha256
        and receipt.terminalArchivePath == link.terminalArchivePath
        and receipt.terminalArchiveSha256 == link.terminalArchiveSha256
        and receipt.terminalReceiptPath == link.terminalReceiptPath
        and archive.locator.publicationRequestId == link.publicationRequestId
        and archive.manifest.worktreeGroup == link.worktreeGroup
        and archive.contractPath == receipt.contractPath
        and archive.contractPath == contract_path.as_posix()
    )


def _inside(path: Path, parent: Path) -> bool:
    """Whether ``path`` is the parent itself or stands strictly under it."""

    try:
        return path.is_relative_to(parent.resolve(strict=False))
    except OSError:  # pragma: no cover - an unresolvable path is not inside anything
        return False


def with_outside_scratch_reconcile(
    result: WorktreeCommandResult, *, dry_run: bool
) -> WorktreeCommandResult:
    """The terminal operation's own answer, carrying its outside-scratch sweep.

    The sweep runs on every cleanup, abandon and finalize run: a root whose generation has
    ended is deleted, and one that cannot be proved ended or removed is reported with its
    reason. A run that found nothing to reconcile leaves the answer exactly as it was, and a
    preview never deletes anything.
    """

    if dry_run:
        return result
    try:
        reconcile = reconcile_outside_scratch_roots()
    except Exception as error:
        return WorktreeCommandResult(
            result.returncode,
            {
                **result.payload,
                "outsideScratchReconcile": {
                    "removed": [],
                    "reported": [{"path": "", "reason": f"reconcile-failed: {error}"}],
                },
            },
        )
    if not (reconcile["removed"] or reconcile["reported"]):
        return result
    return WorktreeCommandResult(
        result.returncode,
        {**result.payload, "outsideScratchReconcile": reconcile},
    )


def _recorded_in_terminal_archive(
    coordination_root: Path,
    contract_path: Path,
) -> outside_scratch.OutsideScratchRoot | None:
    """The root this generation's own terminal archive records, or ``None`` without proof."""

    try:
        observed = inspect_lifecycle_operation_locator(coordination_root, contract_path)
    except Exception:
        return None
    if observed.state != "terminal-archived" or observed.locator is None:
        return None
    try:
        archive: TerminalEnclosureArchive | None = validate_terminal_proof(
            coordination_root, contract_path, observed.locator
        )
        if archive is None:
            return None
        archived: WorktreeContract = parse_contract_text(archive.contractText, path=contract_path)
    except Exception:
        return None
    return outside_scratch.recorded_root(archived)
