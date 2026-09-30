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
//
// UNEXPLAINED IS A SEPARATE FACT. A tree comparison's summary also carries the unexplained-changes
// lane's file count (MIK-R32): `· K unexplained`, or `· K unexplained · U unknown` when some file's
// attribution is unknown. It is its own element after `+N −N`, never added to it. A partially measured
// change set says `· attribution partial` (the unmeasured scope is in the disclosure), an unmeasured
// one `· attribution unknown`; zero of both, a dataset comparison and a pending read show nothing.
import type { ChangeSetTarget } from "../changeset/ChangeSetViewer";
import { type IntentSummaryRead, useIntentReviewSummary } from "../../data/reviewIntentSummary";
import type { ReviewLaneSummary } from "../../data/reviewLane";
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

// The lane's entry count, or nothing: `null` for zero of both, a pending read and a dataset comparison.
function attributionText(attribution: ReviewLaneSummary): string | null {
  if (attribution.state === "partial") return "· attribution partial";
  if (attribution.state === "unavailable") return "· attribution unknown";
  const unexplained = attribution.unexplained ?? 0;
  const unknown = attribution.attribution_unknown ?? 0;
  if (unknown > 0) return `· ${unexplained} unexplained · ${unknown} unknown`;
  return unexplained > 0 ? `· ${unexplained} unexplained` : null;
}

function AttributionCount({ read }: { read: IntentSummaryRead }) {
  const attribution = read.phase === "loading" ? undefined : read.attribution;
  const text = attribution ? attributionText(attribution) : null;
  if (!attribution || text === null) return null;
  return (
    <span
      className={changeSetCounts}
      data-testid="intent-review-attribution"
      data-attribution-state={attribution.state}
      data-unexplained={attribution.unexplained}
      data-unknown={attribution.attribution_unknown}
    >
      {text}
    </span>
  );
}

function AttributionDetails({ read }: { read: IntentSummaryRead }) {
  const attribution = read.phase === "loading" ? undefined : read.attribution;
  if (!attribution || attribution.state === "counted") return null;
  const scope = attribution.unmeasured.length
    ? ` Not measured: ${attribution.unmeasured.join(", ")}.`
    : "";
  return (
    <EntryStateDetails testId="intent-review-attribution-details" label="Attribution">
      {attribution.state === "partial"
        ? `The change set was measured with some paths not reportable whole, so no unexplained count is given. ${attribution.detail ?? ""}${scope}`
        : `The attribution of the changed files could not be measured: ${attribution.detail ?? "no reason was given"}.`}
    </EntryStateDetails>
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
        <AttributionCount read={read} />
      </button>
      <IntentDetails read={read} />
      <AttributionDetails read={read} />
    </>
  );
}
