// MIK-R127 rules 3, 10 and 11: the Order block of a sprint. The masters that are not
// finished stand in one row per wave, one mark per unit of the projected execution graph;
// the whole graph (every unit with its tasks and the
// reason of every edge) is one link away. Resting the pointer on a mark lights up what it
// waits for and what waits for it (rule 11: after a rest, with a fade; focus at once).
// Dependency waves/readiness come from the graph; task words and activity come from entries.
import { useMemo } from 'react';

import { sameTaskDocumentRef } from '../../data/taskIdentity';
import { taskEntrySelectionKey,taskReferenceKey } from '../../data/taskReference';
import type {
TaskDocumentRef,
TaskEntry,
TaskExecutionGraphView,
TaskExecutionNodeView,
} from '../../types/projection';
import {
SprintGraphView,
relatedGraphNodes,
useReducedMotion,
useRestHighlight,
} from '../sprint-graph/SprintGraphView';
import { liveChildCount } from './indexRows';
import { Block,MoreLink,StatusWord,SubHead,plural,statusLabel,taskStatusKey,type DocView } from './pageParts';
import {
dim,
orderCount,
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

const isOver = (node: TaskExecutionNodeView): boolean =>
  node.frontierState === 'landed' || node.frontierState === 'abandoned';

interface OrderRows {
  /** Entries working now, then every other unfinished unit once in its dependency wave. */
  rows: Array<{ key: string; label: string; now: boolean; nodes: TaskExecutionNodeView[] }>;
}

// The rows of the block. Within a wave the units that wait for nothing
// unfinished stand first; the order the server gave is kept otherwise.
function orderRows(nodes: TaskExecutionNodeView[], handlers: OrderHandlers): OrderRows {
  const open = nodes.filter((node) => {
    const task = masterOf(node, handlers);
    return task?.stage !== 'completed' && task?.stage !== 'aborted';
  });
  const working = (node: TaskExecutionNodeView): boolean => masterOf(node, handlers)?.activity === 'works';
  const now = open.filter(working);
  const waiting = open.filter((node) => !working(node));
  const waves = [...new Set(waiting.map((node) => node.waveIndex))].sort((a, b) => a - b);
  const rows = waves.map((wave) => {
    const inWave = waiting.filter((node) => node.waveIndex === wave);
    const cycleNames = nodes
      .filter((node) => node.cycle && node.waveIndex === wave)
      .map((node) => node.masterTitle);
    return {
      key: `wave-${wave}`,
      label: inWave.some((node) => node.cycle)
        ? `wave ${wave} · cycle: ${cycleNames.join(', ')}`
        : `wave ${wave}`,
      now: false,
      nodes: [
        ...inWave.filter((node) => node.frontierState === 'ready'),
        ...inWave.filter((node) => node.frontierState !== 'ready'),
      ],
    };
  });
  return {
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
  tasks: Record<string, TaskEntry>;
  onJump: (id: string) => void;
}

function masterOf(node: TaskExecutionNodeView, handlers: OrderHandlers): TaskEntry | undefined {
  return handlers.tasks[taskReferenceKey(node.masterRef)];
}

function nodeMeta(node: TaskExecutionNodeView, master: TaskEntry | undefined): string {
  if (master?.summary?.form === 'task') return `${master.stepsDone}/${master.stepsTotal}`;
  if (node.kind === 'segment') return plural(node.leafIds.length, 'task');
  const total = master ? liveChildCount(master) : 0;
  return master && total > 0 ? `${master.counts.completed}/${total}` : '';
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
  const status = master ? taskStatusKey(master) : undefined;
  const meta = nodeMeta(node, master);
  return (
    <button
      type="button"
      className={orderNode({ ready: node.frontierState === 'ready', mark, fade })}
      data-testid="order-node"
      data-node={node.nodeId}
      data-readiness={node.frontierState}
      data-mark={mark}
      data-fade={fade ? 'true' : 'false'}
      title={`${node.masterTitle} — ${status ? statusLabel(status) : 'task unavailable'}`}
      // A unit whose master is not projected keeps its mark and its place; it opens nothing.
      aria-disabled={master ? undefined : true}
      onClick={() => master && handlers.onJump(taskEntrySelectionKey(master.key))}
    >
      {master ? <span className={orderNodeId}>{master.id}</span> : null}
      <span className={orderNodeTitle}>{node.masterTitle}</span>
      {status ? <StatusWord status={status} /> : null}
      {meta ? <span className={orderNodeMeta}>{meta}</span> : null}
    </button>
  );
}

function OrderRead({
  nodes,
  shown,
  unit,
}: {
  nodes: TaskExecutionNodeView[];
  shown: string | null;
  unit: 'task' | 'master';
}) {
  const relations = shown ? orderRelations(nodes, shown) : undefined;
  if (!relations) {
    return (
      <div className={orderRead} data-testid="order-read">
        <span className={orderKey}>ready: waits for nothing unfinished</span>
        <span className={dim}>
          Rest the pointer on a {unit}: what it waits for and what waits for it light up.
        </span>
      </div>
    );
  }
  return (
    <div className={orderRead} data-testid="order-read">
      <strong>{relations.title}</strong>
      <span>waits for: {relations.waitsFor.join(', ') || 'nothing unfinished'}</span>
      <span>needed by: {relations.neededBy.join(', ') || `no open ${unit}`}</span>
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
  unit,
}: {
  graphView: TaskExecutionGraphView;
  handlers: OrderHandlers;
  unit: 'task' | 'master';
}) {
  const { hot, rest, markNow } = useRestHighlight();
  const reducedMotion = useReducedMotion();
  const related = useMemo(
    () => (hot ? relatedGraphNodes(graphView.nodes, hot) : null),
    [graphView, hot],
  );
  const { rows } = orderRows(graphView.nodes, handlers);
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
      <OrderRead nodes={graphView.nodes} shown={hot} unit={unit} />
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
  form = 'sprint',
}: {
  graphView: TaskExecutionGraphView | undefined;
  handlers: OrderHandlers;
  openView: (view: DocView) => void;
  form?: 'sprint' | 'master';
}) {
  if (!graphView || graphView.nodes.length === 0) return null;
  return (
    <Block
      title="Order"
      block="order"
      count={`${form === 'master' ? 'tasks' : 'masters'} not finished, by wave`}
    >
      <div className={orderList} data-testid="order-list">
        <MoreLink onClick={() => openView({ kind: 'orderGraph' })} testid="order-graph-link">
          the whole graph
        </MoreLink>
        <OrderRowsView
          graphView={graphView}
          handlers={handlers}
          unit={form === 'master' ? 'task' : 'master'}
        />
      </div>
      <p className={orderNote} data-testid="order-note">
        Drawn from{' '}
        {form === 'master' ? 'the tasks’ waitsFor fields' : 'the execution graph of the sprint'}:{' '}
        {plural(graphView.nodes.length, 'unit')}, {plural(edgeCount(graphView.nodes), 'edge')}. The
        whole graph names the tasks of every unit and the reason of every edge.
      </p>
    </Block>
  );
}

export function CanStartNext({
  graphView,
  handlers,
}: {
  graphView: TaskExecutionGraphView | undefined;
  handlers: OrderHandlers;
}) {
  const ready = graphView?.nodes.filter((node) =>
    node.frontierState === 'ready' && masterOf(node, handlers)?.stage === 'planned') ?? [];
  if (ready.length === 0) return null;
  return (
    <div data-testid="can-start-next">
      <SubHead title="Can start next" count={ready.length} />
      <div className={orderNodes({ now: false })}>
        {ready.map((node) => (
          <OrderNode key={node.nodeId} node={node} mark="none" fade={false} handlers={handlers} />
        ))}
      </div>
    </div>
  );
}

// The whole graph in a view of its own: every unit, landed ones too, with its
// tasks and the reason of each edge.
export function OrderGraphViewBody({ graphView }: { graphView: TaskExecutionGraphView }) {
  return <SprintGraphView graphView={graphView} />;
}
