// The tray's one target choice (MIK-R188 rule 7): a paged multi-select over the host-wide list of
// live AR seats. One Send delivers the same message to every chosen chat. Choosing a target never
// moves or switches the chat column: the host's list stays the only chat navigation, and the tray
// only names where its message goes.
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

import { useFollowAnchor } from './followAnchor';
import { useShownChat } from './shownChat';
import { agentTag, loadSeats, seatRoleTask, useChatNames, useSeatList, type ChatName } from './traySeats';
import { trayStore, useTray } from './trayStore';
import * as look from './trayStyles';

type Names = (agentId: string) => ChatName;

export function targetsLabel(targets: readonly string[], name: Names): string {
  if (targets.length === 0) return 'choose a chat';
  if (targets.length === 1) return name(targets[0]).label;
  return `${targets.length} chats`;
}

// The targets in two parts, as Send's button and the head show them: one chat's role and task (or
// its id) and its agent tag; several chats by their number.
export function targetParts(targets: readonly string[], name: Names): { short: string; tag: string | null } {
  if (targets.length === 1) {
    const { short, tag } = name(targets[0]);
    return { short, tag };
  }
  return { short: targetsLabel(targets, name), tag: null };
}

// Why a target is named by its id, for a control's title; undefined while every target has a name.
export function targetsTitle(targets: readonly string[], name: Names): string | undefined {
  const notes = targets.map((agentId) => name(agentId).unnamed).filter((note): note is string => note !== null);
  return notes.length ? notes.join(' ') : undefined;
}

function useDismiss(open: boolean, root: React.RefObject<HTMLElement | null>, close: () => void) {
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, root, close]);
}

// The popup's gap to the "to:" button and its least distance to the window's edges, in pixels.
const MENU_GAP = 5;
const MENU_MARGIN = 8;

export interface MenuPlacement {
  left: number;
  top: number | null;
  bottom: number | null;
  maxHeight: number;
}

interface Edges {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

// Where the target popup goes in the window: its right edge on the button's, moved inside the
// window; above the button where its rows fit there or where there is more room than below, else
// below it; at most half the window high, its rows scrolling in it.
export function menuPlacement(button: Edges, menu: { width: number; height: number }, view: { width: number; height: number }): MenuPlacement {
  const left = Math.max(MENU_MARGIN, Math.min(button.right - menu.width, view.width - menu.width - MENU_MARGIN));
  const above = button.top - MENU_GAP - MENU_MARGIN;
  const below = view.height - button.bottom - MENU_GAP - MENU_MARGIN;
  const most = view.height / 2;
  if (Math.min(menu.height, most) <= above || above >= below)
    return { left, top: null, bottom: view.height - button.top + MENU_GAP, maxHeight: Math.max(0, Math.min(above, most)) };
  return { left, top: button.bottom + MENU_GAP, bottom: null, maxHeight: Math.max(0, Math.min(below, most)) };
}

const samePlacement = (one: MenuPlacement, other: MenuPlacement) =>
  one.left === other.left && one.top === other.top && one.bottom === other.bottom && one.maxHeight === other.maxHeight;

// The popup is placed in the window (position: fixed), so the right rail's clipping does not reach
// it, and stays where it is in the document, so Tab and the outside-click close keep working. It
// follows its button while it is open (followAnchor.ts), the tray's size included.
function useMenuPlacement(button: RefObject<HTMLElement | null>, menu: RefObject<HTMLElement | null>): MenuPlacement | null {
  const [placement, setPlacement] = useState<MenuPlacement | null>(null);
  const measure = useCallback(() => {
    if (!button.current || !menu.current) return;
    const size = { width: menu.current.getBoundingClientRect().width, height: menu.current.scrollHeight };
    const next = menuPlacement(button.current.getBoundingClientRect(), size, { width: window.innerWidth, height: window.innerHeight });
    setPlacement((now) => (now && samePlacement(now, next) ? now : next));
  }, [button, menu]);
  const observed = useCallback(() => [button.current, menu.current, button.current?.closest('[data-tray-root]')], [button, menu]);
  useFollowAnchor(true, measure, observed);
  return placement;
}

interface TargetRowProps {
  agentId: string;
  short: string;
  tag: string | null;
  why: string;
  title?: string;
}

// One chat of the list, drawn as the approved mockup draws it (MIK-R188 rule 7, d9a1ec8f): a dot on a
// chosen chat, its role and task on one line, cut short at need, and its agent tag whole beside them.
// The checkbox keeps the choice for the keyboard and assistive technology, and is not drawn.
function TargetRow({ agentId, short, tag, why, title }: TargetRowProps) {
  const chosen = useTray((state) => state.targets.includes(agentId));
  return (
    <label className={look.menuRow} data-testid="tray-target" title={title}>
      <input
        type="checkbox"
        className={look.unseen}
        checked={chosen}
        onChange={() => trayStore.getState().toggleTarget(agentId)}
      />
      <span className={look.menuMark} data-target-mark aria-hidden="true">
        {chosen ? '●' : ''}
      </span>
      <span className={look.menuIdentity} data-target-identity>
        <span className={look.menuName}>{short}</span>
        {tag ? <span className={look.menuTag}>{` · ${tag}`}</span> : null}
      </span>
      <span className={look.menuWhy}>{why}</span>
    </label>
  );
}

function SeatListState() {
  const list = useSeatList();
  if (list.status === 'loading') return <div className={look.menuHint}>Reading the live seats…</div>;
  if (list.status === 'unavailable')
    return (
      <div className={look.menuHint} role="status">
        The seat list is unavailable: {list.detail}.{' '}
        <button type="button" className={look.textButton} onClick={() => void loadSeats()}>
          Retry
        </button>
      </div>
    );
  return (
    <>
      {list.nextOffset !== null ? (
        <div className={look.menuHint}>
          <button type="button" className={look.textButton} onClick={() => void loadSeats(true)}>
            More seats…
          </button>
        </div>
      ) : null}
      {list.status === 'ready' && !list.complete ? (
        <div className={look.menuHint} role="status" data-testid="tray-seats-incomplete">
          The host&apos;s list did not end: a live seat may be missing from it.
        </div>
      ) : null}
    </>
  );
}

function TargetList({ button }: { button: RefObject<HTMLButtonElement | null> }) {
  const menu = useRef<HTMLDivElement>(null);
  const placement = useMenuPlacement(button, menu);
  const list = useSeatList();
  const targets = useTray((state) => state.targets);
  const shown = useShownChat();
  const name = useChatNames();
  const listed = new Set(list.seats.map((seat) => seat.agentId));
  const why = (agentId: string) => (agentId === shown?.agentId ? 'shown · live' : 'live');
  return (
    <div
      ref={menu}
      role="group"
      aria-label="Send the tray to"
      className={look.menu}
      data-testid="tray-target-menu"
      style={placement ? { left: placement.left, top: placement.top ?? undefined, bottom: placement.bottom ?? undefined, maxHeight: placement.maxHeight } : undefined}
    >
      <div className={look.menuGroup}>Send the tray to</div>
      {targets
        .filter((agentId) => !listed.has(agentId))
        .map((agentId) => {
          const { short, tag, unnamed } = name(agentId);
          return <TargetRow key={agentId} agentId={agentId} short={short} tag={tag} why="chosen" title={unnamed ?? undefined} />;
        })}
      {list.seats.map((seat) => (
        <TargetRow
          key={seat.agentId}
          agentId={seat.agentId}
          short={seatRoleTask(seat)}
          tag={agentTag(seat, list.seats)}
          why={why(seat.agentId)}
          title={`agent ${seat.agentId}`}
        />
      ))}
      <SeatListState />
      <div className={look.menuHint}>
        A reference means the same thing in every chat: each agent opens it with AR&apos;s tools.
      </div>
    </div>
  );
}

export function TrayTargets() {
  const [open, setOpen] = useState(false);
  const targets = useTray((state) => state.targets);
  const name = useChatNames();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useDismiss(open, root, () => setOpen(false));
  // Each opening reads the host's list again from offset 0: new seats appear, archived ones go.
  useEffect(() => {
    if (open) void loadSeats();
  }, [open]);
  const { short, tag } = targetParts(targets, name);
  // One line in the tray's head: the role and task give way first, the agent tag stays whole.
  return (
    <div ref={root} className={look.targetAnchor}>
      <button
        ref={button}
        type="button"
        className={look.targetButton}
        aria-expanded={open}
        aria-label={`Send to: ${targetsLabel(targets, name)}. Choose the chats`}
        title={targetsTitle(targets, name)}
        onClick={() => setOpen((now) => !now)}
      >
        <span className={look.keepSpaces}>to: </span>
        <span className={look.targetName}>{short}</span>
        <span className={look.keepSpaces}>{tag ? ` · ${tag} ▾` : ' ▾'}</span>
      </button>
      {open ? <TargetList button={button} /> : null}
    </div>
  );
}
