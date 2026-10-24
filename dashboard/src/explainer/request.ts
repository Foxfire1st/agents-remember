// One signal: "open the first-request form for this leaf's explainer". MIK-R106 v2 rule 3 says
// ✦ Explain and a ⊕ on an invariant or a block both open the column on the first-request form;
// they are pressed outside the column (the review's top line, an object's ⊕), so the ask is held
// here until the column is shown, and the column reads it on mount.
//
// It carries nothing to send and starts nothing: the form it opens is filled by him, and nothing
// exists until his Send (rules 3 and 10).
import type { ExplainerAddress, ExplainerStalePart } from './client';
import { sameExplainerAddress } from './client';

export interface FirstRequestBody {
  address: ExplainerAddress;
  /** "Regenerate stale parts" with no seat: the parts the form is pre-filled with (MIK-R106 rule 3). */
  staleParts: ExplainerStalePart[];
  /** Stale grades pre-filled with the request (MIK-R187 rule 8). */
  staleGrades: string[];
  /** Optional message text carried into the form. */
  message?: string;
}

export interface AskFirstRequestOptions {
  staleGrades?: string[];
  message?: string;
}

export const EXPLAINER_TARGET_PREFIX = 'explainer:';
const MAX_KNOWN_EXPLAINERS = 64;

interface KnownExplainerTarget {
  address: ExplainerAddress;
  agentIds: Set<string>;
  activeSeat: boolean;
}

let current: FirstRequestBody | null = null;
const listeners = new Set<(body: FirstRequestBody) => void>();
const knownExplainers = new Map<string, KnownExplainerTarget>();
let activeColumnAddress: ExplainerAddress | null = null;

const keyOf = (address: ExplainerAddress): string =>
  `${address.repository}\u0000${address.master}\u0000${address.leaf}`;

export function placeholderExplainerTargetId(address: ExplainerAddress): string {
  return `${EXPLAINER_TARGET_PREFIX}${address.repository}/${address.master}/${address.leaf}`;
}

function evictOldestIfNeeded(hasKey: boolean): void {
  if (hasKey || knownExplainers.size < MAX_KNOWN_EXPLAINERS) return;
  const oldest = knownExplainers.keys().next().value;
  if (oldest !== undefined) knownExplainers.delete(oldest);
}

export function registerExplainerTarget(
  address: ExplainerAddress,
  facts: { agentId: string | null; activeSeat: boolean },
): void {
  if (!address.repository || !address.master || !address.leaf) return;
  const key = keyOf(address);
  const existing = knownExplainers.get(key);
  const agentIds = existing ? existing.agentIds : new Set<string>();
  agentIds.add(placeholderExplainerTargetId(address));
  if (facts.agentId) agentIds.add(facts.agentId);
  evictOldestIfNeeded(Boolean(existing));
  knownExplainers.set(key, { address, agentIds, activeSeat: facts.activeSeat });
}

export function setActiveExplainerColumn(address: ExplainerAddress | null): void {
  const valid = Boolean(address && address.repository && address.master && address.leaf);
  activeColumnAddress = valid ? address : null;
}

function parsePrefixedTarget(targetId: string): ExplainerAddress | null {
  if (!targetId.startsWith(EXPLAINER_TARGET_PREFIX)) return null;
  const parts = targetId.slice(EXPLAINER_TARGET_PREFIX.length).split('/');
  if (parts.length < 3) return null;
  const address = { repository: parts[0], master: parts[1], leaf: parts.slice(2).join('/') };
  const known = knownExplainers.get(keyOf(address));
  return !known || !known.activeSeat ? address : null;
}

function lookupRegisteredTarget(targetId: string): { found: boolean; address: ExplainerAddress | null } {
  for (const entry of knownExplainers.values()) {
    if (entry.agentIds.has(targetId)) {
      return { found: true, address: entry.activeSeat ? null : entry.address };
    }
  }
  return { found: false, address: null };
}

export function archivedExplainerForTarget(targetId: string): ExplainerAddress | null {
  const prefixed = parsePrefixedTarget(targetId);
  if (prefixed) return prefixed;
  const registered = lookupRegisteredTarget(targetId);
  if (registered.found) return registered.address;
  if (!activeColumnAddress) return null;
  const known = knownExplainers.get(keyOf(activeColumnAddress));
  return !known || !known.activeSeat ? activeColumnAddress : null;
}

export function askFirstRequest(
  address: ExplainerAddress,
  staleParts: ExplainerStalePart[] = [],
  options?: string[] | AskFirstRequestOptions,
): void {
  const staleGrades = Array.isArray(options) ? options : (options?.staleGrades ?? []);
  const message = Array.isArray(options) ? undefined : options?.message;
  current = {
    address,
    staleParts,
    staleGrades,
    ...(message !== undefined ? { message } : {}),
  };
  for (const listener of listeners) listener(current);
}

/** The ask pending for this address, or null. A stale ask for another leaf is not taken. */
export function pendingFirstRequest(address: ExplainerAddress): FirstRequestBody | null {
  return current && sameExplainerAddress(current.address, address) ? current : null;
}

/** His Cancel, or his Send: the ask is done with. */
export function clearFirstRequest(address: ExplainerAddress): void {
  if (current && sameExplainerAddress(current.address, address)) current = null;
}

export function onFirstRequest(listener: (body: FirstRequestBody) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Tests only: forget every ask and listener this module holds. */
export function resetFirstRequest(): void {
  current = null;
  listeners.clear();
  knownExplainers.clear();
  activeColumnAddress = null;
}
