import { css, cva } from "../../styled-system/css";
import { sessionSeatRole, type OpenSession } from "../data/sessions";

export type ChatActivityState = "needs-input" | "working" | "unknown" | "idle";

export interface ChatActivityIdentity {
  leafKey?: string;
  lifecycleId?: string;
}

export interface ChatActivitySummary {
  state: ChatActivityState;
  label: string;
  detail: string;
}

const shell = css({
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
  gap: "0.22rem",
  minWidth: "0",
  maxWidth: "4.5rem",
  fontSize: "0.66rem",
  whiteSpace: "nowrap",
});

const marker = cva({
  base: {
    display: "inline-block",
    width: "0.48rem",
    height: "0.48rem",
    flex: "0 0 auto",
    borderRadius: "full",
  },
  variants: {
    state: {
      "needs-input": { background: "amber" },
      working: { background: "mint" },
      unknown: { background: "dormant" },
      idle: { background: "cyan" },
    },
  },
});

const label = cva({
  base: {
    minWidth: "0",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  variants: {
    state: {
      "needs-input": { color: "amber" },
      working: { color: "mint" },
      unknown: { color: "muted" },
      idle: { color: "cyan" },
    },
  },
});

const ACTIVITY_RANK: Record<ChatActivityState, number> = {
  "needs-input": 3,
  working: 2,
  unknown: 1,
  idle: 0,
};

function isLiveHarness(session: OpenSession): boolean {
  return session.kind === "harness" && session.status === "running";
}

function activityState(
  turnState: string | undefined,
  controlState: OpenSession["controlState"],
): ChatActivityState {
  if (turnState === "awaiting-input") return "needs-input";
  if (turnState === "working") return "working";
  if (turnState === "turn-ended") return "idle";
  // A fresh ready-idle chat carries NO seat turn claim (the sweep
  // stamps none until its first turn) — its calm idle reads from the control lifecycle,
  // never from a fabricated stale/turn-ended.
  if (turnState === undefined && controlState === "ready") return "idle";
  return "unknown";
}

function activityLabel(state: ChatActivityState): string {
  return state === "needs-input" ? "needs input" : state;
}

function boundSessions(
  sessions: OpenSession[],
  identity: ChatActivityIdentity,
): OpenSession[] {
  const liveHarnesses = sessions.filter(isLiveHarness);
  const exactLeaf = identity.leafKey
    ? liveHarnesses.filter((session) => session.leafKey === identity.leafKey)
    : [];
  if (exactLeaf.length > 0) return exactLeaf;
  if (!identity.lifecycleId) return [];
  return liveHarnesses.filter(
    (session) => !session.leafKey && session.lifecycleId === identity.lifecycleId,
  );
}

export function summarizeChatActivity(
  sessions: OpenSession[],
  identity: ChatActivityIdentity,
): ChatActivitySummary | undefined {
  const seats = boundSessions(sessions, identity)
    .map((session) => ({
      id: session.id,
      role: sessionSeatRole(session),
      state: activityState(session.turnState, session.controlState),
    }))
    .sort((left, right) => left.role.localeCompare(right.role) || left.id.localeCompare(right.id));
  if (seats.length === 0) return undefined;

  const state = seats.reduce<ChatActivityState>(
    (highest, seat) =>
      ACTIVITY_RANK[seat.state] > ACTIVITY_RANK[highest] ? seat.state : highest,
    "idle",
  );
  return {
    state,
    label: activityLabel(state),
    detail: seats.map((seat) => `${seat.role}: ${activityLabel(seat.state)}`).join("; "),
  };
}

export function ChatActivityIndicator({ summary }: { summary: ChatActivitySummary | undefined }) {
  if (!summary) return null;
  const accessible = `Chat activity: ${summary.label}. ${summary.detail}`;
  return (
    <span
      className={shell}
      role="status"
      aria-label={accessible}
      title={accessible}
      data-testid="chat-activity"
      data-chat-activity={summary.state}
    >
      <span className={marker({ state: summary.state })} aria-hidden="true" />
      <span className={label({ state: summary.state })}>{summary.label}</span>
    </span>
  );
}
