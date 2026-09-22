// One listed source entry, opened into the actual content of both bound code trees (ICR-R03).
//
// The Source pane lists what a task changed. This module is what one of those rows opens *into*: the
// complete available text of the file at the recorded base tree and at the captured candidate tree,
// drawn by the shipped `DiffPane` (the same diff engine the change-set viewer uses), plus each
// side's own state and exact object identity.
//
// The rendering rules are the packet's, and they are decided by each side's declared `state` and
// never by inspecting its text:
//
//   * both sides `present`  -> the shipped two-sided diff over the two files' own bytes.
//   * otherwise             -> both sides' state lines, a note that no diff is claimed, and each
//                              textual side drawn as content (`FilePane`). A symlink's target is
//                              content, a binary side is not text at all, and an `absent` side is a
//                              measured fact about that endpoint rather than an empty file -- so an
//                              edit must not be claimed from a pairing that is not two documents.
//   * neither side textual  -> the state lines alone (binary, submodule, unavailable, absent).
//
// The generation is an input, not a lookup: `beforeCodeTreeId`/`afterCodeTreeId` are the ids the
// inventory published to this client and are sent back with every expansion, so the text under a row
// belongs to the generation the reader was looking at even after the branch moved. `currentness`
// states whether that generation is still the leaf's, and a superseded read says so rather than
// silently showing newer bytes. When the requested generation could not be measured at all, the read
// is bounded by the change set this leaf's review publishes and `path_bound` says so -- the row is
// still a changed path of a measured pair, and the pane names which one.
//
// Two answer shapes reach this pane and both are rendered with the owner's own fields (ICR-R16). A
// refusal this route *admits* arrives as its typed result and is rendered below by this module's own
// `refusalBlock`. Everything else -- an unwired process, a query the route does not admit, a socket
// that never answered -- reaches the shared review failure and is rendered by `ReviewProblemBlock`,
// the same renderer the surface uses, so this pane shows the code, reason, offending input and next
// action instead of only the thrown message.

import { useEffect, useState } from "react";

import type {
  ReviewChangedFile,
  ReviewFailure,
  ReviewRefusal,
  ReviewSourceContentResult,
  ReviewSourceExpansion,
  ReviewSourceSide,
} from "../../data/review";
import { reviewProblemFromCause, reviewSourceContent } from "../../data/review";
import { ReviewProblemBlock } from "./ReviewOutcome";
import { DiffPane } from "../changeset/DiffPane";
import { FilePane } from "../file-viewer/FilePane";

// The two states whose content is text a reader can be shown: a regular file's bytes, and a
// symlink's target.
const textual = (side: ReviewSourceSide) => side.text !== undefined;

const mutedStyle = { color: "muted", margin: "0.2rem 0" } as const;

// One side's own state, with the identity the server measured for it. The long detail is drawn
// whenever the state is not a complete, untruncated text -- `present` content that is whole needs no
// explanation, and every other state is exactly the thing a reader has to be told about.
function sideLine(side: ReviewSourceSide, name: "before" | "after") {
  const facts = [
    side.object_id ? `object ${side.object_id}` : null,
    side.byte_length === undefined ? null : `${side.byte_length} byte(s)`,
  ].filter((fact): fact is string => fact !== null);
  const needsDetail = side.state !== "present" || side.truncated;
  return (
    <p
      style={mutedStyle}
      data-testid={`review-source-${name}-state`}
      data-side-state={side.state}
      data-side-truncated={side.truncated ? "true" : "false"}
    >
      {name} ({side.state}){facts.length ? ` · ${facts.join(" · ")}` : ""}
      {needsDetail ? ` — ${side.detail}` : ""}
    </p>
  );
}

function contentBlock(side: ReviewSourceSide, name: "before" | "after", language: string) {
  return (
    <div data-testid={`review-source-${name}-content`} style={{ minHeight: "8rem" }}>
      <FilePane content={side.text ?? ""} language={language} />
    </div>
  );
}

function Sides({ expansion }: { expansion: ReviewSourceExpansion }) {
  const { before, after } = expansion;
  const lines = (
    <>
      {sideLine(before, "before")}
      {sideLine(after, "after")}
    </>
  );
  if (before.state === "present" && after.state === "present") {
    return (
      <>
        {lines}
        <div style={{ minHeight: "10rem" }}>
          <DiffPane
            before={before.text ?? ""}
            after={after.text ?? ""}
            language={expansion.language}
            mode="split"
            collapse={false}
          />
        </div>
      </>
    );
  }
  if (!textual(before) && !textual(after)) return lines;
  return (
    <>
      {lines}
      <p style={mutedStyle} data-testid="review-source-no-diff-claimed">
        no diff is drawn: at least one side is not a regular file's text, so an addition or a change
        cannot be claimed from the two sides beside it.
      </p>
      {textual(before) ? contentBlock(before, "before", expansion.language) : null}
      {textual(after) ? contentBlock(after, "after", expansion.language) : null}
    </>
  );
}

function boundedNote(expansion: ReviewSourceExpansion) {
  const truncated = (["before", "after"] as const).filter((name) => expansion[name].truncated);
  if (!truncated.length) return null;
  return (
    <p style={mutedStyle} data-testid="review-source-truncated">
      bounded expansion: the {truncated.join(" and ")} text above is a stated prefix of the object,
      not the whole of it; the object's exact identity and size are named on its own line and the
      reproduction below reaches the rest.
    </p>
  );
}

function refusalBlock(refusal: ReviewRefusal) {
  return (
    <div data-testid="review-source-refusal">
      <p style={{ margin: "0.2rem 0" }}>
        this entry's content could not be opened ({refusal.code}): {refusal.detail}
      </p>
      <p style={mutedStyle}>next: {refusal.next_action}</p>
      {refusal.offending_input ? (
        <p style={mutedStyle}>offending input: {refusal.offending_input}</p>
      ) : null}
    </div>
  );
}

function Expansion({ expansion }: { expansion: ReviewSourceExpansion }) {
  return (
    <div data-testid="review-source-expansion" data-currentness={expansion.currentness}>
      <p style={mutedStyle} data-testid="review-source-currentness">
        {expansion.currentness}: {expansion.currentness_detail}
      </p>
      <p style={mutedStyle} data-testid="review-source-generation">
        generation {expansion.before_code_tree_id} → {expansion.after_code_tree_id} · {expansion.status}
        {expansion.mode_change ? " · mode changed" : ""}
      </p>
      {expansion.path_bound === "leaf_change_set" ? (
        <p style={mutedStyle} data-testid="review-source-path-bound">
          path admitted by: {expansion.path_bound_detail}
        </p>
      ) : null}
      <Sides expansion={expansion} />
      {boundedNote(expansion)}
      <p style={{ ...mutedStyle, whiteSpace: "pre-wrap" }} data-testid="review-source-command">
        reproduce: {expansion.command}
      </p>
    </div>
  );
}

export function SourceContent({
  repo,
  master,
  leaf,
  entry,
  beforeCodeTreeId,
  afterCodeTreeId,
}: {
  repo: string;
  master: string;
  leaf: string;
  entry: ReviewChangedFile;
  beforeCodeTreeId: string;
  afterCodeTreeId: string;
}) {
  const [result, setResult] = useState<ReviewSourceContentResult | null>(null);
  // The transport-level failure (ICR-R16): this route answers a refusal it *admits* as its typed
  // result -- rendered below, unchanged -- and everything else (an unwired process, an unadmitted
  // query, a socket that never answered) as the shared review failure, which is rendered by the same
  // `ReviewProblemBlock` the surface uses so the code, reason, offending input and next action are
  // all shown here too. The pre-existing rendering printed only the thrown message, which for a 503
  // read "503 unavailable" and dropped the adapter's own instruction.
  const [problem, setProblem] = useState<ReviewFailure | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setResult(null);
    setProblem(null);
    reviewSourceContent(
      repo,
      master,
      leaf,
      entry.path,
      beforeCodeTreeId,
      afterCodeTreeId,
    )
      .then((opened) => {
        if (live) setResult(opened);
      })
      .catch((cause: unknown) => {
        if (live) setProblem(reviewProblemFromCause(cause));
      });
    return () => {
      live = false;
    };
  }, [repo, master, leaf, entry.path, beforeCodeTreeId, afterCodeTreeId, attempt]);

  if (problem !== null) {
    return (
      <ReviewProblemBlock
        origin="failure"
        subject="this entry's content"
        problem={problem}
        onRetry={() => setAttempt((previous) => previous + 1)}
      />
    );
  }
  if (result === null) {
    return (
      <p style={mutedStyle} data-testid="review-source-loading">
        opening {entry.path} at the listed generation…
      </p>
    );
  }
  if (result.state === "refused" && result.refusal) return refusalBlock(result.refusal);
  if (result.expansion) return <Expansion expansion={result.expansion} />;
  return null;
}
