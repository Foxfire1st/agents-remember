// MIK-R127 rules 3, 10 and 11: the Order block of a sprint. The masters that are not
// finished stand in one row per wave, one mark per unit of the projected execution graph;
// what has landed is a count, and the whole graph (every unit with its tasks and the
// reason of every edge) is one link away. Resting the pointer on a mark lights up what it
// waits for and what waits for it (rule 11: after a rest, with a fade; focus at once).
// The block draws projected facts only: the wave and the state of a unit are the server's.
import { useMemo } from 'react';

import { sameTaskDocumentRef, taskDocSelectionKey } from '../../data/taskIdentity';
import type {
  TaskDocNode,
  TaskDocumentRef,
  TaskExecutionGraphView,
  TaskExecutionNodeView,
} from '../../types/projection';
import {
  SprintGraphView,
  relatedGraphNodes,
  useReducedMotion,
  useRestHighlight,
} from '../sprint-graph/SprintGraphView';
import { masterTaskCounts } from './model';
import { Block, MoreLink, plural, type DocView } from './pageParts';
import {
  dim,
  orderCount,
  orderFlat,
  orderKey,
  orderLabel,
  orderList,
  orderNode,
  orderNodeId,
  orderNodeMeta,
  orderNodeTitle,
  orderNodes,
  orderNote,
  orderRead,
  orderRow,
} from './pageStyles';

type NodeTone = 'work' | 'ready' | 'waits';

const toneOf = (node: TaskExecutionNodeView): NodeTone => {
  if (node.frontierState === 'in-flight') return 'work';
  return node.frontierState === 'ready' ? 'ready' : 'waits';
};

const isOver = (node: TaskExecutionNodeView): boolean =>
  node.frontierState === 'landed' || node.frontierState === 'abandoned';

interface OrderRows {
  landed: number;
  abandoned: number;
  /** The units in work, then one row per wave of the units that wait. */
  rows: Array<{ key: string; label: string; now: boolean; nodes: TaskExecutionNodeView[] }>;
}

// The rows of the block. Within a wave the units that wait for nothing
// unfinished stand first; the order the server gave is kept otherwise.
function orderRows(nodes: TaskExecutionNodeView[]): OrderRows {
  const open = nodes.filter((node) => !isOver(node));
  const now = open.filter((node) => node.frontierState === 'in-flight');
  const waiting = open.filter((node) => node.frontierState !== 'in-flight');
  const waves = [...new Set(waiting.map((node) => node.waveIndex))].sort((a, b) => a - b);
  const rows = waves.map((wave) => {
    const inWave = waiting.filter((node) => node.waveIndex === wave);
    return {
      key: `wave-${wave}`,
      label: `wave ${wave}`,
      now: false,
      nodes: [
        ...inWave.filter((node) => node.frontierState === 'ready'),
        ...inWave.filter((node) => node.frontierState !== 'ready'),
      ],
    };
  });
  return {
    landed: nodes.filter((node) => node.frontierState === 'landed').length,
    abandoned: nodes.filter((node) => node.frontierState === 'abandoned').length,
    rows: now.length > 0 ? [{ key: 'now', label: 'now', now: true, nodes: now }, ...rows] : rows,
  };
}

// What a marked unit waits for and what waits for it, among the units that are not over.
function orderRelations(
  nodes: TaskExecutionNodeView[],
  nodeId: string,
): { title: string; waitsFor: string[]; neededBy: string[] } | undefined {
  const self = nodes.find((node) => node.nodeId === nodeId);
  if (!self) return undefined;
  const over = (ref: TaskDocumentRef): boolean => {
    const units = nodes.filter((node) => sameTaskDocumentRef(node.masterRef, ref));
    return units.length > 0 && units.every(isOver);
  };
  const waitsFor = self.predecessors
    .filter((edge) => !over(edge.predecessorRef))
    .map((edge) => edge.predecessorTitle);
  const neededBy = nodes
    .filter((node) => !isOver(node))
    .filter((node) =>
      node.predecessors.some((edge) => sameTaskDocumentRef(edge.predecessorRef, self.masterRef)),
    )
    .map((node) => node.masterTitle);
  return { title: self.masterTitle, waitsFor, neededBy: [...new Set(neededBy)] };
}

export interface OrderHandlers {
  allDocs: TaskDocNode[];
  docPathForRef?: (ref: TaskDocumentRef) => string | undefined;
  onJump: (id: string) => void;
}

function masterOf(node: TaskExecutionNodeView, handlers: OrderHandlers): TaskDocNode | undefined {
  const path = handlers.docPathForRef?.(node.masterRef);
  return path ? handlers.allDocs.find((doc) => doc.docPath === path) : undefined;
}

// Beside the name: how many tasks of a whole master are completed, or how many
// tasks this part of a master holds.
function nodeMeta(node: TaskExecutionNodeView, master: TaskDocNode | undefined): string {
  if (node.kind === 'segment') return plural(node.leafIds.length, 'task');
  const counts = master ? masterTaskCounts(master) : undefined;
  return counts && counts.total > 0 ? `${counts.done}/${counts.total}` : '';
}

type NodeMark = 'none' | 'self' | 'related' | 'other';

function OrderNode({
  node,
  mark,
  fade,
  handlers,
}: {
  node: TaskExecutionNodeView;
  mark: NodeMark;
  fade: boolean;
  handlers: OrderHandlers;
}) {
  const master = masterOf(node, handlers);
  const tone = toneOf(node);
  const meta = nodeMeta(node, master);
  return (
    <button
      type="button"
      className={orderNode({ state: tone, mark, fade })}
      data-testid="order-node"
      data-node={node.nodeId}
      data-state={node.frontierState}
      data-mark={mark}
      data-fade={fade ? 'true' : 'false'}
      title={`${node.masterTitle} — ${node.frontierState}`}
      // A unit whose master is not projected keeps its mark and its place; it opens nothing.
      aria-disabled={master ? undefined : true}
      onClick={() => master && handlers.onJump(taskDocSelectionKey(master.docPath))}
    >
      {master ? <span className={orderNodeId({ state: tone })}>{master.id}</span> : null}
      <span className={orderNodeTitle}>{node.masterTitle}</span>
      {meta ? <span className={orderNodeMeta({ state: tone })}>{meta}</span> : null}
    </button>
  );
}

function OrderRead({ nodes, shown }: { nodes: TaskExecutionNodeView[]; shown: string | null }) {
  const relations = shown ? orderRelations(nodes, shown) : undefined;
  if (!relations) {
    return (
      <div className={orderRead} data-testid="order-read">
        <span className={orderKey({ state: 'work' })}>in work</span>
        <span className={orderKey({ state: 'ready' })}>waits for nothing unfinished</span>
        <span className={orderKey({ state: 'waits' })}>waits</span>
        <span className={dim}>
          Rest the pointer on a master: what it waits for and what waits for it light up.
        </span>
      </div>
    );
  }
  return (
    <div className={orderRead} data-testid="order-read">
      <strong>{relations.title}</strong>
      <span>waits for: {relations.waitsFor.join(', ') || 'nothing unfinished'}</span>
      <span>needed by: {relations.neededBy.join(', ') || 'no open master'}</span>
    </div>
  );
}

const markOf = (related: Set<string> | null, shown: string | null, nodeId: string): NodeMark => {
  if (!related) return 'none';
  if (nodeId === shown) return 'self';
  return related.has(nodeId) ? 'related' : 'other';
};

const nodeUnder = (target: EventTarget | null): string | undefined =>
  (target as HTMLElement | null)?.closest?.<HTMLElement>('[data-node]')?.dataset.node;

function OrderRowsView({
  graphView,
  handlers,
}: {
  graphView: TaskExecutionGraphView;
  handlers: OrderHandlers;
}) {
  const { hot, rest, markNow } = useRestHighlight();
  const reducedMotion = useReducedMotion();
  const related = useMemo(
    () => (hot ? relatedGraphNodes(graphView.nodes, hot) : null),
    [graphView, hot],
  );
  const { rows } = orderRows(graphView.nodes);
  return (
    <div
      data-testid="order-rows"
      data-reduced-motion={reducedMotion ? 'true' : 'false'}
      onMouseOver={(event) => rest(nodeUnder(event.target) ?? null)}
      onMouseLeave={() => rest(null)}
      onFocus={(event) => {
        const id = nodeUnder(event.target);
        if (id) markNow(id);
      }}
      onBlur={(event) => {
        const to = nodeUnder(event.relatedTarget);
        if (!(to && event.currentTarget.contains(event.relatedTarget as Node))) rest(null);
      }}
    >
      {rows.map((row) => (
        <div key={row.key} className={orderRow({ now: row.now })} data-testid={`order-${row.key}`}>
          <span className={orderLabel({ now: row.now })}>
            <span>{row.label}</span>
            <span className={orderCount}>{row.nodes.length}</span>
          </span>
          <div className={orderNodes({ now: row.now })}>
            {row.nodes.map((node) => (
              <OrderNode
                key={node.nodeId}
                node={node}
                mark={markOf(related, hot, node.nodeId)}
                fade={!reducedMotion}
                handlers={handlers}
              />
            ))}
          </div>
        </div>
      ))}
      <OrderRead nodes={graphView.nodes} shown={hot} />
    </div>
  );
}

// How many edges the graph holds, for the line that says what the block is drawn from.
const edgeCount = (nodes: TaskExecutionNodeView[]): number =>
  nodes.reduce((sum, node) => sum + node.predecessors.length, 0);

export function OrderBlock({
  graphView,
  handlers,
  openView,
}: {
  graphView: TaskExecutionGraphView | undefined;
  handlers: OrderHandlers;
  openView: (view: DocView) => void;
}) {
  if (!graphView || graphView.nodes.length === 0) return null;
  const { landed, abandoned } = orderRows(graphView.nodes);
  return (
    <Block title="Order" block="order" count="masters not finished, by wave">
      <div className={orderList} data-testid="order-list">
        <div className={orderRow()} data-testid="order-done">
          <span className={orderLabel()}>
            <span>done</span>
            <span className={orderCount}>{landed}</span>
          </span>
          <div className={orderFlat}>
            <MoreLink onClick={() => openView({ kind: 'orderGraph' })} testid="order-graph-link">
              the whole graph
            </MoreLink>
            {abandoned > 0 ? <span className={dim}>{abandoned} abandoned</span> : null}
          </div>
        </div>
        <OrderRowsView graphView={graphView} handlers={handlers} />
      </div>
      <p className={orderNote} data-testid="order-note">
        Drawn from the execution graph of the sprint: {plural(graphView.nodes.length, 'unit')},{' '}
        {plural(edgeCount(graphView.nodes), 'edge')}. The whole graph names the tasks of every unit
        and the reason of every edge.
      </p>
    </Block>
  );
}

// The whole graph in a view of its own: every unit, landed ones too, with its
// tasks and the reason of each edge.
export function OrderGraphViewBody({ graphView }: { graphView: TaskExecutionGraphView }) {
  return <SprintGraphView graphView={graphView} />;
}
