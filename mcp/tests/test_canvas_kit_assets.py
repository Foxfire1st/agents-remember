"""MIK-R104 rule 12: the kit files a revision carries, under the path a page names (L104-B13)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from agents_remember.canvas.kit import (
    KIT_CONTRACT_PREFIX,
    KIT_ROOT,
    KitRefused,
    kit_assets,
    kit_manifest,
)
from agents_remember.canvas.readiness import check_page
from canvas_host_test_support import packet, record


def test_the_shipped_manifest_names_every_entry_with_its_licence_and_size() -> None:
    """Rule 12: each entry names a pinned version, a licence and measured bytes."""

    entries = kit_manifest()
    assert {entry.id for entry in entries} >= {
        "shiki",
        "motion-canvas",
        "open-generative-ui",
        "ar-video",
    }
    for entry in entries:
        assert entry.version, entry.id
        assert entry.licence, entry.id
        assert entry.files, entry.id
        for item in entry.files:
            assert item.bytes > 0 and len(item.sha256) == 64, f"{entry.id}/{item.path}"


def test_a_named_kit_file_is_carried_under_its_contract_path() -> None:
    """A page names `kit/<entry>/<version>/<file>`; the revision carries exactly those bytes."""

    carried = kit_assets(["shiki/4.5.0/magic-move.css"])
    prefix = f"{KIT_CONTRACT_PREFIX}shiki/4.5.0/"
    assert (
        carried[f"{prefix}magic-move.css"] == (KIT_ROOT / "shiki/4.5.0/magic-move.css").read_bytes()
    )
    assert f"{prefix}LICENSE" in carried
    entry = next(item for item in kit_manifest() if item.id == "shiki")
    for notice in entry.notices:
        assert (
            carried[f"{KIT_CONTRACT_PREFIX}{notice.path}"] == (KIT_ROOT / notice.path).read_bytes()
        )


def test_an_unshipped_or_tampered_kit_file_is_refused_by_name(tmp_path: Path) -> None:
    """The kit cannot carry a file the manifest does not declare, nor bytes that do not match."""

    with pytest.raises(KitRefused) as unknown:
        kit_assets(["shiki/4.5.0/not-a-file.js"])
    assert unknown.value.reason == "kit-unknown-file"

    # A copy whose bytes differ from the manifest is refused before it becomes an asset.
    shutil_root = tmp_path / "kit"
    shutil.copytree(KIT_ROOT, shutil_root)
    manifest = json.loads((KIT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    (shutil_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (shutil_root / "shiki/4.5.0/magic-move.css").write_bytes(b"body{color:red}")
    with pytest.raises(KitRefused) as tampered:
        kit_assets(["shiki/4.5.0/magic-move.css"], root=shutil_root)
    assert tampered.value.reason in {"kit-size-mismatch", "kit-digest-mismatch"}


def test_a_page_that_carries_a_kit_file_is_ready_and_needs_no_network() -> None:
    """Rule 12 + rule 14: the revision renders from its own kit files with no outside request."""

    style = kit_assets(["shiki/4.5.0/magic-move.css"])
    assets = {
        "index.html": (
            b"<!doctype html><link rel='stylesheet' href='kit/shiki/4.5.0/magic-move.css'>"
            # The kit animates; a page that uses it owes the reduced-motion rule (rule 14).
            b"<style>@media (prefers-reduced-motion: reduce){*{animation:none!important;"
            b"transition:none!important}}</style><p>a highlighted snippet</p>"
        ),
        **style,
    }
    report = check_page(assets, entrypoint="index.html", packet=packet(record()))
    assert report.ready, report.diagnostics()
