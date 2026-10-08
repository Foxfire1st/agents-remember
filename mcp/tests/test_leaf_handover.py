"""A leaf handover addresses its sibling seats without pinning their occupants."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import test_role_instruction_wording as wording
from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.cli.role_launch_preparation import RoleHandoverRequest, _compile_handover
from agents_remember.models.role_launcher import RoleSelection


@pytest.fixture
def handovers():
    fixture = wording.HandoverTextWordingTests()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.doCleanups()


def test_leaf_roles_receive_exact_sibling_addresses_without_agent_ids(handovers) -> None:
    roles = ["worker", "reviewer", "curator"]
    for role in roles:
        prompt, handover = handovers.compiled(role, None)
        seats = handover["leafSeats"]
        assert seats == {
            "roles": roles,
            "roleMessageArguments": {
                sibling: {
                    "role": sibling,
                    "sprint_document_ref": {"repository": "repo", "path": "sprint/task.json"},
                    "master_document_ref": {"repository": "repo", "path": "master/task.json"},
                    "task_document_ref": {"repository": "repo", "path": "master/01_leaf.json"},
                }
                for sibling in roles
                if sibling != role
            },
        }
        assert json.dumps(seats, ensure_ascii=False, separators=(",", ":")) in prompt
        for sibling, arguments in seats["roleMessageArguments"].items():
            resolved = resolve_role_launch_context(
                handovers.config, RoleSelection.model_validate(arguments)
            )
            assert resolved.role == sibling
            assert resolved.task is not None
            assert resolved.task.ref.key == "repo/master/01_leaf.json"


def test_other_roles_and_leafless_contexts_receive_no_sibling_value(handovers) -> None:
    for role in ("architect", "orchestrator", "manager", "investigator"):
        _, handover = handovers.compiled(role, None)
        assert "leafSeats" not in handover
    context, workspace = handovers.launch_of("manager")
    # A non-leaf role is outside the launcher leaf seat; it must not
    # inherit sibling addresses even if a caller presents a leaf field.
    leaf_context, _ = handovers.launch_of("worker")
    context = replace(context, task=leaf_context.task)
    request = RoleHandoverRequest(
        config=handovers.config,
        context=context,
        workspace=workspace,
        agent_id="some-provider",
        ar_mcp_context={"scopeKind": "configured-projects"},
        request_id=wording.uuid.uuid4(),
    )
    assert "leafSeats" not in _compile_handover(request)["handover"]
