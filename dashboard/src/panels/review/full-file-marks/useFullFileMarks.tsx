import { useContext, useId, useState, type ReactNode } from 'react';
import type { ReviewSourceExpansion } from '../../../data/review';
import type { ReviewFileClassification } from '../../../data/reviewLane';
import type { PaneMarks } from '../../file-viewer/markGutter';
import { describesDrawn } from '../IntentMarkers';
import { fileMarks, type HunkMark } from '../hunkMarkers';
import { IntentMarkerScope, useMarkClassification } from '../intentMarkerScope';
import { spanLabel } from '../laneFocus';
import type { SourceMarkers } from '../SourceContent';

function sourceMarks(
  expansion: ReviewSourceExpansion,
  file: ReviewFileClassification | null,
  active: boolean,
) {
  if (
    !active ||
    !file ||
    !describesDrawn(file, {
      before: expansion.before.object_id,
      after: expansion.after.object_id,
    })
  )
    return null;
  return fileMarks(file);
}

function MarksList({
  hunks,
  placed,
  truncated,
  onReveal,
}: {
  hunks: HunkMark[];
  placed: Set<string>;
  truncated: boolean;
  onReveal: (key: string) => void;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} style={{ minWidth: 0, maxHeight: '26rem', overflowY: 'auto' }}>
      <h3 id={id}>Marks in this file</h3>
      <ul>
        {hunks.map((mark) => {
          const available = placed.has(mark.key);
          const invariants = [
            ...new Set([...mark.realizations, ...mark.proofs].map((entry) => entry.invariant)),
          ];
          return (
            <li key={mark.key}>
              <button type="button" disabled={!available} onClick={() => onReveal(mark.key)}>
                before {spanLabel(mark.before)}, after {spanLabel(mark.after)} ·{' '}
                {mark.tone.replaceAll('_', ' ')}
                {invariants.length ? ` · ${invariants.join(', ')}` : ' · no linked invariants'}
                {!available
                  ? truncated
                    ? ' · beyond the part of the file shown here (first 2 MiB)'
                    : ' · not in the text shown here'
                  : ''}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function revealingPane(
  pane: PaneMarks | undefined,
  selected: string | null,
  clear: () => void,
): PaneMarks | undefined {
  if (!pane) return undefined;
  return {
    ...pane,
    reveal: selected ?? pane.reveal,
    onRevealed: selected ? clear : pane.onRevealed,
  };
}

// The list is independent of the editor viewport. Reveal uses the pane's existing
// scroll-and-focus path, which also draws offscreen gutters and expands collapsed runs.
export function useFullFileMarks(
  expansion: ReviewSourceExpansion,
  markers: SourceMarkers | undefined,
  pane: PaneMarks | undefined,
): { marks: PaneMarks | undefined; list: ReactNode } {
  const scope = useContext(IntentMarkerScope);
  const read = useMarkClassification(
    expansion.path,
    expansion.status !== 'unchanged',
    markers?.classification,
  );
  const [requested, setRequested] = useState<{ path: string; key: string } | null>(null);
  const file = read.phase === 'ready' ? read.value : null;
  const classified = sourceMarks(expansion, file, scope !== null);
  if (classified?.kind !== 'hunks') return { marks: pane, list: null };
  const placed = new Set(pane?.marks.map((mark) => mark.id));
  const selected =
    requested?.path === expansion.path && placed.has(requested.key) ? requested.key : null;
  return {
    marks: revealingPane(pane, selected, () => setRequested(null)),
    list: (
      <MarksList
        hunks={classified.hunks}
        placed={placed}
        truncated={expansion.before.truncated || expansion.after.truncated}
        onReveal={(key) => setRequested({ path: expansion.path, key })}
      />
    ),
  };
}
