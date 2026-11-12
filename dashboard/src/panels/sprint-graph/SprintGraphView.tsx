import { memo, useEffect, useMemo, useRef, useState } from 'react';

import { cx } from '../../../styled-system/css';
import { useDashboard } from '../../data/store';
import { taskReferenceKey } from '../../data/taskReference';
import { limitUntil, StatusWord, statusTitle, taskStatusKey, type TaskStatusKey } from '../detail-panel/pageParts';
import { sameTaskDocumentRef } from '../../data/taskIdentity';
import { Markdown } from '../../grammar/Markdown';
import type {
  ProviderLimit,
  TaskDocumentRef,
  TaskEntry,
  TaskExecutionGraphView,
  TaskExecutionNodeView,
} from '../../types/projection';
import {
  box,
  boxHead,
  boxTitle,
  dependency,
  graph,
  leafLine,
  leaves,
  lump,
  nodeDim,
  nodeFade,
  nodeHot,
  nodeMarked,
  pred,
  preds,
  wave,
  waveGrid,
  waveHead,
} from './styles';

// The sprint execution graph wave-grid view (L12-R2/R6). The backend projects a render-ready
// per-node model (executionGraphView) -- kind, master ref + title, leaf ids + titles, derived
// wave index, frontier state, execution nature, and predecessors with reasons -- so this
// component keeps those dependency facts and waves. Task state words and tones come from
// the master's entry through the shared status presentation.
//
// Layout: one row per derived wave; within a wave, boxes in a grid of at most 3 per row before
// wrapping. Each box shows the master title header and one ellipsized leaf line per leaf (the
// character range grows with the viewport); an atomic master renders as a lump box with no leaf
// list. Edges render as textual dependency labels under each box (the documented pure-CSS
// fallback). The narrow/phone layout collapses the grid to a single wave-ordered column while
// preserving box grouping and predecessor info.
//
// MIK-R127 rule 11: where pointing at an element marks other elements, the mark appears only
// when the pointer has rested on the element for a brief moment (GRAPH_REST_MS), and fades in
// and out briefly. A pointer that passes over changes nothing, and going from one element to
// its neighbour does not blank the picture in between. Focus by the keyboard marks at once,
// and Enter/Space pins the mark so it survives blur; the same key unpins. Where the reader
// asks for reduced motion the fade is dropped and the rest stays.
export const GRAPH_REST_MS = 180;

function refsEqual(left: TaskDocumentRef, right: TaskDocumentRef): boolean {
  return sameTaskDocumentRef(left, right);
}

type NodeIndex = Map<string, TaskExecutionNodeView>;

function indexNodes(nodes: TaskExecutionNodeView[]): NodeIndex {
  const byId = new Map<string, TaskExecutionNodeView>();
  for (const node of nodes) byId.set(node.nodeId, node);
  return byId;
}

// Predecessor masters of one marked set, by node id.
function predecessorNodeIds(byId: NodeIndex, seeds: Set<string>): Set<string> {
  const found = new Set<string>();
  for (const id of seeds) {
    const node = byId.get(id);
    if (!node) continue;
    for (const predecessor of node.predecessors) {
      for (const candidate of byId.values()) {
        if (refsEqual(candidate.masterRef, predecessor.predecessorRef)) {
          found.add(candidate.nodeId);
        }
      }
    }
  }
  return found;
}

// Nodes with an edge onto one master's nodes: what waits on the marked set.
function dependentNodeIds(byId: NodeIndex, master: TaskDocumentRef, out: Set<string>): void {
  for (const node of byId.values()) {
    if (out.has(node.nodeId)) continue;
    for (const predecessor of node.predecessors) {
      if (refsEqual(master, predecessor.predecessorRef)) {
        out.add(node.nodeId);
        break;
      }
    }
  }
}

// The marked set for one rested node: the node itself, every node of the same
// master, every predecessor of those, and every node waiting on them.
export function relatedGraphNodes(nodes: TaskExecutionNodeView[], nodeId: string): Set<string> {
  const byId = indexNodes(nodes);
  const self = byId.get(nodeId);
  if (!self) return new Set();
  const out = new Set<string>();
  for (const node of nodes) {
    if (refsEqual(node.masterRef, self.masterRef)) out.add(node.nodeId);
  }
  for (const id of predecessorNodeIds(byId, out)) out.add(id);
  dependentNodeIds(byId, self.masterRef, out);
  return out;
}

function GraphLeaves({ node }: { node: TaskExecutionNodeView }) {
  if (node.kind !== 'segment') {
    return (
      <span className={lump} data-testid="graph-lump">
        atomic unit
      </span>
    );
  }
  return (
    <ul className={leaves}>
      {node.leafTitles.map((title, index) => (
        <li key={node.leafIds[index] ?? index} className={leafLine} data-testid="graph-leaf">
          {node.leafIds[index]} — {title}
        </li>
      ))}
    </ul>
  );
}

function GraphPredecessors({ node }: { node: TaskExecutionNodeView }) {
  if (node.predecessors.length === 0) return null;
  return (
    <ul className={preds}>
      {node.predecessors.map((predecessor) => (
        <li
          key={`${predecessor.predecessorRef.repository}/${predecessor.predecessorRef.path}`}
          className={pred}
          data-testid="graph-predecessor"
        >
          ← {predecessor.predecessorTitle} — <Markdown inline>{predecessor.reason}</Markdown>
        </li>
      ))}
    </ul>
  );
}

function readinessOf(node: TaskExecutionNodeView) {
  if (node.cycle) return 'cycle';
  if (node.frontierState === 'ready' || node.frontierState === 'waiting') return node.frontierState;
  return undefined;
}

/** The master's word, its served end and the served limit itself, for the box and its name. */
function boxStatus(task: TaskEntry | undefined): {
  status: TaskStatusKey | undefined;
  until: string | undefined;
  limit: ProviderLimit | null | undefined;
} {
  if (!task) return { status: undefined, until: undefined, limit: undefined };
  return { status: taskStatusKey(task), until: limitUntil(task), limit: task.providerLimit };
}

function GraphBox({
  node,
  marked,
  dimmed,
  faded,
  pressed,
  onToggle,
}: {
  node: TaskExecutionNodeView;
  marked: boolean;
  dimmed: boolean;
  faded: boolean;
  pressed: boolean;
  onToggle: () => void;
}) {
  const task = useDashboard((state) => state.tasks[taskReferenceKey(node.masterRef)]);
  const { status, until, limit } = boxStatus(task);
  const readiness = readinessOf(node);
  return (
    <div
      className={cx(box, faded && nodeFade, marked && cx(nodeMarked, nodeHot), dimmed && nodeDim)}
      data-testid="graph-box"
      data-node={node.nodeId}
      data-readiness={readiness}
      data-marked={marked ? 'true' : 'false'}
      data-fade={faded ? 'true' : 'false'}
      role="button"
      aria-pressed={pressed}
      aria-label={[
        node.masterTitle,
        status ? statusTitle(status, until) : 'task unavailable',
      ].join(', ')}
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onToggle();
        }
      }}
    >
      <header className={boxHead}>
        <span className={boxTitle} data-testid="graph-box-title">
          {node.masterTitle}
        </span>
        {status && (
          <StatusWord status={status} testid="graph-task-status" until={until} limit={limit} />
        )}
        {readiness && (
          <span className={dependency({ state: readiness })} data-testid="graph-readiness">
            {readiness}
          </span>
        )}
      </header>
      <GraphLeaves node={node} />
      <GraphPredecessors node={node} />
    </div>
  );
}

// The rest timer: a highlight is due only after the rest. Between nodes
// nothing is due and what is shown stays, so moving to a neighbour never
// blanks the picture.
export function useRestHighlight(): {
  hot: string | null;
  rest: (id: string | null) => void;
  markNow: (id: string | null) => void;
} {
  const [hot, setHot] = useState<string | null>(null);
  const restTimer = useRef<ReturnType<typeof setTimeout> | 0>(0);
  const planned = useRef<string | null | undefined>(undefined);
  useEffect(
    () => () => {
      if (restTimer.current) clearTimeout(restTimer.current);
    },
    [],
  );
  const drop = (): void => {
    if (restTimer.current) clearTimeout(restTimer.current);
    restTimer.current = 0;
    planned.current = undefined;
  };
  const rest = (id: string | null): void => {
    if (restTimer.current && planned.current === id) return;
    drop();
    if (id === hot) return;
    planned.current = id;
    restTimer.current = setTimeout(() => {
      restTimer.current = 0;
      planned.current = undefined;
      setHot(id);
    }, GRAPH_REST_MS);
  };
  const markNow = (id: string | null): void => {
    drop();
    setHot(id);
  };
  return { hot, rest, markNow };
}

export function useReducedMotion(): boolean {
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(query.matches);
    const onChange = (event: MediaQueryListEvent): void => setReducedMotion(event.matches);
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, []);
  return reducedMotion;
}

// Nodes arrive ordered by derived wave then node order (the server contract); group into
// wave rows in that same order so the render is deterministic.
function waveRowsOf(graphView: TaskExecutionGraphView): Array<[number, TaskExecutionNodeView[]]> {
  const waves = new Map<number, TaskExecutionNodeView[]>();
  for (const node of graphView.nodes) {
    const row = waves.get(node.waveIndex) ?? [];
    row.push(node);
    waves.set(node.waveIndex, row);
  }
  return [...waves.entries()].sort(([left], [right]) => left - right);
}

function nodeOf(target: EventTarget | null): HTMLElement | null {
  const element = target as HTMLElement | null;
  return element?.closest?.('[data-node]') ?? null;
}

function SprintGraphViewImpl({ graphView }: { graphView: TaskExecutionGraphView }) {
  const { hot, rest, markNow } = useRestHighlight();
  const reducedMotion = useReducedMotion();
  const [pinned, setPinned] = useState<string | null>(null);
  const shown = hot ?? pinned;
  const related = useMemo(
    () => (shown ? relatedGraphNodes(graphView.nodes, shown) : null),
    [graphView, shown],
  );
  const waveRows = waveRowsOf(graphView);
  const togglePin = (id: string): void => {
    setPinned((current) => (current === id ? null : id));
  };
  return (
    <div
      className={graph}
      data-testid="sprint-graph"
      data-reduced-motion={reducedMotion ? 'true' : 'false'}
      onMouseOver={(event) => {
        const element = nodeOf(event.target);
        if (element?.dataset.node) rest(element.dataset.node);
        else rest(null);
      }}
      onMouseLeave={() => rest(null)}
      onFocus={(event) => {
        const element = nodeOf(event.target);
        if (element?.dataset.node) markNow(element.dataset.node);
      }}
      onBlur={(event) => {
        const to = nodeOf(event.relatedTarget);
        if (!(to && event.currentTarget.contains(to))) rest(null);
      }}
    >
      {waveRows.map(([waveIndex, nodes]) => (
        <section key={waveIndex} className={wave} data-testid={`graph-wave-${waveIndex}`}>
          <h4 className={waveHead}>Wave {waveIndex}</h4>
          <div className={waveGrid}>
            {nodes.map((node) => (
              <GraphBox
                key={node.nodeId}
                node={node}
                marked={related?.has(node.nodeId) ?? false}
                dimmed={related !== null && !related.has(node.nodeId)}
                faded={!reducedMotion}
                pressed={pinned === node.nodeId}
                onToggle={() => togglePin(node.nodeId)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export const SprintGraphView = memo(SprintGraphViewImpl);
