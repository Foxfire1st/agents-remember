import type { ReviewFamilyContextEntry, ReviewFamilyGuarantee } from '../../data/review';
import { guaranteeComparison } from '../../data/review';
import { GuaranteeTextChange, OneSidedGuaranteeLabel, guaranteeTextChange, useTreeComparison } from './IntentWordDiff';
import { guaranteeRevisionLabels, revisionMeta } from './statementWording';
import { DiffPane } from '../changeset/DiffPane';
import type { DiffMode as DiffLayout } from '../changeset/DiffPane';
import { card, sectionLabel, prose, muted } from './familyCenterStyles';

function GuaranteeBlock({ guarantee, side }: { guarantee: ReviewFamilyGuarantee; side?: string }) {
  return (
    <div
      className={card}
      data-review-content="guarantee"
      data-testid="review-center-guarantee"
      data-revision={guarantee.revision_id}
    >
      <p className={sectionLabel}>Joint guarantee{side ? ` · ${side}` : ''}</p>
      <p className={prose}>{guarantee.joint_guarantee}</p>
      <p className={muted}>State at origin: {guarantee.state_at_origin}</p>
      <details data-disclosure="identifiers">
        <summary>Guarantee record</summary>
        <p className={muted}>
          family {guarantee.family_id}
          {side ? ` · ${side} snapshot` : ''} · revision {guarantee.revision_id} · version{' '}
          {guarantee.display_version}
          {guarantee.acceptance_ref ? ` · accepted by ${guarantee.acceptance_ref}` : ''}
        </p>
      </details>
    </div>
  );
}

export function GuaranteeComparisonBlock({
  entry,
  layout,
}: {
  entry: ReviewFamilyContextEntry;
  layout: DiffLayout;
}) {
  // A tree comparison's changed guarantee text reads as one word-diffed passage (MIK-R35).
  const wordDiff = useTreeComparison();
  const textChange = wordDiff ? guaranteeTextChange(entry) : null;
  if (textChange) return <GuaranteeTextChange {...textChange} />;
  const comparison = guaranteeComparison(entry);
  if (comparison.kind === 'unrecorded') {
    return (
      <p className={muted} data-testid="review-center-guarantee-unrecorded">
        no guarantee is compared here: neither snapshot selected a family revision for this family.
        The composition&apos;s own statement is: {comparison.detail}
      </p>
    );
  }
  if (comparison.kind === 'one_sided') {
    const other = comparison.side === 'before' ? 'after' : 'before';
    return (
      <div data-testid="review-center-guarantee-one-sided" data-side={comparison.side}>
        {wordDiff ? (
          <OneSidedGuaranteeLabel side={comparison.side} other={entry[other]} />
        ) : (
          <p className={muted}>
            {comparison.side === 'after' ? 'Added guarantee' : 'Removed guarantee'} · recorded on{' '}
            {comparison.side} only · no {other} revision
          </p>
        )}
        <GuaranteeBlock guarantee={comparison.guarantee} side={comparison.side} />
      </div>
    );
  }
  if (comparison.kind === 'unchanged_revision') {
    return (
      <div data-testid="review-center-guarantee-unchanged">
        <p className={muted}>Guarantee unchanged · same recorded revision</p>
        <GuaranteeBlock guarantee={comparison.guarantee} />
      </div>
    );
  }
  if (comparison.kind === 'identical_text') {
    // MIK-R31 rule 3: the guarantee is the family's one authored text field, so identical text is
    // "wording unchanged", shown once, with the revisions as compact metadata and the IDs in details.
    const { before, after } = comparison;
    return (
      <div data-testid="review-center-guarantee-identical-text">
        <p className={muted}>
          Wording unchanged · {revisionMeta(...guaranteeRevisionLabels(before, after))}
        </p>
        <GuaranteeBlock guarantee={after} />
        <details data-disclosure="identifiers">
          <summary>Revision records</summary>
          <p className={muted}>
            before revision {before.revision_id} · after revision {after.revision_id}
          </p>
        </details>
      </div>
    );
  }
  return <ChangedGuarantee before={comparison.before} after={comparison.after} layout={layout} />;
}

// A changed guarantee's two texts as a diff that sizes to its content (`fit`): the selected family
// adds no scroll container of its own on the review page (E6).
function ChangedGuarantee({
  before,
  after,
  layout,
}: {
  before: ReviewFamilyGuarantee;
  after: ReviewFamilyGuarantee;
  layout: DiffLayout;
}) {
  return (
    <div data-testid="review-center-guarantee-changed">
      <p className={muted}>
        the family guarantee changed between {before.revision_id} and {after.revision_id}:
      </p>
      <DiffPane before={before.joint_guarantee} after={after.joint_guarantee} language="text" mode={layout} collapse={false} fit />
      <p className={muted}>
        before revision {before.revision_id} · after revision {after.revision_id}
      </p>
    </div>
  );
}

