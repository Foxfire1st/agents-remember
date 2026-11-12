// Highlighting text in a dashboard panel offers a pick; only a click takes it (MIK-R188 rule 3).
// Copy keeps working (rule 4).
//
// - The selection is read synchronously in the document's `mouseup`, the moment the drag ends.
// - With copy on highlight on, the same handler copies with `document.execCommand('copy')`: the end
//   of a drag is a user activation, so the copy needs no prompt.
// - Nothing here moves focus or clears the selection; no code path calls `removeAllRanges()`.
// - Editable fields, the tray, the offer itself and the chat frame are left alone. A highlight in
//   the chat frame never reaches this document: the plug-in offers its own "⊕ add" there.
import { useCallback, useEffect, useState } from 'react';

import { passageTitle } from './trayModel';
import { trayPreferences } from './trayPreferences';
import { trayStore } from './trayStore';
import { shownStore } from './shownChat';
import type { TrayPickInput } from './types';

export interface SelectionSnapshot {
  text: string;
  // Where the offer goes: the selection's last line, in viewport pixels.
  top: number;
  left: number;
  panel: string;
}

export const OFFER_ATTRIBUTE = 'data-selection-offer';
const LEFT_ALONE = `input, textarea, select, [contenteditable=""], [contenteditable="true"], [data-tray-root], [${OFFER_ATTRIBUTE}], iframe`;

function elementOf(node: Node | null | undefined): Element | null {
  if (!node) return null;
  return node instanceof Element ? node : node.parentElement;
}

const leftAlone = (node: Node | null | undefined): boolean => elementOf(node)?.closest(LEFT_ALONE) != null;

// The panel a passage came from: the nearest `data-tray-panel` name, else the page's name.
function panelOf(node: Node | null | undefined): string {
  const named = elementOf(node)?.closest('[data-tray-panel]')?.getAttribute('data-tray-panel');
  return named || shownStore.getState().page || 'the dashboard';
}

export function readPanelSelection(selection: Selection | null): SelectionSnapshot | null {
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const text = selection.toString().trim();
  if (!text || leftAlone(selection.anchorNode) || leftAlone(selection.focusNode)) return null;
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  return { text, top: rect.bottom + 4, left: rect.right + 4, panel: panelOf(selection.anchorNode) };
}

export function passagePick(snapshot: SelectionSnapshot): TrayPickInput {
  return {
    kind: 'text',
    title: passageTitle(snapshot.text),
    text: snapshot.text,
    provenance: { source: 'panel', panel: snapshot.panel },
  };
}

function copyHighlight(): void {
  try {
    document.execCommand('copy');
  } catch {
    // A browser that refuses the copy leaves the clipboard as it was; the offer still shows.
  }
}

const isOffer = (target: EventTarget | null): boolean =>
  target instanceof Element && target.closest(`[${OFFER_ATTRIBUTE}]`) !== null;
const inTray = (target: EventTarget | null): boolean =>
  target instanceof Element && target.closest('[data-tray-root]') !== null;

export function useSelectionOffer(): { offer: SelectionSnapshot | null; take: () => void; dismiss: () => void } {
  const [offer, setOffer] = useState<SelectionSnapshot | null>(null);
  useEffect(() => {
    const onMouseUp = (event: MouseEvent) => {
      if (isOffer(event.target) || inTray(event.target)) return;
      const snapshot = readPanelSelection(document.getSelection());
      if (snapshot && trayPreferences.getState().copyOnHighlight) copyHighlight();
      setOffer(snapshot);
    };
    // A press that keeps the selection (the offer, the Tray button, a ⊕) keeps the offer too.
    const onMouseDown = (event: MouseEvent) => {
      if (!isOffer(event.target) && !event.defaultPrevented) setOffer(null);
    };
    const onSelectionChange = () => {
      if (document.getSelection()?.isCollapsed !== false) setOffer(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOffer(null);
    };
    const onScroll = () => setOffer(null);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('scroll', onScroll, true);
    document.addEventListener('selectionchange', onSelectionChange);
    return () => {
      document.removeEventListener('selectionchange', onSelectionChange);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('scroll', onScroll, true);
    };
  }, []);
  const take = useCallback(() => {
    if (!offer) return;
    trayStore.getState().take(passagePick(offer));
    setOffer(null);
  }, [offer]);
  const dismiss = useCallback(() => setOffer(null), []);
  return { offer, take, dismiss };
}
