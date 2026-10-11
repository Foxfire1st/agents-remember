// The outline owns the canonical-record/selector join; labels and revisions are not record keys.
import type { ReviewOutline } from '../../data/review';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import type { ReviewSubject } from './ReviewNavigation';
import { SubjectWorklistItems } from './LeafKnowledgeChanges';

export function canonicalWorklistRecord(
  outline: ReviewOutline | undefined,
  subject: ReviewSubject,
): string | undefined {
  if (outline?.state !== 'outlined') return undefined;
  const rows = subject.kind === 'family' ? outline.families : outline.invariants;
  return rows.find((row) => row.selector_id === subject.id)?.id;
}

export function SubjectKnowledge({
  read,
  outline,
  subject,
  familyId,
  comparison,
}: {
  read?: ReviewTreesRead | null;
  outline?: ReviewOutline;
  subject?: ReviewSubject;
  familyId?: string;
  comparison?: number;
}) {
  if (comparison === undefined || (!subject && !familyId)) return null;
  const selected = subject ?? { kind: 'family' as const, id: familyId! };
  return (
    <SubjectWorklistItems
      read={read}
      recordId={canonicalWorklistRecord(outline, selected)}
      reviewComparison={comparison}
    />
  );
}
