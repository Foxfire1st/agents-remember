"""MIK-R74 rule 6: a read changes no branch, tag or HEAD, no worktree file, no Git index,
no task document and no requirement packet.

A test runs every read against scratch clones and compares, before and after: refs and HEAD, the
index file, the working trees, the task folder. It fails on any difference outside (a) cache files
under ``<coordination root>/runtime/``, (b) a recorded comparison (its record, captured objects and
one pin ref) and (c) a preview's worklist, quality reports and judged-tree objects. A second case
mutates one read to create a directory and shows the harness catching it, so the first case cannot
pass vacuously.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import agents_remember.kernel.git_freshness as freshness
import knowledge_writer_test_support as writer_fixture
import pytest
from agents_remember.application.context_packet import ContextPacketRequest
from agents_remember.application.memory_tools import CarryoverSelection, drift_check_tool
from agents_remember.application.published_intent import published_intent_block
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_intent_summary import intent_summary_of
from agents_remember.application.review_tree_comparison import ReviewTrees, live_review_trees
from agents_remember.application.skill_resources.catalog import SkillCatalogError, read_served_file
from agents_remember.application.skill_resources.operation import skill_catalog_registry
from agents_remember.application.skill_resources.responses import (
    skill_catalog_list_response,
    skill_catalog_read_response,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tool_requests import CloseoutCommitMessages, LandedCommits
from agents_remember.errors import AuthorityError
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.mcp.tools import knowledge as tools
from agents_remember.mcp.tools.closeout_queue import closeout_queue_payload
from agents_remember.mcp.tools.core import (
    context_packet_payload,
    server_info_payload,
)
from agents_remember.mcp.tools.memory import (
    memory_baseline_adopt_payload,
    memory_baseline_status_payload,
    memory_carryover_plan_payload,
)
from agents_remember.mcp.tools.read_files import read_ar_files_payload
from agents_remember.mcp.tools.task_doc import task_reopen_payload
from agents_remember.mcp.tools.worktree import (
    worktree_checkpoint_landing_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
    worktree_record_landing_payload,
    worktree_status_payload,
    worktree_sync_payload,
)
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.memory_quality.reference_state import git_blob_id
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.queue.closeout_queue import CloseoutQueueRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.changeset_cache import ChangesetCache
from agents_remember.serving.files import register_files_routes
from agents_remember.serving.notes import register_notes_routes
from agents_remember.serving.review import register_review_routes
from agents_remember.serving.review_summary import (
    KNOWLEDGE_REVIEW_SUMMARY_ROUTE,
    ReviewIntentSummaryPort,
    register_review_summary_route,
)
from agents_remember.worktrees.modules import closeout_external, integration_gate
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import write_contract
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_knowledge_review_source_endpoints import build_endpoint_fixture, compose_endpoint_review
from test_source_lineage import _git as git
from test_transaction_only_worktree_delivery import _apply
from test_worktree_support import init_repo
from transaction_only_worktree_delivery_test_support import _closeout_fixture

pytestmark = pytest.mark.evidence_unit


from read_boundary_oracle_test_support import _classify, _snapshot


@pytest.fixture
def tool_world(tmp_path: Path) -> writer_fixture.World:
    return writer_fixture.build_world(tmp_path / "tools")


def _read_tool(world: writer_fixture.World, coordination: Path, **fields: Any) -> dict[str, Any]:
    return tools.knowledge_read_payload(
        tools.ReadToolRequest(memory_root=str(world.memory), **fields),
        workspace_root=str(world.code),
        coordination_root=str(coordination),
    )


def test_every_registered_read_leaves_nothing_but_cache(
    tmp_path: Path, tool_world: writer_fixture.World
) -> None:
    """Rule 6 over the tool reads: knowledge read, diff, integrity and the intent block."""

    world = tool_world
    coordination = tmp_path / "tools" / "coordination"
    coordination.mkdir(parents=True, exist_ok=True)
    roots = {"code": world.code, "memory": world.memory, "coord": coordination}
    before = _snapshot(roots)

    family = _read_tool(
        world,
        coordination,
        view="family",
        family_revision_id=text_uuid("revision", f"{writer_fixture.BASE_FAMILY}@1"),
    )
    assert family["state"] in {"view", "page"}, family
    invariant = _read_tool(
        world,
        coordination,
        view="invariant",
        invariant_revision_id=text_uuid("revision", f"{writer_fixture.BASE_INVARIANT}@1"),
    )
    assert invariant["state"] in {"view", "page"}, invariant
    leaf = _read_tool(
        world, coordination, view="source_context", source_path=writer_fixture.CODE_FILE
    )
    assert leaf["state"] == "page", leaf
    for view in ("review_matrix", "curation_queue"):
        refused = _read_tool(world, coordination, view=view)
        assert refused["state"] == "refused", refused
    compared = tools.knowledge_diff_payload(
        tools.DiffToolRequest(memory_root=str(world.memory), before="HEAD", after="HEAD")
    )
    # MIK-R109 rule 3: the comparison answers only beside its caller-readable
    # report, and this rootless direct call names nowhere to write it, so the
    # tool refuses and writes nothing -- the registered tool always passes
    # the coordination root (the compared path rides the report suites).
    assert compared["state"] == "refused", compared
    assert compared["refusalCode"] == "report-unwritable", compared
    checked = tools.knowledge_integrity_check_payload(
        tools.IntegrityCheckRequest(contractPath=str(world.contract))
    )
    assert checked["state"] == "reported", checked
    block = published_intent_block(_context(world, coordination), [writer_fixture.CODE_FILE])
    assert block["state"] == "recorded", block

    after = _snapshot(roots)
    findings = _classify(before, after, coordination_roots=("coord",))
    assert findings == [], "\n".join(findings)


def _context(world: writer_fixture.World, coordination: Path) -> Any:
    return SimpleNamespace(
        memory_root=world.memory,
        coordination_root=coordination,
        code_repository_root=world.code,
        code_repository_name="agents-remember",
    )


def test_a_mutated_read_is_caught(
    tmp_path: Path, tool_world: writer_fixture.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The harness is not vacuous: a read that creates a directory fails it."""

    world = tool_world
    coordination = tmp_path / "tools" / "coordination"
    coordination.mkdir(parents=True, exist_ok=True)
    roots = {"code": world.code, "memory": world.memory, "coord": coordination}
    before = _snapshot(roots)

    real = tools.knowledge_read_payload

    def _writing(*args: Any, **kwargs: Any) -> Any:
        (world.code / "reports").mkdir(exist_ok=True)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, "knowledge_read_payload", _writing)
    _read_tool(
        world,
        coordination,
        view="family",
        family_revision_id=text_uuid("revision", f"{writer_fixture.BASE_FAMILY}@1"),
    )
    after = _snapshot(roots)
    findings = _classify(before, after, coordination_roots=("coord",))
    assert any("reports" in finding for finding in findings), findings


def test_enclosure_reads_record_only_comparisons_and_previews(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the enclosure reads: review, status, preview and dry runs.

    The review captures its compared trees (b); on this synthetic seat no task document
    exists, so no comparison record or pin is written beside them -- the captured objects
    are attributed instead through the compared trees the review answer itself carries.
    The preview writes the leaf's worklist (c); status and dry runs change nothing.
    Anything else fails.
    """

    endpoints = build_endpoint_fixture(tmp_path / "enclosure")
    config = dataclasses.replace(
        endpoints.config,
        repositories={
            endpoints.repository_id: RepositoryScope(
                repo_id=endpoints.repository_id,
                path=endpoints.contract.code_repo_path,
            )
        },
    )
    roots = {"enclosure": tmp_path / "enclosure"}
    coordination_roots = ("enclosure/ar-coordination",)
    before = _snapshot(roots)

    result = compose_endpoint_review(endpoints)
    assert result.state == "review", result.refusal
    assert result.payload is not None and result.payload.comparison is not None
    compared = result.payload.comparison
    assert compared.before_code_tree_id is not None
    assert compared.after_code_tree_id is not None
    reviewed_trees = {"enclosure": (compared.before_code_tree_id, compared.after_code_tree_id)}

    # The synthetic enclosure is not an adopted seat (no task document), so the seat-bound
    # operations answer their named refusals here rather than reading. A refusal is still a
    # read for rule 6: it must change nothing either.
    status = worktree_status_payload(
        config,
        TaskRef(
            repo_id=endpoints.repository_id,
            contract_path=endpoints.contract.contract_path.as_posix(),
        ),
    )
    assert status["ok"] is False, status
    assert status["state"] == "operation-location-adoption-required", status

    preview = worktree_closeout_preview_payload(
        config,
        endpoints.contract.contract_path.as_posix(),
        CloseoutCommitMessages(code="preview", memory="preview"),
    )
    assert preview["ok"] is False, preview
    assert preview["state"] == "operation-location-adoption-required", preview

    reopened = task_reopen_payload(
        config, endpoints.contract.contract_path.as_posix(), dry_run=True
    )
    assert reopened["ok"] is False, reopened

    # Dry runs are reads for rule 6 even when the seat would refuse the write: they must
    # change nothing either.
    integrated = worktree_integrate_payload(
        config, endpoints.contract.contract_path.as_posix(), dry_run=True
    )
    assert integrated["ok"] is False, integrated

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=coordination_roots,
        reviewed_prefixes=("enclosure/diff",),
        reviewed_trees=reviewed_trees,
    )
    assert findings == [], findings


def test_review_get_routes_read_without_writing(tmp_path: Path, worktree_services: Any) -> None:
    """Rule 6 over the dashboard reviewer GETs: the review and its summary, served for real.

    Both routes run against the scratch enclosure with ports that read the fixture's real
    datasets -- the review through the composition, the summary through the placed snapshots.
    The knowledge-reader family's own writelessness case lives beside its route
    (``test_the_route_serves_every_view_and_the_reader_writes_nothing``); this case does not
    repeat it.
    """

    endpoints = build_endpoint_fixture(tmp_path / "enclosure")
    knowledge = endpoints.contract.worktree_group / "provider-runtime/dev-ar-coordination/knowledge"
    summary_resolution = ReviewCandidateResolution(
        repository_id=endpoints.repository_id,
        leaf_id=endpoints.diff.retry_invariant_id,
        baseline_database=knowledge / "baseline/knowledge-candidate.db",
        candidate_database=knowledge / "candidate/knowledge-candidate.db",
        baseline_code_root=None,
        candidate_code_root=None,
        baseline_code_tree_id=None,
        candidate_code_tree_id=None,
    )
    app = FastAPI()
    compared_trees: list[str] = []

    def port(request):  # type: ignore[no-untyped-def]
        result = compose_endpoint_review(endpoints, request)
        if result.payload is not None and result.payload.comparison is not None:
            compared = result.payload.comparison
            if compared.before_code_tree_id is not None:
                compared_trees.append(compared.before_code_tree_id)
            if compared.after_code_tree_id is not None:
                compared_trees.append(compared.after_code_tree_id)
        return result

    register_review_routes(
        app,
        endpoints.config,
        port=port,
    )
    register_review_summary_route(
        app,
        ReviewIntentSummaryPort(
            inputs=lambda *_: (),
            read=lambda _repo, master, _leaf, _number, _observe: intent_summary_of(
                summary_resolution, master
            ),
        ),
        ChangesetCache(),
    )
    client = TestClient(app)

    roots = {"enclosure": tmp_path / "enclosure"}
    coordination_roots = ("enclosure/ar-coordination",)
    before = _snapshot(roots)

    task_context = client.get(
        "/api/review/intent",
        params={
            "repo": endpoints.repository_id,
            "master": endpoints.master,
            "leaf": "260921-icr-l1",
        },
    )
    assert task_context.status_code == 200, task_context.text[:400]
    subject = client.get(
        "/api/review/intent",
        params={
            "repo": endpoints.repository_id,
            "master": endpoints.master,
            "leaf": "260921-icr-l1",
            "selectorKind": "invariant",
            "selectorId": endpoints.diff.retry_invariant_id,
        },
    )
    assert subject.status_code == 200, subject.text[:400]
    summary = client.get(
        KNOWLEDGE_REVIEW_SUMMARY_ROUTE,
        params={
            "repo": endpoints.repository_id,
            "master": endpoints.master,
            "leaf": "260921-icr-l1",
        },
    )
    assert summary.status_code == 200, summary.text[:400]

    # Every GET above composed a review through the port: the compared trees it
    # published attribute the objects those reviews captured, exactly as in the
    # enclosure case. No shipped UI path selects anything else here.
    assert compared_trees, "the three GETs composed no review to attribute objects through"
    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=coordination_roots,
        reviewed_prefixes=("enclosure/diff",),
        reviewed_trees={"enclosure": tuple(compared_trees)},
    )
    assert findings == [], findings


def test_miscellaneous_description_called_reads_write_nothing(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the remaining description-called reads: server info, resolver,
    gate list and the read_ar_files block.

    Each answers (or names its refusal) on the synthetic seat; every one of them must leave
    refs/HEAD, the index file, the working trees, the task folder, the object stores and the
    directories exactly as they were.
    """

    endpoints = build_endpoint_fixture(tmp_path / "misc", memory_mode="external")
    config = dataclasses.replace(
        endpoints.config,
        repositories={
            endpoints.repository_id: RepositoryScope(
                repo_id=endpoints.repository_id,
                path=endpoints.contract.code_repo_path,
                memory_root=(
                    endpoints.config.coordination_root
                    / "memory-repos"
                    / f"ar-{endpoints.repository_id}"
                ),
            )
        },
    )
    roots = {"enclosure": tmp_path / "misc"}
    coordination_roots = ("enclosure/ar-coordination",)
    before = _snapshot(roots)

    info = server_info_payload(
        config, ServingBuildPayload(version="0", bootedAt="2026-10-02T00:00:00Z")
    )
    assert isinstance(info, dict) and info.get("ok") is True, info
    # `resolve_context` is removed (MIK-R109 rule 5): no removed tool rides
    # the miscellaneous-read battery; `context_packet` covers its readers.
    code_files = sorted(
        p for p in endpoints.contract.code_repo_path.rglob("*.py") if ".git" not in p.parts
    )
    assert code_files, "the endpoint fixture holds no code file to read"
    first = code_files[0].relative_to(endpoints.contract.code_repo_path).as_posix()
    read = read_ar_files_payload(
        config,
        endpoints.repository_id,
        [
            {"path": first, "source": "full"},
            {"path": "no/such/file.py", "source": "full"},
        ],
    )
    assert isinstance(read, dict) and read.get("ok") is True, read

    after = _snapshot(roots)
    findings = _classify(
        before, after, coordination_roots=coordination_roots, reviewed_prefixes=("enclosure/diff",)
    )
    assert findings == [], findings


def test_memory_backed_reads_write_nothing(tmp_path: Path, worktree_services: Any) -> None:
    """Rule 6 over the reads that need a memory root: the context packet, drift check and
    baseline status.

    They run on the external-memory endpoint seat, whose real memory repository sits at the
    conventional coordination path; each answers (or names its refusal) and must leave
    refs/HEAD, the index file, the working trees, the task folder, the object stores and
    the directories exactly as they were.
    """

    endpoints = build_endpoint_fixture(tmp_path / "membacked", memory_mode="external")
    config = dataclasses.replace(
        endpoints.config,
        repositories={
            endpoints.repository_id: RepositoryScope(
                repo_id=endpoints.repository_id,
                path=endpoints.contract.code_repo_path,
                memory_root=(
                    endpoints.config.coordination_root
                    / "memory-repos"
                    / f"ar-{endpoints.repository_id}"
                ),
            )
        },
    )
    roots = {"enclosure": tmp_path / "membacked"}
    before = _snapshot(roots)

    packet = context_packet_payload(config, ContextPacketRequest(repo_id=endpoints.repository_id))
    assert isinstance(packet, dict), packet
    drift = drift_check_tool(config, repo_id=endpoints.repository_id)
    assert isinstance(drift, dict), drift
    # Without onboarding content there is nothing to baseline: on unconverted memory the
    # status answers legacy-format (MIK-R46 rule 6) instead of running drift. A refusing
    # read is still a read for rule 6: it must write nothing.
    status = memory_baseline_status_payload(config, endpoints.repository_id)
    assert status["ok"] is False
    assert status["state"] == "legacy-format"

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], findings


def test_converted_drift_check_read_writes_nothing(tmp_path: Path, worktree_services: Any) -> None:
    """Rule 6 over drift_check on converted memory: the selected format files nothing.

    A drift read on a converted tree measures and answers; it must leave
    refs/HEAD, the index file, the working trees, the task folder, the object
    stores and the directories exactly as they were (allowances (a)-(c)).
    """

    root = tmp_path / "convdrift"
    code = root / "code"
    code.mkdir(parents=True)
    init_repo(code, "main")
    (code / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-qm", "code")

    endpoints = build_endpoint_fixture(root / "seat", memory_mode="external")
    # Convert the enclosure's own conventional memory repo in place: converted
    # layout marker plus one current card beside the existing ledger.
    memory = endpoints.config.coordination_root / "memory-repos" / f"ar-{endpoints.repository_id}"
    (memory / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text(
        '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n',
        encoding="utf-8",
    )
    data = (code / "alpha.py").read_bytes()
    document = (
        {
            "path": "alpha.py",
            "schema": "ar-onboarding-file/v1",
            "realizes": [],
            "references": {
                "1": {
                    "targets": [
                        {
                            "kind": "code",
                            "anchor": {
                                "blob": git_blob_id(data),
                                "locator": {"kind": "file"},
                                "content": content_identity(data),
                            },
                        }
                    ]
                }
            },
        },
    )
    sidecar = memory / "onboarding" / "alpha.py.json"
    sidecar.write_text(canonical_text(document), encoding="utf-8")
    (memory / "onboarding" / "alpha.py.md").write_text("# alpha.py\n\nSee [1].\n", encoding="utf-8")

    config = dataclasses.replace(
        endpoints.config,
        repositories={
            endpoints.repository_id: RepositoryScope(
                repo_id=endpoints.repository_id,
                path=code,
                memory_root=memory,
            )
        },
    )
    roots = {"enclosure": root}
    before = _snapshot(roots)

    drift = drift_check_tool(config, repo_id=endpoints.repository_id)
    assert isinstance(drift, dict), drift
    status = memory_baseline_status_payload(config, endpoints.repository_id)
    assert isinstance(status, dict), status

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], "\n".join(findings)


def test_converted_adopt_dry_run_writes_nothing(tmp_path: Path, worktree_services: Any) -> None:
    """Rule 6 over baseline adoption previews: a dry run files nothing.

    Adoption itself is a mutating operation, but its dry_run preview is a
    read: the drift measurement inside it must not file reports, snapshots
    or pointers, and the preview must leave the trees exactly as they were.
    """

    root = tmp_path / "convadopt"
    code = root / "code"
    code.mkdir(parents=True)
    init_repo(code, "main")
    (code / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-qm", "code")

    endpoints = build_endpoint_fixture(root / "seat", memory_mode="external")
    memory = endpoints.config.coordination_root / "memory-repos" / f"ar-{endpoints.repository_id}"
    (memory / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text(
        '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n',
        encoding="utf-8",
    )
    data = (code / "alpha.py").read_bytes()
    document = (
        {
            "path": "alpha.py",
            "schema": "ar-onboarding-file/v1",
            "realizes": [],
            "references": {
                "1": {
                    "targets": [
                        {
                            "kind": "code",
                            "anchor": {
                                "blob": git_blob_id(data),
                                "locator": {"kind": "file"},
                                "content": content_identity(data),
                            },
                        }
                    ]
                }
            },
        },
    )
    sidecar = memory / "onboarding" / "alpha.py.json"
    sidecar.write_text(canonical_text(document), encoding="utf-8")
    (memory / "onboarding" / "alpha.py.md").write_text("# alpha.py\n\nSee [1].\n", encoding="utf-8")

    config = dataclasses.replace(
        endpoints.config,
        repositories={
            endpoints.repository_id: RepositoryScope(
                repo_id=endpoints.repository_id,
                path=code,
                memory_root=memory,
            )
        },
    )
    roots = {"enclosure": root}
    before = _snapshot(roots)

    adopted = memory_baseline_adopt_payload(config, endpoints.repository_id, dry_run=True)
    assert isinstance(adopted, dict), adopted

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], "\n".join(findings)


def test_context_packet_freshness_fetch_is_bounded(
    tmp_path: Path, worktree_services: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 6(d): without freshness nothing changes; with it only the fetch.

    An asked-for freshness check runs one fetch from the configured upstream:
    remote-tracking refs and objects may move and nothing else — no branch,
    no tag, no HEAD, no working tree, no index. FETCH_HEAD is inherent to the
    one fetch action itself. Without the argument nothing may change at all.
    """

    root = tmp_path / "freshread"
    origin = root / "origin.git"
    origin.mkdir(parents=True)
    git(origin, "init", "-q", "--bare")
    code = root / "code"
    code.mkdir(parents=True)
    init_repo(code, "main")
    (code / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-qm", "code")
    git(code, "remote", "add", "origin", origin.as_posix())
    git(code, "push", "-q", "origin", "main")
    git(code, "branch", "--set-upstream-to=origin/main", "main")
    # Advance the upstream from a peer clone so the read's fetch has
    # something to bring down as remote-tracking refs and objects.
    peer = root / "peer"
    git(root, "clone", "-q", origin.as_posix(), peer.as_posix())
    git(peer, "checkout", "-q", "-B", "main", "origin/main")
    git(peer, "config", "user.email", "agents-remember-tests@example.invalid")
    git(peer, "config", "user.name", "Agents Remember Tests")
    (peer / "gamma.py").write_text("print('gamma')\n", encoding="utf-8")
    git(peer, "add", "-A")
    git(peer, "commit", "-qm", "peer")
    git(peer, "push", "-q", "origin", "main")

    endpoints = build_endpoint_fixture(root / "seat", memory_mode="external")
    config = dataclasses.replace(
        endpoints.config,
        repositories={
            endpoints.repository_id: RepositoryScope(
                repo_id=endpoints.repository_id,
                path=code,
                memory_root=(
                    endpoints.config.coordination_root
                    / "memory-repos"
                    / f"ar-{endpoints.repository_id}"
                ),
            )
        },
    )
    calls: list[str] = []
    real_fetch = freshness.fetch_remote

    def spy_fetch(*args: Any, **kwargs: Any) -> Any:
        calls.append("fetch")
        return real_fetch(*args, **kwargs)

    monkeypatch.setattr(freshness, "fetch_remote", spy_fetch)

    # Without the argument: nothing changes and nothing fetches.
    roots = {"enclosure": root}
    before = _snapshot(roots)
    packet = context_packet_payload(
        config,
        ContextPacketRequest(repo_id=endpoints.repository_id),
    )
    assert isinstance(packet, dict), packet
    assert calls == [], calls
    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], "\n".join(findings)

    # With the argument: one fetch happens; only (d) may change.
    before = _snapshot(roots)
    packet = context_packet_payload(
        config,
        ContextPacketRequest(repo_id=endpoints.repository_id, include_freshness=True),
    )
    assert isinstance(packet, dict), packet
    assert calls == ["fetch"], calls
    # MIK-R109 rule 4: the slim answer carries behind-ness as an action line,
    # not as a raw freshness block.
    assert "the code checkout is 1 behind its upstream" in packet.get("actions", []), packet
    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    # (d) footprint: remote-tracking ref moves (incl. their reflogs and the
    # origin/HEAD symref), fetched objects, and FETCH_HEAD inherent to the
    # one fetch action. Anything else fails.
    allowed = [
        finding
        for finding in findings
        if "refs/remotes/" in finding
        or ".git/objects/" in finding
        or finding.endswith(".git/FETCH_HEAD")
    ]
    assert len(allowed) == len(findings), "\n".join(findings)
    assert allowed != [], "expected the (d) fetch footprint"


def test_adopted_seat_status_preview_and_dry_runs(tmp_path: Path, worktree_services: Any) -> None:
    """Rule 6 over the ok-path reads: status, closeout preview, integrate and reopen dry runs.

    The seat is adopted and the candidate reviewed, so every call answers instead of
    refusing. The preview may write exactly rule 6 (c): the leaf's worklist, its quality
    reports and the judged-tree objects. Anything else fails.
    """

    contract, config = _closeout_fixture(tmp_path)
    assert contract.memory_worktree is not None and contract.memory_repo_path is not None
    layout = contract.memory_worktree / "knowledge" / "layout.json"
    layout.parent.mkdir(exist_ok=True)
    layout.write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )
    source = contract.memory_source_branch
    seed = git(
        contract.memory_repo_path,
        "commit-tree",
        f"{source}^{{tree}}",
        "-p",
        source,
        "-m",
        render_memory_content_message("Seed memory", str(contract.code_base_commit)),
    )
    git(contract.memory_repo_path, "update-ref", f"refs/heads/{source}", seed)
    git(contract.memory_worktree, "merge", "--ff-only", seed)
    contract = dataclasses.replace(contract, memory_base_commit=seed)
    write_contract(contract.contract_path, contract)
    capture = capture_future_code_candidate(contract)
    trees = live_review_trees(contract.coordination_root, contract, capture.codeCandidateTree)
    assert isinstance(trees, ReviewTrees), trees

    roots = {"adopted": tmp_path}
    before = _snapshot(roots)

    status = worktree_status_payload(
        config,
        TaskRef(repo_id=contract.repo_name, contract_path=contract.contract_path.as_posix()),
    )
    assert status["ok"] is True, status
    with mock.patch.object(closeout_external, "leaf_gate_refusal", return_value=None):
        preview = worktree_closeout_preview_payload(
            config,
            contract.contract_path.as_posix(),
            CloseoutCommitMessages(code="preview", memory="preview"),
        )
    assert preview["ok"] is True, preview

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("adopted/coordination",),
        reviewed_prefixes=("adopted/code", "adopted/memory"),
    )
    assert findings == [], findings

    # The integrate dry run needs a completed closeout. The closeout itself is a write
    # tool, so it runs between the two baselines: the second classification covers only
    # the dry runs.
    with mock.patch.object(closeout_external, "leaf_gate_refusal", return_value=None):
        applied = _apply(config, contract)
    assert applied["ok"] is True, applied

    before = _snapshot(roots)
    # MIK-R52 rule 3 judges the closeout pair once more at integration, with the parent line's
    # tip as base; this fixture pair is built for read-boundary classification, not for passing
    # that judgment, so the re-judgment is held passing here as the closeout gate is above.
    with mock.patch.object(integration_gate, "leaf_pair_refusal", return_value=None):
        integrated = worktree_integrate_payload(
            config, contract.contract_path.as_posix(), dry_run=True
        )
    assert integrated["ok"] is True, integrated
    reopened = task_reopen_payload(config, contract.contract_path.as_posix(), dry_run=True)
    assert isinstance(reopened, dict), reopened

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("adopted/coordination",),
        reviewed_prefixes=("adopted/code", "adopted/memory"),
    )
    assert findings == [], findings


# --- the rest of the roster: skills, previews, GETs ----------------
# Mappings R1-R9 above cover the knowledge reads, enclosure review/status/previews/dry runs,
# three dashboard GETs, readers, miscellaneous/memory reads and adopted-seat dry runs. The
# mappings below cover every remaining read-described route: the skill catalog,
# the remaining worktree preview/dry-run variants, the carryover plan, the
# closeout-queue inspection and the remaining dashboard GETs. Each names
# its actual constructor, call and outcome -- answers where the seat admits them, the exact
# refusal elsewhere -- and classifies the full before/after populations.
#
# UNCONSTRUCTIBLE boundaries (named, not run, never waived): live-session routes
# (conversation control, terminal/pty/tmux, harness control, the /api/state
# projection, unbounded /api/stream and /api/events rivers), and the held
# unauthorized case2/3 drivers, which the Reviewer forbids running. Write tools
# are not read-described and are outside this roster entirely.
# (The code-search tools, provider diagnostics and watcher actions were removed
# by MIK-R112; their roster entries went with them.)


def test_diagnostic_watcher_and_skill_reads_write_nothing(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the skill catalog reads.

    The catalog lists shipped skills and reads one back by the URI the list
    itself published. A bogus URI names its refusal. None of it writes.
    """

    build_endpoint_fixture(tmp_path / "diag", memory_mode="external")
    roots = {"enclosure": tmp_path / "diag"}
    before = _snapshot(roots)

    catalog = skill_catalog_registry()
    listed = skill_catalog_list_response(catalog).to_payload()
    assert listed.get("ok") is True, listed
    uris = [skill["uri"] for skill in listed.get("skills", [])]
    assert uris, "the shipped skill catalog lists no skill to read back"
    served = read_served_file(catalog, uris[0])
    root = served.entry.root
    read = skill_catalog_read_response(
        served, skill_revision="" if root is None else root.revision
    ).to_payload()
    assert read.get("ok") is True and isinstance(read.get("content"), str), read
    # A URI the catalog never published raises the catalog's own refusal.
    with pytest.raises(SkillCatalogError, match="not a skill resource"):
        read_served_file(catalog, "skill://no/such/skill/SKILL.md")

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], findings


def test_remaining_preview_plan_and_queue_reads_write_nothing(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the remaining preview/dry-run variants, the carryover plan and queue status.

    Sync, checkpoint-landing and record-landing dry runs plan without landing anything
    (or name why the seat cannot plan them); the carryover plan names the seat's authority
    refusal without carrying; queue status without a declared caller raises the
    caller-required refusal before it reads anything. Each must leave the adopted seat
    exactly as it was.
    """

    contract, config = _closeout_fixture(tmp_path)
    roots = {"adopted": tmp_path}
    before = _snapshot(roots)

    synced = worktree_sync_payload(config, contract.contract_path.as_posix(), dry_run=True)
    assert isinstance(synced, dict), synced
    # Checkpoint landing needs an atomic series contract: the closeout fixture's
    # contract is not one, so even the dry run names that refusal before touching
    # anything. An answered refusal is still a preview for rule 6: it must write nothing.
    checkpoint = worktree_checkpoint_landing_payload(
        config, contract.contract_path.as_posix(), dry_run=True
    )
    assert checkpoint["ok"] is False
    assert checkpoint["code"] == "contract-not-a-series"
    # D279: an answered refusal carries its reason in summary, not the raised channel.
    assert "error" not in checkpoint
    assert checkpoint["summary"] == (
        "worktree_checkpoint_landing requires an atomic series contract: "
        "a leaf lands through closeout and worktree_integrate instead."
    )
    # Recording a landing the protected branch never saw refuses before touching
    # anything, dry run or not: the all-zero tip is reachable from no landing target.
    recorded = worktree_record_landing_payload(
        config,
        contract.contract_path.as_posix(),
        landed=LandedCommits(code="0" * 40),
        dry_run=True,
    )
    assert recorded["ok"] is False
    assert recorded["code"] == "commit-not-on-landing-target"
    assert "error" not in recorded
    assert "not reachable from any landing target" in recorded["summary"]
    # The adopted seat keeps branch memory outside the coordination root, so the plan
    # names that authority refusal before reading anything. A raising plan is still a
    # plan for rule 6: it must write nothing.
    with pytest.raises(AuthorityError, match="must stay inside coordination_root"):
        memory_carryover_plan_payload(
            config,
            CarryoverSelection(
                repo_id=contract.repo_name,
                contract_path=contract.contract_path.as_posix(),
                source_memory=contract.memory_repo_path.as_posix()
                if contract.memory_repo_path is not None
                else "",
                official_code_ref=contract.code_base_commit,
                source_code_ref=contract.code_base_commit,
                old_base=contract.code_base_commit,
            ),
        )
    with pytest.raises(CloseoutQueueError, match="caller-required"):
        closeout_queue_payload(
            config,
            CloseoutQueueRequest(
                action="status",
                sprint_task_document_ref=TaskDocumentRef(
                    repository="agents-remember", path="sprint.json"
                ),
            ),
        )

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("adopted/coordination",),
        reviewed_prefixes=("adopted/code", "adopted/memory"),
    )
    assert findings == [], findings


def test_remaining_dashboard_gets_read_without_writing(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the remaining dashboard GETs: files, notes and the changeset file diff.

    The catalog answers; scoped reads answer or name their refusal for bad scopes, missing
    repos and absent paths; the changeset diff answers or refuses without a comparable
    range. None of them writes. The fuller protected-read roster -- requirements,
    onboarding, changeset task/master, unwired review ports and the cockpit boundary --
    lives in the next two tests.
    """

    endpoints = build_endpoint_fixture(tmp_path / "gets")
    app = FastAPI()
    register_review_routes(
        app,
        endpoints.config,
        port=lambda request: compose_endpoint_review(endpoints, request),
    )
    register_files_routes(app, endpoints.config)
    register_notes_routes(app, endpoints.config)
    register_changeset_routes(app, endpoints.config)
    client = TestClient(app)
    roots = {"enclosure": tmp_path / "gets"}
    before = _snapshot(roots)

    repos = client.get("/api/files/repos")
    assert repos.status_code == 200, repos.text[:400]
    assert isinstance(repos.json().get("repos"), list), repos.text[:400]
    listing = client.get("/api/files/list", params={"repo": "agents-remember", "path": ""})
    assert listing.status_code in (200, 400, 403, 404), listing.text[:400]
    missing_repo = client.get("/api/files/list", params={"repo": "no-such-repo"})
    assert missing_repo.status_code in (400, 403, 404), missing_repo.text[:400]
    missing_file = client.get(
        "/api/files/read", params={"repo": "agents-remember", "path": "no/such/file.py"}
    )
    assert missing_file.status_code in (200, 400, 403, 404), missing_file.text[:400]
    notes = client.get("/api/notes/list", params={"repo": "agents-remember"})
    assert notes.status_code in (200, 400, 403, 404), notes.text[:400]
    note = client.get(
        "/api/notes/read", params={"repo": "agents-remember", "path": "no/such/note.md"}
    )
    assert note.status_code in (200, 400, 403, 404), note.text[:400]
    leaf_diff = client.get(
        "/api/changeset/file-diff",
        params={
            "repo": endpoints.repository_id,
            "leaf": "260921-icr-l1",
            "master": endpoints.master,
            "mode": "working",
            "kind": "code",
            "path": "src/anchors.py",
        },
    )
    assert leaf_diff.status_code == 200, leaf_diff.text[:400]
    assert leaf_diff.json()["after"] is not None, leaf_diff.text[:400]
    absent_diff = client.get(
        "/api/changeset/file-diff",
        params={
            "repo": endpoints.repository_id,
            "leaf": "260921-icr-l1",
            "master": endpoints.master,
            "mode": "working",
            "kind": "code",
            "path": "no/such.py",
        },
    )
    assert absent_diff.status_code == 200, absent_diff.text[:400]
    assert absent_diff.json()["after"] is None, absent_diff.text[:400]

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], findings
