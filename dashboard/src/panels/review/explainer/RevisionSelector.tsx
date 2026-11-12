/**
 * The revision selector (MIK-R106 v2 rule 6; MIK-R103 v2 rule 8).
 *
 * It lists each retained revision with its binding (`#n`, or `planned`), its time and its kind, marks
 * the agreed revision, and offers Compare -- two revisions side by side, each in its own isolated
 * frame. It never offers a model, a regeneration or a build.
 *
 * "Remove old revisions" is the developer's one removal control. It appears only when a new revision
 * was refused at the task total, names that refusal, and offers only unpinned revisions: the agreed
 * one is pinned and is never offered. The 16 MiB per-revision ceiling refuses that one revision and
 * removing others does not help, so no control is offered for it.
 */

import { useState } from 'react';

import { css } from '../../../../styled-system/css';
import type { ExplainerRevision, RefusalInfo } from './explainerApi';
import {
  agreedRevision,
  bindingLabel,
  clockOf,
  isPinned,
  kindLabel,
  refusalLine,
  removalOffered,
  removableRevisions,
} from './explainerModel';

export interface ComparePair {
  left: number;
  right: number;
}

const box = css({
  display: 'grid',
  gap: '0.35rem',
  padding: '0.4rem 0.55rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
  minWidth: 0,
});

const head = css({ display: 'flex', gap: '0.5rem', alignItems: 'baseline', fontSize: '0.72rem', color: 'muted', fontFamily: 'mono' });
const rows = css({ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: '0.2rem' });
const row = css({ display: 'flex', gap: '0.45rem', alignItems: 'baseline', flexWrap: 'wrap', fontSize: '0.76rem' });
const fact = css({ color: 'muted', fontFamily: 'mono', fontSize: '0.7rem' });
const agreed = css({ color: 'mint', fontFamily: 'mono', fontSize: '0.7rem' });
const pinned = css({ color: 'cyan', fontFamily: 'mono', fontSize: '0.7rem' });
const control = css({
  fontFamily: 'mono',
  fontSize: '0.72rem',
  padding: '0.15rem 0.45rem',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'transparent',
  color: 'inherit',
  cursor: 'pointer',
  _disabled: { opacity: 0.45, cursor: 'default' },
});
const refusalBox = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'alarm',
  borderRadius: '3px',
  padding: '0.35rem 0.5rem',
  display: 'grid',
  gap: '0.25rem',
  fontSize: '0.74rem',
});
const refusalName = css({ color: 'alarm', fontFamily: 'mono' });
const offer = css({ display: 'grid', gap: '0.25rem' });
const choice = css({ display: 'flex', gap: '0.4rem', alignItems: 'baseline', fontFamily: 'mono', fontSize: '0.72rem' });
const note = css({ color: 'muted', fontSize: '0.7rem', margin: 0 });

export interface RevisionSelectorProps {
  rows: readonly ExplainerRevision[];
  /** `intentReview.agreedRevision`, as the frame store reads it. */
  pinned?: number | null;
  /** The revision on screen, if any. */
  shown: number | null;
  /** The pair being compared, when Compare is on. */
  compared: ComparePair | null;
  /** A refusal the host answered with: the task total's, the per-revision ceiling's, or another. */
  refusal?: RefusalInfo | null;
  busy?: boolean;
  onShow: (revision: number) => void;
  onCompare: () => void;
  onStopCompare: () => void;
  onRemove: (revisions: number[]) => void;
}

export function RevisionSelector(props: RevisionSelectorProps) {
  const [removing, setRemoving] = useState(false);
  const [chosen, setChosen] = useState<number[]>([]);
  const agreedRow = agreedRevision(props.rows, props.pinned);
  const refusal = props.refusal ?? null;
  const toggle = (revision: number) =>
    setChosen((current) =>
      current.includes(revision) ? current.filter((one) => one !== revision) : [...current, revision],
    );

  return (
    <section className={box} data-testid="revision-selector" aria-label="Revisions of the explanation">
      <div className={head}>
        <span>Revisions</span>
        <span>{props.rows.length}</span>
        {agreedRow ? (
          <span className={pinned} data-testid="agreed-line">
            {`the agreed revision is ${bindingLabel(agreedRow.binding)} (revision ${agreedRow.revision}), pinned`}
          </span>
        ) : null}
      </div>
      <ol className={rows}>
        {props.rows.map((entry) => (
          <RevisionRow
            key={entry.revision}
            entry={entry}
            pinned={isPinned(entry, props.pinned)}
            shown={props.shown === entry.revision}
            onShow={props.onShow}
          />
        ))}
      </ol>
      <CompareControl
        rows={props.rows}
        compared={props.compared}
        busy={props.busy === true}
        onCompare={props.onCompare}
        onStopCompare={props.onStopCompare}
      />
      {refusal ? (
        <RefusalBox
          refusal={refusal}
          removable={removableRevisions(props.rows, props.pinned)}
          chosen={chosen}
          removing={removing}
          busy={props.busy === true}
          onToggle={toggle}
          onToggleOpen={() => setRemoving((open) => !open)}
          onRemove={() => {
            props.onRemove(chosen);
            setChosen([]);
          }}
        />
      ) : null}
    </section>
  );
}

function RevisionRow({
  entry,
  pinned: pinnedRow,
  shown,
  onShow,
}: {
  entry: ExplainerRevision;
  pinned: boolean;
  shown: boolean;
  onShow: (revision: number) => void;
}) {
  return (
    <li
      className={row}
      data-testid="revision-row"
      data-revision={entry.revision}
      data-binding={bindingLabel(entry.binding)}
      data-kind={kindLabel(entry.kind)}
      data-agreed={entry.agreed ? 'true' : undefined}
      data-pinned={pinnedRow ? 'true' : undefined}
      data-shown={shown ? 'true' : undefined}
      data-removed={entry.removed ? 'true' : undefined}
    >
      <button
        type="button"
        className={control}
        data-testid="revision-show"
        aria-pressed={shown}
        disabled={entry.removed}
        onClick={() => onShow(entry.revision)}
      >
        {bindingLabel(entry.binding)}
      </button>
      <span className={fact} data-testid="revision-time">
        <time dateTime={entry.at}>{clockOf(entry.at)}</time>
      </span>
      <span className={fact} data-testid="revision-kind">
        {kindLabel(entry.kind)}
        {entry.focus ? ` · ${entry.focus}` : ''}
      </span>
      <span className={fact}>{`revision ${entry.revision}`}</span>
      {entry.agreed ? (
        <span className={agreed} data-testid="revision-agreed">
          ● agreed
        </span>
      ) : null}
      {pinnedRow && !entry.agreed ? (
        <span className={pinned} data-testid="revision-pinned">
          pinned
        </span>
      ) : null}
      {entry.removed ? <span className={fact}>removed · kept as a receipt</span> : null}
    </li>
  );
}

function CompareControl({
  rows,
  compared,
  busy,
  onCompare,
  onStopCompare,
}: {
  rows: readonly ExplainerRevision[];
  compared: ComparePair | null;
  busy: boolean;
  onCompare: () => void;
  onStopCompare: () => void;
}) {
  const comparable = rows.filter((row) => !row.removed).length >= 2;
  const label = compared
    ? `Stop comparing · ${bindingLabel(bindingOf(rows, compared.left))} and ${bindingLabel(bindingOf(rows, compared.right))}`
    : 'Compare';
  return (
    <div className={row}>
      <button
        type="button"
        className={control}
        data-testid="compare"
        disabled={!comparable || busy}
        onClick={compared ? onStopCompare : onCompare}
      >
        {label}
      </button>
      {compared ? (
        <span className={fact} data-testid="compare-line">
          two revisions side by side, each in its own isolated frame
        </span>
      ) : null}
    </div>
  );
}

function RefusalBox({
  refusal,
  removable,
  chosen,
  removing,
  busy,
  onToggle,
  onToggleOpen,
  onRemove,
}: {
  refusal: RefusalInfo;
  removable: readonly ExplainerRevision[];
  chosen: number[];
  removing: boolean;
  busy: boolean;
  onToggle: (revision: number) => void;
  onToggleOpen: () => void;
  onRemove: () => void;
}) {
  return (
    <div className={refusalBox} data-testid="revision-refusal" role="alert">
      <span className={refusalName} data-testid="revision-refusal-name">
        {`A new revision was refused: ${refusalLine(refusal)}`}
      </span>
      {removalOffered(refusal) ? (
        <div className={offer}>
          <p className={note} data-testid="task-total-line">
            The task total is full. Removing old revisions is your choice alone; the agreed revision
            is pinned and is never offered.
          </p>
          {removing ? <RemovalChoices removable={removable} chosen={chosen} onToggle={onToggle} /> : null}
          <div className={row}>
            <button
              type="button"
              className={control}
              data-testid="remove-old-revisions"
              onClick={onToggleOpen}
            >
              {removing ? 'Cancel removal' : 'Remove old revisions'}
            </button>
            {removing ? (
              <button
                type="button"
                className={control}
                data-testid="remove-selected"
                disabled={chosen.length === 0 || busy}
                onClick={onRemove}
              >
                {`Remove ${chosen.length} ${chosen.length === 1 ? 'revision' : 'revisions'}`}
              </button>
            ) : null}
          </div>
        </div>
      ) : (
        <p className={note} data-testid="no-removal-line">
          Only this revision was refused, so removing others would not help. Nothing is offered.
        </p>
      )}
    </div>
  );
}

function RemovalChoices({
  removable,
  chosen,
  onToggle,
}: {
  removable: readonly ExplainerRevision[];
  chosen: number[];
  onToggle: (revision: number) => void;
}) {
  if (!removable.length) {
    return (
      <ul className={rows} data-testid="remove-choices">
        <li className={choice}>Nothing may be removed: every retained revision is pinned.</li>
      </ul>
    );
  }
  return (
    <ul className={rows} data-testid="remove-choices">
      {removable.map((entry) => (
        <li className={choice} key={entry.revision} data-testid="remove-choice" data-revision={entry.revision}>
          <input
            type="checkbox"
            id={`remove-${entry.revision}`}
            checked={chosen.includes(entry.revision)}
            onChange={() => onToggle(entry.revision)}
          />
          <label htmlFor={`remove-${entry.revision}`}>
            {`${bindingLabel(entry.binding)} · ${clockOf(entry.at)} · ${kindLabel(entry.kind)} · revision ${entry.revision}`}
          </label>
        </li>
      ))}
    </ul>
  );
}

function bindingOf(rows: readonly ExplainerRevision[], revision: number) {
  return rows.find((row) => row.revision === revision)?.binding ?? null;
}
