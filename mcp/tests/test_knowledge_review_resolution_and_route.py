"""The Intent Reviewer surface's resolution and transport: the pair it serves and how it refuses.

These cases drive the same real adapter over the same real two-snapshot comparison as
:mod:`test_knowledge_review_surface`, reusing that module's request, resolution and render helpers,
and measure the boundary in front of the panes rather than the panes themselves:

* the candidate is resolved from canonical task context and never from a browser-chosen path, and a
  resolution that cannot be made yields no fallback dataset and no fabricated record;
* an absent candidate dataset, an absent baseline half and a before side that is present but
  unreadable are each a typed refusal naming the side, on the composition and on the entry route
  alike, instead of a substituted empty dataset or a storage error raised from inside the read;
* the transport admits exactly the two reviewable selector kinds, serializes the port's own typed
  result, refuses by name with no adapter, and carries a refresh's previous comparison identity all
  the way to the composition that compares it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    ReviewCandidateResolution,
    compose_review,
    list_knowledge_review_entries,
    resolve_review_candidate,
    review_records_for,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.serving.review import register_review_routes, review_request_from_query
from diff_scope_test_support import DiffFixture, build_diff_fixture
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_knowledge_review_surface import (
    REPOSITORY_LEAF,
    render,
    resolution_for,
    review_config,
    review_request,
)

pytestmark = pytest.mark.evidence_unit


@pytest.fixture
def fixture(tmp_path: Path) -> DiffFixture:
    """One fresh two-snapshot fixture per case; no case observes another's candidate state."""

    return build_diff_fixture(tmp_path / "review")


# -- resolution and transport -------------------------------------------------------------------


def test_the_candidate_is_resolved_from_task_context_and_never_from_a_browser_chosen_path() -> None:
    """A path-shaped selector is refused rather than resolved, and no fallback dataset is used."""

    config = review_config()
    for bad in ("../escape", "a/b", ".hidden", ""):
        outcome = resolve_review_candidate(config, "agents-remember", "master", bad)
        assert not isinstance(outcome, ReviewCandidateResolution)
        assert outcome.code == "candidate_unresolved"
    missing = resolve_review_candidate(
        config, "agents-remember", "260915_knowledge-substrate", "L1"
    )
    assert not isinstance(missing, ReviewCandidateResolution)
    assert missing.code == "candidate_unresolved"
    assert missing.next_action


def test_an_absent_candidate_dataset_refuses_by_name_rather_than_substituting_one(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """A candidate whose dataset is gone is refused; no other dataset is read in its place."""

    resolution = ReviewCandidateResolution(
        repository_id=fixture.repository_id,
        leaf_id=REPOSITORY_LEAF,
        baseline_database=fixture.before.database_path,
        candidate_database=tmp_path / "absent" / "knowledge-candidate.sqlite",
        baseline_code_root=fixture.before.git_root,
        candidate_code_root=fixture.after.git_root,
        baseline_code_tree_id=fixture.before_tree_id,
        candidate_code_tree_id=fixture.after_tree_id,
    )
    result = compose_review(resolution, review_request(fixture))
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "candidate_dataset_absent"
    assert result.refusal.next_action


def test_an_absent_baseline_half_refuses_by_name_rather_than_substituting_an_empty_one(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """A BEFORE side that is gone refuses; the surface never invents an empty before-generation.

    The sibling case above removes the candidate. This one removes the dataset the candidate is
    compared *against* -- the state a cold-start repository reached until the first knowledge write
    established that side, and the state a leaf reaches when the fork point it named is missing or
    unreadable. The requirement is explicit that a missing historical dataset is not empty history:
    the surface refuses and names *which* half is missing, because the two halves are different facts
    about a leaf -- one is answered by authoring a candidate, the other by placing the dataset it
    forks from -- and answering either with a freshly created empty dataset is how a missing history
    comes to be displayed as a measured one.
    """

    resolution = ReviewCandidateResolution(
        repository_id=fixture.repository_id,
        leaf_id=REPOSITORY_LEAF,
        baseline_database=tmp_path / "absent-baseline" / "knowledge-candidate.sqlite",
        candidate_database=fixture.after.database_path,
        baseline_code_root=fixture.before.git_root,
        candidate_code_root=fixture.after.git_root,
        baseline_code_tree_id=fixture.before_tree_id,
        candidate_code_tree_id=fixture.after_tree_id,
    )
    result = compose_review(resolution, review_request(fixture))
    assert result.state == "refused"
    assert result.payload is None
    assert result.refusal is not None
    assert result.refusal.code == "candidate_dataset_absent"
    assert "baseline" in result.refusal.detail, result.refusal.detail
    assert result.refusal.offending_input == resolution.baseline_database.name
    assert result.refusal.next_action
    assert not resolution.baseline_database.exists(), (
        "the refused review created the before side it was asked about, so a missing historical "
        "dataset has been replaced with a newly empty one"
    )


def test_a_before_side_that_is_present_but_unreadable_refuses_by_name(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """A corrupt before side is a typed refusal, not a storage error raised from inside the read.

    A comparison is between two dataset files, so a file that is not a dataset used to make the read
    raise ``apsw.NotADBError`` out of this composition: the operator got a traceback where the surface
    promises a state that names the side and the action. The corrupt side is exercised on its own,
    with the candidate intact, because that is the shape a leaf reaches when the dataset it forked
    from was replaced by something else -- and the refusal has to name the *before* side rather than
    the candidate, since only one of the two can be repaired by authoring knowledge again.
    """

    corrupt = tmp_path / "corrupt-baseline" / "knowledge-candidate.sqlite"
    corrupt.parent.mkdir()
    corrupt.write_bytes(b"this is not a database\n")
    resolution = ReviewCandidateResolution(
        repository_id=fixture.repository_id,
        leaf_id=REPOSITORY_LEAF,
        baseline_database=corrupt,
        candidate_database=fixture.after.database_path,
        baseline_code_root=fixture.before.git_root,
        candidate_code_root=fixture.after.git_root,
        baseline_code_tree_id=fixture.before_tree_id,
        candidate_code_tree_id=fixture.after_tree_id,
    )
    result = compose_review(resolution, review_request(fixture))
    assert result.state == "refused"
    assert result.payload is None
    assert result.refusal is not None
    assert result.refusal.code == "candidate_dataset_absent"
    assert "baseline" in result.refusal.detail, result.refusal.detail
    assert "present but cannot be read" in result.refusal.detail, result.refusal.detail
    assert str(corrupt) in result.refusal.detail, result.refusal.detail
    assert result.refusal.offending_input == corrupt.name
    assert result.refusal.next_action
    assert corrupt.read_bytes() == b"this is not a database\n", (
        "the refused review rewrote the side it could not read"
    )


ENTRY_MASTER = "260921_icr"


def _entry_route_config(root: Path, fixture: DiffFixture, code_root: Path) -> McpRuntimeConfig:
    """A configuration whose one recorded enclosure resolves to a pair this case owns.

    The entry route resolves its pair from canonical task context and the browser never names a
    dataset, so a case that drives that route has to *be* that context rather than hand it a
    resolution: a coordination root, one enclosure contract under it, and the leaf root the
    contract's own recorded worktree group derives. The code side points at the fixture's own
    repository, because a resolution requires a live code worktree and this case is about the
    knowledge halves.

    The repository the contract names is the fixture's own namespace, because the halves are copied
    from datasets bound to it and carry no receipt beside them: a candidate with no receipt is read
    under the requested name, which is the shipped fallback for a pair a caller assembled itself.
    """

    repository_id = fixture.repository_id
    contract_path = (
        root / "coordination" / "tasks" / repository_id / ENTRY_MASTER / "enclosures" / "leaf"
    )
    contract_path.mkdir(parents=True)
    contract_file = contract_path / "series-contract.md"
    contract_file.write_text(
        "---\n"
        "schema: ar-series-contract/v1\n"
        "schemaVersion: 1.0\n"
        "kind: leaf\n"
        "task_id: 260921_ENTRY\n"
        "task_name: entry_route\n"
        f"repo_name: {repository_id}\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "\n"
        "coordination:\n"
        f"  root: {root}\n"
        f"  task_root: {root / 'tasks'}\n"
        f"  task_artifact: {root / 'tasks' / 'task.md'}\n"
        f"  worktree_group: {root / 'leaf'}\n"
        f"  leaf_id: {REPOSITORY_LEAF}\n"
        f"  parent_task_name: {ENTRY_MASTER}\n"
        "\n"
        "code:\n"
        f"  repo_path: {code_root}\n"
        "  source_branch: main\n"
        "  work_branch: ar/entry\n"
        f"  base_commit: {fixture.before_tree_id}\n"
        f"  worktree: {code_root}\n"
        "\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {code_root}\n"
        "  source_branch: main\n"
        "  work_branch: ar/entry\n"
        f"  base_commit: {fixture.after_tree_id}\n"
        f"  worktree: {code_root}\n"
        f"  ledger: {code_root / 'memory.md'}\n"
        "---\n",
        encoding="utf-8",
    )
    return McpRuntimeConfig(
        workspace_root=root,
        coordination_root=root / "coordination",
        config_path=root / "config.json",
        transcript_root=root / "transcripts",
    )


def test_the_entry_route_refuses_a_damaged_before_half_instead_of_raising(
    fixture: DiffFixture, tmp_path: Path
) -> None:
    """The entry list answers an unreadable before side the same way the composition does.

    The subject list is the *first* call a reader's surface makes, and it compares every identity the
    candidate records against the pair -- so a before half holding bytes that are not a dataset made
    the per-subject comparison raise ``apsw.NotADBError`` out of the route that exists to offer a
    subject. Both routes now state the pair refusals before any comparison runs.

    The pair is resolved the way production resolves it, from canonical task context, and the case
    proves the refusal is caused by the corruption rather than by a fixture that could never answer:
    replacing the damaged side with the dataset that actually belongs there turns the same route into
    an entry list.
    """

    config = _entry_route_config(tmp_path, fixture, fixture.after.git_root)
    repository_id = fixture.repository_id
    resolved = resolve_review_candidate(config, repository_id, ENTRY_MASTER, REPOSITORY_LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    resolved.candidate_database.parent.mkdir(parents=True, exist_ok=True)
    resolved.candidate_database.write_bytes(fixture.after.database_path.read_bytes())
    resolved.baseline_database.parent.mkdir(parents=True, exist_ok=True)
    resolved.baseline_database.write_bytes(b"this is not a database\n")

    refused = list_knowledge_review_entries(config, repository_id, ENTRY_MASTER, REPOSITORY_LEAF)
    assert refused.state == "refused", refused
    assert refused.entries == ()
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"
    assert "baseline" in refused.refusal.detail, refused.refusal.detail
    assert "present but cannot be read" in refused.refusal.detail, refused.refusal.detail
    assert str(resolved.baseline_database) in refused.refusal.detail, refused.refusal.detail
    assert refused.refusal.next_action

    resolved.baseline_database.write_bytes(fixture.before.database_path.read_bytes())
    offered = list_knowledge_review_entries(config, repository_id, ENTRY_MASTER, REPOSITORY_LEAF)
    assert offered.state == "entries", offered
    assert fixture.retry_invariant_id in {entry.selector_id for entry in offered.entries}, offered


def test_the_transport_admits_exactly_the_two_reviewable_selector_kinds() -> None:
    """Only an invariant or family identity is a reviewable subject; nothing else is mapped onto one."""

    invariant = review_request_from_query(
        "agents-remember",
        "master",
        "leaf",
        "invariant",
        "11111111-1111-1111-1111-111111111111",
    )
    assert invariant is not None and invariant.selector is not None
    assert invariant.selector.kind == "invariant"
    family = review_request_from_query(
        "agents-remember", "master", "leaf", "family", "11111111-1111-1111-1111-111111111111"
    )
    assert family is not None and family.selector is not None
    assert family.selector.kind == "family"
    for refused in ("path", "invariant_revision", "", "latest"):
        assert review_request_from_query("agents-remember", "master", "leaf", refused, "x") is None


def test_the_route_serves_the_typed_result_and_refuses_by_name_with_no_adapter(
    fixture: DiffFixture,
) -> None:
    """The route is transport only: it serializes the port's own result and refuses without one."""

    config = review_config()
    request = review_request(fixture)
    payload = render(fixture)
    served = FastAPI()
    register_review_routes(
        served, config, lambda _: compose_review(resolution_for(fixture), request)
    )
    with TestClient(served) as client:
        body = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
            },
        )
        assert body.status_code == 200
        assert body.json()["state"] == "review"
        assert payload.comparison is not None
        assert (
            body.json()["payload"]["comparison"]["binding_digest"]
            == payload.comparison.binding_digest
        )
        bad = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "latest",
                "selectorId": "x",
            },
        )
        assert bad.status_code == 400
        assert bad.json()["status"] == "bad-request"

    unwired = FastAPI()
    register_review_routes(unwired, config, None)
    with TestClient(unwired) as client:
        refused = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
            },
        )
        assert refused.status_code == 503
        assert refused.json()["status"] == "unavailable"


def test_the_previous_binding_identity_reaches_the_port_and_is_compared_against_the_read(
    fixture: DiffFixture,
) -> None:
    """A refresh's identity travels the whole way and the answer names it (ICR-R17@v1).

    One property per hop. The TRANSPORT admits the previous identity in its own vocabulary -- a
    spelling that is not a comparison digest is refused with the offending input named, exactly as
    the page-size parameter is -- and hands it to the port on the request rather than dropping it;
    the COMPOSITION compares it against the comparison it rendered, where a disagreement is the
    ``stale`` state that labels the identity the reader was looking at and disables submission
    against the new one. The identity is the caller's recorded input and never a substitute for the
    current one: the digest that comes back as ``comparison.binding_digest`` is the comparison
    operation's, and the previous one only ever appears as ``previous_comparison_ref``.
    """

    config = review_config()
    request = review_request(fixture)
    previous = "9" * 64
    asked: list[ReviewSurfaceRequest] = []
    served = FastAPI()
    register_review_routes(
        served,
        config,
        lambda incoming: (
            asked.append(incoming),
            compose_review(resolution_for(fixture), incoming),
        )[1],
    )
    with TestClient(served) as client:
        refreshed = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
                "previousBindingDigest": previous,
            },
        )
        assert refreshed.status_code == 200
        assert asked[-1].previous_binding_digest == previous
        body = refreshed.json()["payload"]
        assert body["staleness"]["state"] == "stale"
        assert body["staleness"]["previous_comparison_ref"] == previous
        assert body["submission"]["state"] == "disabled_stale"
        assert body["comparison"]["binding_digest"] != previous

        # A read that replaces nothing carries nothing, and is current by construction rather than by
        # assumption: there is no previous input to label.
        plain = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
            },
        )
        assert plain.status_code == 200
        assert asked[-1].previous_binding_digest is None
        assert plain.json()["payload"]["staleness"]["state"] == "current"

        malformed = client.get(
            "/api/review/intent",
            params={
                "repo": request.repository_id,
                "master": request.master,
                "leaf": request.leaf_id,
                "selectorKind": "invariant",
                "selectorId": fixture.retry_invariant_id,
                "previousBindingDigest": "not-a-digest",
            },
        )
        assert malformed.status_code == 400
        assert malformed.json()["offendingInput"] == "not-a-digest"


def test_the_published_assessment_loader_returns_nothing_for_an_unresolvable_candidate() -> None:
    """A candidate with no readable authority yields an empty collection, not a fabricated record.

    The same unresolvable context is asked for the surface's *entry* list (260915-KS-L45 S2): the
    entry read resolves through the identical operation the review does, so it refuses with the same
    code rather than answering with an empty list. An empty list would read as "this candidate
    records nothing to review", which is a different fact from "no candidate resolves here", and the
    task view renders no entry for either -- but only one of the two is what happened.
    """

    config = review_config()
    request = ReviewSurfaceRequest(
        repository_id="agents-remember",
        master="260915_knowledge-substrate",
        leaf_id=REPOSITORY_LEAF,
        selector=InvariantIdentitySeed(invariant_id="11111111-1111-1111-1111-111111111111"),
    )
    assert review_records_for(config, request).assessments == ()

    entries = list_knowledge_review_entries(
        config, request.repository_id, request.master, request.leaf_id
    )
    assert entries.state == "refused"
    assert entries.refusal is not None
    assert entries.refusal.code == "candidate_unresolved"
    assert entries.entries == ()
