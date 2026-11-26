"""Regression tests for explainer staging confinement, locking, starter copy, kit licences, and runtime media."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    reopen_frame,
)
from agents_remember.canvas import (
    explainer_server,
    explainer_staging,
    explainer_workspace,
    kit,
    kit_references,
    presenter_tools,
)
from agents_remember.canvas.explainer_paths import (
    EXEMPLAR_DIRECTORY,
    EXPLAINER_FRAME,
    STARTERS_DIRECTORY,
    STATE_DIRECTORY,
)
from agents_remember.canvas.explainer_staging import (
    PUBLISH_STAGING_DIRECTORY,
    discard_unused_staging,
    prune_finished_staging,
    publish_working_directory,
)
from agents_remember.canvas.kit import KIT_ROOT
from agents_remember.canvas.paths import PathRefused
from agents_remember.canvas.prospective import BrowserProspectiveLoad, ProspectiveResult
from agents_remember.canvas.readiness import check_page
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameDraft
from canvas_browser_test_support import configure_browser_env
from canvas_host_test_support import LEAF
from canvas_host_test_support import packet as host_packet
from explainer_server_test_support import World, _call, _server, _world

KIT_AR_VIDEO = "kit/ar-video/23fd71bf/ar-video-vocabulary.js"
KIT_SHIKI_CSS = "kit/shiki/4.5.0/magic-move.css"


def _write_claimed_html(draft_dir: Path, body: str, *, head: str = "", tail: str = "") -> Path:
    draft_dir.mkdir(parents=True, exist_ok=True)
    target = draft_dir / "index.html"
    target.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>{head}</head><body>"
        '<section data-part="overview" data-part-title="Overview">'
        '<p data-evidence-claim="c-present">Summary</p>'
        '<p data-evidence-claim="c-gone">Earlier</p>'
        f"{body}</section>{tail}</body></html>",
        encoding="utf-8",
    )
    return target


def _write_kit_draft(world: World, label: str) -> None:
    draft_dir = world.workspace / "drafts" / "assets"
    _write_claimed_html(
        draft_dir, f"<p>{label}</p>", tail='<script type="module" src="app.js"></script>'
    )
    (draft_dir / "app.js").write_text(
        f"import {{ col }} from './{KIT_AR_VIDEO}';\nconsole.log(col('mint'));\n",
        encoding="utf-8",
    )


def _stub_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())

    def _load(self: BrowserProspectiveLoad, **_kwargs: Any) -> ProspectiveResult:
        if self._screenshot is not None:
            self._screenshot.write_bytes(b"\x89PNG-ok")
        return ProspectiveResult(
            state="loaded",
            kind="browser",
            detail="stubbed clean load",
            interactive={"reportOnly": True, "passes": [], "errors": [], "unvisited": []},
        )

    monkeypatch.setattr(BrowserProspectiveLoad, "load", _load)


def _dummy_draft(working: Path) -> FrameDraft:
    return FrameDraft(
        frame_id=EXPLAINER_FRAME,
        request_id="req-reused",
        state="active",
        authoring=FrameAuthoring(
            request_id="req-reused",
            session_id="seat-1",
            started_at="2026-11-25T10:00:00+00:00",
        ),
        working_directory=str(working),
        assets=(),
        focus="explainer",
        updated_at="2026-11-25T10:00:00+00:00",
        lease_until="2026-11-25T11:00:00+00:00",
    )


def _assert_symlink_refused_direct_and_via_tool(
    world: World, marker: Path, expected_text: str, carried: dict[str, bytes]
) -> None:
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    with pytest.raises(PathRefused):
        prune_finished_staging(world.workspace, scope)
    assert marker.read_text(encoding="utf-8") == expected_text

    with pytest.raises(PathRefused):
        publish_working_directory(
            world.workspace, world.workspace / "drafts", carried, draft_digest="a" * 32
        )
    assert marker.read_text(encoding="utf-8") == expected_text

    _write_kit_draft(world, "Kit page")
    reply = _call(_server(world), "render")
    assert reply["publication"]["state"] == "refused", reply
    assert reply["publication"]["refusal"] == "path-held", reply
    assert any("symlink" in line.lower() or "path-held" in line for line in reply["errors"])
    assert marker.read_text(encoding="utf-8") == expected_text
    assert reopen_frame(scope).revision is None


def test_staging_guards_refuse_symlinks_and_surface_as_refused_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symlinked staging or state dirs and outside discard paths refuse without outside deletion."""

    _stub_loaded(monkeypatch)
    carried = {KIT_AR_VIDEO: b"export const x = 1;\n"}

    # state/publish-staging symlinked to an outside directory containing a victim folder.
    world_a = _world(tmp_path / "case-staging-link")
    outside_a = tmp_path / "outside-a"
    victim_a = outside_a / "victim-dir"
    victim_a.mkdir(parents=True)
    marker_a = victim_a / "marker.txt"
    marker_a.write_text("must-survive-a", encoding="utf-8")
    state_a = world_a.workspace / STATE_DIRECTORY
    state_a.mkdir(parents=True, exist_ok=True)
    (state_a / PUBLISH_STAGING_DIRECTORY).symlink_to(outside_a, target_is_directory=True)
    _assert_symlink_refused_direct_and_via_tool(world_a, marker_a, "must-survive-a", carried)

    # state/ itself symlinked to an outside directory.
    world_b = _world(tmp_path / "case-state-link")
    outside_b = tmp_path / "outside-b"
    victim_b = outside_b / PUBLISH_STAGING_DIRECTORY / "victim-dir"
    victim_b.mkdir(parents=True)
    marker_b = victim_b / "marker.txt"
    marker_b.write_text("must-survive-b", encoding="utf-8")
    state_b = world_b.workspace / STATE_DIRECTORY
    if state_b.exists():
        shutil.rmtree(state_b)
    state_b.symlink_to(outside_b, target_is_directory=True)
    _assert_symlink_refused_direct_and_via_tool(world_b, marker_b, "must-survive-b", carried)

    # Reused earlier working directory pointing outside or through a symlink is refused.
    world_c = _world(tmp_path / "case-reused-and-discard")
    outside_c = tmp_path / "outside-c"
    outside_c.mkdir()
    marker_c = outside_c / "marker.txt"
    marker_c.write_text("must-survive-c", encoding="utf-8")
    staging_root_c = world_c.workspace / STATE_DIRECTORY / PUBLISH_STAGING_DIRECTORY
    staging_root_c.mkdir(parents=True, exist_ok=True)
    linked_reused = staging_root_c / "linked-reused"
    linked_reused.symlink_to(outside_c, target_is_directory=True)
    for bad_target in (linked_reused, outside_c):
        with pytest.raises(PathRefused):
            publish_working_directory(
                world_c.workspace,
                world_c.workspace / "drafts",
                carried,
                draft_digest="c" * 32,
                earlier=_dummy_draft(bad_target),
            )

    # discard_unused_staging refuses an outside path and leaves its files intact.
    with pytest.raises(PathRefused):
        discard_unused_staging(world_c.workspace, outside_c, None)
    assert marker_c.read_text(encoding="utf-8") == "must-survive-c"


def _patch_if_present(
    monkeypatch: pytest.MonkeyPatch, target: object, attr: str, value: Any
) -> None:
    if hasattr(target, attr):
        monkeypatch.setattr(target, attr, value)


def _assert_prune_does_not_delete_in_flight_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path / "race-stage-before-draft")
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    _write_kit_draft(world, "Race A")

    real_working = explainer_staging.publish_working_directory
    real_active = explainer_staging.active_staging_dirs
    staging_created = threading.Event()
    prune_read_active = threading.Event()
    draft_began = threading.Event()
    prune_done = threading.Event()

    def _hook_working(*args: Any, **kwargs: Any) -> tuple[Path, bool]:
        result = real_working(*args, **kwargs)
        staging_created.set()
        prune_read_active.wait(timeout=0.35)
        return result

    def _hook_active(active_scope: Any) -> set[Path]:
        active = real_active(active_scope)
        prune_read_active.set()
        draft_began.wait(timeout=0.35)
        return active

    def _hook_begin(*args: Any, **kwargs: Any) -> FrameDraft:
        draft = begin_draft(*args, **kwargs)
        draft_began.set()
        if prune_read_active.is_set():
            prune_done.wait(timeout=0.8)
        return draft

    for module in (explainer_staging, explainer_server):
        _patch_if_present(monkeypatch, module, "publish_working_directory", _hook_working)
        _patch_if_present(monkeypatch, module, "begin_draft", _hook_begin)
    monkeypatch.setattr(explainer_staging, "active_staging_dirs", _hook_active)

    prune_errors: list[BaseException] = []

    def _run_prune() -> None:
        try:
            assert staging_created.wait(timeout=3.0)
            prune_finished_staging(world.workspace, scope)
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            prune_errors.append(exc)
        finally:
            prune_done.set()

    pruner = threading.Thread(target=_run_prune, daemon=True)
    pruner.start()
    render_result = explainer_server.render_and_publish(
        world.workspace, task_root=world.task_root, seat="seat-1"
    )
    pruner.join(timeout=5.0)
    assert not pruner.is_alive(), "prune thread deadlocked"
    assert not prune_errors, prune_errors
    assert render_result["publication"]["state"] == "published", render_result
    assert reopen_frame(scope).revision == 1


def _assert_render_blocks_at_prune_barrier_without_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path / "race-prune-barrier")
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    stale = world.workspace / STATE_DIRECTORY / PUBLISH_STAGING_DIRECTORY / "stale-old"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("stale", encoding="utf-8")
    _write_kit_draft(world, "Race B")

    real_working = explainer_staging.publish_working_directory
    real_active = explainer_staging.active_staging_dirs
    pruner_tid = threading.get_ident()
    prune_entered_gap = threading.Event()
    gap_barrier = threading.Barrier(2, timeout=0.35)
    state = {"timed_out": False, "entered_during_gap": False}

    def _barrier_active(active_scope: Any) -> set[Path]:
        active = real_active(active_scope)
        if threading.get_ident() == pruner_tid:
            prune_entered_gap.set()
            try:
                gap_barrier.wait()
            except threading.BrokenBarrierError:
                state["timed_out"] = True
        return active

    def _probe_working(*args: Any, **kwargs: Any) -> tuple[Path, bool]:
        if not gap_barrier.broken:
            try:
                gap_barrier.wait()
                state["entered_during_gap"] = True
            except threading.BrokenBarrierError:
                pass
        return real_working(*args, **kwargs)

    monkeypatch.setattr(explainer_staging, "active_staging_dirs", _barrier_active)
    for module in (explainer_staging, explainer_server):
        _patch_if_present(monkeypatch, module, "publish_working_directory", _probe_working)

    render_box: list[dict[str, Any]] = []
    render_errors: list[BaseException] = []

    def _run_render() -> None:
        try:
            assert prune_entered_gap.wait(timeout=3.0)
            render_box.append(
                explainer_server.render_and_publish(
                    world.workspace, task_root=world.task_root, seat="seat-2"
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            render_errors.append(exc)

    renderer = threading.Thread(target=_run_render, daemon=True)
    renderer.start()
    prune_finished_staging(world.workspace, scope)
    renderer.join(timeout=5.0)
    assert not renderer.is_alive(), "render thread deadlocked"
    assert not render_errors, render_errors
    assert state["timed_out"] and not state["entered_during_gap"]
    assert not stale.exists()
    assert render_box and render_box[0]["publication"]["state"] == "published", render_box
    assert reopen_frame(scope).revision == 1


def _assert_identical_concurrent_renders_serialize(tmp_path: Path) -> None:
    world = _world(tmp_path / "race-identical-renders")
    _write_kit_draft(world, "Identical")
    start_both = threading.Barrier(2, timeout=3.0)
    outcomes: list[dict[str, Any]] = []
    outcome_errors: list[BaseException] = []

    def _identical_render(seat_name: str) -> None:
        try:
            start_both.wait()
            outcomes.append(
                explainer_server.render_and_publish(
                    world.workspace, task_root=world.task_root, seat=seat_name
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            outcome_errors.append(exc)

    threads = [
        threading.Thread(target=_identical_render, args=(name,), daemon=True)
        for name in ("seat-a", "seat-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
        assert not thread.is_alive()
    assert not outcome_errors, outcome_errors
    assert sorted(item["publication"]["state"] for item in outcomes) == ["published", "unchanged"]


def test_prune_and_render_draft_serialize_under_task_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prune and draft staging serialize under one task lock without losing files or deadlocking."""

    with monkeypatch.context() as ctx:
        _stub_loaded(ctx)
        _assert_prune_does_not_delete_in_flight_staging(tmp_path, ctx)
    with monkeypatch.context() as ctx:
        _stub_loaded(ctx)
        _assert_render_blocks_at_prune_barrier_without_deadlock(tmp_path, ctx)
    with monkeypatch.context() as ctx:
        _stub_loaded(ctx)
        _assert_identical_concurrent_renders_serialize(tmp_path)


def test_copy_package_folder_replaces_stray_file_and_refuses_symlink(tmp_path: Path) -> None:
    """Stray regular files at starters and exemplar are unlinked and replaced while symlinks refuse."""

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    src_starters = tmp_path / "src-starters"
    (src_starters / "demo").mkdir(parents=True)
    (src_starters / "demo" / "index.html").write_text("starter-content", encoding="utf-8")

    src_exemplar = tmp_path / "src-exemplar"
    src_exemplar.mkdir()
    (src_exemplar / "index.html").write_text("exemplar-content", encoding="utf-8")

    starters_dst = workspace / STARTERS_DIRECTORY
    exemplar_dst = workspace / EXEMPLAR_DIRECTORY
    starters_dst.write_text("stray starters file", encoding="utf-8")
    exemplar_dst.write_text("stray exemplar file", encoding="utf-8")

    copied_starters = explainer_workspace._copy_package_folder(
        src_starters, starters_dst, root=tmp_path, label="explainer starters"
    )
    copied_exemplar = explainer_workspace._copy_package_folder(
        src_exemplar, exemplar_dst, root=tmp_path, label="explainer exemplar"
    )
    assert copied_starters == 1
    assert copied_exemplar == 1
    assert (starters_dst / "demo" / "index.html").read_text(encoding="utf-8") == "starter-content"
    assert (exemplar_dst / "index.html").read_text(encoding="utf-8") == "exemplar-content"

    # Regression guard: a symbolic link at starters or exemplar is still refused.
    outside = tmp_path / "outside-target"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("untouched", encoding="utf-8")
    shutil.rmtree(starters_dst)
    starters_dst.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathRefused):
        explainer_workspace._copy_package_folder(
            src_starters, starters_dst, root=tmp_path, label="explainer starters"
        )
    assert keep.read_text(encoding="utf-8") == "untouched"


def test_render_refuses_publish_when_kit_licence_notice_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing kit licence notice refuses publish even when the draft carries the verified kit file."""

    _stub_loaded(monkeypatch)
    world = _world(tmp_path / "world-missing-licence")

    temp_kit = tmp_path / "temp-kit"
    shutil.copytree(KIT_ROOT, temp_kit)
    (temp_kit / "shiki" / "4.5.0" / "LICENSE").unlink()
    monkeypatch.setattr(kit, "KIT_ROOT", temp_kit)
    monkeypatch.setattr(kit_references, "KIT_ROOT", temp_kit)

    draft_dir = world.workspace / "drafts" / "assets"
    _write_claimed_html(
        draft_dir,
        "<p>Styled with Shiki</p>",
        head=f'<link rel="stylesheet" href="{KIT_SHIKI_CSS}">',
    )
    # The draft carries the verified shipped CSS file itself, so static readiness alone passes.
    carried_css = draft_dir / KIT_SHIKI_CSS
    carried_css.parent.mkdir(parents=True, exist_ok=True)
    carried_css.write_bytes((temp_kit / "shiki" / "4.5.0" / "magic-move.css").read_bytes())

    rendered = _call(_server(world), "render")
    assert rendered["publication"]["state"] == "refused", rendered
    assert rendered["publication"]["refusal"] == "kit-problem", rendered
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    errors_text = " ".join(report["allErrors"])
    assert "kit-file-absent" in errors_text, report["allErrors"]
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    assert reopen_frame(scope).revision is None


def test_browser_render_refuses_script_inserted_nonconforming_media_and_publishes_conforming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chromium prospective load checks script-inserted media rules before publication."""

    configure_browser_env(monkeypatch)
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())

    # 1. Script appends <video autoplay>, <audio loop>, and a <video> without controls.
    bad_world = _world(tmp_path / "world-bad-media")
    bad_draft = bad_world.workspace / "drafts" / "assets"
    _write_claimed_html(
        bad_draft,
        '<main><h1>Dynamic media</h1><div id="host"></div></main>',
        tail=(
            "<script>"
            "const host = document.getElementById('host');"
            "const vAuto = document.createElement('video');"
            "vAuto.autoplay = true; vAuto.controls = true;"
            "const aLoop = document.createElement('audio');"
            "aLoop.loop = true; aLoop.controls = true;"
            "const vBare = document.createElement('video');"
            "host.append(vAuto, aLoop, vBare);"
            "</script>"
        ),
    )
    # Static check alone does not execute scripts and would mark this markup ready.
    static_report = check_page(
        {"index.html": (bad_draft / "index.html").read_bytes()},
        entrypoint="index.html",
        packet=host_packet(bad_world.record),
    )
    assert static_report.ready, static_report.diagnostics()

    bad_reply = _call(_server(bad_world), "render")
    assert bad_reply["state"] == "failed", bad_reply
    assert bad_reply["publication"]["state"] == "not attempted", bad_reply
    bad_report = json.loads((bad_world.workspace / bad_reply["report"]).read_text(encoding="utf-8"))
    errors_text = " ".join(bad_report["allErrors"])
    assert "media-controls" in errors_text, bad_report["allErrors"]
    assert "autoplays" in errors_text, bad_report["allErrors"]
    assert "loops" in errors_text, bad_report["allErrors"]
    assert "without controls" in errors_text, bad_report["allErrors"]
    assert reopen_frame(frame_scope(bad_world.task_root, LEAF, EXPLAINER_FRAME)).revision is None

    # 2. The same page with conforming script-inserted media (controls, no autoplay, no loop) publishes.
    good_world = _world(tmp_path / "world-good-media")
    good_draft = good_world.workspace / "drafts" / "assets"
    _write_claimed_html(
        good_draft,
        '<main><h1>Dynamic media</h1><div id="host"></div></main>',
        tail=(
            "<script>"
            "const host = document.getElementById('host');"
            "const vOk = document.createElement('video');"
            "vOk.controls = true;"
            "const aOk = document.createElement('audio');"
            "aOk.controls = true;"
            "host.append(vOk, aOk);"
            "</script>"
        ),
    )
    good_reply = _call(_server(good_world), "render")
    assert good_reply["state"] == "loaded", good_reply
    assert good_reply["publication"]["state"] == "published", good_reply
    assert good_reply["publication"]["revision"] == 1
    assert good_reply["errorsTotal"] == 0, good_reply
    assert reopen_frame(frame_scope(good_world.task_root, LEAF, EXPLAINER_FRAME)).revision == 1
