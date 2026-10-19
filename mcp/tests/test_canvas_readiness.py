"""The model-free readiness check of one prospective revision (MIK-R104 rule 14).

Each case protects a distinct way a page can fail to load as itself: reaching the network, naming an
asset that is not the revision's, citing evidence the packet does not carry, or moving without a
reduced-motion rule. The inert-script note is asserted separately because scripts are disabled by
policy (rule 8), not by a page defect.
"""

from __future__ import annotations

from agents_remember.canvas.readiness import check_page
from canvas_host_test_support import packet, record

PAGE = b"""<!doctype html><html><head>
<link rel="stylesheet" href="app.css">
</head><body><img src="img/plot.svg" alt=""><p id="claim">the change</p></body></html>"""

CSS = b"body{font:16px monospace}"


def _assets(**extra: bytes) -> dict[str, bytes]:
    assets: dict[str, bytes] = {
        "index.html": PAGE,
        "app.css": CSS,
        "img/plot.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
    }
    assets.update(extra)
    return assets


def test_an_admitted_self_contained_page_is_ready() -> None:
    report = check_page(_assets(), entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()
    assert {check.name for check in report.checks} == {
        "entrypoint",
        "admitted-assets",
        "evidence-references",
        "reduced-motion",
    }


def test_a_page_that_requests_the_network_is_not_ready() -> None:
    assets = _assets()
    assets["index.html"] = (
        b"<!doctype html><link rel=stylesheet href='https://cdn.example/x.css'>"
        b"<script src='//cdn.example/x.js'></script>"
    )
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    failed = {check.name: check.detail for check in report.failures}
    assert "admitted-assets" in failed
    assert "https://cdn.example/x.css" in failed["admitted-assets"]
    assert "//cdn.example/x.js" in failed["admitted-assets"]


def test_a_page_that_escapes_its_revision_or_names_a_missing_asset_is_not_ready() -> None:
    escape = _assets()
    escape["index.html"] = b"<!doctype html><link rel=stylesheet href='../sibling/app.css'>"
    report = check_page(escape, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert "outside its own admitted assets" in report.failures[0].detail

    missing = _assets()
    missing["index.html"] = b"<!doctype html><img src='img/gone.svg'>"
    report = check_page(missing, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert "does not carry" in report.failures[0].detail


def test_an_evidence_reference_must_be_one_the_admitted_packet_carries() -> None:
    admitted = _assets()
    admitted["index.html"] = b"<!doctype html><p data-evidence='code-candidate:src/app.py'>x</p>"
    report = check_page(admitted, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()

    invented = _assets()
    invented["index.html"] = b"<!doctype html><p data-evidence='code-candidate:src/other.py'>x</p>"
    report = check_page(invented, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert "does not carry" in report.failures[0].detail


def test_a_page_that_moves_without_a_reduced_motion_rule_is_not_ready() -> None:
    moving = _assets()
    moving["app.css"] = b"@keyframes pulse{to{opacity:.4}} .a{animation:pulse 1s infinite}"
    report = check_page(moving, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert "reduced-motion" in {check.name for check in report.failures}

    cared = _assets()
    cared["app.css"] = (
        b".a{transition:opacity .2s}@media (prefers-reduced-motion: reduce){.a{transition:none}}"
    )
    assert check_page(cared, entrypoint="index.html", packet=packet(record())).ready

    loud = _assets()
    loud["index.html"] = b"<!doctype html><video autoplay src='img/plot.svg'></video>"
    report = check_page(loud, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert "autoplays" in report.failures[0].detail


def test_a_script_is_noted_not_failed_because_this_check_never_executes_the_page() -> None:
    assets = _assets(**{"app.js": b"document.body.append('ready')"})
    assets["index.html"] = b"<!doctype html><script src='app.js'></script><p>x</p>"
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()
    assert any("rule 8" in note and "not executed here" in note for note in report.notes)


def test_a_library_bundle_is_not_read_as_a_list_of_requests() -> None:
    """L104-B13: a shipped bundle may hold `src="..."` in a selector; that is not a request.

    Only documents name subresources in attributes. A JavaScript asset is never scanned for
    references, so a bundled library cannot be refused for strings it happens to contain.
    """

    bundle = (
        b"""var q = 'img[src="https://cdn.invalid/x.js"]';"""
        b"""var u = 'url(http://127.0.0.1:9/steal)';"""
        b"window.__kit = 1;"
    )
    assets = _assets(**{"kit.js": bundle})
    assets["index.html"] = b"<!doctype html><script src='kit.js'></script><p>x</p>"
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()


def test_a_stylesheet_url_that_leaves_the_revision_still_fails() -> None:
    """The scan is scoped by media type, not switched off: CSS url() is still a request."""

    assets = _assets(**{"kit.css": b"body{background:url(http://127.0.0.1:9/pixel.png)}"})
    assets["index.html"] = b"<!doctype html><link rel='stylesheet' href='kit.css'><p>x</p>"
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert any(
        check.name == "admitted-assets" and check.state == "failed" for check in report.checks
    )


def test_an_unquoted_evidence_reference_is_still_a_reference() -> None:
    """R104.14 (L104-A5): the attribute grammar allows an unquoted value; the gate must see it.

    The pre-repair gate matched quoted values only, so `data-evidence=not-admitted` cited nothing and
    a page that names an unadmitted reference was reported ready.
    """

    assets = _assets()
    assets["index.html"] = b"<!doctype html><p data-evidence=not-admitted>x</p>"
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert any(
        check.name == "evidence-references" and check.state == "failed" for check in report.checks
    )


def test_a_quoted_reference_outside_the_packet_is_refused_too() -> None:
    """The same gate, quoted: this is the control the unquoted case must behave like."""

    assets = _assets()
    assets["index.html"] = b'<!doctype html><p data-evidence="not-admitted">x</p>'
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
