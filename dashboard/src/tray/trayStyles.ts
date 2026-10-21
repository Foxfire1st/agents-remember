// The tray's look: one amber-topped panel, one row per pick, one line when it is minimised, a quiet
// pill when it is closed.
import { css } from '../../styled-system/css';

export const panel = css({
  display: 'flex',
  flexDirection: 'column',
  gap: '0.5rem',
  padding: '0.55rem 0.65rem 0.6rem',
  background: 'bgPanel',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderTopWidth: '2px',
  borderTopColor: 'amber',
  borderRadius: '6px',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.72rem',
  boxShadow: '0 6px 20px rgba(0, 0, 0, 0.45)',
  // In its dock's bounded box the panel may shrink; its pick list gives up the height.
  minHeight: '0',
});

// The minimised tray: the panel's frame around one line.
export const line = css({
  display: 'flex',
  flexDirection: 'column',
  gap: '0.3rem',
  padding: '0.3rem 0.6rem',
  background: 'bgPanel',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderTopWidth: '2px',
  borderTopColor: 'amber',
  borderRadius: '6px',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.72rem',
  boxShadow: '0 6px 20px rgba(0, 0, 0, 0.45)',
});
export const lineOpen = css({
  font: 'inherit',
  background: 'transparent',
  border: '0',
  padding: '0',
  color: 'amber',
  fontWeight: '600',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  _hover: { color: 'ink' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '1px' },
});

export const head = css({ display: 'flex', alignItems: 'center', gap: '0.5rem', position: 'relative' });
export const headLabel = css({
  whiteSpace: 'nowrap',
  color: 'amber',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  fontSize: '0.66rem',
  fontWeight: '700',
});
export const dim = css({ color: 'muted', fontSize: '0.68rem' });
export const spacer = css({ flex: '1' });

export const quiet = css({
  font: 'inherit',
  fontSize: '0.72rem',
  background: 'transparent',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  padding: '0.15rem 0.45rem',
  cursor: 'pointer',
  _hover: { borderColor: 'amber', color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '1px' },
  _disabled: { opacity: '0.5', cursor: 'default' },
});

// The head's "to:" control: one line that gives way inside the head.
export const targetAnchor = css({ display: 'flex', alignItems: 'center', position: 'relative', minWidth: '0' });
export const targetButton = css({
  font: 'inherit',
  fontSize: '0.72rem',
  background: 'transparent',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  padding: '0.15rem 0.45rem',
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'baseline',
  minWidth: '0',
  maxWidth: '100%',
  overflow: 'hidden',
  whiteSpace: 'nowrap',
  _hover: { borderColor: 'amber', color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '1px' },
});
export const targetName = css({ minWidth: '0', overflow: 'hidden', textOverflow: 'ellipsis' });
export const keepSpaces = css({ flexShrink: '0', whiteSpace: 'pre' });

export const close = css({
  font: 'inherit',
  background: 'transparent',
  border: '0',
  color: 'muted',
  fontSize: '0.9rem',
  padding: '0 0.2rem',
  cursor: 'pointer',
  _hover: { color: 'alarm' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
});

export const minimise = css({
  font: 'inherit',
  background: 'transparent',
  border: '0',
  color: 'muted',
  fontSize: '0.9rem',
  padding: '0 0.2rem',
  cursor: 'pointer',
  _hover: { color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
});

export const notice = css({ margin: '0', color: 'amber', fontSize: '0.7rem', lineHeight: '1.45' });
export const refusalLine = css({ margin: '0', color: 'alarm', fontSize: '0.7rem', lineHeight: '1.45' });

export const chips = css({
  listStyle: 'none',
  margin: '0',
  padding: '0',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.35rem',
  maxHeight: '38vh',
  // The one part of the full view that shrinks in its bounded box, and scrolls (15f92a8f).
  minHeight: '0',
  flexShrink: '1',
  overflowY: 'auto',
  overscrollBehavior: 'contain',
});

export const chip = css({
  display: 'grid',
  gridTemplateColumns: 'auto minmax(0, 1fr) auto',
  gap: '0.5rem',
  alignItems: 'start',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '4px',
  padding: '0.35rem 0.45rem',
  background: 'bg',
  // The quick intents show while the pointer is over the chip or its row, and while the keyboard's
  // focus is in the chip; at rest the row is not drawn and catches no pointer. Two rules, so the
  // hover holds in a browser without :has().
  '& [data-quick]': { opacity: '0', pointerEvents: 'none' },
  '&:hover [data-quick]': { opacity: '1', pointerEvents: 'auto' },
  '&:has(:focus-visible) [data-quick]': { opacity: '1', pointerEvents: 'auto' },
});
export const chipIcon = css({ color: 'cyan', paddingTop: '0.05rem' });
export const thumb = css({
  width: '64px',
  height: '36px',
  objectFit: 'cover',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
});
export const chipBody = css({ minWidth: '0' });
export const chipLine1 = css({ display: 'flex', gap: '0.45rem', minWidth: '0', whiteSpace: 'nowrap' });
export const chipId = css({ color: 'cyan', fontWeight: '600' });
export const chipTitle = css({ color: 'ink', overflow: 'hidden', textOverflow: 'ellipsis' });
export const chipLine2 = css({
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0.2rem 0.6rem',
  alignItems: 'baseline',
  marginTop: '0.1rem',
  color: 'muted',
  fontSize: '0.68rem',
  minWidth: '0',
});
export const textButton = css({
  font: 'inherit',
  fontSize: '0.68rem',
  background: 'transparent',
  border: '0',
  padding: '0',
  color: 'muted',
  cursor: 'pointer',
  textAlign: 'left',
  _hover: { color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
});
export const noteText = css({ color: 'gold', whiteSpace: 'normal' });
export const noteInput = css({
  font: 'inherit',
  width: '100%',
  background: 'bgPanel',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  borderRadius: '2px',
  padding: '0.15rem 0.35rem',
  fontSize: '0.7rem',
});
// The chip's quick intents: one row placed in the window just outside the chip (TrayChip.tsx,
// quickPlacement), drawn over the neighbouring chip or the message field with the tray's background
// and never over the chip itself. Out of the chip's flow, showing it moves and resizes no chip (D382).
export const quick = css({
  position: 'fixed',
  zIndex: '30',
  display: 'flex',
  gap: '0.3rem',
  padding: '0.1rem 0.25rem',
  whiteSpace: 'nowrap',
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '4px',
  boxShadow: '0 4px 12px rgba(0, 0, 0, 0.45)',
  transition: 'opacity 120ms',
});
export const quickChip = css({
  font: 'inherit',
  fontSize: '0.64rem',
  background: 'transparent',
  color: 'muted',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '8px',
  padding: '0 0.4rem',
  cursor: 'pointer',
  _hover: { color: 'amber', borderColor: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
});

export const empty = css({
  borderWidth: '1px',
  borderStyle: 'dashed',
  borderColor: 'grid',
  borderRadius: '4px',
  padding: '0.9rem 0.8rem',
  color: 'muted',
  fontSize: '0.72rem',
  lineHeight: '1.55',
  textAlign: 'center',
  '& b': { color: 'ink', fontWeight: '500' },
});

export const wish = css({
  font: 'inherit',
  fontSize: '0.8rem',
  lineHeight: '1.55',
  width: '100%',
  resize: 'vertical',
  minHeight: '3.2rem',
  background: 'bg',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '4px',
  padding: '0.5rem 0.6rem',
  _focus: { borderColor: 'cyan', outline: 'none' },
});

// The one-message line, on its own line above the Send row (d9a1ec8f).
export const preview = css({ margin: '0', color: 'muted', fontSize: '0.68rem', lineHeight: '1.45' });
export const sendRow = css({ display: 'flex', justifyContent: 'flex-end', minWidth: '0' });
export const send = css({
  maxWidth: '100%',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  font: 'inherit',
  fontSize: '0.75rem',
  fontWeight: '700',
  background: 'amber',
  color: 'bg',
  border: '0',
  borderRadius: '2px',
  padding: '0.35rem 0.7rem',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  _disabled: { opacity: '0.45', cursor: 'default' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'ink', outlineOffset: '1px' },
});

export const outcomeList = css({
  listStyle: 'none',
  margin: '0',
  padding: '0',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.2rem',
  fontSize: '0.68rem',
  lineHeight: '1.45',
  '& [data-state="delivered"]': { color: 'mint' },
  '& [data-state="held"]': { color: 'amber' },
  '& [data-state="refused"]': { color: 'alarm' },
});

export const sentSummary = css({ cursor: 'pointer', _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' } });
export const sentIds = css({
  listStyle: 'none',
  margin: '0',
  padding: '0.1rem 0 0 0.9rem',
  display: 'flex',
  flexWrap: 'wrap',
  gap: '0 0.6rem',
  fontFamily: 'mono',
  color: 'ink',
});
export const sentFile = css({ paddingLeft: '0.9rem', color: 'muted', overflowWrap: 'anywhere' });

export const foot = css({ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.3rem 0.8rem', color: 'muted', fontSize: '0.68rem' });
export const bounds = css({ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '0.25rem', paddingTop: '0.3rem' });
export const boundRow = css({ display: 'flex', alignItems: 'center', gap: '0.45rem' });
export const boundLabel = css({ display: 'flex', alignItems: 'center', gap: '0.45rem' });
export const boundName = css({ minWidth: '7.5rem' });
export const boundInput = css({
  font: 'inherit',
  width: '4.5rem',
  background: 'bgPanel',
  color: 'ink',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '2px',
  padding: '0.1rem 0.3rem',
  fontSize: '0.68rem',
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber' },
  '&[aria-invalid="true"]': { borderColor: 'alarm' },
});

// Placed in the window beside the "to:" button (TrayTargets.tsx, menuPlacement): no column or dock
// that clips its content clips the popup.
export const menu = css({
  position: 'fixed',
  zIndex: '40',
  // Wide enough for "Reviewer · 260928-MIK-L89 · 0f89acd5" and its state on one line.
  width: 'min(400px, calc(100vw - 2rem))',
  maxHeight: '50vh',
  overflowY: 'auto',
  padding: '0.35rem 0',
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  boxShadow: '0 6px 20px rgba(0, 0, 0, 0.5)',
  fontSize: '0.75rem',
});
export const menuGroup = css({
  padding: '0.45rem 0.8rem 0.2rem',
  fontSize: '0.64rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: 'muted',
});
export const menuRow = css({
  position: 'relative',
  display: 'grid',
  gridTemplateColumns: '1rem minmax(0, 1fr) auto',
  alignItems: 'baseline',
  gap: '0.4rem',
  padding: '0.3rem 0.8rem',
  cursor: 'pointer',
  _hover: { background: 'grid', color: 'amber' },
  '&:has(input:checked)': { color: 'amber' },
  '&:has(input:focus-visible)': { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '-1px' },
});
export const menuMark = css({ color: 'amber' });
export const menuIdentity = css({ display: 'flex', minWidth: '0', whiteSpace: 'nowrap' });
export const menuName = css({ minWidth: '0', overflow: 'hidden', textOverflow: 'ellipsis' });
export const menuTag = css({ flexShrink: '0', whiteSpace: 'pre' });
export const menuWhy = css({ color: 'muted', fontSize: '0.68rem', whiteSpace: 'nowrap' });
// Drawn for nobody, read by the keyboard and assistive technology.
export const unseen = css({
  position: 'absolute',
  width: '1px',
  height: '1px',
  margin: '-1px',
  padding: '0',
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
  border: '0',
});
export const menuHint = css({ padding: '0.3rem 0.8rem 0.45rem', color: 'muted', fontSize: '0.68rem', lineHeight: '1.45' });

export const pill = css({
  font: 'inherit',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.7rem',
  background: 'bgPanel',
  color: 'amber',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  borderRadius: '12px',
  padding: '0.2rem 0.6rem',
  cursor: 'pointer',
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'ink', outlineOffset: '1px' },
});

export const offer = css({
  position: 'fixed',
  zIndex: '60',
  font: 'inherit',
  fontFamily: 'var(--font-mono)',
  fontSize: '0.7rem',
  background: 'bgPanel',
  color: 'cyan',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'cyan',
  borderRadius: '2px',
  padding: '0.1rem 0.45rem',
  cursor: 'pointer',
  boxShadow: '0 3px 10px rgba(0, 0, 0, 0.45)',
  _hover: { color: 'amber', borderColor: 'amber' },
});
