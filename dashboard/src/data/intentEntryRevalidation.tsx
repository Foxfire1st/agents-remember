// When the task entry's Intent review counts are re-validated.
//
// The mounted live reviewer subscribes to its exact memory target through the existing state SSE.
// A changed input revision revalidates its shared entry alongside the selected review. Closed and
// recorded reviews do not watch. Navigation and explicit refresh remain revalidation points:
//
//   * the task detail is shown again (a view switch back to Operations, or the reviewer takeover
//     closing back to the entry) -- `shown` turning true;
//   * the reviewer's refresh or matching memory event runs -- `revalidate()`;
//   * the task detail is opened on a task -- the entry mounts, or its task props change, and reads.
//
// The show transition is derived during render rather than in an effect, so a task change and the
// visibility change that arrive together (opening a task from another view) reach an already-mounted
// entry in one commit and cost one read, instead of a commit for each.
// It is local React state shared through context -- not an event system -- and without a provider the
// generation stays 0 and `revalidate` does nothing.
import { type ReactNode, createContext, useCallback, useContext, useMemo, useState } from "react";

interface EntryRevalidation {
  generation: number;
  revalidate: () => void;
}

const NO_REVALIDATION: EntryRevalidation = { generation: 0, revalidate: () => undefined };
const EntryRevalidationContext = createContext<EntryRevalidation>(NO_REVALIDATION);

export function IntentEntryRevalidation({
  shown,
  children,
}: {
  // Whether the task detail holding the entry is on screen now.
  shown: boolean;
  children: ReactNode;
}) {
  const [state, setState] = useState({ shown, generation: 0 });
  let current = state;
  if (state.shown !== shown) {
    current = { shown, generation: shown ? state.generation + 1 : state.generation };
    setState(current);
  }
  const revalidate = useCallback(
    () => setState((previous) => ({ ...previous, generation: previous.generation + 1 })),
    [],
  );
  const value = useMemo(
    () => ({ generation: current.generation, revalidate }),
    [current.generation, revalidate],
  );
  return (
    <EntryRevalidationContext.Provider value={value}>{children}</EntryRevalidationContext.Provider>
  );
}

// The entry's re-validation generation: a value that changes exactly when the entry must re-read.
export const useIntentEntryGeneration = (): number =>
  useContext(EntryRevalidationContext).generation;

// Ask the entry to re-read (the reviewer's refresh).
export const useRevalidateIntentEntry = (): (() => void) =>
  useContext(EntryRevalidationContext).revalidate;
