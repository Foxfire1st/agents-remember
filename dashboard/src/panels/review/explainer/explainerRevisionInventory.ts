import { text } from './explainerValues';

/** Advisory revision inventory fields carried on saved explanations and revision rows. */
export interface RevisionInventoryFields {
  /** Summary of figures, kit libraries, code excerpts, and unused assets shown by the page. */
  inventory?: string;
  /** Plain reason when the advisory inventory sidecar was left out at the task storage limit. */
  inventoryOmitted?: string;
}

/** Parse inventory summary or omission reason from a wire payload, preferring inventory over omission. */
export function readRevisionInventory(row: Record<string, unknown>): RevisionInventoryFields {
  const inventory = text(row.inventory);
  if (inventory) return { inventory };
  const inventoryOmitted = text(row.inventoryOmitted);
  return inventoryOmitted ? { inventoryOmitted } : {};
}

export function readRevisionKind(row: Record<string, unknown>): 'global' | 'focused' {
  if (text(row.kind) === 'focused') return 'focused';
  const focus = text(row.focus)?.trim() ?? '';
  if (
    focus &&
    !focus.startsWith('the change set of ') &&
    focus !== 'explain this change set end to end'
  ) {
    return 'focused';
  }
  return 'global';
}

export interface RevisionRowReaders<TParts, TClaims, TStale> {
  readNumber: (value: unknown) => number | null;
  readParts: (value: unknown) => TParts;
  readClaims: (value: unknown) => TClaims;
  readStale: (value: Record<string, unknown>) => TStale;
}

export function attachOptionalRevisionFields<
  TResult extends RevisionInventoryFields & {
    focus?: string;
    coverage?: string;
    removed?: boolean;
    bytes?: number;
    parts?: TParts;
    claims?: TClaims;
    stale?: TStale;
  },
  TParts,
  TClaims,
  TStale,
>(
  result: TResult,
  row: Record<string, unknown>,
  readers: RevisionRowReaders<TParts, TClaims, TStale>,
): void {
  const focus = text(row.focus);
  const coverage = text(row.coverage);
  const bytes = readers.readNumber(row.bytes);
  if (focus) result.focus = focus;
  if (coverage) result.coverage = coverage;
  Object.assign(result, readRevisionInventory(row));
  if (row.removed === true) result.removed = true;
  if (bytes !== null) result.bytes = bytes;
  if (Array.isArray(row.parts)) result.parts = readers.readParts(row.parts);
  if (Array.isArray(row.claims)) result.claims = readers.readClaims(row.claims);
  if (row.stale !== undefined) result.stale = readers.readStale(row);
}
