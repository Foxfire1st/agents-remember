import { css } from '../../styled-system/css';
import { trayStore, useTrayHas } from './trayStore';
import type { TrayPickInput } from './types';

// The ⊕ on an object (MIK-R188 rule 1, D366). Quiet until its object is hovered or focused, named for
// assistive technology, and "✓ in tray" once its object is in the tray. One click adds one reference;
// a second click on the same object adds nothing.
//
// The quietness comes from the parent: an element that holds this control and carries the class
// `addToTrayHost` shows it on hover and focus-within. Without that parent the control stays visible.
// Hosts nest (a subject holds its blocks, a file its hunks): a nested host that is neither hovered
// nor focused keeps its own ⊕ quiet inside a host that is. Its rule is the more specific one.
export const addToTrayHost = css({
  '& [data-add-to-tray="idle"]': { opacity: 0 },
  '&:hover [data-add-to-tray="idle"], &:focus-within [data-add-to-tray="idle"]': { opacity: 1 },
  '&:not(:hover):not(:focus-within) [data-add-to-tray="idle"]': { opacity: 0 },
});

const look = css({
  font: 'inherit',
  fontSize: '0.68rem',
  lineHeight: '1',
  paddingInline: '0.35rem',
  paddingBlock: '0.2rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  background: 'transparent',
  color: 'muted',
  cursor: 'pointer',
  transition: 'opacity 120ms',
  _hover: { color: 'cyan', borderColor: 'cyan' },
  _focusVisible: { opacity: 1, outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '1px' },
  '&[data-add-to-tray="done"]': { color: 'mint', borderColor: 'mint', cursor: 'default' },
});

export interface AddToTrayProps {
  pick: TrayPickInput;
  // What the object is, for the accessible name: "invariant INV-1ZKP4R", "block RLZ-78JFRR".
  label: string;
  testId?: string;
}

export function AddToTray({ pick, label, testId }: AddToTrayProps) {
  const inTray = useTrayHas(pick);
  return (
    <button
      type="button"
      className={look}
      data-add-to-tray={inTray ? 'done' : 'idle'}
      data-testid={testId}
      aria-label={inTray ? `${label} is in the AR tray` : `Add ${label} to the AR tray`}
      aria-disabled={inTray || undefined}
      onMouseDown={(event) => event.preventDefault()}
      onClick={(event) => {
        event.stopPropagation();
        if (!inTray) trayStore.getState().take(pick);
      }}
    >
      {inTray ? '✓ in tray' : '⊕'}
    </button>
  );
}
