"""End-to-end proof for shipped starters, exemplar, kit carrying, and revision inventory."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application import review_frame_inventory
from agents_remember.application.review_frame_inventory import (
    INVENTORY_NAME,
    INVENTORY_OMITTED_STORAGE_LIMIT_REASON,
    revision_inventory,
    revision_inventory_omitted_reason,
)
from agents_remember.application.review_frame_store import (
    FrameRefused,
    frame_scope,
    read_draft,
    reopen_frame,
)
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
    task_frames_total,
)
from agents_remember.canvas import explainer_server, presenter_tools
from agents_remember.canvas.explainer_browser import browser_environment, node_executable
from agents_remember.canvas.explainer_paths import EXPLAINER_FRAME
from agents_remember.canvas.explainer_workspace import EXEMPLAR_ROOT, STARTERS_ROOT
from agents_remember.canvas.inventory import inventory_summary
from agents_remember.canvas.kit import KIT_ROOT
from agents_remember.canvas.kit_references import carry_kit_assets
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.models.knowledge.review_frame import FRAME_MANIFEST_SCHEMA, FrameLimits
from agents_remember.serving.canvas import canvas_hosts, register_canvas_routes
from agents_remember.serving.explainer import ExplainerWiring, explainer_composition
from agents_remember.serving.explainer_routes import register_explainer_routes
from agents_remember.serving.write_guard import RefuseCrossOriginWrites
from canvas_browser_test_support import configure_browser_env
from canvas_host_test_support import LEAF, REPO, TASK, config, trees_result
from canvas_host_test_support import packet as host_packet
from explainer_server_test_support import World, _call, _server, _world
from fastapi import FastAPI
from fastapi.testclient import TestClient

STARTER_SLUGS = (
    "shiki-magic-move",
    "motion-canvas-step-through",
    "open-generative-ui-trace",
    "ar-video-measured",
)
STARTER_EXPECTED_KIT: dict[str, tuple[str, ...]] = {
    "shiki-magic-move": (
        "kit/shiki/4.5.0/shiki-kit.js",
        "kit/shiki/4.5.0/magic-move.css",
        "kit/shiki/4.5.0/LICENSE",
    ),
    "motion-canvas-step-through": (
        "kit/motion-canvas/3.17.2/motion-canvas-kit.js",
        "kit/motion-canvas/3.17.2/LICENSE",
    ),
    "open-generative-ui-trace": (
        "kit/open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js",
        "kit/open-generative-ui/0.1.0-8391a94/LICENSE",
    ),
    "ar-video-measured": (
        "kit/ar-video/23fd71bf/ar-video-vocabulary.js",
        "kit/ar-video/23fd71bf/VOCABULARY.md",
    ),
}
STARTER_EXPECTED_LIBRARY: dict[str, str] = {
    "shiki-magic-move": "shiki",
    "motion-canvas-step-through": "motion-canvas",
    "open-generative-ui-trace": "open-generative-ui",
    "ar-video-measured": "ar-video",
}


def _folder_assets(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _prepare_empty_claims_world(tmp_path: Path) -> World:
    world = _world(tmp_path)
    empty_packet = host_packet(world.record, claims=())
    (world.workspace / "instructions" / "evidence.json").write_text(
        json.dumps(empty_packet.model_dump(by_alias=True), indent=2), encoding="utf-8"
    )
    return world


def _copy_folder_to_draft(source: Path, draft_dir: Path) -> None:
    if draft_dir.exists():
        shutil.rmtree(draft_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    for rel, payload in _folder_assets(source).items():
        target = draft_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def _assert_kit_assets_surfaced(
    rendered: dict[str, Any], report: dict[str, Any], expected: Sequence[str]
) -> None:
    report_kit = report.get("kitAssets", [])
    for path in expected:
        assert path in report_kit
    inline_kit = rendered.get("kitAssets")
    if isinstance(inline_kit, list):
        for path in expected:
            assert path in inline_kit
    else:
        assert any(
            entry.get("part") == "kitAssets"
            and entry.get("total") == len(report_kit)
            and entry.get("to") == rendered["report"]
            for entry in rendered.get("moved", [])
        ), rendered


def _write_claimed_page(draft_dir: Path, body: str, *, head: str = "", tail: str = "") -> None:
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "index.html").write_text(
        f"<!doctype html><html><head><meta charset='utf-8'>{head}</head><body>"
        '<section data-part="overview" data-part-title="Overview">'
        '<p data-evidence-claim="c-present">Summary</p>'
        '<p data-evidence-claim="c-gone">Earlier</p>'
        f"{body}</section>{tail}</body></html>",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _ensure_browser_env(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_browser_env(monkeypatch)


@pytest.mark.parametrize("starter_slug", STARTER_SLUGS)
def test_each_starter_passes_readiness_and_browser_checks_alone(
    starter_slug: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    starter_dir = STARTERS_ROOT / starter_slug
    assert (starter_dir / "index.html").is_file()

    world = _prepare_empty_claims_world(tmp_path)
    draft_dir = world.workspace / "drafts" / "assets"
    _copy_folder_to_draft(starter_dir, draft_dir)

    rendered = _call(_server(world), "render")
    assert rendered["state"] == "loaded", rendered
    assert rendered["publication"]["state"] == "published", rendered
    assert rendered["publication"]["revision"] == 1
    assert rendered["errorsTotal"] == 0, rendered
    assert not (draft_dir / "kit").exists()

    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    assert report["readiness"]["ready"] is True, report
    assert report["allErrors"] == []
    _assert_kit_assets_surfaced(rendered, report, STARTER_EXPECTED_KIT[starter_slug])

    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    rev_dir = scope.frame_dir / "revisions" / "1"
    assert (rev_dir / "manifest.json").is_file() and (rev_dir / INVENTORY_NAME).is_file()
    stored_inv = revision_inventory(scope.frame_dir, 1)
    assert stored_inv == report["inventory"] == rendered.get("inventory")
    assert stored_inv is not None
    assert stored_inv["figures"]["total"] == 1
    assert stored_inv["kitLibraries"] == [STARTER_EXPECTED_LIBRARY[starter_slug]]
    assert stored_inv["unusedAssets"] == []


def test_exemplar_passes_non_evidence_checks_and_reports_full_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    assert (EXEMPLAR_ROOT / "index.html").is_file()

    world = _prepare_empty_claims_world(tmp_path)
    draft_dir = world.workspace / "drafts" / "assets"
    _copy_folder_to_draft(EXEMPLAR_ROOT, draft_dir)

    rendered = _call(_server(world), "render")
    assert rendered["state"] == "loaded", rendered
    assert rendered["publication"]["state"] == "published", rendered
    assert rendered["publication"]["revision"] == 1
    assert rendered["errorsTotal"] == 0, rendered
    assert not (draft_dir / "kit").exists()

    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    assert report["readiness"]["ready"] is True, report
    assert report["allErrors"] == []
    _assert_kit_assets_surfaced(
        rendered,
        report,
        (
            "kit/shiki/4.5.0/shiki-kit.js",
            "kit/shiki/4.5.0/magic-move.css",
            "kit/motion-canvas/3.17.2/motion-canvas-kit.js",
            "kit/open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js",
            "kit/ar-video/23fd71bf/ar-video-vocabulary.js",
        ),
    )

    expected_inventory = {
        "figures": {
            "total": 6,
            "byKind": {"svg": 1, "canvas": 2, "driven": 6},
            "byTechnique": {
                "before-and-after": 1,
                "code-excerpt": 1,
                "code-morph": 1,
                "sandbox": 1,
                "step-through": 1,
                "trace-a-path": 1,
            },
        },
        "kitLibraries": ["ar-video", "motion-canvas", "open-generative-ui", "shiki"],
        "codeExcerpts": {"highlighted": 5, "plain": 0},
        "unusedAssets": [],
    }
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    rev_dir = scope.frame_dir / "revisions" / "1"
    assert (rev_dir / "manifest.json").is_file() and (rev_dir / INVENTORY_NAME).is_file()
    stored_inv = revision_inventory(scope.frame_dir, 1)
    assert report["inventory"] == expected_inventory
    assert rendered.get("inventory") == expected_inventory
    assert stored_inv == expected_inventory
    summary = inventory_summary(stored_inv)
    expected_summary = (
        "6 figures (1 SVG, 2 canvas, 6 driven; before-and-after, code-excerpt, code-morph, "
        "sandbox, step-through, trace-a-path) · kit: ar-video, motion-canvas, "
        "open-generative-ui, shiki · code: 5 highlighted"
    )
    assert summary == expected_summary

    opened = reopen_frame(scope)
    assert opened.state == "ready" and opened.manifest is not None
    client = TestClient(_build_explainer_dashboard_app(world), base_url="http://127.0.0.1:4123")
    state_resp = client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/state")
    assert state_resp.status_code == 200, state_resp.text
    assert state_resp.json()["explanation"]["inventory"] == expected_summary
    revs_resp = client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/revisions")
    assert revs_resp.status_code == 200, revs_resp.text
    assert revs_resp.json()["revisions"][0]["inventory"] == expected_summary
    sess_resp = client.post(
        "/api/canvas/sessions",
        headers={"origin": "http://127.0.0.1:4123"},
        json={
            "repository": REPO,
            "master": TASK,
            "leaf": LEAF,
            "frame": EXPLAINER_FRAME,
            "revision": 1,
        },
    )
    assert sess_resp.status_code == 200, sess_resp.text
    session_body = sess_resp.json()
    assert session_body["sandbox"] == "allow-scripts"
    page_url = session_body["pageUrl"]
    assert page_url.startswith("/canvas/page/")
    doc_resp = client.get(page_url)
    assert doc_resp.status_code == 200
    assert b"ar-canvas-binding/v1" in doc_resp.content
    assert "sandbox allow-scripts" in doc_resp.headers.get("content-security-policy", "")
    assert client.get(f"{page_url}exemplar.js").status_code == 200
    assert client.get(f"{page_url}kit/shiki/4.5.0/shiki-kit.js").status_code == 200


def test_render_publishes_kit_by_reference_and_persists_inventory_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    world = _world(tmp_path)
    ticks = iter(f"2026-11-24T09:{minute:02d}:00+00:00" for minute in range(60))
    server = _server(world, now=lambda: next(ticks))

    draft_dir = world.workspace / "drafts" / "assets"
    _write_claimed_page(
        draft_dir,
        '<figure id="fig-code" data-technique="code-excerpt">'
        '<div id="code-host"><pre><code>const x = 1;</code></pre></div></figure>',
        head='<link rel="stylesheet" href="kit/shiki/4.5.0/magic-move.css">',
        tail='<script type="module" src="app.js"></script>',
    )
    (draft_dir / "app.js").write_text(
        "import { createHighlighter } from './kit/shiki/4.5.0/shiki-kit.js';\n"
        "const hl = await createHighlighter();\n"
        "document.getElementById('code-host').innerHTML = hl.codeToHtml('const x = 1;', { lang: 'javascript', theme: 'github-dark' });\n",
        encoding="utf-8",
    )
    (draft_dir / "dead-fragment.html").write_text("<p>unused</p>", encoding="utf-8")

    rendered = _call(server, "render")
    assert rendered["state"] == "loaded" and rendered["publication"]["state"] == "published"
    assert rendered["publication"]["revision"] == 1
    assert not (draft_dir / "kit").exists()

    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    _assert_kit_assets_surfaced(
        rendered,
        report,
        (
            "kit/shiki/4.5.0/shiki-kit.js",
            "kit/shiki/4.5.0/magic-move.css",
            "kit/shiki/4.5.0/LICENSE",
        ),
    )
    inv = report["inventory"]
    assert inv["figures"]["total"] == 1 and inv["figures"]["byTechnique"] == {"code-excerpt": 1}
    assert inv["kitLibraries"] == ["shiki"] and inv["codeExcerpts"]["highlighted"] >= 1
    assert inv["unusedAssets"] == ["dead-fragment.html"] and rendered.get("inventory") == inv

    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    opened = reopen_frame(scope)
    assert opened.state == "ready" and opened.manifest is not None
    assert opened.manifest.schema_ == FRAME_MANIFEST_SCHEMA
    asset_paths = {asset.path for asset in opened.manifest.assets}
    assert {"kit/shiki/4.5.0/shiki-kit.js", "kit/shiki/4.5.0/magic-move.css"} <= asset_paths
    assert INVENTORY_NAME not in asset_paths
    assert revision_inventory(scope.frame_dir, 1) == inv


def test_render_refuses_tampered_draft_kit_copy_with_outside_kit_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    world = _world(tmp_path)
    draft_dir = world.workspace / "drafts" / "assets"
    (draft_dir / "kit" / "shiki" / "4.5.0").mkdir(parents=True, exist_ok=True)
    (draft_dir / "kit" / "shiki" / "4.5.0" / "magic-move.css").write_text(
        "/* tampered */\n", encoding="utf-8"
    )
    _write_claimed_page(
        draft_dir, "", head='<link rel="stylesheet" href="kit/shiki/4.5.0/magic-move.css">'
    )

    rendered = _call(_server(world), "render")
    assert rendered["publication"]["state"] == "not attempted"
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    assert report["readiness"]["ready"] is False
    assert "outside 'kit/'" in " ".join(report["allErrors"])


def test_render_reports_zero_figures_and_unused_asset_for_plain_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    world = _world(tmp_path)
    draft_dir = world.workspace / "drafts" / "assets"
    _write_claimed_page(draft_dir, "<pre><code>plain_code = True</code></pre>")
    (draft_dir / "orphan.html").write_text("<p>orphan</p>", encoding="utf-8")

    rendered = _call(_server(world), "render")
    assert rendered["state"] == "loaded" and rendered["publication"]["state"] == "published"
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    inv: dict[str, Any] = report["inventory"]
    assert inv == {
        "figures": {"total": 0, "byKind": {"svg": 0, "canvas": 0, "driven": 0}, "byTechnique": {}},
        "kitLibraries": [],
        "codeExcerpts": {"highlighted": 0, "plain": 1},
        "unusedAssets": ["orphan.html"],
    }
    assert rendered["inventory"] == inv


def test_render_carries_commented_dynamic_import_and_unspaced_css_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    world = _world(tmp_path)
    draft_dir = world.workspace / "drafts" / "assets"
    _write_claimed_page(
        draft_dir,
        '<p class="note" id="note">Loaded</p>',
        head='<link rel="stylesheet" href="app.css">',
        tail='<script type="module" src="app.js"></script>',
    )
    (draft_dir / "app.css").write_text(
        '@import"kit/shiki/4.5.0/magic-move.css";\n'
        ".note::after { content: \"@import 'not-a-file.css'; url(not-a-file.png)\"; }\n",
        encoding="utf-8",
    )
    (draft_dir / "app.js").write_text(
        "const { col } = await import(/* note */ './kit/ar-video/23fd71bf/ar-video-vocabulary.js');\n"
        "document.getElementById('note').dataset.tone = col('mint');\n",
        encoding="utf-8",
    )

    rendered = _call(_server(world), "render")
    assert rendered["state"] == "loaded" and rendered["publication"]["state"] == "published"
    assert rendered["errorsTotal"] == 0, rendered
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    _assert_kit_assets_surfaced(
        rendered,
        report,
        ("kit/ar-video/23fd71bf/ar-video-vocabulary.js", "kit/shiki/4.5.0/magic-move.css"),
    )
    assert report["inventory"]["kitLibraries"] == ["ar-video", "shiki"]
    assert report["inventory"]["unusedAssets"] == []


def test_render_preserves_active_kit_staging_across_refusal_and_distinct_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    world = _world(tmp_path)
    ticks = iter(f"2026-11-24T10:{minute:02d}:00+00:00" for minute in range(60))
    server = _server(world, now=lambda: next(ticks))

    draft_dir = world.workspace / "drafts" / "assets"
    _write_claimed_page(
        draft_dir, '<p id="tone">Draft A</p>', tail='<script type="module" src="app.js"></script>'
    )
    draft_a_html = (draft_dir / "index.html").read_text(encoding="utf-8")
    draft_b_html = draft_a_html.replace("Draft A", "Draft B")
    (draft_dir / "app.js").write_text(
        "import { col } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';\n"
        "document.getElementById('tone').dataset.tone = col('mint');\n",
        encoding="utf-8",
    )

    real_publish = explainer_server.publish_revision
    failed_once = False

    def _fail_first_publish(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise FrameRefused("temporary store capacity limit", reason="frame-store-full")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(explainer_server, "publish_revision", _fail_first_publish)

    refused = _call(server, "render")
    assert refused["publication"]["state"] == "refused"
    assert refused["publication"]["refusal"] == "frame-store-full"

    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    draft_entries = sorted((scope.frame_dir / "drafts").iterdir())
    assert len(draft_entries) == 1
    draft_a = read_draft(scope, draft_entries[0].name)
    assert draft_a is not None and draft_a.state == "active"
    staging_a = Path(draft_a.working_directory)
    staged_kit_a = staging_a / "assets" / "kit" / "ar-video" / "23fd71bf" / "ar-video-vocabulary.js"
    assert staged_kit_a.is_file()

    (draft_dir / "index.html").write_text(draft_b_html, encoding="utf-8")
    published_b = _call(server, "render")
    assert published_b["publication"]["state"] == "published"
    assert published_b["publication"]["revision"] == 1
    assert staged_kit_a.is_file()

    (draft_dir / "index.html").write_text(draft_a_html, encoding="utf-8")
    retried_a = _call(server, "render")
    assert retried_a["publication"]["state"] == "published"
    assert retried_a["publication"]["revision"] == 2
    assert not staging_a.exists()


def test_render_excludes_script_loaded_asset_and_flags_unreferenced_kit_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    world = _world(tmp_path)
    draft_dir = world.workspace / "drafts" / "assets"
    unused_kit_rel = "kit/open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js"
    unused_kit_dst = draft_dir / unused_kit_rel
    unused_kit_dst.parent.mkdir(parents=True, exist_ok=True)
    unused_kit_dst.write_bytes(
        (KIT_ROOT / "open-generative-ui/0.1.0-8391a94/open-generative-ui-kit.js").read_bytes()
    )
    (draft_dir / "plot.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        '<rect width="80" height="40" fill="#58c9dc"/></svg>',
        encoding="utf-8",
    )
    (draft_dir / "orphan.txt").write_text("unused note\n", encoding="utf-8")
    (draft_dir / "unused.js").write_text(
        'import { clamp } from "./kit/ar-video/23fd71bf/ar-video-vocabulary.js";\n'
        "export const n = clamp(1, 0, 2);\n",
        encoding="utf-8",
    )
    _write_claimed_page(
        draft_dir,
        '<figure id="fig-plot" data-technique="before-and-after"><div id="host"></div></figure>',
        head='<link rel="stylesheet" href="kit/shiki/4.5.0/magic-move.css">',
        tail='<script type="module" src="app.js"></script>',
    )
    (draft_dir / "app.js").write_text(
        "const img = document.createElement('img');\n"
        "img.alt = 'Dynamic plot';\n"
        "img.src = 'plot.svg';\n"
        "document.getElementById('host').appendChild(img);\n",
        encoding="utf-8",
    )

    rendered = _call(_server(world), "render")
    assert rendered["state"] == "loaded" and rendered["publication"]["state"] == "published"
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    stored_inv = revision_inventory(scope.frame_dir, 1)

    for inv in (rendered["inventory"], report["inventory"], stored_inv):
        assert inv is not None
        assert "plot.svg" not in inv["unusedAssets"]
        assert inv["unusedAssets"] == [unused_kit_rel, "orphan.txt", "unused.js"]
        assert inv["kitLibraries"] == ["shiki"]
        assert "ar-video" not in inv["kitLibraries"]
        assert "open-generative-ui" not in inv["kitLibraries"]


def _make_budget_clock() -> Callable[[], str]:
    ticks = iter(f"2026-11-24T11:{minute:02d}:00+00:00" for minute in range(60))
    return lambda: next(ticks)


def _measure_two_budget_revisions(
    root: Path, *, world_factory: Callable[[Path], World] = _world
) -> tuple[int, int, int, int]:
    world = world_factory(root)
    server = _server(world, now=_make_budget_clock())
    _write_claimed_page(world.workspace / "drafts" / "assets", "<p>Budget v1</p>")
    rev1 = _call(server, "render")
    assert rev1["publication"]["state"] == "published", rev1
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    inv1_bytes = (scope.frame_dir / "revisions" / "1" / INVENTORY_NAME).stat().st_size
    assert inv1_bytes > 0
    area = scope.area.parent
    rev1_without_inv = sum(p.stat().st_size for p in area.rglob("*") if p.is_file()) - inv1_bytes

    _write_claimed_page(world.workspace / "drafts" / "assets", "<p>Budget v2</p>")
    rev2 = _call(server, "render")
    assert rev2["publication"]["state"] == "published" and rev2["publication"]["revision"] == 2
    inv2_bytes = (scope.frame_dir / "revisions" / "2" / INVENTORY_NAME).stat().st_size
    assert inv2_bytes > 0
    both_without_inv = (
        sum(p.stat().st_size for p in area.rglob("*") if p.is_file()) - inv1_bytes - inv2_bytes
    )
    return rev1_without_inv, inv1_bytes, both_without_inv, inv2_bytes


def _physical_task_bytes(task_root: Path) -> int:
    scope = frame_scope(task_root, LEAF, EXPLAINER_FRAME)
    return sum(p.stat().st_size for p in scope.area.parent.rglob("*") if p.is_file())


def _assert_omitted_inventory(
    world: World,
    rendered: Mapping[str, Any],
    revision: int,
    *,
    limit: int,
) -> dict[int, dict[str, Any]]:
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    disk = _physical_task_bytes(world.task_root)
    assert disk <= limit and disk == limit
    assert task_frames_total(world.task_root, LEAF)["bytes"] == disk
    assert revision_inventory(scope.frame_dir, revision) is None
    assert (
        revision_inventory_omitted_reason(scope.frame_dir, revision)
        == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    )
    assert "inventory" not in rendered
    assert rendered["inventoryOmitted"] == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    assert rendered["publication"]["inventoryOmitted"] == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    assert "inventory" not in report
    assert report["inventoryOmitted"] == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    with TestClient(_build_explainer_dashboard_app(world)) as client:
        state_body = client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/state").json()
        revs_body = client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/revisions").json()
    assert state_body["explanation"]["revision"] == revision
    assert "inventory" not in state_body["explanation"]
    assert state_body["explanation"]["inventoryOmitted"] == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    by_rev = {row["revision"]: row for row in revs_body["revisions"]}
    assert "inventory" not in by_rev[revision]
    assert by_rev[revision]["inventoryOmitted"] == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    return by_rev


def test_render_inventory_sidecar_respects_task_byte_cap_and_omits_at_zero_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    exact_rev1, inv1_bytes, exact_both, inv2_bytes = _measure_two_budget_revisions(
        tmp_path / "case-a"
    )
    assert inv1_bytes > 0 and inv2_bytes > 0

    # Case B: revision 1 fits exact_rev1 with zero headroom left for inventory.json.
    tight_rev1_world = _world(tmp_path / "case-b")
    _write_claimed_page(tight_rev1_world.workspace / "drafts" / "assets", "<p>Budget v1</p>")
    monkeypatch.setattr(explainer_server, "FrameLimits", lambda: FrameLimits(task_bytes=exact_rev1))
    tight_rev1 = _call(_server(tight_rev1_world, now=_make_budget_clock()), "render")
    assert tight_rev1["publication"]["state"] == "published", tight_rev1
    _assert_omitted_inventory(tight_rev1_world, tight_rev1, 1, limit=exact_rev1)

    # Case C: cap fits rev 1 + rev 1's inventory + rev 2, but not rev 2's inventory.
    # Rev 1 keeps its inventory (nothing evicted); rev 2 publishes without its sidecar.
    tight_cap_both = exact_both + inv1_bytes
    tight_rev2_world = _world(tmp_path / "case-c")
    monkeypatch.setattr(
        explainer_server, "FrameLimits", lambda: FrameLimits(task_bytes=tight_cap_both)
    )
    tight_rev2_server = _server(tight_rev2_world, now=_make_budget_clock())
    _write_claimed_page(tight_rev2_world.workspace / "drafts" / "assets", "<p>Budget v1</p>")
    tight_pair_1 = _call(tight_rev2_server, "render")
    assert tight_pair_1["publication"]["state"] == "published"
    assert "inventoryOmitted" not in tight_pair_1
    rev1_disk = _physical_task_bytes(tight_rev2_world.task_root)
    assert rev1_disk == exact_rev1 + inv1_bytes <= tight_cap_both
    assert task_frames_total(tight_rev2_world.task_root, LEAF)["bytes"] == rev1_disk

    _write_claimed_page(tight_rev2_world.workspace / "drafts" / "assets", "<p>Budget v2</p>")
    tight_pair_2 = _call(tight_rev2_server, "render")
    assert tight_pair_2["publication"]["state"] == "published"
    assert tight_pair_2["publication"]["revision"] == 2
    by_rev = _assert_omitted_inventory(tight_rev2_world, tight_pair_2, 2, limit=tight_cap_both)
    tight_rev2_scope = frame_scope(tight_rev2_world.task_root, LEAF, EXPLAINER_FRAME)
    assert revision_inventory(tight_rev2_scope.frame_dir, 1) is not None
    assert "inventory" in by_rev[1] and "inventoryOmitted" not in by_rev[1]

    # Case D: one byte below rev 1 + inv 1 + rev 2 refuses rev 2 with frame-store-full.
    over_rev2_world = _world(tmp_path / "case-d")
    over_cap = tight_cap_both - 1
    monkeypatch.setattr(explainer_server, "FrameLimits", lambda: FrameLimits(task_bytes=over_cap))
    over_rev2_server = _server(over_rev2_world, now=_make_budget_clock())
    _write_claimed_page(over_rev2_world.workspace / "drafts" / "assets", "<p>Budget v1</p>")
    assert _call(over_rev2_server, "render")["publication"]["state"] == "published"
    over_rev1_disk = _physical_task_bytes(over_rev2_world.task_root)
    assert (
        over_rev1_disk <= over_cap
        and task_frames_total(over_rev2_world.task_root, LEAF)["bytes"] == over_rev1_disk
    )
    _write_claimed_page(over_rev2_world.workspace / "drafts" / "assets", "<p>Budget v2</p>")
    over_pair_2 = _call(over_rev2_server, "render")
    assert (
        over_pair_2["publication"]["state"] == "refused"
        and over_pair_2["publication"]["refusal"] == "frame-store-full"
    )
    assert _physical_task_bytes(over_rev2_world.task_root) <= over_cap

    # Case E: an unwritable sidecar still cannot fail publication.
    fail_world = _world(tmp_path / "case-e")
    _write_claimed_page(fail_world.workspace / "drafts" / "assets", "<p>Budget v1</p>")
    monkeypatch.setattr(explainer_server, "FrameLimits", FrameLimits)
    monkeypatch.setattr(
        review_frame_inventory,
        "write_revision_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    fail_rendered = _call(_server(fail_world, now=_make_budget_clock()), "render")
    assert (
        fail_rendered["publication"]["state"] == "published" and fail_rendered["errorsTotal"] == 0
    )
    fail_scope = frame_scope(fail_world.task_root, LEAF, EXPLAINER_FRAME)
    assert reopen_frame(fail_scope).state == "ready"
    assert revision_inventory(fail_scope.frame_dir, 1) is None


def _initialize_dashboard_world(path: Path) -> World:
    world = _world(path)
    with TestClient(_build_explainer_dashboard_app(world)) as client:
        client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/state").raise_for_status()
    return world


def test_render_zero_headroom_omission_visible_at_first_dashboard_read_without_cap_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presenter_tools, "browser_missing", lambda: ())
    cap, _, _, _ = _measure_two_budget_revisions(
        tmp_path / "case-a", world_factory=_initialize_dashboard_world
    )

    world = _initialize_dashboard_world(tmp_path / "case-b")
    _write_claimed_page(world.workspace / "drafts" / "assets", "<p>Budget v1</p>")
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    original_record = explainer_server._record_published_inventory
    observed: dict[str, Any] = {}

    def _read_routes() -> dict[str, Any]:
        with TestClient(_build_explainer_dashboard_app(world)) as client:
            state_resp = client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/state")
            revs_resp = client.get(f"/api/explainer/{REPO}/{TASK}/{LEAF}/revisions")
        state_resp.raise_for_status()
        revs_resp.raise_for_status()
        expl = state_resp.json()["explanation"]
        rev1 = next(row for row in revs_resp.json()["revisions"] if row["revision"] == 1)
        return {
            "stateRevision": expl["revision"],
            "stateInventory": expl.get("inventory"),
            "stateOmitted": expl.get("inventoryOmitted"),
            "revisionsInventory": rev1.get("inventory"),
            "revisionsOmitted": rev1.get("inventoryOmitted"),
        }

    def _observe_before_record(*args: Any, **kwargs: Any) -> Any:
        assert reopen_frame(scope).state == "ready"
        before_reads = _physical_task_bytes(world.task_root)
        live = _read_routes()
        observed.update(
            {
                **live,
                "beforeReads": before_reads,
                "afterReads": _physical_task_bytes(world.task_root),
            }
        )
        return original_record(*args, **kwargs)

    monkeypatch.setattr(explainer_server, "FrameLimits", lambda: FrameLimits(task_bytes=cap))
    monkeypatch.setattr(explainer_server, "_record_published_inventory", _observe_before_record)
    answer = _call(_server(world, now=_make_budget_clock()), "render")

    assert answer["publication"]["state"] == "published", answer
    assert answer["inventoryOmitted"] == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    assert observed["stateRevision"] == 1
    assert observed["stateInventory"] is None and observed["revisionsInventory"] is None
    assert (
        observed["stateOmitted"]
        == observed["revisionsOmitted"]
        == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    )
    assert observed["beforeReads"] == observed["afterReads"] == cap
    after_publish = _physical_task_bytes(world.task_root)
    assert after_publish == cap and task_frames_total(world.task_root, LEAF)["bytes"] == cap

    # A refused render on an already capped task with a legacy workspace counter must not migrate
    # that counter into the task frame store or add any physical bytes.
    full_world = _world(tmp_path / "case-refused")
    _write_claimed_page(full_world.workspace / "drafts" / "assets", "<p>Too big</p>")
    before_refusal = _physical_task_bytes(full_world.task_root)
    monkeypatch.setattr(
        explainer_server, "FrameLimits", lambda: FrameLimits(task_bytes=before_refusal)
    )
    monkeypatch.setattr(explainer_server, "_record_published_inventory", original_record)
    refused = _call(_server(full_world, now=_make_budget_clock()), "render")
    assert refused["publication"]["state"] == "refused"
    assert refused["publication"]["refusal"] == "frame-store-full"
    assert _physical_task_bytes(full_world.task_root) == before_refusal


_BROWSER_BEHAVIOR_PROBE = r"""
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(process.argv[2]);
const pwUrl = process.env.AR_PLAYWRIGHT ? pathToFileURL(process.env.AR_PLAYWRIGHT).href : "playwright";
const MEDIA = { ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".txt": "text/plain", ".md": "text/markdown" };

const server = createServer(async (req, res) => {
  const target = resolve(join(root, decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname).replace(/^\/+/, "")));
  if (!target.startsWith(root + sep)) return void res.writeHead(404).end();
  try {
    if (!(await stat(target)).isFile()) throw new Error("not file");
    res.writeHead(200, { "Content-Type": MEDIA[extname(target).toLowerCase()] || "application/octet-stream" });
    res.end(await readFile(target));
  } catch { res.writeHead(404).end(); }
});
const visibleRatio = (page, sel) => page.evaluate((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.height > 0 ? Math.max(0, Math.min(window.innerHeight, r.bottom) - Math.max(0, r.top)) / r.height : 0; }, sel);
const assertMorphStill = async (page) => {
  const info = await page.evaluate(() => {
    const still = document.querySelector(".still-snippets"), before = still?.querySelector('.still-block[data-state="before"]'), after = still?.querySelector('.still-block[data-state="after"]'), stage = document.getElementById("morph-stage");
    if (!still || !before || !after || !stage) return { ok: false };
    const b = before.getBoundingClientRect(), a = after.getBoundingClientRect();
    return { ok: true, stillHidden: still.hidden, stageHidden: stage.hidden, beforeVis: b.width > 0 && b.height > 0, afterVis: a.width > 0 && a.height > 0, stacked: b.top < a.top && b.bottom <= a.top + 1 };
  });
  assert.deepEqual(info, { ok: true, stillHidden: false, stageHidden: true, beforeVis: true, afterVis: true, stacked: true });
};
const withPage = async (browser, opts, fn) => { const ctx = await browser.newContext(opts); try { const page = await ctx.newPage(); await fn(page); } finally { await ctx.close(); } };

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const origin = `http://127.0.0.1:${server.address().port}`;
const { chromium } = await import(pwUrl).then((m) => (m.chromium ? m : m.default));
const browser = await chromium.launch({ args: ["--no-sandbox"] });
try {
  // Code morph still form before JS runs, and live <shiki-magic-move> switching when JS runs.
  for (const slug of ["shiki-magic-move", "exemplar"]) {
    const url = `${origin}/${slug}/index.html`;
    await withPage(browser, { javaScriptEnabled: false, viewport: { width: 1280, height: 800 } }, async (p) => { await p.goto(url, { waitUntil: "load" }); await assertMorphStill(p); });
    await withPage(browser, { viewport: { width: 1280, height: 800 } }, async (p) => {
      await p.goto(url, { waitUntil: "load" });
      await p.waitForSelector("#morph-stage shiki-magic-move");
      assert.equal(await p.locator("#morph-stage").isHidden(), false);
      assert.equal(await p.locator(".still-snippets").isHidden(), true);
      await p.locator('#fig-code-morph button[data-state="before"]').click();
      assert.equal(await p.locator('#fig-code-morph button[data-state="before"]').getAttribute("aria-pressed"), "true");
      assert.ok((await p.locator("#morph-status").textContent()).includes("Before"));
      await p.locator('#fig-code-morph button[data-state="after"]').click();
      assert.equal(await p.locator('#fig-code-morph button[data-state="after"]').getAttribute("aria-pressed"), "true");
      assert.ok((await p.locator("#morph-status").textContent()).includes("After"));
      // A stray binding message with a legacy effects flag must never set data-effects.
      await p.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: { schema: "ar-canvas-binding/v1", protocolVersion: 1, session: "probe", revision: 0, comparison: "", effects: false }, source: window.parent })));
      await p.waitForTimeout(100);
      assert.equal(await p.evaluate(() => "effects" in document.documentElement.dataset), false);
    });
  }
  // Step-through Back/Next via Space without toggling Play/Pause.
  for (const cfg of [{ slug: "motion-canvas-step-through", back: "#btn-back", play: "#btn-play", next: "#btn-next", count: "#step-count" }, { slug: "exemplar", back: "#mc-back", play: "#mc-play", next: "#mc-next", count: "#mc-count" }]) {
    await withPage(browser, { viewport: { width: 1280, height: 900 } }, async (p) => {
      await p.goto(`${origin}/${cfg.slug}/index.html`, { waitUntil: "load" });
      await p.waitForTimeout(200);
      await p.locator("#fig-step-through").focus();
      await p.keyboard.press("Home");
      await p.keyboard.press("ArrowRight");
      await p.waitForTimeout(150);
      assert.equal((await p.locator(cfg.count).textContent()).trim(), "2 / 4");
      assert.equal(await p.locator(cfg.play).getAttribute("aria-pressed"), "false");
      await p.locator(cfg.back).focus();
      await p.keyboard.press("Space");
      await p.waitForTimeout(150);
      assert.equal((await p.locator(cfg.count).textContent()).trim(), "1 / 4");
      assert.equal(await p.locator(cfg.play).getAttribute("aria-pressed"), "false");
      await p.locator(cfg.next).focus();
      await p.keyboard.press("Space");
      await p.waitForTimeout(150);
      assert.equal((await p.locator(cfg.count).textContent()).trim(), "2 / 4");
      assert.equal(await p.locator(cfg.play).getAttribute("aria-pressed"), "false");
    });
  }
  // Trace pin keyboard focus, Clear pin focus restoration, and >=44px targets at 560px/390px.
  for (const cfg of [{ slug: "open-generative-ui-trace", clear: "#btn-clear-pin" }, { slug: "exemplar", clear: "#trace-clear" }]) {
    await withPage(browser, { viewport: { width: 1280, height: 800 } }, async (p) => {
      await p.goto(`${origin}/${cfg.slug}/index.html`, { waitUntil: "load" });
      assert.ok(await p.evaluate(() => [...document.querySelectorAll("#fig-trace-path [data-link]")].every((el) => el.tagName === "BUTTON" || el.tabIndex >= 0)));
      const firstLine = p.locator("#fig-trace-path .code-line[data-link]").first();
      await firstLine.focus();
      await p.keyboard.press("Enter");
      assert.equal(await firstLine.getAttribute("aria-pressed"), "true");
      assert.equal(await p.locator(cfg.clear).isHidden(), false);
      await p.locator(cfg.clear).focus();
      await p.keyboard.press("Enter");
      assert.equal(await p.locator(cfg.clear).isHidden(), true);
      assert.ok(await p.evaluate(() => { const fig = document.getElementById("fig-trace-path"), el = document.activeElement, r = el?.getBoundingClientRect(); return Boolean(el && el !== document.body && fig.contains(el) && r.width > 0 && r.height > 0 && el.matches(".code-line[data-link]")); }));
      for (const width of [560, 390]) {
        await p.setViewportSize({ width, height: 800 });
        await firstLine.click();
        const layout = await p.evaluate((clearSel) => {
          const targets = [...document.querySelectorAll("#fig-trace-path .trace-svg .node[data-link], #fig-trace-path .hop-item[data-link], #fig-trace-path .code-line[data-link]"), document.querySelector(clearSel)].filter((el) => el && !el.hidden);
          return { overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth, minHeight: Math.min(...targets.map((el) => el.getBoundingClientRect().height)), count: targets.length };
        }, cfg.clear);
        assert.equal(layout.overflow, false, `${cfg.slug} overflow at ${width}px`);
        assert.ok(layout.count > 0 && layout.minHeight >= 44, `${cfg.slug} min height ${layout.minHeight} at ${width}px`);
        await p.locator(cfg.clear).click();
      }
    });
  }
  // 60% intersection autoplay gate and bounded playback for motion-canvas and ar-video starters.
  const mcUrl = `${origin}/motion-canvas-step-through/index.html`;
  await withPage(browser, { viewport: { width: 1280, height: 300 } }, async (p) => {
    await p.goto(mcUrl, { waitUntil: "load" });
    await p.waitForTimeout(300);
    const low = await visibleRatio(p, "#fig-step-through");
    assert.ok(low > 0 && low < 0.6, `motion-canvas low ratio ${low}`);
    assert.equal(await p.locator("#btn-play").getAttribute("aria-pressed"), "false");
    await p.setViewportSize({ width: 1280, height: 1000 });
    await p.locator("#fig-step-through").scrollIntoViewIfNeeded();
    await p.waitForTimeout(300);
    assert.ok((await visibleRatio(p, "#fig-step-through")) >= 0.6);
    assert.equal(await p.locator("#btn-play").getAttribute("aria-pressed"), "true");
  });
  const avUrl = `${origin}/ar-video-measured/index.html`;
  const rafHook = () => { window.__probeRafCount = 0; const orig = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = (cb) => { window.__probeRafCount += 1; return orig(cb); }; };
  await withPage(browser, { viewport: { width: 1280, height: 300 } }, async (p) => {
    await p.addInitScript(rafHook);
    await p.goto(avUrl, { waitUntil: "load" });
    await p.waitForTimeout(300);
    const low = await visibleRatio(p, "#fig-measured-outcome");
    assert.ok(low > 0 && low < 0.6, `ar-video low ratio ${low}`);
    assert.equal(await p.evaluate(() => window.__probeRafCount), 0);
    await p.setViewportSize({ width: 1280, height: 1000 });
    await p.locator("#fig-measured-outcome").scrollIntoViewIfNeeded();
    await p.waitForTimeout(350);
    assert.ok((await visibleRatio(p, "#fig-measured-outcome")) >= 0.6);
    assert.ok((await p.evaluate(() => window.__probeRafCount)) >= 2);
  });
  // Exemplar Zero-jitter readout and consistent default numbers across essay, step-through, and sandbox.
  await withPage(browser, { viewport: { width: 1280, height: 900 } }, async (p) => {
    await p.goto(`${origin}/exemplar/index.html`, { waitUntil: "load" });
    await p.waitForTimeout(150);
    const essay = await p.locator('.part[data-part="sequence"] .prose').textContent();
    const stepStatus = await p.locator("#mc-status").textContent();
    const step4 = await p.locator('#mc-step-list .step-item[data-step="4"]').textContent();
    const worked = await p.locator("#fig-sandbox .worked-cases").textContent();
    assert.ok(essay.includes("11.5 s") && essay.includes("18.5 s"));
    assert.ok(stepStatus.includes("11.5 s") && step4.includes("11.5 s") && step4.includes("18.5 s"));
    assert.equal((await p.locator("#lbl-sampled").textContent()).trim(), "Sampled delay (r = 0.48)");
    assert.equal((await p.locator("#val-sampled").textContent()).trim(), "11.5 s");
    assert.ok(worked.includes("11.5 s") && worked.includes("18.5 s"));
    assert.equal((await p.locator("body").textContent()).includes("11.4 s"), false);
    await p.locator('#fig-sandbox [data-preset="zero"]').click();
    assert.equal((await p.locator("#lbl-sampled").textContent()).trim(), "Sampled delay (r = 0.00)");
    assert.equal((await p.locator("#val-sampled").textContent()).trim(), "0.0 s");
    const branchZero = await p.locator("#sb-branch-text").textContent();
    const statusZero = await p.locator("#sb-status").textContent();
    assert.ok(branchZero.includes("r = 0.00") && !branchZero.includes("r = 0.48"));
    assert.ok(statusZero.includes("r = 0.00") && !statusZero.includes("r = 0.48"));
    await p.locator("#in-attempt").focus();
    await p.keyboard.press("ArrowRight");
    assert.equal(await p.locator('#fig-sandbox [data-preset="zero"]').getAttribute("aria-selected"), "true");
    assert.equal((await p.locator("#out-attempt").textContent()).trim(), "5");
    assert.equal((await p.locator("#val-raw").textContent()).trim(), "32.0 s");
    assert.equal((await p.locator("#lbl-sampled").textContent()).trim(), "Sampled delay (r = 0.00)");
    assert.equal((await p.locator("#val-sampled").textContent()).trim(), "0.0 s");
    const branchZeroAfter = await p.locator("#sb-branch-text").textContent();
    const statusZeroAfter = await p.locator("#sb-status").textContent();
    assert.ok(branchZeroAfter.includes("r = 0.00") && branchZeroAfter.includes("0.0 s") && !branchZeroAfter.includes("r = 0.48"));
    assert.ok(statusZeroAfter.includes("r = 0.00") && statusZeroAfter.includes("0.0 s") && !statusZeroAfter.includes("r = 0.48"));
    await p.locator('#fig-sandbox [data-preset="under"]').click();
    assert.equal(await p.locator('#fig-sandbox [data-preset="under"]').getAttribute("aria-selected"), "true");
    assert.equal((await p.locator('#fig-sandbox [data-preset="under"]').textContent()).trim(), "Under 80% budget");
    await p.locator("#in-attempt").focus();
    for (let step = 0; step < 3; step += 1) await p.keyboard.press("ArrowRight");
    assert.equal(await p.locator('#fig-sandbox [data-preset="exceeds"]').getAttribute("aria-selected"), "true");
    assert.equal((await p.locator('#fig-sandbox [data-preset="exceeds"]').textContent()).trim(), "Clamped to 80% cap");
    assert.equal(await p.locator('#fig-sandbox [data-preset="under"]').getAttribute("aria-selected"), "false");
    const branchExceeds = await p.locator("#sb-branch-text").textContent();
    assert.ok(branchExceeds.includes("Clamped to 80% of lease") && branchExceeds.includes("r = 0.48"));
    // Between-budget-and-lease band: from Clamped preset (attempt 5, base 4.0 s, lease 30 s),
    // lower Attempt to 4 and raise Lease ceiling to 35 s -> raw 32.0 s > 28.0 s budget while <= 35 s lease.
    await p.locator("#sb-reset").click();
    await p.locator("#in-attempt").focus();
    await p.keyboard.press("ArrowLeft");
    await p.locator("#in-lease").focus();
    await p.keyboard.press("ArrowRight");
    assert.equal((await p.locator("#out-attempt").textContent()).trim(), "4");
    assert.equal((await p.locator("#out-lease").textContent()).trim(), "35 s");
    assert.equal((await p.locator("#val-raw").textContent()).trim(), "32.0 s");
    assert.equal((await p.locator("#val-budget").textContent()).trim(), "28.0 s");
    assert.equal((await p.locator("#val-cap").textContent()).trim(), "28.0 s");
    assert.equal(await p.locator('#fig-sandbox [data-preset="exceeds"]').getAttribute("aria-selected"), "true");
    assert.equal((await p.locator('#fig-sandbox [data-preset="exceeds"]').textContent()).trim(), "Clamped to 80% cap");
    assert.equal(await p.locator("#val-raw").evaluate((el) => el.classList.contains("metric-val--gone")), false);
    const branchBetween = await p.locator("#sb-branch-text").textContent();
    assert.ok(branchBetween.includes("32.0 s raw exceeds 28.0 s budget while within 35 s lease"), branchBetween);
    await p.locator("#in-attempt").focus();
    await p.keyboard.press("ArrowLeft");
    assert.equal(await p.locator('#fig-sandbox [data-preset="under"]').getAttribute("aria-selected"), "true");
    assert.equal((await p.locator('#fig-sandbox [data-preset="under"]').textContent()).trim(), "Under 80% budget");
  });
  console.log(JSON.stringify({ ok: true }));
} finally {
  await browser.close();
  server.close();
}
"""


class _ReadOnlyExplainerHost:
    """Minimal read-only ExplainerHost for serving saved revisions without starting a seat."""

    def create_seat(
        self,
        *,
        agent_id: str,
        workspace: Path,
        title: str,
        capsule: str,
        labels: Mapping[str, str],
    ) -> None:
        del agent_id, workspace, title, capsule, labels
        raise AssertionError("read-only route must not create a seat")

    def resume(self, agent_id: str) -> None:
        del agent_id
        raise AssertionError("read-only route must not resume a seat")

    def send(self, agent_id: str, text: str, *, message_id: str | None = None) -> None:
        del agent_id, text, message_id
        raise AssertionError("read-only route must not send a message")

    def archive(self, agent_id: str) -> None:
        del agent_id
        raise AssertionError("read-only route must not archive a seat")

    def seat_state(self, agent_id: str) -> str:
        del agent_id
        return "missing"

    def in_turn(self, agent_id: str) -> bool:
        del agent_id
        return False

    def cancel(self, agent_id: str, *, request_id: str) -> dict[str, Any] | None:
        del agent_id, request_id
        return None


def _build_explainer_dashboard_app(world: World) -> FastAPI:
    settings = replace(
        config(world.root),
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO,
                path=world.code,
                memory_root=world.memory,
            )
        },
    )

    def _trees(_query: Any) -> Any:
        return trees_result(world.record)

    def _unused_prepare(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("read-only route must not prepare a comparison")

    app = FastAPI()
    register_canvas_routes(app, canvas_hosts(settings, review_trees=_trees))
    register_explainer_routes(
        app,
        explainer_composition(
            settings,
            _trees,
            wiring=ExplainerWiring(
                host=_ReadOnlyExplainerHost(),
                application=explainer_application_ports(),
                comparison_prepare=_unused_prepare,
            ),
        ),
    )
    app.add_middleware(RefuseCrossOriginWrites)
    return app


def test_shipped_starters_and_exemplar_browser_behaviors(tmp_path: Path) -> None:
    probe_root = tmp_path / "probe-pages"
    pages = {slug: STARTERS_ROOT / slug for slug in STARTER_SLUGS}
    pages["exemplar"] = EXEMPLAR_ROOT
    for slug, folder in pages.items():
        merged, carry = carry_kit_assets(_folder_assets(folder))
        assert not carry.unknown_kit_paths and not carry.kit_problems
        for rel, payload in merged.items():
            target = probe_root / slug / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    script = tmp_path / "probe.mjs"
    script.write_text(_BROWSER_BEHAVIOR_PROBE, encoding="utf-8")
    env = {**os.environ, **browser_environment()}
    completed = subprocess.run(
        [node_executable(), str(script), str(probe_root)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, f"stdout={completed.stdout}\nstderr={completed.stderr}"
    assert json.loads(completed.stdout) == {"ok": True}
