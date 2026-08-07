// The one Chats stage body (design §4.1, §12.1). A THIN composition: it selects between the default
// structured ConversationSurface, the in-stage history library, and the legacy-raw terminal, and it
// owns the default-off terminal-diagnostics drawer. It copies no panel's state — the composer,
// interaction bar, queue, header, and status line remain their own authorities and are rendered by
// SessionsView around this body. For a controlled session the structured surface is the default and
// the PTY is only a read-only diagnostic; a legacy-raw session keeps its interactive PTY as the
// primary body, honestly labeled. The keep-alive/epoch machinery lives in this file; the render
// layers live in stageLayers.tsx and the shared layer styles in chatsStageStyles.ts.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import {
  connectConversation,
  focusConversation,
  hasWarmConversation,
  LRU_LIMIT,
  touchConversation,
  useActiveConversation,
} from "../../data/conversation/store";
import type { HarnessId } from "../../data/conversation/types";
import {
  readSubmissionAuthority,
  SubmissionLifecycleRouteError,
} from "../../data/submissionLifecycleClient";
import { sessionStore, useSessions, type OpenSession } from "../../data/sessions";
import { EmptyStateBackdrop } from "../EmptyStateBackdrop";
import { body, hiddenBehind, pool } from "./chatsStageStyles";
import { isControlledSession } from "./lifecycleCopy";
import {
  ConversationPool,
  EmptyChatStage,
  LibraryAndDiagnostics,
  PtyLayer,
} from "./stageLayers";

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
  if (
    session.harness === "codex" ||
    session.harness === "claude" ||
    session.harness === "pi"
  ) {
    return session.harness;
  }
  return null;
}

function harnessIdFor(focused: OpenSession | undefined): HarnessId | null {
  return focused === undefined ? null : harnessOf(focused);
}

function conversationActiveFor(
  controlled: boolean,
  sessionId: string | undefined,
  viewActive: boolean,
  showLibrary: boolean,
): boolean {
  return controlled && sessionId !== undefined && viewActive && !showLibrary;
}

function stageTitle(terminalFocused: boolean): string | undefined {
  return terminalFocused
    ? "legacy terminal · structured conversation unavailable"
    : undefined;
}

// Fresh-chat boot: the epoch resolve raced the bridge's own boot —
// submission-authority 503s until native control is actually listening (~5–7 s after launch, more on
// a cold daemon) — but it gave up after ONE 800 ms retry and escalated to the fail-loud strip, which
// then NEVER self-recovered (still failed 240 s later, with the session long since ready). The
// resolve now polls actual bridge readiness with backoff across a bounded 30 s window, mirroring the
// hydrate window in data/conversation/store: the alarm remains the honest end-state past the bound,
// but a healthy slow boot never reaches it.
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

async function resolveEpoch(
  sessionId: string,
  generation: number,
  generationRef: RefObject<number>,
  markEpochPhase: (id: string, phase: EpochPhase) => void,
): Promise<void> {
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
      const descriptor = await readSubmissionAuthority(sessionId, undefined, {
        refresh: true,
      });
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
      if (
        !isTransientResolveFailure(error) ||
        elapsed >= EPOCH_RESOLVE_WINDOW_MS
      ) {
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
}

function useEpochResolve(
  sessionId: string | undefined,
  conversationActive: boolean,
) {
  // The epoch phase is keyed BY SESSION, never held component-wide. With the
  // keep-alive pool a cold resolve for chat B can still be in flight after the operator has moved
  // on, and one shared phase let B's outcome paint chat A's surface — the alarm ("structured
  // surface unavailable") landed on a healthy chat and unmounted its timeline. A phase written
  // under the id of the session it was observed for is structurally unattributable to any other.
  const [epochPhases, setEpochPhases] = useState<Record<string, EpochPhase>>({});
  const generationRef = useRef(0);
  const markEpochPhase = useCallback((id: string, phase: EpochPhase) => {
    setEpochPhases((phases) =>
      phases[id] === phase ? phases : { ...phases, [id]: phase },
    );
  }, []);

  const connect = useCallback(
    async (refresh: boolean) => {
      if (sessionId === undefined || !conversationActive) return;
      // A warm projection is REUSED on refocus: switching back otherwise re-showed the "connecting"
      // pane and paid a full connect each time. No epoch re-resolve or re-hydrate — the LRU touch
      // resumes a fresh EventSource from the retained cursor. An explicit Retry (refresh=true) still
      // forces the full re-resolve, and a failed/evicted projection falls through to cold connect.
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
      await resolveEpoch(sessionId, generation, generationRef, markEpochPhase);
    },
    [sessionId, conversationActive, markEpochPhase],
  );

  useEffect(() => {
    if (sessionId === undefined || !conversationActive) {
      // Moving to another cockpit view, the library, or a raw terminal leaves no visible structured
      // chat. Cancel any cold authority resolve and release the one conversation SSE slot.
      generationRef.current += 1;
      focusConversation(null);
      return undefined;
    }
    // Transport focus changes before any async epoch/page work. The previous chat's stream closes
    // immediately, while its projection, cursor, DOM, scroll state, and TanStack cache remain warm.
    focusConversation(sessionId);
    void connect(false);
    return () => {
      // Invalidate an in-flight authority resolve before the next focus can claim transport. This is
      // a pause, not a disconnect: the retained runtime/projection remains the instant refocus path.
      generationRef.current += 1;
      focusConversation(null);
    };
  }, [sessionId, conversationActive, connect]);

  const pruneEpochPhases = useCallback(
    (stillKept: (id: string) => boolean) => {
      setEpochPhases((phases) => {
        const kept = Object.entries(phases).filter(([id]) => stillKept(id));
        return kept.length === Object.keys(phases).length
          ? phases
          : Object.fromEntries(kept);
      });
    },
    [],
  );
  // The stage renders exactly one session's phase: the focused one's. An unseen session's phase is
  // never borrowed — a session with no recorded phase yet is honestly "resolving" (its connect
  // effect runs on the same commit).
  const epochState: EpochPhase =
    sessionId === undefined ? "resolving" : epochPhases[sessionId] ?? "resolving";
  return { epochState, connect, pruneEpochPhases };
}

function useEpochRecovery(
  focused: OpenSession | undefined,
  epochState: EpochPhase,
  sessionId: string | undefined,
  connect: (refresh: boolean) => Promise<void>,
) {
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
}

function useConversationKeepAlive(
  sessionId: string | undefined,
  controlled: boolean,
  pruneEpochPhases: (stillKept: (id: string) => boolean) => void,
) {
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
      const next = [sessionId, ...ids.filter((id) => id !== sessionId)].slice(
        0,
        LRU_LIMIT,
      );
      if (next.length === ids.length && next.every((id, index) => id === ids[index])) {
        return ids;
      }
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
    const stillKept = (id: string) => id === sessionId || hasWarmConversation(id);
    setKeptIds((ids) => {
      const next = ids.filter(stillKept);
      return next.length === ids.length ? ids : next;
    });
    // The per-session phase map follows the pool it describes. A session that is neither
    // focused nor warm can render no phase, and its entry would otherwise accumulate for the life
    // of the cockpit; dropping it is lossless because refocusing such a session always falls
    // through to the full cold connect, which writes a fresh phase.
    pruneEpochPhases(stillKept);
  }, [sessionId, sessions, touchOrder, streamPhases, setKeptIds, pruneEpochPhases]);
  return { keptIds, sessions };
}

function usePtyLayer(
  focused: OpenSession | undefined,
  terminalFocused: boolean,
  sessionId: string | undefined,
  viewActive: boolean,
  sessions: OpenSession[],
) {
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
  const ptyLayerBox = useVisiblePtyBox(viewActive && terminalFocused);
  const [ptySessionId, setPtySessionId] = useState<string | undefined>(undefined);
  useEffect(() => {
    // Lazy-on-first-focus: the layer (and PtySurface's own per-seat mountedIds) mounts nothing
    // until a terminal seat is actually focused, then survives every later archetype switch.
    if (terminalFocused && sessionId !== undefined) setPtySessionId(sessionId);
  }, [terminalFocused, sessionId]);
  // The most recently focused terminal seat owns the PTY layer while a harness seat has the stage.
  // Resolving through the store keeps its status fresh (a landed/exited seat prunes honestly
  // inside PtySurface), and a removed row degrades to the placeholder without disposing panes.
  const lastTerminal =
    ptySessionId !== undefined
      ? sessions.find((session) => session.id === ptySessionId)
      : undefined;
  const ptyFocus = terminalFocused ? focused : lastTerminal;
  const ptyLayerMounted = terminalFocused || ptySessionId !== undefined;
  return { ptyLayerBox, ptyFocus, ptyLayerMounted };
}

function stageMode(terminalFocused: boolean, showLibrary: boolean): string {
  if (terminalFocused) return "legacy-raw";
  if (showLibrary) return "library";
  return "active-conversation";
}

function stageDataMode(
  hasFocused: boolean,
  terminalFocused: boolean,
  showLibrary: boolean,
): string {
  if (!hasFocused) return "empty";
  return stageMode(terminalFocused, showLibrary);
}

function activeViewFor(viewActive: boolean, hasFocused: boolean): boolean {
  return viewActive && hasFocused;
}

function controlledFor(focused: OpenSession | undefined): boolean {
  return focused !== undefined && isControlledSession(focused);
}

function terminalFocusedFor(
  focused: OpenSession | undefined,
  controlled: boolean,
): boolean {
  return focused !== undefined && !controlled;
}

function showLibraryFor(
  controlled: boolean,
  libraryOpen: boolean,
  harnessId: HarnessId | null,
): boolean {
  return controlled && libraryOpen && harnessId !== null;
}

function poolFor(
  controlled: boolean,
  sessionId: string | undefined,
  keptIds: string[],
): string[] {
  return controlled && sessionId !== undefined
    ? [sessionId, ...keptIds.filter((id) => id !== sessionId)]
    : keptIds;
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
  const controlled = controlledFor(focused);
  const sessionId = focused?.id;
  const harnessId = harnessIdFor(focused);
  const showLibrary = showLibraryFor(controlled, libraryOpen, harnessId);
  const conversationActive = conversationActiveFor(
    controlled,
    sessionId,
    viewActive,
    showLibrary,
  );
  const { epochState, connect, pruneEpochPhases } = useEpochResolve(
    sessionId,
    conversationActive,
  );
  useEpochRecovery(focused, epochState, sessionId, connect);
  const { keptIds, sessions } = useConversationKeepAlive(
    sessionId,
    controlled,
    pruneEpochPhases,
  );

  const terminalFocused = terminalFocusedFor(focused, controlled);
  const { ptyLayerBox, ptyFocus, ptyLayerMounted } = usePtyLayer(
    focused,
    terminalFocused,
    sessionId,
    viewActive,
    sessions,
  );
  // The visible-pane cols truth belongs to the terminal layer; while it hides, the stage falls
  // back to the pixel estimate (SessionsView's floor chip) instead of a stale pane count.
  useEffect(() => {
    if (!terminalFocused) onVisibleCols?.(null);
  }, [terminalFocused, onVisibleCols]);

  if (focused === undefined && !ptyLayerMounted) {
    // No chat selected and no terminal stack exists yet: the main column keeps the adjutant
    // boomerang backdrop (restored from the pre-consolidation Chats empty canvas) with a quiet
    // pointer to the rail. Effects-gated like every other backdrop. Once a terminal stack exists
    // (ptyLayerMounted), the layers stay mounted through a transient no-focus render (focus
    // handoff after a cleanup ends the focused row) so xterm/socket/scroll identity survives.
    return <EmptyChatStage />;
  }

  const poolIds = poolFor(controlled, sessionId, keptIds);

  return (
    <div
      className={body}
      data-testid="chats-stage-body"
      data-mode={stageDataMode(focused !== undefined, terminalFocused, showLibrary)}
      title={stageTitle(terminalFocused)}
    >
      {focused === undefined ? (
        <EmptyStateBackdrop src="/assets/sc2-adjutant-boomerang.mp4">
          Select a chat to inspect it — or start one from the chat rail.
        </EmptyStateBackdrop>
      ) : null}
      <div className={showLibrary ? hiddenBehind : pool} data-testid="chats-stage-layers">
        <ConversationPool poolIds={poolIds} sessionId={sessionId} epochState={epochState} viewActive={activeViewFor(viewActive, focused !== undefined)} showLibrary={showLibrary} terminalFocused={terminalFocused} connect={connect} onToggleDiagnostics={onToggleDiagnostics} />
        {ptyLayerMounted ? <PtyLayer box={ptyLayerBox} terminalFocused={terminalFocused} ptyFocus={ptyFocus} onVisibleCols={onVisibleCols} /> : null}
      </div>
      {focused ? (
        <LibraryAndDiagnostics showLibrary={showLibrary} harnessId={harnessId} focused={focused} controlled={controlled} diagnosticsOpen={diagnosticsOpen} onCloseLibrary={onCloseLibrary} onToggleDiagnostics={onToggleDiagnostics} onSessionOpened={onSessionOpened} />
      ) : null}
    </div>
  );
}
