import { sessionCockpitStore } from "./sessionCockpitStore";
import {
  fromTerminalSessionInfo,
  sessionStore,
  subscribeSessionCatalogChanges,
} from "./sessions";
import { fetchTerminalSessionsOrNull } from "./terminal";

// The shared terminal-catalog poll driver (260715-FEUI-L2 S1/R1), hoisted out of Chats.tsx so ANY
// view — or none — keeps the session feed alive. Catalog rows have NO push channel by design: this
// poll is the authoritative reconciler (seat events in `seatEvents.ts` only pre-apply what the
// next beat confirms). Refcounted: Cockpit starts it unconditionally, so the one product-facing
// Chats cockpit consumes one interval — never a second timer.

export const CATALOG_REFRESH_INTERVAL_MS = 2500;

const LAST_ACTIVE_SESSION_KEY = "ar-dashboard:last-active-chat-session";

let catalogGeneration = 0;

export interface CatalogAuthority {
  isCurrent: () => boolean;
}

/** Capture the terminal-catalog authority owned by the current dev-bench selector target. */
export function captureCatalogAuthority(): CatalogAuthority {
  const generation = catalogGeneration;
  return { isCurrent: () => generation === catalogGeneration };
}

export function catalogAuthorityIsCurrent(authority: CatalogAuthority): boolean {
  return authority.isCurrent();
}

/**
 * Revoke terminal-catalog reads owned by the previous dev-bench selector target. This generation
 * check is necessary because an already-awaited fetch promise cannot be synchronously cancelled;
 * after reset it must resolve without changing the newer scenario's rows or poll health.
 */
export function resetCatalogPollForDev(): void {
  catalogGeneration += 1;
}

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
 * Make one live row the shared action route and reload preference. Landed/retired rows remain
 * inspectable in the cockpit, but never replace the live id consumed by gates and composers.
 */
export function preferLiveSession(id: string | null): boolean {
  if (!id) return false;
  const row = sessionStore.getState().sessions.find((session) => session.id === id);
  if (!row || (row.status ?? "running") !== "running") return false;
  sessionStore.getState().setActive(row.id);
  writeLastActiveSessionId(row.id);
  return true;
}

/**
 * One catalog fetch → session-store hydrate (moved verbatim from Chats.tsx). Every read also
 * records a poll-health beat (R15/F3): `null` (network/HTTP failure) counts as a missed beat so
 * a dead poll surfaces as the rail's stale banner instead of freezing rows silently.
 */
export async function hydrateTerminalSessionsFromCatalog(
  allowEmpty: boolean,
  excludeSessionIds: ReadonlySet<string> = new Set(),
  authority: CatalogAuthority = captureCatalogAuthority(),
): Promise<boolean> {
  // An enclosing operation may have crossed reset before it reached this call. Reject its original
  // authority before transport so it cannot start a catalog read against the successor fixture.
  if (!catalogAuthorityIsCurrent(authority)) return false;
  const list = await fetchTerminalSessionsOrNull();
  if (!catalogAuthorityIsCurrent(authority)) return false;
  sessionCockpitStore.getState().recordPollBeat(list !== null);
  if (list === null || (list.length === 0 && !allowEmpty)) return true;
  const sessions = list
    .filter((session) => !excludeSessionIds.has(session.id))
    .map(fromTerminalSessionInfo);
  sessionStore.getState().hydrate(sessions, readLastActiveSessionId());
  return true;
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

let reconcilerRefs = 0;
let releaseCatalogChanges: (() => void) | null = null;

/**
 * Own the eager catalog hydrate and cross-tab invalidation listener for the cockpit lifetime.
 *
 * This is the non-timer half of catalog reconciliation that used to live in the legacy Chats
 * component. A remote termination is removed locally before the confirming read and excluded from
 * that read, so a stale catalog echo cannot resurrect it. Create/leaf invalidations re-read with
 * `allowEmpty` because the broadcast itself proves that applying an empty authoritative snapshot is
 * intentional. Refcounting keeps StrictMode/remounts from installing duplicate listeners.
 */
export function startCatalogReconciler(): () => void {
  reconcilerRefs += 1;
  if (reconcilerRefs === 1) {
    void hydrateTerminalSessionsFromCatalog(false);
    releaseCatalogChanges = subscribeSessionCatalogChanges((reason, sessionId) => {
      if (reason === "terminate" && sessionId) {
        sessionStore.getState().setStatus(sessionId, "terminated");
        sessionStore.getState().close(sessionId);
        void hydrateTerminalSessionsFromCatalog(true, new Set([sessionId]));
        return;
      }
      void hydrateTerminalSessionsFromCatalog(true);
    });
  }

  let released = false;
  return () => {
    if (released) return;
    released = true;
    reconcilerRefs -= 1;
    if (reconcilerRefs === 0) {
      releaseCatalogChanges?.();
      releaseCatalogChanges = null;
    }
  };
}
