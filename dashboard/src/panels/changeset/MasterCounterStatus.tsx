import { css } from '../../../styled-system/css';
import type { MasterCounters, TaskChangeset } from '../../data/changeset';
import type { MasterAttribution } from '../../data/useMasterCounters';

export function MasterCounterStatus({ value, className }: { value: MasterAttribution; className?: string }) {
  if (value.phase === 'idle' || value.phase === 'ready') return null;
  return (
    <span className={className} role="status" data-testid="master-attribution-state"
      title={value.phase === 'failed' ? value.problem.detail : undefined}>
      {value.phase === 'loading' ? 'reading leaf breakdown…' : 'leaf breakdown unavailable'}
    </span>
  );
}

const counterRow = css({
  display: 'flex',
  gap: '0.8rem',
  marginLeft: 'auto',
  fontSize: '0.74rem',
  fontFamily: 'mono',
});
// The bound net generation: short digest + currentness + the one scope this view ever serves.
// Rendered only when the list response names its generation, so older payloads read unchanged.
const generationTag = css({ fontSize: '0.7rem', fontFamily: 'mono', color: 'muted' });
const ins = css({ color: 'mint' });
const del = css({ color: 'amber' });

export function CounterRow({ counters }: { counters: TaskChangeset['counters'] }) {
  return (
    <span className={counterRow} data-testid="changeset-counters">
      <span>
        code <span className={ins}>+{counters.code.insertions}</span>{' '}
        <span className={del}>−{counters.code.deletions}</span> ({counters.code.files})
      </span>
      <span>
        memory <span className={ins}>+{counters.memory.insertions}</span>{' '}
        <span className={del}>−{counters.memory.deletions}</span> ({counters.memory.files})
      </span>
    </span>
  );
}

// The bound net generation as a header caption: short digest + currentness + the one scope this
// view ever serves. Rendered only when the list response names its generation, so older
// payloads read unchanged.
export function SeriesGenerationTag({ meta }: { meta: MasterCounters | null }) {
  const generation = meta?.generation;
  if (!generation) return null;
  const currentness = meta?.currentness ?? 'unmeasured';
  return (
    <span
      className={generationTag}
      data-testid="changeset-generation"
      title={`net generation ${generation.digest} · scope integrated · ${currentness}`}
    >
      gen {generation.digest.slice(0, 8)} · {currentness} · integrated
    </span>
  );
}

