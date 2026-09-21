// The statement area of pane 1: the two recorded operands, and the state of each side.
//
// R06 (one-sided knowledge statements). A knowledge addition or removal used to disappear here: the
// pane drew the shipped diff only when *both* sides were `present`, and the side line returned null
// for the side that *was* present, so a full after statement and a full before statement were both
// dropped and the reader saw two state lines and no text at all. The rule this module implements is
// the packet's: render the complete available statement beside the explicit, named absence.
//
// Every branch is decided by a side's declared `state`, never by inspecting its text:
//
//   * both sides `present`      -> the shipped two-sided diff, exactly as before.
//   * one `present`, other `absent` -> a one-sided diff. This is the diff engine's own shape for an
//     addition or a removal (`a` empty = every line added; `b` empty = every line removed), and the
//     absent side is named as absent in its own line above it, so an empty column is a stated fact
//     and never a blank a reader has to interpret.
//   * one `present`, other `binary`/`unresolved` -> the available side's text as content, with the
//     unavailable side's own reason beside it, and NO diff. A diff here would claim the other
//     operand is a known-empty document; the server distinguished `absent` from an unreadable side
//     precisely so this rendering could not make that claim, and an empty-string operand is what the
//     packet forbids (`ICR-R06@v1`, Failure And Recovery Behavior).
//   * neither side `present`    -> both sides' own state lines (the task-context pane, where no
//     subject was compared and neither side may read as an empty operand).
//
// The text is never re-typed here: a present side's `text` is what the server recorded, and a side
// that is not present carries no text at all (the model refuses one that does), so nothing in this
// module can turn a missing operand into an empty document.

import type { ReviewSideContent } from "../../data/review";
import { DiffPane } from "../changeset/DiffPane";
import { FilePane } from "../file-viewer/FilePane";

// The one unavailable-content pair: the side holds no renderable text and says why. It is not a
// known absence, and the difference is the whole point of rendering it on its own line.
const unavailable = (state: ReviewSideContent["state"]) => state === "binary" || state === "unresolved";

const sideLine = (side: ReviewSideContent, name: "before" | "after") => (
  <p
    style={{ color: "muted", margin: "0.2rem 0" }}
    data-testid={`review-${name}-state`}
    data-side-state={side.state}
  >
    {name} ({side.state}): {side.detail}
  </p>
);

// The two-sided statement area, unchanged by R06: the shipped diff over both recorded operands, and
// the before side's declared language exactly as this pane passed it before the one-sided paths
// existed. (The paths below, which this leaf adds, take the language of the side whose text they
// actually draw -- there is no pre-existing choice to preserve there.)
function bothPresent(before: ReviewSideContent, after: ReviewSideContent) {
  return (
    <DiffPane
      before={before.text ?? ""}
      after={after.text ?? ""}
      language={before.language}
      mode="split"
      collapse={false}
    />
  );
}

// A one-sided diff: the present operand on its own side, the known-absent side genuinely empty. The
// caller renders `sideLine` for both sides beside this, so "empty because nothing was recorded" is
// said in words and is not something the reader infers from the diff's blank half. The language is
// the drawn operand's own, because that operand is the only text in the pane.
function oneSidedDiff(before: ReviewSideContent, after: ReviewSideContent) {
  const present = before.state === "present" ? before : after;
  return (
    <DiffPane
      before={before.state === "present" ? (before.text ?? "") : ""}
      after={after.state === "present" ? (after.text ?? "") : ""}
      language={present.language}
      mode="split"
      collapse={false}
    />
  );
}

// The available operand with an unreadable opposite: drawn as content, not as a diff.
function availableContent(before: ReviewSideContent, after: ReviewSideContent) {
  const present = before.state === "present" ? before : after;
  return (
    <>
      <p style={{ color: "muted", margin: "0.2rem 0" }} data-testid="review-no-diff-claimed">
        no diff is drawn: the other side is not a known-empty operand, so an addition or a removal
        cannot be claimed from it.
      </p>
      <FilePane content={present.text ?? ""} language={present.language} />
    </>
  );
}

export function KnowledgeStatements({
  before,
  after,
}: {
  before: ReviewSideContent;
  after: ReviewSideContent;
}) {
  const present = [before.state, after.state].filter((state) => state === "present").length;
  if (present === 2) return bothPresent(before, after);
  const lines = (
    <>
      {sideLine(before, "before")}
      {sideLine(after, "after")}
    </>
  );
  if (present === 0) return lines;
  const other = before.state === "present" ? after : before;
  return (
    <>
      {lines}
      {unavailable(other.state)
        ? availableContent(before, after)
        : oneSidedDiff(before, after)}
    </>
  );
}
