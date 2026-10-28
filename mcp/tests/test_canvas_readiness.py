"""The model-free readiness check of one prospective revision (MIK-R104 rule 14).

Each case protects a distinct way a page can fail to load as itself: reaching the network, naming an
asset that is not the revision's, citing evidence the packet does not carry, or moving without a
reduced-motion rule. The inert-script note is asserted separately because scripts are disabled by
policy (rule 8), not by a page defect.
"""

from __future__ import annotations

from agents_remember.canvas.readiness import check_page
from agents_remember.models.knowledge.review_evidence import ClaimBasis, EvidenceClaim
from canvas_host_test_support import default_claims, packet, record

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
        "part-map",
        "reduced-motion",
    }
    # A page without parts is one part holding every claim; its staleness is whole-page.
    assert report.part_map is not None
    assert report.part_map.implicit
    assert [entry.part_id for entry in report.part_map.parts] == ["page"]

    # No size or count bounds a page (MIK-R211 v4 rule 3, D567): past the old 4 MiB page and 400
    # references, a page that keeps to its own assets is still ready.
    large = _assets()
    large["index.html"] = (
        PAGE + b"<img src='img/plot.svg'>" * 450 + b"<!--" + b"x" * (4 * 1024 * 1024) + b"-->"
    )
    assert check_page(large, entrypoint="index.html", packet=packet(record())).ready


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
    missing["index.html"] = b"<!doctype html>" + b"".join(
        f"<img src='img/gone-{index}.svg'>".encode() for index in range(7)
    )
    report = check_page(missing, entrypoint="index.html", packet=packet(record()))
    assert not report.ready
    assert "does not carry" in report.failures[0].detail
    assert all(f"gone-{index}.svg" in report.failures[0].detail for index in range(7))  # all named


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


def test_readiness_requires_resolving_claim_bases_and_unique_part_ownership() -> None:
    baseless = EvidenceClaim(
        claim_id="c-baseless",
        authoring_revision="rev-1",
        classification="unresolved-question",
        subject="open question",
        applicability="this comparison",
        statement="what happens?",
        bases=(),
        limit="not yet established",
    )
    assets = _assets(
        **{
            "index.html": (
                b"<!doctype html><div data-part='p1'>"
                b"<span data-evidence-claim='c-baseless'>x</span></div>"
            )
        }
    )
    no_basis_report = check_page(
        assets, entrypoint="index.html", packet=packet(record(), claims=(baseless,))
    )
    assert not no_basis_report.ready
    assert "no basis" in {c.name: c.detail for c in no_basis_report.failures}["part-map"]

    wrong_tree = EvidenceClaim(
        claim_id="c-wrong-tree",
        authoring_revision="rev-1",
        classification="recorded-fact",
        subject="wrong tree",
        applicability="this comparison",
        statement="cites foreign tree",
        bases=(
            ClaimBasis(
                kind="tree-blob",
                reference="code-candidate:src/app.py",
                state="present",
                side="code-candidate",
                tree="f" * 40,
                path="src/app.py",
            ),
        ),
    )
    assets["index.html"] = (
        b"<!doctype html><div data-part='p1'>"
        b"<span data-evidence-claim='c-wrong-tree'>x</span></div>"
    )
    bad_tree_report = check_page(
        assets, entrypoint="index.html", packet=packet(record(), claims=(wrong_tree,))
    )
    assert not bad_tree_report.ready
    assert "does not resolve" in {c.name: c.detail for c in bad_tree_report.failures}["part-map"]


def test_claim_ids_and_the_legacy_reference_check_stay_separate() -> None:
    """E11: ``data-evidence-claim`` holds claim ids; ``data-evidence`` keeps its own resolution."""
    admitted = _assets()
    admitted["index.html"] = (
        b"<!doctype html><div data-part='p1'>"
        b"<span data-evidence-claim='c-present' data-evidence='code-candidate:src/app.py'>x</span>"
        b"<span data-evidence-claim='c-gone'>y</span>"
        b"</div>"
    )
    report = check_page(admitted, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()
    assert report.part_map is not None and report.part_map.part_of("c-present") == "p1"
    names = {check.name: check.state for check in report.checks}
    assert names["part-map"] == "passed"
    assert names["evidence-references"] == "passed"

    # The same marks with an invented claim id fail only the part-map check: the legacy
    # reference resolution is its own check and stays resolved.
    invented = _assets()
    invented["index.html"] = (
        b"<!doctype html><div data-part='p1'>"
        b"<span data-evidence-claim='c-invented' data-evidence='code-candidate:src/app.py'>x</span>"
        b"</div>"
    )
    refused = check_page(invented, entrypoint="index.html", packet=packet(record()))
    assert not refused.ready
    failed = {check.name: check.detail for check in refused.failures}
    assert "claim id" in failed["part-map"]
    assert "evidence-references" not in failed


def test_a_page_without_parts_is_ready_as_one_implicit_part_with_real_claim_ids() -> None:
    """E11: an implicit page is one part holding every real claim id, and nothing invented."""
    implicit = _assets()
    implicit["index.html"] = (
        b"<!doctype html><span data-evidence-claim='c-present'>x</span>"
        b"<span data-evidence-claim='c-gone'>y</span>"
    )
    report = check_page(implicit, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()
    assert report.part_map is not None and report.part_map.implicit
    assert set(report.part_map.claims()) == {claim.claim_id for claim in default_claims(record())}
