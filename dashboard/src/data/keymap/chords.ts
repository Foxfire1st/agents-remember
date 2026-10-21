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

// The review page's block traversal (MIK-R89 v2 rule 11): next and previous block of the reading
// column, by the block identity (panels/review/changeTraversal.ts). Printable, so the generic routing
// contract keeps them inert in inputs, textareas and contenteditable regions; handled only in the
// review's own zone.
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
