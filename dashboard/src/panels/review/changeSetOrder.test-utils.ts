// Inputs of the change-set model over two captured card reads (l89.capture-provenance.json):
// L181 at comparison #8 (INV-1ZKP4R, 74 entries) and L104 at #25 (15 subjects, 96 entries). The
// outlines are the subjects' recorded facts as the investigator's capture listed them (change and
// effect labels); the unchanged rows beside them are synthetic and marked so. The captures predate the
// card read's `symbol` and memory values: `withMemory` and `withSymbol` add them, named per entry.
import type { ReviewOutline, ReviewOutlineInvariant } from '../../data/review';
import type {
  ReviewEntryMemory,
  ReviewTreeEntry,
  ReviewTreesResult,
} from '../../data/reviewTrees';
import type { ChangeSetCards, ChangeSetInputs } from './changeSetOrder';
import l104Capture from './l89.l104-cards.captured.json';
import l181Capture from './l89.l181-cards.captured.json';

export const l181Answer = l181Capture as unknown as ReviewTreesResult;
export const l104Answer = l104Capture as unknown as ReviewTreesResult;
const l181 = l181Answer;
const l104 = l104Answer;

export const L181_SUBJECT = '82ef402e-a852-59cf-a037-aeabbbe2dad6';

function invariant(
  id: string,
  selector: string,
  change: ReviewOutlineInvariant['change'],
  labels: string[],
  summary: string,
): ReviewOutlineInvariant {
  return {
    selector_id: selector,
    id,
    summary,
    change,
    meaning: labels.length ? { state: 'steps', labels } : { state: 'none', labels: [] },
  };
}

export function cardsOf(
  answer: ReviewTreesResult,
  entries: readonly ReviewTreeEntry[] = answer.entries ?? [],
): Extract<ChangeSetCards, { state: 'ready' }> {
  if (!answer.comparison) throw new Error('a card capture names its comparison');
  return { state: 'ready', entries, comparison: answer.comparison, memory: answer.entry_memory };
}

// L181 #8: one invariant changed (weaken); two synthetic unchanged invariants beside it.
export const l181Outline: ReviewOutline = {
  state: 'outlined',
  detail: 'L181 #8 as captured, with two synthetic unchanged rows.',
  families: [],
  invariants: [
    invariant('INV-0SYNTH', 'synthetic-unchanged-0', 'unchanged', [], 'A synthetic unchanged invariant'),
    invariant('INV-1ZKP4R', L181_SUBJECT, 'changed', ['weaken'], 'A silent role-agent turn end'),
    invariant('INV-ZSYNTH', 'synthetic-unchanged-z', 'unchanged', [], 'Another synthetic unchanged invariant'),
  ],
};

export function l181Inputs(over: Partial<ChangeSetInputs> = {}): ChangeSetInputs {
  return { outline: l181Outline, catalogue: [], code: new Map(), cards: cardsOf(l181), ...over };
}

export const l181Entries = (): ReviewTreeEntry[] => structuredClone(l181.entries ?? []);

// L104 #25: the fifteen added or changed subjects of the capture, in its listed order.
const L104_ROWS: [string, string, ReviewOutlineInvariant['change'], string[], string][] = [
  ['INV-40VGBY', 'f80ecf80-ded4-5a53-878b-320f6b43e021', 'added', [], 'A revision becomes ready only after a static'],
  ['INV-6V0ET6', '56e2bb4a-6803-5a66-9a07-dfc81b75c5dd', 'added', [], 'A generated page executes script only under a'],
  ['INV-9ZKYWQ', '80f870f0-884a-5b30-a7db-b6daf9a17500', 'changed', ['clarify'], 'Explicit in-tree subject selections preserve the retained walk:'],
  ['INV-ESF2CT', 'd37cc5d3-cd7a-5be1-84ae-b1bbd27e55ad', 'added', ['clarify'], 'The channel binds to the admitted document rather'],
  ['INV-EWGTN0', '7dc25251-0637-5299-adaf-36194c9ff831', 'added', [], 'Presenter retention must not remove an attempt whose'],
  ['INV-FQC46N', '523e3fd4-4f94-5b8d-a134-4dd292e3db48', 'added', [], "A page's filesystem addresses are confined: a segment"],
  ['INV-GWMA1V', 'fa8ec5a9-01e7-5b5d-b307-396cea6b9508', 'added', ['clarify'], 'The presenter begins authoring only on an explicit'],
  ['INV-HDXPMA', '56ae576b-6184-55ce-8f20-27e76bd631d6', 'added', [], 'A state-changing dashboard request is refused before any'],
  ['INV-K302DH', '1c649e45-c1d4-54fe-812d-7cb55689d9fa', 'added', [], 'The frame document is served under a sandbox'],
  ['INV-M6Q82M', 'a58dfc2c-910a-5707-9e15-92270cdce5b6', 'added', [], 'When the presenter request ledger still names a'],
  ['INV-N7SK8E', '75265822-0719-5db0-b13b-303d0c67d233', 'added', ['clarify'], "A frame request is validated against its session's"],
  ['INV-R38XGA', '420d3f2f-8fa4-58d5-9d9f-edc4d98c0671', 'added', ['clarify'], "The pinned kit is the presenter's offline toolbox,"],
  ['INV-RKY270', '8ba2e602-74dc-59d6-b776-42a4c0b26df1', 'added', ['clarify'], 'The host resolves a requested evidence reference only'],
  ['INV-SE4ECQ', 'bc862bf3-6b93-5233-a933-dc35145cb588', 'added', [], 'The installed capability text a non-seat capability is'],
  ['INV-YCMZGZ', '624e5779-10dd-5364-a08f-4ce2716717a5', 'added', [], 'A delayed host receipt must not change a'],
];

export const L104_IDS = L104_ROWS.map(([id]) => id);
export const l104Selector = (id: string): string =>
  L104_ROWS.find((row) => row[0] === id)?.[1] ?? id;

export const l104Outline: ReviewOutline = {
  state: 'outlined',
  detail: 'L104 #25: the fifteen added or changed subjects as captured.',
  families: [],
  invariants: L104_ROWS.map((row) => invariant(...row)),
};

export function l104Inputs(over: Partial<ChangeSetInputs> = {}): ChangeSetInputs {
  return { outline: l104Outline, catalogue: [], code: new Map(), cards: cardsOf(l104), ...over };
}

export const l104Entries = (): ReviewTreeEntry[] => structuredClone(l104.entries ?? []);

// Memory values for named invariants: every entry of the invariant gets `values`, the others zero.
export function withMemory(
  entries: ReviewTreeEntry[],
  values: Record<string, Partial<Omit<ReviewEntryMemory, 'path'>>>,
): ReviewTreeEntry[] {
  return entries.map((entry) => ({
    ...entry,
    memory: {
      path: entry.after.path,
      invariants_at_path: 0,
      incoming_links: 0,
      without_proof: false,
      open_weaknesses: 0,
      ...values[entry.invariant],
    },
  }));
}

export function withSymbol(entries: ReviewTreeEntry[], id: string, symbol: string): ReviewTreeEntry[] {
  return entries.map((entry) =>
    entry.id === id ? { ...entry, after: { ...entry.after, symbol } } : entry,
  );
}
