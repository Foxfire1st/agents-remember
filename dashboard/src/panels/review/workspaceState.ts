// Local display choices survive subject reads, but do not persist across review openings.
import { useRef, useState } from 'react';
import type { DiffMode } from '../changeset/DiffPane';
import type { FamilySelection } from './FamilyTree';
import type { TreeIntent } from './walkedTree';

// A selection's request to land focus on the selected node once its answer is shown. `scrollOnly`
// (a refresh) only brings the node into view; `answered` is set when an answer has arrived after the
// request was made.
export interface FocusRequest {
  from: Element | null;
  // The tree intent that was current when the request was made. A render from before the selection
  // (its passive effect can run late, after the reader's next click) must not take the request.
  after: number;
  scrollOnly?: boolean;
  answered?: boolean;
}

export interface WorkspaceState {
  chosen: FamilySelection | null;
  setChosen: (selection: FamilySelection | null) => void;
  query: string;
  setQuery: (next: string) => void;
  layout: DiffMode;
  setLayout: (next: DiffMode) => void;
  fullFile: boolean;
  setFullFile: (next: boolean) => void;
  openPath: string | null | undefined;
  openFromCenter: (path: string) => void;
  closePath: (path: string | null) => void;
  // Sets the opened path without moving focus (an intent marker's follow and return).
  setOpenPath: (path: string | null | undefined) => void;
  center: React.RefObject<HTMLDivElement | null>;
  // The unexplained-changes lane destination on screen (and the file it opened), or none.
  worklist: boolean;
  setWorklist: (next: boolean) => void;
  // Set by an explicit selection with the element that had focus then; the answer's focus lands on
  // the selected node only if the reader has not moved focus elsewhere in the meantime.
  focusSelection: React.RefObject<FocusRequest | null>;
  // The latest selection as far as the family tree is concerned (MIK-R39): an in-tree selection
  // keeps the tree the reader has walked, anything else starts it afresh. See `walkedTree.ts`.
  treeIntent: TreeIntent;
  beginSelection: (keepTree: boolean) => void;
}

export function useWorkspaceState(): WorkspaceState {
  const [chosen, setChosen] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState('');
  const [layout, setLayout] = useState<DiffMode>(() => window.matchMedia('(max-width: 40rem)').matches ? 'inline' : 'split');
  const [fullFile, setFullFile] = useState(false);
  const [openPath, setOpenPath] = useState<string | null>();
  const [worklist, setWorklist] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const center = useRef<HTMLDivElement>(null);
  const focusSelection = useRef<FocusRequest | null>(null);
  const [treeIntent, setTreeIntent] = useState<TreeIntent>({ id: 0, keep: false });
  const beginSelection = (keepTree: boolean) =>
    setTreeIntent((previous) => ({ id: previous.id + 1, keep: keepTree }));
  const openFromCenter = (path: string) => {
    opener.current = document.activeElement as HTMLElement | null;
    setOpenPath(path);
  };
  const closePath = (path: string | null) => {
    if (path !== null) opener.current = document.activeElement as HTMLElement | null;
    setOpenPath(path);
    if (path === null) opener.current?.focus();
  };
  const choose = (selection: FamilySelection | null) => {
    setChosen(selection);
    setWorklist(false);
    if (selection) revealCenter(center);
  };
  return {
    chosen,
    setChosen: choose,
    query,
    setQuery,
    layout,
    setLayout,
    fullFile,
    setFullFile,
    openPath,
    openFromCenter,
    closePath,
    setOpenPath,
    center,
    worklist,
    setWorklist,
    focusSelection,
    treeIntent,
    beginSelection,
  };
}

// The rail owns its scroll; selecting there restores the common reading position without stealing focus.
function revealCenter(center: React.RefObject<HTMLDivElement | null>): void {
  if (window.matchMedia('(max-width: 40rem)').matches) return;
  const surface = center.current?.closest<HTMLElement>('[data-testid="review-surface"]');
  if (surface) surface.scrollTop = 0;
}
