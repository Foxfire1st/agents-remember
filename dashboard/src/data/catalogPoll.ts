import { sessionCockpitStore } from "./sessionCockpitStore";
import { fromTerminalSessionInfo, sessionStore } from "./sessions";
import { fetchTerminalSessionsOrNull } from "./terminal";

// The shared terminal-catalog poll driver (260715-FEUI-L2 S1/R1), hoisted out of Chats.tsx so ANY
// view — or none — keeps the session feed alive. Catalog rows have NO push channel by design: this
// poll is the authoritative reconciler (seat events in `seatEvents.ts` only pre-apply what the
// next beat confirms). Refcounted: Cockpit starts it unconditionally and Chats/Sessions consume
// the same interval — never a second timer.
//
// L8 DEPENDENCY: the Chats-cutover decision (leaf L8) explicitly depends on this hoist having
// landed — Chats now consumes this driver unchanged instead of owning the interval.

export const CATALOG_REFRESH_INTERVAL_MS = 2500;

const LAST_ACTIVE_SESSION_KEY = "ar-dashboard:last-active-chat-session";

export function readLastActiveSessionId(): string | null {
  try {
    return window.localStorage.getItem(LAST_ACTIVE_SESSION_KEY);
  } catch {
    return null;
  }
}

export function writeLastActiveSessionId(id: string | null): void {
  try {
    if (id) window.localStorage.setItem(LAST_ACTIVE_SESSION_KEY, id);
    else window.localStorage.removeItem(LAST_ACTIVE_SESSION_KEY);
  } catch {
    // localStorage can be unavailable in private contexts; it is only a UI preference.
  }
}

/**
 * One catalog fetch → session-store hydrate (moved verbatim from Chats.tsx). Every read also
 * records a poll-health beat (R15/F3): `null` (network/HTTP failure) counts as a missed beat so
 * a dead poll surfaces as the rail's stale banner instead of freezing rows silently.
 */
export async function hydrateTerminalSessionsFromCatalog(
  allowEmpty: boolean,
  excludeSessionIds: ReadonlySet<string> = new Set(),
): Promise<void> {
  const list = await fetchTerminalSessionsOrNull();
  sessionCockpitStore.getState().recordPollBeat(list !== null);
  if (list === null || (list.length === 0 && !allowEmpty)) return;
  const sessions = list
    .filter((session) => !excludeSessionIds.has(session.id))
    .map(fromTerminalSessionInfo);
  sessionStore.getState().hydrate(sessions, readLastActiveSessionId());
}

let driverRefs = 0;
let driverInterval: number | null = null;

/**
 * Subscribe to the shared 2500 ms poll driver; returns the matching release. The first subscriber
 * starts the interval, the last release stops it — consumers never see each other.
 */
export function startCatalogPollDriver(): () => void {
  driverRefs += 1;
  if (driverRefs === 1) {
    driverInterval = window.setInterval(() => {
      void hydrateTerminalSessionsFromCatalog(false);
    }, CATALOG_REFRESH_INTERVAL_MS);
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    driverRefs -= 1;
    if (driverRefs === 0 && driverInterval !== null) {
      window.clearInterval(driverInterval);
      driverInterval = null;
    }
  };
}
