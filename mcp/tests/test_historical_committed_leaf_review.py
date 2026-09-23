"""A committed or closed leaf reopens the exact comparison its records bound (``ICR-R12@v1``).

`ICR-R12@v1` requires a committed or closed leaf to reopen its recorded code-and-intent comparison
without a live enclosure, and it names the intake defect directly: the ordinary committed diff
succeeds for a cleaned leaf while Intent Review answers ``candidate_not_live``, because the history
route required ``contract.code_worktree.exists()``. These cases measure the produced behavior through
the real production composition -- a real enclosure, a real linked worktree, the real capture and
resolution owners, the real comparison, R11's real freeze and the real HTTP route -- and never through
a preconstructed payload.

The load-bearing properties, one case each:

* a leaf frozen while it was live, its worktree group removed exactly as cleanup removes it, serves
  **byte for byte the same** recorded comparison -- and the serving bytes are compared, not a summary
  of them;
* a **fresh interpreter** reconstructs the same comparison from the coordination root and the record
  alone, so "reopen after a process restart" is measured rather than simulated;
* a **later task** landing on the repository and on the leaf's protected branch changes neither the
  bytes nor the provenance of the recorded comparison, and its content never enters the inventory;
* a **pre-feature leaf** -- source history, no intent generation ever recorded -- exposes its recorded
  source range and states that absence explicitly, as a typed absence and not as missing content;
* **expected content that no longer resolves** is reported unavailable on the channel it affects,
  with the record's own deletion statement, and nothing falls back to the current tip or to today's
  knowledge;
* a closed leaf that recorded **neither** a comparison nor a landed commit is still refused by name,
  which is the one state in which ``candidate_not_live`` is the honest answer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    list_knowledge_review_entries,
    read_knowledge_review,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_committed_leaf import (
    HISTORY_COMPARISON_PREFIX,
    HISTORY_INTENT_PREFIX,
    HISTORY_RECORDED_COMPARISON,
    HISTORY_RECORDED_SOURCE_RANGE,
    HISTORY_SOURCE_PREFIX,
    ClosedLeafReview,
)
from agents_remember.application.review_comparison_freeze import freeze_review_comparison
from agents_remember.application.review_comparison_reclamation import (
    discard_comparison_snapshots,
    release_comparison_code_object,
)
from agents_remember.application.review_evidence_records import review_records_for
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.models.knowledge.review import (
    KnowledgeReviewResult,
)
from agents_remember.serving.review import register_review_routes
from diff_scope_test_support import _git
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_knowledge_review_source_endpoints import (
    LEAF_ID,
    EndpointFixture,
    _commit,
    build_endpoint_fixture,
)

pytestmark = pytest.mark.evidence_unit

REVIEW_ROUTE = "/api/review/intent"

# The one path a later task's line writes. It is deliberately a path the recorded comparison cannot
# contain, so "the later task did not contaminate the record" is a measurement about a named file
# rather than a claim about a digest.
LATER_TASK_PATH = "src/a_later_task_landed.py"
LATER_TASK_TEXT = "# a later task's line, landed after this comparison was frozen\n"

# The child that reopens the same comparison in a process that shares no state with this one: it
# builds the app from the coordination root alone, calls the production port, and writes the served
# bytes out for the parent to compare.
_CHILD_SERVE = """
import json
import sys
from pathlib import Path

from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.review import register_review_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

descriptor = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
config = McpRuntimeConfig(
    workspace_root=Path(descriptor["workspace_root"]),
    coordination_root=Path(descriptor["coordination_root"]),
    config_path=Path(descriptor["config_path"]),
    transcript_root=Path(descriptor["transcript_root"]),
)
collaborators = serving_collaborators(config)
app = FastAPI()
register_review_routes(
    app,
    config,
    collaborators.knowledge_review,
    collaborators.knowledge_review_entries,
    collaborators.review_source_content,
)
with TestClient(app) as client:
    response = client.get(
        descriptor["route"], params=descriptor["params"], follow_redirects=True
    )
Path(sys.argv[2]).write_bytes(response.status_code.to_bytes(2, "big") + response.content)
print(f"child status {response.status_code}")
"""


@pytest.fixture
def closed_fixture(tmp_path: Path) -> EndpointFixture:
    """One fresh live enclosure per case; no case observes another's worktree or record."""

    return build_endpoint_fixture(tmp_path / "closed")


def _serve(fixture: EndpointFixture, params: dict[str, str]) -> tuple[int, bytes]:
    """One review route response through the production collaborators, as served bytes."""

    collaborators = serving_collaborators(fixture.config)
    app = FastAPI()
    register_review_routes(
        app,
        fixture.config,
        collaborators.knowledge_review,
        collaborators.knowledge_review_entries,
        collaborators.review_source_content,
    )
    with TestClient(app) as client:
        response = client.get(REVIEW_ROUTE, params=params)
    return response.status_code, response.status_code.to_bytes(2, "big") + response.content


def _subject_params(fixture: EndpointFixture, history: str | None = None) -> dict[str, str]:
    """The one reviewed subject, with or without the record selector the packet adds."""

    params = {
        "repo": fixture.repository_id,
        "master": fixture.master,
        "leaf": LEAF_ID,
        "selectorKind": "invariant",
        "selectorId": fixture.diff.retry_invariant_id,
    }
    if history is not None:
        params["history"] = history
    return params


def _entry_params(fixture: EndpointFixture) -> dict[str, str]:
    return {"repo": fixture.repository_id, "master": fixture.master, "leaf": LEAF_ID}


def _body(payload: bytes) -> dict:
    """The served body, with its two status bytes stripped, as the client decodes it."""

    return json.loads(payload[2:].decode("utf-8"))


def _freeze(fixture: EndpointFixture):
    outcome = freeze_review_comparison(fixture.config, fixture.request())
    assert outcome.state == "published", outcome.refusal
    assert outcome.manifest is not None
    return outcome


def _composition(fixture: EndpointFixture, **updates) -> KnowledgeReviewResult:
    """The owner's own payload for one request, through the record loader the route uses."""

    request = fixture.request().model_copy(update=updates)
    return read_knowledge_review(
        fixture.config, request, review_records_for(fixture.config, request)
    )


def _served_in_a_new_process(
    fixture: EndpointFixture, directory: Path, params: dict[str, str]
) -> bytes:
    """Serve one request from a child interpreter that shares no state with this process."""

    descriptor = directory / "restart.json"
    descriptor.write_text(
        json.dumps(
            {
                "workspace_root": str(fixture.config.workspace_root),
                "coordination_root": str(fixture.config.coordination_root),
                "config_path": str(fixture.config.config_path),
                "transcript_root": str(fixture.config.transcript_root),
                "route": REVIEW_ROUTE,
                "params": params,
            }
        ),
        encoding="utf-8",
    )
    target = directory / "restarted.bin"
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SERVE, str(descriptor), str(target)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0 and target.is_file(), (
        f"the child serve failed: exit {completed.returncode}, "
        f"stdout {completed.stdout!r}, stderr {completed.stderr!r}"
    )
    return target.read_bytes()


def _land_a_later_task(fixture: EndpointFixture) -> str:
    """Land one later task's line on the repository and on the leaf's protected branch."""

    repository = fixture.contract.code_repo_path
    (repository / LATER_TASK_PATH).write_text(LATER_TASK_TEXT, encoding="utf-8")
    _git(repository, ["add", "-A"])
    _git(
        repository,
        [
            "-c",
            "user.email=later@example.invalid",
            "-c",
            "user.name=a later task",
            "commit",
            "-q",
            "-m",
            "a later task lands",
        ],
    )
    head = _git(repository, ["rev-parse", "HEAD"])
    _git(repository, ["update-ref", f"refs/heads/{fixture.contract.code_source_branch}", head])
    return head


# -- the packet's journey ------------------------------------------------------------------------


def test_a_closed_leaf_serves_its_recorded_comparison_byte_for_byte(
    closed_fixture: EndpointFixture,
) -> None:
    """The live frozen view and the cleaned view are the same served bytes, measured as bytes.

    Three measurements make this the packet's demonstration rather than a re-read of what the
    implementation just wrote. The two served bodies are compared as **bytes**, so a field that moved
    anywhere in either is a failure and not a summary. The inventory digest the composition measures
    is compared against the digest R11's manifest froze while the leaf was live, which is the record's
    own statement about the inventory it bound. And the worktree group is removed before the second
    read, so nothing the closed read reports can come from the live enclosure.
    """

    fixture = closed_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None

    live_status, live = _serve(fixture, _subject_params(fixture, "recorded"))
    assert live_status == 200, live
    live_default_status, live_default = _serve(fixture, _subject_params(fixture))
    assert live_default_status == 200, live_default

    # The entry half of the same question answers from the same pair: while the leaf is live, the
    # catalogue of the *recorded* comparison is the catalogue the live resolution offers, because the
    # generation's retained halves are the datasets that were frozen.
    recorded_catalogue = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID, recorded=True
    )
    live_catalogue = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert recorded_catalogue.entries == live_catalogue.entries
    assert recorded_catalogue.total_subjects == live_catalogue.total_subjects > 0

    # The record's own frozen digests reproduce from the live composition: the comparison binding it
    # stored and the inventory digest it stored are the values this composition measures.
    composed = _composition(fixture)
    assert composed.payload is not None and composed.payload.comparison is not None
    assert (
        composed.payload.comparison.binding_digest == manifest.lineage.comparison_binding_digest
    ), "the live comparison is not the one the record froze"
    assert (
        sha256_digest(composed.payload.source.inventory.model_dump(mode="json"))
        == manifest.scope.inventory_digest
    ), "the live inventory is not the one the record froze"

    shutil.rmtree(fixture.contract.worktree_group)
    assert not fixture.contract.worktree_group.exists()

    closed_status, closed = _serve(fixture, _subject_params(fixture, "recorded"))
    default_status, default = _serve(fixture, _subject_params(fixture))
    assert closed_status == 200, closed
    assert default_status == 200, default

    assert closed == live, "the cleaned leaf served different bytes from the live frozen one"
    assert default == live, "the ordinary route after cleanup served different bytes"
    limitations = _body(closed)["payload"]["limitations"]
    assert HISTORY_RECORDED_COMPARISON in limitations
    assert f"{HISTORY_COMPARISON_PREFIX}{manifest.generation_id}" in limitations
    assert f"{HISTORY_INTENT_PREFIX}before:retained" in limitations
    assert f"{HISTORY_INTENT_PREFIX}after:retained" in limitations
    # The live read is unchanged: it declares the comparison's own limits and no record provenance.
    assert not [
        token
        for token in _body(live_default)["payload"]["limitations"]
        if token.startswith("history:")
    ]


def test_a_fresh_process_reconstructs_the_same_recorded_comparison(
    closed_fixture: EndpointFixture, tmp_path: Path
) -> None:
    """The reopened comparison comes from durable records: a new interpreter serves the same bytes.

    Nothing is passed to the child but the coordination root and the request: it locates the leaf's
    enclosure contract, reopens the generation the record names and serves the same answer, so
    "reconstruct the same comparison from durable records rather than memory" is measured rather than
    asserted. The generation's identity is read back in the child from the served provenance, so the
    child cannot be answering from a record it invented.
    """

    fixture = closed_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None
    live = _serve(fixture, _subject_params(fixture, "recorded"))[1]

    shutil.rmtree(fixture.contract.worktree_group)

    restarted = _served_in_a_new_process(fixture, tmp_path, _subject_params(fixture, "recorded"))
    restarted_default = _served_in_a_new_process(fixture, tmp_path, _subject_params(fixture))
    assert restarted == live, "a fresh process served different bytes"
    assert restarted_default == live, "the ordinary route in a fresh process served different bytes"
    provenance = _body(restarted)["payload"]["limitations"]
    assert f"{HISTORY_COMPARISON_PREFIX}{manifest.generation_id}" in provenance


def test_a_later_task_does_not_contaminate_the_recorded_comparison(
    closed_fixture: EndpointFixture,
) -> None:
    """A later task's landing changes neither the bytes nor the provenance of the record.

    The recorded comparison is read from the objects and the retained snapshots its own manifest
    bound, so neither a new commit on the leaf's protected branch nor new content in the repository it
    was captured from can enter it. The later task's path is looked for in the served inventory by
    name, which is the measurement; the equal bytes are the claim it supports.
    """

    fixture = closed_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None
    live = _serve(fixture, _subject_params(fixture, "recorded"))[1]
    shutil.rmtree(fixture.contract.worktree_group)
    closed = _serve(fixture, _subject_params(fixture, "recorded"))[1]
    assert closed == live

    landed = _land_a_later_task(fixture)

    after_status, after = _serve(fixture, _subject_params(fixture, "recorded"))
    assert after_status == 200, after
    assert after == live, "a later task's landing changed the recorded comparison's bytes"
    body = _body(after)["payload"]
    assert landed not in json.dumps(body)
    assert LATER_TASK_PATH not in json.dumps(body["source"]["inventory"])
    assert body["comparison"]["binding_digest"] == manifest.lineage.comparison_binding_digest
    assert body["limitations"] == _body(closed)["payload"]["limitations"]


def test_a_pre_feature_leaf_exposes_its_recorded_source_range_and_its_absence(
    closed_fixture: EndpointFixture,
) -> None:
    """A leaf with no intent generation opens its recorded code and states the absence explicitly.

    The range is the contract's own recorded pair -- the base it forked from and the commit it
    landed -- so the candidate endpoint is measured against that commit's tree rather than trusted.
    The absence is a *typed* one: the pane, the declared limitation and the entry route all state that
    no generation was ever recorded, which a reader can tell apart from content that was expected and
    could not be read. Nothing is fabricated: no comparison, no statement text and no assessment.
    """

    fixture = closed_fixture
    landed = _commit(fixture.worktree, "the leaf's own landing")
    recorded = fixture.recorded_range(landed)
    shutil.rmtree(fixture.contract.worktree_group)

    status, served = _serve(fixture, _entry_params(fixture))
    assert status == 200, served
    payload = _body(served)["payload"]
    inventory = payload["source"]["inventory"]
    assert inventory["state"] == "measured"
    assert inventory["listed_total"] > 0
    # The route serializes with `exclude_none=True`, so an absent comparison omits the key.
    assert payload.get("comparison") is None
    assert payload["staleness"]["state"] == "not_compared"
    assert payload["knowledge"]["selection_state"] == "task_context"
    assert (
        "no intent generation was ever recorded for this leaf"
        in payload["knowledge"]["selection_detail"]
    )
    assert payload["knowledge"]["before_statement"]["state"] == "unresolved"
    assert payload["knowledge"]["after_statement"]["state"] == "unresolved"
    assert payload["knowledge"]["before_statement"].get("text") is None
    for side in ("before", "after"):
        assert f"{HISTORY_INTENT_PREFIX}{side}:not-recorded" in payload["limitations"]
    assert HISTORY_RECORDED_SOURCE_RANGE in payload["limitations"]
    assert not [
        token for token in payload["limitations"] if token.startswith(HISTORY_SOURCE_PREFIX)
    ]

    resolved = resolve_review_candidate(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    review = resolved.closed_leaf
    assert isinstance(review, ClosedLeafReview), resolved
    assert review.state == "recorded-source-range"
    assert resolved.baseline_code_tree_id == recorded.code_base_commit
    assert resolved.candidate_code_tree_id == _git(
        fixture.contract.code_repo_path, ["rev-parse", f"{landed}^{{tree}}"]
    ), "the candidate endpoint is not the tree of the commit the contract recorded"

    # The entry route states the same absence in its own words rather than reporting a lost dataset.
    refused = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert refused.state == "refused" and refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"
    assert "records no comparison generation" in refused.refusal.detail


def test_a_generation_that_recorded_no_intent_half_states_that_absence_as_its_own_fact(
    tmp_path: Path,
) -> None:
    """A published generation with a typed absence says so, and never calls it unavailable.

    The two facts the packet keeps apart are an absence the *owner stated* -- ``not-recorded`` or
    ``not-selected``, which is history rather than a failure -- and expected content that no longer
    resolves. A generation frozen over a task with no knowledge half at all records the first, and
    both the entry route and the subject route must describe it as the record's own statement. The
    sentence is asserted, not only the token: the token was already right while the sentence
    collapsed the distinction into "does not resolve now".
    """

    fixture = build_endpoint_fixture(tmp_path / "no-intent", datasets=False)
    outcome = freeze_review_comparison(fixture.config, fixture.task_request())
    assert outcome.state == "published", outcome.refusal
    manifest = outcome.manifest
    assert manifest is not None
    assert {binding.side: binding.state for binding in manifest.knowledge} == {
        "before": "not-selected",
        "after": "not-selected",
    }
    shutil.rmtree(fixture.contract.worktree_group)

    status, served = _serve(fixture, _entry_params(fixture))
    assert status == 200, served
    payload = _body(served)["payload"]
    assert f"{HISTORY_INTENT_PREFIX}before:not-selected" in payload["limitations"]
    assert f"{HISTORY_INTENT_PREFIX}after:not-selected" in payload["limitations"]
    detail = payload["knowledge"]["selection_detail"]
    assert "states that no such intent content was ever recorded" in detail
    assert "before:not-selected, after:not-selected" in detail
    assert "does not resolve now" not in detail

    entries = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert entries.state == "refused" and entries.refusal is not None
    assert entries.refusal.code == "candidate_dataset_absent"
    assert "the record states that no such knowledge content was ever recorded" in entries.refusal.detail
    assert "before:not-selected, after:not-selected" in entries.refusal.detail
    assert "does not resolve now" not in entries.refusal.detail

    subject_status, subject = _serve(fixture, _subject_params(fixture))
    assert subject_status == 404, subject
    subject_refusal = _body(subject)["refusal"]
    assert subject_refusal["code"] == "candidate_dataset_absent"
    assert "the record states that no such knowledge content was ever recorded" in subject_refusal[
        "detail"
    ]
    assert "does not resolve now" not in subject_refusal["detail"]


def test_a_recorded_range_the_repository_cannot_resolve_is_unresolved_not_not_live(
    tmp_path: Path,
) -> None:
    """A leaf that recorded a landed commit keeps that answer when the commit is gone.

    ``candidate_not_live`` is the state "there is no candidate", and it is not true of a leaf whose
    contract records a landed commit this repository no longer holds: it recorded a candidate and the
    surface failed to read it. The sibling failure -- a commit that will not peel to a tree -- already
    answers ``candidate_unresolved``, and the same failure class is measured here at the range.
    """

    fixture = build_endpoint_fixture(tmp_path / "unresolvable")
    landed = _commit(fixture.worktree, "the leaf's own landing")
    fixture.recorded_range(landed)
    repository = fixture.contract.code_repo_path
    _git(repository, ["update-ref", "-d", f"refs/heads/{fixture.contract.code_work_branch}"])
    _git(repository, ["reflog", "expire", "--expire=now", "--all"])
    shutil.rmtree(fixture.contract.worktree_group)
    _git(repository, ["gc", "--prune=now", "--quiet"])
    assert not _object_present(repository, landed)

    status, served = _serve(fixture, _entry_params(fixture))
    assert status == 404, served
    refusal = _body(served)["refusal"]
    assert refusal["code"] == "candidate_unresolved"
    assert "records a source range that cannot be read (unresolvable)" in refusal["detail"]
    assert landed in refusal["detail"]
    assert "restore the recorded commit" in refusal["next_action"]


def _object_present(repository: Path, object_id: str) -> bool:
    """Whether one object resolves in a repository, asked without raising on a missing one."""

    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{object_id}^{{commit}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": str(repository),
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        ).returncode
        == 0
    )


def test_expected_content_that_no_longer_resolves_is_unavailable_not_substituted(
    closed_fixture: EndpointFixture,
) -> None:
    """A retained input that is gone reports unavailable on its own channel, with no substitution.

    Two channels are measured separately. The knowledge half is discarded through R11's own deletion
    owner, which writes the unavailable-history record first, and the reopened review states that
    recorded deletion rather than an unexplained absence. The code half is released and reclaimed, and
    the inventory reports the measurement it could not make instead of reading the branch tip that
    now holds the same content. Neither case falls back to what the repository holds today.
    """

    fixture = closed_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None and manifest.source.retained is not None
    shutil.rmtree(fixture.contract.worktree_group)

    discard_comparison_snapshots(
        fixture.contract.task_root,
        LEAF_ID,
        manifest.generation_id,
        reason="ICR-L12 case: discard the retained halves",
    )
    status, served = _serve(fixture, _entry_params(fixture))
    assert status == 200, served
    payload = _body(served)["payload"]
    assert f"{HISTORY_INTENT_PREFIX}after:unavailable-history" in payload["limitations"]
    assert f"{HISTORY_INTENT_PREFIX}before:unavailable-history" in payload["limitations"]
    assert "does not resolve now" in payload["knowledge"]["selection_detail"]
    assert "unavailable and never as empty" in payload["knowledge"]["selection_detail"]
    refused_status, refused = _serve(fixture, _subject_params(fixture))
    assert refused_status == 404, refused
    assert _body(refused)["refusal"]["code"] == "candidate_dataset_absent"
    assert "unavailable-history" in _body(refused)["refusal"]["detail"]

    release_comparison_code_object(
        fixture.contract.task_root,
        LEAF_ID,
        manifest.generation_id,
        reason="ICR-L12 case: release the code pin",
    )
    repository = fixture.contract.code_repo_path
    _git(repository, ["reflog", "expire", "--expire=now", "--all"])
    _git(repository, ["gc", "--prune=now", "--quiet"])
    pruned = subprocess.run(
        ["git", "cat-file", "-e", manifest.source.candidate_code_tree_id],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repository),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    assert pruned.returncode != 0, "the released pin's tree survived reclamation"

    source_status, source_body = _serve(fixture, _entry_params(fixture))
    assert source_status == 200, source_body
    source_payload = _body(source_body)["payload"]
    assert source_payload["source"]["inventory"]["state"] == "unavailable"
    assert f"{HISTORY_SOURCE_PREFIX}unavailable-history" in source_payload["limitations"]
    assert LATER_TASK_PATH not in json.dumps(source_payload)


def test_a_closed_leaf_with_nothing_recorded_is_refused_by_name(
    closed_fixture: EndpointFixture,
) -> None:
    """The one state ``candidate_not_live`` still answers: nothing is live and nothing was recorded.

    A leaf whose worktree is gone, which published no comparison generation and whose contract
    records no landed commit has no candidate this surface may resolve -- and the action it earns
    names both ways to produce one instead of pointing at a view that is not this surface.
    """

    fixture = closed_fixture
    shutil.rmtree(fixture.contract.worktree_group)

    status, served = _serve(fixture, _entry_params(fixture))
    assert status == 404, served
    refusal = _body(served)["refusal"]
    assert refusal["code"] == "candidate_not_live"
    assert "records no comparison generation" in refusal["detail"]
    assert "record the leaf's landed commit" in refusal["next_action"]
