// AR's tray (MIK-R188): what a pick is. A pick of an object is a reference, never a copy of the
// object's text (rule 2). The dashboard holds the structured reference; the server renders each
// reference's written form, with the call that opens it, when the message is composed and filed.
import type { BlockIdentity, ComparisonTrees, HunkIdentity } from '../panels/review/blockIdentity';
import type { TaskDocumentRef } from '../types/projection';

// The review comparison a review-page reference names: its number, its pin ref and its four trees.
export interface ComparisonRef {
  repository: string;
  task: string; // the master's folder name, as the review target names it
  leaf: string;
  number: number;
  pinRef: string; // refs/ar/review/<task>/<leaf>/<n>
  trees: ComparisonTrees;
}

// An invariant or a family is named by its text id (INV-…/FAM-…). Taken on the review page, it also
// names the comparison it was read in: the server then reads the leaf's memory and finds the record's
// path in that comparison's memory tree (`memoryTree`), not in the repository's checkout.
export type TrayReference =
  | { kind: 'invariant'; repository: string; id: string; revisionId?: string; memoryTree: string; comparison?: ComparisonRef }
  | { kind: 'family'; repository: string; id: string; revisionId?: string; memoryTree: string; comparison?: ComparisonRef }
  | { kind: 'review-block'; comparison: ComparisonRef; block: BlockIdentity; invariant: string; invariantRevisionId?: string }
  | { kind: 'code-without-invariant'; comparison: ComparisonRef; hunk: HunkIdentity | { kind: 'file'; path: string } }
  | { kind: 'task'; reference: TaskDocumentRef; id: string }
  | { kind: 'file-line'; repository: string; scope: string; path: string; start: number; end: number }
  | { kind: 'change-set'; comparison: ComparisonRef };

export type TextProvenance =
  | { source: 'panel'; panel: string }
  | { source: 'chat'; agentId: string; messageId: string | null; messageTime: string };

// What a ⊕ or a selection offer hands to the tray. `title` is the chip's text: the object's id and
// title, for example "RLZ-78JFRR · TurnEndedTracker · turn_ended.ts L99–144".
export type TrayPickInput =
  | { kind: 'reference'; title: string; reference: TrayReference }
  | { kind: 'text'; title: string; text: string; provenance: TextProvenance }
  | { kind: 'image'; title: string; mimeType: string; sha256: string; bytes: number };

export interface TrayPick {
  id: string; // stable within the tray
  key: string; // the object's identity: a second ⊕ on the same object adds nothing
  addedAt: string;
  input: TrayPickInput;
  note: string;
}

// ---- The server's answers (rules 7, 9, 10). The routes are the dashboard server's; the dashboard
// only reads and posts them.

// One row of the host-wide list of live AR seats (`GET /api/role-chats?offset=<n>`).
export interface TraySeatTask {
  repository: string;
  path: string;
  id: string;
  title: string;
}

export interface TraySeat {
  agentId: string;
  workspaceId?: string;
  role: string;
  task: TraySeatTask | null; // null: a seat without a task ("Projects")
  createdAt: string;
}

// `complete: false` says the host's listing did not end: a seat missing from it is unknown, not gone.
export type TraySeatPage =
  | { state: 'seats'; seats: TraySeat[]; nextOffset: number | null; complete: boolean }
  | { state: 'unavailable'; detail: string };

// A pick as the developer-send route takes it. References travel structured; the server renders
// each written form with the call that opens it. Image bytes travel as base64.
export interface TrayWirePick {
  id: string;
  kind: 'reference' | 'text' | 'image';
  title: string;
  note: string;
  reference?: TrayReference;
  text?: string;
  provenance?: TextProvenance;
  image?: { mimeType: string; data: string };
}

// The body of one `POST /api/role-chats/{agentId}/message`: one per target.
export interface TrayMessageBody {
  messageId: string;
  repository: string;
  text: string;
  picks: TrayWirePick[];
}

export type TrayDeliveryState = 'delivered' | 'held' | 'refused';

// The route's answer for one target. A held message counts as sent and is never sent again.
export interface TrayDelivery {
  state: TrayDeliveryState;
  messageId: string;
  messageFile: string | null;
  messageFileSha256: string | null;
  reason?: string;
  detail?: string;
}

// One line of a chat's sent-picks log (`GET /api/role-chats/{agentId}/sent`).
export interface TraySentEntry {
  time: string;
  targetAgentId: string;
  pickIds: string[];
  messageId: string;
  messageFile: string;
  sha256: string;
  state: string;
}
