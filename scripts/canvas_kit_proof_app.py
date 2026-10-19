#!/usr/bin/env python3
"""Disposable selected-revision kit fixture using the real readiness, browser and R103 writer."""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import uvicorn
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    publish_revision,
)
from agents_remember.canvas.kit import kit_assets, kit_manifest
from agents_remember.canvas.prospective import BrowserProspectiveLoad
from agents_remember.canvas.readiness import check_page
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameLimits
from canvas_proof_app import AT, FRAME, LEAF, REPO, TASK, _evidence, _record, build

PAGE = b"""<!doctype html><title>All four pinned kit entries</title>
<style>body{font:18px system-ui;margin:24px}canvas{border:1px solid #888;max-width:100%}</style>
<h1>Selected immutable revision: all four kit entries</h1>
<p data-evidence="code-candidate:src/probe.py">Model-free fixture; no authoring/model claim.</p>
<section id="code"></section><button>OpenGenerativeUI control</button><br>
<canvas id="motion" width="480" height="270"></canvas>
<canvas id="vocabulary" width="480" height="270"></canvas>
<script type="module" src="page.js"></script>"""


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="ar-kit-proof-"))
    app, _hosts, _manifest = build(root, "http://127.0.0.1:1")
    entries = kit_manifest()
    assets = kit_assets([entry.entry for entry in entries])
    assets.update(
        {
            "index.html": PAGE,
            "page.js": Path(__file__).with_name("canvas-kit-page.mjs").read_bytes(),
        }
    )
    packet = _evidence(_record())
    readiness = check_page(assets, entrypoint="index.html", packet=packet)
    assert readiness.ready, readiness.diagnostics()
    prospective = BrowserProspectiveLoad().load(
        assets=assets, entrypoint="index.html", packet=packet
    )
    assert prospective.loaded, prospective.diagnostics()
    scope = frame_scope(root / "tasks" / REPO / TASK, LEAF, FRAME)
    begin_draft(
        scope,
        working_directory=root / "working",
        request_id="kit-proof",
        authoring=FrameAuthoring(
            request_id="kit-proof", session_id="model-free-kit-fixture", started_at=AT
        ),
        focus="all four functional kit entries",
        assets=assets,
        limits=FrameLimits(),
        now=AT,
    )
    publication = publish_revision(
        scope,
        request_id="kit-proof",
        entrypoint="index.html",
        evidence=packet,
        supplemental=(),
        created_at=AT,
        limits=FrameLimits(),
    )
    assert publication.published and publication.manifest
    manifest = publication.manifest

    @app.get("/proof/kit")
    def result() -> dict:
        return {
            "publication": manifest.model_dump(mode="json", by_alias=True),
            "prospective": asdict(prospective),
            "entries": [entry.id for entry in entries],
        }

    print(json.dumps({"root": str(root)}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")


if __name__ == "__main__":
    main()
