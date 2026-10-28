/**
 * The Explanation view (MIK-R106 v2 rule 6; MIK-R104 v3; MIK-R103 v2).
 *
 * It is the explainer's end-to-end page for the bound comparison, shown in the review page's reading
 * column behind the top line's Review | Explanation switch. The top line, the tree and the chat stay
 * outside it: this component draws neither, and it mounts no chat.
 *
 * WHAT IT SHOWS, in this order:
 *   * the revision selector: each retained revision with its binding, time and kind, the agreed one
 *     marked, Compare (two isolated frames) and, at the task total, the removal control;
 *   * the page itself, in MIK-R104 v3's isolated host, stored by MIK-R103 v2.
 *
 * WHAT IT DOES NOT SHOW: staleness, and the moved notice. Both are the top line's (F18): the badge
 * beside the switch carries the count, the stale parts and their reasons, the newer comparison and
 * the one refresh control that regenerates. A revision column draws its record and nothing else.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useState, type RefObject } from 'react';

import { css } from '../../../../styled-system/css';
import { buildFailure as buildFailureOf } from '../../../explainer/model';
import { askFirstRequest, clearFirstRequest, onFirstRequest } from '../../../explainer/request';
import { sameExplainerAddress } from '../../../explainer/client';
import { trayStore } from '../../../tray/trayStore';
import type { EvidenceDestination } from '../canvas/canvasApi';
import type { ChangeSetModel } from '../changeSetOrder';
import { blockElement } from '../reading/blockDom';
import { ExplanationRequest } from './ExplanationRequest';
import { RevisionFrame } from './RevisionFrame';
import { RevisionSelector, type ComparePair } from './RevisionSelector';
import {
  readExplainerState,
  type BindingOwner,
  type ExplainerRevision,
  type ExplainerState,
  type ExplainerTarget,
  type RefusalInfo,
} from './explainerApi';
import { limitRefusal, useExplainer, type ExplainerRead } from './explainerRead';
import {
  bindingLabel,
  clockOf,
  defaultComparePair,
  coldRegenerateItems,
  defaultShownRevision,
  kindLabel,
  orderRevisions,
  phaseOf,
  staleGradesOf,
  staleParts,
  staleRegenerationOf,
  standfirstText,
  type ExplanationPhase,
} from './explainerModel';

const view = css({ display: 'grid', gap: '0.5rem', minWidth: 0, paddingBottom: '1rem' });
const card = css({
  display: 'grid',
  gap: '0.5rem',
  justifyItems: 'start',
  padding: '1.1rem 1.2rem',
  borderWidth: '1px',
  borderStyle: 'dashed',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
});
const title = css({ fontSize: '1rem', margin: 0 });
const bodyText = css({ fontSize: '0.82rem', color: 'muted', margin: 0, maxWidth: '46rem' });
const primary = css({
  fontFamily: 'mono',
  fontSize: '0.8rem',
  padding: '0.35rem 0.7rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'purple',
  borderRadius: '3px',
  color: 'purple',
  background: 'transparent',
  cursor: 'pointer',
});
const quiet = css({
  fontFamily: 'mono',
  fontSize: '0.72rem',
  padding: '0.2rem 0.5rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
});
const standfirst = css({ fontFamily: 'mono', fontSize: '0.72rem', color: 'muted', margin: 0 });
// In the frame's single header row the same sentence is one clipped line: it must never wrap and push
// the canvas down (F5 c).
const standfirstInline = css({
  fontFamily: 'mono',
  fontSize: '0.68rem',
  color: 'muted',
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
});
const who = css({ color: 'purple' });
const skeleton = css({ display: 'grid', gap: '0.4rem', width: '100%' });
const bone = css({ height: '0.8rem', borderRadius: '2px', background: 'grid' });
const frames = css({ display: 'grid', gap: '0.5rem', gridTemplateColumns: '1fr 1fr', minWidth: 0 });
const compareCol = css({ display: 'grid', gap: '0.45rem', minWidth: 0, alignContent: 'start' });
const alert = css({ fontSize: '0.78rem', color: 'dormant', margin: 0 });

export interface ExplanationViewProps {
  target: ExplainerTarget;
  /** The comparison on screen: an Update names it (MIK-R185 v2), and the view never rebinds silently. */
  comparison?: { reference: string; number?: number; owner?: BindingOwner | string };
  /** "✦ Explain the change set": opens the chat's first-request form. It starts nothing here. */
  onExplain: () => void;
  /** Where a frame's own evidence operation goes; the page's selection owner wires this. */
  onEvidence?: (destination: EvidenceDestination) => void;
  /** The leaf's short name, for the empty state's one sentence. */
  taskLabel?: string;
  /** A read the page's owner already holds, so the switch and this view read one answer between them. */
  read?: ExplainerRead;
  /** The page's provenance disclosure shares the frame's compact header. */
  headerDetails?: React.ReactNode;
}

interface Model {
  read: ExplainerRead;
  activeState: ExplainerState | null;
  compareStates: Readonly<Record<number, ExplainerState>>;
  rows: ExplainerRevision[];
  phase: ExplanationPhase;
  shown: number | null;
  compared: ComparePair | null;
  refusal: RefusalInfo | null;
  regenProblem: string | null;
  chosen: number | null;
  /** The developer's ✦ Explain in this view: the request's form is open here, where he pressed. */
  requestOpen: boolean;
  choose: (revision: number | null) => void;
  compare: (pair: ComparePair | null) => void;
  pressRegenerate: (forState?: ExplainerState | null, forRevision?: number | null) => void;
  openRequest: () => void;
  cancelRequest: () => void;
}

const phaseOfRead = (read: ExplainerRead): ExplanationPhase =>
  read.problem ? 'unavailable' : phaseOf(read.state);

const refusalOf = (read: ExplainerRead): RefusalInfo | null =>
  read.refusal ?? limitRefusal(read.state);

const pickItems = <T,>(overrideList: T[] | undefined, rowList: T[] | undefined, fallback: T[]): T[] =>
  overrideList?.length ? overrideList : (rowList ?? fallback);

function mergeRevisionIntoState(
  base: ExplainerState | null,
  row: ExplainerRevision | undefined,
  override?: ExplainerState | null,
): ExplainerState | null {
  const source = override ?? base;
  if (!source || !row) return source;
  return {
    ...source,
    parts: pickItems(override?.parts, row.parts, source.parts),
    claims: pickItems(override?.claims, row.claims, source.claims),
    stale: override?.stale ?? row.stale ?? source.stale,
  };
}

function useRevisionStates(
  props: ExplanationViewProps,
  chosen: number | null,
  compared: ComparePair | null,
): Readonly<Record<number, ExplainerState>> {
  const [revisionStates, setRevisionStates] = useState<Record<number, ExplainerState>>({});
  const { repository, master, leaf } = props.target;
  const compRef = props.comparison?.reference;
  const compNum = props.comparison?.number;
  const compOwner = props.comparison?.owner;

  useEffect(() => {
    const needed = new Set<number>();
    if (props.read !== undefined && chosen !== null) needed.add(chosen);
    if (compared) {
      needed.add(compared.left);
      needed.add(compared.right);
    }
    if (needed.size === 0) return undefined;
    let live = true;
    for (const rev of needed) {
      void readExplainerState(
        { repository, master, leaf },
        { revision: rev, comparison: compRef, comparisonNumber: compNum, comparisonOwner: compOwner },
      )
        .then((fetched) => {
          if (live) setRevisionStates((prev) => ({ ...prev, [rev]: fetched }));
        })
        .catch(() => undefined);
    }
    return () => {
      live = false;
    };
  }, [props.read, chosen, compared, repository, master, leaf, compRef, compNum, compOwner]);

  return revisionStates;
}

function useRegenerateHandler(
  activeState: ExplainerState | null,
  shown: number | null,
  read: ExplainerRead,
  props: ExplanationViewProps,
  setRegenProblem: (msg: string | null) => void,
) {
  const { repository, master, leaf } = props.target;
  return useCallback(
    async (forState?: ExplainerState | null, forRevision?: number | null) => {
      const targetState = forState ?? activeState;
      const targetRev = forRevision ?? shown;
      const stale = staleParts(targetState);
      const staleGrades = staleGradesOf(targetState);
      const regeneration = staleRegenerationOf(targetState, targetRev);
      if (!regeneration) return;
      setRegenProblem(null);

      if (targetState?.seat.state === 'live') {
        const outcome = await read.regenerate({
          message: regeneration.message,
          stalePartIds: stale.parts.map((p) => p.id),
          staleGrades,
        });
        if (outcome.kind === 'refused') setRegenProblem(`${outcome.reason}: ${outcome.detail}`);
        return;
      }

      askFirstRequest({ repository, master, leaf }, coldRegenerateItems(targetState));
      trayStore.getState().setText(regeneration.message);
      props.onExplain();
    },
    [activeState, shown, read, repository, master, leaf, props, setRegenProblem],
  );
}

function useExplanationView(props: ExplanationViewProps): Model {
  const [chosen, setChosen] = useState<number | null>(null);
  const [compared, setCompared] = useState<ComparePair | null>(null);
  const [regenProblem, setRegenProblem] = useState<string | null>(null);
  const [requestOpen, setRequestOpen] = useState(false);
  const { repository, master, leaf } = props.target;
  const address = useMemo(
    () => ({ repository, master, leaf }),
    [repository, master, leaf],
  );

  // Handed the surface's read, this view's own read is off and stays off: the surface's read follows
  // the leaf's publications, so a second forced read here would ask the same answer twice.
  const own = useExplainer(props.target, props.comparison, props.read === undefined, chosen, {
    watchWhileDisabled: false,
  });
  const read = props.read ?? own;
  const rows = useMemo<ExplainerRevision[]>(
    () => orderRevisions(read.revisions?.revisions ?? []),
    [read.revisions],
  );
  const shown = chosen ?? defaultShownRevision(rows, read.state);
  const revisionStates = useRevisionStates(props, chosen, compared);

  // A press of ✦ Explain for this leaf asks for the first request; the ask opens this view's own
  // form, so the request is where he pressed it and not only in the chat column.
  useEffect(
    () =>
      onFirstRequest((body) => {
        if (sameExplainerAddress(body.address, address)) setRequestOpen(true);
      }),
    [address],
  );
  // A revision is the answer the request was waiting for: the page takes the form's place.
  useEffect(() => {
    if (rows.length > 0) setRequestOpen(false);
  }, [rows.length]);

  const openRequest = useCallback(() => {
    setRequestOpen(true);
    props.onExplain();
  }, [props]);
  const cancelRequest = useCallback(() => {
    setRequestOpen(false);
    clearFirstRequest(address);
  }, [address]);

  const activeRow = useMemo(() => rows.find((r) => r.revision === shown), [rows, shown]);
  const activeState = useMemo(
    () =>
      mergeRevisionIntoState(
        read.state,
        activeRow,
        shown !== null ? revisionStates[shown] : undefined,
      ),
    [read.state, activeRow, shown, revisionStates],
  );

  const pressRegenerate = useRegenerateHandler(activeState, shown, read, props, setRegenProblem);

  return {
    read,
    activeState,
    compareStates: revisionStates,
    rows,
    phase: phaseOfRead(read),
    shown,
    compared,
    refusal: refusalOf(read),
    regenProblem,
    chosen,
    requestOpen,
    choose: setChosen,
    compare: setCompared,
    pressRegenerate: (forState, forRevision) => void pressRegenerate(forState, forRevision),
    openRequest,
    cancelRequest,
  };
}

export function ExplanationView(props: ExplanationViewProps) {
  const model = useExplanationView(props);
  return (
    <section className={view} data-testid="explanation-view" data-phase={model.phase}>
      <ExplanationBody model={model} props={props} />
    </section>
  );
}

/**
 * Whether a revision exists to be stale against. Before one does, the host answers that staleness
 * cannot be read ("the leaf has no retained revision"), and there is nothing to say yet.
 */
const hasRevisionOf = (model: Model): boolean => model.rows.length > 0 || model.shown !== null;

/** The build the state answer holds, when it stopped at a failed step: the server's own words. */
const failedBuildOf = (state: ExplainerState | null | undefined) =>
  state?.build ? buildFailureOf(state.build) : null;

const revisionMenu = css({
  flex: '0 0 auto',
  '& > summary': { cursor: 'pointer', padding: '0 0.3rem', whiteSpace: 'nowrap' },
});
const revisionPanel = css({
  position: 'absolute', top: '100%', insetInline: 0, zIndex: 10,
  maxHeight: '70vh', overflow: 'auto', whiteSpace: 'normal', background: 'bgPanel',
});

function RevisionControls({ model }: { model: Model }) {
  return <details className={revisionMenu} data-testid="revision-controls">
    <summary>Revisions {model.rows.length}</summary>
    <div className={revisionPanel}>
        <RevisionSelector
          rows={model.rows}
          pinned={model.read.revisions?.pinned ?? null}
          shown={model.shown}
          compared={model.compared}
          refusal={model.refusal}
          busy={model.read.busy !== 'idle'}
          onShow={(revision) => {
            model.choose(revision);
            model.compare(null);
          }}
          onCompare={() => model.compare(defaultComparePair(model.rows, model.shown))}
          onStopCompare={() => model.compare(null)}
          onRemove={(numbers) => void model.read.remove(numbers)}
        />

    </div>
  </details>;
}

/**
 * Which body a view without a revision draws. A build the server is running is drawn with its own
 * steps and counts here, not sent away to the chat; the request is drawn where ✦ Explain was pressed,
 * with the server's own sentence when it stopped (MIK-R211 rules 3 and 4).
 */
function noRevisionKind(model: Model): 'request' | 'placeholder' | 'empty' {
  if (model.requestOpen || failedBuildOf(model.activeState)) return 'request';
  if (model.phase !== 'building') return 'empty';
  return model.activeState?.build?.steps.length ? 'request' : 'placeholder';
}

function NoRevisionBody({ model, props }: { model: Model; props: ExplanationViewProps }) {
  const kind = noRevisionKind(model);
  const taskLabel = props.taskLabel ?? props.target.leaf;
  if (kind === 'request') {
    return (
      <ExplanationRequest
        target={props.target}
        task={taskLabel}
        binding={model.activeState?.binding ?? null}
        comparison={props.comparison}
        build={model.activeState?.build ?? null}
        onBuilt={() => void model.read.refresh()}
        onCancel={model.cancelRequest}
      />
    );
  }
  if (kind === 'placeholder') return <BuildingPlaceholder />;
  return <EmptyExplanation taskLabel={taskLabel} onExplain={model.openRequest} />;
}

/** What the view draws once a revision exists: the page, or the still placeholder while it builds. */
function RevisionBody({ model, props }: { model: Model; props: ExplanationViewProps }) {
  if (model.phase === 'building') return <BuildingPlaceholder />;
  if (model.shown === null) return null;
  return <SavedPage model={model} props={props} shown={model.shown} />;
}

/** What the column draws for the phase on screen: a sentence, a placeholder, or the page itself. */
function ExplanationBody({ model, props }: { model: Model; props: ExplanationViewProps }) {
  if (model.phase === 'reading' && model.read.state === null) {
    return (
      <p className={standfirst} data-testid="explanation-reading">
        Reading the explanation…
      </p>
    );
  }
  if (model.phase === 'unavailable') {
    return <Unreadable problem={model.read.problem} onRetry={() => void model.read.refresh()} />;
  }
  if (!hasRevisionOf(model)) return <NoRevisionBody model={model} props={props} />;
  return <RevisionBody model={model} props={props} />;
}

function Unreadable({ problem, onRetry }: { problem: RefusalInfo | null; onRetry: () => void }) {
  return (
    <div className={card} data-testid="explanation-unavailable">
      <p className={alert} role="alert">
        {problem ? `The explanation cannot be read: ${problem.reason}: ${problem.detail}` : ''}
      </p>
      <button type="button" className={quiet} data-testid="explanation-retry" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

function EmptyExplanation({ taskLabel, onExplain }: { taskLabel: string; onExplain: () => void }) {
  return (
    <div className={card} data-testid="explanation-empty">
      <h3 className={title}>No explanation yet</h3>
      <p className={bodyText}>
        {`The explainer reads the whole change set of ${taskLabel} and writes one end-to-end review of it here. It can also grade the blocks, so the tree can sort by its judgment.`}
      </p>
      <p className={bodyText}>Nothing runs until you ask.</p>
      <button type="button" className={primary} data-testid="explain-change-set" onClick={onExplain}>
        ✦ Explain the change set
      </button>
    </div>
  );
}

function BuildingPlaceholder() {
  return (
    <div className={card} data-testid="explanation-building" aria-busy="true">
      <p className={standfirst} data-testid="explanation-building-line">
        Being written · the progress is in the chat
      </p>
      <div className={skeleton} aria-hidden="true">
        <div className={bone} style={{ width: '62%' }} />
        <div className={bone} />
        <div className={bone} style={{ width: '88%' }} />
        <div className={bone} style={{ width: '76%' }} />
      </div>
    </div>
  );
}

const agentIdOf = (state: ExplainerState | null | undefined): string | null =>
  state?.explainer?.agentId ?? state?.seat.agentId ?? null;

const partIdsOf = (parts: ReadonlyArray<{ id: string }> | undefined): string[] =>
  (parts ?? []).map((p) => p.id);

function FrameMeta({ state, revision, rows, details, controls, slot }: {
  state: ExplainerState | null;
  revision: number;
  rows: ExplainerRevision[];
  details?: React.ReactNode;
  controls?: React.ReactNode;
  slot?: 'left' | 'right';
}) {
  return <>
    <span className={standfirstInline} data-testid="explanation-standfirst">
      <span className={who}>✦ Written by the explainer</span>
      {standfirstText(state, revision, rows)}
    </span>
    {slot === 'right' ? null : controls}
    {slot === 'right' ? null : details}
  </>;
}

function CompareColumn({
  slot,
  revision,
  model,
  props,
  agentId,
}: {
  slot: 'left' | 'right';
  revision: number;
  model: Model;
  props: ExplanationViewProps;
  agentId: string | null;
}) {
  const row = model.rows.find((r) => r.revision === revision);
  const revState = mergeRevisionIntoState(model.read.state, row, model.compareStates[revision]);
  const stale = staleParts(revState);
  const binding = row ? row.binding : (revState?.binding ?? null);
  return (
    <div className={compareCol} data-testid="explanation-compare-column" data-revision={revision}>
      <RevisionFrame
        target={props.target}
        revision={revision}
        caption={captionOf(model.rows, revision)}
        binding={binding}
        compareSlot={slot}
        stalePartIds={partIdsOf(stale.parts)}
        knownPartIds={partIdsOf(revState?.parts)}
        staleTotal={stale.total}
        explainerAgentId={agentId}
        onEvidence={props.onEvidence}
        meta={<FrameMeta state={revState} revision={revision} rows={model.rows} slot={slot} details={props.headerDetails} controls={<RevisionControls model={model} />} />}
      />
    </div>
  );
}

function RetiredNotice({ retired }: { retired?: boolean }) {
  if (!retired) return null;
  return (
    <p className={standfirst} data-testid="explanation-retired">
      from a retired explainer · read-only
    </p>
  );
}

function SingleSavedPage({
  model,
  props,
  shown,
  agentId,
}: {
  model: Model;
  props: ExplanationViewProps;
  shown: number;
  agentId: string | null;
}) {
  const state = model.activeState;
  const row = model.rows.find((r) => r.revision === shown);
  const stale = staleParts(state);
  const binding = row ? row.binding : (state?.binding ?? null);
  return (
    <>
      <RevisionFrame
        target={props.target}
        revision={shown}
        caption={captionOf(model.rows, shown)}
        binding={binding}
        compareSlot="primary"
        stalePartIds={partIdsOf(stale.parts)}
        knownPartIds={partIdsOf(state?.parts)}
        staleTotal={stale.total}
        explainerAgentId={agentId}
        onEvidence={props.onEvidence}
        meta={<FrameMeta state={state} revision={shown} rows={model.rows} details={props.headerDetails} controls={<RevisionControls model={model} />} />}
      />
      <RetiredNotice retired={state?.retired} />
    </>
  );
}

/** The saved page: its standfirst and one frame or the compared pair. */
function SavedPage({
  model,
  props,
  shown,
}: {
  model: Model;
  props: ExplanationViewProps;
  shown: number;
}) {
  const state = model.activeState;
  const agentId = agentIdOf(state);
  if (model.compared) {
    return (
      <>
        <div className={frames} data-testid="explanation-compare">
          <CompareColumn slot="left" revision={model.compared.left} model={model} props={props} agentId={agentId} />
          <CompareColumn slot="right" revision={model.compared.right} model={model} props={props} agentId={agentId} />
        </div>
        <RetiredNotice retired={state?.retired} />
      </>
    );
  }
  return <SingleSavedPage model={model} props={props} shown={shown} agentId={agentId} />;
}

/** A revision's caption: its binding, its time and its kind, exactly as the selector lists them. */
function captionOf(rows: readonly ExplainerRevision[], revision: number): string {
  const row = rows.find((one) => one.revision === revision);
  if (!row) return `revision ${revision}`;
  const focus = row.focus ? ` · ${row.focus}` : '';
  return `${bindingLabel(row.binding)} · ${clockOf(row.at)} · ${kindLabel(row.kind)}${focus} · revision ${row.revision}`;
}

function allModelNodes(model: ChangeSetModel) {
  return model.subjects.flatMap((subject) => [
    ...subject.changed,
    ...subject.unchanged,
    ...subject.notComparable,
  ]);
}

function mountJudgmentChip(
  cardEl: HTMLElement,
  judgment: NonNullable<ReturnType<typeof allModelNodes>[number]['judgment']>,
  created: HTMLElement[],
) {
  cardEl.setAttribute('data-graded', 'true');
  if (judgment.stale) cardEl.setAttribute('data-stale-grade', 'true');
  else cardEl.removeAttribute('data-stale-grade');
  const headEl = cardEl.firstElementChild;
  if (!(headEl instanceof HTMLElement)) return;
  const chip = document.createElement('span');
  chip.setAttribute('data-testid', 'review-judgment-chip');
  if (judgment.stale) chip.setAttribute('data-stale', 'true');
  if (judgment.activeCategory) chip.setAttribute('data-category', judgment.activeCategory);
  if (judgment.activeGrade) chip.setAttribute('data-grade', judgment.activeGrade);
  chip.setAttribute('title', judgment.hoverText);
  chip.textContent = judgment.chipLabel;
  headEl.appendChild(chip);
  created.push(chip);
}

function mountUngradedHeading(cardEl: HTMLElement, count: number | undefined, created: HTMLElement[]) {
  if (count === undefined || count <= 0 || !cardEl.parentElement) return;
  const groupHeading = document.createElement('div');
  groupHeading.setAttribute('data-testid', 'review-reading-ungraded-group');
  groupHeading.setAttribute('data-count', String(count));
  groupHeading.textContent = `Ungraded (${count})`;
  cardEl.parentElement.insertBefore(groupHeading, cardEl);
  created.push(groupHeading);
}

/**
 * Attaches explainer judgment chips and `Ungraded (n)` group headings to the reading column's
 * location cards (MIK-R187 rules 5 and 6).
 */
export function ReadingJudgmentMounts({
  model,
  scroller,
}: {
  model: ChangeSetModel;
  scroller: RefObject<HTMLElement | null>;
}) {
  useLayoutEffect(() => {
    const nodes = allModelNodes(model);
    if (!nodes.some((n) => n.judgment !== undefined || n.ungradedGroupCount !== undefined)) {
      return undefined;
    }
    const container = scroller.current;
    if (!container) return undefined;
    const created: HTMLElement[] = [];

    for (const node of nodes) {
      const cardEl = blockElement(container, node.key);
      if (!cardEl) continue;
      mountUngradedHeading(cardEl, node.ungradedGroupCount, created);
      if (node.judgment) {
        mountJudgmentChip(cardEl, node.judgment, created);
      } else {
        cardEl.removeAttribute('data-graded');
        cardEl.removeAttribute('data-stale-grade');
      }
    }

    return () => {
      for (const el of created) el.remove();
    };
  }, [model, scroller]);

  return null;
}
