// The review page's local display choices: they survive subject reads, but do not persist across
// review openings. How code is drawn is not here: "Changes only | Full context" is the page's one
// remembered control (reading/codeContextPreference.ts) and the diff layout follows the reading
// column's width (reading/useReadingLayout.ts). Nothing here scrolls the page.
import { useCallback, useRef, useState } from 'react';
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
  // Focus has landed for the selection's failed or refused answer; the request stays for the retry.
  failureLanded?: boolean;
}

export interface WorkspaceState {
  chosen: FamilySelection | null;
  setChosen: (selection: FamilySelection | null) => void;
  query: string;
  setQuery: (next: string) => void;
  // The reading column's opened groups (MIK-R89 v2 rule 5), by group id.
  openGroups: ReadonlySet<string>;
  toggleGroup: (id: string) => void;
  openGroup: (id: string) => void;
  // The leaf worklist view on screen in place of the change set (MIK-R86 rule 21), or not.
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
  const [openGroups, setOpenGroups] = useState<ReadonlySet<string>>(() => new Set());
  const [worklist, setWorklist] = useState(false);
  const focusSelection = useRef<FocusRequest | null>(null);
  const [treeIntent, setTreeIntent] = useState<TreeIntent>({ id: 0, keep: false });
  const beginSelection = useCallback(
    (keepTree: boolean) => setTreeIntent((previous) => ({ id: previous.id + 1, keep: keepTree })),
    [],
  );
  const toggleGroup = useCallback(
    (id: string) =>
      setOpenGroups((previous) => {
        const next = new Set(previous);
        if (!next.delete(id)) next.add(id);
        return next;
      }),
    [],
  );
  const openGroup = useCallback(
    (id: string) => setOpenGroups((previous) => (previous.has(id) ? previous : new Set(previous).add(id))),
    [],
  );
  const choose = useCallback((selection: FamilySelection | null) => {
    setChosen(selection);
    setWorklist(false);
  }, []);
  return {
    chosen,
    setChosen: choose,
    query,
    setQuery,
    openGroups,
    toggleGroup,
    openGroup,
    worklist,
    setWorklist,
    focusSelection,
    treeIntent,
    beginSelection,
  };
}
