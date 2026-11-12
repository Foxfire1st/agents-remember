// The tray's page-wide part, mounted once by the shell outside `.shell__body` (MIK-R188 rules 3–5):
//
// - it takes chat passages from every Paseo frame: while it listens, the frame channel tells each
//   frame's plug-in to offer "⊕ add" (`picks: true`), so no chat shows a control nobody reads;
// - it tells the frames the copy-on-highlight preference, so the plug-in copies inside the frame as
//   the dashboard does outside it;
// - it offers "⊕ add" on highlights in dashboard panels;
// - it docks the tray at the bottom right while no chat column shows it.
import { useEffect } from 'react';

import { onPick, setTrayPreferences } from '../cockpit/frameTrayChannel';
import { takeChatPick } from './chatPicks';
import { SelectionOffer } from './SelectionOffer';
import { TrayBottomRightDock } from './TrayDock';
import { useCopyOnHighlight } from './trayPreferences';

export function useTrayRuntime(): void {
  const copyOnHighlight = useCopyOnHighlight();
  useEffect(() => setTrayPreferences({ copyOnHighlight }), [copyOnHighlight]);
  useEffect(
    () =>
      onPick((pick) => {
        takeChatPick(pick);
      }),
    [],
  );
}

export function TrayLayer() {
  useTrayRuntime();
  return (
    <>
      <SelectionOffer />
      <TrayBottomRightDock />
    </>
  );
}
