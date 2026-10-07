// The fixed single-line header and truthful read problems of the Knowledge reader.
import { useEffect, useMemo, useState } from 'react';
import { css } from '../../../styled-system/css';
import {
  readerGet,
  type ReaderAddress,
  type ReaderAnswer,
  type ReaderSelection,
  type SelectionOptions,
  type RecordListAnswer,
} from '../../data/knowledgeReader';
import { StateBadge, muted } from './readerParts';
const toolbar = css({
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
  fontSize: '0.75rem',
  flexShrink: '0',
  minWidth: '0',
  overflowX: 'auto',
  '& select, & input, & button': {
    font: 'inherit',
    background: 'bg',
    color: 'ink',
    border: '1px solid token(colors.grid)',
    borderRadius: '2px',
    padding: '0.15rem 0.25rem',
    minWidth: '0',
  },
  '& label': {
    display: 'flex',
    alignItems: 'center',
    gap: '0.25rem',
    whiteSpace: 'nowrap',
    flexShrink: '0',
  },
  '& button': { flexShrink: '0', whiteSpace: 'nowrap' },
  '& input': { width: '9rem', flexShrink: '0' },
  '& select': { maxWidth: '12rem' },
  '@media (max-width: 40rem)': {
    '& input': { width: '4rem' },
    '& select': { maxWidth: '6rem' },
    '& label > span': { display: 'none' },
  },
});
const banner = css({
  fontSize: '0.68rem',
  color: 'muted',
  whiteSpace: 'nowrap',
  marginLeft: 'auto',
});
const browse = css({
  display: 'none',
  '@media (max-width: 40rem)': {
    display: 'inline-block',
    position: 'sticky',
    left: '0',
    zIndex: '2',
  },
});

function PinButton({
  pin,
  address,
  go,
}: {
  pin: string;
  address: ReaderAddress;
  go: (address: ReaderAddress) => void;
}) {
  return (
    <>
      {' '}
      <button
        type="button"
        data-testid="reader-pin"
        onClick={() => go({ ...address, commit: pin })}
      >
        pin {pin.slice(0, 9)}
      </button>
    </>
  );
}

export function SelectionNotes({ selection }: { selection: ReaderSelection }) {
  const problems = selection.problems ?? [];
  return (
    <>
      {selection.codeNote ? <p className={muted} data-testid="reader-code-note">{selection.codeNote}</p> : null}
      {selection.indexState === 'partial' ? (
    <div data-testid="reader-partial">
      <StateBadge state="partial" /> index: {problems.length} file(s) could not be read —{' '}
      {problems.map((problem) => `${problem.path}: ${problem.detail}`).join('; ')}
    </div>
      ) : null}
    </>
  );
}

function selectionLine(selection: ReaderSelection & { treeKey: string }): string {
  const memory = selection.memoryRevision
    ? ` · memory ${selection.memoryRevision.slice(0, 9)}`
    : '';
  const code = selection.codeTree ? selection.codeTree.treeId.slice(0, 12) : 'none';
  return `${selection.kind} ${selection.treeKey.slice(0, 9)}${memory} · code ${code}`;
}

function SelectionBanner({
  answer,
  address,
  go,
}: {
  answer: ReaderAnswer;
  address: ReaderAddress;
  go: (address: ReaderAddress) => void;
}) {
  const selection = answer.selection;
  if (!selection?.treeKey) return null;
  // N2: `published` and a leaf move; a clean tree offers the same view pinned to its memory commit.
  const pin = selection.kind !== 'commit' ? selection.pinnedCommit : null;
  return (
    <div className={banner} data-testid="reader-selection" data-index-state={selection.indexState}>
      <span className={muted}>{selectionLine({ ...selection, treeKey: selection.treeKey })}</span>
      {pin ? <PinButton pin={pin} address={address} go={go} /> : null}
    </div>
  );
}

function CommitOptions({ options }: { options: SelectionOptions | null }) {
  const leaves = options?.leaves ?? [];
  const listed = options?.commitsState?.state ?? 'listed';
  return (
    <>
      {leaves.length > 0 ? (
        <optgroup label="live leaves">
          {leaves.map((leaf) => (
            <option key={leaf.commit} value={leaf.commit}>
              {leaf.leafId ?? leaf.commit}
            </option>
          ))}
        </optgroup>
      ) : null}
      <optgroup label={listed === 'listed' ? 'memory commits' : `memory commits (${listed})`}>
        {(options?.commits ?? []).map((one) => (
          <option key={one.commit} value={one.commit} disabled={!one.converted}>
            {one.commit.slice(0, 9)} {one.subject.slice(0, 60)}
            {one.converted ? '' : ' (not converted)'}
          </option>
        ))}
      </optgroup>
    </>
  );
}

function CommitSelect({
  address,
  options,
  go,
}: {
  address: ReaderAddress;
  options: SelectionOptions | null;
  go: (address: ReaderAddress) => void;
}) {
  const known = new Set(
    ['published', ...(options?.commits ?? []), ...(options?.leaves ?? [])].map((one) =>
      typeof one === 'string' ? one : one.commit,
    ),
  );
  return (
    <label>
      <span className={muted}>tree </span>
      <select
        aria-label="Memory tree"
        data-testid="reader-commit"
        value={address.commit}
        onChange={(event) => go({ ...address, commit: event.target.value })}
      >
        <option value="published">published (default)</option>
        {known.has(address.commit) ? null : (
          <option value={address.commit}>{address.commit}</option>
        )}
        <CommitOptions options={options} />
      </select>
    </label>
  );
}

type SideRead<T> = { answer: T | null; failure: string | null; load: () => Promise<T> };

// A side read (the selector's choices, the record list). A failure is kept and named beside the
// control it feeds, never turned into an empty list (review F11).
export function useRead<T extends { state: string }>(
  view: string,
  params: Record<string, string>,
): SideRead<T> {
  const [read, setRead] = useState<{ key: string; answer: T | null; failure: string | null }>({
    key: '', answer: null, failure: null,
  });
  const key = JSON.stringify(params);
  const readKey = `${view}\0${key}`;
  // One promise for this selection; the toolbar and Records consume the same answer.
  const load = useMemo(() => {
    let promise: Promise<T> | null = null;
    return () => (promise ??= readerGet<T>(view, JSON.parse(key) as Record<string, string>));
  }, [view, key]);
  useEffect(() => {
    if (!params.repo) return;
    let live = true;
    load().then(
      (body) =>
        live &&
        setRead({
          key: readKey,
          answer: body,
          failure: body.state === 'options' || body.state === 'view' ? null : body.state,
        }),
      (error: unknown) => live && setRead({ key: readKey, answer: null, failure: String(error) }),
    );
    return () => {
      live = false;
    };
  }, [load, readKey, params.repo]);
  return {
    answer: read.key === readKey ? read.answer : null,
    failure: read.key === readKey ? read.failure : null,
    load,
  };
}

function SideFailures({
  options,
  records,
}: {
  options: SideRead<SelectionOptions>;
  records: SideRead<RecordListAnswer>;
}) {
  const commits = options.answer?.commitsState;
  const notes = [
    options.failure ? `tree choices unavailable: ${options.failure}` : null,
    commits && commits.state !== 'listed'
      ? `memory commits ${commits.state}${commits.detail ? `: ${commits.detail}` : ''}`
      : null,
    records.failure ? `record list unavailable: ${records.failure}` : null,
  ].filter((one): one is string => one !== null);
  if (notes.length === 0) return null;
  return (
    <span className={muted} data-testid="reader-side-failure">
      {notes.join(' · ')}
    </span>
  );
}

export function ReaderToolbar({
  address,
  repos,
  go,
  selection,
  browsing,
  onBrowse,
  records,
}: {
  address: ReaderAddress;
  repos: string[];
  selection?: ReaderAnswer;
  browsing: boolean;
  onBrowse: () => void;
  records: SideRead<RecordListAnswer>;
  go: (address: ReaderAddress) => void;
}) {
  const [lookup, setLookup] = useState('');
  const options = useRead<SelectionOptions>('selections', { repo: address.repo });
  const recordIds = Object.values(records.answer?.kinds ?? {}).flat();
  const at = { repo: address.repo, commit: address.commit };
  const openLookup = () => {
    const id = lookup.trim().split(' ')[0];
    if (id) go({ ...at, view: 'record', id });
  };
  return (
    <div className={toolbar}>
      <button className={browse} type="button" onClick={onBrowse}>
        {browsing ? 'Document' : 'Browse'}
      </button>
      <label>
        <span className={muted}>repository </span>
        <select
          aria-label="Repository"
          value={address.repo}
          onChange={(event) =>
            go({ repo: event.target.value, commit: 'published', view: 'path', path: '.' })
          }
        >
          {[...new Set([address.repo, ...repos])].map((one) => (
            <option key={one} value={one}>
              {one}
            </option>
          ))}
        </select>
      </label>
      <CommitSelect address={address} options={options.answer} go={go} />
      <input
        aria-label="Record ID"
        placeholder="INV-… / FAM-… / DEC-…"
        list="knowledge-record-ids"
        value={lookup}
        onChange={(event) => setLookup(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') openLookup();
        }}
      />
      <datalist id="knowledge-record-ids">
        {recordIds.map((one) => (
          <option key={one.id} value={one.id}>
            {one.kind} · {one.title}
          </option>
        ))}
      </datalist>
      <button type="button" onClick={openLookup}>
        open
      </button>
      {selection ? <SelectionBanner answer={selection} address={address} go={go} /> : null}
      <SideFailures options={options} records={records} />
    </div>
  );
}
