"""ICR-R16: every refusal the review transport publishes is actionable, in the body of its status.

The Intent Reviewer's route answers a refusal with the change-set routes' 400/404/503 idiom and puts
the answer in the *body*: either this route's own typed refusal (``state = "refused"``) or one of the
transport-level bodies it builds itself (a selector it does not admit, an unwired adapter, a port's
two named failures). A reader has to be able to act on whichever it is, so every one of them carries
a reason, the offending input when there is one, and the next action its own failure implies --
``nextAction`` on all of them, ``offendingInput`` where an input is what was refused.

This module is the fast transport contract for those shapes; it drives the REAL route over
``TestClient`` and reads the exact bodies. The same bodies were measured over real HTTP against the
production composition in this leaf's evidence run
(``ar-coordination/temp/icr/evidence-l16-refusals.txt``, §2/§3), and the real adapter's own refusals
plus the task-context review that keeps source inspection available while intent is not are measured
by ``test_knowledge_review_source_endpoints.py`` over a real enclosure; neither is re-derived here.

Nothing here re-implements the route: the ports are injected because the route is transport-only by
design (``serving`` ranks below ``application`` and reaches every answer through its port), so an
injected port is how the route's own two exception mappings are reached at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import (
    KnowledgeReviewResult,
    ReviewEntryListResult,
    ReviewRefusal,
)
from agents_remember.serving.review import (
    KNOWLEDGE_REVIEW_ENTRIES_ROUTE,
    KNOWLEDGE_REVIEW_ROUTE,
    register_review_routes,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.evidence_unit

TASK = {
    "repo": "agents-remember",
    "master": "260921_complete-code-and-intent-review",
    "leaf": "260921-ICR-L16",
}
SUBJECT = {"selectorKind": "invariant", "selectorId": "3c513a59-5e5f-428a-b5e2-7301fcb280f7"}
ACTIONABLE = ("status", "detail", "nextAction")


def runtime_config() -> McpRuntimeConfig:
    """A configuration naming no real root: these routes resolve nothing from it."""

    return McpRuntimeConfig(
        workspace_root=Path("/nonexistent-workspace"),
        coordination_root=Path("/nonexistent-coordination"),
        config_path=Path("/nonexistent-config.json"),
        transcript_root=Path("/nonexistent-transcripts"),
    )


def served(port: Any, entries: Any = None) -> TestClient:
    app = FastAPI()
    register_review_routes(app, runtime_config(), port, entries)
    return TestClient(app)


def typed_refusal(code: str, *, offending_input: str | None = None) -> ReviewRefusal:
    return ReviewRefusal(
        code=code,  # type: ignore[arg-type]  # the caller names one declared code
        detail=f"the {code} state is measured, not assumed",
        next_action="reopen the review from a task context this leaf really records",
        offending_input=offending_input,
    )


def test_an_unwired_adapter_answers_with_the_action_that_wires_it() -> None:
    """The process that cannot answer says so, on both routes, with what to do instead."""

    client = served(None)
    intent = client.get(KNOWLEDGE_REVIEW_ROUTE, params={**TASK, **SUBJECT})
    entries = client.get(KNOWLEDGE_REVIEW_ENTRIES_ROUTE, params=TASK)

    for response in (intent, entries):
        assert response.status_code == 503
        body = response.json()
        assert set(ACTIONABLE) <= set(body)
        assert body["status"] == "unavailable"
        assert body["detail"].strip()
        assert "composition root" in body["nextAction"]


def test_an_unadmitted_selector_names_the_input_and_the_admitted_kinds() -> None:
    """A validation failure is refused before any port runs, and names what was wrong."""

    def never_called(_request: Any) -> Any:
        raise AssertionError("the route must refuse an unadmitted selector before the port")

    client = served(never_called)
    response = client.get(
        KNOWLEDGE_REVIEW_ROUTE, params={**TASK, "selectorKind": "latest", "selectorId": "x"}
    )

    assert response.status_code == 400
    body = response.json()
    assert set(ACTIONABLE) | {"offendingInput", "expected"} <= set(body)
    assert body["status"] == "bad-request"
    assert body["offendingInput"] == "latest"
    assert "invariant" in body["expected"] and "family" in body["expected"]


def test_a_refused_authority_carries_the_action_that_clears_it() -> None:
    """The route's own AuthorityError mapping keeps a usable next action (the ICR-R16 addition)."""

    message = "repository 'not-admitted' is outside the configured workspace authority"

    def refuses(_request: Any) -> Any:
        raise AuthorityError(message)

    response = served(refuses).get(KNOWLEDGE_REVIEW_ROUTE, params={**TASK, **SUBJECT})

    assert response.status_code == 400
    body = response.json()
    assert set(ACTIONABLE) <= set(body)
    assert body["status"] == "bad-path"
    assert body["detail"] == message
    assert "authority" in body["nextAction"]


def test_a_missing_path_names_the_path_it_does_not_hold_and_a_next_action() -> None:
    """The route's own FileNotFoundError mapping keeps the path AND the action (ICR-R16)."""

    missing = "/workspace/tasks/agents-remember/260921-icr-l16/enclosures/gone"

    def refuses(_request: Any) -> Any:
        raise FileNotFoundError(missing)

    response = served(refuses).get(KNOWLEDGE_REVIEW_ROUTE, params={**TASK, **SUBJECT})

    assert response.status_code == 404
    body = response.json()
    assert set(ACTIONABLE) | {"path", "offendingInput"} <= set(body)
    assert body["status"] == "not-found"
    assert body["path"] == missing
    assert body["offendingInput"] == missing


def test_a_typed_refusal_travels_whole_in_the_body_of_its_own_status() -> None:
    """The packet's conforming example, at the transport: 404 + candidate_dataset_absent + actions."""

    refusal = typed_refusal(
        "candidate_dataset_absent", offending_input="knowledge-candidate.sqlite"
    )

    def refuses(_request: Any) -> KnowledgeReviewResult:
        return KnowledgeReviewResult(state="refused", repository_id=TASK["repo"], refusal=refusal)

    response = served(refuses).get(KNOWLEDGE_REVIEW_ROUTE, params={**TASK, **SUBJECT})

    assert response.status_code == 404
    body = response.json()
    assert body["state"] == "refused"
    assert "payload" not in body
    assert body["refusal"] == {
        "code": "candidate_dataset_absent",
        "detail": refusal.detail,
        "next_action": refusal.next_action,
        "offending_input": "knowledge-candidate.sqlite",
    }


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("candidate_dataset_absent", 404),
        ("candidate_not_live", 404),
        ("candidate_unresolved", 404),
        ("subject_unresolved", 404),
        ("comparison_refused", 400),
    ],
)
def test_the_entry_route_maps_each_refusal_code_onto_its_own_status(code: str, status: int) -> None:
    """The client reads the body whatever the status; the status family stays the route's own."""

    def refuses(_repository_id: str, _master: str, _leaf_id: str) -> ReviewEntryListResult:
        return ReviewEntryListResult(
            state="refused",
            repository_id=TASK["repo"],
            master=TASK["master"],
            leaf_id=TASK["leaf"],
            refusal=typed_refusal(code),
        )

    response = served(None, refuses).get(KNOWLEDGE_REVIEW_ENTRIES_ROUTE, params=TASK)

    assert response.status_code == status
    body = response.json()
    assert body["state"] == "refused"
    assert body["entries"] == []
    assert body["refusal"]["code"] == code
    assert body["refusal"]["next_action"].strip()
