import { limitedUntil, type ProviderLimitDocument } from "./provider_limits.ts";

const PROVIDER_LIMIT_CHECK_MS = 15_000;

export interface ProviderLimitScheduling {
  readonly now?: () => Date;
  readonly setTimeout?: (callback: () => void, delayMs: number) => unknown;
  readonly clearTimeout?: (handle: unknown) => void;
}

export interface DeferredProviderLimitAction {
  readonly observedAt: number;
  readonly scheduling: ProviderLimitScheduling;
}

export interface DeferredProviderLimitResume extends DeferredProviderLimitAction {
  readonly fire: (clearedAt?: string) => void;
}

interface ProviderLimitCheck {
  readonly scheduling: ProviderLimitScheduling;
  timer: unknown;
}

export interface ProviderLimitCheckScope {
  readonly signals: { readonly readProviderLimits?: () => ProviderLimitDocument };
  readonly resumes: ReadonlyMap<string, DeferredProviderLimitResume>;
  readonly limitFlushes: ReadonlyMap<string, DeferredProviderLimitAction>;
  readonly flushClearedParents: (parents: readonly string[]) => void;
  providerLimitCheck: ProviderLimitCheck | null;
}

export function startTimer(
  scheduling: ProviderLimitScheduling,
  callback: () => void,
  delayMs: number,
): unknown {
  if (scheduling.setTimeout !== undefined) return scheduling.setTimeout(callback, delayMs);
  return setTimeout(callback, delayMs);
}

export function stopTimer(scheduling: ProviderLimitScheduling, handle: unknown): void {
  if (handle === undefined) return;
  if (scheduling.clearTimeout !== undefined) scheduling.clearTimeout(handle);
  else clearTimeout(handle as ReturnType<typeof setTimeout>);
}

function clearedActions<Action extends DeferredProviderLimitAction>(
  actions: ReadonlyMap<string, Action>,
  document: ProviderLimitDocument,
  now: Date,
): { readonly seat: string; readonly pending: Action; readonly clearedAt: string }[] {
  const cleared: { seat: string; pending: Action; clearedAt: string }[] = [];
  for (const [seat, pending] of actions) {
    const clear = document.clearedSeats[seat];
    // The writer replaces the seat's limit with its clear marker. It samples
    // the clear time before publication, while the prior limit is still readable.
    if (clear === undefined) continue;
    if (limitedUntil(document, seat, now) !== null) continue;
    cleared.push({ seat, pending, clearedAt: clear.clearedAt });
  }
  return cleared;
}

/** Share one snapshot across the existing deferred fleet, and stop with it. */
export function syncProviderLimitCheck(scope: ProviderLimitCheckScope, lastCheckAt?: number): void {
  const read = scope.signals.readProviderLimits;
  const first = scope.resumes.values().next().value ?? scope.limitFlushes.values().next().value;
  const existing = scope.providerLimitCheck;
  if (read === undefined || first === undefined) {
    if (existing !== null) stopTimer(existing.scheduling, existing.timer);
    scope.providerLimitCheck = null;
    return;
  }
  if (existing !== null) return;

  const check: ProviderLimitCheck = { scheduling: first.scheduling, timer: undefined };
  const now = check.scheduling.now ?? (() => new Date());
  const dueAt = (lastCheckAt ?? first.observedAt) + PROVIDER_LIMIT_CHECK_MS;
  scope.providerLimitCheck = check;
  check.timer = startTimer(check.scheduling, () => {
    if (scope.providerLimitCheck !== check) return;
    const checkedAt = now();
    const document = read();
    for (const { pending, clearedAt } of clearedActions(scope.resumes, document, checkedAt)) {
      pending.fire(clearedAt);
    }
    const parents = clearedActions(scope.limitFlushes, document, checkedAt);
    if (parents.length > 0) scope.flushClearedParents(parents.map(({ seat }) => seat));
    if (scope.providerLimitCheck === check) scope.providerLimitCheck = null;
    syncProviderLimitCheck(scope, checkedAt.getTime());
  }, Math.max(0, dueAt - now().getTime()));
}
