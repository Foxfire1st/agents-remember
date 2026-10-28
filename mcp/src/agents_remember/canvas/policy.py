"""The isolation policy one admitted page executes under (MIK-R104 rules 2, 3, 4, 8).

One policy, stated once, so that the served bytes and every answer about them cannot drift apart:

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
  accepted channel, not something this module guards against. Scripts always run, and only ever
  inside this isolation: the document is served with ``sandbox="allow-scripts"`` and a
  ``script-src`` that admits nothing but the session's own admitted assets and inline script. No
  runtime switch can serve the page with less than that, or with more.

The finite limits (:data:`LIMITS`) name, for each bound, whether the browser enforces it or whether
only a stronger container could. Nothing here claims a measured resource guarantee an iframe cannot
give: CPU/GPU and heap are *named* as container work in :data:`CONTAINER_REQUIRED`, and a live
frame's timers have no enforced count. Those are named, not engineered around (rule 8, D331).
"""

from __future__ import annotations

__all__ = [
    "ASSET_HEADERS",
    "ASSET_PATH_PREFIX",
    "CONTAINER_REQUIRED",
    "FRAME_HEADERS",
    "FRAME_PERMISSIONS_POLICY",
    "LIMITS",
    "ORIGIN_ARRANGEMENT",
    "asset_headers",
    "document_headers",
    "frame_csp",
    "frame_sandbox",
    "shell_csp",
]

#: The design this policy implements: the arrangement the served headers produce.
ORIGIN_ARRANGEMENT = "opaque-sandbox-v1"

#: Where a live page and its assets are served: ``/canvas/page/{session}/`` and
#: ``/canvas/page/{session}/<asset>``. The CSP allowlist is this one prefix, so a relative reference
#: inside the document resolves inside it and a load of any other host path is refused by the
#: browser even before the server answers.
ASSET_PATH_PREFIX = "/canvas/page/"

#: The sandbox tokens. ``allow-same-origin`` is absent by design and must stay absent: with it the
#: framed document would share the dashboard's origin and the whole boundary would collapse.
_SANDBOX_SCRIPTS = "allow-scripts"

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


def frame_csp(origin: str, *, session: str) -> str:
    """The document policy one frame is served with.

    ``origin`` is the host origin (scheme, host and port) the request arrived on. Only paths below
    :data:`ASSET_PATH_PREFIX` on that origin are admissible for any subresource; everything the
    browser can reach over the network for data is closed. Script execution is part of the served
    document, never a separate decision: ``script-src`` admits the session's own assets and inline
    script, and no other origin.
    """

    allowed = _sources(origin, session)
    # The `sandbox` directive applies to the document however it is opened: inside the reviewer's
    # iframe, in a tab, or through a popup. Without it a direct open would be a same-origin dashboard
    # document and the whole authority separation would depend on the embedding caller (L104-B01).
    sandbox = f"sandbox {_SANDBOX_SCRIPTS}"
    return "; ".join(
        (
            sandbox,
            "default-src 'none'",
            f"script-src {allowed} 'unsafe-inline'",
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


def frame_sandbox() -> str:
    """The sandbox token set every served document carries. Never ``allow-same-origin``."""

    return _SANDBOX_SCRIPTS


def document_headers(origin: str, *, session: str) -> dict[str, str]:
    """Every header a frame document carries, including the policy and a missing-cache answer."""

    return {
        **FRAME_HEADERS,
        "Content-Security-Policy": frame_csp(origin, session=session),
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
