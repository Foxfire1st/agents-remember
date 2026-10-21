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
  // When the number on screen was recorded (the trees answer's `recorded_at`); null until it is read.
  recordedAt: string | null;
  // The reader's current date, for the recorded time's form (statusChipModel.ts `localTime`).
  now: Date;
  // The leaf's latest recorded number; the list is 1 … latest.
  latest: number | null;
  onChoose: (number: number) => void;
  pick: TrayPickInput | null;
  // The selector's button, which the status popover hangs from as well (StatusChip.tsx).
  buttonRef?: Ref<HTMLButtonElement>;
}

interface OptionProps {
  number: number;
  shown: number | null;
  latest: number | null;
  recordedAt: string | null;
  now: Date;
  choose: (number: number) => void;
}

// One number's row: the number, its recorded time where a read gives it, and its state.
function ComparisonOption({ number, shown, latest, recordedAt, now, choose }: OptionProps) {
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
        <span>{number === latest ? 'latest' : 'read-only'}</span>
      </button>
    </li>
  );
}

export function ComparisonSelector({ leaf, shown, recordedAt, now, latest, onChoose, pick, buttonRef }: ComparisonSelectorProps) {
  const [open, setOpen] = useState(false);
  const headingId = useId();
  const numbers = latest === null ? [] : Array.from({ length: latest }, (_, index) => latest - index);
  const choose = (number: number) => {
    setOpen(false);
    if (number !== shown) onChoose(number);
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
              {numbers.map((number) => (
                <ComparisonOption key={number} number={number} shown={shown} latest={latest} recordedAt={recordedAt} now={now} choose={choose} />
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
