// The list of a consolidation pass on its task's page: a count for each outcome over the whole
// list, the first subjects with their outcome, and the whole list one counted link away. Built
// like the Review block, from the same parts.
import type { TaskConsolidationNode, TaskConsolidationRowNode } from '../../types/projection';
import { Block, Clip, MoreLink, Row, Rows, SubHead, plural, type DocView } from './pageParts';
import { dim, findingText, lineRow, muted, none, rowId, warn } from './pageStyles';
import { REVIEW_INLINE_BOUND } from './taskBlocks';

type Outcome = TaskConsolidationRowNode['outcome'];

// The words and tones follow the Review block: what needs attention is amber, what is settled
// recedes, and open work keeps the muted tone of a fact still to come.
const OUTCOME_TONE: Record<Outcome, string | undefined> = {
  stands: dim,
  corrected: undefined,
  unresolved: warn,
  open: muted,
};

function standsWord(count: number): string {
  return count === 1 ? 'stands' : 'stand';
}

/** The outcome counts as one line, for a view's head. */
export function outcomeCountsText(consolidation: TaskConsolidationNode): string {
  if (!consolidation.memoryRead) {
    return `${consolidation.total} listed · outcomes not known yet`;
  }
  const { stands, corrected, unresolved, open } = consolidation;
  return `${stands} ${standsWord(stands)} · ${corrected} corrected · ${unresolved} unresolved · ${open} open`;
}

function OutcomeCounts({ consolidation }: { consolidation: TaskConsolidationNode }) {
  if (!consolidation.memoryRead) {
    return (
      <div className={lineRow} data-testid="consolidation-counts">
        <span className={muted} data-testid="consolidation-count-listed">
          {consolidation.total} listed · outcomes not known yet
        </span>
      </div>
    );
  }
  const { stands, corrected, unresolved, open } = consolidation;
  return (
    <div className={lineRow} data-testid="consolidation-counts">
      <span data-testid="consolidation-count-stands">
        {stands} {standsWord(stands)}
      </span>
      <span data-testid="consolidation-count-corrected">{corrected} corrected</span>
      <span
        className={unresolved > 0 ? warn : undefined}
        data-testid="consolidation-count-unresolved"
      >
        {unresolved} unresolved
      </span>
      <span className={muted} data-testid="consolidation-count-open">
        {open} open
      </span>
    </div>
  );
}

function ListedLine({ consolidation }: { consolidation: TaskConsolidationNode }) {
  const listed = [
    consolidation.families.length > 0
      ? plural(consolidation.families.length, 'family', 'families')
      : '',
    consolidation.rules.length > 0 ? plural(consolidation.rules.length, 'rule') : '',
  ].filter(Boolean);
  return (
    <div className={lineRow}>
      <span className={muted} data-testid="consolidation-listed">
        listed: {listed.join(', ')}
      </span>
      {consolidation.memoryRead ? null : (
        <span className={warn} data-testid="consolidation-memory-unread">
          the task's memory was not read: the ids stand as listed and no outcome is known
        </span>
      )}
    </div>
  );
}

function rowText(row: TaskConsolidationRowNode): string {
  return [row.family ? `member of ${row.family}` : '', row.reason ?? '']
    .filter(Boolean)
    .join(' · ');
}

function ConsolidationRow({
  row,
  memoryRead,
}: {
  row: TaskConsolidationRowNode;
  memoryRead: boolean;
}) {
  const text = rowText(row);
  const badge = !memoryRead && row.outcome === 'open' ? 'listed' : row.outcome;
  return (
    <Row testid={`consolidation-row-${row.subject}`} state={badge}>
      <span className={rowId({ tone: 'plain' })}>{row.subject}</span>
      <span className={OUTCOME_TONE[row.outcome]}>{badge}</span>
      <span className={findingText}>{text ? <Clip text={text} tone="ink" /> : null}</span>
    </Row>
  );
}

// The consolidation of a task: shown only where its document names the list of a pass. The
// counts are the whole list's; the rows come with the document body, the first few inline.
export function ConsolidationBlock({
  consolidation,
  openView,
}: {
  consolidation: TaskConsolidationNode | undefined;
  openView: (view: DocView) => void;
}) {
  if (!consolidation) return null;
  const rows = consolidation.rows ?? [];
  const shown = rows.slice(0, REVIEW_INLINE_BOUND);
  return (
    <Block
      title="Consolidation"
      block="consolidation"
      count={plural(consolidation.total, 'subject')}
    >
      <OutcomeCounts consolidation={consolidation} />
      <ListedLine consolidation={consolidation} />
      {shown.length > 0 ? (
        <>
          <SubHead title="Subjects" count={consolidation.total} />
          <Rows cols="findingAll" testid="consolidation-rows">
            {shown.map((row) => (
              <ConsolidationRow
                key={row.subject}
                row={row}
                memoryRead={consolidation.memoryRead}
              />
            ))}
          </Rows>
        </>
      ) : null}
      {rows.length > 0 ? (
        <MoreLink
          onClick={() => openView({ kind: 'consolidation' })}
          testid="consolidation-all-link"
        >
          all {plural(consolidation.total, 'subject')}
        </MoreLink>
      ) : null}
    </Block>
  );
}

export function ConsolidationViewBody({ consolidation }: { consolidation: TaskConsolidationNode }) {
  const rows = consolidation.rows ?? [];
  if (rows.length === 0) {
    return <p className={none}>The subjects arrive with the document body.</p>;
  }
  return (
    <Rows cols="findingAll" testid="consolidation-all-rows">
      {rows.map((row) => (
        <ConsolidationRow key={row.subject} row={row} memoryRead={consolidation.memoryRead} />
      ))}
    </Rows>
  );
}
