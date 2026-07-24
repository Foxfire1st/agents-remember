// The one Chats stage body (design §4.1, §12.1). A THIN composition: it selects between the default
// structured ConversationSurface, the in-stage history library, and the legacy-raw terminal, and it
// owns the default-off terminal-diagnostics drawer. It copies no panel's state — the composer,
// interaction bar, queue, header, and status line remain their own authorities and are rendered by
// SessionsView around this body. For a controlled session the structured surface is the default and
// the PTY is only a read-only diagnostic; a legacy-raw session keeps its interactive PTY as the
// primary body, honestly labeled.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { css } from "../../../styled-system/css";
import {
  readSubmissionAuthority,
  SubmissionLifecycleRouteError,
} from "../../data/submissionLifecycleClient";
import {
  connectConversation,
  hasWarmConversation,
  LRU_LIMIT,
  touchConversation,
  useActiveConversation,
} from "../../data/conversation/store";
import type { HarnessId } from "../../data/conversation/types";
import { sessionStore, useSessions, type OpenSession } from "../../data/sessions";
import { ConversationReconnect } from "./conversation/ConversationReconnect";
import { ConversationSurface } from "./conversation/ConversationSurface";
import { TerminalDiagnosticsDrawer } from "./conversation/TerminalDiagnosticsDrawer";
import { ConversationLibrarySurface } from "./conversation-library/ConversationLibrarySurface";
import { isControlledSession } from "./lifecycleCopy";
import { PtySurface } from "./PtySurface";

const body = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", minWidth: "0", gap: "0.3rem" });
// The live surface stays mounted (its store keeps updating) but goes inert behind the library (§4.4).
const hiddenBehind = css({ display: "none" });
// The keep-alive pool fixes a switch-back glitch: unloading a chat's surface on blur lost its
// scroll offset, so returning to it no longer stayed pinned to the bottom. Every chat focused in
// this stage keeps its ConversationSurface MOUNTED; only the focused one is visible. The pool is
// the positioning context for the hidden entries.
const pool = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  position: "relative",
});
// A hidden-but-mounted surface is taken out of the flow (absolute over the pool) and hidden with
// `visibility` — NEVER display:none. display:none destroys the scroll offset and the virtualizer's
// measurements, which is exactly the top-then-jump-to-bottom glitch being fixed; visibility keeps
// layout alive (scroll state, measured rows, bottom-follow all keep working while hidden) while
// removing the surface from pointer, keyboard, tab-order, and the accessibility tree.
const keptHidden = css({
  position: "absolute",
  top: "0",
  right: "0",
  bottom: "0",
  left: "0",
  visibility: "hidden",
  display: "flex",
  flexDirection: "column",
  minHeight: "0",
  minWidth: "0",
});

interface PtyBox {
  width: number;
  height: number;
}

// The kept-alive terminal must retain more than React identity: visibility:hidden still
// participates in layout, so harness-only chrome (notably the composer) would otherwise resize the
// hidden xterm and make it refit twice per focus round-trip. Remember the PTY layer's box only while
// it is genuinely visible. The hidden caller applies that last box and this observer disconnects;
// on re-show the fixed box is released, so an actual browser/layout resize still produces exactly
// one honest terminal ResizeObserver update.
function useVisiblePtyBox(visible: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<PtyBox | null>(null);

  useLayoutEffect(() => {
    if (!visible) return undefined;
    const node = ref.current;
    if (node === null) return undefined;
    const measure = () => {
      const { width, height } = node.getBoundingClientRect();
      if (width <= 0 || height <= 0) return;
      setBox((current) =>
        current?.width === width && current.height === height
          ? current
          : { width, height },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  return {
    ref,
    frozenStyle:
      box === null
        ? undefined
        : { width: box.width, height: box.height, flex: "none" as const },
  };
}

function harnessOf(session: OpenSession): HarnessId | null {
  if (session.harness === "codex" || session.harness === "claude" || session.harness === "pi") {
    return session.harness;
  }
  return null;
}

// Fresh-chat boot: the epoch resolve raced the bridge's own boot —
// submission-authority 503s until native control is actually listening (~5–7 s after launch, more on
// a cold daemon) — but it gave up after ONE 800 ms retry and escalated to the fail-loud strip, which
// then NEVER self-recovered (still failed 240 s later, with the session long since ready). The
// resolve now polls actual bridge readiness with backoff across a bounded 30 s window, mirroring the
// hydrate window in data/conversation/store: the alarm remains the honest end-state past the bound,
// but a healthy slow boot never reaches it.
// A later tuning pass: the bridge typically turns ready at
// ~2–5 s after launch, so the coarse 800→2500 ms backoff added up to ~2.5 s of pure quantization to
// every fresh-chat boot. The endpoint is cheap (2.5 s-memo'd server-side), so the poll starts fine
// and caps low — readiness is discovered within ≤1 s of the first 200 instead of ≤2.5 s.
const EPOCH_RESOLVE_WINDOW_MS = 30_000;
const EPOCH_RESOLVE_RETRY_MS = 250;
const EPOCH_RESOLVE_RETRY_MAX_MS = 1_000;

// The epoch-resolve phase of ONE session (see the per-session map in the component).
type EpochPhase = "resolving" | "ready" | "failed";

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => window.setTimeout(resolve, ms));

// Only a TRANSIENT boot answer is retried: a transport drop (httpStatus null) or a 5xx while the
// bridge composes. A 4xx (404 unknown session, 409 epoch) or a malformed 200 is a real, terminal
// answer and must fail loud immediately, never masked behind the window (honesty).
function isTransientResolveFailure(error: unknown): boolean {
  if (error instanceof SubmissionLifecycleRouteError) {
    return error.httpStatus === null || error.httpStatus >= 500;
  }
  return true; // an unexpected throw is a transport anomaly, not a terminal server answer
}

export function ChatsStageBody({
  focused,
  onVisibleCols,
  libraryOpen,
  onCloseLibrary,
  diagnosticsOpen,
  onToggleDiagnostics,
  onSessionOpened,
  viewActive = true,
}: {
  focused?: OpenSession;
  onVisibleCols?: (cols: number | null) => void;
  libraryOpen: boolean;
  onCloseLibrary: () => void;
  diagnosticsOpen: boolean;
  onToggleDiagnostics: (open: boolean) => void;
  onSessionOpened: (sessionId: string) => void;
  /** false while the cockpit's Chats view is the hidden keep-alive layer (display:none —
      which destroys the timeline's DOM scroll offset). Threaded to every pooled surface as
      scroll-geometry availability, independently from which surface the operator can see. */
  viewActive?: boolean;
}) {
  const controlled = focused !== undefined && isControlledSession(focused);
  const sessionId = focused?.id;
  // The epoch phase is keyed BY SESSION, never held component-wide. With the
  // keep-alive pool a cold resolve for chat B can still be in flight after the operator has moved
  // on, and one shared phase let B's outcome paint chat A's surface — the alarm ("structured
  // surface unavailable") landed on a healthy chat and unmounted its timeline. A phase written
  // under the id of the session it was observed for is structurally unattributable to any other.
  const [epochPhases, setEpochPhases] = useState<Record<string, EpochPhase>>({});
  const generationRef = useRef(0);
  const markEpochPhase = useCallback((id: string, phase: EpochPhase) => {
    setEpochPhases((phases) => (phases[id] === phase ? phases : { ...phases, [id]: phase }));
  }, []);

  const connect = useCallback(
    async (refresh: boolean) => {
      if (sessionId === undefined || !controlled) return;
      // A warm projection is REUSED on refocus: switching back otherwise re-showed the "connecting"
      // pane and paid a full connect each time. No epoch re-resolve, no re-hydrate, no stream churn
      // — just an LRU touch. An explicit Retry (refresh=true) still forces the full re-resolve, and
      // a failed/evicted projection falls through to the ordinary connect.
      if (!refresh && hasWarmConversation(sessionId)) {
        // The warm short-circuit is still a connect, so it bumps the generation like every
        // other one. `generationRef.current` names the ONE live resolve; skipping the bump here
        // left an in-flight resolve for the session we just left believing it was still current.
        generationRef.current += 1;
        touchConversation(sessionId);
        markEpochPhase(sessionId, "ready");
        return;
      }
      const generation = ++generationRef.current;
      markEpochPhase(sessionId, "resolving");
      const startedAt = Date.now();
      for (let attempt = 0; ; attempt += 1) {
        try {
          // The cold path NEVER reads the authority cache. That cache is a bare Map
          // with no TTL and is invalidated only by bridge-epoch-mismatch handlers, so a hit issues
          // no request at all — it would hand connectConversation a possibly-dead bridgeEpoch and
          // turn the controlState pre-apply below into a remembered claim instead of a proven one
          // (honesty: "ready" must be provable). Forcing the read costs nothing the warm reuse cared
          // about: the warm short-circuit above means we only reach here when the projection is
          // absent, evicted, or failed — i.e. when a full connect is being paid anyway.
          const descriptor = await readSubmissionAuthority(sessionId, undefined, { refresh: true });
          if (generationRef.current !== generation) return;
          connectConversation(sessionId, descriptor.bridgeEpoch);
          // An authority 200 is the daemon's direct proof that native control answered
          // right now — fresher than the sweep-bounded catalog row the composer gate reads (the
          // sweep + poll can lag readiness by seconds). Pre-apply it; the next catalog hydrate
          // confirms or replaces it (the sessionStore.patch contract).
          sessionStore.getState().patch(sessionId, { controlState: "ready" });
          markEpochPhase(sessionId, "ready");
          return;
        } catch (error) {
          if (generationRef.current !== generation) return;
          const elapsed = Date.now() - startedAt;
          if (!isTransientResolveFailure(error) || elapsed >= EPOCH_RESOLVE_WINDOW_MS) {
            // A terminal answer, or the readiness window exhausted: escalate to the alarm state
            // FOR THIS SESSION. Fail-loud is preserved — the visible projection-failed banner
            // is the honest end-state, and a later catalog flip to controlState=ready re-drives the
            // connect below.
            markEpochPhase(sessionId, "failed");
            return;
          }
          // Transient boot race: stay on the quiet resolving phase and give the bridge time to
          // come up (one 800 ms retry was never enough for a real fresh-chat boot).
          await delay(
            Math.min(
              EPOCH_RESOLVE_RETRY_MS * 2 ** attempt,
              EPOCH_RESOLVE_RETRY_MAX_MS,
              EPOCH_RESOLVE_WINDOW_MS - elapsed,
            ),
          );
          if (generationRef.current !== generation) return;
        }
      }
    },
    [sessionId, controlled, markEpochPhase],
  );

  // The stage renders exactly one session's phase: the focused one's. An unseen session's phase is
  // never borrowed — a session with no recorded phase yet is honestly "resolving" (its connect
  // effect runs on the same commit).
  const epochState: EpochPhase =
    sessionId === undefined ? "resolving" : epochPhases[sessionId] ?? "resolving";

  useEffect(() => {
    if (sessionId === undefined || !controlled) return undefined;
    void connect(false);
    // NO disconnect on unmount/focus switch — recently-focused conversations stay warm so
    // switching back is instant. The LRU (store.enforceLru) and session termination
    // (sessionLifecycle) are the only disconnectors now.
    return undefined;
  }, [sessionId, controlled, connect]);

  // Recovery: once the projection DID fail loud, a later catalog flip to
  // controlState=ready is the daemon's own proof the bridge finally came up — re-drive the connect
  // on that TRANSITION (bounded: one connect per flip, so a re-failure cannot loop). The manual
  // "retry projection" action is unchanged.
  const controlState = focused?.controlState;
  const previousControlRef = useRef<{ sessionId?: string; controlState?: string }>({});
  useEffect(() => {
    const previous = previousControlRef.current;
    previousControlRef.current = { sessionId, controlState };
    if (
      epochState === "failed" &&
      controlState === "ready" &&
      (previous.sessionId !== sessionId || previous.controlState !== "ready")
    ) {
      void connect(true);
    }
  }, [epochState, controlState, sessionId, connect]);

  // ── Per-session keep-alive surfaces ──────────────────────────────────────────────────────────
  // Before this, ONE shared (unkeyed) ConversationSurface served whichever session was focused:
  // switching chats swapped its `sessionId` prop, and for a cold/evicted session the surface's
  // missing projection rendered the "connecting" pane instead of the timeline — the timeline (and
  // its scroll offset) was UNMOUNTED, remounted at the top on hydrate, and only the next live event
  // pulled it back to the bottom (the scroll-jump glitch). Now every controlled chat focused here
  // joins a bounded pool of mounted surfaces; only the focused one is visible.
  const [keptIds, setKeptIds] = useState<string[]>([]);
  useEffect(() => {
    if (sessionId === undefined || !controlled) return;
    setKeptIds((ids) => {
      // Most-recent-first, bounded by the SAME limit as the data layer's warm-projection LRU, so
      // the DOM pool can never outlive the projections it renders.
      const next = [sessionId, ...ids.filter((id) => id !== sessionId)].slice(0, LRU_LIMIT);
      if (next.length === ids.length && next.every((id, index) => id === ids[index])) return ids;
      return next;
    });
  }, [sessionId, controlled]);

  // A kept surface MUST drop when its session is no longer warm: LRU eviction and termination
  // (sessionLifecycle → disconnectConversation) are the data-layer disconnectors, and a
  // projection-failed session falls back to the full cold connect on refocus (the warm-reuse contract).
  // Keeping its DOM would resurrect a dead session's chrome and leak detached trees. The focused
  // session is exempt — its surface renders the resolving/failed states honestly. The selectors
  // deliberately return cheap scalars (LRU order + stream phases) so a per-event item ingest does
  // NOT re-render the stage body; they flip exactly when a kept session's warmth can change.
  const sessions = useSessions((state) => state.sessions);
  const touchOrder = useActiveConversation((state) => state.touchOrder);
  const streamPhases = useActiveConversation((state) =>
    Object.values(state.bySession)
      .map((projection) => projection.stream)
      .join(","),
  );
  useEffect(() => {
    setKeptIds((ids) => {
      const next = ids.filter((id) => id === sessionId || hasWarmConversation(id));
      return next.length === ids.length ? ids : next;
    });
    // The per-session phase map follows the pool it describes. A session that is neither
    // focused nor warm can render no phase, and its entry would otherwise accumulate for the life
    // of the cockpit; dropping it is lossless because refocusing such a session always falls
    // through to the full cold connect, which writes a fresh phase.
    setEpochPhases((phases) => {
      const kept = Object.entries(phases).filter(
        ([id]) => id === sessionId || hasWarmConversation(id),
      );
      return kept.length === Object.keys(phases).length ? phases : Object.fromEntries(kept);
    });
  }, [sessionId, sessions, touchOrder, streamPhases]);

  // ── The PTY layer is a PERSISTENT SIBLING of the conversation pool ─────────────────────────────
  // Switching harness → terminal → harness brought back the redraw/rescroll glitch. The
  // stage used to render EITHER the PTY (legacy-raw seats) OR the keep-alive conversation pool —
  // mutually exclusive subtrees — so focusing a harness seat UNMOUNTED the whole PTY stack
  // (xterm dispose + socket teardown), and returning to a terminal paid full boot (construct +
  // WebSocket + full scrollback replay + refit cascade). Both layers now stay mounted; the one
  // that does not match the focused seat's archetype hides with the keptHidden pattern
  // (visibility + aria-hidden), exactly like the pool's own hidden entries. The terminal's
  // xterm/socket/scroll state survives archetype switches; only an honest box change (the
  // browser or owning cockpit geometry changed while the layer was away) drives ONE RO refit.
  // Sibling-only chrome changes do not resize the hidden terminal: it freezes at its last visible
  // box until selected again.
  const terminalFocused = focused !== undefined && !controlled;
  const harnessId = focused === undefined ? null : harnessOf(focused);
  const showLibrary = controlled && libraryOpen && harnessId !== null;
  const ptyLayerBox = useVisiblePtyBox(viewActive && terminalFocused);
  const [ptySessionId, setPtySessionId] = useState<string | undefined>(undefined);
  useEffect(() => {
    // Lazy-on-first-focus: the layer (and PtySurface's own per-seat mountedIds) mounts nothing
    // until a terminal seat is actually focused, then survives every later archetype switch.
    if (terminalFocused && sessionId !== undefined) setPtySessionId(sessionId);
  }, [terminalFocused, sessionId]);

  // The visible-pane cols truth belongs to the terminal layer; while it hides, the stage falls
  // back to the pixel estimate (SessionsView's floor chip) instead of a stale pane count.
  useEffect(() => {
    if (!terminalFocused) onVisibleCols?.(null);
  }, [terminalFocused, onVisibleCols]);

  if (focused === undefined) return null;

  // The most recently focused terminal seat owns the PTY layer while a harness seat has the stage.
  // Resolving through the store keeps its status fresh (a landed/exited seat prunes honestly
  // inside PtySurface), and a removed row degrades to the placeholder without disposing panes.
  const lastTerminal =
    ptySessionId !== undefined
      ? sessions.find((session) => session.id === ptySessionId)
      : undefined;
  const ptyFocus = terminalFocused ? focused : lastTerminal;
  const ptyLayerMounted = terminalFocused || ptySessionId !== undefined;

  // The in-stage library and the diagnostics drawer belong to controlled seats only — a raw
  // terminal renders neither (the earlier early return rendered no chrome for legacy-raw either).

  // The pool: the focused CONTROLLED session first (always rendered, even before the effect above
  // has added it), then the previously-focused warm ones. A focused terminal seat never joins —
  // the conversation layer must not host a ConversationSurface for a raw PTY. Keying by session
  // id is what preserves each surface's component/DOM identity — and with it the scroll offset —
  // across focus switches.
  const poolIds =
    controlled && sessionId !== undefined
      ? [sessionId, ...keptIds.filter((id) => id !== sessionId)]
      : keptIds;

  // Legacy raw: the native PTY is the primary interactive body. To declutter, there is no banner
  // row — the raw-terminal fact lives in the tooltip and data-mode, the pixels belong to the
  // terminal.
  return (
    <div
      className={body}
      data-testid="chats-stage-body"
      data-mode={terminalFocused ? "legacy-raw" : showLibrary ? "library" : "active-conversation"}
      title={terminalFocused ? "legacy terminal · structured conversation unavailable" : undefined}
    >
      {/* BOTH layers are persistent siblings in one positioning context. The non-matching one
          hides with the keptHidden pattern (visibility + aria-hidden) — never unmounted — so the
          conversation pool's scroll offsets AND the terminal stack's xterm/socket/scrollback all
          survive harness↔terminal switches. While the in-stage library is up the whole container
          goes display:none (unchanged §4.4 behavior — the library needs the space, and this
          pre-dates the keep-alive pool). */}
      <div className={showLibrary ? hiddenBehind : pool} data-testid="chats-stage-layers">
        <div
          className={terminalFocused ? keptHidden : pool}
          aria-hidden={terminalFocused ? true : undefined}
          data-testid="conversation-layer"
        >
          {poolIds.map((id) => {
            const isFocused = id === sessionId;
            return (
              <div
                key={id}
                className={isFocused ? body : keptHidden}
                aria-hidden={isFocused ? undefined : true}
                data-testid={`conversation-keepalive-${id}`}
              >
                {isFocused && epochState === "failed" ? (
                  <ConversationReconnect
                    phase="projection-failed"
                    onRetry={() => void connect(true)}
                    onShowDiagnostics={() => onToggleDiagnostics(true)}
                  />
                ) : (
                  <ConversationSurface
                    sessionId={id}
                    visible={isFocused && !showLibrary}
                    scrollGeometryActive={viewActive && !showLibrary}
                    onRetry={() => void connect(true)}
                    onShowDiagnostics={() => onToggleDiagnostics(true)}
                  />
                )}
              </div>
            );
          })}
        </div>
        {/* The terminal stack outlives archetype switches: PtySurface keeps its own per-seat
            keep-alive (mountedIds) and now the LAYER itself never unmounts once created. While
            hidden it carries no cols truth and no focus/zone affordances (`hidden`), so the
            visible conversation owns the stage's keyboard/focus contract alone. */}
        {ptyLayerMounted ? (
          <div
            ref={ptyLayerBox.ref}
            className={terminalFocused ? body : keptHidden}
            style={!terminalFocused ? ptyLayerBox.frozenStyle : undefined}
            aria-hidden={terminalFocused ? undefined : true}
            data-testid="pty-layer"
          >
            <PtySurface
              focused={ptyFocus}
              onVisibleCols={terminalFocused ? onVisibleCols : undefined}
              hidden={!terminalFocused}
            />
          </div>
        ) : null}
      </div>
      {showLibrary && harnessId !== null ? (
        <ConversationLibrarySurface
          harnessId={harnessId}
          launchContext={focused.leafKey ? { leafKey: focused.leafKey, seatRole: focused.seatRole } : undefined}
          onOpened={(arSessionId) => {
            onCloseLibrary();
            onSessionOpened(arSessionId);
          }}
          onBack={onCloseLibrary}
        />
      ) : null}
      {/* The diagnostics drawer belongs to the active conversation; while the in-stage library
          overlay is up it is not rendered, so the two surfaces can never overlay. */}
      {!showLibrary && controlled ? (
        <TerminalDiagnosticsDrawer
          focused={focused}
          open={diagnosticsOpen}
          onClose={() => onToggleDiagnostics(false)}
        />
      ) : null}
    </div>
  );
}
