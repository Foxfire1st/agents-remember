// The comparison selector of the review's top line (MIK-R89 v2 rule 10; mockup `comparison-menu`):
// "Comparison #k ▾" lists every recorded number of the leaf, the latest first, from the trees
// answer's latest number. Choosing an older number shows that comparison read-only: its outline,
// summary and cards are read with `comparison=<k>` (W17). The change set's ⊕ sits on the label (W29).
// Drawn as the mockup's menu (D401; app.js `cmpMenu`, styles.css `.menu`): a heading naming the leaf,
// one row per number with its recorded time and its state, and the hint under them. A number's time is
// the trees answer's for the number on screen; no read lists the other numbers' times.
import { useId, useState, type Ref } from 'react';
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components';

import { css } from '../../../../styled-system/css';
import { AddToTray, addToTrayHost } from '../../../tray/AddToTray';
import type { TrayPickInput } from '../../../tray/types';
import type { ReviewDeliveredComparison } from '../../../data/reviewTrees';
import { shortLeafId } from '../ReviewHeader';
import { localTime } from './statusChipModel';

const anchor = css({ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', flex: 'none' });

// The selector's places are held from the first paint (rules 6 and 9, evidence 10): the chip on its
// left keeps its own place only while nothing on its right changes width. The label holds
// "Comparison #99" in its own font, and the ⊕'s slot holds its widest state, "✓ in tray", in the ⊕'s
// font, padding and border (AddToTray.tsx).
const label = css({ display: 'inline-block', minWidth: '14ch', textAlign: 'start' });
const addSlot = css({
  display: 'inline-flex',
  flex: 'none',
  fontSize: '0.68rem',
  minWidth: 'calc(9ch + 0.7rem + 2px)',
});

// The mockup's dim text (styles.css `--dim`), from the theme's muted text toward the page ground.
const dim = 'color-mix(in oklab, token(colors.muted) 82%, token(colors.bg))';

const popup = css({
  zIndex: 110,
  width: 'min(20.625rem, calc(100vw - 1.5rem))',
  padding: '0.35rem 0',
  background: 'bgPanel',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  boxShadow: '0 6px 20px rgba(0, 0, 0, 0.5)',
  fontFamily: 'mono',
  fontSize: '0.75rem',
  '& ul': { listStyle: 'none', margin: 0, padding: 0 },
});

const heading = css({
  padding: '0.45rem 0.8rem 0.2rem',
  fontSize: '0.64rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: dim,
});

const hint = css({ margin: 0, padding: '0.3rem 0.8rem 0.45rem', color: dim, fontSize: '0.68rem', lineHeight: '1.45' });

const item = css({
  display: 'grid',
  gridTemplateColumns: '3rem 1fr auto',
  alignItems: 'baseline',
  gap: '0.4rem',
  width: '100%',
  textAlign: 'left',
  background: 'transparent',
  border: 'none',
  color: 'ink',
  font: 'inherit',
  padding: '0.3rem 0.8rem',
  cursor: 'pointer',
  '& > span:last-child': { color: dim, fontSize: '0.68rem' },
  '&[aria-current="true"]': { color: 'amber' },
  _hover: { background: 'color-mix(in oklab, token(colors.amber) 8%, transparent)', color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
});

export interface ComparisonSelectorProps {
  // The leaf whose comparisons these are; the heading names it by its short id.
  leaf: string;
  // The number on screen; null until the trees answer names one.
  shown: number | null;
  // The comparison the page opens by default: a landed leaf's delivered pair (MIK-R195 rule 2).
  resolved: number | null;
  // The pair the leaf delivered, when its enclosure records one; its row opens the default view.
  delivered: Pick<ReviewDeliveredComparison, 'state' | 'comparison'> | null;
  // When the number on screen was recorded (the trees answer's `recorded_at`); null until it is read.
  recordedAt: string | null;
  // The reader's current date, for the recorded time's form (statusChipModel.ts `localTime`).
  now: Date;
  // The leaf's highest recorded number; the list is 1 … latest.
  latest: number | null;
  onChoose: (number: number | null) => void;
  // The delivered row's own request: open the pair the leaf delivered, which is the page's default
  // and not one of the frame's numbers (MIK-R195 F06).
  onChooseDelivered: () => void;
  pick: TrayPickInput | null;
  // The selector's button, which the status popover hangs from as well (StatusChip.tsx).
  buttonRef?: Ref<HTMLButtonElement>;
}

interface OptionProps {
  number: number;
  shown: number | null;
  latest: number | null;
  delivered: number | undefined;
  recordedAt: string | null;
  now: Date;
  choose: (number: number | null) => void;
}

// One number's row: the number, its recorded time where a read gives it, and its state. The number
// whose four trees are the delivered pair says so rather than "latest": a landed leaf's delivery need
// not be its highest record (MIK-R195 rule 3).
function ComparisonOption({ number, shown, latest, delivered, recordedAt, now, choose }: OptionProps) {
  const state = number === delivered ? 'delivered' : number === latest ? 'latest' : 'read-only';
  return (
    <li>
      <button
        type="button"
        className={item}
        data-testid="review-comparison-option"
        data-number={number}
        aria-current={number === shown ? 'true' : undefined}
        onClick={() => choose(number)}
      >
        <span>#{number}</span>
        <span>{number === shown && recordedAt ? localTime(recordedAt, now) : ''}</span>
        <span>{state}</span>
      </button>
    </li>
  );
}

// The delivered pair's own row: the page's default view, whichever number holds that pair. A leaf
// whose delivery cannot be established gets no row, because no view could be offered (rule 5).
function DeliveredOption({
  shown,
  resolved,
  delivered,
  choose,
}: {
  shown: number | null;
  resolved: number | null;
  delivered: Pick<ReviewDeliveredComparison, 'comparison'>;
  choose: (number: number | null) => void;
}) {
  return (
    <li>
      <button
        type="button"
        className={item}
        data-testid="review-comparison-delivered"
        data-number={delivered.comparison}
        aria-current={shown === resolved ? 'true' : undefined}
        onClick={() => choose(null)}
      >
        <span>delivered</span>
        <span>{delivered.comparison === undefined ? '' : `#${delivered.comparison}`}</span>
        <span>{shown === resolved ? 'shown' : 'default'}</span>
      </button>
    </li>
  );
}

export function ComparisonSelector({ leaf, shown, resolved, delivered, recordedAt, now, latest, onChoose, onChooseDelivered, pick, buttonRef }: ComparisonSelectorProps) {
  const [open, setOpen] = useState(false);
  const headingId = useId();
  const numbers = latest === null ? [] : Array.from({ length: latest }, (_, index) => latest - index);
  const choose = (number: number | null) => {
    setOpen(false);
    if (number !== shown) onChoose(number);
  };
  const offered = delivered !== null && delivered.state !== 'unavailable' ? delivered : null;
  // The delivered row asks for the pair itself, so it closes the menu and leaves the frame through
  // the page's own delivered action rather than choosing a number.
  const chooseDelivered = () => {
    setOpen(false);
    if (offered?.comparison !== shown) onChooseDelivered();
  };
  return (
    <span className={`${anchor} ${addToTrayHost}`} data-testid="review-comparison">
      <DialogTrigger isOpen={open} onOpenChange={setOpen}>
        <Button
          ref={buttonRef}
          data-testid="review-comparison-button"
          aria-label={shown === null ? 'Comparison' : `Comparison #${shown}`}
          isDisabled={latest === null}
        >
          <span className={label}>{shown === null ? 'Comparison …' : `Comparison #${shown}`}</span> ▾
        </Button>
        <Popover className={popup} placement="bottom end">
          <Dialog aria-labelledby={headingId} data-testid="review-comparison-menu" data-kbzone="review">
            <div className={heading} id={headingId} data-testid="review-comparison-heading">
              Comparisons of {shortLeafId(leaf)}
            </div>
            <ul>
              {offered ? (
                <DeliveredOption shown={shown} resolved={resolved} delivered={offered} choose={chooseDelivered} />
              ) : null}
              {numbers.map((number) => (
                <ComparisonOption
                  key={number}
                  number={number}
                  shown={shown}
                  latest={latest}
                  delivered={offered?.comparison}
                  recordedAt={recordedAt}
                  now={now}
                  choose={choose}
                />
              ))}
            </ul>
            <p className={hint}>Choosing an older number shows that comparison read-only.</p>
          </Dialog>
        </Popover>
      </DialogTrigger>
      <span className={addSlot} data-testid="review-change-set-add-slot">
        {pick ? <AddToTray pick={pick} label={`change set of comparison #${shown ?? ''}`} testId="review-change-set-add" /> : null}
      </span>
    </span>
  );
}
