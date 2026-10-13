import { memo, useRef, useState, type RefObject } from "react";
import { Button, Dialog, DialogTrigger, Popover } from "react-aria-components";

import { css, cva, cx } from "../../styled-system/css";
import { postAttentionDismiss } from "../data/actions";
import { fmtWait, selectQueue } from "../data/selectors";
import { servedAgeSeconds, useNowMs } from "../data/servedAges";
import { dashboardStore, useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";
import { columnTabButton } from "../grammar/viewTabLook";
import {
  ATTENTION_SEVERITIES,
  type AttentionItem,
  type AttentionSeverity,
  type TaskDocNode,
} from "../types/projection";
import { dim } from "./detail-panel/pageStyles";
import { masterIdsByFolder, shortTaskId } from "./lifecycle-list/rowState";

// The attention queue: the server-ranked list of what needs the human. It is no block of the
// column (MIK-R144 rule 3a): a bell in the head of the tasks column carries the number that waits
// and the colour of the most pressing entry, and opens the entries in a layer over the tree. The
// tree behind it keeps its place and its height. The layer is the dashboard's popover (the one of
// the model and effort control): it has a bound of its own, scrolls inside it, and closes by
// Escape, by a click outside and by the bell.
//
// What one does with an entry is unchanged. Its task is a link that opens the entry's lifecycle
// in the detail view and closes the layer: the deliberate queue↔detail coupling. Lifecycle-bound
// entries are dismissable: × on an entry and "Clear all" both POST a current acknowledgement.
// Lifecycle rows are scoped by lifecycle id, gate rows are consumed by cancelling/deleting the
// gate, and actionable-drift rows are repo-level one-shot signals anchored by the drift snapshot
// timestamp.
//
// The measures are the approved pages' measures; see lifecycle-list/columnLook.ts.
//
// The bell is a view tab at the column's size (`columnTabButton`). What only a bell has is here:
// it takes the colour of the most pressing entry that waits, and it is filled while its layer is
// open. Each of these is said on a state of the button, so it stands above the tab's own colour.
const bellShape = css({
  display: "inline-flex",
  alignItems: "stretch",
  "&[data-tone='alarm']": { color: "alarm", borderColor: "currentColor" },
  "&[data-tone='warn']": { color: "amber", borderColor: "currentColor" },
  "&[data-tone='info']": { color: "cyan", borderColor: "currentColor" },
  _expanded: { background: "color-mix(in oklab, currentColor 16%, token(colors.bg))" },
});
// The bell's face fills the button, so its hover text is the button's: a React Aria button passes
// no `title` on. It reaches over the tab's own room to the button's edge and sets the bell's
// narrower room; the number is set as a number, without the tab's letter spacing.
const bellFace = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.2625rem",
  marginInline: "-0.525rem",
  marginBlock: "-0.2625rem",
  paddingInline: "0.3938rem",
  paddingBlock: "0.2625rem",
  lineHeight: "1",
});
// A line drawing of 13 px in the bell's own colour.
const bellIcon = css({
  display: "block",
  width: "0.8125rem",
  height: "0.8125rem",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: "1.4",
  strokeLinecap: "round",
  strokeLinejoin: "round",
});
const bellCount = css({ fontWeight: "700", letterSpacing: "0" });
// Where the layer hangs: under the head of the column, a little inside the column's edges. The
// head (grammar/Panel's sticky band) is the box this line is laid out in.
const layerAnchor = css({
  position: "absolute",
  left: "calc(0.3938rem - 1px)",
  right: "calc(0.3938rem - 1px)",
  bottom: "0",
  height: "0",
  pointerEvents: "none",
});
const layer = cva({
  base: {
    width: "var(--trigger-width)",
    overflow: "auto",
    background: "bg",
    color: "ink",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "color-mix(in oklab, token(colors.muted) 45%, token(colors.grid))",
    borderRadius: "3px",
    boxShadow: "0 12px 30px oklch(0.08 0.02 250 / 0.8)",
    paddingInline: "0.6125rem",
    paddingBottom: "0.3938rem",
    scrollbarWidth: "thin",
    scrollbarColor: "token(colors.grid) transparent",
  },
  variants: { empty: { true: { paddingBottom: "0.1313rem" } } },
});
const layerBody = css({ outline: "none" });
// The head of the layer is one line at the column's own width. In a rail that was dragged
// narrower "Clear all" goes under the title and the title breaks: nothing is pushed out of sight.
const layerHead = css({
  position: "sticky",
  top: "0",
  zIndex: "2",
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  justifyContent: "space-between",
  columnGap: "0.4375rem",
  rowGap: "0.2625rem",
  minWidth: "0",
  marginInline: "-0.6125rem",
  marginBottom: "0.1313rem",
  paddingInline: "0.6125rem",
  paddingTop: "0.4375rem",
  paddingBottom: "0.3063rem",
  background: "bg",
});
const heading = css({ margin: "0", minWidth: "0" });
const nothing = css({ color: "muted", fontWeight: "400" });
const list = css({
  listStyle: "none",
  display: "grid",
  marginBlock: "0",
  marginInline: "-0.2625rem",
  padding: "0",
});
// An entry is two lines: mark, what, age, dismiss; under it the task.
const item = css({
  display: "grid",
  gridTemplateColumns: "1ch minmax(0, 1fr) auto auto",
  columnGap: "0.3938rem",
  alignItems: "baseline",
  paddingInline: "0.2625rem",
  paddingBlock: "0.21rem",
  fontSize: "0.7rem",
  lineHeight: "1.45",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "color-mix(in oklab, token(colors.grid) 55%, transparent)",
  _first: { borderTopWidth: "0" },
});
// The Dot is decorative (`aria-hidden`), so the severity has to be spoken by its wrapper. The
// wrapper needs a ROLE that can carry a name: `aria-label` on a bare span names a `generic`, which
// ARIA prohibits and which no screen reader announces (axe-core reports `aria-prohibited-attr` at
// `serious`), so the label reached nobody. `img` is the role for a mark that means something — it
// puts "Severity: warn" in the accessibility tree with the glyph hidden underneath it.
// (LifecycleList's equivalent span escapes the same bug only because it sits inside React Aria's
// `role="option"`, whose name-from-content absorbs the label.)
// Dot brings a margin for running text; here the grid's gap spaces it.
const severityMark = css({
  textAlign: "center",
  userSelect: "none",
  "& > span": { marginRight: "0" },
});
const what = css({ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const age = css({ fontSize: "0.6125rem", whiteSpace: "nowrap" });
const dismissButton = css({
  font: "inherit",
  lineHeight: "1",
  color: "muted",
  background: "transparent",
  borderStyle: "none",
  paddingBlock: "0",
  paddingInline: "0.1313rem",
  cursor: "pointer",
  _hover: { color: "ink" },
  _disabled: { opacity: 0.5, cursor: "default" },
});
// An entry that cannot be dismissed keeps the room of the ×'s padding, so its age stands where the
// approved page has it.
const noDismiss = css({ paddingInline: "0.1313rem" });
const where = cva({
  base: {
    gridColumn: "2 / 5",
    minWidth: "0",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    fontSize: "0.63rem",
    color: "muted",
  },
  variants: {
    link: {
      true: {
        font: "inherit",
        fontSize: "0.63rem",
        color: "cyan",
        background: "transparent",
        borderStyle: "none",
        padding: "0",
        textAlign: "left",
        cursor: "pointer",
        _hover: { textDecoration: "underline" },
      },
    },
  },
});

const EMPTY_TASK_DOCS: readonly TaskDocNode[] = [];
const KIND_WORDS: Record<AttentionSeverity, string> = {
  alarm: "alarm",
  warn: "warning",
  info: "information",
};
// The layer's bound: this part of the height of the column it lies over.
const LAYER_BOUND = 0.62;

function taskForAttention(item: AttentionItem, docs: readonly TaskDocNode[]): TaskDocNode | undefined {
  if (!item.lifecycleId) return undefined;
  const matches = docs.filter((doc) => doc.lifecycleId === item.lifecycleId);
  return matches.find((doc) => doc.kind !== "master") ?? matches[0];
}

// The cause of an entry: what differs between the entries of one task.
function causeForAttention(item: AttentionItem): string {
  return [item.title, item.detail].filter(Boolean).join(" · ");
}

// Where an entry belongs: its task as the tree names it, else its repository or lane.
function placeForAttention(
  item: AttentionItem,
  doc: TaskDocNode | undefined,
  masterIds: ReadonlyMap<string, string>,
): string {
  if (!doc) return item.repoId ?? item.lane;
  return [shortTaskId(doc, masterIds), doc.title].filter(Boolean).join(" ");
}

function dismissPayload(item: AttentionItem) {
  return {
    itemId: item.id,
    kind: item.kind,
    lifecycleId: item.lifecycleId ?? null,
    gateId: item.gateId,
  };
}

function canDismiss(item: AttentionItem): boolean {
  return Boolean(
    item.lifecycleId ||
      (item.kind === "gate-open" && item.gateId) ||
      item.kind === "actionable-drift",
  );
}

function dismissAttentionItem(
  item: AttentionItem,
  dismissing: ReadonlySet<string>,
  setDismissing: (updater: (prev: ReadonlySet<string>) => ReadonlySet<string>) => void,
): void {
  if (!canDismiss(item) || dismissing.has(item.id)) return;
  dashboardStore.getState().suppressAttention([item.id]);
  setDismissing((prev) => new Set(prev).add(item.id));
  void postAttentionDismiss(dismissPayload(item))
    .then((status) => {
      if (status !== "dismissed") dashboardStore.getState().releaseAttention([item.id]);
    })
    .finally(() =>
      setDismissing((prev) => {
        const next = new Set(prev);
        next.delete(item.id);
        return next;
      }),
    );
}

function clearAllAttention(
  items: AttentionItem[],
  clearing: boolean,
  setClearing: (value: boolean) => void,
): void {
  if (clearing || items.length === 0) return;
  const ids = items.map((item) => item.id);
  dashboardStore.getState().suppressAttention(ids);
  setClearing(true);
  void Promise.all(
    items.map(async (item) => ({
      id: item.id,
      status: await postAttentionDismiss(dismissPayload(item)),
    })),
  )
    .then((results) => {
      const failed = results.filter((result) => result.status !== "dismissed").map((result) => result.id);
      if (failed.length > 0) dashboardStore.getState().releaseAttention(failed);
    })
    .finally(() => setClearing(false));
}

// What the bell says in words: the number that waits and, in its hover, the kinds.
function bellWords(queue: readonly AttentionItem[]): { label: string; hover: string } {
  if (queue.length === 0) {
    return { label: "Attention: nothing waiting", hover: "Attention: nothing waiting" };
  }
  const label = `Attention: ${queue.length} waiting`;
  const kinds = ATTENTION_SEVERITIES.map((severity) => {
    const count = queue.filter((entry) => entry.severity === severity).length;
    return count > 0 ? `${count} ${KIND_WORDS[severity]}` : "";
  }).filter(Boolean);
  return { label, hover: `${label} (${kinds.join(", ")})` };
}

// The bell, drawn for this panel on a grid of 16: a dome 7.4 wide and 4 high round (8, 6.6), sides
// down to 8.9, a lip that flares out to 2.7 and 13.3 at 11.8, a clapper under it and a stem on
// top. It is the panel's own drawing, scaled as a whole until it reaches as far as the bell of the
// approved pages does in their pictures (9.5 by 12 px at 13 px). The body is one closed path, so
// `filled` can tint it while something waits.
function BellIcon({ filled }: { filled: boolean }) {
  return (
    <svg className={bellIcon} viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M2.7 11.8Q4.3 10 4.3 8.9V6.6A3.7 4 0 0 1 8 2.6A3.7 4 0 0 1 11.7 6.6V8.9Q11.7 10 13.3 11.8Z"
        fill={filled ? "currentColor" : "none"}
        fillOpacity="0.28"
      />
      <path d="M6.4 14A1.7 1.8 0 0 0 9.6 14" />
      <path d="M8 1.5V2.6" />
    </svg>
  );
}

function AttentionRow({
  entry,
  doc,
  place,
  nowMs,
  dismissing,
  onOpen,
  onDismiss,
}: {
  entry: AttentionItem;
  doc: TaskDocNode | undefined;
  place: string;
  nowMs: number;
  dismissing: boolean;
  onOpen: (lifecycleId: string) => void;
  onDismiss: (item: AttentionItem) => void;
}) {
  const lifecycleId = entry.lifecycleId;
  const cause = causeForAttention(entry);
  const waited = fmtWait(servedAgeSeconds(entry, entry.waitSeconds, nowMs));
  // An entry with a lifecycle opens it, also where no task document names that lifecycle.
  const target = doc ? place : `lifecycle ${lifecycleId}`;
  return (
    <li
      className={item}
      title={[cause, place, `Severity: ${entry.severity}`, `${entry.lane} · ${waited}`].join("\n")}
      data-testid="attn-item"
    >
      <span
        className={severityMark}
        role="img"
        aria-label={`Severity: ${entry.severity}`}
        data-testid="attn-severity"
      >
        <Dot variant={entry.severity} />
      </span>
      <span className={what}>{cause}</span>
      <span className={cx(age, dim)}>{waited}</span>
      {canDismiss(entry) ? (
        <button
          type="button"
          className={dismissButton}
          onClick={() => onDismiss(entry)}
          disabled={dismissing}
          data-testid="attn-dismiss"
          aria-label={`Dismiss ${cause}${doc ? `: ${place}` : ""}`}
          title="Dismiss"
        >
          ×
        </button>
      ) : (
        <span className={noDismiss} />
      )}
      {lifecycleId ? (
        <button
          type="button"
          className={where({ link: true })}
          onClick={() => onOpen(lifecycleId)}
          title={`Open ${target}`}
          data-testid="attn-open"
        >
          {target}
        </button>
      ) : (
        <span className={where()}>{place}</span>
      )}
    </li>
  );
}

function AttentionLayer({
  queue,
  bodyRef,
  onOpen,
}: {
  queue: readonly AttentionItem[];
  bodyRef: RefObject<HTMLElement | null>;
  onOpen: (lifecycleId: string) => void;
}) {
  const docs = useDashboard((state) => state.analytics?.taskDocuments ?? EMPTY_TASK_DOCS);
  // Wait times advance locally between emissions — the change gate no longer re-serves the queue
  // every tick just because the waits aged. The clock runs only while the layer is open: a
  // closed bell shows no age.
  const nowMs = useNowMs(10_000);
  const [clearing, setClearing] = useState(false);
  const [dismissing, setDismissing] = useState<ReadonlySet<string>>(() => new Set());
  const dismissableQueue = queue.filter(canDismiss);
  const masterIds = masterIdsByFolder(docs);
  // An entry that goes takes its × and the keyboard's focus with it. The layer takes the focus
  // first, so the keyboard stays in it: Escape still closes it and Tab goes on inside.
  const keepFocus = () => bodyRef.current?.focus();
  return (
    <>
      <div className={layerHead}>
        <h2 className={heading}>
          Attention ·{" "}
          {queue.length > 0 ? `${queue.length} waiting` : <span className={nothing}>nothing waiting</span>}
        </h2>
        {dismissableQueue.length > 0 ? (
          <button
            type="button"
            className={columnTabButton}
            onClick={() => {
              keepFocus();
              clearAllAttention(dismissableQueue, clearing, setClearing);
            }}
            disabled={clearing}
            data-testid="attn-clear"
          >
            {clearing ? "Clearing" : "Clear all"}
          </button>
        ) : null}
      </div>
      {queue.length > 0 ? (
        <ul className={list}>
          {queue.map((entry) => {
            const doc = taskForAttention(entry, docs);
            return (
              <AttentionRow
                key={entry.id}
                entry={entry}
                doc={doc}
                place={placeForAttention(entry, doc, masterIds)}
                nowMs={nowMs}
                dismissing={dismissing.has(entry.id)}
                onOpen={onOpen}
                onDismiss={(target) => {
                  keepFocus();
                  dismissAttentionItem(target, dismissing, setDismissing);
                }}
              />
            );
          })}
        </ul>
      ) : null}
    </>
  );
}

function AttentionQueueImpl({ onSelect }: { onSelect: (lifecycleId: string) => void }) {
  const queue = useDashboard(selectQueue);
  const [open, setOpen] = useState(false);
  const [bound, setBound] = useState<number | undefined>(undefined);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const bodyRef = useRef<HTMLElement>(null);
  const words = bellWords(queue);
  const pressing = ATTENTION_SEVERITIES.find((severity) =>
    queue.some((entry) => entry.severity === severity),
  );
  const changeOpen = (next: boolean) => {
    if (next) {
      const column = anchorRef.current?.closest("section");
      setBound(column?.clientHeight ? Math.round(column.clientHeight * LAYER_BOUND) : undefined);
    }
    setOpen(next);
  };
  return (
    <DialogTrigger isOpen={open} onOpenChange={changeOpen}>
      <span ref={anchorRef} className={layerAnchor} aria-hidden="true" />
      <Button
        className={cx(columnTabButton, bellShape)}
        data-tone={pressing}
        aria-label={words.label}
        data-testid="attention-bell"
      >
        <span className={bellFace} title={words.hover}>
          <BellIcon filled={queue.length > 0} />
          {queue.length > 0 ? <span className={bellCount}>{queue.length}</span> : null}
        </span>
      </Button>
      <Popover
        className={layer({ empty: queue.length === 0 })}
        triggerRef={anchorRef}
        placement="bottom start"
        offset={2}
        maxHeight={bound}
      >
        <Dialog ref={bodyRef} className={layerBody} aria-label="Attention" data-testid="attention-queue">
          <AttentionLayer
            queue={queue}
            bodyRef={bodyRef}
            onOpen={(lifecycleId) => {
              setOpen(false);
              onSelect(lifecycleId);
            }}
          />
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

// Memoized (tab-switch CPU): the bell sits in the head of a persistent rail panel. Its one prop
// keeps its identity, so the task list's own renders stop at this gate; the queue's store
// subscription still drives the bell's updates.
export const AttentionQueue = memo(AttentionQueueImpl);
