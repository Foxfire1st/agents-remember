// The thin React binding over the pure keymap logic (260715-FEUI-L1 S4): tinykeys at the window,
// capture-phase, active only while the sessions view is the visible view. Every handler defers to
// data/keymap for zone resolution + the routing contract — this file owns ONLY the DOM wiring.
// tinykeys' default ignore (skip form elements) is disabled: the composer chords MUST fire inside
// a textarea; editable-target suppression is the zone contract's job (printables only, R7).
import { useEffect, useRef } from "react";
import { tinykeys, type KeybindingsMap } from "tinykeys";

import {
  keymapCommandIsActive,
  useEffectiveKeymap,
} from "../../data/keymap/preferences";
import { PTY_RESERVED } from "../../data/keymap/reserved";
import { routeKey, slashOpensPalette, zoneForTarget } from "../../data/keymap/zones";

type KeyHandler = (event: KeyboardEvent) => void;

export function useKeyboardZones({
  active,
  dispatch,
}: {
  /** The view is the visible cockpit view (the hidden keep-alive layer never grabs keys). */
  active: boolean;
  /** Routes a command id into the registry with the view's live context. */
  dispatch: (commandId: string) => void;
}) {
  const dispatchRef = useRef(dispatch);
  dispatchRef.current = dispatch;
  const keymap = useEffectiveKeymap();

  useEffect(() => {
    if (!active) return undefined;

    // One binding string can serve several zones with different actions (F6: chrome region-cycle
    // vs PTY exit-to-chrome), so handlers accumulate per chord and at most one acts per event.
    const handlers = new Map<string, KeyHandler[]>();
    const add = (chord: string, handler: KeyHandler) => {
      const list = handlers.get(chord) ?? [];
      list.push(handler);
      handlers.set(chord, list);
    };

    for (const entry of keymap.bindings) {
      // In Vim profile, Escape belongs to Vim's insert→normal transition. F6 remains the
      // invariant cockpit escape, including from normal mode, so the editor cannot trap focus.
      if (!keymapCommandIsActive(keymap, entry.commandId)) continue;
      add(entry.chord, (event) => {
        const target = event.target as Element | null;
        const zone = zoneForTarget(target);
        if (!entry.zones.includes(zone)) return;
        if (routeKey(zone, event, target) !== "handle") return;
        event.preventDefault();
        event.stopPropagation();
        dispatchRef.current(entry.commandId);
      });
    }

    // The PTY reserved set — the ONLY chords handled when focus sits in the pty zone. The
    // tinykeys match already proved the chord, but routeKey stays the authority (reserved.ts
    // data), so an unbound/removed entry can never be intercepted by a stale binding.
    for (const reserved of PTY_RESERVED) {
      if (!reserved.bound || !reserved.tinykeys) continue;
      add(reserved.tinykeys, (event) => {
        const target = event.target as Element | null;
        if (zoneForTarget(target) !== "pty") return;
        if (routeKey("pty", event, target) !== "handle") return;
        event.preventDefault();
        event.stopPropagation();
        dispatchRef.current(reserved.action);
      });
    }

    // Composer rule (§5.2): `/` at the start of a line opens the palette. A deliberate exception
    // to printable-suppression, gated by the pure caret test. `[Shift]` keeps layouts where `/`
    // needs shift (e.g. German Shift+7) working.
    add("[Shift]+/", (event) => {
      const target = event.target as HTMLTextAreaElement | null;
      if (zoneForTarget(target) !== "composer") return;
      if (typeof target?.value !== "string") return;
      if (!slashOpensPalette(target.value, target.selectionStart)) return;
      event.preventDefault();
      event.stopPropagation();
      dispatchRef.current("palette.open");
    });

    const map: KeybindingsMap = {};
    for (const [chord, list] of handlers) {
      map[chord] = (event) => {
        for (const handler of list) {
          if (event.defaultPrevented) return;
          handler(event);
        }
      };
    }
    return tinykeys(window, map, { ignore: () => false, capture: true });
  }, [active, keymap]);
}
