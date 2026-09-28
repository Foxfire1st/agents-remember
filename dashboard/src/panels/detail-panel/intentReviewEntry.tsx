// The task entry's one Intent review control: `⇄ Intent review +N −N`.
//
// WHAT THE NUMBERS ARE. `+N` counts invariant and joint-guarantee revisions only the comparison's after
// side holds as current (added, or the new text of a revised one); `−N` counts those only the before
// side holds (retired, or the superseded text). They come from the comparison owner's summary read
// (`data/reviewIntentSummary.ts`) -- never from the change set's line counts, which is what the entry
// used to show by reusing the change-set button, and never from the size of the subject catalogue,
// which enumerates identities without comparing them.
//
// WHAT THE ENTRY DOES NOT DO. It reads no subject catalogue, offers no subject picker and has no
// refresh control of its own: choosing a subject and re-reading the comparison belong to the reviewer,
// which loads its catalogue when it is opened. The entry always opens the task-context review, bound
// to the live candidate while the enclosure is live and to the leaf's recorded comparison once it is
// closed (ICR-R12) -- the summary's answer is a label on the control and never a gate on it.
//
// UNAVAILABLE IS NOT ZERO. A comparison whose knowledge could not be read shows a brief state ("no
// knowledge yet", "unavailable") and no counts; the owner's code, reason and next action are one click
// away in the disclosure beside the control (ICR-R16). A partial answer shows its counts marked partial.
import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import { type IntentSummaryRead, useIntentReviewSummary } from "../../data/reviewIntentSummary";
import { EntryStateDetails, briefProblem, problemSentence } from "./entryState";
import { changeSetBtn, changeSetCounts } from "./styles";

function IntentCounts({ read }: { read: IntentSummaryRead }) {
  if (read.phase === "loading") {
    return (
      <span className={changeSetCounts} data-testid="intent-review-state" data-review-state="loading">
        …
      </span>
    );
  }
  if (read.phase === "unavailable") {
    return (
      <span
        className={changeSetCounts}
        data-testid="intent-review-state"
        data-review-state={read.problem.token}
        data-review-code={read.problem.code}
      >
        {briefProblem(read.problem)}
      </span>
    );
  }
  return (
    <>
      <span
        className={changeSetCounts}
        data-testid="intent-review-counts"
        data-added={read.counts.added}
        data-removed={read.counts.removed}
      >
        +{read.counts.added} −{read.counts.removed}
      </span>
      {read.phase === "partial" ? (
        <span className={changeSetCounts} data-testid="intent-review-state" data-review-state="partial">
          partial
        </span>
      ) : null}
    </>
  );
}

function IntentDetails({ read }: { read: IntentSummaryRead }) {
  if (read.phase === "unavailable") {
    return (
      <EntryStateDetails testId="intent-review-details" label="Intent review">
        {problemSentence(read.problem)}
      </EntryStateDetails>
    );
  }
  if (read.phase === "partial") {
    return (
      <EntryStateDetails testId="intent-review-details" label="Intent review">
        {read.counts.unresolved} subject(s) have no single current revision on one side, so they are
        not counted; the counts describe every other subject. The reviewer shows each one.
      </EntryStateDetails>
    );
  }
  return null;
}

export function IntentReviewEntry({
  repo,
  master,
  leaf,
  live,
  facts,
  onOpen,
}: {
  repo: string;
  master: string;
  leaf: string;
  live: boolean;
  // This leaf's own projection facts: the summary is re-read when they move and at no other time.
  facts: string;
  onOpen: (target: ChangeSetTarget) => void;
}) {
  const read = useIntentReviewSummary(repo, master, leaf, facts);
  const target: ChangeSetTarget = {
    repo,
    master,
    leaf,
    review: live ? {} : { historical: true },
  };
  return (
    <>
      <button
        type="button"
        className={changeSetBtn}
        onClick={() => onOpen(target)}
        data-testid="open-intent-review"
        data-intent-state={read.phase}
      >
        ⇄ {live ? "Intent review" : "Intent review (recorded)"}
        <IntentCounts read={read} />
      </button>
      <IntentDetails read={read} />
    </>
  );
}
