"""Tests for kit reference extraction, kit verification, inventory builder, and workspace copy."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import review_frame_inventory
from agents_remember.application import review_frame_store as store
from agents_remember.application.review_frame_inventory import (
    INVENTORY_NAME,
    INVENTORY_OMITTED_STORAGE_LIMIT_NAME,
    INVENTORY_OMITTED_STORAGE_LIMIT_REASON,
    persist_revision_inventory,
    persist_revision_inventory_within_limit,
    revision_inventory,
    revision_inventory_omitted_reason,
)
from agents_remember.application.review_frame_usage import revision_usage_bytes
from agents_remember.application.review_planned_comparison import task_frames_total
from agents_remember.canvas import explainer_server, explainer_workspace
from agents_remember.canvas.explainer_paths import (
    EXEMPLAR_DIRECTORY,
    KIT_DIRECTORY,
    STARTERS_DIRECTORY,
    copy_folder,
)
from agents_remember.canvas.explainer_workspace import (
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    build_environment,
    update_environment,
)
from agents_remember.canvas.inventory import (
    build_inventory,
    inventory_summary,
    kit_libraries_used,
    normalize_technique,
    unused_draft_assets,
)
from agents_remember.canvas.kit import KIT_ROOT, kit_assets
from agents_remember.canvas.kit_references import (
    carry_kit_assets,
    extract_css_references,
    extract_html_references,
    extract_js_references,
    extract_references,
    resolve_kit_references,
    resolve_reference,
    verify_kit_assets,
)
from agents_remember.canvas.readiness import check_page
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameBinding, FrameLimits
from agents_remember.tasks.task_paths import slugify
from canvas_host_test_support import LEAF, TASK, config, packet, record


def test_js_extractor_handles_static_reexport_and_dynamic_imports_only() -> None:
    source = """
    import { createHighlighter } from './kit/shiki/4.5.0/shiki-kit.js';
    import './side-effect.js';
    export { helper } from './reexport.mjs';
    export * from './all.js';
    const loaded = await import('./dynamic.js');
    const tpl = import(`./template.js`);
    const interp = import(`./dynamic-${name}.js`);
    const fake1 = "import x from './in-string.js'";
    const fake2 = `import('./in-template.js')`;
    // import y from './in-line-comment.js';
    /* import z from './in-block-comment.js'; */
    """
    assert extract_js_references(source) == (
        "./kit/shiki/4.5.0/shiki-kit.js",
        "./side-effect.js",
        "./reexport.mjs",
        "./all.js",
        "./dynamic.js",
        "./template.js",
    )


def test_css_and_html_extractors_handle_imports_inline_blocks_and_comments() -> None:
    css = """
    /* @import "ignored.css"; background: url(ignored.png); */
    @import "kit/shiki/4.5.0/magic-move.css";
    @import 'theme.css' screen;
    @import url("extra.css");
    @import url(bare.css);
    .hero { background-image: url('./img/bg.svg#layer'); }
    """
    assert extract_css_references(css) == (
        "kit/shiki/4.5.0/magic-move.css",
        "theme.css",
        "extra.css",
        "bare.css",
        "./img/bg.svg#layer",
    )

    html = """<!doctype html>
    <html><head>
      <!-- <link rel="stylesheet" href="commented.css"> -->
      <link rel="stylesheet" href="app.css">
      <style>@import "inline.css"; body{background:url(bg.png)}</style>
      <script type="application/json">{"hint": "import './not-js.js'"}</script>
      <script type="module">
        import { col } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';
        const sample = "import './not-imported.js'";
      </script>
    </head><body>
      <img srcset="a.png 1x, b.png 2x" src="fallback.png">
      <video controls poster="poster.png" src="clip.webm"></video>
      <div style="background:url(inline-attr.svg)"></div>
    </body></html>"""
    assert extract_html_references(html) == (
        "app.css",
        "inline.css",
        "bg.png",
        "./kit/ar-video/23fd71bf/ar-video-vocabulary.js",
        "a.png",
        "b.png",
        "fallback.png",
        "poster.png",
        "clip.webm",
        "inline-attr.svg",
    )


def test_extract_references_skips_kit_files_and_resolves_relative_paths() -> None:
    assert (
        extract_references(
            "kit/shiki/4.5.0/shiki-kit.js",
            b"import './anything.js';",
        )
        == ()
    )
    assert resolve_reference("sub/app.js", "../kit/shiki/4.5.0/shiki-kit.js?v=1#top") == (
        "kit/shiki/4.5.0/shiki-kit.js"
    )
    assert resolve_reference("index.html", "#section") is None
    assert resolve_reference("index.html", "?tab=1") is None
    assert resolve_reference("index.html", "data:image/png;base64,AA==") is None
    assert resolve_reference("index.html", "../outside.js") is None
    assert resolve_reference("index.html", "/etc/passwd") is None
    assert resolve_reference("index.html", "https://cdn.example/x.js") is None


def test_resolve_and_carry_kit_assets_includes_notices_and_handles_unknown_cleanly() -> None:
    draft = {
        "index.html": (
            b"<!doctype html><link rel='stylesheet' href='app.css'>"
            b"<script type='module' src='app.js'></script><p>hi</p>"
        ),
        "app.css": b'@import "kit/shiki/4.5.0/magic-move.css";',
        "app.js": (
            b"import { createHighlighter } from './kit/shiki/4.5.0/shiki-kit.js';\n"
            b"import { col } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';\n"
        ),
    }
    resolved_direct = resolve_kit_references(draft)
    assert resolved_direct.unknown_kit_paths == ()
    assert "kit/shiki/4.5.0/shiki-kit.js" in resolved_direct.carried

    merged, result = carry_kit_assets(draft)
    assert result.unknown_kit_paths == ()
    assert result.kit_problems == ()
    assert set(result.referenced_known) == {
        "ar-video/23fd71bf/ar-video-vocabulary.js",
        "shiki/4.5.0/magic-move.css",
        "shiki/4.5.0/shiki-kit.js",
    }
    assert "kit/shiki/4.5.0/LICENSE" in merged
    assert "kit/shiki/4.5.0/LICENSES/@shikijs__core.txt" in merged
    assert "kit/ar-video/23fd71bf/VOCABULARY.md" in merged
    assert "kit/ar-video/23fd71bf/PROVENANCE.md" in merged

    # magic-move.css passes readiness without any prefers-reduced-motion rule.
    report = check_page(merged, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()

    # Unknown kit paths never raise KitRefused and fail admitted-assets cleanly.
    unknown_draft = {
        "index.html": b"<!doctype html><script type='module' src='app.js'></script>",
        "app.js": b"import x from './kit/shiki/4.5.0/does-not-exist.js';",
    }
    merged_unknown, unknown_result = carry_kit_assets(unknown_draft)
    assert unknown_result.unknown_kit_paths == ("kit/shiki/4.5.0/does-not-exist.js",)
    bad_report = check_page(merged_unknown, entrypoint="index.html", packet=packet(record()))
    assert not bad_report.ready
    failed = {check.name: check.detail for check in bad_report.failures}
    assert "admitted-assets" in failed
    assert "does-not-exist.js" in failed["admitted-assets"]


def test_modified_or_custom_kit_file_in_draft_is_refused_with_outside_kit_remedy() -> None:
    tampered = {
        "index.html": (
            b"<!doctype html><link rel='stylesheet' href='kit/shiki/4.5.0/magic-move.css'>"
        ),
        "kit/shiki/4.5.0/magic-move.css": b".shiki-magic-move-container { color: red; }",
    }
    problems = verify_kit_assets(tampered)
    assert len(problems) == 1
    assert "outside 'kit/'" in problems[0]

    # carry_kit_assets must not mask tampered draft kit bytes with shipped bytes.
    merged, result = carry_kit_assets(tampered)
    assert result.kit_problems
    report = check_page(merged, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    failed = {check.name: check.detail for check in report.failures}
    assert "admitted-assets" in failed
    assert "outside 'kit/'" in failed["admitted-assets"]

    custom = {
        "index.html": b"<!doctype html><script src='kit/custom/1.0.0/mod.js'></script>",
        "kit/custom/1.0.0/mod.js": b"export const x = 1;",
    }
    custom_report = check_page(custom, entrypoint="index.html", packet=packet(record()))
    assert not custom_report.ready
    assert "outside 'kit/'" in {c.name: c.detail for c in custom_report.failures}["admitted-assets"]

    # Unmodified kit bytes in draft pass verification.
    exact = {
        "index.html": (
            b"<!doctype html><link rel='stylesheet' href='kit/shiki/4.5.0/magic-move.css'>"
        ),
        **kit_assets(["shiki/4.5.0/magic-move.css"]),
    }
    assert verify_kit_assets(exact) == ()
    assert check_page(exact, entrypoint="index.html", packet=packet(record())).ready


def test_readiness_checks_css_imports_and_non_kit_js_imports() -> None:
    bad_css = {
        "index.html": b"<!doctype html><link rel='stylesheet' href='app.css'>",
        "app.css": b'@import "missing.css";',
    }
    css_report = check_page(bad_css, entrypoint="index.html", packet=packet(record()))
    assert not css_report.ready
    assert "missing.css" in {c.name: c.detail for c in css_report.failures}["admitted-assets"]

    bad_js = {
        "index.html": b"<!doctype html><script type='module' src='app.js'></script>",
        "app.js": b"import './missing.js';",
    }
    js_report = check_page(bad_js, entrypoint="index.html", packet=packet(record()))
    assert not js_report.ready
    assert "missing.js" in {c.name: c.detail for c in js_report.failures}["admitted-assets"]

    remote_js = {
        "index.html": b"<!doctype html><script type='module' src='app.js'></script>",
        "app.js": b"import 'https://cdn.example/mod.js';",
    }
    remote_report = check_page(remote_js, entrypoint="index.html", packet=packet(record()))
    assert not remote_report.ready
    assert (
        "outside its own admitted assets"
        in {c.name: c.detail for c in remote_report.failures}["admitted-assets"]
    )

    # A valid relative JS import chain passes cleanly.
    ok_js = {
        "index.html": b"<!doctype html><script type='module' src='app.js'></script>",
        "app.js": b"import { x } from './lib/helper.js'; console.log(x);",
        "lib/helper.js": b"export const x = 42;",
    }
    assert check_page(ok_js, entrypoint="index.html", packet=packet(record())).ready


def test_inventory_helpers_and_summary_formatting() -> None:
    assert normalize_technique("  Code   Morph \t ") == "code-morph"
    assert normalize_technique("Step-Through") == "step-through"
    assert normalize_technique("   ") == ""

    draft = {
        "index.html": (
            b"<!doctype html><link rel='stylesheet' href='app.css'>"
            b"<script type='module' src='app.js'></script>"
            b"<figure data-technique=' Code Morph '><svg></svg><button>Next</button></figure>"
            b"<figure data-technique='step-through'><canvas></canvas><input type='range'></figure>"
            b"<pre class='shiki'><span>code</span></pre><pre>plain</pre>"
        ),
        "app.css": b'@import "theme.css"; .a{background:url(used.svg)}',
        "theme.css": b"body{color:#fff}",
        "app.js": (
            b"import './helper.js';\n"
            b"import { col } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';\n"
        ),
        "helper.js": b"export const ok = true;",
        "used.svg": b"<svg/>",
        "orphan.png": b"\x89PNG",
        "claim-map.json": b"{}",
        "LICENSE": b"MIT",
        "LICENSES/dep.txt": b"MIT",
        "NOTICE.md": b"notice",
        "kit/ar-video/23fd71bf/VOCABULARY.md": (
            KIT_ROOT / "ar-video/23fd71bf/VOCABULARY.md"
        ).read_bytes(),
    }
    assert unused_draft_assets(draft) == ("orphan.png",)
    assert kit_libraries_used(
        draft,
        carried_paths=("kit/shiki/4.5.0/shiki-kit.js", "kit/shiki/4.5.0/LICENSE"),
    ) == ("ar-video",)
    assert kit_libraries_used(
        {},
        carried_paths=("kit/shiki/4.5.0/shiki-kit.js", "kit/shiki/4.5.0/LICENSE"),
    ) == ("shiki",)

    static_inv = build_inventory(
        draft,
        observed_assets=("kit/shiki/4.5.0/shiki-kit.js",),
    )
    assert static_inv == {
        "figures": {
            "total": 2,
            "byKind": {"svg": 1, "canvas": 1, "driven": 2},
            "byTechnique": {"code-morph": 1, "step-through": 1},
        },
        "kitLibraries": ["ar-video", "shiki"],
        "codeExcerpts": {"highlighted": 1, "plain": 1},
        "unusedAssets": ["orphan.png"],
    }
    assert inventory_summary(static_inv) == (
        "2 figures (1 SVG, 1 canvas, 2 driven; code-morph, step-through) · "
        "kit: ar-video, shiki · code: 1 highlighted, 1 plain · 1 unused asset"
    )

    dom_inv = build_inventory(
        {"index.html": b"<!doctype html><p>x</p>"},
        dom_inventory={
            "figures": {
                "total": 4,
                "byKind": {"svg": 2, "canvas": 1, "driven": 4},
                "byTechnique": {"Code Morph": 1, " step  through ": 1},
            },
            "codeExcerpts": {"highlighted": 3, "plain": 0},
        },
        observed_assets=(
            "kit/ar-video/23fd71bf/ar-video-vocabulary.js",
            "kit/shiki/4.5.0/shiki-kit.js",
        ),
    )
    assert "incomplete" not in dom_inv
    assert inventory_summary(dom_inv) == (
        "4 figures (2 SVG, 1 canvas, 4 driven; code-morph, step-through) · "
        "kit: ar-video, shiki · code: 3 highlighted"
    )

    empty_like = {
        "figures": {"total": 0, "byKind": {"svg": 0, "canvas": 0, "driven": 0}, "byTechnique": {}},
        "kitLibraries": [],
        "codeExcerpts": {"highlighted": 0, "plain": 4},
        "unusedAssets": [f"u{i}.png" for i in range(9)],
    }
    assert (
        inventory_summary(empty_like) == "0 figures · kit: none · code: 4 plain · 9 unused assets"
    )

    no_code = {
        "figures": {"total": 1, "byKind": {"svg": 1, "canvas": 0, "driven": 0}, "byTechnique": {}},
        "kitLibraries": [],
        "codeExcerpts": {"highlighted": 0, "plain": 0},
        "unusedAssets": [],
    }
    assert inventory_summary(no_code) == "1 figure (1 SVG) · kit: none"
    assert inventory_summary(None) is None
    assert inventory_summary({}) is None


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _make_repo(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    for rel, text in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def test_workspace_copies_starters_and_exemplar_and_skips_kit_when_seeding_draft(
    tmp_path: Path,
) -> None:
    code = _make_repo(tmp_path, "code", {"src/app.py": "x = 1\n"})
    memory = _make_repo(tmp_path, "memory", {"m.md": "m\n"})
    runtime = config(tmp_path)
    task_dir = runtime.coordination_root / "tasks" / "agents-remember" / TASK
    rev_assets = (
        task_dir
        / "notes"
        / "reports"
        / "review-frames"
        / slugify(LEAF)
        / "explainer"
        / "revisions"
        / "1"
        / "assets"
    )
    rev_assets.mkdir(parents=True)
    (rev_assets / "index.html").write_text("<!doctype html><p>v1</p>", encoding="utf-8")
    (rev_assets / KIT_DIRECTORY / "shiki" / "4.5.0").mkdir(parents=True)
    (rev_assets / KIT_DIRECTORY / "shiki" / "4.5.0" / "magic-move.css").write_text(
        "/* carried */", encoding="utf-8"
    )

    starters_src = tmp_path / "pkg-starters"
    (starters_src / "basic").mkdir(parents=True)
    (starters_src / "basic" / "index.html").write_text("starter-v1", encoding="utf-8")
    (starters_src / "stale.txt").write_text("will-go-away", encoding="utf-8")

    exemplar_src = tmp_path / "pkg-exemplar"
    exemplar_src.mkdir(parents=True)
    (exemplar_src / "index.html").write_text("exemplar-v1", encoding="utf-8")

    def _inputs(number: int) -> EnvironmentInputs:
        comparison = record(number)
        binding = FrameBinding.from_packet(packet(comparison).comparison).model_copy(
            update={
                "code_base_tree": _git(code, "rev-parse", "HEAD^{tree}"),
                "code_candidate_tree": _git(code, "rev-parse", "HEAD^{tree}"),
                "memory_base_tree": _git(memory, "rev-parse", "HEAD^{tree}"),
                "memory_candidate_tree": _git(memory, "rev-parse", "HEAD^{tree}"),
                "compared_memory_base_tree": _git(memory, "rev-parse", "HEAD^{tree}"),
            }
        )
        return EnvironmentInputs(
            binding=binding,
            packet=packet(comparison),
            capsule="capsule\n",
            code_repository=code,
            memory_repository=memory,
            record_digest="sha256:" + "0" * 64,
        )

    doc_events: list[dict[str, object]] = []

    def _progress(step: str, facts: object) -> None:
        if step == "documents" and isinstance(facts, dict):
            doc_events.append(dict(facts))

    ref = EnvironmentRef(config=runtime, repository_id="agents-remember", task=TASK, leaf_id=LEAF)
    with (
        mock.patch.object(explainer_workspace, "STARTERS_ROOT", starters_src),
        mock.patch.object(explainer_workspace, "EXEMPLAR_ROOT", exemplar_src),
    ):
        build = build_environment(
            EnvironmentRun(
                ref=ref,
                inputs=_inputs(7),
                now="2026-10-22T08:00:00+00:00",
                progress=_progress,
            )
        )
        assert (build.workspace / STARTERS_DIRECTORY / "basic" / "index.html").read_text() == (
            "starter-v1"
        )
        assert (build.workspace / EXEMPLAR_DIRECTORY / "index.html").read_text() == "exemplar-v1"
        assert doc_events[-1]["starters"] == 2
        assert doc_events[-1]["exemplar"] == 1
        # Draft seeded from revision 1 skips carried kit/ files.
        assert (build.workspace / "drafts" / "assets" / "index.html").read_text() == (
            "<!doctype html><p>v1</p>"
        )
        assert not (build.workspace / "drafts" / "assets" / KIT_DIRECTORY).exists()
        # Revision 1 copy still holds the carried kit file.
        assert (
            build.workspace
            / "revisions"
            / "1"
            / "assets"
            / KIT_DIRECTORY
            / "shiki"
            / "4.5.0"
            / "magic-move.css"
        ).is_file()

        # On Update, stale starter/exemplar files are removed and updated files refreshed.
        (starters_src / "stale.txt").unlink()
        (starters_src / "basic" / "index.html").write_text("starter-v2", encoding="utf-8")
        (exemplar_src / "index.html").write_text("exemplar-v2", encoding="utf-8")
        update_environment(
            EnvironmentRun(
                ref=ref,
                inputs=_inputs(8),
                now="2026-10-22T09:00:00+00:00",
                previous=build.binding,
                progress=_progress,
            )
        )
        assert (build.workspace / STARTERS_DIRECTORY / "basic" / "index.html").read_text() == (
            "starter-v2"
        )
        assert not (build.workspace / STARTERS_DIRECTORY / "stale.txt").exists()
        assert (build.workspace / EXEMPLAR_DIRECTORY / "index.html").read_text() == "exemplar-v2"
        assert doc_events[-1]["starters"] == 1
        assert doc_events[-1]["exemplar"] == 1


def test_copy_folder_skip_top_level_only_skips_named_root_dir(tmp_path: Path) -> None:
    src_dir = tmp_path / "copy-src"
    (src_dir / "kit" / "a").mkdir(parents=True)
    (src_dir / "kit" / "a" / "bundle.js").write_text("k", encoding="utf-8")
    (src_dir / "img" / "kit").mkdir(parents=True)
    (src_dir / "img" / "kit" / "pic.svg").write_text("<svg/>", encoding="utf-8")
    dst_dir = tmp_path / "copy-dst"
    copied = copy_folder(src_dir, dst_dir, root=tmp_path, skip_top_level=(KIT_DIRECTORY,))
    assert copied == 1
    assert not (dst_dir / "kit").exists()
    assert (dst_dir / "img" / "kit" / "pic.svg").is_file()


def test_js_and_css_extractors_handle_commented_dynamic_imports_and_css_strings() -> None:
    js = """
    const mod = await import(/* leading comment */ './kit/shiki/4.5.0/shiki-kit.js');
    const second = import(
      // leading line comment
      './kit/ar-video/23fd71bf/ar-video-vocabulary.js'
    );
    """
    assert extract_js_references(js) == (
        "./kit/shiki/4.5.0/shiki-kit.js",
        "./kit/ar-video/23fd71bf/ar-video-vocabulary.js",
    )

    assert extract_css_references('@import"kit/shiki/4.5.0/magic-move.css";') == (
        "kit/shiki/4.5.0/magic-move.css",
    )
    assert (
        extract_css_references(
            ".box::after { content: \"@import 'not-a-dep.css'; url(not-a-dep.png)\"; }"
        )
        == ()
    )


def test_inventory_treats_observed_assets_as_used_and_unreferenced_kit_as_unused() -> None:
    unused_kit = "kit/ar-video/23fd71bf/ar-video-vocabulary.js"
    draft = {
        "index.html": b"<!doctype html><p>hi</p>",
        "plot.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        "orphan.txt": b"orphan",
        unused_kit: (KIT_ROOT / "ar-video/23fd71bf/ar-video-vocabulary.js").read_bytes(),
    }
    assert verify_kit_assets(draft) == ()
    assert unused_draft_assets(draft, observed_assets=("plot.svg",)) == (
        unused_kit,
        "orphan.txt",
    )
    assert kit_libraries_used(draft, observed_assets=("plot.svg",)) == ()

    inv = build_inventory(draft, observed_assets=("plot.svg",))
    assert "plot.svg" not in inv["unusedAssets"]
    assert inv["unusedAssets"] == [unused_kit, "orphan.txt"]
    assert "ar-video" not in inv["kitLibraries"]
    assert inv["kitLibraries"] == []


def test_persist_revision_inventory_returns_false_without_raising_on_unwritable_or_missing(
    tmp_path: Path,
) -> None:
    frame_dir = tmp_path / "explainer"
    sample = {
        "figures": {"total": 0, "byKind": {"svg": 0, "canvas": 0, "driven": 0}, "byTechnique": {}},
        "kitLibraries": [],
        "codeExcerpts": {"highlighted": 0, "plain": 0},
        "unusedAssets": [],
    }
    assert persist_revision_inventory(frame_dir, 1, sample) is False
    assert revision_inventory(frame_dir, 1) is None

    rev_dir = frame_dir / "revisions" / "1"
    rev_dir.mkdir(parents=True)
    with mock.patch.object(
        review_frame_inventory,
        "write_revision_inventory",
        side_effect=PermissionError("read-only revision directory"),
    ):
        assert persist_revision_inventory(frame_dir, 1, sample) is False
    assert not (rev_dir / INVENTORY_NAME).exists()

    assert persist_revision_inventory(frame_dir, 1, sample) is True
    assert revision_inventory(frame_dir, 1) == sample


def test_dormant_unused_module_importing_kit_does_not_mark_library_used() -> None:
    draft = {
        "index.html": b"<!doctype html><main><p>Static page</p></main>",
        "unused.js": b"import { col } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';\n",
    }
    _, carry = carry_kit_assets(draft)
    carried_paths = sorted(carry.carried)
    assert "kit/ar-video/23fd71bf/ar-video-vocabulary.js" in carried_paths

    assert kit_libraries_used(draft, carried_paths=carried_paths) == ()
    assert unused_draft_assets(draft, carried_kit_paths=carried_paths) == ("unused.js",)
    inv = build_inventory(draft, carried_kit_paths=carried_paths)
    assert inv["kitLibraries"] == []
    assert inv["unusedAssets"] == ["unused.js"]

    # When index.html uses shiki and a dormant file imports ar-video alongside a pinned draft copy,
    # only shiki is reported as used while both the dormant script and pinned copy stay unused.
    pinned_copy = "kit/ar-video/23fd71bf/ar-video-vocabulary.js"
    mixed = {
        "index.html": (
            b"<!doctype html><link rel='stylesheet' href='kit/shiki/4.5.0/magic-move.css'>"
            b"<p>Uses shiki only</p>"
        ),
        "unused.js": b"import { col } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js';\n",
        pinned_copy: (KIT_ROOT / "ar-video/23fd71bf/ar-video-vocabulary.js").read_bytes(),
    }
    _, mixed_carry = carry_kit_assets(mixed)
    mixed_inv = build_inventory(mixed, carried_kit_paths=sorted(mixed_carry.carried))
    assert mixed_inv["kitLibraries"] == ["shiki"]
    assert mixed_inv["unusedAssets"] == [pinned_copy, "unused.js"]


def test_build_inventory_surfaces_incomplete_dom_flag_and_summary() -> None:
    base_dom = {
        "figures": {"total": 2, "byKind": {"svg": 2, "canvas": 0, "driven": 0}, "byTechnique": {}},
        "codeExcerpts": {"highlighted": 0, "plain": 1},
    }
    complete = build_inventory(
        {"index.html": b"<!doctype html><p>ok</p>"},
        dom_inventory={**base_dom, "incomplete": False},
    )
    assert "incomplete" not in complete
    assert inventory_summary(complete) == "2 figures (2 SVG) · kit: none · code: 1 plain"

    incomplete = build_inventory(
        {"index.html": b"<!doctype html><p>ok</p>"},
        dom_inventory={**base_dom, "incomplete": True},
    )
    assert incomplete["incomplete"] is True
    assert (
        inventory_summary(incomplete)
        == "2 figures (2 SVG) · kit: none · code: 1 plain · incomplete"
    )


def _admit_simple_revision(
    tmp_path: Path,
    scope: store.FrameScope,
    request_id: str,
    html: bytes,
    limits: FrameLimits,
) -> int:
    working = tmp_path / f"work-{request_id}"
    working.mkdir(parents=True, exist_ok=True)
    (working / "index.html").write_bytes(html)
    store.begin_draft(
        scope,
        request_id=request_id,
        authoring=FrameAuthoring(
            request_id=request_id,
            session_id="session-1",
            started_at="2026-10-22T08:00:00+00:00",
        ),
        focus="explainer",
        assets={"index.html": html},
        working_directory=working,
        limits=limits,
        now="2026-10-22T08:00:00+00:00",
    )
    publication = store.publish_revision(
        scope,
        request_id=request_id,
        entrypoint="index.html",
        evidence=packet(record(1)),
        supplemental=(),
        created_at="2026-10-22T08:00:01+00:00",
        limits=limits,
    )
    assert publication.published and publication.manifest is not None
    return publication.manifest.revision


def test_revision_inventory_sidecar_counts_in_task_meter_and_omits_without_room(
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "task"
    task_root.mkdir()
    scope = store.frame_scope(task_root, LEAF, "explainer")
    area = store.frames_area(task_root, LEAF)
    task_area = area.parent

    rev1 = _admit_simple_revision(
        tmp_path, scope, "req-1", b"<!doctype html><p>v1</p>", FrameLimits()
    )
    before_area = store._area_usage(task_area)
    assert before_area == store.usage(area)["bytes"]

    large_inventory = {
        "figures": {"total": 1, "byKind": {"svg": 1, "canvas": 0, "driven": 0}, "byTechnique": {}},
        "kitLibraries": [],
        "codeExcerpts": {"highlighted": 0, "plain": 0},
        "unusedAssets": [f"orphan-{i:04d}.png" for i in range(1200)],
    }
    assert persist_revision_inventory_within_limit(
        scope.frame_dir,
        rev1,
        large_inventory,
        limit_bytes=before_area + 100_000,
        measure_used_bytes=lambda: store._area_usage(task_area),
    ) == (True, None)
    sidecar = scope.frame_dir / "revisions" / str(rev1) / INVENTORY_NAME
    sidecar_size = sidecar.stat().st_size
    assert sidecar_size > 20_000

    # Every physical byte counts in the task meter, including inventory.json.
    assert store._area_usage(task_area) == before_area + sidecar_size
    assert store.usage(area)["bytes"] == before_area + sidecar_size
    assert revision_inventory_omitted_reason(scope.frame_dir, rev1) is None

    # A second revision publishes when its own bytes fit; when its sidecar does not fit in the
    # remaining room, the sidecar is omitted without evicting revision 1's sidecar.
    tight_cap = before_area * 2 + sidecar_size + 4096
    rev2 = _admit_simple_revision(
        tmp_path, scope, "req-2", b"<!doctype html><p>v2</p>", FrameLimits(task_bytes=tight_cap)
    )
    after_rev2 = store._area_usage(task_area)
    assert rev2 == 2 and after_rev2 <= tight_cap and after_rev2 + sidecar_size > tight_cap

    assert persist_revision_inventory_within_limit(
        scope.frame_dir,
        rev2,
        large_inventory,
        limit_bytes=tight_cap,
        measure_used_bytes=lambda: store._area_usage(task_area),
    ) == (False, INVENTORY_OMITTED_STORAGE_LIMIT_REASON)
    rev2_dir = scope.frame_dir / "revisions" / str(rev2)
    marker = rev2_dir / INVENTORY_OMITTED_STORAGE_LIMIT_NAME
    assert not (rev2_dir / INVENTORY_NAME).exists()
    assert marker.is_file() and marker.stat().st_size == 0
    assert revision_inventory(scope.frame_dir, rev1) is not None
    assert revision_inventory(scope.frame_dir, rev2) is None
    assert (
        revision_inventory_omitted_reason(scope.frame_dir, rev2)
        == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    )
    physical_after = sum(p.stat().st_size for p in task_area.rglob("*") if p.is_file())
    assert physical_after == after_rev2 <= tight_cap
    assert store._area_usage(task_area) == physical_after == store.usage(area)["bytes"]

    # Containment is still validated on the top-level inventory.json path.
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    sidecar.unlink()
    sidecar.symlink_to(outside)
    with pytest.raises(store.FrameRefused):
        revision_usage_bytes(scope.frame_dir / "revisions" / str(rev1), refuse=store.FrameRefused)
    assert store.usage(area)["held"]


def test_inventory_sidecar_interleaved_second_leaf_publish_never_exceeds_task_cap(
    tmp_path: Path,
) -> None:
    other_leaf = "260928-MIK-L103"
    measured = tmp_path / "base"
    measured.mkdir()
    scope_m1 = store.frame_scope(measured, LEAF, "explainer")
    scope_m2 = store.frame_scope(measured, other_leaf, "explainer")
    _admit_simple_revision(
        tmp_path / "b1", scope_m1, "req-1", b"<!doctype html><p>a</p>", FrameLimits()
    )
    _admit_simple_revision(
        tmp_path / "b2", scope_m2, "req-2", b"<!doctype html><p>b</p>", FrameLimits()
    )
    cap = sum(p.stat().st_size for p in scope_m1.area.parent.rglob("*") if p.is_file())

    # Interleave: leaf 1 publishes, leaf 2 publishes up to exact task cap, then leaf 1 records its
    # advisory inventory via the production path. Live re-measurement under the task lock must omit
    # leaf 1's sidecar rather than overflowing the cap, and omission stays stable across retries.
    task_root = tmp_path / "live"
    task_root.mkdir()
    limits = FrameLimits(task_bytes=cap)
    scope_1 = store.frame_scope(task_root, LEAF, "explainer")
    scope_2 = store.frame_scope(task_root, other_leaf, "explainer")
    rev1 = _admit_simple_revision(
        tmp_path / "w1", scope_1, "req-1", b"<!doctype html><p>a</p>", limits
    )
    rev2 = _admit_simple_revision(
        tmp_path / "w2", scope_2, "req-2", b"<!doctype html><p>b</p>", limits
    )
    assert rev1 == 1 and rev2 == 1
    pub1 = store.FramePublication(
        manifest=store.revision_manifest(scope_1.frame_dir, 1),
        published=True,
        reclaimed=(),
        detail="published",
    )
    inventory = {"figures": {"total": 0}, "unusedAssets": [], "kitLibraries": []}
    report: dict[str, object] = {"inventory": inventory, "publication": {"state": "published"}}
    explainer_server._record_published_inventory(scope_1, pub1, inventory, cap, report)

    actual = sum(p.stat().st_size for p in scope_1.area.parent.rglob("*") if p.is_file())
    meter = task_frames_total(task_root, LEAF)
    assert actual == cap and meter["state"] == "ok" and meter["bytes"] == actual
    assert not (scope_1.frame_dir / "revisions" / "1" / INVENTORY_NAME).exists()
    assert report.get("inventory") is None
    assert report.get("inventoryOmitted") == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    assert (
        revision_inventory_omitted_reason(scope_1.frame_dir, 1)
        == INVENTORY_OMITTED_STORAGE_LIMIT_REASON
    )

    # Stability: even if space opens up later, the already-omitted revision stays stably omitted.
    assert persist_revision_inventory_within_limit(
        scope_1.frame_dir,
        1,
        inventory,
        limit_bytes=cap + 100_000,
        measure_used_bytes=lambda: 0,
    ) == (False, INVENTORY_OMITTED_STORAGE_LIMIT_REASON)
    assert not (scope_1.frame_dir / "revisions" / "1" / INVENTORY_NAME).exists()

    # Reentrancy under the exact task lock succeeds cleanly on the publishing thread.
    task_root_reentrant = tmp_path / "reentrant"
    task_root_reentrant.mkdir()
    scope_r = store.frame_scope(task_root_reentrant, LEAF, "explainer")
    with exclusive_file_lock(scope_r.area.parent / store.TASK_LOCK_NAME, "review frame store"):
        rev_r = _admit_simple_revision(
            tmp_path / "wr", scope_r, "req-r", b"<!doctype html><p>r</p>", FrameLimits()
        )
        assert persist_revision_inventory_within_limit(
            scope_r.frame_dir,
            rev_r,
            inventory,
            limit_bytes=FrameLimits().task_bytes,
            measure_used_bytes=lambda: store._area_usage(scope_r.area.parent),
        ) == (True, None)
    assert revision_inventory(scope_r.frame_dir, 1) == inventory
