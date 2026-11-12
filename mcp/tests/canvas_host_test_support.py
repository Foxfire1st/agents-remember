"""Fixtures for the canvas host and presenter tests: a real task, record, packet and page.

Nothing here starts a model, a role or a browser. The builders write the same durable shapes the
landed owners write, so a test asserts the host's own behaviour rather than a stub's.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.review_evidence_packet import SideState, build_evidence_packet
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    publish_revision,
)
from agents_remember.canvas.paths import presenter_working_root
from agents_remember.canvas.presenter import RunnerReceipt
from agents_remember.canvas.presenter_store import (
    PresenterAttempt,
    digest_bytes,
    receipt_changes,
)
from agents_remember.cli.role_instructions import compose_capability_capsule
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceMap,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameBinding, FrameLimits
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
    ReviewTreesResult,
)

REPO = "agents-remember"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L104"
FRAME = "host-and-presenter"
AT = "2026-10-18T09:00:00Z"

ASSETS: dict[str, bytes] = {
    "index.html": b"<!doctype html><title>comparison</title><p>the explained change</p>",
    "app.css": b"body{font:16px monospace}",
    "app.js": b"document.body.append('ready')",
    "img/mark.svg": (
        b"<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'>"
        b"<rect width='8' height='8' fill='#123'/></svg>"
    ),
}


def record(number: int = 7) -> ReviewTreeComparisonRecord:
    """One recorded comparison: two code sides, two memory sides, no conversion."""

    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=ReviewTreeSide(repository="/repo/code", tree="a" * 40),
        code_candidate=ReviewTreeSide(repository="/repo/code", tree="b" * 40),
        memory_base=ReviewTreeSide(repository="/repo/memory", tree="c" * 40),
        memory_candidate=ReviewTreeSide(repository="/repo/memory", tree="d" * 40),
        recorded_at=AT,
    )


def default_claims(comparison: ReviewTreeComparisonRecord) -> tuple[EvidenceClaim, EvidenceClaim]:
    """The packet's default two claims: a present tree-blob and an unavailable historical record."""

    present = EvidenceClaim(
        claim_id="c-present",
        authoring_revision="rev-1",
        classification="recorded-fact",
        subject="the change to src/app.py",
        applicability="this comparison",
        statement="src/app.py gained one function",
        bases=(
            ClaimBasis(
                kind="tree-blob",
                reference="code-candidate:src/app.py",
                state="present",
                side="code-candidate",
                tree=comparison.code_candidate.tree,
                path="src/app.py",
            ),
        ),
    )
    gone = EvidenceClaim(
        claim_id="c-gone",
        authoring_revision="rev-1",
        classification="unavailable-evidence",
        subject="the earlier record of the same path",
        applicability="this comparison",
        statement="the earlier record cannot be read",
        bases=(
            ClaimBasis(
                kind="knowledge-record",
                reference="knowledge:old-record",
                state="unavailable",
                limitation="the record was pruned before this comparison was recorded",
                record_id="old-record",
                record_revision=2,
                path="onboarding/src/app.md",
            ),
        ),
        limit="the object is gone; today's record is not it",
    )
    return present, gone


def packet(
    comparison: ReviewTreeComparisonRecord,
    *,
    claims: Sequence[EvidenceClaim] | None = None,
    supplemental: Sequence[SupplementalReceipt] = (),
) -> Any:
    """One admitted packet, with the default claims unless the caller names its own."""

    named = default_claims(comparison) if claims is None else claims
    return build_evidence_packet(
        comparison,
        repository_id=REPO,
        scope="the change to src/app.py",
        question="why did it move?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the selected change"),
        supplemental=supplemental,
        claims=named,
        recorded_at=AT,
    )


def config(tmp_path: Path) -> McpRuntimeConfig:
    """A coordination root with one repository, its memory layer and its settings file."""

    return McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        transcript_root=tmp_path / "transcripts",
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO,
                path=tmp_path / "repo",
                memory_root=tmp_path / "memory",
            )
        },
    )


def write_settings(settings: McpRuntimeConfig, block: dict[str, Any] | None) -> None:
    """Write the presenter's block where the landed settings owner reads it.

    The user's own settings file is the one owner of role launch choices (L104-B17); the MCP
    authority file at ``config_path`` is never parsed for them. The write goes through the guard
    so a run can never reach the account's real settings file.
    """

    payload: dict[str, Any] = {"orchestration": {"roles": {}}}
    if block is not None:
        payload["orchestration"]["roles"]["review-presenter"] = block
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def task(settings: McpRuntimeConfig) -> Path:
    """The task directory the frame store and the presenter both live under."""

    root = Path(settings.coordination_root) / "tasks" / REPO / TASK
    (root / "notes" / "reports").mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class PageContent:
    """One revision's own bytes and evidence: what a caller of :func:`publish` may vary."""

    assets: dict[str, bytes] = field(default_factory=lambda: dict(ASSETS))
    claims: Sequence[EvidenceClaim] | None = None
    supplemental: Sequence[SupplementalReceipt] = ()


def publish(
    task_root: Path,
    comparison: ReviewTreeComparisonRecord,
    *,
    request: str = "req-publish",
    page: PageContent | None = None,
    frame: str = FRAME,
) -> int:
    """Publish one ready revision through the store, so the host has something to serve."""

    content = PageContent() if page is None else page
    scope = frame_scope(task_root, LEAF, frame)
    working = Path(task_root).parent / "authoring" / request
    begin_draft(
        scope,
        working_directory=working,
        request_id=request,
        authoring=FrameAuthoring(request_id=request, session_id="seed", started_at=AT),
        focus="the explained change",
        assets=content.assets,
        limits=FrameLimits(),
        now=AT,
    )
    publication = publish_revision(
        scope,
        request_id=request,
        entrypoint="index.html",
        evidence=packet(comparison, claims=content.claims, supplemental=content.supplemental),
        supplemental=tuple(content.supplemental),
        created_at=AT,
        limits=FrameLimits(),
    )
    assert publication.manifest is not None
    return publication.manifest.revision


def trees_result(comparison: ReviewTreeComparisonRecord) -> ReviewTreesResult:
    """The comparison owner's answer, with both memory sides available."""

    return ReviewTreesResult(
        state="trees",
        repository_id=REPO,
        master=TASK,
        leaf_id=LEAF,
        comparison=comparison,
        knowledge_sides=(
            ReviewKnowledgeSide(
                side="before", state="available", tree="c" * 40, index_state="complete"
            ),
            ReviewKnowledgeSide(
                side="after", state="available", tree="d" * 40, index_state="complete"
            ),
        ),
    )


def watch_opens(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every file opened through ``Path.open`` or the no-follow ``os.open``."""

    opened: list[Path] = []
    original_path_open = Path.open
    original_os_open = os.open

    def watch_path(self: Path, *args, **kwargs):
        opened.append(self)
        return original_path_open(self, *args, **kwargs)

    def watch_os(path, *args, **kwargs):
        opened.append(Path(path))
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", watch_path)
    monkeypatch.setattr(os, "open", watch_os)
    return opened


class FakeRunner:
    """A confined authoring execution, as the product's future harness adapter would answer."""

    def __init__(self, *, execution: str = "exec-1", session: str = "sess-1") -> None:
        self.execution = execution
        self.session = session
        self.calls: list[tuple[str, str, Path]] = []

    def start(self, attempt: Any, instructions: str, working: Path) -> RunnerReceipt:
        self.calls.append((attempt.attempt_id, instructions[:20], working))
        return RunnerReceipt(
            execution_id=self.execution, session_id=self.session, model="fake-model"
        )


def swap_on_scandir(monkeypatch, directory: Path, decoy: Path) -> list[str]:
    """Swap after guard/open but just before enumeration; count actual foreign entries yielded."""
    original = os.scandir
    foreign: list[str] = []
    swapped = False

    def scan(path):
        nonlocal swapped
        physical = (
            Path(f"/proc/self/fd/{path}").resolve()
            if isinstance(path, int)
            else Path(path).resolve()
        )
        if not swapped and physical == directory:
            directory.rename(directory.with_name(directory.name + "-held"))
            directory.symlink_to(decoy, target_is_directory=True)
            swapped = True
        physical = (
            Path(f"/proc/self/fd/{path}").resolve()
            if isinstance(path, int)
            else Path(path).resolve()
        )
        entries = original(path)
        if physical != decoy:
            return entries

        class Counted:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                entries.close()

            def __iter__(self):
                for entry in entries:
                    foreign.append(entry.name)
                    yield entry

        return Counted()

    monkeypatch.setattr(os, "scandir", scan)
    return foreign


def stored_attempt(
    presenter, *, evidence=None, request="req-start", focus="the change to src/app.py"
):
    """Seed a historical record for read/message/publication tests; no product start exists.

    Only a supplied deterministic fixture runner creates the local session bytes needed by
    host-operation tests. This fixture does not implement launch admission or reconciliation.
    """

    evidence = evidence if evidence is not None else packet(record())
    existing = presenter.by_request(request)
    if existing is not None:
        return existing
    at = presenter._clock()
    attempt_id = "att-" + digest_bytes(request.encode())[:16]
    working = presenter_working_root(
        presenter._config,
        repository_id=presenter._repository_id,
        task=presenter._task,
        leaf_id=presenter._leaf_id,
        frame_id=presenter._frame_id,
        attempt_id=attempt_id,
    )
    working.mkdir(parents=True)
    evidence_bytes = canonical_json_bytes(json.loads(evidence.model_dump_json(by_alias=True)))
    instructions, _ = compose_capability_capsule("review-presenter", {})
    attempt = PresenterAttempt(
        attempt_id=attempt_id,
        request_id=request,
        caller="dashboard",
        repository_id=presenter._repository_id,
        task=presenter._task,
        leaf_id=presenter._leaf_id,
        frame_id=presenter._frame_id,
        comparison=FrameBinding.from_packet(evidence.comparison),
        evidence_digest=digest_bytes(evidence_bytes),
        focus=focus,
        state="started",
        created_at=at,
        updated_at=at,
        working_directory=str(working),
        model="historical-test-model",
        design_source="default",
        design_sha256="1" * 64,
        design_words=3,
        instructions_sha256=digest_bytes(instructions.encode()),
    )
    with presenter._store.locked():
        presenter._store.evidence_path(attempt_id).write_bytes(evidence_bytes)
        presenter._store.write_locked(attempt)
    receipt = presenter._runner.start(attempt, instructions, working)
    return presenter._store.mutate(
        attempt_id, lambda current: receipt_changes(current, receipt, attempt.model or "")
    )
