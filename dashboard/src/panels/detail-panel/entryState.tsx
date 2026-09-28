// The brief-status-plus-disclosure pattern the task entry's controls share (ICR-R16).
//
// A refusal or an unrecorded range is ANSWERED, never swallowed: its code and the owner's own reason
// stay on the page. But the owner's reason is a paragraph, and printing it inside and beside every
// entry control is what turned the task header into a wall of backend prose. So a control carries a
// one- or two-word state, and the whole explanation sits in a `<details>` beside it -- outside the
// button, because interactive content inside a button is not a disclosure a keyboard can open.
import type { ReactNode } from "react";

import type { ReviewFailure } from "../../data/review";
import { entryStateBody, entryStateDetails, entryStateSummary } from "./styles";

export function EntryStateDetails({
  testId,
  label,
  children,
}: {
  testId: string;
  // What the disclosure explains, for the summary's accessible name ("committed: why").
  label: string;
  children: ReactNode;
}) {
  return (
    <details className={entryStateDetails} data-testid={testId}>
      <summary className={entryStateSummary} aria-label={`${label}: details`} title="details">
        ?
      </summary>
      <p className={entryStateBody}>{children}</p>
    </details>
  );
}

// A failure's brief state, as the control shows it. The token is the shared classification, so the
// entry and the review surface cannot come to call the same code two different things.
export function briefProblem(problem: ReviewFailure): string {
  switch (problem.token) {
    case "not-initialized":
      return "no knowledge yet";
    case "network":
      return "offline";
    case "unreadable":
      return "unreadable";
    case "not-found":
      return "not found";
    default:
      return "unavailable";
  }
}

// A failure's whole explanation, in the owner's own words: its code, its reason, the input it named
// and the next action it published.
export function problemSentence(problem: ReviewFailure): string {
  return [
    `${problem.code}: ${problem.detail}`,
    problem.offendingInput ? `offending input: ${problem.offendingInput}` : "",
    problem.nextAction ? `next: ${problem.nextAction}` : "",
  ]
    .filter(Boolean)
    .join(" — ");
}
