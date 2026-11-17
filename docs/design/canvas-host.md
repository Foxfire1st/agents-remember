# The canvas host, and the review-presenter capability

This contributor reference states the contracts of two parts and their limits: the
[canvas host](../concepts.md#canvas-host) that serves an explainer's page, and the `review-presenter`
capability, by whose text (`canvas/review-presenter.md`) and settings (the role row
`orchestration.roles.review-presenter`) the [explainer](../concepts.md#explainer) authors the page. A
module path without a root is relative to `mcp/src/agents_remember/`.
[The review page and the explainer](../guides/review-page-and-explainer.md) describes the same
parts for the user.

## The two surfaces

| Surface | Path | Who may call it | What it does |
| --- | --- | --- | --- |
| page | `/canvas/page/{session}/` (document) and `/canvas/page/{session}/{asset}` | the document in the sandboxed `iframe`, after the dashboard has opened a session for it ([Sessions and limits](#sessions-and-limits)) | serves the session's exact admitted [revision](../concepts.md#revision) with the policy headers; reads and sets no cookie |
| trusted API | `/api/canvas/...` | the dashboard (same origin) | opens sessions, carries frame messages, revokes sessions and reads the saved-first state of a frame |

The explainer writes a page in the explainer's workspace and publishes it with `render`, one of
the explainer's tools.
The dashboard sends the user's request to the explainer through
`POST /api/explainer/<repository>/<master folder>/<task id>/build`; when the task has no explainer,
the call first builds the explainer's workspace and starts the explainer.

The store (`application/review_frame_store.py`) owns revision identity, containment, digests,
drafts, publication and reclamation. A frame is the stored object of one explanation: its immutable
revisions, its drafts and its reclamation receipts. A draft is the store's record of a page that an
authoring turn writes; publication turns it into a revision. Containment means that every path resolves inside the task's own
durable area, and a symbolic link is not followed out of it. A digest is the SHA-256 of an asset or
of a manifest, checked at every read. A reclamation removes the bytes of a revision or a draft and
leaves a receipt that says why. An admitted revision is one that the store took in at publication;
it never changes. The ready revision is the one that the frame's ready pointer names. The canvas
host consumes the store and never re-implements it. The session is the only asset address: a
caller cannot name a revision, a frame, a task report or a filesystem path.

## Origin arrangement and policy

An admitted page is framed with `sandbox="allow-scripts"` and **never** `allow-same-origin`, and the
served document repeats the sandbox directive in its own policy, so the *document* is opaque whether
it is embedded, opened directly, or opened from a same-origin opener. It has no dashboard cookie, no
dashboard storage, no parent DOM, no `window.opener` and no trusted chat channel.

The served policy (`canvas/policy.py`):

- `sandbox` and `default-src 'none'`; script, style, image, font, media and manifest loads only
  under the selected `/canvas/page/{session}/` prefix on the canvas host's own origin; `connect-src`, `frame-src`, `child-src`,
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
  `null` origins. Headerless CLI/tool calls remain admitted. Every route checks before dispatch
  that the `Host` header names `localhost`, `127.0.0.1`, `::1` or the address the dashboard bound,
  with the port it bound: any other name or port gets status 403 with the reason `foreign-host`,
  and a request without the header is admitted.

The sandboxed `iframe` may navigate itself. That navigation gains no dashboard, Agents Remember,
credential or local-service authority: the document it lands on stays sandboxed and has an opaque
origin, so the write guard refuses its writes. The canvas host does not count the navigation as a
failed boundary; the panel revokes that channel and reopens the saved page ([The panel](#the-panel)).
No further safeguard is engineered around it.

### Scripts always run inside the isolation

A page's own scripts are part of the served policy, never a separate decision: `script-src` admits
the session's own assets and inline script, inside the opaque sandbox, always. No runtime switch
turns them off. The isolation is the sandbox, the CSP, the opaque origin and the session's own
assets only, and the suite checks it on every run (the commands under [Checks](#checks) run these
tests):

- `mcp/tests/test_canvas_host.py`: `test_every_response_carries_its_policy_headers` (every CSP
  directive, the sandbox token set, COOP, the Permissions-Policy, the asset headers),
  `test_one_session_serves_its_page_and_assets_under_the_page_prefix`,
  `test_the_asset_route_serves_only_the_sessions_own_revision` (another revision and a sibling frame
  refused), `test_a_traversed_symlinked_missing_or_corrupt_asset_is_refused` (the task root through
  the canvas prefix refused), `test_revoking_a_frame_ends_every_channel_of_it` and
  `test_a_replaced_channel_answers_410_and_changes_nothing` (revocation).
- `mcp/tests/test_canvas_serving.py`: `test_the_served_page_carries_its_isolation_policy` (the
  served document's headers end to end) and `test_a_foreign_or_opaque_origin_cannot_write`.

What the browser then does with that policy -- no cookie, storage, parent DOM, opener, popup, top
navigation, worker, `eval` or network for an opaque, sandboxed document -- is the browser's own
enforcement of those headers. The tests above check what the canvas host serves and refuses, not
what the browser does.

### What the browser does not contain

The limit table (`canvas/policy.LIMITS`) names twelve bounds. Each entry has a value, a field
`enforced_by` and a text `where` that names what holds the bound. The canvas host or the served
policy holds seven of them: 20 messages per second with a burst of 40 (the protocol's token bucket),
4,096 bytes per message, 240 requests per session, 4 active frames, 1,800 seconds for a session, and
no worker (`worker-src 'none'` and `child-src 'none'`). The table also names 64 files and 16 MiB for
one revision; the frame store applies no file or byte ceiling to a revision unless its caller names
one, and no caller in the product names one. The store's one ceiling is 512 MiB for all frames of a
task. The field `enforced_by` reads `browser` for these nine entries.
**Timers, heap/CPU and GPU are not enforced** (`enforced_by` reads `container`): no browser API caps
them, and the product builds no stronger container, so the record names them as the unproven
boundary instead of claiming them.

## Readiness before a revision becomes ready

`canvas/readiness.py` checks the exact files of the next revision before they are published and
before the frame's ready pointer moves to them: the entrypoint is one UTF-8 document, every
reference stays inside this revision and names an admitted asset, every `data-evidence` reference
resolves in the admitted evidence packet (the comparison, the claims of the explanation and the
basis of each claim, stored with the revision), and declared motion is paired with a
`prefers-reduced-motion: reduce` rule. The check starts no browser and no model, and says
nothing about how good the explanation is.

Two callers run the check. `render`, the explainer's tool (the function `render_and_publish` in
`canvas/explainer_server.py`), runs it on the draft together with one browser load, and publishes only when the check is ready
and the load reports `loaded`; otherwise it returns its report and publishes nothing. `Presenter.publish` runs
it and refuses with `readiness-failed` and the named failing check. In both cases the last ready
revision stays in place.

## The canvas host's protocol (`canvas/protocol.py`)

One registered version (`1`), five operations from the page -- `ready`, `status`, `open-evidence`,
`request-explanation`, `offer-selections` -- and a closed payload per operation: `ready` carries
none; `status` a `kind` (`ready`, `error` or `size`) with an optional `detail` and `bytes`;
`open-evidence` a `reference` and an optional `claimId`; `request-explanation` a `question` of at
most 1,024 characters; `offer-selections` one to twelve `{question, choice}` rows. `stale-parts`
goes the other way: the mounted panel sends it into the page; it is not a page operation. Every
envelope is strict about its
types, carries its type, version, request identity, session, revision and
[comparison](../concepts.md#comparison), and the canvas host
compares all three of the last to the session it created. A JSON depth bound and the byte ceiling
are checked before validation; every request is charged to the session's bucket before it is parsed.
Refusals are named: `malformed`, `oversized`, `wrong-version`, `unknown-op`, `foreign-session`,
`stale-revision`, `foreign-comparison`, `replayed`, `rate-limited`, `exhausted`, `revoked`,
`wrong-window`. An origin string, including `null`, authenticates nothing.

`open-evidence` is resolved by `canvas/evidence.py` against the revision's *own* admitted evidence
packet: a claim basis resolved by typed identity, a claim, a collection item's address field or a
supplemental receipt with its own `read_state`. A reference carried by two claims without a claim
identity is refused as ambiguous; a basis that was not `present` answers `unavailable` with its own
limitation; nothing is ever re-pointed at today's record.

`request-explanation` records the question for the trusted conversation and answers
`startsModel: false`. It cannot start authoring. Explain opens the first-request form;
its explicit Send requests a build.

## Sessions and limits

A session is created by the canvas host (`secrets.token_urlsafe(32)`), bound to frame, revision,
comparison and evidence digest (the SHA-256 of the revision's evidence packet). Four live at most;
the least recently used is disposed first. Every session ends 30 minutes after it opens, used or
not. Replacement, publication of a successor, disposal and a scope change revoke the
channel: a revoked value answers `refused:revoked` (with a bounded tombstone keeping the reason), and
a channel whose revision is no longer the ready one refuses its display messages `stale-revision`
rather than applying them.

## The review-presenter capability (`canvas/presenter.py`)

`review-presenter` is a capability: its page becomes no review verdict and no gate decision. The
explainer authors every page in the explainer's workspace (`canvas/explainer_service.py`,
`canvas/explainer_server.py`). `canvas/presenter.py`
keeps the durable attempt records readable and publishes through the confined frame writer.

- **launch choices** come from the role row `orchestration.roles.review-presenter`
  ([orchestration.roles](../reference/settings-json.md#orchestrationroles-orchestrationrolesperlevel));
  `review-presenter` is no role. `settings_role_launch` in
  `cli/paseo_catalog.py` resolves the harness or the default harness, `model`, `effort`,
  `serviceTier`, `mode`, `featureValues` and `notes`. A setting that the host (Paseo) cannot honour refuses
  the launch as `explainer-seat-config`, and an explainer that the host started on other values
  refuses as `explainer-seat-config-unapplied` (`cli/paseo_explainer_host.py`).
- **design guidance** comes from `trees/memory/system/design-guidelines.md` in the explainer's
  workspace, the memory that the workspace checks out at its binding, and from the files it routes
  to. The file is optional: the user keeps it in the memory repository, and nothing falls back to the example
  that Agents Remember ships (`system/defaults/examples/memory-repo/design-guidelines.md`). The
  explainer reads these in its own worktrees. They set the look and techniques within the capability text's bounds. Where the
  guidelines ask for permissions, the rules of evidence and coverage, the claim set or the page's
  isolation, the explainer does not follow them and records that it did not.
- **capability text** is canonical `canvas/review-presenter.md`, with a byte-identical copy in the
  package data (`package_data/canvas/review-presenter.md`). The loader in
  `cli/role_instructions.py` reads the packaged copy and enforces the registered budget of 1,100
  words and the required wording; the authoring attempt records baseline, addition or replacement provenance
  and the delivered digest.
- **the authoring attempt** (`canvas/presenter_store.py`), the durable record of one authoring run
  through `Presenter`, records caller, comparison binding, evidence
  digest, design identity, instruction composition, launch provenance, execution, session and
  model, working folder, parent revision, predecessor, activity (<=200 typed lines) and
  conversation (<=200 turns, each naming its comparison). Retention keeps the newest 32 attempts,
  lists the newest 20, reclaims the working folders of evicted attempts, and a request ledger
  refuses a retry of a reclaimed request by name instead of starting a duplicate session.
- **writer confinement**: the only path into the task is the frame store's draft/publication. Assets
  are read from the attempt's own working folder
  (`<coordination-root>/presenter/<repository>/<master folder>/<task id>/<frame>/<attempt>`); symlinked roots and
  ancestors, escapes, deep trees and ceiling overruns are refused before any bytes are read.
- **session operations**: every transition is a locked fresh read-modify-write, so a delayed host
  receipt cannot revive a cancelled attempt and publication races keep their real outcome. `resume`
  refuses `not-resumable` for a cancelled attempt and `no-recorded-session` for an attempt without
  a recorded session. `cancel` refuses `not-cancellable` for an attempt that is not live and keeps
  every retained revision.
- **authoring execution** goes through the port `PresenterRunner`: a runner that the caller of
  `Presenter` supplies, and that must refuse when it cannot keep its writes inside the attempt's
  folder (a confined runner). A caller that supplies one gets one
  attempt's prepared folder; a caller that supplies none gets `UnconfiguredPresenterRunner`, which
  refuses `no-confined-harness`. The product supplies none: the explainer does not author through
  `Presenter`, it authors in its own workspace. The deterministic local
  harness in `canvas/presenter_host.py` is selected only by explicit callers such as tests.
- **render** (`canvas/presenter_tools.py`) loads the explainer's draft in Chromium at 1280×720
  without a model. Each run writes its own folder `renders/run-NNN/` in the explainer's
  workspace, with `report.json`, `page.png` and `interactions/`; the reply names the folder and
  carries no picture. The load answers `loaded`, `failed` or `unavailable`, and only `loaded`
  lets a revision become ready. The node script of the load imports the Playwright module that
  `AR_PLAYWRIGHT` names, and the load runs on the Node the product installs for the host. The
  module and its Chromium are the product's own (`canvas/explainer_browser.py`): when either is
  missing at that first load, the load fetches both into `<data>/agents-remember/explainer-browser`
  and its result records that they were fetched, so the answer at the place the page was asked for
  says so. A user who sets `AR_PLAYWRIGHT` keeps their module and its browser folder, and nothing is
  fetched behind it; an instrumented load, which names its own Node, fetches nothing either.
  [What the explainer needs before it can publish](../guides/review-page-and-explainer.md#what-the-explainer-needs-before-it-can-publish)
  tells the user's side. A load whose browser cannot be made ready is `unavailable` with the reason
  and the command that gets the browser, and nothing is published. Documentation that the explainer
  reads is separately bounded and labeled; it is never review evidence.

## The panel

`dashboard/src/panels/review/explainer/ExplanationView.tsx` draws what the tab "Explanation" shows
in the reading column of the review page. The top line, tree and side conversation remain outside it. Every read
and channel is keyed by the exact scope it serves -- repository, master, task, frame and the shown
comparison identity -- so a late answer for another scope is discarded, never shown. It reads
the saved state on mount and opens a revision under a fresh channel. Explain opens the
first-request form, with Send, Cancel and build progress; the revision selector offers
retained revisions and Compare. The sandboxed `iframe` forwards evidence destinations to the
mounted review's navigation owner.
While a build is active it reads build status every second; saved-only opening
never polls, and no passive read starts/resumes a session. It revokes what it owns on unmount and a
channel whose revision or scope moved before opening its successor, and on a second load of the
sandboxed `iframe` (the page navigated itself) revokes that channel and re-opens the saved page.

`onEvidence` in `dashboard/src/panels/review/ReviewSurface.tsx` navigates only where the mounted
review owns a matching selector at the comparison on screen. It then switches the page to the
tab "Review" and selects the destination. For any other destination it returns without a
navigation, and the page shows no sentence. `destinationLine` in
`dashboard/src/panels/review/canvas/canvasModel.ts` composes the lines "Not opened here: …",
"Unavailable: …" and "Not opened: …"; its one caller, `resolveEvidenceSelection`, reads the
selector of the result and draws none of the lines.

## Supported boundaries and remaining limits

The pinned offline kit is described in [canvas-kit.md](canvas-kit.md); selecting an entry carries
its required notices into the immutable revision. The render path prospectively loads the page's
exact selected assets in Chromium before publication. No standalone witness proves every kit
entry's features.

Native conversation, status, cancel, explicit resume and replacement name actual host identities.
An uncertain create repeats its immutable idempotent request. A passive read of the history
resumes no session: when the host refuses a transcript as `presenter-transcript-unavailable`,
`Presenter` returns the conversation that the attempt recorded and marks the host status
`unavailable`. Retention is bounded, not complete-history archival.

The first admitted document load receives `ar-canvas-binding/v1` from its mounted panel: a message
with the session, the revision, the comparison, the protocol version `1` and `effects`, which is
`false` when the dashboard's effects are off or the system asks for reduced motion. The panel
sends it again when `effects` changes. Authored
pages mark bases with `data-evidence` and use the narrow `open-evidence` protocol. A second load
revokes instead of sending another binding. Exact destination rendering remains with the mounted
review's supported selectors; a destination that no selector matches opens nothing.

The explainer changes nothing of the system. When the user asks it for a change of the code, the
memory or a task, it passes the user's message on to the Architect of the task's master with the
tool `role_message`: it attaches the [message file](../concepts.md#message-file) of the delivered
message and marks its own view as inference. It writes no fix itself.

The browser's opaque sandbox does not enforce per-frame CPU, heap, timer or GPU ceilings. On a
self-navigation of the sandboxed `iframe` the mounted panel revokes the old channel and reopens saved output. Scripts
always run inside the isolation.

## Checks

The Python command runs from the root of a development checkout, the dashboard commands from its
folder `dashboard/`.
[Development environment](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#development-environment)
creates `mcp/.venv` with `scripts/bootstrap-mcp-venv.sh` and installs the dashboard's dependencies
with `npm --prefix dashboard ci`.

- Python: `mcp/.venv/bin/python -m pytest mcp/tests/test_canvas_host.py mcp/tests/test_canvas_presenter.py mcp/tests/test_canvas_serving.py mcp/tests/test_canvas_readiness.py`
- Dashboard, from `dashboard/`: `npx vitest run src/panels/review/canvas src/panels/review/explainer`, `npx tsc -b`, `npx eslint src/panels/review/canvas src/panels/review/explainer`.
