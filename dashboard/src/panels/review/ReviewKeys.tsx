import { useEffect, useState } from 'react';
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components';

import { css } from '../../../styled-system/css';
import { COMMANDS } from '../../data/commands';
import { useEffectiveKeymap } from '../../data/keymap/preferences';
import { routeKey } from '../../data/keymap/zones';
import { useReviewShown } from './reviewShown';

const popup = css({
  zIndex: 110,
  maxWidth: 'calc(100vw - 1.5rem)',
  maxHeight: '70vh',
  overflowY: 'auto',
  padding: '1rem',
  background: 'bgPanel',
  color: 'ink',
  border: '1px solid token(colors.grid)',
  borderRadius: '3px',
  fontFamily: 'mono',
  fontSize: '0.8rem',
});

// The page's two sidebar keys (MIK-R89 v2 rule 8): fixed, outside the rebindable keymap.
const PAGE_KEYS = [
  ['Tree sidebar', '['],
  ['Chat sidebar', ']'],
] as const;

function isHelpKey(event: KeyboardEvent): boolean {
  return event.key === '?' && !event.ctrlKey && !event.altKey && !event.metaKey && !event.isComposing;
}

export function ReviewKeys() {
  const [open, setOpen] = useState(false);
  const keymap = useEffectiveKeymap();
  const shown = useReviewShown();
  useEffect(() => {
    if (!shown) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (!isHelpKey(event)) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const zone = target?.closest('[data-kbzone]')?.getAttribute('data-kbzone');
      if (zone && zone !== 'review') return;
      if (routeKey(event, target) === 'passthrough' || target?.closest('input, [contenteditable]:not([contenteditable="false"])')) return;
      event.preventDefault();
      setOpen(true);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [shown]);
  return (
    <DialogTrigger isOpen={open} onOpenChange={setOpen}>
      <Button aria-label="Keys">Keys</Button>
      <Popover className={popup} placement="bottom end">
        <Dialog aria-label="Reviewer keys" data-kbzone="review">
          <strong>Reviewer keys</strong>
          <dl>
            {keymap.bindings.filter((binding) => binding.zones.includes('review')).map((binding) => (
              <div key={binding.commandId} style={{ display: 'flex', gap: '1rem', justifyContent: 'space-between' }}>
                <dt>{COMMANDS.find((command) => command.id === binding.commandId)?.label}</dt>
                <dd style={{ margin: 0 }}><kbd>{binding.label}</kbd></dd>
              </div>
            ))}
            {PAGE_KEYS.map(([label, key]) => (
              <div key={key} style={{ display: 'flex', gap: '1rem', justifyContent: 'space-between' }}>
                <dt>{label}</dt>
                <dd style={{ margin: 0 }}><kbd>{key}</kbd></dd>
              </div>
            ))}
          </dl>
          <p>Printable shortcuts do not run in text fields.</p>
          <Button onPress={() => setOpen(false)}>Close</Button>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}
