"""Focused proof for terminal-enclosure archive identity and byte invariants."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.models.lifecycles.enclosure import (
    LifecycleEnclosureLocator,
    TerminalEnclosureArchive,
    TerminalWorktreeAbandonArguments,
)
from agents_remember.worktrees.integration import terminal_enclosure_archive as archive_module
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from pydantic import ValidationError
from test_lifecycle_operations import _contract


def _archive_payload(tmp_path: Path) -> dict[str, object]:
    contract = _contract(tmp_path)
    result = terminal_archive_required_result(
        contract,
        operation="worktree_abandon",
        arguments=TerminalWorktreeAbandonArguments(force=False),
        dry_run=False,
    )
    assert result.returncode == 0, result.payload
    archive = TerminalEnclosureArchive.model_validate_json(
        Path(str(result.payload["archivePath"])).read_bytes()
    )
    return archive.model_dump(mode="json")


def test_locator_refuses_inconsistent_state_and_successor_identity(tmp_path: Path) -> None:
    payload = _archive_payload(tmp_path)
    locator = deepcopy(payload["locator"])
    assert isinstance(locator, dict)
    locator["provenManifestSha256"] = "b" * 64
    with pytest.raises(ValidationError, match="inconsistent publication proof"):
        LifecycleEnclosureLocator.model_validate(locator)

    successor = deepcopy(payload["locator"])
    assert isinstance(successor, dict)
    successor["publicationKind"] = "successor-enclosure"
    successor["predecessorTerminal"] = None
    with pytest.raises(ValidationError, match="successor enclosure"):
        LifecycleEnclosureLocator.model_validate(successor)


def test_terminal_archive_refuses_every_identity_and_byte_contradiction(
    tmp_path: Path,
) -> None:
    original = _archive_payload(tmp_path)

    identity = deepcopy(original)
    identity["contractPath"] = "/tmp/other-contract.json"

    digest = deepcopy(original)
    digest["contractSha256"] = "b" * 64

    request = deepcopy(original)
    request["cleanupRequestId"] = "b" * 64

    duplicate = deepcopy(original)
    duplicate_entries = duplicate["canonicalEntries"]
    assert isinstance(duplicate_entries, list)
    duplicate_entries.append(deepcopy(duplicate_entries[0]))

    missing_manifest = deepcopy(original)
    missing_entries = missing_manifest["canonicalEntries"]
    assert isinstance(missing_entries, list)
    assert isinstance(missing_entries[0], dict)
    missing_entries[0]["relativePath"] = "other.json"

    contradictory_manifest = deepcopy(original)
    contradictory_entries = contradictory_manifest["canonicalEntries"]
    assert isinstance(contradictory_entries, list)
    assert isinstance(contradictory_entries[0], dict)
    content = "{}\n"
    contradictory_entries[0].update(
        {
            "content": content,
            "sizeBytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    )

    locator_proof = deepcopy(original)
    locator = locator_proof["locator"]
    assert isinstance(locator, dict)
    locator["expectedManifestSha256"] = "b" * 64
    locator["provenManifestSha256"] = "b" * 64

    cases = (
        (identity, "identity disagree"),
        (digest, "contract digest"),
        (request, "cleanup request identity"),
        (duplicate, "paths must be unique"),
        (missing_manifest, "exact enclosure manifest entry"),
        (contradictory_manifest, "contradicts the typed manifest"),
        (locator_proof, "contradict locator proof"),
    )
    for payload, message in cases:
        with pytest.raises(ValidationError, match=message):
            TerminalEnclosureArchive.model_validate(payload)


def test_terminal_archive_refuses_a_nonterminal_operation() -> None:
    with pytest.raises(RuntimeError, match="nonterminal"):
        archive_module._require_terminal_operation(_value(status="running"), "closeout")


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))
