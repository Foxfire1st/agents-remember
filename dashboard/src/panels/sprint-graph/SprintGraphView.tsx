import { memo } from "react";

import type {
  TaskExecutionGraphView,
  TaskExecutionNodeView,
} from "../../types/projection";
import {
  box,
  boxHead,
  boxTitle,
  frontier,
  graph,
  leafLine,
  leaves,
  lump,
  pred,
  preds,
  wave,
  waveGrid,
  waveHead,
} from "./styles";

// The sprint execution graph wave-grid view (L12-R2/R6). The backend projects a render-ready
// per-node model (executionGraphView) -- kind, master ref + title, leaf ids + titles, derived
// wave index, frontier state, execution nature, and predecessors with reasons -- so this
// component renders projected facts verbatim and never joins raw refs or re-derives waves.
//
// Layout: one row per derived wave; within a wave, boxes in a grid of at most 3 per row before
// wrapping. Each box shows the master title header and one ellipsized leaf line per leaf (the
// character range grows with the viewport); an atomic master renders as a lump box with no leaf
// list. Edges render as textual dependency labels under each box (the documented pure-CSS
// fallback). The narrow/phone layout collapses the grid to a single wave-ordered column while
// preserving box grouping and predecessor info.

function GraphBox({ node }: { node: TaskExecutionNodeView }) {
  return (
    <article
      className={box}
      data-testid="graph-box"
      data-node={node.nodeId}
      data-frontier={node.frontierState}
    >
      <header className={boxHead}>
        <span className={boxTitle} data-testid="graph-box-title">
          {node.masterTitle}
        </span>
        <span className={frontier({ state: node.frontierState })} data-testid="graph-frontier">
          {node.frontierState}
        </span>
      </header>
      {node.kind === "segment" ? (
        <ul className={leaves}>
          {node.leafTitles.map((title, index) => (
            <li key={node.leafIds[index] ?? index} className={leafLine} data-testid="graph-leaf">
              {node.leafIds[index]} — {title}
            </li>
          ))}
        </ul>
      ) : (
        <span className={lump} data-testid="graph-lump">
          atomic unit
        </span>
      )}
      {node.predecessors.length > 0 ? (
        <ul className={preds}>
          {node.predecessors.map((predecessor) => (
            <li
              key={`${predecessor.predecessorRef.repository}/${predecessor.predecessorRef.path}`}
              className={pred}
              data-testid="graph-predecessor"
            >
              ← {predecessor.predecessorTitle} — {predecessor.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

function SprintGraphViewImpl({ graphView }: { graphView: TaskExecutionGraphView }) {
  // Nodes arrive ordered by derived wave then node order (the server contract); group into
  // wave rows in that same order so the render is deterministic.
  const waves = new Map<number, TaskExecutionNodeView[]>();
  for (const node of graphView.nodes) {
    const row = waves.get(node.waveIndex) ?? [];
    row.push(node);
    waves.set(node.waveIndex, row);
  }
  const waveRows = [...waves.entries()].sort(([left], [right]) => left - right);
  return (
    <div className={graph} data-testid="sprint-graph">
      {waveRows.map(([waveIndex, nodes]) => (
        <section key={waveIndex} className={wave} data-testid={`graph-wave-${waveIndex}`}>
          <h4 className={waveHead}>Wave {waveIndex}</h4>
          <div className={waveGrid}>
            {nodes.map((node) => (
              <GraphBox key={node.nodeId} node={node} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export const SprintGraphView = memo(SprintGraphViewImpl);