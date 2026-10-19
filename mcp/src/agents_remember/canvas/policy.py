"""The isolation policy one admitted page executes under (MIK-R104 rules 2, 3, 4, 8, 10).

One policy, stated once, so that the served bytes, the browser proof and the runtime gate cannot
drift apart:

* **the origin arrangement is an opaque origin.** The page is framed with ``sandbox="allow-scripts"``
  and never ``allow-same-origin``, so its document has an opaque origin: no dashboard cookies, no
  dashboard storage, no parent DOM, no trusted chat channel. It is a *different* authority from the
  dashboard even though the host serves the bytes.
* **the resource policy is a closed allowlist.** ``default-src 'none'`` with script/style/image/font
  and media loads permitted only under the host's own admitted-asset path prefix, `connect-src
  'none'`` (fetch, XHR, beacon, websocket), ``frame-src 'none'`` (no nested application),
  ``object-src 'none'``, ``form-action 'none'``, ``base-uri 'none'``. The frame may not redefine
  this: it is a response header the generated asset bytes cannot touch.
* **credentials are absent by construction.** These routes read no cookie and set none, and the
  dashboard installs no cookie of its own; the policy additionally refuses cross-origin and
  ``Origin: null`` writes at the HTTP boundary (:mod:`agents_remember.serving.write_guard`), so a
  page that found a write route still cannot reach it with an opaque origin.
* **generated scripts run in the browser's sandbox** (MIK-R104 v2, D331). The browser is the
  execution environment: no stronger container is built, and the frame navigating itself is an
  accepted channel, not something this module guards against. ``scripts_enabled`` is the one gate,
  and it is a *proof* gate (rule 10): the shipped record must prove this exact policy -- the same
  digest and origin arrangement, a browser and version this policy is written for, no failed check,
  and every check the record itself names present in its own observations with the value it
  expected. A record that was not run in full enables nothing; a page with no record is served
  inert (``sandbox`` without ``allow-scripts``, ``script-src 'none'``).
  The proof is produced by ``scripts/canvas-isolation-proof.mjs`` against the real browser and the
  served headers; the record is the artifact that gate reads.

The finite limits (:data:`LIMITS`) name, for each bound, whether the browser enforces it or whether
only a stronger container could. Nothing here claims a measured resource guarantee an iframe cannot
give: CPU/GPU and heap are *named* as container work in :data:`CONTAINER_REQUIRED`, and a live
frame's timers have no enforced count. Those are named, not engineered around (rule 8, D331).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.canvas.proof_record import record_gaps, source_bindings

__all__ = [
    "ASSET_HEADERS",
    "ASSET_PATH_PREFIX",
    "CONTAINER_REQUIRED",
    "ENFORCEMENT_SOURCES",
    "FRAME_HEADERS",
    "FRAME_PERMISSIONS_POLICY",
    "ISOLATION_PROOF_NAME",
    "ISOLATION_PROOF_SCHEMA",
    "LIMITS",
    "ORIGIN_ARRANGEMENT",
    "POLICY_ID",
    "REQUIRED_CHECKS",
    "SUPPORTED_BROWSERS",
    "IsolationProof",
    "PolicyDigestMismatch",
    "asset_headers",
    "document_headers",
    "frame_csp",
    "frame_sandbox",
    "load_isolation_proof",
    "policy_digest",
    "proof_gaps",
    "scripts_enabled",
    "scripts_gap",
    "shell_csp",
]

#: The design this policy implements. A proof record names it, and the gate refuses any other.
ORIGIN_ARRANGEMENT = "opaque-sandbox-v1"
POLICY_ID = "ar-canvas-isolation/v1"

#: Where a live page and its assets are served: ``/canvas/page/{session}/`` and
#: ``/canvas/page/{session}/<asset>``. The CSP allowlist is this one prefix, so a relative reference
#: inside the document resolves inside it and a load of any other host path is refused by the
#: browser even before the server answers.
ASSET_PATH_PREFIX = "/canvas/page/"

#: The sandbox tokens. ``allow-same-origin`` is absent by design and must stay absent: with it the
#: framed document would share the dashboard's origin and the whole boundary would collapse.
_SANDBOX_SCRIPTS = "allow-scripts"
#: The empty token set for an unproven policy: no scripts, no same-origin, no forms, no popups.
_SANDBOX_INERT = ""

#: What the document may not do, in the browser's own vocabulary.
#:
#: A comma-separated dictionary of ``feature=()`` entries: an empty allowlist denies the feature to
#: every origin. Only names the browser recognizes as policy-controlled may be listed -- an
#: unrecognized name is not applied and Chromium reports it on the console, which is what made an
#: earlier 24-feature list read like a parse failure (L104-B04). ``battery`` was the one such name
#: measured against Chromium 148; the entry was removed rather than left as a claim with no effect.
FRAME_PERMISSIONS_POLICY = ", ".join(
    f"{feature}=()"
    for feature in (
        "accelerometer",
        "autoplay",
        "camera",
        "clipboard-read",
        "clipboard-write",
        "cross-origin-isolated",
        "display-capture",
        "encrypted-media",
        "fullscreen",
        "geolocation",
        "gyroscope",
        "hid",
        "idle-detection",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "picture-in-picture",
        "publickey-credentials-get",
        "screen-wake-lock",
        "serial",
        "usb",
        "xr-spatial-tracking",
    )
)

#: Response headers every frame document carries. ``Cache-Control`` is filled per request.
FRAME_HEADERS: dict[str, str] = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": FRAME_PERMISSIONS_POLICY,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}

#: The closed limit table (rule 8). ``enforced_by`` says what actually holds the bound.
LIMITS: dict[str, dict[str, object]] = {
    "assets-per-revision": {
        "value": 64,
        "unit": "files",
        "enforced_by": "browser",
        "where": "admission refuses a revision beyond the store's own file ceiling",
    },
    "bytes-per-revision": {
        "value": 16 * 1024 * 1024,
        "unit": "bytes",
        "enforced_by": "browser",
        "where": "publication refuses and one asset read is bounded before it is served",
    },
    "messages-per-second": {
        "value": 20,
        "unit": "messages",
        "enforced_by": "browser",
        "where": "the host protocol's token bucket refuses the excess without side effects",
    },
    "message-burst": {
        "value": 40,
        "unit": "messages",
        "enforced_by": "browser",
        "where": "the same bucket's burst ceiling",
    },
    "requests-per-session": {
        "value": 240,
        "unit": "requests",
        "enforced_by": "browser",
        "where": "after it the session answers refused:exhausted until it is reopened",
    },
    "message-bytes": {
        "value": 4096,
        "unit": "bytes",
        "enforced_by": "browser",
        "where": "the envelope is sized before it is parsed",
    },
    "active-frames": {
        "value": 4,
        "unit": "frames",
        "enforced_by": "browser",
        "where": "opening a fifth session disposes the least recently used one first",
    },
    "session-seconds": {
        "value": 1800,
        "unit": "seconds",
        "enforced_by": "browser",
        "where": "an idle session is revoked and its channel answers refused:revoked",
    },
    "workers": {
        "value": 0,
        "unit": "workers",
        "enforced_by": "browser",
        "where": "worker-src 'none' and child-src 'none' in the served policy",
    },
    "timers": {
        "value": 0,
        "unit": "enforced-limit",
        "enforced_by": "container",
        "where": "a browser page's timer count is not inspectable; hidden/disposed frames are dropped",
    },
    "heap-and-cpu": {
        "value": 0,
        "unit": "enforced-limit",
        "enforced_by": "container",
        "where": "no browser API caps a frame's heap or CPU; the frame process is the bound",
    },
    "gpu": {
        "value": 0,
        "unit": "enforced-limit",
        "enforced_by": "container",
        "where": "context is reachable only if a page asks; a GPU share needs a stronger container",
    },
}

#: Limits no browser can enforce, named so a reader is never told "bounded" when it is not.
CONTAINER_REQUIRED: tuple[str, ...] = (
    "timers",
    "heap-and-cpu",
    "gpu",
)

#: The proof record the gate reads. It is shipped beside the policy it proves.
#: The enforcing check catalog (L104-B05). This is the policy's own table, not the record's: it is
#: the set of checks the shipped runner must observe for scripts to be served at all. A record that
#: trims the table -- one matching pair out of forty-two -- proves nothing about this policy, so the
#: gate refuses it rather than accepting a self-selected scope. The expected value of each check is
#: the value the enforcement code is written to produce; a record may add checks, never lower one.
REQUIRED_CHECKS: tuple[tuple[str, str], ...] = (
    ("servedSandbox", "sandbox allow-scripts"),
    ("servedScriptSrc", "admitted-prefix-and-inline"),
    ("documentOrigin", "null"),
    ("inlineScript", "ran"),
    ("ownAssetScript", "ran"),
    ("externalScript", "blocked"),
    ("cspViolations", "refused-by-policy"),
    ("cookie", "blocked"),
    ("localStorage", "blocked"),
    ("sessionStorage", "blocked"),
    ("indexedDb", "blocked"),
    ("parentDom", "blocked"),
    ("opener", "null"),
    ("topNavigation", "blocked"),
    ("popup", "blocked"),
    ("worker", "blocked"),
    ("eval", "blocked"),
    ("admittedStyle", "applied"),
    ("admittedCorsImage", "loaded"),
    # The asset-authority matrix (L104-B03): a document's own relative script, stylesheet and image
    # render, and every governed load class refuses an asset of another revision of its frame, of a
    # sibling frame, and of the leaf's task root. Each class is attempted in a real browser through
    # the canvas prefix -- the only address a served document holds -- and recorded as refused.
    ("ownScript", "loaded"),
    ("ownStyle", "loaded"),
    ("ownImage", "loaded"),
    ("foreignScript", "blocked"),
    ("foreignStyle", "blocked"),
    ("foreignImage", "blocked"),
    ("foreignFont", "blocked"),
    ("foreignMedia", "blocked"),
    ("foreignIframe", "blocked"),
    ("foreignWorker", "blocked"),
    ("foreignFetch", "blocked"),
    ("foreignTaskPath", "blocked"),
    ("permissions", "denied"),
    ("permissionsPolicyWarnings", "0"),
    ("hostApiRequests", "0"),
    ("externalSinkRequests", "0"),
    ("navigationGuard", "absent"),
    ("formNavigation", "blocked"),
    ("linkNavigation", "self-frame-only"),
    ("selfNavigation", "observed:navigated"),
    ("metaNavigation", "observed:script-inserted-meta-refresh-navigated"),
    ("timerRuns", "active"),
    ("timerReleasedOnClose", "stopped"),
    ("revokedPage", "410"),
    ("revokedAsset", "410"),
    ("directOpenOrigin", "null"),
    ("directOpenScript", "ran"),
    ("directOpenCookie", "blocked"),
    ("directOpenStorage", "blocked"),
    ("directOpenOpener", "null"),
    ("directOpenDom", "blocked"),
    ("directOpenSentinel", "blocked"),
    ("frameCorp", "same-origin"),
    ("assetCorp", "cross-origin"),
    ("assetCors", "*"),
    # L104-B02: the frame's own navigation is the accepted channel (D331); the product's second-load
    # reaction revokes the old channel, and the ready revision outlives it.
    ("navigatedAway", "observed:navigated"),
    ("navigatedRevoke", "410"),
    ("navigatedMessage", "410"),
    ("navigatedRevoked", "revoked"),
    ("revisionRetained", "served"),
    ("browserProtocol", "accepted"),
    ("assetMatrix", "complete:35"),
)

ISOLATION_PROOF_NAME = "isolation-proof.json"
ISOLATION_PROOF_SCHEMA = "ar-canvas-isolation-proof/v1"

#: The browsers this policy is written and measured for. A proof from any other engine proves a
#: different browser than the one this product runs in, so it enables nothing (rule 10).
SUPPORTED_BROWSERS: frozenset[str] = frozenset(
    {"chromium", "chrome", "firefox", "safari", "webkit"}
)


class PolicyDigestMismatch(ValueError):
    """A proof record was produced for a policy other than the one now served."""


@dataclass(frozen=True)
class IsolationProof:
    """The shipped browser proof, as far as the runtime gate reads it.

    ``state`` is ``proven`` only when the proof ran every hostile check against this exact policy;
    ``failed`` carries the checks that did not hold, and ``absent`` means no proof was shipped.
    ``required_checks`` is the record's own table of what it set out to check, with the value it
    expected, and ``observed`` the values the run actually saw. A record whose checks were not all
    run is not a proof: :func:`proof_gaps` names what it lacks and :func:`scripts_enabled` refuses
    it.
    """

    state: Literal["proven", "failed", "absent"]
    policy_digest: str | None = None
    browser: str | None = None
    browser_version: str | None = None
    origin_arrangement: str | None = None
    failed_checks: tuple[str, ...] = ()
    observed: tuple[str, ...] = ()
    required_checks: tuple[tuple[str, str], ...] = ()
    detail: str = ""
    record_gaps: tuple[str, ...] = ("no served/source/artifact observations",)

    @property
    def proven(self) -> bool:
        """Whether this record proves every clause of the policy now served."""

        return not proof_gaps(self)


#: The modules whose bytes enforce the policy. A proof that does not cover them would certify a
#: policy the product no longer runs (L104-B05).
ENFORCEMENT_SOURCES = (
    "agents_remember.canvas.policy",
    "agents_remember.serving.canvas",
    "agents_remember.serving.write_guard",
    "agents_remember.serving.app",
    "agents_remember.canvas.sessions",
    "agents_remember.canvas.document",
    "agents_remember.canvas.protocol",
    "agents_remember.canvas.evidence",
    "agents_remember.canvas.proof_record",
    "agents_remember.application.review_frame_store",
    "agents_remember.application.review_frame_read",
    "agents_remember.models.knowledge.review_frame",
)


def _enforcement_digest() -> str:
    """sha256 over the enforcement modules' own source bytes, imported from the running package."""

    return hashlib.sha256(json.dumps(source_bindings(), sort_keys=True).encode()).hexdigest()


def _policy_payload() -> dict[str, object]:
    """The exact facts a proof is bound to. Any edit here invalidates every old proof."""

    return {
        "policy": POLICY_ID,
        "enforcementDigest": _enforcement_digest(),
        "originArrangement": ORIGIN_ARRANGEMENT,
        "sandboxScripts": _SANDBOX_SCRIPTS,
        "sandboxInert": _SANDBOX_INERT,
        "assetPrefix": ASSET_PATH_PREFIX,
        "permissions": FRAME_PERMISSIONS_POLICY,
        "headers": FRAME_HEADERS,
        "limits": LIMITS,
        "containerRequired": list(CONTAINER_REQUIRED),
        "supportedBrowsers": sorted(SUPPORTED_BROWSERS),
    }


def policy_digest() -> str:
    """sha256 of the canonical policy, the value a proof record must name."""

    encoded = json.dumps(_policy_payload(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sources(origin: str, session: str) -> str:
    """The one origin, restricted to admitted assets. Paths are part of a CSP source expression."""

    # A live sibling session is another asset authority, even on the same HTTP origin.
    # Session IDs are minted by the registry, never supplied by the generated document.
    if not session or any(not (char.isalnum() or char in "-_") for char in session):
        raise ValueError("a document policy needs its exact session ID")
    return f"{origin}{ASSET_PATH_PREFIX}{session}/"


def _document_tail() -> str:
    """The trailing directives: this document is only ever framed by its own host."""

    return "frame-ancestors 'self'; base-uri 'none'; form-action 'none'"


def frame_csp(origin: str, *, session: str, scripts: bool) -> str:
    """The document policy one frame is served with.

    ``origin`` is the host origin (scheme, host and port) the request arrived on. Only paths below
    :data:`ASSET_PATH_PREFIX` on that origin are admissible for any subresource; everything the
    browser can reach over the network for data is closed.
    """

    allowed = _sources(origin, session)
    script = f"script-src {allowed} 'unsafe-inline'" if scripts else "script-src 'none'"
    # The `sandbox` directive applies to the document however it is opened: inside the reviewer's
    # iframe, in a tab, or through a popup. Without it a direct open would be a same-origin dashboard
    # document and the whole authority separation would depend on the embedding caller (L104-B01).
    sandbox = "sandbox allow-scripts" if scripts else "sandbox"
    return "; ".join(
        (
            sandbox,
            "default-src 'none'",
            script,
            f"style-src {allowed} 'unsafe-inline'",
            f"img-src {allowed} data:",
            f"font-src {allowed} data:",
            f"media-src {allowed}",
            f"manifest-src {allowed}",
            "connect-src 'none'",
            "frame-src 'none'",
            "child-src 'none'",
            "worker-src 'none'",
            "object-src 'none'",
            # ``frame-ancestors``, ``base-uri`` and ``form-action`` arrive with the tail; a directive
            # that appears twice is ignored after its first occurrence, so they are stated once.
            _document_tail(),
        )
    )


def shell_csp(origin: str, nonce: str) -> str:
    """The trusted shell's policy: its own script and style only, one framed asset origin."""

    return "; ".join(
        (
            "default-src 'none'",
            f"script-src 'nonce-{nonce}'",
            f"style-src 'nonce-{nonce}'",
            f"frame-src {origin}",
            "connect-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'self'",
        )
    )


def frame_sandbox(*, scripts: bool) -> str:
    """The sandbox token set. Never carries ``allow-same-origin``."""

    return _SANDBOX_SCRIPTS if scripts else _SANDBOX_INERT


def document_headers(origin: str, *, session: str, scripts: bool) -> dict[str, str]:
    """Every header a frame document carries, including the policy and a missing-cache answer."""

    return {
        **FRAME_HEADERS,
        "Content-Security-Policy": frame_csp(origin, session=session, scripts=scripts),
        "Cache-Control": "no-store",
    }


#: The headers an admitted asset carries. ``Cross-Origin-Resource-Policy`` is ``cross-origin``
#: because the document that loads it is an opaque origin: a ``same-origin`` CORP there reads the
#: frame as a foreign reader and refuses the page its own CSS, script and images (L104-B03).
#: ``Access-Control-Allow-Origin: *`` answers the same fact for a *CORS-mode* fetch -- a font, an
#: image with ``crossorigin``, an SVG -- which an opaque-origin document makes cross-origin even on
#: the host's own origin; without it the browser refuses the load with "No
#: 'Access-Control-Allow-Origin' header is present" (measured). ``*`` carries no credential: these
#: routes read no cookie, and a wildcard forbids credentials by definition. It is not a wider grant
#: either -- the asset is reachable only through a live session's unguessable value, and a document
#: served from this prefix is still opaque however it is opened.
ASSET_HEADERS: dict[str, str] = {
    "Access-Control-Allow-Origin": "*",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def asset_headers(media_type: str) -> dict[str, str]:
    """Every header an admitted revision asset carries.

    An asset is never a document that may execute on its own: it is served ``nosniff``, is not
    cacheable by a shared cache, and an SVG or HTML asset is additionally neutered with a
    ``sandbox`` policy so a direct open cannot run its script.
    """

    headers = {
        **ASSET_HEADERS,
        "Cache-Control": "private, max-age=31536000, immutable",
        "Content-Type": media_type,
    }
    if media_type in {"text/html", "image/svg+xml"}:
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return headers


def _check_entry(entry: object) -> tuple[str, str] | None:
    """One ``{name, expected}`` row of the record's check table, or ``None`` when it is not one."""

    if not isinstance(entry, dict):
        return None
    name, expected = entry.get("name"), entry.get("expected")
    if not isinstance(name, str) or not isinstance(expected, str) or not name:
        return None
    return name, expected


def _required_checks(entries: object) -> tuple[tuple[str, str], ...]:
    """The record's own check table; a table that cannot be read covers nothing at all."""

    if not isinstance(entries, (list, tuple)):
        return ()
    parsed: list[tuple[str, str]] = []
    for entry in entries:
        row = _check_entry(entry)
        if row is None:
            return ()
        parsed.append(row)
    return tuple(parsed)


def _observed_values(observed: tuple[str, ...]) -> dict[str, str]:
    """The record's observations as name -> value; the record writes them as ``name: value``."""

    values: dict[str, str] = {}
    for line in observed:
        name, separator, value = line.partition(": ")
        if separator:
            values[name.strip()] = value.strip()
    return values


def _uncovered(required: tuple[tuple[str, str], ...], observed: tuple[str, ...]) -> tuple[str, ...]:
    """Every check the record required that it did not observe with the value it expected."""

    values = _observed_values(observed)
    missing: list[str] = []
    for name, expected in required:
        value = values.get(name)
        if value is None or not value.startswith(expected):
            missing.append(f"{name} (expected {expected!r}, observed {value!r})")
    return tuple(missing)


def _catalog_gaps(proof: IsolationProof) -> list[str]:
    """The enforcing catalog judged against the record's own table and observations (L104-B05).

    A record may add checks, never narrow the set it is judged by and never lower one of its
    expected values: the catalog is the policy's, so a record that trims it proves nothing.
    """

    declared = dict(proof.required_checks)
    gaps: list[str] = []
    omitted = [name for name, _ in REQUIRED_CHECKS if name not in declared]
    if omitted:
        listed = ", ".join(omitted[:8]) + ("…" if len(omitted) > 8 else "")
        gaps.append(
            f"the record omits {len(omitted)} of the {len(REQUIRED_CHECKS)} enforcing checks: "
            f"{listed}"
        )
    lowered = [
        f"{name} (expected {expected!r}, record declares {declared[name]!r})"
        for name, expected in REQUIRED_CHECKS
        if name in declared and declared[name] != expected
    ]
    if lowered:
        gaps.append(
            f"{len(lowered)} enforcing check(s) were declared more weakly: {', '.join(lowered)}"
        )
    missing = _uncovered(REQUIRED_CHECKS, proof.observed)
    if missing:
        gaps.append(
            f"{len(missing)} required check(s) were not observed as expected: {', '.join(missing)}"
        )
    return gaps


def proof_gaps(proof: IsolationProof) -> tuple[str, ...]:
    """Every clause of the proof gate this record does not meet, named.

    The gate is rule 10's: a browser proof precedes enablement, so the record must be a *proven*
    record of *this* policy, run in a browser this policy is written for, with nothing failed, and
    with every check it set out to run present in its own observations. An empty table is not
    coverage -- a record that named no checks proved nothing.
    """

    gaps: list[str] = list(proof.record_gaps)
    if proof.state != "proven":
        gaps.append(f"the record's state is {proof.state!r}")
    if proof.policy_digest != policy_digest():
        gaps.append("the record names another policy digest")
    if proof.origin_arrangement != ORIGIN_ARRANGEMENT:
        gaps.append(f"the record names the origin arrangement {proof.origin_arrangement!r}")
    if (proof.browser or "").lower() not in SUPPORTED_BROWSERS:
        gaps.append(f"the record names no browser this policy is written for ({proof.browser!r})")
    if not (proof.browser_version or "").strip():
        gaps.append("the record names no browser version")
    if proof.failed_checks:
        gaps.append(f"{len(proof.failed_checks)} check(s) failed: {', '.join(proof.failed_checks)}")
    if not proof.required_checks:
        gaps.append("the record names no required checks, so it proves nothing")
    else:
        gaps.extend(_catalog_gaps(proof))
    return tuple(gaps)


def load_isolation_proof(bundle_root: Path | None) -> IsolationProof:
    """Read the shipped proof record, refusing malformed JSON rather than assuming proof."""

    if bundle_root is None:
        return IsolationProof(state="absent", detail="no proof record ships")
    path = bundle_root / ISOLATION_PROOF_NAME
    if not path.is_file():
        return IsolationProof(state="absent", detail=f"no proof record at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return IsolationProof(state="absent", detail=f"the proof record could not be read: {error}")
    if not isinstance(payload, dict) or payload.get("schema") != ISOLATION_PROOF_SCHEMA:
        return IsolationProof(state="absent", detail="the proof record names another schema")
    return IsolationProof(
        record_gaps=record_gaps(payload),
        state=payload.get("state", "absent"),
        policy_digest=payload.get("policyDigest"),
        browser=payload.get("browser"),
        browser_version=payload.get("browserVersion"),
        origin_arrangement=payload.get("originArrangement"),
        failed_checks=tuple(payload.get("failedChecks", ())),
        observed=tuple(payload.get("observed", ())),
        required_checks=_required_checks(payload.get("requiredChecks", ())),
        detail=str(payload.get("detail", "")),
    )


def scripts_enabled(proof: IsolationProof) -> bool:
    """Whether generated script execution may be served for this policy (rule 10)."""

    return proof.proven


def scripts_gap(proof: IsolationProof) -> str:
    """The named reasons generated scripts are served disabled, or ``""`` when they may run."""

    gaps = proof_gaps(proof)
    if not gaps:
        return ""
    return "generated scripts stay disabled (MIK-R104 rule 10): " + "; ".join(gaps)
