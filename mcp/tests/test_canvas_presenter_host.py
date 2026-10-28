"""MIK-R105 L104-B15/B16: the confined harness and the identity-bound host operations.

These cases explicitly inject the deterministic LocalPresenterHarness fixture. Production wires no
presenter harness: its native launch was retired with the dedicated tool server (MIK-R186 @ v2
Scope), so a presenter built without a runner refuses. These cases hold the facade and fixture
contracts; they are not model authoring or native-host evidence:

* a start prepares the attempt's folder, writes presentation output there only, reads exactly the
  three admitted inputs by digest, and records a host session whose receipt the attempt keeps;
* a turn travels through the host, its transcript grows, and the receipt it answered is recorded;
* status, resume, cancel and replacement name one exact session and record what the host answered;
* a native-scope write and a foreign-scope read are refused by name;
* the documentation surface is separate and cannot be handed to review as evidence;
* constructing a presenter, reading its view or asking its status starts and resumes nothing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from agents_remember.canvas.presenter import (
    Presenter,
    PresenterRefused,
    RunnerReceipt,
)
from agents_remember.canvas.presenter_host import (
    SESSION_SCHEMA,
    HarnessRefused,
    HostIdentity,
    LocalPresenterHarness,
)
from agents_remember.canvas.prospective import ProspectiveResult
from canvas_host_test_support import (
    AT,
    FRAME,
    LEAF,
    REPO,
    TASK,
    config,
    packet,
    record,
    stored_attempt,
    task,
    write_settings,
)

LAUNCH = {"harness": "hermes", "model": "the-presenter-model", "effort": "high", "mode": "auto"}


class LoadedProspective:
    """A prospective load that observed a working page.

    These cases are about the host harness and the confined publication, not the load gate (see
    :mod:`test_canvas_prospective`); the shipped browser load would otherwise run here.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, bytes], str, object]] = []

    def load(self, *, assets, entrypoint, packet):
        self.calls.append((dict(assets), entrypoint, packet))
        return ProspectiveResult(state="loaded", kind="browser")


def _identity() -> HostIdentity:
    return HostIdentity(repository_id=REPO, task=TASK, leaf_id=LEAF, frame_id=FRAME)


def _harness(settings) -> LocalPresenterHarness:
    return LocalPresenterHarness(settings, identity=_identity(), clock=lambda: AT)


def _presenter(settings, *, runner=None) -> Presenter:
    """A presenter with an explicit deterministic harness fixture unless the case names another runner."""

    return Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=runner if runner is not None else _harness(settings),
        prospective=LoadedProspective(),
        clock=lambda: AT,
    )


def _start(presenter: Presenter, *, request: str = "req-host"):
    return stored_attempt(presenter, evidence=packet(record()), request=request)


def test_stored_attempt_fixture_has_a_confined_draft_and_host_receipt(tmp_path: Path) -> None:
    """Rules 1, 4, 14: one prepared folder, exact grants, and a named draft result."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)

    assert attempt.state == "running"
    assert attempt.session_id == f"sess-{attempt.attempt_id}"
    assert attempt.execution_id == f"exec-{attempt.attempt_id}"
    working = Path(attempt.working_directory)
    assert (working / "assets" / "index.html").is_file()
    assert (working / "assets" / "app.css").is_file()
    assert (working / "host" / "session.json").is_file()

    grants = json.loads((working / "host" / "inputs.json").read_text())
    assert [grant["role"] for grant in grants] == ["evidence", "instructions"]
    assert all(len(grant["sha256"]) == 64 for grant in grants)

    render = presenter.draft(attempt.attempt_id)
    assert render.state == "draft-ready"
    assert render.ready is True
    assert render.surface == "static-html-readiness"
    assert (render.width, render.height) == (1280, 720)
    assert render.browser is None
    assert "no browser was launched" in render.detail


def test_the_authored_draft_publishes_through_the_confined_writer(tmp_path: Path) -> None:
    """Rules 14, 15/L104-B15: the authored page is admitted, then published by MIK-R103 only."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)

    publication = presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert publication.published is True
    assert publication.manifest is not None and publication.manifest.revision == 1
    stored = presenter.read(attempt.attempt_id)
    assert stored is not None and stored.state == "published"
    assert stored.published_revision == 1

    restarted = _presenter(settings)
    opening = restarted.opening()
    assert opening["state"] == "ready" and opening["revision"] == 1
    current = restarted.current()
    assert current is not None and current.published_revision == 1
    # A ready page outlives its host session. Explicit conversation recovery still names that
    # session and does not turn its published attempt into a second authoring attempt.
    resumed = restarted.resume(attempt.attempt_id)
    assert resumed.session_id == attempt.session_id
    assert resumed.state == "published" and resumed.published_revision == 1
    restarted.say(attempt.attempt_id, role="developer", text="Why did you say that?")
    assert any(
        "Product publication receipt" in turn.text and "published revision 1" in turn.text
        for turn in restarted.transcript(attempt.attempt_id)
    )


def test_a_turn_travels_to_the_host_and_its_receipt_is_recorded(tmp_path: Path) -> None:
    """Rule 4/B16: the conversation is a host operation, not an annotation beside one."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)
    before = presenter.status(attempt.attempt_id)
    assert before["source"] == "host" and before["turns"] == 1

    said = presenter.say(attempt.attempt_id, role="developer", text="what function was gained?")
    turns = presenter.transcript(attempt.attempt_id)
    assert [turn.role for turn in turns] == ["system-owner", "developer", "presenter"]
    assert turns[0].lane == "system-owner" and turns[-1].lane == "developer"
    assert "gained one function" in turns[-1].text
    assert [entry.role for entry in said.conversation] == ["developer", "presenter"]
    assert said.detail is not None
    assert "host message" in said.detail

    after = presenter.status(attempt.attempt_id)
    assert after["turns"] == 3 and after["state"] == "running"
    assert after["session_id"] == attempt.session_id


def test_resume_keeps_the_same_identity_across_a_restart(tmp_path: Path) -> None:
    """Rules 9, 11: the same recorded session is reconciled, never re-created."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    first = _presenter(settings)
    attempt = _start(first)
    first.say(attempt.attempt_id, role="developer", text="what function was gained?")
    before = first.status(attempt.attempt_id)

    restarted = _presenter(settings)
    resumed = restarted.resume(attempt.attempt_id)
    assert resumed.session_id == attempt.session_id
    assert resumed.execution_id == attempt.execution_id
    assert resumed.detail is not None
    assert "resumed the recorded session" in resumed.detail

    after = restarted.status(attempt.attempt_id)
    assert after["session_id"] == before["session_id"]
    assert after["execution_id"] == before["execution_id"]
    assert after["turns"] == before["turns"]


def test_cancel_is_truthful_and_publication_stays_refused(tmp_path: Path) -> None:
    """Rules 8, 9: the host's cancellation is recorded and no page is later claimed."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)

    cancelled = presenter.cancel(attempt.attempt_id, reason="the developer stopped it")
    assert cancelled.state == "cancelled"
    assert cancelled.detail is not None
    assert "host cancellation recorded" in cancelled.detail
    assert presenter.status(attempt.attempt_id)["state"] == "cancelled"

    with pytest.raises(PresenterRefused) as not_publishable:
        presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert not_publishable.value.reason == "not-publishable"


def test_archive_stores_the_transcript_beside_the_session(tmp_path: Path) -> None:
    """Rule 11/B16: an archived session keeps its transcript outside the page's assets."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)
    presenter.say(attempt.attempt_id, role="developer", text="what function was gained?")

    session_id = attempt.session_id
    assert session_id is not None
    receipt = _harness(settings).archive(session_id)
    assert receipt.state == "archived"
    archive = Path(receipt.detail)
    assert archive == Path(attempt.working_directory) / "host" / "archive.json"
    stored = json.loads(archive.read_text())
    assert stored["schema"] == SESSION_SCHEMA
    assert [turn["role"] for turn in stored["turns"]][-2:] == ["developer", "presenter"]
    assert not (Path(attempt.working_directory) / "assets" / "archive.json").exists()


def test_a_native_scope_write_escape_is_refused_by_name(tmp_path: Path) -> None:
    """Rules 4, 5, 17: presentation output has one write area, and no other target exists."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)
    harness = _harness(settings)
    session_id = attempt.session_id
    assert session_id is not None

    with pytest.raises(HarnessRefused) as traversal:
        harness.write_output(session_id, "../escape.html", b"x")
    assert traversal.value.reason == "native-scope-escape"

    durable = Path(attempt.working_directory).parent.parent / "escape.html"
    with pytest.raises(HarnessRefused) as absolute:
        harness.write_output(session_id, str(durable), b"x")
    assert absolute.value.reason == "native-scope-escape"
    assert not durable.exists()

    evidence = _record_files(settings)[0]
    admitted = evidence.read_bytes()
    with pytest.raises(HarnessRefused) as evidence_write:
        harness.write_output(session_id, str(evidence), b"x")
    assert evidence_write.value.reason == "native-scope-escape"
    assert evidence.read_bytes() == admitted


def test_a_foreign_scope_read_is_refused_but_each_grant_is_readable(tmp_path: Path) -> None:
    """Rules 4, 5: the harness reads its three inputs and nothing else."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)
    harness = _harness(settings)
    grants = json.loads((Path(attempt.working_directory) / "host" / "inputs.json").read_text())
    session_id = attempt.session_id
    assert session_id is not None

    for grant in grants:
        payload = harness.read_input(session_id, grant["path"])
        assert hashlib.sha256(payload).hexdigest() == grant["sha256"]

    with pytest.raises(HarnessRefused) as foreign:
        harness.read_input(session_id, "/etc/passwd")
    assert foreign.value.reason == "foreign-scope-read"

    with pytest.raises(HarnessRefused) as sibling:
        harness.read_input(session_id, Path(settings.config_path))
    assert sibling.value.reason == "foreign-scope-read"


def test_documentation_is_a_separate_surface_and_not_review_evidence(tmp_path: Path) -> None:
    """Rules 14, 15: the authoring tools are documentation, never an admitted claim."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)

    documentation = presenter.documentation()
    assert documentation.evidence is False
    assert "authoring" in documentation.topics()
    assert "read" in documentation.read("capability").lower()
    with pytest.raises(HarnessRefused) as refused:
        documentation.as_review_evidence()
    assert refused.value.reason == "not-review-evidence"

    assets = Path(attempt.working_directory) / "assets"
    assert sorted(path.name for path in assets.iterdir()) == ["app.css", "index.html"]


def test_the_draft_reports_a_requested_page_it_cannot_load(tmp_path: Path) -> None:
    """Rule 14: the draft names its own failures instead of claiming a rendering."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings)
    attempt = _start(presenter)
    (Path(attempt.working_directory) / "assets" / "index.html").unlink()

    render = presenter.draft(attempt.attempt_id)
    assert render.state == "draft-not-ready" and render.ready is False
    assert any(error.startswith("entrypoint:") for error in render.errors)
    assert render.browser is None


def test_constructing_a_presenter_starts_nothing(tmp_path: Path) -> None:
    """Rule 4/B16: no mount, read or status starts an authoring run or resumes a session."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)
    work_base = Path(settings.coordination_root) / "presenter"

    presenter = _presenter(settings)
    assert presenter.attempts() == ()
    assert presenter.live() is None
    presenter.opening()
    assert not work_base.exists()

    attempt = _start(presenter)
    assert attempt.state == "running"
    assert presenter.live() is not None


def test_a_start_only_runner_keeps_the_conversation_local_and_says_so(tmp_path: Path) -> None:
    """B16: without host operations the record is local, and its status says exactly that."""

    settings = config(tmp_path)
    task(settings)
    write_settings(settings, LAUNCH)

    class StartOnly:
        """A runner that can author but answers no session operation."""

        def start(self, _attempt, _instructions, _working) -> RunnerReceipt:
            return RunnerReceipt(execution_id="exec-local", session_id="sess-local")

    presenter = _presenter(settings, runner=StartOnly())
    attempt = _start(presenter)
    said = presenter.say(attempt.attempt_id, role="developer", text="why this claim?")
    assert said.conversation[-1].text == "why this claim?"
    status = presenter.status(attempt.attempt_id)
    assert status["source"] == "local"
    assert "no host operations" in status["detail"]
    assert presenter.transcript(attempt.attempt_id) == said.conversation


def _record_files(settings) -> list[Path]:
    """The presenter's durable record files, so a probe can name one exact evidence path."""

    root = (
        Path(settings.coordination_root)
        / "tasks"
        / REPO
        / TASK
        / "notes"
        / "reports"
        / "review-presenter"
        / LEAF
        / FRAME
    )
    return sorted(root.glob("*.evidence.json"))
