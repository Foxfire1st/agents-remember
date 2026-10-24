/**
 * The basis legend and the claim chips (MIK-R102 v2 rule 7; MIK-R106 v2 rule 6; R104 v3 rule 4).
 *
 * Every claim carries its basis -- a recorded fact, a mechanical signal, the explainer's inference,
 * or `intended` -- and the legend names all four. The chips and the legend are drawn from the
 * revision's product-written claim map, outside the frame, so a page cannot relabel its own claims.
 *
 * A claim's link opens its block in the Review view and selects it (MIK-R89 v2 rule 9). The view
 * does not open it: it hands the block id to the page that owns the selection.
 */

import { css } from '../../../../styled-system/css';
import type { ClaimBasis, ClaimLink, ExplanationClaim } from './explainerApi';
import { BASIS_LEGEND, basisLabel } from './explainerModel';

const bar = css({
  display: 'flex',
  gap: '0.6rem',
  alignItems: 'baseline',
  flexWrap: 'wrap',
  padding: '0.3rem 0.55rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
  fontFamily: 'mono',
  fontSize: '0.7rem',
  color: 'muted',
});

const chip = css({
  fontFamily: 'mono',
  fontSize: '0.68rem',
  padding: '0 0.3rem',
  borderRadius: '2px',
  borderWidth: '1px',
  borderStyle: 'solid',
});

const recorded = css({ color: 'mint', borderColor: 'mint' });
const mechanical = css({ color: 'cyan', borderColor: 'cyan' });
const inference = css({ color: 'purple', borderColor: 'purple' });
const intended = css({ color: 'amber', borderColor: 'amber' });

const CHIP: Record<ClaimBasis, string> = {
  'recorded-fact': recorded,
  'mechanical-signal': mechanical,
  'presenter-inference': inference,
  intended,
};

const legend = css({ display: 'grid', gap: '0.15rem', padding: '0.35rem 0.55rem' });
const legendRow = css({ display: 'flex', gap: '0.45rem', alignItems: 'baseline', fontSize: '0.72rem' });
const legendText = css({ color: 'muted' });
const list = css({ margin: 0, paddingLeft: '1.1rem', display: 'grid', gap: '0.2rem' });
const claimRow = css({ fontSize: '0.76rem', display: 'flex', gap: '0.4rem', alignItems: 'baseline', flexWrap: 'wrap' });
const claimText = css({ color: 'ink' });
const link = css({
  fontFamily: 'mono',
  fontSize: '0.7rem',
  color: 'cyan',
  background: 'transparent',
  border: '0',
  padding: 0,
  cursor: 'pointer',
  textDecoration: 'underline',
});
const partHead = css({ fontFamily: 'mono', fontSize: '0.72rem', color: 'muted', margin: '0.35rem 0 0.1rem' });
const empty = css({ fontSize: '0.74rem', color: 'muted', margin: 0 });

export function ClaimChip({ basis }: { basis: ClaimBasis }) {
  return (
    <span className={`${chip} ${CHIP[basis]}`} data-testid="claim-chip" data-basis={basis}>
      {basisLabel(basis)}
    </span>
  );
}

export function BasisLegend() {
  return (
    <div className={bar} data-testid="basis-legend">
      <span>Each claim is marked:</span>
      {BASIS_LEGEND.map((entry) => (
        <span className={legendRow} key={entry.basis} data-testid="legend-entry" data-basis={entry.basis}>
          <ClaimChip basis={entry.basis} />
          <span className={legendText}>{entry.meaning}</span>
        </span>
      ))}
    </div>
  );
}

export interface ClaimListProps {
  claims: readonly ExplanationClaim[];
  parts: ReadonlyArray<{ id: string; title: string }>;
  onOpenBlock: (claim: ClaimLink) => void;
}

/**
 * The revision's claims, part by part, with their bases.
 *
 * This is the readable form of the claim map: it exists so the bases can be checked without trusting
 * anything drawn inside the frame.
 */
export function ClaimList({ claims, parts, onOpenBlock }: ClaimListProps) {
  if (!claims.length) {
    return (
      <section className={legend} data-testid="explanation-claims">
        <p className={empty}>No claim map was returned for this revision.</p>
      </section>
    );
  }
  const ordered = parts.length
    ? parts.map((part) => ({ part, claims: claims.filter((claim) => claim.part === part.id) }))
    : [{ part: { id: '', title: 'Claims' }, claims: [...claims] }];
  return (
    <section className={legend} data-testid="explanation-claims" aria-label="Claims and their bases">
      <p className={partHead}>Claims, as the product recorded them</p>
      {ordered.map(({ part, claims: rows }) =>
        rows.length ? (
          <div key={part.id || 'all'} data-testid="claim-part" data-part={part.id}>
            <p className={partHead}>{part.title || part.id}</p>
            <ol className={list}>
              {rows.map((claim) => (
                <li className={claimRow} key={claim.id} data-testid="claim" data-claim={claim.id} data-basis={claim.basis}>
                  <ClaimChip basis={claim.basis} />
                  {claim.text ? <span className={claimText}>{claim.text}</span> : null}
                  {claim.link ? (
                    <button
                      type="button"
                      className={link}
                      data-testid="claim-link"
                      data-block={claim.link.block}
                      title={`Open ${claim.link.label} in the Review view`}
                      onClick={() => onOpenBlock(claim.link as ClaimLink)}
                    >
                      {claim.link.label}
                    </button>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        ) : null,
      )}
    </section>
  );
}
