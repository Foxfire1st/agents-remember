// The walked tree (MIK-R39): the family tree a reader has walked, kept across in-tree selections.
//
// The server composes a family context per selected subject: one family for a family subject, every
// containing family for an invariant subject. Drawn from the latest answer alone, the tree lost the
// rows of an earlier subject at every selection. The walk keeps them: every admitted answer's
// families are folded into one list, so a selection of a row the tree shows only ever adds rows, and
// `k` can step back to every change the reader passed. A family that is not in the selected subject's
// context is a kept family; its rows were read for an earlier subject and are shown tagged as such.
//
// Everything here is presentation of answers already read; no request is made and nothing is stored
// outside the mounted reviewer. What starts the walk afresh is decided by the selection that causes
// it (`TreeIntent`: an in-tree selection keeps, every other selection does not), and by the answer: one
// of another comparison (the cache's `sameGeneration`) is never shown beside the rows of this one.

import { useState } from 'react';

import type { ReviewFamilyContext, ReviewPayload } from '../../data/review';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyRevisionContext,
} from '../../data/reviewFamily';
import { mergeChangeKinds } from './changeTriage';
import { mergeMember } from './familyWalkMerge';
import {
  type ComparisonGeneration,
  comparisonGenerationOf,
  sameGeneration,
} from './ReviewReadCache';

export interface WalkedSubject {
  kind: 'family' | 'invariant';
  id: string;
}

export interface WalkedFamily {
  entry: ReviewFamilyContextEntry;
  // The subject whose answer last carried this family.
  readFor?: WalkedSubject;
  // The answer that last carried this family, whole and already read: whatever that answer carries
  // beside the family context (MIK-R43 rule 10: the recorded effect steps of its rows) stays with the
  // kept family without another request. A new comparison drops it with the tree.
  answer: ReviewPayload;
}

// The reader's latest selection, numbered so the walk applies each one once: `keep` for an in-tree
// selection, otherwise the selection starts the tree afresh.
export interface TreeIntent {
  id: number;
  keep: boolean;
}

export interface Walk {
  scope: string;
  intent: number;
  generation: ComparisonGeneration | null;
  // The answer last folded in; also what makes folding idempotent.
  source: ReviewPayload | null;
  // Ascending by family identifier: the server's order of families within one context.
  families: WalkedFamily[];
  // The families of the selected subject's own context (the latest answer).
  current: ReadonlySet<string>;
  // An outside selection dropped the kept families already; the answer it asks for replaces the rest.
  replaceNext: boolean;
  notice: string | null;
}

export const MOVED_NOTICE =
  'The comparison changed, so the tree starts again with the families of this subject.';

export const NO_FAMILY_CONTEXT: ReviewFamilyContext = {
  state: 'unavailable',
  detail: 'This answer carries no family context.',
  entries: [],
  families_total: 0,
  families_returned: 0,
  families_remaining: 0,
  membership_rows_total: 0,
  unique_member_revision_total: 0,
  references: {
    relationship_union: '',
    source_inventory: '',
    evidence_links: '',
    observations: '',
    assessments: '',
    applicability: '',
    join_key: '',
    detail: '',
  },
  limitations: [],
};

function emptyWalk(scope: string, intent: number): Walk {
  return {
    scope,
    intent,
    generation: null,
    source: null,
    families: [],
    current: new Set(),
    replaceNext: false,
    notice: null,
  };
}

function subjectOf(payload: ReviewPayload): WalkedSubject | undefined {
  const selection = payload.knowledge.revision_selection;
  return selection ? { kind: selection.record_kind, id: selection.record_id } : undefined;
}

// How far a roster walk of one side has come: the members the server counted as returned.
function progress(side: ReviewFamilyRevisionContext): number {
  return side.page?.counts.primary_items_returned ?? side.members.length;
}

// Two reads of one side: every member either returned stays, and the side that walked further
// (or, equal, the newer) gives the position, so the continuation control holds the furthest cursor.
function mergeSide(
  prior: ReviewFamilyRevisionContext,
  next: ReviewFamilyRevisionContext,
): ReviewFamilyRevisionContext {
  if (
    prior.state !== 'recorded' ||
    next.state !== 'recorded' ||
    prior.family_revision_id !== next.family_revision_id
  )
    return next;
  const ahead = progress(next) >= progress(prior) ? next : prior;
  const members = new Map(prior.members.map((member) => [member.member_id, member]));
  for (const member of next.members)
    members.set(member.member_id, mergeMember(members.get(member.member_id), member) ?? member);
  return { ...ahead, members: [...members.values()] };
}

function sideComplete(side: ReviewFamilyRevisionContext): boolean {
  return (
    side.state === 'not_recorded' ||
    (side.page?.complete === true &&
      side.members.length === side.members_total &&
      side.members.every((member) => member.state === 'recorded'))
  );
}

function mergeEntry(
  prior: ReviewFamilyContextEntry,
  next: ReviewFamilyContextEntry,
): ReviewFamilyContextEntry {
  const before = mergeSide(prior.before, next.before);
  const after = mergeSide(prior.after, next.after);
  const kinds = mergeChangeKinds(prior.change_kinds, next.change_kinds);
  const whole = next.state === 'partial' && sideComplete(before) && sideComplete(after);
  return {
    ...next,
    before,
    after,
    state: whole ? 'recorded' : next.state,
    ...(kinds === undefined ? {} : { change_kinds: kinds }),
  };
}

function mergeFamilies(
  known: WalkedFamily[],
  entries: ReviewFamilyContextEntry[],
  readFor: WalkedSubject | undefined,
  answer: ReviewPayload,
): WalkedFamily[] {
  const byId = new Map(known.map((family) => [family.entry.family_id, family]));
  for (const entry of entries) {
    const prior = byId.get(entry.family_id);
    byId.set(entry.family_id, {
      entry: prior ? mergeEntry(prior.entry, entry) : entry,
      readFor,
      answer,
    });
  }
  return [...byId.values()].sort((a, b) =>
    a.entry.family_id < b.entry.family_id ? -1 : a.entry.family_id > b.entry.family_id ? 1 : 0,
  );
}

// What the answer itself calls the subject it was read for: a family's label, or a member's label.
// The last resort is the identifier with its hyphens read as spaces, never a bare identifier.
export function subjectTitle(answer: ReviewPayload, subject: WalkedSubject): string {
  const entries = answer.family_context?.entries ?? [];
  const label =
    subject.kind === 'family'
      ? entries.find((entry) => entry.family_id === subject.id)?.display_label
      : entries
          .flatMap((entry) => [...entry.before.members, ...entry.after.members])
          .find((member) => member.invariant_id === subject.id)?.display_label;
  return label ?? subject.id.replace(/-/g, ' ');
}

export function keptFamilies(walk: Walk): WalkedFamily[] {
  return walk.families.filter((family) => !walk.current.has(family.entry.family_id));
}

// A task-context answer compares no knowledge: it leaves the snapshot pair the walk holds as it is.
function carriedGeneration(
  known: ComparisonGeneration | null,
  next: ComparisonGeneration,
): ComparisonGeneration {
  return { trees: next.trees, snapshots: next.snapshots ?? known?.snapshots };
}

// Fold one answer into the walk. An answer of another comparison, or the first one after an outside
// selection, replaces what the walk held.
function absorb(walk: Walk, payload: ReviewPayload): Walk {
  const next = comparisonGenerationOf(payload);
  const moved = walk.generation !== null && !sameGeneration(walk.generation, next);
  const entries = payload.family_context?.entries ?? [];
  const known = moved || walk.replaceNext ? [] : walk.families;
  return {
    ...walk,
    generation: moved ? next : carriedGeneration(walk.generation, next),
    source: payload,
    families: mergeFamilies(known, entries, subjectOf(payload), payload),
    current: new Set(entries.map((entry) => entry.family_id)),
    replaceNext: false,
    notice: moved && keptFamilies(walk).length > 0 ? MOVED_NOTICE : null,
  };
}

// The walk a selection leaves behind: an in-tree selection changes nothing; any other drops the kept
// families at once (the tree is then the answer on screen's own) and marks the walk to be replaced by
// the answer the selection asks for.
function rebase(walk: Walk, payload: ReviewPayload, scope: string, intent: TreeIntent): Walk {
  if (walk.scope !== scope) return absorb(emptyWalk(scope, intent.id), payload);
  if (walk.intent === intent.id) return walk;
  if (intent.keep) return { ...walk, intent: intent.id, replaceNext: false };
  return { ...absorb(emptyWalk(scope, intent.id), payload), replaceNext: true };
}

// Pure and idempotent: the same walk comes back when nothing is new, so a render can store the result.
export function advanceWalk(
  walk: Walk,
  payload: ReviewPayload,
  scope: string,
  intent: TreeIntent,
): Walk {
  const based = rebase(walk, payload, scope, intent);
  return based.source === payload ? based : absorb(based, payload);
}

// The walk of the reviewer's tree, advanced with every answer the workspace shows. The result is
// stored in the same render that finds it new (derived state, never an effect), so no frame draws a
// tree that lacks the answer on screen.
export function useWalkedTree(payload: ReviewPayload, scope: string, intent: TreeIntent): Walk {
  const [walk, setWalk] = useState<Walk>(() => emptyWalk('', 0));
  const next = advanceWalk(walk, payload, scope, intent);
  if (next !== walk) setWalk(next);
  return next;
}
