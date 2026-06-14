import { useState } from "react";

import { buildTree, fmtWait, type Pivot } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";
import type { LifecycleProjection, TaskDocNode } from "../types/projection";

// The single unit list (note 01: the lifecycle is THE unit; note 06 IA). This merges the old
// session strip + operation tree into one place — a BY REPO | BY PHASE pivot over every lifecycle
// (fleeting + persistent), selectable, driving the centre detail — removing the Operations/Sessions
// duplication and freeing the middle. A task-progress hint surfaces the work each lifecycle carries.
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
  const groups = buildTree(Object.values(lifecycles), pivot);

  return (
    <section className="panel lclist" data-testid="lifecycle-list">
      <div className="lclist__head">
        <h2>Lifecycles · {Object.keys(lifecycles).length}</h2>
        <div className="tree__pivot" role="group" aria-label="pivot">
          {(["repo", "phase"] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={pivot === option}
              className={pivot === option ? "is-active" : ""}
              onClick={() => setPivot(option)}
            >
              BY {option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {groups.length === 0 ? (
        <p className="muted">No lifecycles.</p>
      ) : (
        <ul className="lclist__groups">
          {groups.map((group) => (
            <li key={group.key}>
              <div className="lclist__group">{group.label}</div>
              <ul className="lclist__rows">
                {group.lifecycles.map((lifecycle) => (
                  <li key={lifecycle.id}>
                    <Row
                      lifecycle={lifecycle}
                      pivot={pivot}
                      selected={lifecycle.id === selectedId}
                      docs={docsByLifecycle.get(lifecycle.id) ?? []}
                      onSelect={onSelect}
                    />
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
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

function Row({
  lifecycle,
  pivot,
  selected,
  docs,
  onSelect,
}: {
  lifecycle: LifecycleProjection;
  pivot: Pivot;
  selected: boolean;
  docs: TaskDocNode[];
  onSelect: (id: string) => void;
}) {
  const label = lifecycle.id.includes("/") ? lifecycle.id.slice(lifecycle.id.indexOf("/") + 1) : lifecycle.id;
  const secondary = pivot === "repo" ? lifecycle.phase : (lifecycle.repoId ?? "—");
  const hint = taskHint(docs);
  return (
    <button
      type="button"
      className={[
        "lclist__row",
        lifecycle.fleeting ? "lclist__row--fleeting" : "",
        selected ? "is-selected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={() => onSelect(lifecycle.id)}
      title={lifecycle.id}
    >
      <Dot variant={lifecycle.state} />
      <span className="lclist__id">{label}</span>
      <span className="lclist__sec">{secondary}</span>
      <span className="lclist__meta">
        {hint ? `${hint} · ` : ""}
        {fmtWait(lifecycle.staleSeconds)}
        {lifecycle.inferred ? " · inf" : ""}
      </span>
    </button>
  );
}
