import { useEffect, useMemo, useRef, useState } from "react";
import { sameTaskDocumentRef } from "../../data/taskIdentity";
import type { TaskDocumentRef } from "../../types/projection";
import type { RoleLaunchSelection } from "../roleLaunchModel";
import { documentAgents, type DocumentAgent, type DocumentBinding } from "./model";

/** How many selections the dashboard keeps an answer for; the oldest answer leaves first. */
export const KNOWN_CHATS_LIMIT = 512;
/** How many documents one request for the tree's chats names; the backend refuses more. */
export const TREE_SELECTION_LIMIT = 256;
/** How long the tree's answer stands before it is asked for again while the chat is shown. */
const TREE_REFRESH_MS = 30_000;

/**
 * The chats the dashboard already knows, by selection: the tree's answer and every fresh read
 * of one document. A working surface only: bounded, and what is dropped is asked for again.
 */
export class KnownChats {
  private readonly rows = new Map<string, DocumentAgent[]>();

  get size(): number {
    return this.rows.size;
  }

  get(selection: string): DocumentAgent[] | undefined {
    return this.rows.get(selection);
  }

  /** A renewed answer is the newest row; over the limit the oldest row leaves. */
  set(selection: string, agents: DocumentAgent[]): void {
    this.rows.delete(selection);
    this.rows.set(selection, agents);
    if (this.rows.size <= KNOWN_CHATS_LIMIT) return;
    for (const oldest of this.rows.keys()) {
      this.rows.delete(oldest);
      break;
    }
  }
}

/** How near a document of the tree is to the viewed one: its own master, its sprint, elsewhere. */
function distance(row: DocumentBinding, viewed: DocumentBinding | undefined): number {
  if (!viewed) return 0;
  const { sprintDocumentRef, masterDocumentRef } = row.binding.selection;
  const at = viewed.binding.selection;
  if (!sameTaskDocumentRef(sprintDocumentRef, at.sprintDocumentRef)) return 2;
  if (!masterDocumentRef) return 0;
  return sameTaskDocumentRef(masterDocumentRef, at.masterDocumentRef) ? 0 : 1;
}

/**
 * The selections one request for the tree's chats names: every document of the tree while it
 * fits the limit, else the nearest to the viewed document first, each group in the tree's order.
 */
export function treeSelections(tree: DocumentBinding[], viewed: TaskDocumentRef | undefined): RoleLaunchSelection[] {
  if (tree.length <= TREE_SELECTION_LIMIT) return tree.map((row) => row.binding.selection);
  const at = viewed ? tree.find((row) => sameTaskDocumentRef(row.ref, viewed)) : undefined;
  const near: DocumentBinding[][] = [[], [], []];
  for (const row of tree) near[distance(row, at)].push(row);
  return near.flat().slice(0, TREE_SELECTION_LIMIT).map((row) => row.binding.selection);
}

async function readTree(selections: string, signal: AbortSignal): Promise<unknown[] | null> {
  const response = await fetch("/api/role-launch/document-chats/tree", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: '{"selections":' + selections + "}", signal,
  });
  const value = response.ok ? await response.json() as { chats?: unknown } : null;
  return Array.isArray(value?.chats) ? value.chats : null;
}

/** Whether the browser shows this tab. */
function useTabShown(): boolean {
  const [shown, setShown] = useState(() => !document.hidden);
  useEffect(() => {
    const onChange = () => setShown(!document.hidden);
    document.addEventListener("visibilitychange", onChange);
    onChange();
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return shown;
}

/**
 * Keeps the chats of the tree's documents known before a click. The tree is asked for in one
 * request when it is first shown, when its documents change, and again every half minute while
 * the chat is shown, so that a chat started elsewhere is known too. Nothing is asked while the
 * chat pane or the browser's tab is hidden; shown again, the tree is asked for once its last
 * answer is a half minute old, at once when it already is. A backend that does not answer the
 * request (an older build, an unreachable host) leaves the store as it is: the read of the one
 * selected document then answers alone, as before.
 */
export function useKnownChats(tree: DocumentBinding[], viewed: TaskDocumentRef | undefined, active: boolean) {
  const [known] = useState(() => new KnownChats());
  const [, setLearned] = useState(0);
  const shown = useTabShown();
  const selections = useMemo(() => JSON.stringify(treeSelections(tree, viewed)), [tree, viewed]);
  // The selections last asked for to the end, and when their request ended.
  const asked = useRef<{ selections: string; at: number } | null>(null);
  useEffect(() => {
    if (!active || !shown || selections === "[]") return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      const chats = await readTree(selections, controller.signal).catch(() => null);
      if (controller.signal.aborted) return;
      asked.current = { selections, at: Date.now() };
      (JSON.parse(selections) as RoleLaunchSelection[]).forEach((selection, index) => {
        const agents = documentAgents((chats?.[index] as { agents?: unknown } | undefined)?.agents);
        if (agents) known.set(JSON.stringify(selection), agents);
      });
      if (chats) setLearned((count) => count + 1);
      timer = setTimeout(() => void read(), TREE_REFRESH_MS);
    };
    const last = asked.current?.selections === selections ? asked.current.at : null;
    const wait = last === null ? 0 : Math.max(0, last + TREE_REFRESH_MS - Date.now());
    if (wait === 0) void read();
    else timer = setTimeout(() => void read(), wait);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selections, active, shown, known]);
  return known;
}

/**
 * The agents of the selection: its fresh answer, else what is already known of it, so a known
 * selection switches with the click and the fresh answer corrects it. Every fresh answer is
 * kept. `usable` is false while the selection is not read at all, and for a started request,
 * which is read by its own answer only.
 */
export function useSelectionAgents(
  known: KnownChats, selection: RoleLaunchSelection, fresh: DocumentAgent[] | null | undefined, usable: boolean,
): DocumentAgent[] | null {
  const key = JSON.stringify(selection);
  useEffect(() => {
    if (usable && fresh) known.set(key, fresh);
  }, [known, key, usable, fresh]);
  if (fresh) return fresh;
  return (usable ? known.get(key) : undefined) ?? null;
}
