// One pick in the tray (MIK-R188 rules 2, 3 and 6): its id and title, where it came from, its note
// ("+ note", then an inline field), the three quick intents and its remove control (×). The quick
// intents show in a row just outside the chip while it is in use.
import { useCallback, useEffect, useRef, useState, type FocusEvent, type PointerEvent, type RefObject } from 'react';
import { useStore } from 'zustand';
import { createStore } from 'zustand/vanilla';

import { useFollowAnchor } from './followAnchor';
import { pickKindWords, provenanceLine } from './trayModel';
import { roleOf, useSeatList } from './traySeats';
import { trayImageStore, trayStore } from './trayStore';
import * as look from './trayStyles';
import type { TrayPick } from './types';

export const QUICK_INTENTS = ['explain', 'challenge this', 'is this a bug?'] as const;

const ICON: Record<TrayPick['input']['kind'], string> = { reference: '⊕', text: '❝', image: '▣' };

// "RLZ-78JFRR · TurnEndedTracker · turn_ended.ts L99–144" reads as the id, then the title.
function splitTitle(title: string): [string, string] {
  const cut = title.indexOf(' · ');
  return cut < 0 ? [title, ''] : [title.slice(0, cut), title.slice(cut + 3)];
}

function Thumbnail({ sha256 }: { sha256: string }) {
  const [url, setUrl] = useState<string | null | 'missing'>(null);
  useEffect(() => {
    let made: string | null = null;
    let live = true;
    void trayImageStore.get(sha256).then((blob) => {
      if (!live) return;
      if (!blob || typeof URL.createObjectURL !== 'function') return setUrl(blob ? null : 'missing');
      made = URL.createObjectURL(blob);
      setUrl(made);
    });
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [sha256]);
  if (url === 'missing') return <span className={look.chipIcon} title="The image's bytes were not kept by this browser">▣?</span>;
  return url ? <img className={look.thumb} src={url} alt="" /> : <span className={look.chipIcon}>▣</span>;
}

function NoteField({ pick, onDone }: { pick: TrayPick; onDone: () => void }) {
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => field.current?.focus(), []);
  const save = () => {
    trayStore.getState().setNote(pick.id, field.current?.value ?? pick.note);
    onDone();
  };
  return (
    <input
      ref={field}
      className={look.noteInput}
      aria-label={`Note on ${pick.input.title}`}
      placeholder="Your note on this pick"
      defaultValue={pick.note}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === 'Escape') {
          event.preventDefault();
          save();
        }
      }}
      onBlur={save}
    />
  );
}

function Note({ pick }: { pick: TrayPick }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <NoteField pick={pick} onDone={() => setEditing(false)} />;
  if (pick.note)
    return (
      <button type="button" className={`${look.textButton} ${look.noteText}`} aria-label={`Edit the note on ${pick.input.title}`} onClick={() => setEditing(true)}>
        ✎ {pick.note}
      </button>
    );
  return (
    <button type="button" className={look.textButton} aria-label={`Add a note to ${pick.input.title}`} onClick={() => setEditing(true)}>
      + note
    </button>
  );
}

// ---- the quick intents' row ----

// The row's least distance to the window's edges, in pixels.
const QUICK_MARGIN = 8;

type Edges = Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom'>;

export interface QuickPlacement {
  left: number;
  top: number;
  side: 'below' | 'above';
}

// Where a chip's quick intents go: one row just outside the chip, its top edge on the chip's bottom
// border, so the pointer moves onto it without leaving the chip. Above the chip, its bottom edge on
// the chip's top border, where the tray has no room below or where the chip below is in use: the row
// never lies over the id, the × or the focused control of a chip in use. Its left edge on the chip's,
// inside the window. `room` is the tray's top and bottom in the window and the window's width;
// `taken` names a neighbouring chip in use.
export function quickPlacement(
  chip: Edges,
  bar: { width: number; height: number },
  room: { top: number; bottom: number; width: number },
  taken: { above: boolean; below: boolean },
): QuickPlacement {
  const left = Math.max(QUICK_MARGIN, Math.min(chip.left, room.width - bar.width - QUICK_MARGIN));
  const below: QuickPlacement = { left, top: chip.bottom - 1, side: 'below' };
  const above: QuickPlacement = { left, top: chip.top + 1 - bar.height, side: 'above' };
  if (taken.below) return above;
  if (taken.above || below.top + bar.height <= room.bottom) return below;
  return above.top >= room.top ? above : below;
}

// The chip the pointer is over and the chip that holds the keyboard's focus, by pick id. A chip in
// use places its row off its neighbour in use.
const quickUse = createStore<{ hovered: string | null; focused: string | null }>(() => ({ hovered: null, focused: null }));

const release = (key: 'hovered' | 'focused', id: string) =>
  quickUse.setState((state) => (state[key] === id ? { ...state, [key]: null } : state));

function inUse(chip: Element | null): boolean {
  const id = chip?.getAttribute('data-pick-id');
  if (!chip || !id) return false;
  const { hovered, focused } = quickUse.getState();
  return id === hovered || (id === focused && chip.matches(':has(:focus-visible)'));
}

function trayRoom(chip: Element): { top: number; bottom: number; width: number } {
  const tray = chip.closest('[data-tray-root]')?.getBoundingClientRect();
  const top = tray ? Math.max(0, tray.top) : 0;
  const bottom = tray ? Math.min(window.innerHeight, tray.bottom) : window.innerHeight;
  return { top, bottom, width: window.innerWidth };
}

// The chip's part in `quickUse`: the pointer over it (a touch keeps it until another chip is
// touched, as the browser's hover does), the keyboard's focus in it.
function useQuickUse(id: string) {
  useEffect(
    () => () => {
      release('hovered', id);
      release('focused', id);
    },
    [id],
  );
  return {
    onPointerEnter: () => quickUse.setState({ hovered: id }),
    onPointerLeave: (event: PointerEvent) => {
      if (event.pointerType !== 'touch') release('hovered', id);
    },
    onFocus: (event: FocusEvent) => {
      if ((event.target as Element).matches(':focus-visible')) quickUse.setState({ focused: id });
      else release('focused', id);
    },
    onBlur: (event: FocusEvent) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) release('focused', id);
    },
  };
}

// While the chip is in use, its row follows it (followAnchor.ts), the list's and the tray's size
// included, and keeps off a neighbour that comes into use.
function useQuickPlacement(id: string, chip: RefObject<HTMLLIElement | null>, bar: RefObject<HTMLSpanElement | null>) {
  const engaged = useStore(quickUse, (state) => (state.hovered === id || state.focused === id ? state : null)) !== null;
  const [placement, setPlacement] = useState<QuickPlacement | null>(null);
  const measure = useCallback(() => {
    const node = chip.current;
    const row = bar.current?.getBoundingClientRect();
    if (!node || !row) return;
    const taken = { above: inUse(node.previousElementSibling), below: inUse(node.nextElementSibling) };
    const next = quickPlacement(node.getBoundingClientRect(), { width: row.width, height: row.height }, trayRoom(node), taken);
    setPlacement((now) => (now && now.left === next.left && now.top === next.top ? now : next));
  }, [chip, bar]);
  const observed = useCallback(
    () => [chip.current, bar.current, chip.current?.parentElement, chip.current?.closest('[data-tray-root]')],
    [chip, bar],
  );
  useFollowAnchor(engaged, measure, observed);
  return placement;
}

// After the note in the keyboard's order; drawn just outside the chip while it is in use.
function QuickIntents({ pick, bar, placement }: { pick: TrayPick; bar: RefObject<HTMLSpanElement | null>; placement: QuickPlacement | null }) {
  return (
    <span ref={bar} className={look.quick} data-quick style={placement ? { left: placement.left, top: placement.top } : undefined}>
      {QUICK_INTENTS.map((intent) => (
        <button
          key={intent}
          type="button"
          className={look.quickChip}
          aria-label={`Set the note on ${pick.input.title} to “${intent}”`}
          aria-pressed={pick.note === intent}
          onClick={() => trayStore.getState().setNote(pick.id, intent)}
        >
          {intent}
        </button>
      ))}
    </span>
  );
}

export function TrayChip({ pick }: { pick: TrayPick }) {
  useSeatList(); // a passage's provenance names its chat's role once the seat list has it
  const input = pick.input;
  const [id, title] = input.kind === 'text' ? [`“${input.title}”`, ''] : splitTitle(input.title);
  const where = input.kind === 'text' ? provenanceLine(input.provenance, roleOf) : pickKindWords(input);
  const chip = useRef<HTMLLIElement>(null);
  const bar = useRef<HTMLSpanElement>(null);
  const placement = useQuickPlacement(pick.id, chip, bar);
  const use = useQuickUse(pick.id);
  return (
    <li ref={chip} className={look.chip} data-testid="tray-pick" data-pick-kind={input.kind} data-pick-id={pick.id} {...use}>
      {input.kind === 'image' ? <Thumbnail sha256={input.sha256} /> : <span className={look.chipIcon}>{ICON[input.kind]}</span>}
      <div className={look.chipBody}>
        <div className={look.chipLine1}>
          <span className={look.chipId}>{id}</span>
          {title ? <span className={look.chipTitle}>{title}</span> : null}
        </div>
        <div className={look.chipLine2}>
          <span>{where}</span>
          <Note pick={pick} />
          <QuickIntents pick={pick} bar={bar} placement={placement} />
        </div>
      </div>
      <button
        type="button"
        className={look.close}
        aria-label={`Remove ${input.title} from the tray`}
        onClick={() => trayStore.getState().remove(pick.id)}
      >
        ×
      </button>
    </li>
  );
}
