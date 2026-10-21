// The "Tray (n)" button beside the bottom bar's view tabs (MIK-R188 rule 5). It is not one of them:
// it sits outside the view-tab group (ModeBar's ToggleButtonGroup), so the bar keeps exactly its four
// views. It opens the tray to its full view also when the tray is empty or minimised (15f92a8f), and
// it keeps the page's selection and focus while it does (rule 4).
//
// The closed pill "⊕ n in the AR tray · open" shows at the dock's edge while the tray is closed with
// picks.
import { css, cx } from '../../styled-system/css';
import { viewTabButton } from '../grammar/viewTabLook';
import { isExpanded, trayStore, useTray } from './trayStore';
import * as look from './trayStyles';

const trayTab = css({
  color: 'amber',
  borderColor: 'color-mix(in oklab, var(--colors-amber) 45%, transparent)',
  whiteSpace: 'nowrap',
  '&[aria-expanded="true"]': { borderColor: 'amber' },
});

const keepSelection = (event: React.MouseEvent) => event.preventDefault();

export function TrayButton() {
  const count = useTray((state) => state.picks.length);
  const open = useTray(isExpanded);
  return (
    <button
      type="button"
      className={cx(viewTabButton, trayTab)}
      data-testid="tray-button"
      aria-expanded={open}
      aria-label={`${open ? 'Close' : 'Open'} the AR tray, ${count} ${count === 1 ? 'pick' : 'picks'}`}
      onMouseDown={keepSelection}
      onClick={() => trayStore.getState().setOpen(!open)}
    >
      Tray ({count})
    </button>
  );
}

// The button's place in the bottom bar: the rest of the bar's line, right of the view tabs. It
// carries the bar's top rule, so the line runs on past the tabs.
const barSlot = css({
  flex: '1',
  minWidth: '0',
  display: 'flex',
  justifyContent: 'flex-end',
  alignItems: 'flex-start',
  borderTopWidth: '1px',
  borderTopStyle: 'solid',
  borderTopColor: 'grid',
  paddingTop: '0.5rem',
  // At 40rem and narrower the view tabs take the whole line and wrap, and the button sits under them.
  '@media (max-width: 40rem)': { borderTopWidth: '0', paddingTop: '0', justifyContent: 'flex-start' },
});

// The bottom bar's line: the view tabs, then the Tray button. At 40rem and narrower the two stack, so
// the tabs keep the full width they wrap in and the page never scrolls sideways.
export const trayBarRow = css({
  display: 'flex',
  alignItems: 'stretch',
  minWidth: '0',
  '@media (max-width: 40rem)': { flexDirection: 'column', gap: '0.3rem' },
});

export function TrayBarSlot() {
  return (
    <div className={barSlot}>
      <TrayButton />
    </div>
  );
}

export function TrayPill() {
  const count = useTray((state) => state.picks.length);
  return (
    <button
      type="button"
      className={look.pill}
      data-testid="tray-pill"
      onMouseDown={keepSelection}
      onClick={() => trayStore.getState().setOpen(true)}
    >
      ⊕ {count} in the AR tray · open
    </button>
  );
}
