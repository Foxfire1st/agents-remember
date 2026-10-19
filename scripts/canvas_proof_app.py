#!/usr/bin/env python3
"""Serve one hostile page for the browser isolation proof (MIK-R104 rules 4, 8, 10).

This is proof scaffolding, not product code. It writes a throwaway task, publishes one revision
whose page tries every escape the packet names, and serves that revision through the product's real
canvas routes and real policy -- with generated scripts **enabled**, because MIK-R104 v2 runs them in
the browser's sandbox and claims no stronger container (D331). ``scripts/canvas-isolation-proof.mjs``
drives it in a real browser.

The page is hostile *and* cooperative at once: its own inline script probes the authority it does
not have (cookie, storage, IndexedDB, parent DOM, top navigation, popup, worker, ``eval``) and
reports what happened, while its markup tries every subresource the policy must refuse (an external
stylesheet, script, image, frame, media, font; a host API image) and its assets carry the work it is
supposed to do (a stylesheet, a script, an SVG fetched in CORS mode). Its query string selects one
of three runs: ``observe`` (everything at once), ``selfnav`` (the page navigates its own frame) and
``timer`` (an interval the harness watches stop when the document is disposed). A fourth mode,
``matrix``, exercises the asset-authority contract of L104-B03 class by class: the page's own
relative script, stylesheet and image, then every foreign asset it can name -- the previous
revision of its own frame, a sibling frame's revision and the leaf's task report -- addressed
through the canvas prefix, plus the sibling frame's own live session when one is handed to it.

Two controlled sinks make a request effect measurable rather than inferred: the frame's host origin
(every request this app answers outside ``/proof`` is ledgered with its status, and ``/api/state`` is
a read-only stand-in for the dashboard's own read) and a second origin on a loopback port (every hit
is ledgered there). Zero hits at both is an observed absence, not a response count.

Run directly: ``PYTHONPATH=mcp/src python scripts/canvas_proof_app.py [port]``. It prints one JSON
object naming the addresses it bound, then serves until killed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import quote

import uvicorn
from agents_remember.application.review_evidence_packet import SideState, build_evidence_packet
from agents_remember.application.review_evidence_read import DEFAULT_READ_LIMITS
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    publish_revision,
)
from agents_remember.canvas.policy import (
    ASSET_PATH_PREFIX,
    CONTAINER_REQUIRED,
    FRAME_PERMISSIONS_POLICY,
    ORIGIN_ARRANGEMENT,
    IsolationProof,
    asset_headers,
    frame_sandbox,
    policy_digest,
    scripts_enabled,
    scripts_gap,
    shell_csp,
)
from agents_remember.canvas.proof_record import source_bindings
from agents_remember.canvas.sessions import SessionRegistry
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceMap,
)
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameLimits, FrameManifest
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.serving.canvas import CanvasHosts, register_canvas_routes
from agents_remember.serving.write_guard import RefuseCrossOriginWrites, RefuseForeignHosts
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

REPO = "agents-remember"
TASK = "canvas-isolation-proof"
LEAF = "260928-MIK-L104"
FRAME = "review-page"
#: The sibling frame of the same leaf: its assets are foreign to the hostile page (L104-B03).
SIBLING = "sibling-page"
#: A real leaf task report under the task root: the matrix tries to load it through the canvas prefix.
TASK_REPORT = f"task-reports/{LEAF}-worker.md"
AT = "2026-10-18T09:00:00Z"

#: Every request this app answers outside ``/proof``: (method, path, status).
LEDGER: list[tuple[str, str, int]] = []
#: Every hit the second origin received: the frame has no admitted path to it.
SINK_HITS: list[str] = []

#: The checks the run must carry, and the observation that satisfies each. The harness serves this
#: table to the browser driver, so a check cannot be dropped on one side without failing the run.
EXPECTED_CHECKS: dict[str, str] = {
    # The sandbox, as served: scripts on, subresources under the one admitted prefix.
    "servedSandbox": "sandbox allow-scripts",
    "servedScriptSrc": "admitted-prefix-and-inline",
    # The document's own origin, and the authority it does not have.
    "documentOrigin": "null",
    "inlineScript": "ran",
    "ownAssetScript": "ran",
    "externalScript": "blocked",
    "cspViolations": "refused-by-policy",
    "cookie": "blocked",
    "localStorage": "blocked",
    "sessionStorage": "blocked",
    "indexedDb": "blocked",
    "parentDom": "blocked",
    "opener": "null",
    "topNavigation": "blocked",
    "popup": "blocked",
    "worker": "blocked",
    "eval": "blocked",
    # Admitted assets still load -- including a CORS-mode fetch from an opaque origin.
    "admittedStyle": "applied",
    "admittedCorsImage": "loaded",
    # The asset-authority matrix (L104-B03): the page's own relative JS/CSS/image render, and every
    # governed load class refuses the assets of another revision of this frame, of a sibling frame
    # and of the leaf's task root.
    "ownScript": "loaded",
    "ownStyle": "loaded",
    "ownImage": "loaded",
    "foreignScript": "blocked",
    "foreignStyle": "blocked",
    "foreignImage": "blocked",
    "foreignFont": "blocked",
    "foreignMedia": "blocked",
    "foreignIframe": "blocked",
    "foreignWorker": "blocked",
    "foreignFetch": "blocked",
    "foreignTaskPath": "blocked",
    # Permissions: the served dictionary denies, and parses without a warning.
    "permissions": "denied",
    "permissionsPolicyWarnings": "0",
    # Request observations at the controlled sinks.
    "hostApiRequests": "0",
    "externalSinkRequests": "0",
    "navigationGuard": "absent",
    "formNavigation": "blocked",
    # The accepted channel of D331, observed and named rather than failed.
    "linkNavigation": "self-frame-only",
    "selfNavigation": "observed:navigated",
    "metaNavigation": "observed:script-inserted-meta-refresh-navigated",
    # Disposal: the document's timer stops with it, and a revoked channel serves nothing.
    "timerRuns": "active",
    "timerReleasedOnClose": "stopped",
    "revokedPage": "410",
    "revokedAsset": "410",
    # A direct open in a tab from a dashboard origin that owns a cookie, storage and a sentinel.
    "directOpenOrigin": "null",
    "directOpenScript": "ran",
    "directOpenCookie": "blocked",
    "directOpenStorage": "blocked",
    "directOpenOpener": "null",
    "directOpenDom": "blocked",
    "directOpenSentinel": "blocked",
    # The document's and an asset's served headers.
    "frameCorp": "same-origin",
    "assetCorp": "cross-origin",
    "assetCors": "*",
    # L104-B02: the frame navigates itself, the product's second-load reaction revokes the channel,
    # and the ready revision stays servable.
    "navigatedAway": "observed:navigated",
    "navigatedRevoke": "410",
    "navigatedMessage": "410",
    "navigatedRevoked": "revoked",
    "revisionRetained": "served",
    "browserProtocol": "accepted",
    "assetMatrix": "complete:35",
}

PROBE = Template(
    """<!doctype html>
<html><head><meta charset="utf-8"><title>hostile probe</title>
<link rel="stylesheet" href="probe.css">
<link rel="stylesheet" href="$sink/sink/style.css">
</head><body>
<h1 id="marker">isolation probe</h1>
<script>
window.__inlineRan = 1;
window.__probe = {};
window.addEventListener('message', function (event) {
  if (event.source === parent && event.data && event.data.schema === 'ar-canvas-binding/v1') {
    window.__hostBinding = event.data;
  }
});
window.__probeOk = function (name, run) {
  try { run(); window.__probe[name] = 'ok'; }
  catch (error) { window.__probe[name] = 'blocked:' + ((error && error.name) || 'error'); }
};
window.__probeValue = function (name, run) {
  try { window.__probe[name] = String(run()); }
  catch (error) { window.__probe[name] = 'blocked:' + ((error && error.name) || 'error'); }
};
window.__probe.framed = window.top !== window.self;
window.__probe.mode = new URLSearchParams(location.search).get('mode') || 'observe';
if (window.__probe.framed) {
  window.__probeOk('cookie', function () { document.cookie = 'probe=1; path=/'; });
  window.__probeOk('storage', function () { localStorage.setItem('probe', '1'); });
  window.__probeOk('sessionStorage', function () { sessionStorage.setItem('probe', '1'); });
  window.__probeOk('indexedDb', function () { indexedDB.open('probe'); });
  window.__probeOk('parentDom', function () { parent.document.body.tagName; });
  window.__probeOk('topNavigation', function () { top.location.href = '/api/state'; });
  window.__probeValue('popup', function () { return window.open('/api/state', 'probe-popup'); });
  window.__probeOk('worker', function () { new Worker('probe.js'); });
  window.__probeValue('eval', function () { return (0, eval)('1 + 1'); });
  window.__probeOk('cookieRead', function () { document.cookie; });
  window.__probe.cookieAfterSet = (function () {
    try { return document.cookie; } catch (error) { return 'blocked:' + ((error && error.name) || 'error'); }
  })();
}
if (window.__probe.framed && window.__probe.mode === 'selfnav') {
  setTimeout(function () { location.replace('/api/state'); }, 600);
}
if (window.__probe.framed && window.__probe.mode === 'meta') {
  var redirect = document.createElement('meta');
  redirect.httpEquiv = 'refresh';
  redirect.content = '0;url=/api/state';
  document.head.appendChild(redirect);
}
if (window.__probe.framed && window.__probe.mode === 'timer') {
  setInterval(function () {
    var tick = new Image();
    tick.src = '/api/state?tick=' + Date.now();
  }, 120);
}
// The asset-authority matrix (L104-B03). Every governed load class is attempted twice: against the
// page's own relative asset (the positive control) and against an asset that exists -- the previous
// revision of this frame, the sibling frame's revision, the leaf's task report -- addressed through
// the canvas prefix, which is the only address a served document holds. `&foreign=<session>` names
// the sibling frame's live session: its own prefix is measured separately, because a session value
// is a host-issued capability, not an address a document derives.
if (window.__probe.framed && window.__probe.mode === 'matrix') {
  window.__probe.matrix = {};
  (function () {
    var matrix = window.__probe.matrix;
    var params = new URLSearchParams(location.search);
    var foreign = (params.get('foreign') || '').split(',').filter(function (value) { return value; });
    var own = location.pathname;
    if (own.charAt(own.length - 1) !== '/') { own = own.slice(0, own.lastIndexOf('/') + 1); }
    var sibling = foreign.length ? '/canvas/page/' + foreign[0] + '/' : null;
    var task = params.get('task') || '$taskreport';
    var names = {
      revision: {
        script: 'foreign.js', style: 'foreign.css', image: 'foreign.svg', font: 'foreign.woff2',
        media: 'foreign.js', iframe: 'foreign.html', worker: 'foreign.js', fetch: 'foreign.js'
      },
      frame: {
        script: 'sibling.js', style: 'sibling.css', image: 'sibling.svg', font: 'sibling.woff2',
        media: 'sibling.js', iframe: 'sibling.html', worker: 'sibling.js', fetch: 'sibling.js'
      }
    };
    // The store admits no media suffix (review_frame_store._MEDIA_BY_SUFFIX), so no revision can
    // hold a video file: the media element points at the revision's script asset, and the class
    // measures the media load path, not the bytes' decodability.
    var violations = [];
    var pending = [];
    var targets = {};
    var final = false;
    var absolute = function (target) { return new URL(target, location.href).href.split('?')[0]; };
    window.__probe.violations = [];
    document.addEventListener('securitypolicyviolation', function (event) {
      var entry = {
        directive: String(event.effectiveDirective || ''),
        blocked: absolute(String(event.blockedURI || ''))
      };
      violations.push(entry);
      window.__probe.violations.push(entry);
    });
    var refused = function (address, directive) {
      return violations.some(function (entry) {
        if (entry.directive !== directive) { return false; }
        return entry.blocked === address || entry.blocked.indexOf(address) === 0;
      });
    };
    var record = function (key, value) {
      if (matrix[key] === undefined) { matrix[key] = value; }
    };
    var directives = {};
    var track = function (key, target, node, ok, bad, directive) {
      var address = absolute(target);
      targets[key] = address;
      directives[key] = directive;
      node.addEventListener(ok, function () { record(key, 'loaded'); });
      node.addEventListener(bad, function () {
        record(key, refused(address, directive) ? 'blocked:SecurityPolicyViolation' : 'blocked:error');
      });
      document.body.appendChild(node);
    };
    var attempt = function (kind, label, target) {
      var key = kind + ':' + label;
      var node;
      if (kind === 'script') {
        node = document.createElement('script');
        node.src = target;
        return track(key, target, node, 'load', 'error', 'script-src');
      }
      if (kind === 'style') {
        node = document.createElement('link');
        node.rel = 'stylesheet';
        node.href = target;
        return track(key, target, node, 'load', 'error', 'style-src');
      }
      if (kind === 'image') {
        node = document.createElement('img');
        node.alt = '';
        node.src = target;
        return track(key, target, node, 'load', 'error', 'img-src');
      }
      if (kind === 'font') {
        targets[key] = absolute(target);
        directives[key] = 'font-src';
        var font = new FontFace('probe-' + label, 'url(' + target + ')');
        pending.push(font.load().then(function () { record(key, 'loaded'); },
          function () { record(key, 'blocked:font-error'); }));
        return;
      }
      if (kind === 'media') {
        node = document.createElement('video');
        node.preload = 'metadata';
        node.muted = true;
        node.src = target;
        return track(key, target, node, 'loadedmetadata', 'error', 'media-src');
      }
      if (kind === 'iframe') {
        node = document.createElement('iframe');
        node.hidden = true;
        node.src = target;
        return track(key, target, node, 'load', 'error', 'frame-src');
      }
      if (kind === 'worker') {
        targets[key] = absolute(target);
        try {
          new Worker(target).terminate();
          record(key, 'loaded');
        } catch (error) {
          record(key, 'blocked:' + ((error && error.name) || 'error'));
        }
        return;
      }
      targets[key] = absolute(target);
      pending.push(fetch(target, { mode: 'cors', credentials: 'omit' }).then(
        function (response) { record(key, response.ok ? 'loaded' : 'blocked:http-' + response.status); },
        function (error) { record(key, 'blocked:' + ((error && error.name) || 'error')); }
      ));
    };
    attempt('script', 'own', 'probe.js');
    attempt('style', 'own', 'probe.css');
    attempt('image', 'own', 'probe.svg');
    ['script', 'style', 'image', 'font', 'media', 'iframe', 'worker', 'fetch'].forEach(function (kind) {
      attempt(kind, 'revision', own + names.revision[kind]);
      attempt(kind, 'frame', own + names.frame[kind]);
      attempt(kind, 'task', own + task);
      if (sibling) { attempt(kind, 'session', sibling + names.frame[kind]); }
    });
    var finish = function () {
      if (final) { return; }
      final = true;
      Object.keys(targets).forEach(function (key) {
        if (matrix[key] === undefined) {
          matrix[key] = refused(targets[key], directives[key])
            ? 'blocked:SecurityPolicyViolation'
            : 'blocked:no-signal';
        } else if (refused(targets[key], directives[key])) {
          // An iframe's load event also fires for the browser's blocked-frame error page.
          matrix[key] = 'blocked:SecurityPolicyViolation';
        }
      });
      window.__probe.matrixTargets = targets;
      window.__matrixDone = true;
    };
    Promise.allSettled(pending).then(function () { setTimeout(finish, 1500); });
    setTimeout(finish, 3000);
  })();
}
</script>
<script src="probe.js"></script>
<script src="$sink/sink/script.js"></script>
<img id="cors-image" src="probe.svg" crossorigin="anonymous" alt="">
<img src="$sink/sink/pixel.png" alt="">
<img src="/api/state" alt="">
<iframe src="$sink/sink/frame"></iframe>
<video src="$sink/sink/movie.mp4"></video>
<link rel="preload" as="font" href="$sink/sink/font.woff2" crossorigin>
<a id="host-link" href="/api/state">host api</a>
<form id="host-form" action="/api/state" method="post"><input name="x" value="1">
<button type="submit">go</button></form>
</body></html>
"""
)

WORKER = "window.__assetRan = 1;\n"
STYLE = "html,body{background:rgb(15,17,21);color:rgb(230,232,235)}\n"
#: An admitted image, fetched in CORS mode by the page: it measures the asset CORS answer.
IMAGE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    '<rect width="8" height="8" fill="#0f1115"/></svg>\n'
)


def _sink_server() -> tuple[ThreadingHTTPServer, int]:
    """The second origin: it answers every path and remembers that it was reached."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            SINK_HITS.append(self.path)
            body = b"/* sink */" if self.path.endswith(".css") else b"sink"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            """Silence: the ledger is the record, not stderr."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _record() -> ReviewTreeComparisonRecord:
    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=1,
        code_base=ReviewTreeSide(repository="/proof/code", tree="a" * 40),
        code_candidate=ReviewTreeSide(repository="/proof/code", tree="b" * 40),
        memory_base=ReviewTreeSide(repository="/proof/memory", tree="c" * 40),
        memory_candidate=ReviewTreeSide(repository="/proof/memory", tree="d" * 40),
        recorded_at=AT,
    )


def _evidence(comparison: ReviewTreeComparisonRecord) -> Any:
    claim = EvidenceClaim(
        claim_id="c1",
        authoring_revision="rev-1",
        classification="recorded-fact",
        subject="the probe's own file",
        applicability="this page",
        statement="the probe page exists at index.html",
        bases=(
            ClaimBasis(
                kind="tree-blob",
                reference="code-candidate:src/probe.py",
                state="present",
                side="code-candidate",
                tree=comparison.code_candidate.tree,
                path="src/probe.py",
            ),
        ),
    )
    return build_evidence_packet(
        comparison,
        repository_id=REPO,
        scope="the isolation proof",
        question="what can generated content reach?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the proof page"),
        limits=DEFAULT_READ_LIMITS,
        claims=(claim,),
        recorded_at=AT,
    )


def _published(task_root: Path, sink_origin: str) -> FrameManifest:
    """Publish the one hostile revision the run serves, and hand back its manifest."""

    scope = frame_scope(task_root, LEAF, FRAME)
    assets = {
        "index.html": PROBE.substitute(sink=sink_origin, taskreport=TASK_REPORT).encode(),
        "probe.css": STYLE.encode(),
        "probe.js": WORKER.encode(),
        "probe.svg": IMAGE.encode(),
    }
    begin_draft(
        scope,
        working_directory=task_root.parent / "authoring",
        request_id="req-proof",
        authoring=FrameAuthoring(request_id="req-proof", session_id="proof", started_at=AT),
        focus="the hostile probe",
        assets=assets,
        limits=FrameLimits(),
        now=AT,
    )
    publication = publish_revision(
        scope,
        request_id="req-proof",
        entrypoint="index.html",
        evidence=_evidence(_record()),
        supplemental=(),
        created_at=AT,
        limits=FrameLimits(),
    )
    if publication.manifest is None:
        raise SystemExit(f"the proof page did not publish: {publication.detail}")
    return publication.manifest


def _published_foreign(task_root: Path) -> dict[str, int]:
    """Publish the foreign revisions the matrix attempts to load (L104-B03).

    Two things must exist for a page to have something foreign to reach: a **second revision of the
    same frame** and a **first revision of a sibling frame** in the same leaf. Neither is linked from
    the hostile page; the run only proves that a load attempt at their assets is refused. Each
    revision carries every load class the matrix exercises (script, stylesheet, SVG, HTML, font,
    media) under a name that belongs to that revision alone, so an attempt names a real asset.
    """

    published: dict[str, int] = {}
    for label, frame, request, prefix in (
        ("revision", FRAME, "req-proof-second", "foreign"),
        ("sibling", SIBLING, "req-proof-sibling", "sibling"),
    ):
        scope = frame_scope(task_root, LEAF, frame)
        assets = {
            "index.html": b"<!doctype html><p>foreign</p>",
            f"{prefix}.js": f"window.__{prefix}Ran = 1;\n".encode(),
            f"{prefix}.css": b"html,body{background:rgb(1,2,3)}\n",
            f"{prefix}.svg": (
                b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">'
                b'<rect width="2" height="2" fill="#010203"/></svg>'
            ),
            f"{prefix}.html": b"<!doctype html><p>a foreign document</p>",
            f"{prefix}.woff2": b"wOF2\x00\x01\x00\x00foreign-font",
        }
        begin_draft(
            scope,
            working_directory=task_root.parent / f"authoring-{label}",
            request_id=request,
            authoring=FrameAuthoring(
                request_id=request, session_id=f"proof-{label}", started_at=AT
            ),
            focus=f"the {label} the matrix must not reach",
            assets=assets,
            limits=FrameLimits(),
            now=AT,
        )
        publication = publish_revision(
            scope,
            request_id=request,
            entrypoint="index.html",
            evidence=_evidence(_record()),
            supplemental=(),
            created_at=AT,
            limits=FrameLimits(),
        )
        if publication.manifest is None:
            raise SystemExit(f"the {label} did not publish: {publication.detail}")
        published[label] = publication.manifest.revision
    return published


def _harness_proof() -> IsolationProof:
    """The scripts-on switch this run needs, for this exact policy.

    It is a switch, not evidence: the browser run below writes the record that is evidence, and
    every real host loads that record instead. The host enables scripts only for a proven record,
    and the proof *is* what this run has not measured yet, so the harness composes the table the run
    is about to check -- and ``detail`` says so, so nothing here can be mistaken for a measurement.
    """

    table = tuple(EXPECTED_CHECKS.items())
    return IsolationProof(
        state="proven",
        record_gaps=(),
        policy_digest=policy_digest(),
        browser="chromium",
        browser_version="the run records the version",
        origin_arrangement=ORIGIN_ARRANGEMENT,
        required_checks=table,
        observed=tuple(f"{name}: {expected}" for name, expected in table),
        detail="harness switch: the browser run writes the proof record; this object is not one",
    )


def _shell(body: str, origin: str) -> HTMLResponse:
    """The trusted embedding: exactly the dashboard panel's attributes and policy."""

    nonce = "proofnonce"
    return HTMLResponse(
        body,
        headers={
            "Content-Security-Policy": shell_csp(origin, nonce),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _shell_document(
    session: str, mode: str, origin: str, *, scripts: bool, foreign: str = ""
) -> HTMLResponse:
    nonce = "proofnonce"
    run = mode if mode in {"observe", "selfnav", "timer", "meta", "matrix"} else "observe"
    query = f"?mode={run}"
    if run == "matrix" and foreign:
        query += f"&foreign={quote(foreign, safe='')}"
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>proof shell</title>
<style nonce="{nonce}">html,body{{margin:0;height:100%}}iframe{{width:100%;height:100%;border:0}}
</style></head><body>
<iframe id="frame" sandbox="{frame_sandbox(scripts=scripts)}"
 src="{ASSET_PATH_PREFIX}{session}/{query}"></iframe>
</body></html>"""
    return _shell(body, origin)


def _sentinel_document(origin: str) -> HTMLResponse:
    """A dashboard-origin page: it owns a cookie, storage and a DOM sentinel of its own."""

    nonce = "proofnonce"
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>sentinel</title>
<style nonce="{nonce}">body{{font:16px system-ui}}</style></head><body>
<h1>dashboard sentinel</h1>
<script nonce="{nonce}">
document.cookie = 'dashboard=secret; path=/';
localStorage.setItem('dashboard', 'secret');
window.__dashboardSentinel = 'secret';
</script>
</body></html>"""
    return _shell(body, origin)


class _RequestLedger:
    """The host-origin sink: method, path and status of every request outside ``/proof``."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or str(scope.get("path", "")).startswith("/proof"):
            await self._app(scope, receive, send)
            return
        status = {"code": 0}

        async def _send(message: Any) -> None:
            if message["type"] == "http.response.start":
                status["code"] = int(message["status"])
            await send(message)

        await self._app(scope, receive, _send)
        LEDGER.append((str(scope.get("method", "")), str(scope.get("path", "")), status["code"]))


def _artifact_view(manifest: FrameManifest) -> dict[str, object]:
    return {
        "entrypoint": manifest.entrypoint.path,
        "artifactDigest": manifest.artifact_digest,
        "assets": [
            {
                "path": asset.path,
                "digest": asset.digest,
                "bytes": asset.byte_size,
                "mediaType": asset.media_type,
            }
            for asset in manifest.assets
        ],
    }


def _proof_routes(
    app: FastAPI,
    manifest: FrameManifest,
    *,
    proof: IsolationProof,
    scripts: bool,
    foreign: dict[str, int],
) -> None:
    """The observation endpoints. They answer facts about this run, never a conclusion."""

    @app.get("/proof/policy")
    def policy() -> dict[str, object]:
        return {
            "policyDigest": policy_digest(),
            "originArrangement": ORIGIN_ARRANGEMENT,
            "assetPrefix": ASSET_PATH_PREFIX,
            "sandbox": frame_sandbox(scripts=scripts),
            "permissionsPolicy": FRAME_PERMISSIONS_POLICY,
            "assetCorp": asset_headers("text/css")["Cross-Origin-Resource-Policy"],
            "assetCors": asset_headers("text/css")["Access-Control-Allow-Origin"],
            "assetPath": "probe.css",
            "scriptPolicy": "enabled" if scripts else "disabled",
            "containerRequired": list(CONTAINER_REQUIRED),
            "requiredChecks": [
                {"name": name, "expected": expected} for name, expected in EXPECTED_CHECKS.items()
            ],
            "blockedReason": scripts_gap(proof),
            "foreign": {**foreign, "siblingFrame": SIBLING, "frame": FRAME},
            "taskReport": TASK_REPORT,
        }

    @app.get("/proof/panel")
    def panel() -> HTMLResponse:
        return HTMLResponse(
            '<div id="root"></div><script type="module" src="/proof/panel.js"></script>'
        )

    @app.get("/proof/panel.js")
    def panel_script() -> HTMLResponse:
        return HTMLResponse(
            Path(os.environ["AR_CANVAS_PANEL_BUNDLE"]).read_text(), media_type="text/javascript"
        )

    @app.get("/proof/panel-state")
    def panel_state() -> JSONResponse:
        comparison = manifest.binding.comparison_reference
        opening = {
            "state": "ready",
            "revision": manifest.revision,
            "detail": "actual published proof fixture revision",
            "entrypoint": manifest.entrypoint.path,
            "comparison": comparison,
        }
        state = {
            "state": "ok",
            "opening": opening,
            "staleness": {"state": "current"},
            "presenter": {**opening, "attempts": [], "live": None},
            "launch": {"state": "refused", "detail": "fixture never starts an author"},
            "design": {"source": "default", "available": True, "words": 0, "sha256": None},
            "policy": {"scripts": True},
        }
        return JSONResponse(
            {
                "state": state,
                "props": {
                    "repository": REPO,
                    "master": TASK,
                    "leaf": LEAF,
                    "comparison": {"reference": comparison, "number": 1},
                },
            }
        )

    @app.get("/proof/source")
    def source() -> JSONResponse:
        return JSONResponse(
            {
                "policyDigest": policy_digest(),
                **source_bindings(),
                "proofServer": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
        )

    @app.get("/proof/artifacts")
    def artifacts() -> JSONResponse:
        return JSONResponse(_artifact_view(manifest))

    @app.get("/proof/ledger")
    def ledger() -> JSONResponse:
        host = [f"{method} {path} -> {status}" for method, path, status in LEDGER]
        return JSONResponse({"host": host, "external": list(SINK_HITS)})

    @app.post("/proof/ledger/reset")
    def reset() -> JSONResponse:
        LEDGER.clear()
        SINK_HITS.clear()
        return JSONResponse({"state": "ok"})


def _page_routes(app: FastAPI, *, scripts: bool) -> None:
    @app.get("/api/state")
    def dashboard_read() -> JSONResponse:
        """A read-only stand-in for the dashboard's own projection read."""

        return JSONResponse({"state": "dashboard-projection", "detail": "a read, not an effect"})

    @app.get("/")
    def document(request: Request) -> HTMLResponse:
        origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if request.query_params.get("mode") == "sentinel":
            return _sentinel_document(origin)
        session = request.query_params.get("session")
        if not session:
            return HTMLResponse("a proof shell names its session", status_code=400)
        return _shell_document(
            session,
            request.query_params.get("mode", "observe"),
            origin,
            scripts=scripts,
            foreign=request.query_params.get("foreign", ""),
        )


def build(tmp: Path, sink_origin: str) -> tuple[FastAPI, CanvasHosts, FrameManifest]:
    task_root = tmp / "tasks" / REPO / TASK
    (task_root / "notes" / "reports").mkdir(parents=True)
    # A real leaf task report in the task root: the matrix attempts to load it through the canvas
    # prefix, so its refusal is about the store's address list, not about a path that does not exist.
    report = task_root / TASK_REPORT
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("the leaf's task report, which no canvas address serves\n", encoding="utf-8")
    config = McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp / "workspace",
        transcript_root=tmp / "transcripts",
        repositories={REPO: RepositoryScope(REPO, tmp / "repo")},
    )
    # The foreign revisions publish first, so the hostile page stays the newest revision of its
    # frame: a session opened without a revision then serves the page under test (L104-B03).
    foreign = _published_foreign(task_root)
    manifest = _published(task_root, sink_origin)
    proof = _harness_proof()
    hosts = CanvasHosts(config=config, sessions=SessionRegistry(), proof=proof)
    scripts = scripts_enabled(proof)
    app = FastAPI()
    register_canvas_routes(app, hosts)
    app.add_middleware(RefuseCrossOriginWrites)
    app.add_middleware(RefuseForeignHosts)
    _proof_routes(app, manifest, proof=proof, scripts=scripts, foreign=foreign)
    _page_routes(app, scripts=scripts)
    # Added last, so it is outermost and records the refusals too: a request stopped by the guard
    # is an observed effect, not an absence the ledger cannot see.
    app.add_middleware(_RequestLedger)
    return app, hosts, manifest


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    tmp = Path(tempfile.mkdtemp(prefix="ar-canvas-proof-", dir="/tmp"))
    _sink, sink_port = _sink_server()
    app, _hosts, _manifest = build(tmp, f"http://127.0.0.1:{sink_port}")
    print(json.dumps({"root": str(tmp), "host": "127.0.0.1", "port": port}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
