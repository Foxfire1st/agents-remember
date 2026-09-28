// The merge of one admitted family roster walk: a continuation page is folded into the family
// context already on screen only when it continues exactly that walk (same comparison, candidate,
// revision selection, family, side, guarantee and scope), and it never erases content already
// delivered. The read cycle (`ReviewReadCycle`) decides WHEN a continuation is admitted; this module
// owns WHAT an admitted continuation looks like.

import type {
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewPayload,
} from '../../data/review';

function mergeMember(
  previous: ReviewFamilyMember | undefined,
  next: ReviewFamilyMember,
): ReviewFamilyMember | null {
  if (!previous) return next;
  if (
    previous.invariant_revision_id !== next.invariant_revision_id ||
    (previous.state === 'recorded' &&
      next.state === 'recorded' &&
      previous.payload_digest !== next.payload_digest)
  )
    return null;
  const sources = new Map(previous.sources.map((source) => [source.claim_id, source]));
  for (const source of next.sources) {
    const known = sources.get(source.claim_id);
    if (known && JSON.stringify(known) !== JSON.stringify(source)) return null;
    sources.set(source.claim_id, source);
  }
  // Sparse later claims never erase the exact content already delivered for this membership.
  return { ...(previous.state === 'recorded' ? previous : next), sources: [...sources.values()] };
}

function mergeFamilySide(
  previous: ReviewFamilyRevisionContext,
  next: ReviewFamilyRevisionContext,
  cursor: string,
): ReviewFamilyRevisionContext | null {
  if (!sameFamilyWalk(previous, next)) return null;
  // Other walks are resent at page one by the server; they cannot reset an already advanced walk.
  if (next.page?.state !== 'continued') return previous;
  if (next.page.continued_from !== cursor) return null;
  if (previous.page?.continuation !== cursor) {
    return JSON.stringify(previous.page) === JSON.stringify(next.page) ? previous : null;
  }
  const members = new Map(previous.members.map((member) => [member.member_id, member]));
  for (const member of next.members) {
    const merged = mergeMember(members.get(member.member_id), member);
    if (!merged) return null;
    members.set(member.member_id, merged);
  }
  return {
    ...next,
    members: [...members.values()],
    detail: `Loaded ${members.size} exact member context(s) of ${next.members_total} recorded memberships across this roster walk.`,
  };
}

function sameFamilyWalk(
  previous: ReviewFamilyRevisionContext,
  next: ReviewFamilyRevisionContext,
): boolean {
  return [
    [previous.family_id, next.family_id],
    [previous.side, next.side],
    [previous.state, next.state],
    [previous.family_revision_id, next.family_revision_id],
    [previous.guarantee?.payload_digest, next.guarantee?.payload_digest],
    [JSON.stringify(previous.page?.scope), JSON.stringify(next.page?.scope)],
  ].every(([known, supplied]) => known === supplied);
}

function admittedFamilyContinuation(
  previous: ReviewPayload,
  next: ReviewPayload,
  cursor: string,
): boolean {
  const page = next.page;
  if (
    !previous.comparison ||
    !next.comparison ||
    !previous.family_context ||
    !next.family_context ||
    !page
  )
    return false;
  return [
    page.collection === 'family_members',
    page.state === 'continued',
    page.continued_from === cursor,
    !next.page_refusal,
    next.staleness.state !== 'stale',
    JSON.stringify(previous.comparison) === JSON.stringify(next.comparison),
    JSON.stringify(previous.candidate) === JSON.stringify(next.candidate),
    JSON.stringify(previous.knowledge.revision_selection) ===
      JSON.stringify(next.knowledge.revision_selection),
    previous.family_context.entries.length === next.family_context.entries.length,
  ].every(Boolean);
}

// This is presentation of one admitted walk, not another dataset or selection authority. The
// latest response still owns the primary statements, source inventory, evidence and assessments.
export function mergeFamilyContinuation(
  previous: ReviewPayload,
  next: ReviewPayload,
  cursor: string,
): ReviewPayload | null {
  if (!admittedFamilyContinuation(previous, next, cursor)) return null;
  const families = previous.family_context!;
  const incoming = next.family_context!;
  const byFamily = new Map(families.entries.map((entry) => [entry.family_id, entry]));
  const entries = [];
  let continued = 0;
  for (const entry of incoming.entries) {
    const known = byFamily.get(entry.family_id);
    if (!known || JSON.stringify(known.selection) !== JSON.stringify(entry.selection)) return null;
    const before = mergeFamilySide(known.before, entry.before, cursor);
    const after = mergeFamilySide(known.after, entry.after, cursor);
    if (!before || !after) return null;
    continued += [entry.before, entry.after].filter(
      (side) => side.page?.continued_from === cursor,
    ).length;
    const complete = [before, after].every(
      (side) =>
        side.state === 'not_recorded' ||
        (side.page?.complete &&
          side.members.length === side.members_total &&
          side.members.every((member) => member.state === 'recorded')),
    );
    entries.push({
      ...entry,
      before,
      after,
      state: complete ? ('recorded' as const) : entry.state,
      detail: `${entry.selection.statement}; before: ${before.detail}; after: ${after.detail}`,
    });
  }
  if (continued !== 1) return null;
  return {
    ...next,
    family_context: {
      ...incoming,
      entries,
      state: entries.every((entry) => entry.state === 'recorded') ? 'recorded' : incoming.state,
      unique_member_revision_total: new Set(
        entries.flatMap((entry) =>
          [entry.before, entry.after].flatMap((side) =>
            side.members.map((member) => member.invariant_revision_id),
          ),
        ),
      ).size,
      detail:
        'Loaded family context retains the exact members and source claims from these bounded roster walks.',
    },
  };
}
