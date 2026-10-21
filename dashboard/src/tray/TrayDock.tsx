// Where the tray lives (MIK-R188 rule 5).
//
// - In the chat column the tray is AR's own element, outside the frame, drawn over it directly above
//   the top edge of Paseo's message box. The plug-in reports that edge through the frame channel
//   (cockpit/frameTrayChannel.ts); the dock asks the frame to keep the tray's height free above the
//   box (`ar.tray-space`), so the tray never covers the box. When the box cannot be read, the tray
//   sits below the frame (the frame gives up its height) with one named notice.
// - On pages without a chat column (Knowledge, File Viewer, and wherever the column is hidden) the
//   same panel docks at the bottom right, above the bottom bar.
// - Shown, the tray is minimised to one line, "⊕ Attached · n", or expanded to its full view
//   (15f92a8f). A pick shows it minimised unless it is expanded; a click on the line expands it. The
//   full view is bounded by the room above the message box, and its pick list scrolls in it, so the
//   message field and Send stay in view.
// - Closed with picks, a pill "⊕ n in the AR tray · open" shows at the same edge.
//
// One tray at a time: the first visible column dock shows it; the bottom-right dock shows it only
// while no column dock is visible. The dock never moves, re-parents or reloads the frame (MIK-R122
// rule 2): it is a sibling of the frame's wrapper and only reads rectangles.
import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

import { css } from '../../styled-system/css';
import {
  composerUnavailableText,
  setTraySpace,
  useComposerEdge,
  type ComposerEdge,
  type FramePage,
} from '../cockpit/frameTrayChannel';
import { TrayPill } from './TrayButton';
import { TrayLine, TrayPanel } from './TrayPanel';
import { useTray } from './trayStore';
import * as look from './trayStyles';

// ---- which dock shows the tray ----

export const dockStore = createStore<{ columns: string[] }>(() => ({ columns: [] }));

function useColumnRegistration(id: string, visible: boolean): boolean {
  useEffect(() => {
    if (!visible) return;
    dockStore.setState((state) => ({ columns: [...state.columns, id] }));
    return () => dockStore.setState((state) => ({ columns: state.columns.filter((column) => column !== id) }));
  }, [id, visible]);
  return useStore(dockStore, (state) => state.columns[0] === id);
}

// ---- geometry ----

export const DOCK_GAP = 6;

export interface DockPlacement {
  left: number;
  width: number;
  bottom: number;
  // The room over the frame above the message box, a gap at each end: the full view fits in it.
  maxHeight: number;
}

// The message box's top edge in viewport pixels. The box sits at the frame's bottom, so its distance
// from the frame's bottom edge holds while the frame's height changes before the plug-in reports again.
function composerEdgeTop(frame: DOMRect, composer: Extract<ComposerEdge, { kind: 'measured' }>): number {
  if (composer.frameHeight > 0) return frame.bottom - (composer.frameHeight - composer.top);
  return frame.top + composer.top;
}

// Where the docked tray goes, relative to the dock's own anchor (a zero-height box at the frame's
// bottom edge): over the frame, its bottom edge `DOCK_GAP` pixels above the message box's top edge.
// Null: the box is not measured, or the frame is not drawn (a placeholder shows instead).
export function dockPlacement(anchor: DOMRect, frame: DOMRect | null, composer: ComposerEdge | undefined): DockPlacement | null {
  if (composer?.kind !== 'measured' || !frame || frame.height === 0 || frame.width === 0) return null;
  const edge = composerEdgeTop(frame, composer);
  return {
    left: frame.left - anchor.left,
    width: frame.width,
    bottom: anchor.top - edge + DOCK_GAP,
    maxHeight: Math.max(0, edge - frame.top - 2 * DOCK_GAP),
  };
}

function frameOf(anchor: HTMLElement | null, host: RefObject<HTMLElement | null> | undefined): HTMLIFrameElement | null {
  const scope = host?.current ?? anchor?.parentElement ?? null;
  return scope?.querySelector('iframe') ?? null;
}

function usePlacement(
  anchor: RefObject<HTMLDivElement | null>,
  host: RefObject<HTMLElement | null> | undefined,
  composer: ComposerEdge | undefined,
  shows: boolean,
) {
  const [placement, setPlacement] = useState<DockPlacement | null>(null);
  useLayoutEffect(() => {
    if (!shows) return;
    const measure = () => {
      const frame = frameOf(anchor.current, host);
      const anchorRect = anchor.current?.getBoundingClientRect();
      setPlacement(anchorRect ? dockPlacement(anchorRect, frame?.getBoundingClientRect() ?? null, composer) : null);
    };
    measure();
    const frame = frameOf(anchor.current, host);
    const observer = new ResizeObserver(measure);
    if (frame) observer.observe(frame);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [anchor, host, composer, shows]);
  return placement;
}

// Keeps the tray's height free above the message box while the tray floats over the frame. `drawn`
// names what the dock draws (the line, the full view, the pill), so a change of it reports again.
function useReservedSpace(box: RefObject<HTMLDivElement | null>, page: FramePage, floating: boolean, shows: boolean, drawn: string) {
  useLayoutEffect(() => {
    if (!floating || !shows || !box.current) {
      setTraySpace(page, 0);
      return;
    }
    const element = box.current;
    const report = () => setTraySpace(page, Math.ceil(element.getBoundingClientRect().height) + DOCK_GAP);
    report();
    const observer = new ResizeObserver(report);
    observer.observe(element);
    return () => {
      observer.disconnect();
      setTraySpace(page, 0);
    };
  }, [box, page, floating, shows, drawn]);
}

// ---- the chat column's dock ----

// Over the reading column's sticky head (z-index 10), as the chat column lies over the reading
// column below 1440 px (reviewPage.tsx, z-index 20): the target popup may cross the rail's edge.
const anchorBox = css({ position: 'relative', flexShrink: '0', height: '0', zIndex: '20' });
const floatingBox = css({ position: 'absolute', display: 'flex', flexDirection: 'column', alignItems: 'stretch', padding: '0 0.7rem' });
const belowBox = css({ flexShrink: '0', display: 'flex', flexDirection: 'column', gap: '0.3rem', padding: '0.4rem 0.7rem' });
const pillRow = css({ display: 'flex', justifyContent: 'flex-end' });

function useDrawn(): 'full' | 'line' | 'pill' {
  return useTray((state) => (!state.open ? 'pill' : state.minimised ? 'line' : 'full'));
}

function DockContent({ drawn }: { drawn: 'full' | 'line' | 'pill' }) {
  if (drawn === 'full') return <TrayPanel />;
  if (drawn === 'line') return <TrayLine />;
  return (
    <div className={pillRow}>
      <TrayPill />
    </div>
  );
}

export interface TrayColumnDockProps {
  // Whether the column is shown (not hidden by its toggle, its rail view or a closed column).
  visible: boolean;
  // The frame's page on the frame channel: "document" for the rail's chat column (the review
  // page's column included), "chats" for the Chats view.
  page?: FramePage;
  // The element holding the chat's iframe; by default the dock's own parent.
  frameHost?: RefObject<HTMLElement | null>;
}

export function TrayColumnDock({ visible, page = 'document', frameHost }: TrayColumnDockProps) {
  const id = useId();
  const mine = useColumnRegistration(id, visible);
  const open = useTray((state) => state.open);
  const count = useTray((state) => state.picks.length);
  const drawn = useDrawn();
  const anchor = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const composer = useComposerEdge(page);
  const shows = visible && mine && (open || count > 0);
  const placement = usePlacement(anchor, frameHost, composer, shows);
  useReservedSpace(box, page, placement !== null, shows, drawn);
  if (!shows) return null;
  if (placement)
    return (
      <div ref={anchor} className={anchorBox} data-testid="tray-column-dock" data-dock="above-composer">
        <div
          ref={box}
          className={floatingBox}
          style={{ left: placement.left, width: placement.width, bottom: placement.bottom, maxHeight: placement.maxHeight }}
        >
          <DockContent drawn={drawn} />
        </div>
      </div>
    );
  return (
    <div ref={anchor} className={belowBox} data-testid="tray-column-dock" data-dock="below-frame">
      {composer?.kind === 'unavailable' ? (
        <p role="status" className={look.notice} data-testid="tray-dock-notice">
          {composerUnavailableText(composer.reason)}
        </p>
      ) : null}
      <DockContent drawn={drawn} />
    </div>
  );
}

// ---- pages without a chat column ----

const corner = css({
  position: 'fixed',
  right: '0.9rem',
  bottom: 'var(--tray-bottom-offset, 3.2rem)',
  zIndex: '50',
  width: 'min(440px, calc(100vw - 1.8rem))',
  // The full view fits between the bottom bar and the window's top; its list scrolls in it.
  maxHeight: 'calc(100vh - var(--tray-bottom-offset, 3.2rem) - 0.9rem)',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'stretch',
});

export function TrayBottomRightDock() {
  const columnShown = useStore(dockStore, (state) => state.columns.length > 0);
  const open = useTray((state) => state.open);
  const count = useTray((state) => state.picks.length);
  const drawn = useDrawn();
  if (columnShown || (!open && count === 0)) return null;
  return (
    <div className={corner} data-testid="tray-corner-dock">
      <DockContent drawn={drawn} />
    </div>
  );
}
