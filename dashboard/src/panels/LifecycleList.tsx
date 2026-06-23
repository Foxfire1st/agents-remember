import { useState } from "react";

import {
  Header,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  ToggleButton,
  ToggleButtonGroup,
} from "react-aria-components";

import { css, cva } from "../../styled-system/css";
import { buildTree, fmtWait, type Pivot } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";
import { Panel } from "../grammar/Panel";
import type { TaskDocNode } from "../types/projection";

// The single unit list (note 01: the lifecycle is THE unit; note 06 IA). A BY REPO | BY PHASE pivot
// (React Aria ToggleButtonGroup) over every lifecycle (fleeting + persistent), presented as a React
// Aria ListBox so the 30+ rows are arrow-navigable + type-aheadable from the keyboard; selecting a
// row drives the centre detail. A task-progress hint surfaces the work each lifecycle carries.
const sizing = css({ flex: "1 1 0" });
const headRow = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.5rem",
});
const headTitle = css({ margin: "0" });
const pivotBar = css({ display: "flex", gap: "0.25rem" });
const pivotBtn = css({
  font: "inherit",
  fontSize: "0.7rem",
  letterSpacing: "0.06em",
  paddingInline: "0.45rem",
  paddingBlock: "0.2rem",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _selected: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const listBox = css({ listStyle: "none", display: "grid", gap: "0.45rem", outline: "none" });
const section = css({ display: "grid", gap: "0.12rem" });
const groupHeader = css({
  color: "amber",
  fontSize: "0.74rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  paddingBlock: "0.1rem",
});
const row = cva({
  base: {
    display: "flex",
    alignItems: "baseline",
    gap: "0.4rem",
    width: "100%",
    background: "bg",
    borderLeftWidth: "2px",
    borderLeftStyle: "solid",
    borderLeftColor: "cyan",
    paddingInline: "0.4rem",
    paddingBlock: "0.25rem",
    cursor: "pointer",
    outline: "none",
    _selected: { outline: "1px solid token(colors.amber)" },
    _focusVisible: { outline: "1px solid token(colors.amber)" },
  },
  variants: { fleeting: { true: { borderLeftStyle: "dashed", opacity: "0.85" } } },
});
const rowId = css({
  fontWeight: "600",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const rowSec = css({ color: "cyan", fontSize: "0.76rem" });
const rowGate = css({
  maxWidth: "8rem",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: "0.66rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.28rem",
  paddingBlock: "0.04rem",
});
const rowMeta = css({ marginLeft: "auto", color: "muted", fontSize: "0.72rem", whiteSpace: "nowrap" });

export function LifecycleList({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [pivot, setPivot] = useState<Pivot>("repo");
  const lifecycles = useDashboard((s) => s.lifecycles);
  const analytics = useDashboard((s) => s.analytics);
  const docsByLifecycle = groupDocs(analytics?.taskDocuments ?? []);
  const tree = buildTree(Object.values(lifecycles), pivot);

  const head = (
    <div className={headRow}>
      <h2 className={headTitle}>Tasks · {Object.keys(lifecycles).length}</h2>
      <ToggleButtonGroup
        className={pivotBar}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[pivot]}
        onSelectionChange={(keys) => {
          const next = [...keys][0];
          if (next === "repo" || next === "phase") setPivot(next);
        }}
        aria-label="Group tasks by"
      >
        <ToggleButton id="repo" className={pivotBtn}>
          BY REPO
        </ToggleButton>
        <ToggleButton id="phase" className={pivotBtn}>
          BY PHASE
        </ToggleButton>
      </ToggleButtonGroup>
    </div>
  );

  return (
    <Panel testid="lifecycle-list" head={head} className={sizing}>
      {tree.length === 0 ? (
        <p className="muted">No tasks.</p>
      ) : (
        <ListBox
          className={listBox}
          aria-label="Tasks"
          selectionMode="single"
          selectedKeys={selectedId ? [selectedId] : []}
          onSelectionChange={(keys) => {
            const id = [...keys][0];
            if (typeof id === "string") onSelect(id);
          }}
        >
          {tree.map((group) => (
            <ListBoxSection key={group.key} className={section}>
              <Header className={groupHeader}>{group.label}</Header>
              {group.lifecycles.map((lifecycle) => {
                const docs = docsByLifecycle.get(lifecycle.id) ?? [];
                const label = lifecycle.id.includes("/")
                  ? lifecycle.id.slice(lifecycle.id.indexOf("/") + 1)
                  : lifecycle.id;
                const secondary = pivot === "repo" ? lifecycle.phase : (lifecycle.repoId ?? "—");
                const hint = taskHint(docs);
                const gate = gateHint(lifecycle.gate?.kind, lifecycle.ask);
                return (
                  <ListBoxItem
                    key={lifecycle.id}
                    id={lifecycle.id}
                    textValue={label}
                    className={row({ fleeting: lifecycle.fleeting })}
                  >
                    <Dot variant={lifecycle.state} />
                    <span className={rowId}>{label}</span>
                    <span className={rowSec}>{secondary}</span>
                    {gate ? <span className={rowGate}>{gate}</span> : null}
                    <span className={rowMeta}>
                      {hint ? `${hint} · ` : ""}
                      {fmtWait(lifecycle.staleSeconds)}
                      {lifecycle.inferred ? " · inf" : ""}
                    </span>
                  </ListBoxItem>
                );
              })}
            </ListBoxSection>
          ))}
        </ListBox>
      )}
    </Panel>
  );
}

function gateHint(kind: string | undefined, ask: Record<string, unknown> | undefined): string {
  if (kind) return kind;
  const question = ask?.question;
  if (typeof question === "string" && question.trim()) return question;
  return ask ? "ask" : "";
}

function groupDocs(docs: TaskDocNode[]): Map<string, TaskDocNode[]> {
  const byLifecycle = new Map<string, TaskDocNode[]>();
  for (const doc of docs) {
    const list = byLifecycle.get(doc.lifecycleId);
    if (list) list.push(doc);
    else byLifecycle.set(doc.lifecycleId, [doc]);
  }
  return byLifecycle;
}

function taskHint(docs: TaskDocNode[]): string {
  if (docs.length > 1) return `series ${docs.length}`; // a multi-task series (subtask slices)
  if (docs.length === 1) return `${docs[0].stepsDone}/${docs[0].stepsTotal}`; // single task progress
  return "";
}
