"""The pinned offline presentation kit (MIK-R104 rule 12).

Rule 12 asks for a named minimum kit -- Shiki with Magic Move, Motion Canvas, OpenGenerativeUI and
the ar-video vocabulary -- to be shipped, pinned and licensed so a page can load it with no network.
These tests hold the shipped bytes to that claim: every file is accounted for by size and digest,
each entry carries the licence text it was read from and a quotation that actually occurs in that
text, every dependency whose code is inside a bundle has its licence beside it, and no bundle
reaches the network. The last test builds a page out of kit assets and passes it through the
model-free readiness check, with a second page that uses no kit at all, because the kit must not
impose a schema or a template on the page.

They are static: no browser runs here. The browser smoke test that actually executes these bundles
is recorded in docs/design/canvas-kit.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from typing import Any

from agents_remember.canvas.readiness import check_page
from canvas_host_test_support import packet, record

KIT = resources.files("agents_remember").joinpath("package_data", "canvas", "kit")
MANIFEST: dict[str, Any] = json.loads(KIT.joinpath("manifest.json").read_text(encoding="utf-8"))

SHIKI = "kit/shiki/4.5.0"
MOTION = "kit/motion-canvas/3.17.2"
OGUI = "kit/open-generative-ui/0.1.0-8391a94"
AR_VIDEO = "kit/ar-video/23fd71bf"

#: Every named part of the rule's minimum kit, and nothing else (Remotion is refused, not shipped).
NAMED = {"shiki", "motion-canvas", "open-generative-ui", "ar-video"}

#: Strings that would make a bundle reach a host of its own choosing rather than the page's prefix.
_NETWORK_CALL = re.compile(
    r"""(?:fetch|import)\s*\(\s*["'](?:[a-z][a-z0-9+.\-]*:|//)|XMLHttpRequest|WebSocket|sendBeacon""",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//)", re.IGNORECASE)
_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*["']([^"']+)["']""", re.IGNORECASE)


def _entry(entry_id: str) -> dict[str, Any]:
    for entry in MANIFEST["entries"]:
        if entry["id"] == entry_id:
            return entry
    raise AssertionError(f"the manifest names no {entry_id!r} entry")


def _bytes(relative: str) -> bytes:
    # The manifest names page paths (``kit/<entry>/<version>/<file>``); the disk path is the same
    # path without the admitted prefix.
    return KIT.joinpath(relative.removeprefix("kit/")).read_bytes()


def _all_files() -> list[str]:
    found: list[str] = []

    def walk(directory: Any, prefix: str = "") -> None:
        for child in directory.iterdir():
            name = f"{prefix}{child.name}"
            if child.is_dir():
                walk(child, f"{name}/")
            else:
                found.append(name)

    walk(KIT)
    return sorted(found)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _assemble(relative_paths: list[str]) -> dict[str, bytes]:
    """The revision a page would be admitted as: the page's own bytes plus the kit files it names."""

    return {path: _bytes(path) for path in relative_paths}


def test_the_manifest_ships_the_named_minimum_kit_and_nothing_more() -> None:
    assert MANIFEST["kit"] == "ar-canvas-kit/v1"
    assert {entry["id"] for entry in MANIFEST["entries"]} == NAMED
    assert "remotion" not in {entry["id"].lower() for entry in MANIFEST["entries"]}
    for entry in MANIFEST["entries"]:
        # A pinned identity, not a range: the directory is the version, the version is the directory.
        assert entry["version"] and not re.search(r"[\^~*]|\blatest\b", entry["version"]), entry
        assert KIT.joinpath(entry["id"], entry["version"]).is_dir()
        assert KIT.joinpath(entry["entry"]).is_file()


def test_every_shipped_byte_is_accounted_for_by_size_and_digest() -> None:
    total = 0
    for entry in MANIFEST["entries"]:
        accounted = 0
        for file in entry["files"]:
            payload = _bytes(f"{entry['id']}/{entry['version']}/{file['path']}")
            assert len(payload) == file["bytes"], file["path"]
            assert hashlib.sha256(payload).hexdigest() == file["sha256"], file["path"]
            accounted += file["bytes"]
        for licence in entry["dependencyLicences"]:
            payload = _bytes(f"{entry['id']}/{entry['version']}/{licence['file']}")
            assert len(payload) == licence["bytes"], licence["file"]
            assert hashlib.sha256(payload).hexdigest() == licence["sha256"], licence["file"]
            accounted += licence["bytes"]
        assert accounted == entry["bytes"], entry["id"]
        total += accounted
    assert total == MANIFEST["totalBytes"]
    # Nothing is on disk that the manifest does not name.
    named = {"manifest.json"}
    for entry in MANIFEST["entries"]:
        base = f"{entry['id']}/{entry['version']}"
        named.update(f"{base}/{file['path']}" for file in entry["files"])
        named.update(f"{base}/{licence['file']}" for licence in entry["dependencyLicences"])
    assert set(_all_files()) == named


def test_each_entry_carries_the_licence_text_it_was_read_from() -> None:
    for entry in MANIFEST["entries"]:
        licence_file = entry["licenceFile"]
        if entry["licenceQuotation"] is None:
            # Only the developer's own material may arrive without a third-party licence.
            assert licence_file is None
            assert entry["licence"].startswith("own material")
            continue
        assert licence_file, entry["id"]
        text = _bytes(licence_file).decode("utf-8")
        assert _flat(entry["licenceQuotation"]) in _flat(text), entry["id"]
        assert len(text) > 200, entry["id"]


def test_every_dependency_inside_a_bundle_has_its_licence_beside_it() -> None:
    shiki = _entry("shiki")
    assert [row["package"] for row in shiki["dependencies"]] == sorted(
        row["package"] for row in shiki["dependencies"]
    )
    shipped = {licence["file"] for licence in shiki["dependencyLicences"]}
    for dependency in shiki["dependencies"]:
        assert dependency["licenceFile"] in shipped, dependency["package"]
        text = _bytes(f"shiki/4.5.0/{dependency['licenceFile']}").decode("utf-8")
        assert len(text) > 200, dependency["package"]
    # The two licences that are not MIT are named as what their files say, not as the field says.
    motion = _entry("motion-canvas")
    by_package = {row["package"]: row for row in motion["dependencies"]}
    assert set(by_package) == {"@motion-canvas/2d", "@motion-canvas/core", "chroma-js"}
    assert by_package["chroma-js"]["declared"] == "(BSD-3-Clause AND Apache-2.0)"
    assert by_package["chroma-js"]["firstLine"].startswith("chroma.js")
    assert by_package["@motion-canvas/core"]["declared"] == "MIT"
    shiki_by_package = {row["package"]: row for row in shiki["dependencies"]}
    assert shiki_by_package["diff-match-patch-es"]["declared"] == "Apache-2.0"
    assert shiki_by_package["diff-match-patch-es"]["firstLine"].startswith("Apache License")


def test_no_bundle_reaches_the_network() -> None:
    bundles = {
        "shiki": f"{SHIKI}/shiki-kit.js",
        "motion-canvas": f"{MOTION}/motion-canvas-kit.js",
        "open-generative-ui": f"{OGUI}/open-generative-ui-kit.js",
        "ar-video": f"{AR_VIDEO}/ar-video-vocabulary.js",
    }
    for entry_id, path in bundles.items():
        text = _bytes(path).decode("utf-8")
        assert "XMLHttpRequest" not in text, entry_id
        assert "sendBeacon" not in text, entry_id
        assert "WebSocket" not in text, entry_id
        assert not _NETWORK_CALL.search(text), entry_id
    # The two entries that carry no media loader at all also carry no URL of any scheme.
    assert "http://" not in _bytes(f"{SHIKI}/shiki-kit.js").decode("utf-8")
    assert "https://" not in _bytes(f"{SHIKI}/shiki-kit.js").decode("utf-8")
    assert "https://" not in _bytes(f"{OGUI}/open-generative-ui-kit.js").decode("utf-8")
    assert "esm.sh" not in _bytes(f"{OGUI}/open-generative-ui-kit.js").decode("utf-8")


def test_each_bundle_is_the_library_it_says_it_is() -> None:
    expected = {
        f"{SHIKI}/shiki-kit.js": ("createHighlighter", "shiki-magic-move", "github-dark"),
        f"{MOTION}/motion-canvas-kit.js": ("createKit", "renderFrame", "View2D"),
        f"{OGUI}/open-generative-ui-kit.js": (
            "THEME_CSS",
            "FORM_STYLES_CSS",
            "OPEN_GEN_UI_DESIGN_SKILL",
        ),
        f"{AR_VIDEO}/ar-video-vocabulary.js": ("Proj", "orbitKeys", "oklch"),
    }
    for path, symbols in expected.items():
        text = _bytes(path).decode("utf-8")
        for symbol in symbols:
            assert symbol in text, (path, symbol)
    assert _bytes(f"{AR_VIDEO}/VOCABULARY.md").startswith(b"# The ar-video vocabulary")


PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>the change, offline</title>
<link rel="stylesheet" href="kit/shiki/4.5.0/magic-move.css">
<link rel="stylesheet" href="page.css">
</head><body>
<h1>the change</h1>
<pre id="before"></pre>
<shiki-magic-move id="morph" lang="javascript" theme="github-dark"></shiki-magic-move>
<canvas id="stage" width="960" height="540"></canvas>
<script type="module" src="page.js"></script>
</body></html>"""

PAGE_CSS = b"""body{background:#0b0e12;color:#e8e6de;font:16px ui-monospace,monospace}
h1{transition:opacity .2s}
@media (prefers-reduced-motion: reduce){h1{transition:none}}
"""

PAGE_JS = b"""import { createHighlighter } from './kit/shiki/4.5.0/shiki-kit.js'
import { col, ease, Proj, orbit, strokeSegs, curveSegs, v3 } from './kit/ar-video/23fd71bf/ar-video-vocabulary.js'

const highlighter = await createHighlighter()
document.getElementById('before').innerHTML = highlighter.codeToHtml('const a = 1', {
  lang: 'javascript',
  theme: 'github-dark',
})
const morph = document.createElement('shiki-magic-move')
morph.highlighter = highlighter
morph.lang = 'javascript'
morph.theme = 'github-dark'
morph.code = 'function f(a) {\\n  return a + 1\\n}'
document.body.append(morph)

// Frame = f(t): one projection, one colour function, two tree branches, no network.
const canvas = document.getElementById('stage')
const context = canvas.getContext('2d')
const camera = orbit(v3(0, 0, 0), 900, 0.4, 0.2)
const projection = new Proj(camera)
const branches = [
  curveSegs(v3(0, 0, 0), v3(200, 180, 0), v3(400, 0, 0), 24),
  curveSegs(v3(0, 0, 0), v3(-200, -180, 0), v3(-400, 0, 0), 24),
]
for (const segments of branches) {
  strokeSegs(context, projection, segments, alpha => col('amber', alpha * projection.fog(2000)))
}
context.fillStyle = col('cyan', ease.outExpo(0.5))
context.fillRect(24, 24, 120, 8)
"""

PAGE_FILES = {
    "index.html": PAGE,
    "page.css": PAGE_CSS,
    "page.js": PAGE_JS,
}

PAGE_ASSETS = (
    "kit/shiki/4.5.0/shiki-kit.js",
    "kit/shiki/4.5.0/magic-move.css",
    "kit/ar-video/23fd71bf/ar-video-vocabulary.js",
)


def _page_assets() -> dict[str, bytes]:
    assets = dict(PAGE_FILES)
    for path in PAGE_ASSETS:
        assets[path] = _bytes(path)
    return assets


def test_a_page_renders_from_the_kit_offline_and_with_no_template() -> None:
    assets = _page_assets()
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()

    # Every module the page imports resolves inside the same admitted revision.
    for target in _IMPORT.findall(PAGE_JS.decode("utf-8")):
        resolved = target.lstrip("./")
        assert resolved in assets, target
        assert not _ABSOLUTE.match(target), target

    # Nothing in the page or its kit names another origin.
    for path, payload in assets.items():
        if path.endswith((".html", ".css", ".js")):
            text = payload.decode("utf-8")
            assert "http://" not in text, path
            assert "https://" not in text, path

    # No template and no schema: the page is plain HTML, and its names are its own.
    html = PAGE.decode("utf-8")
    assert "<template" not in html
    assert "data-schema" not in html
    assert "schema" not in MANIFEST
    assert "template" not in MANIFEST


def test_a_page_that_uses_no_kit_at_all_is_still_ready() -> None:
    plain = {
        "index.html": b"<!doctype html><title>plain</title><p>no kit, no template</p>",
    }
    report = check_page(plain, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()


def test_the_motion_canvas_bundle_declares_every_reference_it_carries() -> None:
    """Pin URLs in the Motion Canvas bundle; JavaScript error prose is not an HTML resource.

    The bundle carries two documentation links inside error prose (@motion-canvas/core's shader and
    experimental-feature remarks) and one ``url(data:image/svg+xml;base64,...#filter)`` that a shape
    applies when ``antialiased()`` is false. Neither error link is a subresource load. Readiness scans
    markup/styles, not JavaScript string literals. Actual browser loading remains the publication
    gate; this static pin alone makes no runtime network claim.
    """

    text = _bytes(f"{MOTION}/motion-canvas-kit.js").decode("utf-8")
    # Three comment/namespace URLs (Apache licence, SVG namespace, CSS colour names) plus the two
    # documentation links that appear in error prose as ``href='...'``.
    assert sorted(set(re.findall(r"https?://[^'\"\s<>)]+", text))) == [
        "http://www.apache.org/licenses/LICENSE-2.0",
        "http://www.w3.org/2000/svg",
        "http://www.w3.org/TR/css3-color/#svg-color",
        "https://motioncanvas.io/docs/experimental",
        "https://motioncanvas.io/docs/shaders",
    ]
    assert sorted(set(re.findall(r"""href=['"](https?://[^'"]+)['"]""", text))) == [
        "https://motioncanvas.io/docs/experimental",
        "https://motioncanvas.io/docs/shaders",
    ]
    assert text.count("url(data:image/svg+xml;base64,") == 1
    assert not re.search(r"""(?:fetch|import)\s*\(\s*["']https?://""", text)
    # The only media load the bundle can perform is the page's own relative source.
    assert "fetch(this.source" in text
