# The generated page's host, and the review presenter

MIK-R104 (isolated generated application host, **v2** on D331) and MIK-R105 (review presenter and
conversation) as built. This contributor reference states the supported contracts and their limits.

## The two surfaces

| Surface | Path | Who may call it | What it does |
| --- | --- | --- | --- |
| frame | `/canvas/page/{session}/` (document) and `/canvas/page/{session}/{asset}` | the framed document, after one bound session exists | serves the session's exact admitted revision with the policy headers; reads and sets no cookie |
| trusted API | `/api/canvas/...` | the dashboard (same origin) | opens sessions, carries frame messages, reads the saved-first state; the old authoring POSTs are retired and refuse by name |

The store (`application/review_frame_store.py`, MIK-R103) owns revision identity, containment,
digests, drafts, publication and reclamation. The host consumes it and never re-implements it. The
session is the only asset address: a caller cannot name a revision, a frame, a task report or a
filesystem path.

## Origin arrangement and policy

An admitted page is framed with `sandbox="allow-scripts"` and **never** `allow-same-origin`, and the
served document repeats the sandbox directive in its own policy, so the *document* is opaque whether
it is embedded, opened directly, or opened from a same-origin opener. It has no dashboard cookie, no
dashboard storage, no parent DOM, no `window.opener` and no trusted chat channel.

The served policy (`canvas/policy.py`, one digest over all of it):

- `sandbox` and `default-src 'none'`; script, style, image, font, media and manifest loads only
  under the selected `/canvas/page/{session}/` prefix on the host's own origin; `connect-src`, `frame-src`, `child-src`,
  `worker-src`, `object-src`, `form-action`, `base-uri` all `'none'`; `frame-ancestors 'self'`.
- `eval`/`new Function` are refused by the policy (no `'unsafe-eval'`); inline script and style are
  allowed because the admitted revision *is* the code.
- The document carries `Cross-Origin-Opener-Policy: same-origin` and
  `Cross-Origin-Resource-Policy: same-origin`; admitted assets carry
  `Cross-Origin-Resource-Policy: cross-origin` and `Access-Control-Allow-Origin: *`, because the
  loading document is an opaque origin and a font is fetched in CORS mode.
- A valid comma-separated `Permissions-Policy` denies camera, microphone, geolocation, clipboard,
  serial, USB, HID, fullscreen, payment and the rest; `X-Content-Type-Options: nosniff`;
  `Referrer-Policy: no-referrer`.
- An HTML or SVG asset served directly is additionally neutered with `default-src 'none'; sandbox`.
- Writes: `serving/write_guard.py` accepts state-changing browser requests only when
  `Sec-Fetch-Site` is `same-origin`. With no Fetch Metadata it checks `Origin`, refusing foreign or
  `null` origins. Headerless CLI/tool calls remain admitted. Every route validates the dashboard's
  own loopback Host name/address and port before dispatch.

D331 accepts the frame navigating itself: that channel gains no dashboard, AR, credential or
local-service authority, and the proof records the navigation as an observation rather than a failed
boundary. No further safeguard is engineered around it.

### Scripts are gated on the browser proof

`canvas/policy.py` reads `package_data/canvas/isolation-proof.json`. Scripts are enabled only when
the record is `proven` for the policy currently served: the same digest, the same origin
arrangement, no failed check, a named browser and version, and every name/value in the product's
fixed required catalog. Effective enforcement modules, packaged frontend, served policy, exact
artifact digests and complete observations are mandatory; deleting or contradicting them disables
scripts. A missing record, a stale digest, a partial
run or a failed check disables scripts, and the panel is told why by name.

The record is produced by `scripts/canvas-isolation-proof.mjs`, which serves a hostile page through
these routes (`scripts/canvas_proof_app.py`), embeds it exactly as the panel does, and observes
parent DOM, storage, cookies, host API, external fetch, beacon, websocket, worker, popup, top
navigation, form post, external and host images, nested frame, clipboard, `eval`, permissions, its
own navigation and disposal. **Re-run it after any change to the policy or the serving code**: the
digest covers the enforcement modules' own source, so an edited host serves an inert page instead of
silently trusting an old proof.

### What the browser does not contain

The limit table (`canvas/policy.LIMITS`) names every bound with what enforces it. Assets, messages,
message bytes, requests per session, active frames and session seconds are enforced by the host or
the browser. **Timers, heap/CPU and GPU are not**: no browser API caps them, and D331 does not build
a stronger container, so the record names them as the unproven boundary instead of claiming them.

## Readiness before a revision becomes ready (rule 14)

`canvas/readiness.py` checks the exact prospective revision before any ready pointer moves: the
entrypoint is one UTF-8 document, every reference stays inside this revision and names an admitted
asset, every `data-evidence` reference resolves in the admitted packet, and declared motion is paired
with a `prefers-reduced-motion: reduce` rule. `Presenter.publish` runs it and refuses with
`readiness-failed` and the named failing check; the last ready revision stays in place and the
attempt stays retryable. The check starts no browser and no model, and says nothing about how good
the explanation is.

## The host protocol (`canvas/protocol.py`)

One registered version (`1`), four operations from the page -- `ready`, `status`, `open-evidence`,
`request-explanation` -- and a closed payload per operation. Every envelope is strict about its
types, carries its type, version, request identity, session, revision and comparison, and the host
compares all three of the last to the session it created. A JSON depth bound and the byte ceiling
are checked before validation; every request is charged to the session's bucket before it is parsed.
Refusals are named: `malformed`, `oversized`, `wrong-version`, `unknown-op`, `foreign-session`,
`stale-revision`, `foreign-comparison`, `replayed`, `rate-limited`, `exhausted`, `revoked`,
`wrong-window`. An origin string, including `null`, authenticates nothing.

`open-evidence` is resolved by `canvas/evidence.py` against the revision's *own* admitted R102
packet: a claim basis resolved by typed identity, a claim, a collection item's address field or a
supplemental receipt with its own `read_state`. A reference carried by two claims without a claim
identity is refused as ambiguous; a basis that was not `present` answers `unavailable` with its own
limitation; nothing is ever re-pointed at today's record.

`request-explanation` records the question for the trusted conversation and answers
`startsModel: false`. It cannot start authoring; only the Generate control does.

## Sessions and limits

A session is host-created (`secrets.token_urlsafe(32)`), bound to frame, revision, comparison and
evidence digest. Four live at most; the least recently used is disposed first. Idle sessions expire
after 30 minutes. Replacement, publication of a successor, disposal and a scope change revoke the
channel: a revoked value answers `refused:revoked` (with a bounded tombstone keeping the reason), and
a channel whose revision is no longer the ready one refuses its display messages `stale-revision`
rather than applying them.

## The presenter (`canvas/presenter.py`)

A capability, not a fourth seat. One durable attempt record per explicit activation:

- **launch choices** go through the landed L71 user settings, catalog and host profile. The
  actual native receipt records applied choices and permission provenance. An unavailable
  provider/tool boundary refuses by name; production never selects the deterministic test composer.
- **design text** is the repository memory layer's `system/artifact-design.md` where it exists, else
  the shipped default (`package_data/canvas/artifact-design.md`), bounded to 2,000 words, its source
  and sha256 recorded on the attempt, and its **bytes delivered in the assignment** behind a
  non-authority fence: it steers the look and cannot change what the presenter may read, write or
  start, the rules of evidence, or the isolation.
- **capability text** is canonical `canvas/review-presenter.md`, copied to package data by the
  generator. The L101 loader enforces its 900-word ceiling and required wording; the attempt records
  baseline/addition/replacement provenance and delivered digest.
- **the attempt** records caller, comparison binding, evidence digest, design identity, instruction
  digest, execution/session/model, working folder, parent revision, predecessor, activity (<=200
  typed lines) and conversation (<=200 turns, each naming its comparison). Retention keeps the
  newest 32 attempts, reclaims their working folders, and a request ledger refuses a retry of a
  reclaimed request by name instead of starting a duplicate session.
- **writer confinement**: the only path into the task is the frame store's draft/publication. Assets
  are read from the attempt's own working folder
  (`<coordination>/presenter/<repo>/<task>/<leaf>/<frame>/<attempt>`); symlinked roots and
  ancestors, escapes, deep trees and ceiling overruns are refused before any bytes are read.
- **session operations**: every transition is a locked fresh read-modify-write, so a delayed host
  receipt cannot revive a cancelled attempt and publication races keep their real outcome. `resume`
  refuses without a recorded session, `cancel` keeps the last ready revision, `replace` validates
  its successor first, records its predecessor and starts from that revision's assets.
- **authoring execution** is the presenter's own port. A caller that wires a confined runner gets one
  attempt's prepared folder; a caller that wires none gets `UnconfiguredPresenterRunner`, which
  refuses by name. The native launch that attached the dedicated presenter tools was retired with
  `canvas/presenter_tools.py`'s standalone entry (MIK-R186 @ v2 Scope); the explainer's seat on the
  normal task server is the authoring path (MIK-R105 @ v4). The kept tools read immutable
  packet/assignment bytes, R102 source windows at the packet's four trees, and this attempt's own
  assets; writes are descriptor anchored in its assets folder. Documentation is separately bounded
  and labeled; it is never review evidence. Render returns Chromium diagnostics and a picture at
  1280×720. An installed build needs an explicitly configured browser runner/dependencies; missing
  browser capability refuses publication.

## The panel

`dashboard/src/panels/review/canvas/` sits above the review surface (never replacing it). Every read
and channel is keyed by the exact scope it serves -- repository, task, leaf, frame and the shown
comparison identity -- so a late answer for another scope is discarded, never shown. It reads the
saved state on mount, opens the saved revision under a fresh channel, and offers Generate /
Regenerate, Cancel, progress, the side conversation, the frame and the exact destination the page
asks to open. While an attempt is live it observes status every three seconds; saved-only opening
never polls, and no passive read starts/resumes a session. It revokes what it owns on unmount and a
channel whose revision or scope moved before opening its successor, and on a second load of the
frame (the page navigated itself) revokes that channel and re-opens the saved page.
`onOpenEvidence` navigates only where the mounted review owns a matching selector; otherwise the
exact destination is named and the panel says it cannot show it there.

## Supported boundaries and remaining limits

The pinned offline kit is described in `canvas-kit.md`; selecting an entry carries its required
notices into the immutable revision. A separate model-free witness executes all four entries under
an actual selected session/CSP. It is not a native authoring witness.

Native conversation, status, cancel, explicit resume and replacement name actual host identities.
An uncertain create repeats its immutable idempotent request. Passive native history reads refuse
for closed/archived sessions because the host would load them; the UI shows any previously collected
conversation with that limitation. Retention is bounded, not complete-history archival.

The first admitted document load receives `ar-canvas-binding/v1` from its mounted panel. Authored
pages mark bases with `data-evidence` and use the narrow `open-evidence` protocol. A second load
revokes instead of sending another binding. Exact destination rendering remains with the mounted
review's supported selectors (MIK-R106); unresolved destinations are named honestly.

A system-change request requires an admitted existing AR owner route. This capability has no AR
role messaging tool and never invents an owner or writes the fix itself. The author must name a
missing route/scope when none was admitted.

The browser's opaque sandbox does not enforce per-frame CPU, heap, timer or GPU ceilings. D331
accepts frame self-navigation; the mounted panel revokes the old channel and reopens saved output.
The browser proof's exact supported runtime and bound enforcement bytes govern any claim that
scripts are qualified. Missing/stale proof disables them.

## Checks

- Python: `mcp/.venv/bin/python -m pytest mcp/tests/test_canvas_host.py mcp/tests/test_canvas_presenter.py mcp/tests/test_canvas_serving.py mcp/tests/test_canvas_readiness.py`
- Browser proof: `node scripts/canvas-isolation-proof.mjs` (needs `dashboard/node_modules` and a
  Playwright Chromium), then confirm `canvas/policy.policy_digest()` equals the record's
  `policyDigest`.
- Dashboard: `npx vitest run src/panels/review/canvas`, `npx tsc -b`, `npx eslint src/panels/review/canvas`.
