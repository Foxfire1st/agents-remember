"""Historical presenter records: conversation, confined publication and path admission.

New authoring is owned by the explainer. These cases seed historical attempts to keep the
existing read, current-frame message, cancellation and publication contracts covered.
"""

from __future__ import annotations

import os
import threading
from contextlib import suppress
from pathlib import Path

import pytest
from agents_remember.application.review_frame_store import (
    FramePublication,
    FrameRefused,
    frame_scope,
    reopen_frame,
)
from agents_remember.canvas import presenter as presenter_module
from agents_remember.canvas.presenter import (
    Presenter,
    PresenterRefused,
    RunnerReceipt,
)
from agents_remember.canvas.prospective import ProspectiveResult
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import (
    AT,
    FRAME,
    LEAF,
    REPO,
    TASK,
    FakeRunner,
    config,
    packet,
    record,
    stored_attempt,
    swap_on_scandir,
    task,
    watch_opens,
    write_settings,
)

LAUNCH = {"harness": "hermes", "model": "the-presenter-model", "effort": "high", "mode": "auto"}
# The old draft depth bound, which a draft now passes: the walk has none (the Manager's 14:29Z ruling).
OLD_ASSET_DEPTH_BOUND = 8


class LoadedProspective:
    """A prospective load that observed a working page.

    These cases assert the publication mechanics, not the load gate; the gate's own behaviour is
    :mod:`test_canvas_prospective`. Without this the default browser load would run inside a unit
    test, which is neither the gate's proof nor this module's subject.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, bytes], str, object]] = []

    def load(self, *, assets, entrypoint, packet):
        self.calls.append((dict(assets), entrypoint, packet))
        return ProspectiveResult(state="loaded", kind="browser")


def _presenter(settings, root: Path, *, runner=None) -> Presenter:
    return Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=runner or FakeRunner(),
        prospective=LoadedProspective(),
        clock=lambda: AT,
    )


def _start(presenter: Presenter, comparison=None, *, request: str = "req-start"):
    comparison = comparison or record()
    return stored_attempt(presenter, evidence=packet(comparison), request=request)


def test_a_stored_attempt_records_identity_activity_and_conversation(tmp_path: Path) -> None:
    """Rules 5, 7, 8: one attributable record, one design text, one bounded conversation."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter)
    assert attempt.state == "running"
    assert attempt.comparison.comparison_reference.startswith("review-comparison:")
    assert attempt.execution_id == "exec-1" and attempt.session_id == "sess-1"
    assert attempt.design_source == "default" and len(attempt.design_sha256) == 64
    assert Path(attempt.working_directory).is_dir()
    noted = presenter.note_activity(attempt.attempt_id, kind="inspection", text="read src/app.py")
    assert noted.activity[-1].kind == "inspection"
    with pytest.raises(PresenterRefused):
        presenter.note_activity(attempt.attempt_id, kind="thought", text="private")
    said = presenter.say(attempt.attempt_id, role="developer", text="why this claim?")
    assert said.conversation[-1].comparison == attempt.comparison.comparison_reference


def test_publication_goes_through_the_store_and_refuses_a_symlinked_asset(
    tmp_path: Path,
) -> None:
    """Rules 5, 7: the confined writer publishes; a symlink is held, not followed."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter)
    assets = Path(attempt.working_directory) / "assets"
    (assets / "nested").mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><p>explained</p>")
    (assets / "app.css").write_text("body{color:#eee}")
    publication = presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert publication.published is True
    assert publication.manifest is not None and publication.manifest.revision == 1
    published = presenter.read(attempt.attempt_id)
    assert published is not None and published.state == "published"
    opening = reopen_frame(frame_scope(root, LEAF, FRAME))
    assert opening.state == "ready" and opening.revision == 1

    second = Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=FakeRunner(execution="exec-2"),
        prospective=LoadedProspective(),
        clock=lambda: AT,
    )
    live = _start(second, request="req-symlink")
    live_assets = Path(live.working_directory) / "assets"
    live_assets.mkdir(parents=True)
    (live_assets / "index.html").write_text("<!doctype html>")
    os.symlink("/etc/passwd", live_assets / "leak.txt")
    with pytest.raises(PresenterRefused) as symlink:
        second.publish(live.attempt_id, entrypoint="index.html")
    assert symlink.value.reason == "symlinked-asset"


def test_cancellation_keeps_the_revision(
    tmp_path: Path,
) -> None:
    """Rules 7, 9: a cancelled attempt publishes nothing and the last ready page survives."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    first = _start(presenter, request="req-a")
    assets = Path(first.working_directory) / "assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><p>one</p>")
    presenter.publish(first.attempt_id, entrypoint="index.html")

    second = _start(presenter, request="req-b")
    cancelled = presenter.cancel(second.attempt_id, reason="the developer stopped it")
    assert cancelled.state == "cancelled"
    opening = reopen_frame(frame_scope(root, LEAF, FRAME))
    assert opening.revision == 1 and opening.state == "ready"


def test_a_resume_without_a_recorded_session_is_refused(tmp_path: Path) -> None:
    """Rule 9: resume uses a recorded session or is refused; it never creates one."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)

    class NoSession:
        """A runner that answers an execution but records no session."""

        def start(self, _attempt, _instructions, _working):
            return RunnerReceipt(execution_id="exec-x")

    presenter = _presenter(settings, root, runner=NoSession())
    attempt = _start(presenter)
    with pytest.raises(PresenterRefused) as no_session:
        presenter.resume(attempt.attempt_id)
    assert no_session.value.reason == "no-recorded-session"
    with pytest.raises(PresenterRefused) as unknown:
        presenter.resume("att-does-not-exist")
    assert unknown.value.reason == "unknown-attempt"


# --- L104-B18: the design text travels with its source and hash -------------------------------


# --- L104-B21: every transition is a locked fresh read-modify-write ---------------------------


class DelayedRunner:
    """A runner that holds its receipt until the test releases it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.receipt = RunnerReceipt(
            execution_id="exec-delayed", session_id="sess-delayed", model="delayed-model"
        )

    def start(self, _attempt, _instructions, _working) -> RunnerReceipt:
        self.entered.set()
        assert self.release.wait(HANG_GUARD_SECONDS)
        return self.receipt


def test_a_delayed_receipt_does_not_revive_a_cancelled_attempt(tmp_path: Path) -> None:
    """Rules 7, 9: the host answer arrives after cancellation and only annotates the record."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    runner = DelayedRunner()
    presenter = _presenter(settings, root, runner=runner)
    errors: list[BaseException] = []

    def run_start() -> None:
        try:
            _start(presenter, request="req-delayed")
        except BaseException as error:  # reported to the test thread
            errors.append(error)

    thread = threading.Thread(target=run_start, daemon=True)
    thread.start()
    assert runner.entered.wait(HANG_GUARD_SECONDS)
    pending = presenter.by_request("req-delayed")
    assert pending is not None
    assert pending.state == "started"

    cancelled = presenter.cancel(pending.attempt_id, reason="the developer stopped it")
    assert cancelled.state == "cancelled"
    runner.release.set()
    thread.join(HANG_GUARD_SECONDS)
    assert not thread.is_alive()
    assert errors == []

    final = presenter.read(pending.attempt_id)
    assert final is not None
    assert final.state == "cancelled"
    assert final.execution_id == "exec-delayed"
    assert final.session_id == "sess-delayed"
    assert "no state was revived" in (final.detail or "")

    successor = _start(presenter, request="req-after-cancel")
    assert successor.attempt_id != pending.attempt_id
    assert successor.state == "running"


def test_a_cancellation_during_a_successful_publication_keeps_the_ready_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rules 7, 9: the store published, so the attempt reports the ready revision."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-publish-race")
    assets = Path(attempt.working_directory) / "assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><p>ready</p>")

    entered = threading.Event()
    release = threading.Event()
    real_publish = presenter_module.publish_revision

    def delayed_publish(*args, **kwargs):
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS)
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(presenter_module, "publish_revision", delayed_publish)
    outcome: dict[str, object] = {}

    def run_publish() -> None:
        try:
            outcome["publication"] = presenter.publish(attempt.attempt_id, entrypoint="index.html")
        except BaseException as error:  # reported to the test thread
            outcome["error"] = error

    thread = threading.Thread(target=run_publish, daemon=True)
    thread.start()
    assert entered.wait(HANG_GUARD_SECONDS)
    presenter.cancel(attempt.attempt_id, reason="stopped during publication")
    release.set()
    thread.join(HANG_GUARD_SECONDS)
    assert not thread.is_alive()
    assert "error" not in outcome
    publication = outcome["publication"]
    assert isinstance(publication, FramePublication)
    assert publication.published is True
    final = presenter.read(attempt.attempt_id)
    assert final is not None
    assert final.state == "published"
    assert final.published_revision == 1
    assert reopen_frame(frame_scope(root, LEAF, FRAME)).revision == 1


def test_a_refused_publication_does_not_overwrite_a_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 9: a cancellation stays cancelled when the store then refuses the publication."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-refused-race")
    assets = Path(attempt.working_directory) / "assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html>")

    entered = threading.Event()
    release = threading.Event()

    def refused_publish(*_args, **_kwargs):
        entered.set()
        assert release.wait(HANG_GUARD_SECONDS)
        raise FrameRefused("the store refused this publication for the race test")

    monkeypatch.setattr(presenter_module, "publish_revision", refused_publish)
    outcome: dict[str, object] = {}

    def run_publish() -> None:
        try:
            presenter.publish(attempt.attempt_id, entrypoint="index.html")
        except BaseException as error:  # reported to the test thread
            outcome["error"] = error

    thread = threading.Thread(target=run_publish, daemon=True)
    thread.start()
    assert entered.wait(HANG_GUARD_SECONDS)
    presenter.cancel(attempt.attempt_id, reason="stopped before the refusal")
    release.set()
    thread.join(HANG_GUARD_SECONDS)
    assert not thread.is_alive()
    assert isinstance(outcome["error"], PresenterRefused)
    final = presenter.read(attempt.attempt_id)
    assert final is not None
    assert final.state == "cancelled"
    assert "publication refused" in (final.detail or "")
    assert "stayed cancelled" in (final.detail or "")


# --- L104-B22: physical admission before any read or write ------------------------------------


def test_a_symlinked_assets_root_is_refused_before_foreign_bytes_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rules 4, 5, 17: the root link is held; the foreign fixture is never opened."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-assets-link")
    assets = Path(attempt.working_directory) / "assets"
    foreign = tmp_path / "foreign-assets"
    foreign.mkdir()
    (foreign / "secret.txt").write_bytes(b"FOREIGN-CANARY")
    os.symlink(foreign, assets)

    opened = watch_opens(monkeypatch)
    with pytest.raises(PresenterRefused) as refused:
        presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert refused.value.reason in {"symlinked-asset", "unsafe-asset-path"}
    assert all(foreign not in path.parents for path in opened)
    assert (foreign / "secret.txt").read_bytes() == b"FOREIGN-CANARY"


def test_an_asset_past_the_old_revision_ceiling_publishes_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R211 v4 rule 3, D567: no asset is refused for its size and no revision for its file
    count; the old 16 MiB and 64-file ceilings are gone."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-huge-asset")
    assets = Path(attempt.working_directory) / "assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html>")
    huge = assets / "huge.css"
    with huge.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    for index in range(64):
        (assets / f"part-{index:02}.css").write_text("p{}")

    del monkeypatch
    published = presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert published.published
    assert published.manifest is not None and len(published.manifest.assets) == 66


def test_a_symlinked_working_folder_is_refused_before_its_bytes_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rules 4, 5, 17: an ancestor link is refused, not only a child asset link."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-working-link")
    working = Path(attempt.working_directory)
    real = working.with_name(f"{working.name}-real")
    working.rename(real)
    foreign = tmp_path / "foreign-working"
    foreign.mkdir()
    (foreign / "assets").mkdir()
    (foreign / "assets" / "index.html").write_text("<!doctype html>FOREIGN")
    os.symlink(foreign, working)

    opened = watch_opens(monkeypatch)
    with pytest.raises(PresenterRefused) as refused:
        presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert refused.value.reason == "unsafe-working-path"
    assert all(foreign not in path.parents for path in opened)


def test_a_deeply_nested_assets_tree_is_published_whole(
    tmp_path: Path,
) -> None:
    """A draft is read however deep it is laid out: past the old depth bound it is published whole,
    and a link is still refused, so the walk cannot loop (MIK-R211, the Manager's 14:29Z ruling)."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-deep-assets")
    assets = Path(attempt.working_directory) / "assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html>")
    deep = assets
    for level in range(OLD_ASSET_DEPTH_BOUND + 2):
        deep = deep / f"d{level}"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("deep")

    publication = presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert publication.published is True and publication.manifest is not None
    stored = frame_scope(root, LEAF, FRAME).frame_dir / "revisions" / "1" / "assets"
    assert (stored / deep.relative_to(assets) / "leaf.txt").read_text() == "deep"


# --- L104-B23: replacement admits its successor before touching its predecessor --------------


# --- L104-B14: no ready pointer moves before the page has been loaded once ----------------------


def test_an_unready_page_is_refused_publication_and_the_last_ready_revision_survives(
    tmp_path: Path,
) -> None:
    """Rule 14: the model-free load check gates the ready pointer and keeps the last ready page."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    first = _start(presenter, request="req-ready")
    assets = Path(first.working_directory) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "index.html").write_text("<!doctype html><p>explained</p>")
    assert presenter.publish(first.attempt_id, entrypoint="index.html").published is True
    scope = frame_scope(root, LEAF, FRAME)
    assert reopen_frame(scope).revision == 1

    second = Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=FakeRunner(execution="exec-2"),
        prospective=LoadedProspective(),
        clock=lambda: AT,
    )
    attempt = _start(second, request="req-unready")
    unready = Path(attempt.working_directory) / "assets"
    unready.mkdir(parents=True, exist_ok=True)
    (unready / "index.html").write_text(
        '<!doctype html><img src="https://example.com/plot.png" alt="">'
    )
    with pytest.raises(PresenterRefused) as refused:
        second.publish(attempt.attempt_id, entrypoint="index.html")
    assert refused.value.reason == "readiness-failed"
    assert "admitted-assets" in refused.value.detail
    assert "example.com/plot.png" in refused.value.detail
    # The last ready revision is untouched, and the attempt stays retryable after a fix.
    assert reopen_frame(scope).revision == 1
    retryable = second.read(attempt.attempt_id)
    assert retryable is not None and retryable.state in {"started", "running"}


@pytest.mark.parametrize("population", [2, 4])
def test_publication_never_enumerates_a_swapped_foreign_asset_directory(
    tmp_path, monkeypatch, population
):
    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="asset-scan-race")
    assets = Path(attempt.working_directory) / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "index.html").write_text("<!doctype html><p>the admitted original page</p>")
    decoy = tmp_path / "foreign-assets"
    decoy.mkdir()
    for number in range(population):
        (decoy / f"foreign-{number}.txt").write_text("foreign bytes")
    foreign = swap_on_scandir(monkeypatch, assets, decoy)
    with suppress(PresenterRefused):
        presenter.publish(attempt.attempt_id, entrypoint="index.html")
    assert foreign == [], f"enumerated {len(foreign)} foreign entries"
