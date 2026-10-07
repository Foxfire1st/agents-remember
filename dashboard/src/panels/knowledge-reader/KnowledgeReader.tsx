// The Knowledge area (MIK-R29): a read-only reader of one repository's knowledge at any memory
// tree, browsed like a file explorer. It needs no task. Its address -- repository, memory tree and
// path or record ID -- is the URL hash, so every link is a navigation and a view can be shared.
//
// Left: the explorer (KnowledgeTree). Top: the repository, the memory-tree selector (the
// published tree by default, any memory commit, or a live leaf's candidate), a record lookup, the
// without-proof list and the census. Centre: the view the address names.
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useLayoutEffect,
  memo,
  type ReactNode,
  type RefObject,
} from 'react';

import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';

import { css } from '../../../styled-system/css';
import { fetchRepos } from '../../data/files';
import {
  parseReaderHash,
  codeAddress,
  type ReferenceItem,
  readAddress,
  readerHash,
  type CensusAnswer,
  type CodeAnswer,
  type PathViewAnswer,
  type ReaderAddress,
  type ReaderAnswer,
  type RecordViewAnswer,
  type SubtreeAnswer,
  type WithoutProofAnswer,
  type RecordListAnswer,
} from '../../data/knowledgeReader';
import { ReaderToolbar as Toolbar, SelectionNotes, useRead } from './ReaderToolbar';
import { KnowledgeTree } from './KnowledgeTree';
import { CensusView, PathView, SubtreeView, WithoutProofView } from './PathViews';
import { ReaderNavContext, muted, type ReaderNav } from './readerParts';
import { useReaderNavigation, useRestoreReaderScroll } from './readerNavigation';
import { ReaderOutline } from './ReaderOutline';
import { CodeView } from './ReaderCode';
import { TargetLink } from './readerParts';
import { TruthView } from './TruthView';

const shell = css({
  height: '100%',
  minHeight: '0',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.4rem',
  overflow: 'hidden',
});
const pane = css({
  height: '100%',
  overflow: 'auto',
  overflowWrap: 'anywhere',
  padding: '0 0.8rem 1rem',
  background: 'bgPanel',
  scrollPaddingTop: '2.3rem',
  minWidth: '0',
});
const handle = css({
  width: '4px',
  background: 'grid',
  cursor: 'col-resize',
  _hover: { background: 'amber' },
});
const body = css({
  flex: '1',
  minHeight: '0',
  '@media (min-width: 40.001rem) and (max-width: 70rem)': {
    '& [data-reader-aside], & [data-reader-outline-handle]': {
      display: 'none!',
    },
    '& [data-reader-document]': { flex: '1!' },
    '&[data-picker=true] [data-reader-aside]': { display: 'block!', flex: '1!' },
    '&[data-picker=true] [data-reader-document]': { display: 'none!' },
  },
  '@media (max-width: 40rem)': {
    '& [data-reader-tree], & [data-reader-handle], & [data-reader-aside]': { display: 'none!' },
    '& [data-reader-content], & [data-reader-document]': { flex: '1!' },
    '&[data-browsing=true] [data-reader-tree]': { display: 'block!', flex: '1!' },
    '&[data-browsing=true] [data-reader-content]': { display: 'none!' },
    '&[data-picker=true] [data-reader-aside]': { display: 'block!', flex: '1!' },
    '&[data-picker=true] [data-reader-document]': { display: 'none!' },
  },
});
type Loaded =
  | { state: 'idle' }
  | { state: 'loading'; hash: string }
  | { state: 'answer'; hash: string; answer: ReaderAnswer }
  | { state: 'failed'; hash: string; detail: string };

function currentAddress(): ReaderAddress | null {
  return parseReaderHash(window.location.hash);
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

function useRepositories(go: (address: ReaderAddress) => void, active: boolean): string[] {
  const [repos, setRepos] = useState<string[]>([]);
  const settled = useRef(false);
  useEffect(() => {
    if (!active || settled.current) return;
    let live = true;
    fetchRepos().then(
      (catalog) => {
        if (!live) return;
        const ids = catalog.repos.map((one) => one.repo);
        settled.current = true;
        setRepos(ids);
        if (!currentAddress()?.repo && ids.length > 0) {
          go({ repo: ids[0], commit: 'published', view: 'path', path: '.' });
        }
      },
      () => {
        if (live) settled.current = true;
      },
    );
    return () => {
      live = false;
    };
  }, [go, active]);
  return repos;
}

function ViewPane({ hash, loaded }: { hash: string; loaded: Loaded }) {
  if (loaded.state === 'answer' && loaded.hash === hash) {
    return (
      <>
        {loaded.answer.selection ? <SelectionNotes selection={loaded.answer.selection} /> : null}
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

export const KnowledgeReader = memo(function KnowledgeReader({
  active = true,
}: {
  active?: boolean;
}) {
  const document = useRef<HTMLDivElement>(null);
  const { address, go: navigate, save, remember, restore, restoreCurrent } =
    useReaderNavigation(document);
  const [browsing, setBrowsing] = useState(false);
  const [citation, setCitation] = useState<ReferenceItem | ReaderAddress | null>(null);
  const go = useCallback(
    (next: ReaderAddress) => {
      setBrowsing(false);
      setCitation(null);
      navigate(next);
    },
    [navigate],
  );
  const repos = useRepositories(go, active);
  const records = useRead<RecordListAnswer>('records', {
    repo: address?.repo ?? '',
    commit: address?.commit ?? 'published',
  });
  const { hash, loaded } = useAddressAnswer(address);
  const ready = readyHash(loaded);
  useRestoreReaderScroll(document, ready, restore);
  const nav = useCitationNavigation(address, go, setCitation, save);
  useEffect(() => {
    setCitation(null);
  }, [hash]);
  useVisibleReaderPlace(browsing, citation, active, restoreCurrent);
  if (!address || !nav || !address.repo)
    return <div data-testid="knowledge-reader">Choose a repository to read its knowledge.</div>;
  const answer = currentAnswer(loaded, hash);
  return (
    <ReaderNavContext.Provider value={nav}>
      <div data-testid="knowledge-reader" className={shell}>
        <Toolbar
          address={address}
          repos={repos}
          go={go}
          selection={answer}
          browsing={browsing}
          onBrowse={() => {
            save();
            setBrowsing((known) => !known);
          }}
          records={records}
        />
        <PanelGroup
          direction="horizontal"
          autoSaveId="knowledge.reader"
          className={body}
          data-browsing={browsing}
          data-picker={isReference(citation)}
        >
          <Panel defaultSize={24} minSize={12} data-reader-tree>
            <KnowledgeTree
              key={`${address.repo}\0${address.commit}`}
              address={address}
              onOpen={go}
              loadRecords={records.load}
            />
          </Panel>
          <PanelResizeHandle className={handle} data-reader-handle />
          <ReaderDocument
            paneRef={document}
            hash={hash}
            loaded={loaded}
            save={remember}
            citation={citation}
            ready={ready}
            onClose={() => setCitation(null)}
          />
        </PanelGroup>
      </div>
    </ReaderNavContext.Provider>
  );
});

function CitationPane({
  citation,
  onClose,
}: {
  citation: ReferenceItem | ReaderAddress;
  onClose: () => void;
}) {
  const address = 'view' in citation ? citation : null;
  const { hash, loaded } = useAddressAnswer(address);
  return (
    <div
      data-testid="reader-citation-pane"
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        padding: '0.4rem',
        minHeight: 0,
      }}
    >
      <button type="button" onClick={onClose}>
        Close cited code
      </button>
      {'targets' in citation ? (
        <ol>
          {citation.targets.map((target, index) => (
            <li key={index} data-testid="reference-target">
              <TargetLink target={target} />
            </li>
          ))}
        </ol>
      ) : (
        <ViewPane hash={hash} loaded={loaded} />
      )}
    </div>
  );
}

function useCitationNavigation(
  address: ReaderAddress | null,
  go: (next: ReaderAddress) => void,
  setCitation: (citation: ReferenceItem | ReaderAddress) => void,
  save: () => void,
): ReaderNav | null {
  const openCode = useCallback(
    (next: ReaderAddress) => {
      if (window.matchMedia('(max-width: 70rem)').matches) go(next);
      else setCitation(next);
    },
    [go, setCitation],
  );
  return useMemo(
    () =>
      address
        ? {
            repo: address.repo,
            commit: address.commit,
            go,
            openCode,
            openReference: (reference) => {
              save();
              const target = reference.targets[0];
              if (
                reference.targets.length === 1 &&
                (target.kind === 'code' || target.kind === 'test') &&
                target.anchor
              )
                openCode(
                  codeAddress(address, target.anchor, target.path ?? target.anchor.path ?? ''),
                );
              else setCitation(reference);
            },
          }
        : null,
    [address, go, openCode, setCitation, save],
  );
}

function readyHash(loaded: Loaded) {
  return loaded.state === 'answer' || loaded.state === 'failed' ? loaded.hash : '';
}
function currentAnswer(loaded: Loaded, hash: string) {
  return loaded.state === 'answer' && loaded.hash === hash ? loaded.answer : undefined;
}
function isReference(citation: ReferenceItem | ReaderAddress | null) {
  return citation !== null && 'targets' in citation;
}

function useVisibleReaderPlace(
  browsing: boolean,
  citation: ReferenceItem | ReaderAddress | null,
  active: boolean,
  restoreCurrent: () => void,
) {
  useLayoutEffect(() => {
    if (!browsing && !isReference(citation) && active) restoreCurrent();
  }, [browsing, citation, active, restoreCurrent]);
}

function ReaderDocument({
  paneRef,
  hash,
  loaded,
  save,
  citation,
  ready,
  onClose,
}: {
  paneRef: RefObject<HTMLDivElement | null>;
  hash: string;
  loaded: Loaded;
  save: () => void;
  citation: ReferenceItem | ReaderAddress | null;
  ready: string;
  onClose: () => void;
}) {
  return (
    <Panel minSize={35} data-reader-content data-testid="knowledge-document-pane">
      <PanelGroup direction="horizontal" autoSaveId="knowledge.document">
        <Panel defaultSize={76} minSize={35} data-reader-document>
          <div ref={paneRef} className={pane} data-testid="knowledge-view" onScroll={save}>
            <button type="button" onClick={() => window.history.back()} aria-label="Back">
              Back
            </button>
            <ViewPane hash={hash} loaded={loaded} />
          </div>
        </Panel>
        <PanelResizeHandle className={handle} data-reader-handle data-reader-outline-handle />
        <Panel defaultSize={24} minSize={12} data-reader-aside data-citation={citation !== null}>
          {citation ? (
            <CitationPane citation={citation} onClose={onClose} />
          ) : (
            <ReaderOutline pane={paneRef} ready={ready} />
          )}
        </Panel>
      </PanelGroup>
    </Panel>
  );
}
