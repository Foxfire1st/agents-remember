import { css } from '../../../styled-system/css';
import type { ReviewPayload, ReviewJudgmentRow, ReviewProofDisplay } from '../../data/review';
import type { ReviewSubject } from './ReviewNavigation';

const muted = css({ color: 'muted', fontSize: '0.78rem', margin: '0.35rem 0' });
const rows = css({ paddingLeft: '1.1rem', margin: '0.4rem 0', overflowWrap: 'anywhere' });

function effectLabel(row: ReviewJudgmentRow, family: boolean): string | undefined {
  if (row.effect) return row.effect;
  return family && row.disposition === 'changed' ? 'effect not recorded' : undefined;
}

function JudgmentRow({ row, family }: { row: ReviewJudgmentRow; family: boolean }) {
  const effect = effectLabel(row, family);
  const because = row.because.map((reference) => typeof reference === 'string' ? reference :
    `${reference.id}@${reference.version} · ${reference.task.repository}:${reference.task.path} · ${reference.packet}`);
  return <li data-testid="review-judgment-row">
    <p><strong>attempt {row.attempt} · {row.disposition}</strong>
      {effect ? ` · ${effect}` : ''} · {row.closed ? 'closed' : 'open'}</p>
    {row.reason ? <p>{row.reason}</p> : null}
    {because.length ? <p>Because: {because.join(' · ')}</p> : null}
    {row.covered_entries !== undefined ? <p>{row.covered_entries} covered {row.covered_entries === 1 ? 'entry' : 'entries'}</p> : null}
    {row.examined_members !== undefined ? <p>{row.examined_members} examined {row.examined_members === 1 ? 'member' : 'members'}</p> : null}
  </li>;
}

function Proof({ proof }: { proof: ReviewProofDisplay }) {
  return <li data-testid="review-judgment-proof" data-proof-state={proof.state}>
    <p><strong>{proof.path}{proof.symbol ? ` · ${proof.symbol}` : ''}</strong> · {proof.state}</p>
    <p>{proof.facet}</p>
    <p className={muted}>{proof.detail}</p>
  </li>;
}

/** The group's identifiers, in one closed disclosure: MIK-R86 rule 20 caps a view's
 * disclosures and draws none per item, while R57 rule 3 keeps a row's identifiers here. */
function Identifiers({ summary, lines }: { summary: string; lines: string[] }) {
  return <details data-disclosure="identifiers"><summary>{summary}</summary>
    {lines.map((line, index) => <p key={index} className={muted}>{line}</p>)}
  </details>;
}

export function SubjectJudgment({ payload, subject }: { payload: ReviewPayload; subject: ReviewSubject }) {
  const judgment = payload.judgment;
  if (!judgment) return <p role="alert">The server did not supply judgment for this read.</p>;
  const selected = payload.knowledge.revision_selection;
  if (!selected || selected.record_kind !== subject.kind || selected.record_id !== subject.id)
    return <p role="alert">The server did not supply the selected subject's judgment read.</p>;
  return <section data-review-content="judgment" data-testid="review-center-judgment" data-judgment-state={judgment.state}>
    <h3 className={css({ color: 'cyan', fontSize: '0.85rem' })}>Judgment and proofs</h3>
    <p className={muted} role={judgment.state === 'unavailable' ? 'alert' : undefined}>{judgment.detail}</p>
    {judgment.rows.length ? <ul className={rows}>{judgment.rows.map((row) =>
      <JudgmentRow key={`${row.attempt}:${row.row_id}`} row={row} family={subject.kind === 'family'} />)}</ul> : null}
    {judgment.rows.length ? <Identifiers summary="Judgment identifiers" lines={judgment.rows.map((row) =>
      `${row.subject} · ${row.row_id} · ${row.history_path}`)} /> : null}
    {judgment.proofs.length ? <ul className={rows}>{judgment.proofs.map((proof) =>
      <Proof key={proof.id} proof={proof} />)}</ul> : <p className={muted}>No proofs were returned for this subject.</p>}
    {judgment.proofs.length ? <Identifiers summary="Proof identifiers" lines={judgment.proofs.map((proof) =>
      `${proof.id} · ${proof.invariant}`)} /> : null}
  </section>;
}
