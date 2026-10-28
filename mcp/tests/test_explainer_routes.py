"""The explainer's routes on the review page: the trusted list, the move, the plan, removal, counts.

Each case protects one promise a page reads and cannot recompute: the trusted list of stale parts is
the host's own read of the shown revision (MIK-R187 rules 4 and 8), the moved notice says what moved
and offers nothing more (MIK-R186 rule 7), a planning leaf's page is its plan and nothing else
(MIK-R189 rule 5), only the developer's control removes a revision and never the pinned one
(MIK-R106 v2 rule 6), and an Update answers its counts (MIK-R186 rule 6).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.application import review_frame_store
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    comparison_reference,
)
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    publish_revision,
)
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.application.review_tree_comparison import (
    _code_sides,
    comparison_directory,
)
from agents_remember.canvas.explainer_paths import EXPLAINER_FRAME, leaf_workspace
from agents_remember.canvas.explainer_service import (
    _BUILD_CANCELS,
    _BUILD_STATES,
    BoundComparison,
    ExplainerDeps,
    LeafFacts,
    LeafScope,
    PreparedPacket,
    _progress_tracker,
)
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    read_binding,
    write_binding,
)
from agents_remember.canvas.parts import WHOLE_PAGE_PART
from agents_remember.cli.dashboard import _master_contract
from agents_remember.models.knowledge.review_frame import (
    MAX_TASK_BYTES,
    FrameAuthoring,
    FrameBinding,
    FrameInteractionCoverage,
    FrameLimits,
    FrameManifest,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreesResult,
)
from agents_remember.serving.explainer_move_watch import move_notice_watch
from agents_remember_test_support.testing.prepared_comparison import prepared_from_port

WATCHER_PORTS = explainer_application_ports().watcher
from agents_remember.serving.explainer import (
    ExplainerComposition,
    ExplainerWiring,
    _labelled_for_the_revision,
    explainer_composition,
)
from agents_remember.serving.explainer_routes import register_explainer_routes
from agents_remember.serving.explainer_watcher import (
    LeafSelection,
    MovedNotice,
    blocks_changed,
)
from canvas_host_test_support import ASSETS, REPO, TASK, config, packet, record, task
from fastapi import FastAPI
from fastapi.testclient import TestClient

LEAF = "260928-MIK-L183"
NOW = "2026-10-22T09:00:00+00:00"
AT = "2026-10-22T09:00:00Z"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _comparison(code: Path, memory: Path, number: int):
    """One recorded comparison at the two repositories' current tips."""

    base = record(number)
    code_tree = _git(code, "rev-parse", "HEAD^{tree}")
    memory_tree = _git(memory, "rev-parse", "HEAD^{tree}")
    return base.model_copy(
        update={
            "task_id": TASK,
            "leaf_id": LEAF,
            "code_base": base.code_base.model_copy(
                update={"repository": str(code), "tree": code_tree}
            ),
            "code_candidate": base.code_candidate.model_copy(
                update={"repository": str(code), "tree": code_tree}
            ),
            "memory_base": base.memory_base.model_copy(
                update={"repository": str(memory), "tree": memory_tree}
            ),
            "memory_candidate": base.memory_candidate.model_copy(
                update={"repository": str(memory), "tree": memory_tree}
            ),
        }
    )


def _world(tmp_path: Path):
    code = _repository(tmp_path, "repo", {"src/app.py": "x = 1\n"})
    memory = _repository(tmp_path, "memory", {"system/settings.md": "s\n"})
    runtime = config(tmp_path)
    root = task(runtime)
    return runtime, root, code, memory


def _canonical_contract(
    root: Path,
    code: Path,
    memory: Path,
    *,
    source_branch: str,
    work_branch: str,
) -> Path:
    """One canonical series contract naming each side's fork base and integration line."""

    contract = root / "series-contract.md"
    coordination_root = root.parents[2]
    contract.write_text(
        "---\n"
        "schema: ar-series-contract/v1\n"
        f"task_id: {TASK.upper()}\n"
        f"task_name: {TASK}\n"
        f"repo_name: {REPO}\n"
        "kind: series\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "coordination:\n"
        f"  root: {coordination_root}\n"
        f"  task_root: {root}\n"
        f"  series_contract_path: {contract}\n"
        f"  task_artifact: {root / 'task.md'}\n"
        f"  worktree_group: {coordination_root / 'worktrees' / REPO / TASK}\n"
        "code:\n"
        f"  repo_path: {code}\n"
        f"  source_branch: {source_branch}\n"
        f"  work_branch: {work_branch}\n"
        f"  base_commit: {_git(code, 'rev-parse', 'HEAD')}\n"
        f"  worktree: {code}\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {memory}\n"
        f"  source_branch: {source_branch}\n"
        f"  work_branch: {work_branch}\n"
        f"  base_commit: {_git(memory, 'rev-parse', 'HEAD')}\n"
        f"  ledger: {memory / 'memory.md'}\n"
        "human_review:\n"
        "  status: pending-review\n"
        "  approved_for_commit: no\n"
        "closeout:\n"
        "  status: not-started\n"
        "integration:\n"
        "  status: not-started\n"
        "  cleanup: pending\n"
        "---\n",
        encoding="utf-8",
    )
    return contract


def _publish(
    root: Path,
    comparison,
    *,
    request: str = "req-1",
    coverage: FrameInteractionCoverage | None = None,
) -> int:
    """Publish one ready revision of the explainer frame, bound to ``comparison``."""

    scope = frame_scope(root, LEAF, EXPLAINER_FRAME)
    # A draft is authored outside the task folder, as the store requires.
    working = Path(root).parent / "authoring" / request
    begin_draft(
        scope,
        working_directory=working,
        request_id=request,
        authoring=FrameAuthoring(request_id=request, session_id="seed", started_at=AT),
        focus="the explained change",
        assets=dict(ASSETS),
        limits=FrameLimits(),
        now=AT,
    )
    publication = publish_revision(
        scope,
        request_id=request,
        entrypoint="index.html",
        evidence=packet(comparison),
        supplemental=(),
        created_at=AT,
        limits=FrameLimits(),
        interaction_coverage=coverage,
    )
    assert publication.manifest is not None
    return publication.manifest.revision


def _bound(comparison) -> BoundComparison:
    """The comparison an environment binds, with the record an Update's count needs."""

    binding = FrameBinding.from_record(
        comparison,
        comparison_owner=COMPARISON_OWNER,
        comparison_reference=comparison_reference(comparison),
    )
    return BoundComparison(binding=binding, record_digest=None, record=comparison)


def _write_environment(runtime, binding) -> Path:
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    workspace.mkdir(parents=True, exist_ok=True)
    write_binding(
        workspace,
        ExplainerBinding(
            binding=binding,
            snapshot="snapshot-1",
            prepared_at=NOW,
            packet_digest="sha256:" + "0" * 64,
        ),
    )
    return workspace


def _deps(
    runtime, bound: BoundComparison, *, facts: LeafFacts | None = None, host: _Host | None = None
) -> ExplainerDeps:
    return ExplainerDeps(
        config=runtime,
        host=host or _Host(),
        facts=lambda _scope: facts or LeafFacts(status="inProgress"),
        comparison=lambda _scope, _facts: bound,
        prepare=lambda _scope, _facts, _given: PreparedPacket(
            packet=packet(bound.record), digest="sha256:" + "0" * 64
        ),
        capsule=lambda _scope: "You are the explainer.\n",
        recheck=lambda _scope, _facts, _bound: True,
        counter=partial(blocks_changed, ports=WATCHER_PORTS),
        now=lambda: NOW,
    )


class _Host:
    """A host that records what it was asked and answers live."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.archived: list[str] = []
        self.cancelled: list[tuple[str, str]] = []

    def create_seat(
        self,
        *,
        agent_id: str,
        workspace: Path,
        title: str,
        capsule: str,
        labels: Mapping[str, str],
    ) -> None:
        del workspace, title, capsule, labels
        self.created.append(agent_id)

    def resume(self, agent_id: str) -> None:
        del agent_id

    def send(self, agent_id: str, text: str, *, message_id: str | None = None) -> None:
        del agent_id, text, message_id

    def archive(self, agent_id: str) -> None:
        self.archived.append(agent_id)

    def seat_state(self, agent_id: str) -> str:
        del agent_id
        return "missing"

    def cancel(self, agent_id: str, *, request_id: str) -> dict[str, Any] | None:
        self.cancelled.append((agent_id, request_id))
        return {
            "status": "cancelled",
            "agentId": agent_id,
            "requestId": request_id,
            "turnStopped": True,
            "permissionClosed": True,
            "requestUncertain": False,
        }

    def in_turn(self, agent_id: str) -> bool:
        del agent_id
        return False


def _client(deps: ExplainerDeps, *, contract: Path | None = None) -> TestClient:
    """The routes as the dashboard registers them, with one leaf's contract when a test has one."""

    app = FastAPI()
    register_explainer_routes(
        app,
        ExplainerComposition(
            deps=deps,
            application=explainer_application_ports(),
            contract_reader=None if contract is None else lambda _scope: contract,
        ),
    )
    return TestClient(app)


def _scope() -> LeafScope:
    return LeafScope(repository_id=REPO, task=TASK, leaf_id=LEAF)


def _url(suffix: str) -> str:
    return f"/api/explainer/{REPO}/{TASK}/{LEAF}{suffix}"


def test_the_state_route_answers_the_trusted_list_of_the_page_it_shows(tmp_path: Path) -> None:
    """Rules 4 and 8: the list is the host's read, and a moved basis names its part."""

    runtime, root, code, memory = _world(tmp_path)
    published = _comparison(code, memory, 7)
    _publish(root, published)
    # The leaf moved on: the environment now serves a comparison whose candidate tree differs.
    (code / "src/app.py").write_text("x = 2\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "two")
    moved_on = _comparison(code, memory, 8)
    bound = _bound(moved_on)
    _write_environment(runtime, bound.binding)

    answer = _client(_deps(runtime, bound)).get(_url("/state")).json()

    assert answer["state"] == "ok"
    stale = answer["stale"]
    assert "unavailable" not in stale
    assert stale["total"] == len(stale["parts"]) >= 1
    # The page has no part marks, so the whole page is the part that holds the changed basis.
    assert stale["parts"][0]["id"] == WHOLE_PAGE_PART
    assert "code-candidate" in stale["parts"][0]["reason"]
    assert stale["regeneration"]["partCount"] == stale["total"]


def test_the_state_route_names_why_it_could_not_read_staleness(tmp_path: Path) -> None:
    """Rule 4's failure clause: no revision is never reported as nothing stale."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))

    answer = _client(_deps(runtime, bound)).get(_url("/state")).json()

    assert answer["stale"]["parts"] == []
    assert answer["stale"]["unavailable"] == "the leaf has no retained revision"


def test_the_state_route_without_an_environment_reads_the_comparison_on_screen(
    tmp_path: Path,
) -> None:
    """Rule 4: after Retire the target is the comparison on screen, and a capture writes no record.

    With no enclosure the owner captures nothing, so the read names that instead of calling the page
    current; the leaf's frame area is unchanged by the read.
    """

    runtime, root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    _publish(root, comparison)
    bound = _bound(comparison)
    before = sorted(p.name for p in Path(root).rglob("*"))

    answer = _client(_deps(runtime, bound)).get(_url("/state")).json()

    assert answer["state"] == "ok"
    assert answer["stale"]["parts"] == []
    assert "comparison-unavailable" in answer["stale"]["unavailable"]
    assert sorted(p.name for p in Path(root).rglob("*")) == before


def test_the_state_route_carries_the_notice_the_watcher_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 7: the page's `moved` record is the watcher's own notice, passed through unchanged."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    _write_environment(runtime, bound.binding)
    notice = MovedNotice(
        kind="comparison-moved",
        from_number=7,
        to_number=8,
        blocks_changed=4,
        update_offered=True,
    )
    monkeypatch.setattr(
        "agents_remember.serving.explainer.notice_for", lambda *_args, **_kwargs: notice
    )

    answer = _client(_deps(runtime, bound)).get(_url("/state")).json()

    moved = answer["moved"]
    assert moved is not None
    assert moved["kind"] == "comparison-moved"
    assert (moved["from"], moved["to"], moved["blocks"]) == (7, 8, 4)
    assert moved["updateOffered"] is True
    assert moved["text"] == "task moved: #7 -> #8, 4 blocks changed"


def test_the_state_route_shows_no_notice_it_did_not_earn(tmp_path: Path) -> None:
    """Rule 7: a watcher that cannot tell answers nothing, and the page shows nothing."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    _write_environment(runtime, bound.binding)

    answer = _client(_deps(runtime, bound)).get(_url("/state")).json()

    assert answer["moved"] is None


def test_the_planned_route_answers_a_planning_leaf_and_refuses_another(tmp_path: Path) -> None:
    """Rule 5: a planning leaf's page is its plan; another leaf is refused, not shown one."""

    runtime, root, code, memory = _world(tmp_path)
    # The master's line is its integration branch; before the first landing that branch names no
    # commit, so the planned comparison binds the fork base the contract also names.
    source = _git(code, "branch", "--show-current")
    contract = _canonical_contract(
        root, code, memory, source_branch=source, work_branch="ar/not-landed"
    )
    document = {
        "id": LEAF,
        "expectedKnowledgeEffects": [
            {"subject": "new:the-explainers-folder", "effect": "introduce"}
        ],
        "requirements": ["[MIK-R186@v1](requirements/MIK-R186-v1.md) — owned"],
        "sections": [{"heading": "Prepared context", "body": "the architect's notes"}],
    }
    (root / f"{LEAF}.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "requirements").mkdir(parents=True, exist_ok=True)
    (root / "requirements" / "MIK-R186-v1.md").write_text(
        "# MIK-R186 @ v1 — The explainer's environment\n\n## Scope\n\n- `src/app.py`, and `gone/x.py`.\n",
        encoding="utf-8",
    )
    bound = _bound(_comparison(code, memory, 7))

    planning = _client(_deps(runtime, bound, facts=LeafFacts(status="planning")), contract=contract)
    answer = planning.get(_url("/planned")).json()

    assert answer["state"] == "ok"
    planned = answer["planned"]
    assert planned["topLine"] == "planned · no changes"
    assert planned["binding"]["label"] == "planned"
    row = planned["rows"][0]
    assert row["subject"] == "new:the-explainers-folder"
    assert row["title"] == "planned · introduce"
    assert row["label"] == "the-explainers-folder"
    assert row["statement"] is None and row["statementState"] == "new"
    scopes = {
        entry["path"]: entry["state"]
        for packet_ in planned["packets"]
        for entry in packet_["scope"]
    }
    assert scopes == {"src/app.py": "resolved", "gone/x.py": "text"}
    assert planned["preparedContext"]["state"] == "present"

    started = _client(
        _deps(runtime, bound, facts=LeafFacts(status="inProgress")), contract=contract
    )
    refused = started.get(_url("/planned"))
    assert refused.status_code == 409
    assert refused.json()["reason"] == "planned-unavailable"


def test_the_removal_route_removes_and_never_the_pinned_revision(tmp_path: Path) -> None:
    """Rule 6's control: the developer's own removal, and the pin refused by name."""

    runtime, root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    first = _publish(root, comparison, request="req-1")
    second = _publish(root, comparison, request="req-2")
    bound = _bound(comparison)

    client = _client(_deps(runtime, bound))
    removed = client.post(_url("/remove-revisions"), json={"revisions": [first]}).json()
    assert removed["state"] == "ok" and removed["removed"] == [first]
    assert removed["remaining"] == 1

    frame_dir = frame_scope(root, LEAF, EXPLAINER_FRAME).frame_dir
    manifest_sha = hashlib.sha256(
        (frame_dir / "revisions" / str(second) / "manifest.json").read_bytes()
    ).hexdigest()
    document = {
        "id": LEAF,
        "intentReview": {
            "state": "go",
            "agreedRevision": {
                "revisionId": str(second),
                "sha256": manifest_sha,
                "packetVersions": ["MIK-R189@v2"],
            },
        },
    }
    (root / f"{LEAF}.json").write_text(json.dumps(document), encoding="utf-8")

    pinned = client.post(_url("/remove-revisions"), json={"revisions": [second]})
    assert pinned.status_code == 409
    assert pinned.json()["reason"] == "revision-pinned"


def test_the_cancel_route_targets_the_exact_seat_and_request(tmp_path: Path) -> None:
    """MIK-R105 v1 rule 9: cancel names a stopped turn, prevented publication and uncertainty."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    host = _Host()
    client = _client(_deps(runtime, bound, host=host))

    built = client.post(
        _url("/send"), json={"requestId": "cancel-req-1", "message": "explain this"}
    )
    assert built.status_code == 200, built.text
    agent_id = built.json()["agentId"]

    cancelled = client.post(_url("/cancel"), json={"requestId": "cancel-req-1"})
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()["cancel"]
    assert body["agentId"] == agent_id and body["requestId"] == "cancel-req-1"
    assert body["turnStopped"] is True
    assert body["requestUncertain"] is False
    assert body["publicationPrevented"] is True
    assert host.cancelled == [(agent_id, "cancel-req-1")]

    # A request the leaf's ledger never names is reported uncertain, not claimed cancelled.
    unknown = client.post(_url("/cancel"), json={"requestId": "no-such-request"}).json()
    assert unknown["cancel"]["requestUncertain"] is True

    invalid = client.post(_url("/cancel"), json={})
    assert invalid.status_code == 400


def test_a_cancel_ends_a_live_build_that_never_reached_its_seat(tmp_path: Path) -> None:
    """MIK-R211 v2 rule 5: a build in progress can be ended by hand, and says where it stopped."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    host = _Host()
    client = _client(_deps(runtime, bound, host=host))
    scope = _scope()
    report, _state = _progress_tracker(scope, "stuck-build-1", None)
    report("snapshot-code", {"done": 1, "total": 2})
    try:
        cancelled = client.post(_url("/cancel"), json={"requestId": "stuck-build-1"})
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()["cancel"]
        assert body["buildStopped"] is True
        assert body["failedStep"] == "snapshot-code"
        assert str(body["reason"]).startswith("build-cancelled")
        # The ended record is what the page reads next, so it never sees "building" again: the
        # active build is gone and the ended one is the build the page shows.
        state = client.get(_url("/state")).json()
        assert state["building"] is None
        assert state["build"]["state"] == "failed"
        assert state["build"]["failedStep"] == "snapshot-code"
        assert str(state["build"]["error"]).startswith("build-cancelled")
        # No seat turn was involved: the host's cancel was never asked for.
        assert host.cancelled == []
    finally:
        _BUILD_STATES.clear()
        _BUILD_CANCELS.clear()


def test_the_update_route_answers_counts(tmp_path: Path) -> None:
    """Rule 6: an Update's answer carries the counts the page shows."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    _write_environment(runtime, bound.binding)
    (code / "src/app.py").write_text("x = 4\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "four")
    moved_on = _bound(_comparison(code, memory, 7))
    deps = replace(_deps(runtime, bound), comparison=lambda _scope, _facts: moved_on)

    answer = _client(deps).post(_url("/update"), json={}).json()

    assert answer["state"] == "ok"
    counts = answer["counts"]
    assert {"files", "bytes", "parts", "grades"} <= set(counts)
    assert counts.get("blocks", 0) >= 1


def test_a_publication_enforces_the_intended_authority_at_its_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 6: the revision's binding meets its claim map, and the authority is checked there."""

    seen: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        review_frame_store,
        "require_intended_packet",
        lambda packet, *, comparison_owner, leaf_id, leaf_document=None: seen.append(
            (packet.comparison.comparison_owner, comparison_owner, leaf_document)
        ),
    )
    _runtime, root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    (root / f"{LEAF}.json").write_text(json.dumps({"id": LEAF}), encoding="utf-8")

    _publish(root, comparison)

    assert len(seen) == 1
    recorded_owner, asked_owner, document = seen[0]
    # The packet's own owner is what the check is asked about, and the leaf's document is named.
    assert recorded_owner == asked_owner == COMPARISON_OWNER
    assert document == LEAF


def test_the_master_contract_is_the_one_the_document_names(tmp_path: Path) -> None:
    """Rule 4: a planned page reads the master line's contract, by the document's own field."""

    runtime, root, _code, _memory = _world(tmp_path)
    named = root / "series-contract.md"
    named.write_text("# contract\n", encoding="utf-8")

    assert _master_contract(runtime, _scope()) == named

    (root / "task.json").write_text(
        json.dumps({"id": "SBX-M1", "seriesContractPath": str(named)}), encoding="utf-8"
    )
    assert _master_contract(runtime, _scope()) == named

    # A master that names a contract that is not there, and holds no fallback, refuses by name.
    (root / "task.json").write_text(
        json.dumps({"id": "SBX-M1", "seriesContractPath": str(root / "gone.md")}), encoding="utf-8"
    )
    named.unlink()
    assert _master_contract(runtime, _scope()) is None


def test_the_revisions_route_names_the_task_total(tmp_path: Path) -> None:
    """MIK-R103 v2 item 10: the selector knows what the task holds against its ceiling. Each row
    says its revision's interaction coverage (MIK-R211 v5, the Manager's 15:43Z ruling): one
    published before coverage was recorded says so rather than implying any."""

    runtime, root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    first = _publish(root, comparison, request="req-1")
    window = "the browser load process's own 180 s window (canvas/prospective.py; not a page limit)"
    covered = FrameInteractionCoverage(
        passes_total=5, passes_complete=2, complete=False, window=window
    )
    said = f"2 of 5 passes — stopped before {window}"
    second = _publish(root, comparison, request="req-2", coverage=covered)
    bound = _bound(comparison)

    client = _client(_deps(runtime, bound))
    answer = client.get(_url("/revisions")).json()

    assert answer["state"] == "ok"
    assert [row["revision"] for row in answer["revisions"]] == [second, first]  # newest first
    assert [row["coverage"] for row in answer["revisions"]] == [said, "not recorded"]
    assert client.get(_url("/state")).json()["explanation"]["coverage"] == said
    assert answer["pinned"] is None
    assert answer["total"]["state"] == "ok"
    assert answer["total"]["limit"] == MAX_TASK_BYTES
    assert answer["total"]["used"] > 0


def test_the_stale_route_answers_the_top_bars_badge_without_the_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Review tab's badge reads `/stale` alone: the staleness `/state` carries, the shown
    revision and the watcher's notice -- and no seat, build, retire, part map or usage field."""

    runtime, root, code, memory = _world(tmp_path)
    published = _comparison(code, memory, 7)
    revision = _publish(root, published)
    (code / "src/app.py").write_text("x = 2\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "two")
    bound = _bound(_comparison(code, memory, 8))
    _write_environment(runtime, bound.binding)
    notice = MovedNotice(
        kind="comparison-moved",
        from_number=7,
        to_number=8,
        blocks_changed=4,
        update_offered=True,
    )
    monkeypatch.setattr(
        "agents_remember.serving.explainer.notice_for", lambda *_args, **_kwargs: notice
    )
    client = _client(_deps(runtime, bound))

    badge = client.get(_url("/stale")).json()
    state = client.get(_url("/state")).json()

    assert badge["state"] == "ok"
    assert badge["stale"] == state["stale"]
    assert badge["stale"]["total"] >= 1
    assert (badge["grades"], badge["categories"]) == (state["grades"], state["categories"])
    assert badge["explanation"]["revision"] == revision == state["explanation"]["revision"]
    assert badge["movedNotice"] == state["moved"]
    assert badge["movedNotice"]["text"] == "task moved: #7 -> #8, 4 blocks changed"
    # `parts` here is the staleness's own list (spread from `stale`), not the revision's part map.
    assert badge["parts"] == badge["stale"]["parts"]
    # The label's two marks are the state's own, read without the host's seat state.
    assert (badge["retired"], badge["building"]) == (state["retired"], state["building"])
    seat_and_page = {
        "seat",
        "agentId",
        "workspace",
        "workspaceId",
        "generation",
        "build",
        "retiredAt",
        "claims",
        "frameUsage",
        "agreedRevision",
        "explainer",
    }
    assert not seat_and_page & set(badge)
    assert not leaf_workspace(runtime, REPO, TASK, LEAF).joinpath("retired.json").exists()


def test_a_removed_leaf_reads_as_no_explainer_on_the_badge_and_the_state(tmp_path: Path) -> None:
    """MIK-R211 v5 rule 12: a removal leaves no marker, so nothing reads a leaf as 'retired'.

    A ``retired.json`` an older Retire left is not a mark any more, and once the folder is gone both
    the badge and /state say there is no explainer -- and taking no lock made no folder again.
    """

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    _write_environment(runtime, bound.binding)
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    workspace.joinpath("retired.json").write_text(
        json.dumps({"receipt": None, "at": "2026-10-25T00:00:00Z"})
    )
    client = _client(_deps(runtime, bound))

    assert client.get(_url("/stale")).json()["retired"] is False
    assert client.get(_url("/state")).json()["retired"] is False
    shutil.rmtree(workspace)
    badge = client.get(_url("/stale")).json()
    state = client.get(_url("/state")).json()

    assert badge["retired"] is False and badge["building"] is None
    assert state["retired"] is False and state["seat"]["state"] == "missing"
    assert not workspace.exists()


def test_the_stale_route_says_no_revision_exists_on_a_fresh_leaf(tmp_path: Path) -> None:
    """With no retained revision the badge's `explanation` is null and no notice is invented."""

    runtime, _root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    _write_environment(runtime, bound.binding)

    badge = _client(_deps(runtime, bound)).get(_url("/stale")).json()

    assert badge["explanation"] is None
    assert badge["movedNotice"] is None
    assert badge["stale"]["unavailable"] == "the leaf has no retained revision"


@pytest.mark.parametrize("suffix", ["/state", "/revisions", "/stale"])
def test_the_reads_start_nothing(tmp_path: Path, suffix: str) -> None:
    """MIK-R106 v2 rule 10: a read opens nothing, builds nothing and wakes no seat."""

    runtime, root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    _publish(root, comparison)
    bound = _bound(comparison)
    _write_environment(runtime, bound.binding)

    answer = _client(_deps(runtime, bound)).get(_url(suffix))

    assert answer.status_code == 200
    assert not leaf_workspace(runtime, REPO, TASK, LEAF).joinpath("retired.json").exists()


def test_a_receipt_id_that_is_a_path_is_refused_before_any_effect(tmp_path: Path) -> None:
    """MIK-R186 rule 8: the receipt id names one file, so it is one segment and never a path.

    The id arrives in a request and is used as a file name beside the leaf's receipts; a separator, a
    traversal or an absolute path would write outside that directory, so each is refused as a 400
    with nothing archived, removed, created or written outside the leaf's own folder.
    """

    runtime, root, code, memory = _world(tmp_path)
    bound = _bound(_comparison(code, memory, 7))
    _write_environment(runtime, bound.binding)
    host = _Host()
    client = _client(_deps(runtime, bound, host=host))
    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    before = read_binding(workspace)
    sentinel = tmp_path / "outside" / "sentinel.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("keep me", encoding="utf-8")

    for bad in ("../escape", "a/b", "a\\b", "/etc/passwd", "C:\\retire\\1", ".hidden", ".."):
        answer = client.post(_url("/retire"), json={"receiptId": bad})
        assert answer.status_code == 400, bad
        assert answer.json()["reason"] == "request-invalid", bad
        assert "one file name" in answer.json()["detail"], bad

    assert read_binding(workspace) == before
    assert host.archived == []
    receipts = root / "notes" / "reports" / "explainer" / "explainer-retire" / LEAF
    assert not receipts.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep me"


def test_retire_autogenerates_receipt_id_when_omitted_and_send_composes_context(
    tmp_path: Path,
) -> None:
    runtime, _root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    bound = _bound(comparison)
    _write_environment(runtime, bound.binding)
    client = _client(_deps(runtime, bound))

    sent = client.post(
        _url("/send"),
        json={
            "requestId": "req-send-1",
            "message": "Explain src/app.py",
            "picks": [{"title": "src/app.py", "text": "x = 1"}],
            "stalePartIds": ["part-1"],
        },
    )
    assert sent.status_code == 200
    assert sent.json()["state"] == "ok"

    retired = client.post(_url("/retire"), json={})
    assert retired.status_code == 200
    assert retired.json()["state"] == "ok"
    assert retired.json()["receipt"]["receiptId"].startswith("retire-")


def test_state_and_revisions_report_task_wide_usage_binding_object_and_clear_stale_channel_notice(
    tmp_path: Path,
) -> None:
    runtime, root, code, memory = _world(tmp_path)
    comparison = _comparison(code, memory, 7)
    _publish(root, comparison, request="req-leaf-1")

    other_scope = frame_scope(root, "260928-MIK-L184", EXPLAINER_FRAME)
    other_working = Path(root).parent / "authoring" / "req-leaf-2"
    begin_draft(
        other_scope,
        working_directory=other_working,
        request_id="req-leaf-2",
        authoring=FrameAuthoring(request_id="req-leaf-2", session_id="seed", started_at=AT),
        focus="another leaf",
        assets=dict(ASSETS),
        limits=FrameLimits(),
        now=AT,
    )
    other_comp = comparison.model_copy(update={"leaf_id": "260928-MIK-L184"})
    publish_revision(
        other_scope,
        request_id="req-leaf-2",
        entrypoint="index.html",
        evidence=packet(other_comp),
        supplemental=(),
        created_at=AT,
        limits=FrameLimits(),
    )

    bound = _bound(comparison)
    _write_environment(runtime, bound.binding)
    watch = move_notice_watch(runtime, WATCHER_PORTS)
    selection = LeafSelection(REPO, TASK, LEAF)
    watch.channel.publish(selection, MovedNotice("comparison-moved", 6, 7, 1, True))

    client = _client(_deps(runtime, bound))

    state_res = client.get(_url("/state")).json()
    assert state_res["moved"] is None
    assert watch.channel.latest(selection) is None
    assert state_res["binding"]["owner"] == "recorded"
    assert state_res["binding"]["number"] == 7
    assert state_res["binding"]["reference"] == comparison_reference(comparison)
    assert state_res["binding"]["label"] == "#7"
    assert state_res["comparisonReference"] == comparison_reference(comparison)
    assert isinstance(state_res["transcript"], list)
    assert state_res["frameUsage"]["bytes"] == state_res["frameUsage"]["used"]

    single_leaf_used = review_frame_store.usage(review_frame_store.frames_area(root, LEAF))["bytes"]
    revs_res = client.get(_url("/revisions")).json()
    assert revs_res["total"]["used"] > single_leaf_used
    assert revs_res["total"]["bytes"] == revs_res["total"]["used"]


def test_explainer_composition_wires_live_code_sides_record_digest_and_planned_work_branch(
    tmp_path: Path,
) -> None:
    runtime, root, code, memory = _world(tmp_path)
    source = _git(code, "branch", "--show-current")
    contract = _canonical_contract(
        root, code, memory, source_branch=source, work_branch="ar/master"
    )
    _git(code, "checkout", "-q", "-b", "ar/master")
    _git(memory, "checkout", "-q", "-b", "ar/master")
    (code / "src/app.py").write_text("x = 99\n", encoding="utf-8")
    _git(code, "add", "-A")
    master_code = _git(code, "commit", "-q", "-m", "master move") or _git(code, "rev-parse", "HEAD")
    (memory / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text('{"conversion": "1"}\n', encoding="utf-8")
    _git(memory, "add", "-A")
    master_mem = _git(
        memory, "commit", "-q", "-m", f"master mem\n\nCode-Commit: {master_code}"
    ) or _git(memory, "rev-parse", "HEAD")

    comp = _comparison(code, memory, 1)
    comp_dir = comparison_directory(root, LEAF)
    comp_dir.mkdir(parents=True, exist_ok=True)
    (comp_dir / "1.json").write_text(comp.model_dump_json(by_alias=True), encoding="utf-8")

    (root / f"{LEAF}.json").write_text(
        json.dumps({"id": LEAF, "status": "inProgress"}), encoding="utf-8"
    )

    def _port(_query):
        return ReviewTreesResult(
            state="trees",
            repository_id=REPO,
            master=TASK,
            leaf_id=LEAF,
            comparison=comp,
            code_sides=_code_sides(comp),
            knowledge_sides=(
                ReviewKnowledgeSide(
                    side="before",
                    state="available",
                    tree=comp.memory_base.tree,
                    index_state="complete",
                ),
                ReviewKnowledgeSide(
                    side="after",
                    state="available",
                    tree=comp.memory_candidate.tree,
                    index_state="complete",
                ),
            ),
        )

    composed = explainer_composition(
        runtime,
        _port,
        wiring=ExplainerWiring(
            host=_Host(),
            application=explainer_application_ports(),
            comparison_prepare=prepared_from_port(_port),
        ),
    )
    assert composed.deps is not None
    scope = _scope()
    facts = composed.deps.facts(scope)
    bound = composed.deps.comparison(scope, facts)
    assert bound.record_digest is not None and bound.record_digest.startswith("sha256:")
    prepared = composed.deps.prepare(scope, facts, bound)
    assert prepared.packet.comparison.code_base.state == "available"
    assert prepared.packet.comparison.code_candidate.state == "available"

    (root / f"{LEAF}.json").write_text(
        json.dumps({"id": LEAF, "status": "planning"}), encoding="utf-8"
    )
    composed_planned = explainer_composition(
        runtime,
        _port,
        wiring=ExplainerWiring(
            host=_Host(),
            application=explainer_application_ports(),
            comparison_prepare=prepared_from_port(_port),
            contract_for=lambda _s: contract,
        ),
    )
    assert composed_planned.deps is not None
    p_facts = composed_planned.deps.facts(scope)
    p_bound = composed_planned.deps.comparison(scope, p_facts)
    assert p_bound.planned is not None
    assert p_bound.planned.code_tip == master_code
    assert p_bound.planned.memory_tip == master_mem


def _client_composition(composition) -> TestClient:
    app = FastAPI()
    register_explainer_routes(app, composition)
    return TestClient(app)


def test_a_stale_comparison_number_and_an_unknown_history_are_refused_before_the_seat(
    tmp_path: Path,
) -> None:
    """E02: the first-request selector is checked against the leaf's current comparison."""

    runtime, root, code, memory = _world(tmp_path)
    (root / f"{LEAF}.json").write_text(
        json.dumps({"id": LEAF, "status": "inProgress"}), encoding="utf-8"
    )
    comp = _comparison(code, memory, 7)

    def _port(_query):
        return ReviewTreesResult(
            state="trees",
            repository_id=REPO,
            master=TASK,
            leaf_id=LEAF,
            comparison=comp,
            code_sides=_code_sides(comp),
            knowledge_sides=(
                ReviewKnowledgeSide(
                    side="before",
                    state="available",
                    tree=comp.memory_base.tree,
                    index_state="complete",
                ),
                ReviewKnowledgeSide(
                    side="after",
                    state="available",
                    tree=comp.memory_candidate.tree,
                    index_state="complete",
                ),
            ),
        )

    composed = explainer_composition(
        runtime,
        _port,
        wiring=ExplainerWiring(
            host=_Host(),
            application=explainer_application_ports(),
            comparison_prepare=prepared_from_port(_port),
        ),
    )
    client = _client_composition(composed)

    stale = client.post(
        _url("/build"),
        json={"requestId": "r1", "message": "explain", "comparisonNumber": 6},
    )
    assert stale.status_code == 409
    assert stale.json()["reason"] == "comparison-moved"
    assert stale.json()["requestedComparisonNumber"] == 6
    assert stale.json()["currentComparisonNumber"] == 7

    invalid = client.post(
        _url("/build"), json={"requestId": "r2", "message": "explain", "history": "archive"}
    )
    assert invalid.status_code == 400
    assert invalid.json()["reason"] == "invalid-request"


def test_the_revision_list_and_the_state_read_past_the_old_32_ceiling(tmp_path: Path) -> None:
    """E14: revision 33 is listed and read; no display constant decides what the store holds."""

    runtime, root, code, memory = _world(tmp_path)
    comp = _comparison(code, memory, 7)
    for index in range(33):
        _publish(root, comp, request=f"req-{index + 1}")
    client = _client(_deps(runtime, _bound(comp)))

    listed = client.get(_url("/revisions")).json()
    assert [row["revision"] for row in listed["revisions"]][:2] == [33, 32]

    state = client.get(_url("/state")).json()
    assert state["explanation"]["revision"] == 33


def test_the_state_route_names_the_seat_it_shows(tmp_path: Path) -> None:
    """L183-CD4: the page says which seat it is about, id and generation included."""

    runtime, root, code, memory = _world(tmp_path)
    published = _comparison(code, memory, 7)
    _publish(root, published)
    bound = _bound(published)
    _write_environment(runtime, bound.binding)

    answer = _client(_deps(runtime, bound)).get(_url("/state")).json()

    assert answer["state"] == "ok"
    assert set(answer["seat"]) >= {"state", "agentId", "generation"}
    assert answer["seat"]["agentId"] is None
    assert answer["seat"]["generation"] is None


def test_the_state_label_names_the_revisions_own_comparison() -> None:
    """L201 revision 4 was written against #34 while the leaf's live comparison is #37.

    The page shows revision 4, so the state's label says #34. The live binding's newer comparison is
    the reason the page is stale; it reaches the reader through the staleness record and the top
    bar's badge, never through this label.
    """

    def manifest(number: int | None) -> FrameManifest:
        return cast(
            FrameManifest, SimpleNamespace(binding=SimpleNamespace(comparison_number=number))
        )

    record: dict[str, Any] = {"label": "#37", "present": True, "generation": 6}

    labelled = _labelled_for_the_revision(record, manifest(34))

    assert labelled["label"] == "#34"
    assert labelled["present"] is True and labelled["generation"] == 6
    assert record["label"] == "#37", "the state record itself is not mutated"

    # A revision with no comparison of its own, and no revision at all, keep the live label.
    assert _labelled_for_the_revision(record, manifest(None))["label"] == "#37"
    assert _labelled_for_the_revision(record, None)["label"] == "#37"
