import { useState } from "react";

import { buildTree, type Pivot } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";

// The two-axis operation tree (note 06): a BY REPO | BY LIFECYCLE pivot over the flat
// lifecycle collection. Selecting a leaf drives the detail panel (and is the target of the
// attention queue's "Open"), realising the queue↔tree coupling from the Open Design loop.
export function OperationTree({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [pivot, setPivot] = useState<Pivot>("repo");
  const lifecycles = useDashboard((s) => s.lifecycles);
  const groups = buildTree(Object.values(lifecycles), pivot);
  return (
    <section className="panel tree" data-testid="operation-tree">
      <div className="tree__head">
        <h2>Operations</h2>
        <div className="tree__pivot" role="group" aria-label="pivot">
          {(["repo", "lifecycle"] as const).map((option) => (
            <button
              key={option}
              type="button"
              className={pivot === option ? "is-active" : ""}
              onClick={() => setPivot(option)}
            >
              BY {option.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      <ul className="tree__groups">
        {groups.map((group) => (
          <li key={group.key}>
            <div className="tree__group">{group.label}</div>
            <ul className="tree__leaves">
              {group.lifecycles.map((lifecycle) => (
                <li key={lifecycle.id}>
                  <button
                    type="button"
                    className={lifecycle.id === selectedId ? "is-selected" : ""}
                    onClick={() => onSelect(lifecycle.id)}
                  >
                    <Dot variant={lifecycle.state} />
                    {pivot === "repo" ? lifecycle.id : (lifecycle.repoId ?? "—")} · {lifecycle.phase}
                  </button>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}
