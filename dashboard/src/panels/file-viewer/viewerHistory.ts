// The File Viewer's history: addresses, labels and scroll positions stay; large bodies are
// reclaimed. A document or source body can be megabytes, and a long visit can open many
// files. Keeping every body would grow the tab without bound; this module keeps the
// navigable list (never a count limit) and releases the oldest bodies once their combined
// byte size passes a fixed budget. The current entry is always retained.
import type { FileContent } from "../../data/files";
import type { SidecarView } from "./DualPane";

export interface OpenCode {
  kind: "code";
  content: FileContent;
  sidecar: SidecarView;
  rendered: boolean;
  scrollTop: number;
  /** True once history has saved this entry's position; zero is then a real position. */
  scrollKnown?: boolean;
  anchor?: string;
  bodyReleased?: boolean;
}

export interface OpenDocument {
  kind: "document";
  path: string;
  sidecar: SidecarView;
  scrollTop: number;
  /** True once history has saved this entry's position; zero is then a real position. */
  scrollKnown?: boolean;
  anchor?: string;
  bodyReleased?: boolean;
}

export type OpenEntry = OpenCode | OpenDocument;

export interface ViewerHistory {
  entries: OpenEntry[];
  index: number;
}

// Bodies above this many UTF-8 bytes are released oldest-first; addresses, labels and
// scroll positions are never released.
export const VIEWER_BODY_BUDGET = 8 * 1024 * 1024;

function byteLength(value: string | null | undefined): number {
  return value ? new TextEncoder().encode(value).length : 0;
}

function sidecarBytes(sidecar: SidecarView): number {
  return sidecar.state === "markdown" ? byteLength(sidecar.body) : 0;
}

export function bodyBytes(entry: OpenEntry): number {
  if (entry.bodyReleased) return 0;
  if (entry.kind === "code") return byteLength(entry.content.content) + sidecarBytes(entry.sidecar);
  return sidecarBytes(entry.sidecar);
}

function releaseBody(entry: OpenEntry): OpenEntry {
  if (entry.kind === "code") {
    return {
      ...entry,
      content: { ...entry.content, content: "", truncated: false },
      sidecar: { state: "empty" },
      bodyReleased: true,
    };
  }
  return { ...entry, sidecar: { state: "empty" }, bodyReleased: true };
}

export function releaseBodies(
  state: ViewerHistory,
  budget = VIEWER_BODY_BUDGET,
): ViewerHistory {
  let total = state.entries.reduce((sum, entry) => sum + bodyBytes(entry), 0);
  if (total <= budget) return state;
  const entries = [...state.entries];
  for (let index = 0; index < entries.length && total > budget; index += 1) {
    if (index === state.index || entries[index].bodyReleased) continue;
    total -= bodyBytes(entries[index]);
    entries[index] = releaseBody(entries[index]);
  }
  return { entries, index: state.index };
}

export function keepScroll(state: ViewerHistory, scrollTop: number): ViewerHistory {
  if (state.index < 0) return state;
  const entries = [...state.entries];
  entries[state.index] = { ...entries[state.index], scrollTop, scrollKnown: true };
  return { entries, index: state.index };
}
