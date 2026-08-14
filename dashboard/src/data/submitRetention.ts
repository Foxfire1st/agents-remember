import type { SubmitPhase, SubmitRecord } from "./submitMachine";

// FEUI-L5 F4 — bounded newest-tail retention, matching the repository's append-log compactors:
// keep a finite inspection window and separately protect live records whose reconciliation or
// manual endgame is still actionable. Without this boundary, every full message remains in the
// long-lived browser store and every upsert copies an ever-growing array.

export const SUBMIT_HISTORY_INSPECTOR_WINDOW = 64;
export const SUBMIT_QUEUE_RETENTION_WINDOW = 64;

const PROTECTED_SUBMIT_PHASES: ReadonlySet<SubmitPhase> = new Set([
  "sending",
  "queued",
  "delivering",
  "ambiguous",
  "reconciling",
  "endgame",
  "route-error",
  "generation-lost",
]);

export function isProtectedSubmitRecord(record: SubmitRecord): boolean {
  return PROTECTED_SUBMIT_PHASES.has(record.phase);
}

/** Newest settled inspector tail plus every still-actionable record, in original order. */
export function compactSubmitHistory(records: readonly SubmitRecord[]): SubmitRecord[] {
  const protectedIds = new Set(
    records.filter(isProtectedSubmitRecord).map((record) => record.requestId),
  );
  const settledIds = new Set(
    records
      .filter((record) => !protectedIds.has(record.requestId))
      .slice(-SUBMIT_HISTORY_INSPECTOR_WINDOW)
      .map((record) => record.requestId),
  );
  return records.filter(
    (record) => protectedIds.has(record.requestId) || settledIds.has(record.requestId),
  );
}

/** Queue truth is unavailable before UA-8, so this is retention only: newest client-known rows. */
export function compactSubmitQueue<T>(entries: readonly T[]): T[] {
  return entries.slice(-SUBMIT_QUEUE_RETENTION_WINDOW);
}
