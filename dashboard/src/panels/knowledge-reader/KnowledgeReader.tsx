// The Knowledge area (MIK-R29): a read-only reader of one repository's knowledge at any memory
// tree, browsed like a file explorer. It needs no task. Its address -- repository, memory tree and
// path or record ID -- is the URL hash, so every link is a navigation and a view can be shared.
//
// Left: the explorer (KnowledgeTree). Top: the repository, the memory-tree selector (the
// published tree by default, any memory commit, or a live leaf's candidate), a record lookup, the
// without-proof list and the census. Centre: the view the address names.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import { css } from '../../../styled-system/css';
import { fetchRepos } from '../../data/files';
import {
  parseReaderHash,
  readAddress,
  readerGet,
  readerHash,
  type CensusAnswer,
  type CodeAnswer,
  type PathViewAnswer,
  type ReaderAddress,
  type ReaderAnswer,
  type ReaderSelection,
  type RecordListAnswer,
  type RecordViewAnswer,
  type SelectionOptions,
  type SubtreeAnswer,
  type WithoutProofAnswer,
} from '../../data/knowledgeReader';
import { Panel } from '../../grammar/Panel';
import { KnowledgeTree } from './KnowledgeTree';
import { CensusView, CodeView, PathView, SubtreeView, WithoutProofView } from './PathViews';
import { ReaderNavContext, StateBadge, muted, type ReaderNav } from './readerParts';
import { TruthView } from './TruthView';

const sizing = css({ flex: '1', minWidth: '0' });
const toolbar = css({
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: '0.5rem',
  fontSize: '0.8rem',
  marginBottom: '0.5rem',
  '& select, & input, & button': {
    font: 'inherit',
    background: 'bg',
    color: 'ink',
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    borderRadius: '2px',
    padding: '0.1rem 0.35rem',
    maxWidth: '100%',
  },
});
const body = css({
  display: 'grid',
  gridTemplateColumns: 'minmax(14rem, 22rem) minmax(0, 1fr)',
  gap: '0.8rem',
  alignItems: 'start',
  '@media (max-width: 760px)': { gridTemplateColumns: 'minmax(0, 1fr)' },
});
const pane = css({ minWidth: '0', overflowWrap: 'anywhere' });
const banner = css({ fontSize: '0.76rem', margin: '0 0 0.5rem' });

type Loaded =
  | { state: 'idle' }
  | { state: 'loading'; hash: string }
  | { state: 'answer'; hash: string; answer: ReaderAnswer }
  | { state: 'failed'; hash: string; detail: string };

function currentAddress(): ReaderAddress | null {
  return parseReaderHash(window.location.hash);
}

function useReaderAddress(): [ReaderAddress | null, (address: ReaderAddress) => void] {
  const [address, setAddress] = useState<ReaderAddress | null>(currentAddress);
  useEffect(() => {
    const onChange = () => {
      const next = currentAddress();
      if (next) setAddress(next);
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  const go = useCallback((next: ReaderAddress) => {
    const hash = readerHash(next);
    if (window.location.hash !== hash) window.location.hash = hash;
    setAddress(next);
  }, []);
  return [address, go];
}

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
        pin this view to memory {pin.slice(0, 9)}
      </button>
    </>
  );
}

function PartialIndex({ selection }: { selection: ReaderSelection }) {
  if (selection.indexState !== 'partial') return null;
  const problems = selection.problems ?? [];
  return (
    <div data-testid="reader-partial">
      <StateBadge state="partial" /> index: {problems.length} file(s) could not be read —{' '}
      {problems.map((problem) => `${problem.path}: ${problem.detail}`).join('; ')}
    </div>
  );
}

function selectionLine(selection: ReaderSelection & { treeKey: string }): string {
  const memory = selection.memoryRevision
    ? ` · memory ${selection.memoryRevision.slice(0, 9)}`
    : '';
  const code = selection.codeTree ? selection.codeTree.treeId.slice(0, 12) : 'none';
  return `${selection.kind} tree ${selection.treeKey.slice(0, 12)}${memory} · code ${code} (${selection.codeNote})`;
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
      <PartialIndex selection={selection} />
    </div>
  );
}

// The view an answer of state `view` is rendered with, by the view it answers.
const VIEWS: Record<string, (answer: ReaderAnswer) => ReactNode> = {
  record: (answer) => <TruthView answer={answer as RecordViewAnswer} />,
  'without-proof': (answer) => <WithoutProofView answer={answer as WithoutProofAnswer} />,
  subtree: (answer) => <SubtreeView answer={answer as SubtreeAnswer} />,
  census: (answer) => <CensusView answer={answer as CensusAnswer} />,
  code: (answer) => <CodeView answer={answer as CodeAnswer} />,
};

function NotServed({ answer }: { answer: ReaderAnswer }) {
  if (answer.state === 'not-converted') {
    return (
      <p className={muted} data-testid="reader-not-converted">
        This memory tree is not converted, so the reader does not serve it: {answer.detail}
      </p>
    );
  }
  return (
    <p className={muted} data-testid="reader-refusal" data-state={answer.state}>
      {answer.state}: {answer.detail}
    </p>
  );
}

function AnswerView({ answer }: { answer: ReaderAnswer }) {
  // A census, a code file and a subtree page name their own non-view states on their own view.
  const own =
    ['census', 'code', 'subtree'].includes(answer.view ?? '') && answer.state !== 'not-converted';
  if (answer.state === 'view' || own) {
    const render = VIEWS[answer.view ?? ''];
    return render ? render(answer) : <PathView answer={answer as PathViewAnswer} />;
  }
  return <NotServed answer={answer} />;
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

type SideRead<T> = { answer: T | null; failure: string | null };

// A side read (the selector's choices, the record list). A failure is kept and named beside the
// control it feeds, never turned into an empty list (review F11).
function useRead<T extends { state: string }>(
  view: string,
  params: Record<string, string>,
): SideRead<T> {
  const [read, setRead] = useState<SideRead<T>>({ answer: null, failure: null });
  const key = JSON.stringify(params);
  useEffect(() => {
    let live = true;
    readerGet<T>(view, JSON.parse(key) as Record<string, string>).then(
      (body) =>
        live &&
        setRead({
          answer: body,
          failure: body.state === 'options' || body.state === 'view' ? null : body.state,
        }),
      (error: unknown) => live && setRead({ answer: null, failure: String(error) }),
    );
    return () => {
      live = false;
    };
  }, [view, key]);
  return read;
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

function useAddressAnswer(address: ReaderAddress | null): { hash: string; loaded: Loaded } {
  const [loaded, setLoaded] = useState<Loaded>({ state: 'idle' });
  const hash = address ? readerHash(address) : '';
  useEffect(() => {
    const target = parseReaderHash(hash);
    if (!target || !target.repo) return;
    let live = true;
    setLoaded({ state: 'loading', hash });
    readAddress(target).then(
      (answer) => live && setLoaded({ state: 'answer', hash, answer }),
      (error: unknown) => live && setLoaded({ state: 'failed', hash, detail: String(error) }),
    );
    return () => {
      live = false;
    };
  }, [hash]);
  return { hash, loaded };
}

function useRepositories(go: (address: ReaderAddress) => void): string[] {
  const [repos, setRepos] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    fetchRepos().then(
      (catalog) => {
        if (!live) return;
        const ids = catalog.repos.map((one) => one.repo);
        setRepos(ids);
        if (!currentAddress()?.repo && ids.length > 0) {
          go({ repo: ids[0], commit: 'published', view: 'path', path: '.' });
        }
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [go]);
  return repos;
}

function Toolbar({
  address,
  repos,
  go,
}: {
  address: ReaderAddress;
  repos: string[];
  go: (address: ReaderAddress) => void;
}) {
  const [lookup, setLookup] = useState('');
  const options = useRead<SelectionOptions>('selections', { repo: address.repo });
  const records = useRead<RecordListAnswer>('records', {
    repo: address.repo,
    commit: address.commit,
  });
  const recordIds = Object.values(records.answer?.kinds ?? {}).flat();
  const at = { repo: address.repo, commit: address.commit };
  const here = address.view === 'path' ? address.path : undefined;
  const openLookup = () => {
    const id = lookup.trim().split(' ')[0];
    if (id) go({ ...at, view: 'record', id });
  };
  return (
    <div className={toolbar}>
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
      <button
        type="button"
        data-testid="reader-without-proof-open"
        onClick={() => go({ ...at, view: 'without-proof', path: here })}
      >
        without proof{here && here !== '.' ? ` under ${here}` : ''}
      </button>
      <button type="button" onClick={() => go({ ...at, view: 'census' })}>
        census
      </button>
      <SideFailures options={options} records={records} />
    </div>
  );
}

function ViewPane({
  hash,
  loaded,
  address,
  go,
}: {
  hash: string;
  loaded: Loaded;
  address: ReaderAddress;
  go: (address: ReaderAddress) => void;
}) {
  if (loaded.state === 'answer' && loaded.hash === hash) {
    return (
      <>
        <SelectionBanner answer={loaded.answer} address={address} go={go} />
        <AnswerView key={hash} answer={loaded.answer} />
      </>
    );
  }
  if (loaded.state === 'failed' && loaded.hash === hash) {
    return (
      <p className={muted} data-testid="reader-failed">
        The reader could not be reached: {loaded.detail}
      </p>
    );
  }
  return <p className={muted}>reading…</p>;
}

export function KnowledgeReader() {
  const [address, go] = useReaderAddress();
  const repos = useRepositories(go);
  const { hash, loaded } = useAddressAnswer(address);
  const nav: ReaderNav | null = useMemo(
    () => (address ? { repo: address.repo, commit: address.commit, go } : null),
    [address, go],
  );
  if (!address || !nav || !address.repo) {
    return (
      <Panel testid="knowledge-reader" title="Knowledge" className={sizing}>
        <p className={muted}>Choose a repository to read its knowledge.</p>
      </Panel>
    );
  }
  return (
    <ReaderNavContext.Provider value={nav}>
      <Panel testid="knowledge-reader" title="Knowledge" className={sizing}>
        <Toolbar address={address} repos={repos} go={go} />
        <div className={body}>
          <KnowledgeTree
            repo={address.repo}
            commit={address.commit}
            current={address.view === 'path' ? address.path : undefined}
            onOpen={(path) =>
              go({ repo: address.repo, commit: address.commit, view: 'path', path })
            }
          />
          <div className={pane} data-testid="knowledge-view">
            <ViewPane hash={hash} loaded={loaded} address={address} go={go} />
          </div>
        </div>
      </Panel>
    </ReaderNavContext.Provider>
  );
}
