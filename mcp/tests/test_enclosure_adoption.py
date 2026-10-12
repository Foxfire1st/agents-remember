"""MIK-R82 rule 4: ``worktree_enclosure_adopt`` registered as documented.

Every case here fails without the change: the tool, its payload builder, its
response model and its roster/registry entries do not exist on the base, and a
refusal that names ``worktree_enclosure_adopt`` as its next action names
nothing callable.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.registration.worktrees import register_worktree_tools
from agents_remember.mcp.tools.enclosure_adoption import (
    EnclosureAdoptionRequest,
    worktree_enclosure_adopt_payload,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.worktree import WorktreeEnclosureAdoptResponse
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.worktree_contract import write_contract
from lifecycle_enclosure_test_support import enclosure_contract
from mcp.server.fastmcp import FastMCP

_RATIONALE = "L82 test: adopt the scratch pre-locator enclosure"
_LEGACY_ARTIFACT = "integrate-operation.generation-1.json"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _scratch_world(base: Path) -> tuple[McpRuntimeConfig, Path, Path]:
    """A coordination root with one git repo and one pre-locator leaf contract."""

    root = base / "adopt-world"
    coordination = root / "coordination"
    code_repo = root / "repo"
    code_repo.mkdir(parents=True, exist_ok=True)
    _git(code_repo, "init", "-b", "main")
    _git(code_repo, "config", "user.email", "test@example.invalid")
    _git(code_repo, "config", "user.name", "Agents Remember Tests")
    (code_repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(code_repo, "add", "README.md")
    _git(code_repo, "commit", "-m", "base")
    head = _git(code_repo, "rev-parse", "HEAD")
    _git(code_repo, "update-ref", "refs/remotes/origin/main", head)
    _git(code_repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    contract, _text = enclosure_contract(root, kind="leaf")
    contract = dataclasses.replace(
        contract,
        code_repo_path=code_repo,
        code_source_branch="main",
        code_base_commit=head,
    )
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)

    reports = contract.worktree_group / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / _LEGACY_ARTIFACT).write_text('{"generation": 1}\n', encoding="utf-8")

    config = McpRuntimeConfig(
        config_path=coordination / "settings.json",
        coordination_root=coordination,
        workspace_root=coordination,
        transcript_root=coordination / "logs" / "mcp",
        repositories={"repo": RepositoryScope(repo_id="repo", path=code_repo)},
    )
    # The fixture contract lives under root/coordination; the config must agree.
    assert contract.coordination_root.resolve(strict=False) == coordination.resolve(strict=False), (
        contract.coordination_root,
        coordination,
    )
    return config, contract.contract_path, contract.worktree_group


def _request(
    contract_path: Path, group: Path, *, dry_run: bool = True, **overrides: Any
) -> EnclosureAdoptionRequest:
    arguments: dict[str, Any] = {
        "contract_path": contract_path.as_posix(),
        "expected_worktree_group": group.as_posix(),
        "rationale": _RATIONALE,
        "dry_run": dry_run,
    }
    arguments.update(overrides)
    return EnclosureAdoptionRequest(**arguments)


def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path) -> None:
    """The documented call previews the adoption and leaves every byte alone."""

    config, contract_path, group = _scratch_world(tmp_path)

    request = _request(contract_path, group, dry_run=True)
    with tool_call_context(
        "worktree_enclosure_adopt",
        {
            "contract_path": contract_path.as_posix(),
            "expected_worktree_group": group.as_posix(),
            "rationale": _RATIONALE,
            "dry_run": True,
        },
        config=config,
    ):
        payload = worktree_enclosure_adopt_payload(config, request)

    assert payload["ok"] is True
    # MIK-R108 rules 1-2: the caller answer carries the outcome and the next
    # step, not the envelope; `operation` stays in the whole result for the
    # report and the log. The answer points at it through `log` (rule 3).
    assert "operation" not in payload
    assert isinstance(payload.get("log"), str), "the answer must point at its whole result"
    whole = json.loads(Path(str(payload["log"])).read_text(encoding="utf-8"))
    assert whole["operation"] == "worktree_enclosure_adopt"
    assert payload["state"] == "would-adopt-enclosure"
    assert payload["dryRun"] is True
    assert payload["nextAction"] == "adopt"
    assert payload["nextTool"] == "worktree_enclosure_adopt"
    apply_args = payload["nextArgs"]
    assert apply_args["dry_run"] is False
    assert apply_args["approved"] is True
    assert apply_args["expected_publication_request_id"] == payload["publicationRequestId"]
    assert [item["name"] for item in payload["artifacts"]] == [_LEGACY_ARTIFACT]

    observation = inspect_lifecycle_operation_locator(config.coordination_root, contract_path)
    assert observation.state == "missing"
    assert not (group / ".lifecycle" / "enclosure-adoption-receipt.json").exists()
    assert (group / "reports" / _LEGACY_ARTIFACT).is_file()


def test_real_run_without_approval_is_refused(tmp_path: Path) -> None:
    """The real run needs the explicit approval the dry run advertises."""

    config, contract_path, group = _scratch_world(tmp_path)

    payload = worktree_enclosure_adopt_payload(
        config, _request(contract_path, group, dry_run=False, approved=False)
    )

    assert payload["ok"] is False
    assert payload["state"] == "enclosure-adoption-approval-required"
    assert payload["nextTool"] == "worktree_enclosure_adopt"
    assert (
        payload["nextArgs"]["expected_publication_request_id"]
        == worktree_enclosure_adopt_payload(config, _request(contract_path, group, dry_run=True))[
            "publicationRequestId"
        ]
    )
    assert (
        inspect_lifecycle_operation_locator(config.coordination_root, contract_path).state
        == "missing"
    )


def test_blank_rationale_is_refused(tmp_path: Path) -> None:
    """The documented non-blank rationale is enforced, never defaulted."""

    config, contract_path, group = _scratch_world(tmp_path)

    with pytest.raises(ValueError, match="nonblank rationale"):
        worktree_enclosure_adopt_payload(config, _request(contract_path, group, rationale="   "))


def test_real_run_with_approval_adopts_and_repeats(tmp_path: Path) -> None:
    """Approval publishes the locator; repeating proves the same receipt."""

    config, contract_path, group = _scratch_world(tmp_path)
    preview = worktree_enclosure_adopt_payload(config, _request(contract_path, group, dry_run=True))
    request_id = preview["publicationRequestId"]

    applied = worktree_enclosure_adopt_payload(
        config,
        _request(
            contract_path,
            group,
            dry_run=False,
            approved=True,
            expected_publication_request_id=request_id,
        ),
    )

    assert applied["ok"] is True
    assert applied["state"] == "enclosure-adopted"
    assert applied["publicationRequestId"] == request_id
    assert applied["contractSha256"] == preview["contractSha256"]
    assert applied["manifestSha256"] == preview["manifestSha256"]

    observation = inspect_lifecycle_operation_locator(config.coordination_root, contract_path)
    assert observation.state == "addressable"
    assert observation.locator is not None
    receipt = group / ".lifecycle" / "enclosure-adoption-receipt.json"
    assert receipt.is_file()
    assert (group / ".lifecycle" / _LEGACY_ARTIFACT).is_file()
    assert not (group / "reports" / _LEGACY_ARTIFACT).exists()

    repeated = worktree_enclosure_adopt_payload(
        config,
        _request(
            contract_path,
            group,
            dry_run=False,
            approved=True,
            expected_publication_request_id=request_id,
        ),
    )
    assert repeated["ok"] is True
    assert repeated["state"] == "enclosure-adopted"
    assert repeated["publicationRequestId"] == request_id

    preview_again = worktree_enclosure_adopt_payload(
        config, _request(contract_path, group, dry_run=True)
    )
    assert preview_again["ok"] is True
    assert preview_again["publicationRequestId"] == request_id


def test_registration_names_the_documented_tool() -> None:
    """Roster, response registry and live FastMCP surface all name the tool."""

    assert "worktree_enclosure_adopt" in PUBLIC_TOOLS
    assert TOOL_RESPONSE_MODELS["worktree_enclosure_adopt"] is WorktreeEnclosureAdoptResponse

    server = FastMCP("r82-adoption-registration-probe")
    register_worktree_tools(server, _StubConfig())  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert "worktree_enclosure_adopt" in tools
    schema = tools["worktree_enclosure_adopt"].inputSchema
    assert schema["type"] == "object"
    assert set(schema["required"]) == {
        "contract_path",
        "expected_worktree_group",
        "rationale",
    }
    assert schema["properties"]["dry_run"]["default"] is True
    assert schema["properties"]["approved"]["default"] is False
    assert "expected_publication_request_id" in schema["properties"]


class _StubConfig:
    """Registration-time config: no registrar validates the config itself."""

    def __getattr__(self, name: str) -> Any:
        return _StubConfig()
