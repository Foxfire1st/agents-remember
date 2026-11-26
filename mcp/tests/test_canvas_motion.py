"""Browser-backed prospective load, motion-publication, citation, and inventory tests.

Exercises the real Chromium prospective load runner (`canvas-prospective-load.mjs` +
`canvas-dom-inventory.mjs` + `canvas-interactive-report.mjs`) and the registered `render`
tool over served loopback pages.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.canvas import prospective
from agents_remember.canvas.document import bound_document
from agents_remember.canvas.explainer_browser import browser_environment, node_executable
from agents_remember.canvas.inventory import build_inventory, inventory_summary
from agents_remember.canvas.kit import kit_assets
from agents_remember.canvas.prospective import BrowserProspectiveLoad, ProspectiveResult
from agents_remember.canvas.readiness import check_page
from agents_remember.canvas.sessions import CanvasSession
from canvas_browser_test_support import configure_browser_env
from canvas_host_test_support import packet, record
from explainer_server_test_support import _call, _server, _world

DOM_INVENTORY_MODULE = Path(prospective.__file__).with_name("canvas-dom-inventory.mjs")
INTERACTIVE_REPORT_MODULE = Path(prospective.__file__).with_name("canvas-interactive-report.mjs")


@pytest.fixture
def browser_load(monkeypatch: pytest.MonkeyPatch):
    """Return a factory for real-Chromium BrowserProspectiveLoad runs."""

    configure_browser_env(monkeypatch)

    def _run(
        html: str,
        *,
        extra_assets: dict[str, bytes] | None = None,
        interactive_directory: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> ProspectiveResult:
        assets: dict[str, bytes] = {"index.html": html.encode("utf-8")}
        if extra_assets:
            assets.update(extra_assets)
        loader = BrowserProspectiveLoad(
            node=node_executable(),
            timeout_seconds=timeout_seconds,
            interactive_directory=interactive_directory,
        )
        return loader.load(assets=assets, entrypoint="index.html", packet=packet(record()))

    return _run


@pytest.fixture
def render_draft(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Write a draft page into a fresh explainer workspace and invoke registered `render()`."""

    configure_browser_env(monkeypatch)
    counter = 0

    def _render(
        html: str,
        *,
        extra_assets: dict[str, bytes] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal counter
        counter += 1
        world_dir = tmp_path / f"world-{counter}"
        world_dir.mkdir(parents=True, exist_ok=True)
        world = _world(world_dir)
        assets_dir = world.workspace / "drafts" / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / "index.html").write_text(html, encoding="utf-8")
        if extra_assets:
            for relative, payload in extra_assets.items():
                target = assets_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        server = _server(world)
        reply = _call(server, "render")
        report_path = reply.get("report")
        report = (
            json.loads((world.workspace / report_path).read_text(encoding="utf-8"))
            if report_path
            else {}
        )
        return reply, report

    return _render


def _html(body: str, *, head: str = "", style: str = "", script: str = "") -> str:
    """Wrap minimal HTML fixture fragments into a complete document."""

    style_tag = f"<style>{style}</style>" if style else ""
    head_block = f"<head>{head}{style_tag}</head>" if (head or style_tag) else ""
    script_tag = f"<script>{script}</script>" if script else ""
    return f"<!doctype html><html>{head_block}<body>{body}{script_tag}</body></html>"


def _render_error_lines(reply: dict[str, Any], report: dict[str, Any]) -> list[str]:
    """Return error diagnostics from `reply['errors']`, or `report.json` when spilled."""

    spilled = any(entry.get("part") == "errors" for entry in reply.get("moved", []))
    return list(report.get("allErrors") or []) if spilled else list(reply.get("errors") or [])


def _run_node_playwright(
    body: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Run an ESM Playwright snippet with shared Chromium lifecycle and JSON stdin/stdout."""

    script = (
        'import { readFileSync } from "node:fs";\n'
        'import { createServer } from "node:http";\n'
        'import { pathToFileURL } from "node:url";\n'
        'const input = JSON.parse(readFileSync(0, "utf8"));\n'
        "const pwRef = process.env.AR_PLAYWRIGHT "
        '? pathToFileURL(process.env.AR_PLAYWRIGHT).href : "playwright";\n'
        "const { chromium } = await import(pwRef).then((m) => (m.chromium ? m : m.default));\n"
        'const browser = await chromium.launch({ args: ["--no-sandbox"] });\n'
        "try {\n"
        f"{body}\n"
        "} finally {\n"
        "  await browser.close();\n"
        "}\n"
    )
    completed = subprocess.run(
        [node_executable(), "--input-type=module", "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=timeout_seconds,
        env={**os.environ, **browser_environment()},
    )
    assert completed.returncode == 0, f"node probe failed: {completed.stderr or completed.stdout}"
    return json.loads(completed.stdout.strip())


def _poster_svg_data_uri() -> str:
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="#223"/></svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")


def _assert_no_motion_remark(reply: dict[str, Any], report: dict[str, Any]) -> None:
    """Assert neither the render reply nor report.json carries a motion refusal or remark."""

    for forbidden_key in (
        "motionFaults",
        "motionHints",
        "motion_faults",
        "motion_hints",
        "reducedMotionOk",
        "reduced_motion_ok",
    ):
        assert forbidden_key not in reply, reply
        assert forbidden_key not in report, report
    reply_blob = json.dumps(reply).lower()
    report_blob = json.dumps(report).lower()
    for phrase in ("reduced-motion", "reduced motion", "effects-off", "quiet-motion"):
        assert phrase not in reply_blob, f"unexpected {phrase!r} in reply: {reply}"
        assert phrase not in report_blob, f"unexpected {phrase!r} in report: {report}"


# --- Dynamic citation validation in ordinary browser load ----------------------------------------


def test_js_inserted_bad_evidence_reference_fails_in_browser(browser_load) -> None:
    html = _html(
        '<main><h1>Explainer</h1><p id="target">Loading...</p></main>',
        script="""
setTimeout(() => {
  const span = document.createElement("span");
  span.setAttribute("data-evidence", "bad-ref");
  span.textContent = "dynamic citation";
  document.getElementById("target").appendChild(span);
}, 150);
""",
    )
    result = browser_load(html)
    assert result.state == "failed"
    assert "bad-ref" in result.unresolved_references


def test_js_inserted_valid_citation_and_claim_pass_in_browser(browser_load) -> None:
    html = _html(
        '<main><h1>Explainer</h1><p id="target">Loaded</p></main>',
        script="""
setTimeout(() => {
  const target = document.getElementById("target");
  const refSpan = document.createElement("span");
  refSpan.setAttribute("data-evidence", "code-candidate:src/app.py");
  refSpan.textContent = "dynamic evidence";
  target.appendChild(refSpan);
  const claimSpan = document.createElement("span");
  claimSpan.setAttribute("data-claim", "c-present");
  claimSpan.textContent = "dynamic claim";
  target.appendChild(claimSpan);
}, 150);
""",
    )
    result = browser_load(html)
    assert result.state == "loaded", result.diagnostics()
    assert "code-candidate:src/app.py" in result.evidence_references
    assert "c-present" in result.claim_ids
    assert result.unresolved_references == ()


# --- Continuous CSS & canvas motion and carried magic-move.css publish without motion remark -----


def test_render_publishes_continuous_css_and_canvas_motion_without_motion_remark(
    render_draft,
) -> None:
    """Continuous CSS animations and Canvas2D rAF loops publish with no motion refusal or remark."""

    html = _html(
        "<main><h1>Animated Mechanism</h1>"
        '<figure id="fig-css-pulse" data-technique="step-through">'
        '<div class="dot"></div><figcaption>Continuous CSS pulse</figcaption></figure>'
        '<figure id="fig-canvas-loop" data-technique="before-and-after">'
        '<canvas id="stage" width="160" height="90"></canvas>'
        "<figcaption>Continuous Canvas2D animation</figcaption></figure></main>",
        style=(
            "@keyframes pulse { from { opacity: 0.3; transform: translateX(0); } "
            "to { opacity: 1; transform: translateX(24px); } }"
            ".dot { width: 32px; height: 32px; background: #1a73e8; "
            "animation: pulse 0.8s linear infinite; }"
        ),
        script="""
const canvas = document.getElementById("stage");
const ctx = canvas.getContext("2d");
let step = 0;
function tick() {
  step = (step + 3) % 120;
  ctx.fillStyle = "#18202c";
  ctx.fillRect(0, 0, 160, 90);
  ctx.fillStyle = "#4caf50";
  ctx.fillRect(10 + step, 24, 24, 42);
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
""",
    )
    reply, report = render_draft(html)
    assert reply["publication"]["state"] == "published", reply
    assert reply["summary"]["errors"] == 0, reply
    assert _render_error_lines(reply, report) == []
    assert report["state"] == "loaded"
    assert report["visible_content_ok"] is True
    _assert_no_motion_remark(reply, report)

    interactive = report.get("interactive") or {}
    passes = interactive.get("passes") or []
    assert [(one["width"], one["method"]) for one in passes] == [
        (1280, "click"),
        (1280, "keyboard"),
        (390, "click"),
        (390, "keyboard"),
    ]
    assert all("effects" not in one for one in passes)
    assert all("reducedMotion" not in one for one in passes)


def test_render_publishes_carried_shiki_magic_move_css_without_reduced_motion_rule(
    render_draft,
) -> None:
    """Carrying kit/shiki/4.5.0/magic-move.css without a reduced-motion rule passes and publishes."""

    carried = kit_assets(["shiki/4.5.0/magic-move.css"])
    html = _html(
        '<main><figure id="fig-magic-move" data-technique="code-morph">'
        '<div class="shiki-magic-move-container">'
        '<span class="shiki-magic-move-item">const x = 1;</span></div>'
        "<figcaption>Carried magic-move stylesheet</figcaption></figure></main>",
        head='<link rel="stylesheet" href="kit/shiki/4.5.0/magic-move.css" />',
    )
    static_report = check_page(
        {"index.html": html.encode("utf-8"), **carried},
        entrypoint="index.html",
        packet=packet(record()),
    )
    assert static_report.ready is True, static_report.diagnostics()
    assert static_report.failures == ()

    reply, report = render_draft(html)
    assert reply["publication"]["state"] == "published", reply
    assert reply["summary"]["errors"] == 0, reply
    assert _render_error_lines(reply, report) == []
    carried_paths = list(report.get("kitAssets") or [])
    assert "kit/shiki/4.5.0/magic-move.css" in carried_paths
    _assert_no_motion_remark(reply, report)


# --- Ordinary load stability and paint-visibility checks -----------------------------------------


def test_render_publishes_when_hide_control_conceals_main_figure_after_load(render_draft) -> None:
    """Visibility is measured before synthetic interactions, so a Hide control stays publishable."""

    html = _html(
        '<main id="app"><button id="hide-btn" type="button">Hide diagram</button>'
        '<figure id="fig-toggleable"><svg width="160" height="80">'
        '<rect width="160" height="80" fill="#1a73e8" /></svg>'
        "<figcaption>Toggleable figure</figcaption></figure></main>",
        script="""
document.getElementById("hide-btn").addEventListener("click", () => {
  const fig = document.getElementById("fig-toggleable");
  fig.hidden = !fig.hidden;
});
""",
    )
    reply, report = render_draft(html)
    assert reply["publication"]["state"] == "published", reply
    assert reply["summary"]["errors"] == 0, reply
    assert report["visible_content_ok"] is True


def test_no_false_failure_web_fonts_loading(browser_load) -> None:
    html = _html(
        '<figure id="fig-fonts"><p>Static copy rendered with loaded web font.</p></figure>',
        style=(
            '@font-face { font-family: "ExplainerLocal"; '
            'src: local("DejaVu Sans"), local("Liberation Sans"), local("Arial"); '
            'font-display: swap; } body { font-family: "ExplainerLocal", sans-serif; }'
        ),
        script="""
if (typeof FontFace !== "undefined" && document.fonts) {
  const face = new FontFace("LateExplainer", 'local("DejaVu Sans"), local("Liberation Sans"), local("Arial")');
  face.load().then((loaded) => document.fonts.add(loaded)).catch(() => {});
}
""",
    )
    result = browser_load(html)
    assert result.state == "loaded", result.diagnostics()
    assert result.visible_content_ok is True


def test_no_false_failure_focus_ring(browser_load) -> None:
    html = _html(
        '<figure id="fig-focus"><svg width="80" height="40"><circle cx="40" cy="20" r="15" '
        'fill="#188038" /></svg><button id="play-btn" type="button">Play</button></figure>',
        style="button:focus { outline: 3px solid #1a73e8; outline-offset: 2px; }",
        script='document.getElementById("play-btn").focus();',
    )
    result = browser_load(html)
    assert result.state == "loaded", result.diagnostics()


def test_no_false_failure_paused_video_with_poster(browser_load) -> None:
    poster = _poster_svg_data_uri()
    html = _html(
        f'<figure id="fig-video"><video controls width="200" height="100" poster="{poster}">'
        "</video><figcaption>Paused video with poster</figcaption></figure>"
    )
    result = browser_load(html)
    assert result.state == "loaded", result.diagnostics()


# --- Interactive sweep deadline, DOM inventory accuracy/scaling, and live bound_document ---------


def test_interactive_sweep_deadline_keeps_unvisited_out_of_errors(
    browser_load, tmp_path: Path
) -> None:
    buttons = "".join(f'<button type="button">Btn {i}</button>' for i in range(25))
    # RESERVE_MS is 18.5s, so a 19s limit gives a ~0.5s interactive sweep budget before stopping.
    result = browser_load(
        _html(f"<main><h1>Many Controls</h1>{buttons}</main>"),
        interactive_directory=tmp_path / "interactions",
        timeout_seconds=19.0,
    )
    assert result.state == "loaded", result.diagnostics()
    interactive = result.interactive
    assert interactive is not None
    assert interactive["errors"] == []
    assert interactive["unvisited"], (
        "expected deadline to leave at least one pass/control unvisited"
    )
    for one_pass in interactive["passes"]:
        assert one_pass["errors"] == []
        assert "effects" not in one_pass
        if not one_pass["complete"]:
            assert one_pass.get("notReached")


def test_dom_inventory_counts_figures_techniques_and_code_excerpts(browser_load) -> None:
    html = _html(
        '<figure id="f1" data-technique="  Step  Diagram ">'
        '<svg width="100" height="60"><rect width="100" height="60" fill="#336" /></svg>'
        '<button type="button">Play</button></figure>'
        '<div id="f2" data-technique="Canvas Sim"><canvas width="100" height="60"></canvas></div>'
        '<svg id="standalone-svg" width="40" height="40"><circle cx="20" cy="20" r="10" /></svg>'
        '<svg width="12" height="12"><circle cx="6" cy="6" r="4" /></svg>'
        '<pre class="shiki"><code><span style="color:#abc">def f(): pass</span></code></pre>'
        "<pre><code>plain code block</code></pre>"
    )
    result = browser_load(html)
    assert result.state == "loaded", result.diagnostics()
    inv = result.dom_inventory
    assert inv is not None
    assert inv["figures"]["total"] == 3  # f1, f2, standalone-svg (tiny 12x12 icon excluded)
    assert inv["figures"]["byKind"] == {"svg": 2, "canvas": 1, "driven": 1}
    assert inv["figures"]["byTechnique"] == {"canvas-sim": 1, "step-diagram": 1}
    assert inv["codeExcerpts"] == {"highlighted": 1, "plain": 1}


def test_dom_inventory_distinguishes_untagged_containers_nested_figures_and_plain_pre(
    browser_load,
) -> None:
    html = _html(
        '<section id="calc"><span id="calc-out">0</span>'
        '<button id="calc-go" type="button">Go</button></section>'
        '<div><svg width="80" height="40"><rect width="80" height="40" fill="#445" /></svg></div>'
        '<figure id="outer"><figure id="inner"><canvas width="100" height="60"></canvas></figure></figure>'
        "<pre>Plain prose block without code tag</pre>"
        "<pre><code>const x = 1;</code></pre>"
        '<div data-code-excerpt="highlighted"><span>const y = 2;</span></div>',
        script='document.getElementById("calc-go").addEventListener("click", () => {'
        'document.getElementById("calc-out").textContent = "1";});',
    )
    result = browser_load(html)
    assert result.state == "loaded", result.diagnostics()
    inv = result.dom_inventory
    assert inv is not None
    assert inv["figures"]["total"] == 3  # calc, outer, inner (anonymous div/svg excluded)
    assert inv["figures"]["byKind"] == {"svg": 0, "canvas": 1, "driven": 1}
    assert inv["figures"]["byTechnique"] == {}
    assert inv["codeExcerpts"] == {"highlighted": 1, "plain": 1}


def test_dom_inventory_scales_linearly_and_cap_incomplete_preserves_load_eligibility(
    browser_load, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_browser_env(monkeypatch)
    body = """
const { collectDomInventory } = await import(pathToFileURL(input.domInventoryPath).href);
const nested = (depth) => {
  let open = "", close = "";
  for (let i = 0; i < depth; i += 1) {
    open += `<figure id="nest-${i}" class="code-lines">`;
    close = `</figure>${close}`;
  }
  return `<!doctype html><html><body>${open}<span style="color:#abc">leaf</span>${close}</body></html>`;
};
const context = await browser.newContext();
const page = await context.newPage();
const results = {};
for (const depth of input.depths) {
  await page.setContent(nested(depth), { waitUntil: "load" });
  await collectDomInventory(page);
  const samples = [];
  let inventory = null;
  for (let run = 0; run < 5; run += 1) {
    const start = performance.now();
    inventory = await collectDomInventory(page);
    samples.push(performance.now() - start);
  }
  samples.sort((a, b) => a - b);
  results[String(depth)] = { medianMs: samples[2], inventory };
}
process.stdout.write(JSON.stringify(results) + "\\n");
await context.close();
"""
    scaled = _run_node_playwright(
        body, {"domInventoryPath": DOM_INVENTORY_MODULE.as_posix(), "depths": [100, 200]}
    )
    inv_100, inv_200 = scaled["100"]["inventory"], scaled["200"]["inventory"]
    assert inv_100["figures"]["total"] == 100
    assert inv_200["figures"]["total"] == 200
    assert inv_100["codeExcerpts"] == {"highlighted": 1, "plain": 0}
    assert inv_200["codeExcerpts"] == {"highlighted": 1, "plain": 0}
    assert not inv_100.get("incomplete") and not inv_200.get("incomplete")
    ms_100, ms_200 = float(scaled["100"]["medianMs"]), float(scaled["200"]["medianMs"])
    assert ms_200 < 500.0
    assert ms_200 <= max(ms_100 * 3.5, 50.0)

    over_cap_html = _html(
        '<main><h1>Large DOM</h1><div id="bulk"></div></main>'
        '<figure id="fig-overcap"><svg width="60" height="30">'
        '<rect width="60" height="30" fill="#234" /></svg>'
        "<figcaption>Still figure beside a capped DOM walk</figcaption></figure>",
        script="""
const frag = document.createDocumentFragment();
for (let i = 0; i < 20500; i += 1) frag.appendChild(document.createElement("span"));
document.getElementById("bulk").appendChild(frag);
""",
    )
    capped = browser_load(over_cap_html)
    assert capped.state == "loaded", capped.diagnostics()
    assert capped.visible_content_ok is True
    assert capped.dom_inventory is not None and capped.dom_inventory.get("incomplete") is True
    built = build_inventory(
        {"index.html": over_cap_html.encode("utf-8")},
        dom_inventory=capped.dom_inventory,
    )
    assert built["incomplete"] is True
    summary = inventory_summary(built)
    assert summary is not None and "incomplete" in summary


def test_bound_document_delivers_binding_without_effects_bridge_in_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_browser_env(monkeypatch)
    session = CanvasSession(
        session="s" * 24,
        repository_id="repo",
        master="master",
        leaf_id="leaf",
        frame_id="frame",
        revision=1,
        comparison="review-comparison:m/l/1",
        evidence_digest="sha256:" + "0" * 64,
        entrypoint="index.html",
        created_at=0.0,
        expires_at=1800.0,
    )
    raw = _html(
        '<figure id="fig-bound"><div class="dot"></div><figcaption>Bound</figcaption></figure>',
        style=(
            "@keyframes spin { from { opacity: 0.4; } to { opacity: 1; } }"
            ".dot { width: 30px; height: 30px; background: #0a7; animation: spin 1s linear infinite; }"
            '[data-effects="off"] .dot { animation: none; }'
        ),
        script="""
window.__bindings = [];
window.addEventListener("message", (event) => {
  if (event.data && event.data.schema === "ar-canvas-binding/v1") {
    window.__bindings.push(event.data);
  }
});
""",
    ).encode("utf-8")
    served = bound_document(raw, session).decode("utf-8")
    body = """
const server = createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(req.url === "/page.html" ? input.html : '<!doctype html><html><body><iframe id="frame" src="/page.html"></iframe></body></html>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (err) => pageErrors.push(err.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/host.html`, { waitUntil: "load" });
  const frame = await (await page.waitForSelector("#frame")).contentFrame();
  await frame.waitForLoadState("load");
  await page.evaluate((msg) => document.getElementById("frame").contentWindow.postMessage(msg, "*"), {
    schema: "ar-canvas-binding/v1", protocolVersion: 1, session: input.session,
    revision: 1, comparison: input.comparison, effects: false,
  });
  await page.waitForTimeout(100);
  const state = await frame.evaluate(() => ({
    bindings: window.__bindings,
    hasEffectsAttr: "effects" in document.documentElement.dataset,
    dotAnimation: getComputedStyle(document.querySelector(".dot")).animationName,
  }));
  process.stdout.write(JSON.stringify({ state, pageErrors }) + "\\n");
  await context.close();
} finally {
  server.close();
}
"""
    observed = _run_node_playwright(
        body, {"html": served, "session": session.session, "comparison": session.comparison}
    )
    assert observed["pageErrors"] == []
    state = observed["state"]
    assert state["bindings"][0] == {
        "schema": "ar-canvas-binding/v1",
        "protocolVersion": 1,
        "session": session.session,
        "revision": 1,
        "comparison": session.comparison,
    }
    assert state["hasEffectsAttr"] is False
    assert state["dotAnimation"] == "spin"


def test_interactive_resume_drops_legacy_motion_fields_while_preserving_progress(
    tmp_path: Path,
) -> None:
    """Resuming a release-shaped partial report keeps progress while emitting only current fields."""

    script = (
        'import { readFileSync } from "node:fs";\n'
        'import { pathToFileURL } from "node:url";\n'
        'const input = JSON.parse(readFileSync(0, "utf8"));\n'
        "const { interactiveReport } = await import(pathToFileURL(input.modulePath).href);\n"
        "const resumed = await interactiveReport(\n"
        '  null, "about:blank", input.outDir, null, input.previous\n'
        ");\n"
        'process.stdout.write(JSON.stringify(resumed) + "\\n");\n'
    )

    def _legacy_pass(width: int, method: str) -> dict[str, Any]:
        return {
            "width": width,
            "height": 720,
            "method": method,
            "reducedMotion": "no-preference",
            "effects": True,
            "complete": True,
            "errors": [],
            "controls": [{"id": "advance", "status": "exercised"}],
            "pictures": [f"{width}-{method}-initial.png"],
            "motion": {"animations": 1, "playing": 0},
        }

    previous = {
        "run": "run-001",
        "passes": [
            {
                "width": 1280,
                "height": 720,
                "method": "keyboard",
                "reducedMotion": "no-preference",
                "effects": False,
                "complete": True,
                "errors": [],
                "controls": [{"id": "wrong-effects-off", "status": "exercised"}],
                "pictures": ["wrong.png"],
                "motion": {"animations": 0, "playing": 0},
            },
            _legacy_pass(1280, "click"),
            _legacy_pass(1280, "keyboard"),
            _legacy_pass(390, "click"),
            _legacy_pass(390, "keyboard"),
            {
                "width": 1280,
                "method": "keyboard",
                "effects": False,
                "complete": False,
                "controls": [],
                "pictures": [],
            },
        ],
    }
    completed = subprocess.run(
        [node_executable(), "--input-type=module", "-e", script],
        input=json.dumps(
            {
                "modulePath": INTERACTIVE_REPORT_MODULE.as_posix(),
                "outDir": (tmp_path / "resumed-report").as_posix(),
                "previous": previous,
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30.0,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    resumed = json.loads(completed.stdout.strip())
    assert resumed["state"] == "observed"
    assert resumed["resumedFrom"] == "run-001"
    assert resumed["unvisited"] == []
    passes = resumed["passes"]
    assert [(one["width"], one["method"]) for one in passes] == [
        (1280, "click"),
        (1280, "keyboard"),
        (390, "click"),
        (390, "keyboard"),
    ]
    for one in passes:
        assert one["carried"] is True
        assert one["run"] == "run-001"
        assert one["controls"] == [{"id": "advance", "status": "exercised"}]
        assert one["pictures"] == [f"{one['width']}-{one['method']}-initial.png"]
        assert "motion" not in one
        assert "effects" not in one
        assert "reducedMotion" not in one
