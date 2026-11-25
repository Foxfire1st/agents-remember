// Beside its record link, a list row shows the derived state and projected writer independently.
// Explicit unknown attribution appears as a writer. The note omits unavailable fields.
// A retired record has no derived state.
import { css } from '../../../styled-system/css';
import type { RecordSummary } from '../../data/knowledgeReader';

const quiet = css({ color: 'muted' });

export function recordStandingText(summary: Pick<RecordSummary, 'recordState' | 'writer'>): string {
  const writer = summary.writer ? `written by ${summary.writer.role} · ${summary.writer.model}` : '';
  return [summary.recordState ?? '', writer].filter(Boolean).join(' · ');
}

export function RecordStandingNote({ summary }: { summary: RecordSummary }) {
  const text = recordStandingText(summary);
  if (!text) return null;
  return (
    <span className={quiet} data-testid="record-standing" data-record-state={summary.recordState ?? undefined}>
      {` · ${text}`}
    </span>
  );
}
