/**
 * The Explanation view (MIK-R106 v2 rule 6; MIK-R104 v3; MIK-R103 v2).
 *
 * It is the explainer's end-to-end page for the bound comparison, shown in the review page's reading
 * column behind the top line's Review | Explanation switch. The top line, the tree and the chat stay
 * outside it: this component draws neither, and it mounts no chat.
 *
 * WHAT IT SHOWS, in this order:
 *   * the moved notice and its Update offer, when the leaf moved past the binding;
 *   * the trusted list of stale parts with their titles and reasons -- always, whatever the frame
 *     shows, hides or imitates (MIK-R104 v3 rule 3);
 *   * the revision selector: each retained revision with its binding, time and kind, the agreed one
 *     marked, Compare (two isolated frames) and, at the task total, the removal control;
 *   * the page itself, in MIK-R104 v3's isolated host, stored by MIK-R103 v2;
 *   * the claims with their MIK-R102 bases, and their links into the Review view.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useState, type RefObject } from 'react';

import { css } from '../../../../styled-system/css';
import { askFirstRequest } from '../../../explainer/request';
import { trayStore } from '../../../tray/trayStore';
import type { EvidenceDestination } from '../canvas/canvasApi';
import type { ChangeSetModel } from '../changeSetOrder';
import { blockElement } from '../reading/blockDom';
import { BasisLegend, ClaimList } from './BasisLegend';
import { MovedNotice, type UpdateRun } from './MovedNotice';
import { RevisionFrame } from './RevisionFrame';
import { RevisionSelector, type ComparePair } from './RevisionSelector';
import { StalePartsList } from './StalePartsList';
import {
  readExplainerState,
  type BindingOwner,
  type ClaimLink,
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
  defaultShownRevision,
  hasWork,
  kindLabel,
  orderRevisions,
  phaseOf,
  staleGradesOf,
  staleParts,
  staleRegenerationOf,
  standfirstText,
  updateOffered,
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
  /** Opens the block a claim links to, in the Review view, and selects it (MIK-R89 v2 rule 9). */
  onOpenBlock: (claim: ClaimLink) => void;
  /** "✦ Explain the change set": opens the chat's first-request form. It starts nothing here. */
  onExplain: () => void;
  /** Where a frame's own evidence operation goes; the page's selection owner wires this. */
  onEvidence?: (destination: EvidenceDestination) => void;
  /** The leaf's short name, for the empty state's one sentence. */
  taskLabel?: string;
  /** A read the page's owner already holds, so the switch and this view read one answer between them. */
  read?: ExplainerRead;
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
  run: UpdateRun | null;
  regenProblem: string | null;
  chosen: number | null;
  choose: (revision: number | null) => void;
  compare: (pair: ComparePair | null) => void;
  pressUpdate: () => void;
  pressRegenerate: (forState?: ExplainerState | null, forRevision?: number | null) => void;
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

function buildColdRegenerateItems(
  stale: ReturnType<typeof staleParts>,
  staleGrades: readonly string[],
) {
  const partItems = stale.parts.map((p) => ({ id: p.id, title: p.title, reason: p.reason }));
  const seen = new Set(partItems.map((p) => p.id));
  const gradeItems = staleGrades
    .filter((ref) => !seen.has(ref))
    .map((ref) => ({ id: ref, title: `Grade · ${ref}`, reason: 'stale grade' }));
  return [...partItems, ...gradeItems];
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

      askFirstRequest({ repository, master, leaf }, buildColdRegenerateItems(stale, staleGrades));
      trayStore.getState().setText(regeneration.message);
      props.onExplain();
    },
    [activeState, shown, read, repository, master, leaf, props, setRegenProblem],
  );
}

function useExplanationView(props: ExplanationViewProps): Model {
  const [chosen, setChosen] = useState<number | null>(null);
  const [compared, setCompared] = useState<ComparePair | null>(null);
  const [run, setRun] = useState<UpdateRun | null>(null);
  const [regenProblem, setRegenProblem] = useState<string | null>(null);

  const own = useExplainer(props.target, props.comparison, props.read === undefined, chosen);
  const read = props.read ?? own;
  const rows = useMemo<ExplainerRevision[]>(
    () => orderRevisions(read.revisions?.revisions ?? []),
    [read.revisions],
  );
  const shown = chosen ?? defaultShownRevision(rows, read.state);
  const revisionStates = useRevisionStates(props, chosen, compared);

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

  const pressUpdate = useCallback(async () => {
    setRun({ state: 'updating' });
    const outcome = await read.update();
    setRun(
      outcome.kind === 'updated'
        ? { state: 'done', counts: outcome.counts }
        : { state: 'failed', detail: `${outcome.reason}: ${outcome.detail}` },
    );
  }, [read]);

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
    run,
    regenProblem,
    chosen,
    choose: setChosen,
    compare: setCompared,
    pressUpdate: () => void pressUpdate(),
    pressRegenerate: (forState, forRevision) => void pressRegenerate(forState, forRevision),
  };
}

export function ExplanationView(props: ExplanationViewProps) {
  const model = useExplanationView(props);
  return (
    <section className={view} data-testid="explanation-view" data-phase={model.phase}>
      <ExplanationPanel model={model} props={props} />
    </section>
  );
}

/** The panel's fixed lines, then whatever the phase draws. */
function ExplanationPanel({ model, props }: { model: Model; props: ExplanationViewProps }) {
  const state = model.activeState;
  const stale = staleParts(state);
  const staleGrades = staleGradesOf(state);
  const regeneration = staleRegenerationOf(state, model.shown);
  return (
    <>
      {state?.moved ? (
        <MovedNotice
          moved={state.moved}
          hasWork={hasWork(state)}
          update={model.run}
          onUpdate={updateOffered(state) ? model.pressUpdate : undefined}
        />
      ) : null}
      {model.compared ? null : (
        <StalePartsList
          parts={stale.parts}
          total={stale.total}
          unavailable={stale.unavailable}
          staleGrades={staleGrades}
          regeneration={regeneration}
          regenerating={model.read.busy === 'regenerating'}
          regenerationProblem={model.regenProblem}
          onRegenerate={() => model.pressRegenerate(state, model.shown)}
        />
      )}
      {model.rows.length ? (
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
      ) : null}
      <ExplanationBody model={model} props={props} />
    </>
  );
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
  if (model.phase === 'none') {
    return (
      <EmptyExplanation
        taskLabel={props.taskLabel ?? props.target.leaf}
        onExplain={props.onExplain}
      />
    );
  }
  if (model.phase === 'building') return <BuildingPlaceholder />;
  if (model.shown === null) return null;
  return <SavedPage model={model} props={props} shown={model.shown} />;
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
  const staleGrades = staleGradesOf(revState);
  const regeneration = staleRegenerationOf(revState, revision);
  const binding = row ? row.binding : (revState?.binding ?? null);
  return (
    <div className={compareCol} data-testid="explanation-compare-column" data-revision={revision}>
      <p className={standfirst} data-testid="explanation-standfirst">
        <span className={who}>✦ Written by the explainer</span>
        {standfirstText(revState, revision, model.rows)}
      </p>
      <StalePartsList
        parts={stale.parts}
        total={stale.total}
        unavailable={stale.unavailable}
        staleGrades={staleGrades}
        regeneration={regeneration}
        regenerating={model.read.busy === 'regenerating'}
        regenerationProblem={model.regenProblem}
        onRegenerate={() => model.pressRegenerate(revState, revision)}
      />
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
      />
      <ClaimList
        claims={revState?.claims ?? []}
        parts={revState?.parts ?? []}
        onOpenBlock={props.onOpenBlock}
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
      <p className={standfirst} data-testid="explanation-standfirst">
        <span className={who}>✦ Written by the explainer</span>
        {standfirstText(state, shown, model.rows)}
      </p>
      <BasisLegend />
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
      />
      <RetiredNotice retired={state?.retired} />
      <ClaimList
        claims={state?.claims ?? []}
        parts={state?.parts ?? []}
        onOpenBlock={props.onOpenBlock}
      />
    </>
  );
}

/** The saved page: its standfirst, the legend, one frame or the compared pair, and the claims. */
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
        <BasisLegend />
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

/**
 * The moved notice banner shown in the Review tab when the leaf moved past the explainer's binding
 * (MIK-R186 rule 7; MIK-R187 rules 4 and 9).
 */
export function ReviewMovedBanner({ read }: { read: ExplainerRead }) {
  const [run, setRun] = useState<UpdateRun | null>(null);
  const state = read.state;
  const pressUpdate = useCallback(async () => {
    setRun({ state: 'updating' });
    const outcome = await read.update();
    setRun(
      outcome.kind === 'updated'
        ? { state: 'done', counts: outcome.counts }
        : { state: 'failed', detail: `${outcome.reason}: ${outcome.detail}` },
    );
  }, [read]);
  if (!state?.moved) return null;
  return (
    <MovedNotice
      moved={state.moved}
      hasWork={hasWork(state)}
      update={run}
      onUpdate={updateOffered(state) ? () => void pressUpdate() : undefined}
    />
  );
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
