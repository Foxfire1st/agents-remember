import { useEffect, useState } from "react";
import { motion } from "motion/react";

import { css, cva, cx } from "../../styled-system/css";
import { selectQueue } from "../data/selectors";
import type { ConnState } from "../data/store";
import { dashboardStore, useDashboard } from "../data/store";
import { connectEvents, connectState } from "../data/stream";
import { lifecycleIdForSelection, lifecycleSelectionKey } from "../data/taskIdentity";
import { ModeBar } from "../grammar/ModeBar";
import { AttentionQueue } from "../panels/AttentionQueue";
import { Chats } from "../panels/Chats";
import { DetailPanel } from "../panels/DetailPanel";
import { EngineRoom } from "../panels/EngineRoom";
import { useShouldAnimate } from "../panels/engine-room/useShouldAnimate";
import { EventRiver } from "../panels/EventRiver";
import { FlowTab } from "../panels/FlowTab";
import { Hangar } from "../panels/Hangar";
import { HighlightComposer } from "../panels/HighlightComposer";
import { LifecycleList } from "../panels/LifecycleList";
import { MemoryMirror } from "../panels/MemoryMirror";
import { Topology } from "../panels/Topology";

// The cockpit shell (model C, slice 5c): persistent command chrome that never hides the alarms —
// a top status bar, a left rail (attention queue + lifecycle list = the master-caution, always
// visible), a switchable centre viewport (Operations / Engine Room / Memory / Topology / Hangar),
// and a persistent right rail (the event river ticker). The mode bar selects the viewport.
// Selection is ephemeral UI state held here and shared across panels and views.
// Slice 5f S1 (§4.1): the "machine map" views (Engine Room / Topology) and the Chats terminal
// (slice 6e) drop the rails and span the full body width; the top-bar caution stays visible so an
// alarm is never hidden.
type View = "operations" | "flow" | "engine" | "memory" | "topology" | "hangar" | "chats";

const VIEWS: { id: View; label: string }[] = [
  { id: "operations", label: "Operations" },
  { id: "flow", label: "Lifecycle Flow" },
  { id: "engine", label: "Engine Room" },
  { id: "memory", label: "Memory" },
  { id: "topology", label: "Topology" },
  { id: "hangar", label: "Hangar" },
  { id: "chats", label: "Chats" },
];

// Shell layout (slice 5d: co-located Panda css). The shell pins to the viewport so the top + mode
// bars stay fixed and the rails / viewport scroll internally; the marker classes (cockpit--shell /
// shell__body / rail / viewport) are kept so the per-panel descendant rules + tests still resolve.
const shell = css({
  position: "relative",
  display: "flex",
  flexDirection: "column",
  height: "100vh",
  minHeight: "0",
  overflow: "hidden",
  padding: "0.6rem 0.8rem",
  gap: "0.6rem",
});
const topbar = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "center",
  justifyContent: "space-between",
  gap: "1rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  paddingBottom: "0.5rem",
});
const title = css({
  margin: "0",
  fontSize: "0.9rem",
  letterSpacing: "0.18em",
  color: "amber",
  textShadow: "0 0 calc(6px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
});
const statusRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.9rem",
  fontSize: "0.78rem",
});
const dim = css({ color: "muted" });
const effectsToggle = cva({
  base: {
    font: "inherit",
    fontSize: "0.74rem",
    letterSpacing: "0.06em",
    paddingInline: "0.45rem",
    paddingBlock: "0.08rem",
    borderRadius: "2px",
    borderWidth: "1px",
    borderStyle: "solid",
    cursor: "pointer",
    background: "transparent",
    transition: "color 0.15s ease, border-color 0.15s ease",
    _hover: { borderColor: "muted" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: {
    on: {
      true: { color: "amber", borderColor: "amber" },
      false: { color: "muted", borderColor: "grid" },
    },
  },
});
const caution = cva({
  base: { letterSpacing: "0.06em", color: "muted" },
  variants: {
    sev: {
      clear: { color: "mint" },
      info: { color: "cyan" },
      warn: { color: "amber" },
      alarm: { color: "alarm", animation: "pulse 0.6s steps(1) infinite" },
    },
  },
});
const connBadge = cva({
  base: { fontWeight: "600", letterSpacing: "0.1em" },
  variants: {
    state: {
      connecting: { color: "amber" },
      live: { color: "mint" },
      "signal-lost": { color: "alarm", animation: "pulse 0.5s steps(1) infinite" },
    },
  },
});
// The body grid: the railed 3-column shell, or a single full-width column for the machine-map
// views (Engine Room / Topology), which render their own internal layout (5f §4.1).
const bodyGrid = cva({
  base: {
    display: "grid",
    gridTemplateRows: "minmax(0, 1fr)",
    gap: "0.7rem",
    flex: "1",
    minHeight: "0",
  },
  variants: {
    bleed: {
      false: {
        gridTemplateColumns: "minmax(300px, 1fr) minmax(420px, 2.2fr) minmax(260px, 0.95fr)",
      },
      true: { gridTemplateColumns: "1fr" },
    },
  },
});
const rail = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.7rem",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the rail itself does not scroll — each panel scrolls on its own
});
const viewport = css({
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the viewport does not scroll — its panel scrolls on its own
});
// Chats is kept mounted across every view (hidden via display, never unmounted) so the xterm
// instance, its scrollback buffer, and the live WebSocket survive a view switch — the cure for
// "switching away throws the terminal away." Cheap: the heavy xterm chunk is lazy and loads once a
// session opens, and re-entry is instant (no remount / re-init).
const chatsLayer = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
});

// Cockpit wires the live SSE streams, then renders the presentational shell. The shell is split
// out so the dev gallery (/dev/bench) renders the exact same surface against fixture state.
export function Cockpit() {
  useEffect(() => connectState(), []);
  useEffect(() => connectEvents((line) => dashboardStore.getState().pushEvent(line)), []);
  return <CockpitShell />;
}

export function CockpitShell() {
  const [view, setView] = useState<View>("operations");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const animate = useShouldAnimate();
  const selectedLifecycleId = useDashboard((s) =>
    lifecycleIdForSelection(selectedId, s.lifecycles, s.analytics),
  );

  // The machine-map views + the Chats terminal span full width: the rails hide and the view's own
  // layout breathes.
  const fullBleed = view === "flow" || view === "engine" || view === "topology" || view === "chats";

  // Open a node AND surface it in Operations: the attention queue / topology / hangar all jump
  // into the detail view, so a cross-view click lands where you can inspect it.
  const open = (id: string) => {
    setSelectedId(
      id.startsWith("taskdoc:") || id.startsWith("series:") || id.startsWith("lifecycle:")
        ? id
        : lifecycleSelectionKey(id),
    );
    setView("operations");
  };

  // Gated fade-in when the rails return (reduced-motion / data-effects=off → no tween). Leaving to
  // a full-bleed view unmounts them for a clean expand; the determinism path stays snapshot-stable.
  const railEnter = animate ? { initial: { opacity: 0 }, animate: { opacity: 1 } } : {};

  return (
    <div className={cx(shell, "cockpit--shell")}>
      <div className="crt-overlay" aria-hidden="true" />
      <TopBar />
      <div className={cx(bodyGrid({ bleed: fullBleed }), "shell__body")} data-fullbleed={fullBleed}>
        {!fullBleed && (
          <motion.aside
            key="rail-left"
            className={cx(rail, "rail rail--left")}
            transition={{ duration: 0.18 }}
            {...railEnter}
          >
            <AttentionQueue onSelect={open} />
            <LifecycleList selectedId={selectedId} onSelect={open} />
          </motion.aside>
        )}
        <main className={cx(viewport, "viewport")} data-view={view}>
          {view !== "chats" && <ViewBody view={view} selectedId={selectedId} onOpen={open} />}
          {/* Chats is never unmounted — only hidden — so the xterm buffer + live WebSocket survive a
              view switch instead of being re-created empty. See `chatsLayer`. */}
          <div
            className={chatsLayer}
            style={{ display: view === "chats" ? "flex" : "none" }}
            aria-hidden={view !== "chats"}
          >
            <Chats selectedLifecycleId={selectedLifecycleId} />
          </div>
        </main>
        {!fullBleed && (
          <motion.aside
            key="rail-right"
            className={cx(rail, "rail rail--right")}
            transition={{ duration: 0.18 }}
            {...railEnter}
          >
            <EventRiver />
          </motion.aside>
        )}
      </div>
      <ModeBar items={VIEWS} value={view} onChange={setView} label="Views" />
      {/* Slice 6f: a cockpit-wide composer that a text selection raises — send the selection (+ a
          message) to a chat session as a context package. Mounted once here so it works on every view;
          renders nothing until there is a selection. `onSent` flips to Chats so the operator sees it land. */}
      <HighlightComposer selectedLifecycleId={selectedLifecycleId} onSent={() => setView("chats")} />
    </div>
  );
}

function ViewBody({
  view,
  selectedId,
  onOpen,
}: {
  view: View;
  selectedId: string | null;
  onOpen: (id: string) => void;
}) {
  switch (view) {
    case "flow":
      return <FlowTab />;
    case "engine":
      return <EngineRoom />;
    case "memory":
      return <MemoryMirror />;
    case "topology":
      return <Topology onSelect={onOpen} />;
    case "hangar":
      return <Hangar onSelect={onOpen} />;
    // "chats" is intentionally not here — Chats is kept mounted in CockpitShell (hidden via CSS) so
    // the live terminal survives a view switch; routing it through this switch would unmount it.
    case "operations":
    default:
      return <DetailPanel selectedId={selectedId} onOpenLifecycle={onOpen} />;
  }
}

function TopBar() {
  const conn = useDashboard((s) => s.conn);
  const metrics = useDashboard((s) => s.metrics);
  const generatedAt = useDashboard((s) => s.generatedAt);
  const queue = useDashboard(selectQueue);
  const topSeverity = queue[0]?.severity ?? "clear"; // queue is severity-sorted server-side

  return (
    <header className={topbar}>
      <h1 className={title}>AGENTS REMEMBER · MISSION CONTROL</h1>
      <div className={statusRow}>
        <span className={caution({ sev: topSeverity })} data-testid="caution">
          ⚠ {queue.length} waiting
        </span>
        {metrics ? (
          <span className={dim}>
            {metrics.runningCount} running · {metrics.blockedCount} blocked · {metrics.totalTokens}{" "}
            tok
          </span>
        ) : null}
        {generatedAt ? <span className={dim}>@ {generatedAt.slice(11, 19)}</span> : null}
        <ConnBadge conn={conn} />
        <EffectsToggle />
      </div>
    </header>
  );
}

function ConnBadge({ conn }: { conn: ConnState }) {
  const label =
    conn === "live" ? "● LIVE" : conn === "signal-lost" ? "✶ SIGNAL LOST" : "… CONNECTING";
  return (
    <span className={connBadge({ state: conn })} data-testid="conn">
      {label}
    </span>
  );
}

// G6: a visible motion toggle. Flips html[data-effects] (which useShouldAnimate reads live, so the
// engine-room backdrop + all gated motion respond at once) and persists the choice to the
// `calm-cockpit` localStorage flag main.tsx reads on the next load. Default = effects on.
function EffectsToggle() {
  const [on, setOn] = useState(
    () => typeof document === "undefined" || document.documentElement.dataset.effects !== "off",
  );
  const toggle = () => {
    const next = !on;
    setOn(next);
    if (next) {
      delete document.documentElement.dataset.effects;
      window.localStorage.removeItem("calm-cockpit");
    } else {
      document.documentElement.dataset.effects = "off";
      window.localStorage.setItem("calm-cockpit", "1");
    }
  };
  return (
    <button
      type="button"
      className={effectsToggle({ on })}
      onClick={toggle}
      aria-pressed={on}
      data-testid="effects-toggle"
      title={on ? "Effects on — click to calm (freeze motion + backdrop)" : "Calm — click to enable motion + backdrop"}
    >
      {on ? "✦ Effects" : "❄ Calm"}
    </button>
  );
}
