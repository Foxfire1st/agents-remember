// The status chip of the review's top line (MIK-R89 v2 rule 6, W18). One button, mounted from the
// first paint and never removed: its place is reserved by a fixed width, so "reading…" during a read
// moves nothing (rule 9, evidence 10). A click opens the four-row popover with the owner's sentence.
import type { RefObject } from 'react';
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components';

import { css } from '../../../../styled-system/css';
import type { ChipFacts } from './statusChipModel';

const chip = css({
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.4rem',
  // The reserved place: wide enough for the longest label, so a state change shifts nothing.
  width: '13.5rem',
  flex: 'none',
  overflow: 'hidden',
  whiteSpace: 'nowrap',
  textOverflow: 'ellipsis',
  fontFamily: 'mono',
  fontSize: '0.72rem',
  lineHeight: '1.2',
  paddingBlock: '0.25rem',
  paddingInline: '0.55rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '999px',
  background: 'transparent',
  color: 'muted',
  cursor: 'pointer',
  '&[data-tone="warning"]': { color: 'amber', borderColor: 'amber' },
  '&[data-tone="unavailable"]': { color: 'alarm', borderColor: 'alarm' },
  '&[data-tone="current"]': { color: 'mint' },
  _focusVisible: { outlineWidth: '2px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '2px' },
});

const dot = css({ width: '0.45rem', height: '0.45rem', borderRadius: '50%', background: 'currentColor', flex: 'none' });

// The mockup's status popover (D401; app.js `statusPop`, styles.css `.statuspop`, `.label`): a
// 380 px panel hanging from the comparison anchor, a cyan uppercase label, rows ruled under each
// other with their names dim, and the reason and pointer in the muted text. The mockup's `--dim` and
// `--rule` are drawn from the theme's muted and grid colours.
const popup = css({
  zIndex: 110,
  width: 'min(23.75rem, calc(100vw - 1.5rem))',
  padding: '0.7rem 0.8rem',
  background: 'bgPanel',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  boxShadow: '0 6px 20px rgba(0, 0, 0, 0.5)',
  fontFamily: 'mono',
  fontSize: '0.75rem',
  lineHeight: '1.5',
  '& table': { borderCollapse: 'collapse', width: '100%', margin: '0.4rem 0' },
  '& td': {
    padding: '0.2rem 0',
    borderBottom: '1px solid color-mix(in oklab, token(colors.grid) 55%, transparent)',
    fontSize: '0.72rem',
    verticalAlign: 'top',
  },
  '& td:first-child': { color: 'color-mix(in oklab, token(colors.muted) 82%, token(colors.bg))', width: '45%' },
  '& p': { margin: '0.4rem 0 0', color: 'muted', fontSize: '0.72rem' },
});

const heading = css({ color: 'cyan', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.1em' });

export function StatusChip({
  facts,
  comparison,
  anchorRef,
}: {
  facts: ChipFacts;
  comparison: number | null;
  // The comparison selector's button: the popover hangs from it, as the mockup's hangs from the
  // comparison anchor; from the chip itself when none is given.
  anchorRef?: RefObject<HTMLElement | null>;
}) {
  return (
    <DialogTrigger>
      <Button
        className={chip}
        data-testid="review-status-chip"
        data-tone={facts.tone}
        aria-label={`Branch status: ${facts.label}`}
      >
        <span className={dot} aria-hidden="true" />
        <span data-testid="review-status-label">{facts.label}</span>
      </Button>
      <Popover className={popup} placement="bottom end" {...(anchorRef ? { triggerRef: anchorRef } : {})}>
        <Dialog aria-label="Branch status" data-testid="review-status-popover" data-kbzone="review">
          <div className={heading} data-testid="review-status-heading">
            Branch status{comparison === null ? '' : ` · comparison #${comparison}`}
          </div>
          <table>
            <tbody>
              {facts.rows.map(([name, value]) => (
                <tr key={name} data-testid="review-status-row">
                  <td>{name}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {facts.reason ? <p data-testid="review-status-reason">{facts.reason}</p> : null}
          <p>Raw record: Technical details, at the bottom of the review.</p>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}
