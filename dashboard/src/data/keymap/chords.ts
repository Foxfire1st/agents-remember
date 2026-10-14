// The chord table — data, not code, so the tinykeys binding, the labels beside the controls and the
// collision check all read one source. Zone scoping is per-chord: a chord fires only when the
// event's zone is listed. Browser-reserved chords (BROWSER_FORBIDDEN) are banned everywhere.

import type { Zone } from "./zones";

export interface ZoneChord {
  /** tinykeys binding string ("[Shift]" marks an optional modifier). */
  chord: string;
  /** Human label, shown beside the control the chord drives. */
  label: string;
  commandId: string;
  /** Zones where the chord is handled; every other zone passes it through. */
  zones: readonly Zone[];
}

// The reviewer's change traversal (MIK-R33, adopting ICR-R32 rule 7): next and previous change in the
// family tree. Printable, so the generic routing contract keeps them inert in inputs, textareas and
// contenteditable regions; handled only in the reviewer's own zone.
export const REVIEW_CHORDS: ZoneChord[] = [
  {
    chord: "J",
    label: "j",
    commandId: "review.nextChange",
    zones: ["review"],
  },
  {
    chord: "K",
    label: "k",
    commandId: "review.previousChange",
    zones: ["review"],
  },
];
