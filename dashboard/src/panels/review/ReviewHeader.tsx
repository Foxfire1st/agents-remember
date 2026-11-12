// The review's top line (MIK-R89 v2 rules 1, 6 and 10): back, the title, the status chip and the
// comparison selector, with the change set's ⊕ on the comparison label (W29). It spans the tree and
// the reading column and never scrolls (it sits outside the page scroller); stacked, at 40rem and
// narrower, it wraps and scrolls with the page, every part inside the line (rule 11). Refresh and the
// keys reference close the line; a refresh's notice takes a row of its own under it
// (ReviewRefresh.tsx). Its controls are independent of subject reads: the chip keeps its place
// through every read and only its text changes.
import { css } from '../../../styled-system/css';
import { ReviewKeys } from './ReviewKeys';
import { ToggleButton, ToggleButtonGroup } from 'react-aria-components';
import { codeContextStore, useCodeContext, type CodeContext } from './reading/codeContextPreference';
import type { Crumb } from './reading/ReadingColumn';

const line = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.55rem',
  flex: 'none',
  minWidth: 0,
  paddingBlock: '0.2rem 0.55rem',
  // The controls fit one line above 40rem (the title box gives way); a row wraps only for the
  // refresh's notice there, and for the stacked lines at 40rem and narrower.
  flexWrap: 'wrap',
  '& button:not([role="tab"]):not([data-sidebar-side]):not([data-context-choice])': {
    background: 'transparent',
    color: 'muted',
    border: '1px solid token(colors.grid)',
    borderRadius: '3px',
    fontFamily: 'mono',
    fontSize: '0.72rem',
    lineHeight: '1.4',
    padding: '0.2rem 0.55rem',
    cursor: 'pointer',
    '&:hover': {
      background: 'color-mix(in oklab, token(colors.amber) 8%, transparent)',
      color: 'amber',
    },
  },
});

// Stacked, the title box keeps the first line beside back and sends the chip and the comparison to the
// next ones; its own parts wrap inside it, so none runs past the line or under the chip (rule 11).
const titleBox = css({
  display: 'flex',
  alignItems: 'baseline',
  gap: '0.5rem',
  minWidth: 0,
  flex: '1',
  fontSize: '0.85rem',
  '@media (max-width: 40rem)': { flex: '1 1 14rem', flexWrap: 'wrap', rowGap: '0.1rem' },
});

const label = css({ whiteSpace: 'nowrap', '@media (max-width: 40rem)': { whiteSpace: 'normal' } });

const titleText = css({
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  minWidth: 0,
  maxWidth: '14rem',
  flexShrink: 3,
  '@media (max-width: 40rem)': { flexBasis: '100%', maxWidth: 'none' },
});

// A record label gives way before the title does (D401: the mockup's title fits the 1440 px line) and
// keeps its first word; its whole text is its tooltip.
const recordText = css({
  color: 'muted',
  fontSize: '0.75rem',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  flexShrink: 100,
  minWidth: '9ch',
  '@media (max-width: 40rem)': { whiteSpace: 'normal' },
});

const crumb = css({
  flex: 1,
  minWidth: 0,
  fontSize: '0.75rem',
  color: 'muted',
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
});

const crumbSubject = css({ color: 'ink', fontWeight: 600 });

// The crumb's parts (pageParts.tsx `crumbOf`; mockup app.js `updateCrumb`, styles.css
// `.rhead .crumb b`): the change set, the selected subject's id or the lane in bold, and a block's
// file and range or a lane file's name.

function CrumbLine({ crumb: parts }: { crumb: Crumb }) {
  return (
    <div className={crumb} data-testid="review-crumb" title={[parts.base, parts.subject, parts.detail].filter(Boolean).join(' › ')}>
      {parts.base}
      {parts.subject === undefined ? null : (
        <>
          {' › '}
          <b className={crumbSubject}>{parts.subject}</b>
        </>
      )}
      {parts.detail ? ` › ${parts.detail}` : null}
    </div>
  );
}

// The mockup's segmented switch (styles.css `.seg`; ruling d9a1ec8f item 2): one bordered group, its
// buttons divided by a rule, the selected one amber on a faint amber ground. The review's shared
// control look leaves a `data-segment` group alone (ReviewLayout.tsx `reviewRoot`).
const segment = css({
  display: 'inline-flex',
  flex: 'none',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  overflow: 'hidden',
  '& button': {
    font: 'inherit',
    fontSize: '0.72rem',
    lineHeight: '1.4',
    paddingBlock: '0.2rem',
    paddingInline: '0.55rem',
    background: 'bg',
    color: 'muted',
    border: 'none',
    borderRightWidth: '1px',
    borderRightStyle: 'solid',
    borderRightColor: 'grid',
    cursor: 'pointer',
  },
  '& button:last-child': { borderRightWidth: '0' },
  '& button[data-selected]': { background: 'color-mix(in oklab, token(colors.amber) 16%, transparent)', color: 'amber' },
  '& button[data-focus-visible]': { outlineWidth: '2px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '-2px' },
  '& button[data-disabled]': { opacity: 0.45, cursor: 'default' },
});

// The one "Changes only | Full context" control (rule 4, D230): remembered (`review.code-context.v1`).
function CodeContextSwitch({ disabled = false }: { disabled?: boolean }) {
  const context = useCodeContext();
  return (
    <ToggleButtonGroup
      className={segment}
      isDisabled={disabled}
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={[context]}
      onSelectionChange={(keys) => {
        const next = [...keys][0];
        if (next === 'changes' || next === 'full') codeContextStore.getState().setContext(next as CodeContext);
      }}
      aria-label="Code context"
      data-testid="review-code-context"
      data-segment
    >
      <ToggleButton id="changes" data-context-choice>Changes only</ToggleButton>
      <ToggleButton id="full" data-context-choice>Full context</ToggleButton>
    </ToggleButtonGroup>
  );
}


// The leaf's short id, as the mockup's label names it (app.js `topline`, "Intent review · L181"): the
// last part of a dated leaf id; any other id is named whole.
export function shortLeafId(leaf: string): string {
  return /^\d{6}-[A-Z]+-(L\d+)$/.exec(leaf)?.[1] ?? leaf;
}

export function ReviewHeader({
  leaf,
  title,
  record,
  chip,
  comparison,
  refresh,
  treeToggle,
  chatToggle,
  crumb,
  contextDisabled = false,
  markerReturn,
  onBack,
}: {
  leaf: string;
  // The task's title from the dashboard's task projection; the leaf id stands in when it has none.
  title?: string;
  // A historical or reconstructed comparison's label (INV-S26EBC8P); none for a live review.
  record?: string | null;
  chip?: React.ReactNode;
  comparison?: React.ReactNode;
  refresh?: React.ReactNode;
  treeToggle?: React.ReactNode;
  chatToggle?: React.ReactNode;
  crumb?: Crumb;
  contextDisabled?: boolean;
  markerReturn?: React.ReactNode;
  onBack: () => void;
}) {
  const short = shortLeafId(leaf);
  return (
    <div className={line} data-testid="review-top-line" data-review-toolbar>
      <button type="button" onClick={onBack} data-testid="review-back">
        ← back
      </button>
      {treeToggle}
      <div className={titleBox}>
        <b className={label} title={short === leaf ? undefined : leaf}>
          Intent review · {short}
        </b>
        <span className={titleText} data-testid="review-title" title={title ?? leaf}>
          {title ?? leaf}
        </span>
        {record ? (
          <span data-testid="review-history" className={recordText} title={record}>
            {record}
          </span>
        ) : null}
        {crumb ? <CrumbLine crumb={crumb} /> : null}
        {markerReturn}
      </div>
      {chip}
      {comparison}
      {crumb ? <CodeContextSwitch disabled={contextDisabled} /> : null}
      {refresh}
      <ReviewKeys />
      {chatToggle}
    </div>
  );
}
