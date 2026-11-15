"""Recording work already on its source line keeps stale-entry debt rather than refusing it.

The cases pin the three moving parts: the recognition of a stale-only refusal, the record cell that
keeps the names across a publication round-trip, and the cleanup refusal that names the route.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

import anyio
import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.application.worktree_tools import LandedCommits
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools.worktree import worktree_record_landing_payload
from agents_remember.worktrees.modules import record_landing as record_owner
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.record_landing import (
    _stale_answer,
    _stale_key,
    _stale_only_findings,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from test_knowledge_closeout_gate import ADMITTED, anchor_at, commit, invariant, sidecar
from test_record_landing import _commit_on, _fixture, _synced

STALE = "knowledge-stale-at-landing"
HEADER = (
    "the mandatory invariant gate (MIK-R09) refuses: 2 finding(s) for MASTER; each needs an "
    "authored row, a readable input or a valid tree (no waiver exists)"
)
FIRST = (
    "- pkg/a.py: [knowledge-stale-at-landing] RLZ-A00001 of INV-AAAAAA is stale at the master's "
    "code commit " + "a" * 40 + " (the anchor's blob is not the file's); the master's net diff "
    "changed its path and no row and re-anchor made it current"
)
SECOND = (
    "- pkg/b.py: [knowledge-stale-at-landing] RLZ-B00001 of INV-BBBBBB is stale at the master's "
    "code commit " + "a" * 40 + " (the anchor's blob is not the file's); the master's net diff "
    "changed its path and no row and re-anchor made it current"
)


def test_a_stale_only_refusal_is_recognised_and_no_other_is() -> None:
    lines = [HEADER, FIRST, SECOND]
    assert _stale_only_findings(lines) == [FIRST, SECOND]

    # One finding of another kind makes it a refusal again.
    mixed = [HEADER, FIRST, "- pkg/a.py: [R22.3-markers] the marker is missing"]
    assert _stale_only_findings(mixed) == []

    # A truncated refusal cannot prove the rest is stale.
    truncated = [HEADER, FIRST, "- ... and 3 more"]
    assert _stale_only_findings(truncated) == []

    # Findings alone, with no header, still parse; no findings at all is no refusal.
    assert _stale_only_findings([FIRST]) == [FIRST]
    assert _stale_only_findings([HEADER]) == []


def test_the_answer_and_the_record_keep_the_same_entries() -> None:
    lines = [HEADER, FIRST, SECOND]
    answer = _stale_answer(lines[1:])
    assert answer["count"] == 2
    assert answer["record"] == ["pkg/a.py#RLZ-A00001", "pkg/b.py#RLZ-B00001"]
    assert "knowledge-maintenance leaf" in str(answer["remedy"])
    assert _stale_key(FIRST) == "pkg/a.py#RLZ-A00001"

    with tempfile.TemporaryDirectory() as tmp:
        contract = _fixture(Path(tmp))
        kept = answer["record"]
        assert isinstance(kept, list)
        recorded = replace(contract, stale_entries=tuple(str(one) for one in kept))
        write_contract(recorded.contract_path, recorded)
        stored = load_contract(recorded.contract_path)
        assert stored.stale_entries == ("pkg/a.py#RLZ-A00001", "pkg/b.py#RLZ-B00001")
        text = recorded.contract_path.read_text(encoding="utf-8")
        assert "stale_entries: pkg/a.py#RLZ-A00001 | pkg/b.py#RLZ-B00001" in text

        # A contract without the cell round-trips as "no debt", not as an empty string entry.
        write_contract(contract.contract_path, contract)
        assert load_contract(contract.contract_path).stale_entries == ()


@pytest.mark.integration
@pytest.mark.parametrize("dry_run", [True, False])
def test_the_record_landing_tool_records_a_reconciled_source(tmp_path: Path, dry_run: bool) -> None:
    contract, landed, landed_memory = _landed_with_a_stale_entry(tmp_path)
    # Sync reconciles the landed source and moves the comparison base to its tip.
    contract = _synced(tmp_path, contract)
    answer = anyio.run(_record_tool, contract, landed, landed_memory, dry_run)

    assert answer["state"] == ("would-record" if dry_run else "recorded"), answer
    assert "staleEntries" not in answer
    stored = load_contract(contract.contract_path)
    assert stored.stale_entries == ()
    assert stored.integrated_code_commit == ("" if dry_run else landed)


@pytest.mark.integration
@pytest.mark.parametrize("dry_run", [True, False])
def test_the_record_landing_tool_names_the_entries_it_keeps(tmp_path: Path, dry_run: bool) -> None:
    contract, landed, landed_memory = _landed_with_a_stale_entry(tmp_path)
    memory = contract.memory_repo_path
    assert memory is not None
    request = record_owner._landing_request(contract, memory, landed, landed_memory)
    assert not isinstance(request, str), request
    findings = record_owner.landing_gate_refusal(request)
    assert findings is not None
    debt = _stale_only_findings(findings.splitlines())
    assert len(debt) == 1, findings
    contract = _synced(tmp_path, contract)
    original_preconditions = record_owner._record_preconditions

    def with_debt(
        current: WorktreeContract, code_commit: str, memory_commit: str
    ) -> tuple[WorktreeCommandResult | None, list[str], list[str]]:
        refusal, targets, current_debt = original_preconditions(current, code_commit, memory_commit)
        assert refusal is None, refusal
        assert targets
        assert current_debt == []
        # Carry the gate's actual debt across the boundary after sync clears the net diff.
        # Admission and chain checks still run; this case proves answer and record transport.
        return refusal, targets, debt

    config = load_config(contract.code_repo_path.parent / "settings.json")
    arguments = {"contract_path": str(contract.contract_path), "dry_run": dry_run}
    before = contract.contract_path.read_bytes()
    with (
        mock.patch.object(record_owner, "_record_preconditions", side_effect=with_debt),
        tool_call_context("worktree_record_landing", arguments, config=config),
    ):
        payload = worktree_record_landing_payload(
            config,
            str(contract.contract_path),
            landed=LandedCommits(landed, landed_memory),
            dry_run=dry_run,
        )
        answer = complete_tool_response("worktree_record_landing", payload, config=config)

    assert answer["state"] == ("would-record" if dry_run else "recorded"), answer
    entries = answer["staleEntries"]
    assert isinstance(entries, dict)
    assert entries["count"] == 1
    assert entries["record"] == ["base.txt#RLZ-A00001"]
    assert len(entries["entries"]) == 1
    assert (
        "base.txt: [knowledge-stale-at-landing] RLZ-A00001 of INV-AAAAAA" in entries["entries"][0]
    )
    assert "knowledge-maintenance leaf" in entries["remedy"]
    saved = json.loads(Path(str(answer["log"])).read_text(encoding="utf-8"))
    assert saved["staleEntries"] == entries == payload["staleEntries"]
    stored = load_contract(contract.contract_path)
    expected = () if dry_run else ("base.txt#RLZ-A00001",)
    assert stored.stale_entries == expected
    if dry_run:
        assert contract.contract_path.read_bytes() == before


@pytest.mark.integration
@pytest.mark.parametrize("dry_run", [True, False])
def test_stale_entry_debt_does_not_skip_the_recorded_master_chain(
    tmp_path: Path, dry_run: bool
) -> None:
    contract, landed, landed_memory = _landed_with_a_stale_entry(tmp_path)
    before = contract.contract_path.read_bytes()

    answer = anyio.run(_record_tool, contract, landed, landed_memory, dry_run)

    assert answer["state"] == "refused", answer
    assert answer["code"] == "knowledge-gate-refused", answer
    assert "code ref adds history beyond the exact leaf landing chain" in str(
        answer.get("summary")
    ), answer
    assert "staleEntries" not in answer
    assert contract.contract_path.read_bytes() == before


def _landed_with_a_stale_entry(root: Path) -> tuple[WorktreeContract, str, str]:
    contract = _fixture(root)
    memory = contract.memory_repo_path
    assert memory is not None
    anchor = anchor_at(
        contract.code_repo_path,
        "base.txt",
        {"kind": "file"},
        (contract.code_repo_path / "base.txt").read_bytes(),
    )
    _git(memory, "switch", "super")
    _git(memory, "config", "user.email", "fixture@example.invalid")
    _git(memory, "config", "user.name", "gate fixture")
    commit(
        memory,
        {
            "knowledge/invariants/INV-AAAAAA-base.json": invariant(
                "INV-AAAAAA", "The base value is preserved.", admission=ADMITTED
            ),
            "onboarding/base.txt.md": "# Base value\n",
            "onboarding/base.txt.json": sidecar(
                "base.txt",
                [
                    {
                        "id": "RLZ-A00001",
                        "invariant": "INV-AAAAAA",
                        "anchor": anchor,
                        "role": "primary-authority",
                        "rationale": "The file defines the base value.",
                    }
                ],
            ),
        },
        trailer=contract.code_base_commit,
    )
    contract = _synced(root, contract)
    # Keep the contract's source as the repository default, as ordinary recording fixtures do.
    landed = _commit_on(contract.code_repo_path, "super", "base.txt")
    _git(memory, "switch", "super")
    landed_memory = commit(memory, {}, trailer=landed)
    return contract, landed, landed_memory


async def _record_tool(
    contract: WorktreeContract, landed: str, landed_memory: str, dry_run: bool
) -> dict[str, object]:
    config = load_config(contract.code_repo_path.parent / "settings.json")
    server = create_server(config)
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "worktree_record_landing",
            {
                "contract_path": contract.contract_path.as_posix(),
                "landed_code_commit": landed,
                "landed_memory_content_commit": landed_memory,
                "dry_run": dry_run,
            },
        )
    assert result.isError is False, result
    text = next(block.text for block in result.content if isinstance(block, TextContent))
    return json.loads(text)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=ar@example.invalid", "-c", "user.name=AR", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
