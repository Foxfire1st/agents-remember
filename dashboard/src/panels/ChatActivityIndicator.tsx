import { sessionSeatRole, type OpenSession } from "../data/sessions";
import { sameTaskDocumentRef } from "../data/taskIdentity";
import type { TaskDocumentRef } from "../types/terminalCatalog";

// The chat activity of a task: what the live chat seats bound to it are doing, as one summary.
// The tasks column says it on the task's row: "needs input" and "working" as the row's word, the
// seats one by one in the row's hover (lifecycle-list/rowState.ts).
export type ChatActivityState = "needs-input" | "working" | "unknown" | "idle";

export interface ChatActivityIdentity {
  taskDocumentRef?: TaskDocumentRef;
  lifecycleId?: string;
}

export interface ChatActivitySummary {
  state: ChatActivityState;
  label: string;
  detail: string;
}

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
  const exactTask = identity.taskDocumentRef
    ? liveHarnesses.filter((session) =>
        sameTaskDocumentRef(session.taskDocumentRef, identity.taskDocumentRef),
      )
    : [];
  if (exactTask.length > 0) return exactTask;
  if (!identity.lifecycleId) return [];
  return liveHarnesses.filter(
    (session) => !session.taskDocumentRef && session.lifecycleId === identity.lifecycleId,
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
