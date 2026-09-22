// The Intent Reviewer surface: three panes over one comparison, and every state that is not a review.
//
// The surface is display-only. It renders records other owners store, carries every attribution it
// was given, and produces no conclusion of its own: there is no summary, no severity, no score and
// no control that writes anything. The two renderers it reuses are fed by other owners: `DiffPane`
// for the statements the comparison published -- both operands when both sides recorded one, and the
// available operand beside the named absence when one side did not (R06 -- `KnowledgeStatements` owns
// that rule) -- and the Source pane's own entry expansion (R03 -- `SourceContent` owns reading one
// listed entry's content at the two bound code trees the inventory published).
//
// THE READ'S OUTCOMES (ICR-R16). This route answers with its typed result and maps a refusal onto a
// 400/404/503 status with the refusal in the body, so the surface must read the *body* whatever the
// status (data/reviewTransport.ts owns that decode). Its read therefore has four phases, and each is
// rendered as itself: `loading` while the request is in flight, `reviewed` (with the known-empty note
// when the answer measured nothing), `refused` for the owner's typed refusal, and `failed` for a
// transport-level failure (an unwired adapter, an unadmitted input, no HTTP response at all). Every
// non-review phase goes through `ReviewOutcome.tsx`, which carries the server's own code, reason,
// offending input and next action -- so an actionable refusal is visible instead of "404 Not Found".
//
// A failed read never erases the last coherent comparison this surface read: the retained payload is
// still shown, labelled as the last generation read, and no empty review is ever claimed for it. A
// retained comparison is stored and shown with the question it was read for (`targetKeyOf`), so a
// payload is never rendered under a header it was not read for. The only control offered beside a
// refusal is the same task's source change inventory, asked as its own question (the task-context
// route needs no knowledge dataset); the client names no dataset either way, and the refusal's
// reason, offending input and next action stay on screen while that inventory is shown.

import { useCallback, useEffect, useState } from "react";

import type {
  ReviewAssessmentDisplay,
  ReviewAuthoredEffect,
  ReviewChangedFile,
  ReviewCollectionPage,
  ReviewFailure,
  ReviewKnowledgePane,
  ReviewPagedCollection,
  ReviewPayload,
  ReviewSelectorKind,
  ReviewSignal,
  ReviewSourceInventory,
  ReviewUnresolvedReference,
  ReviewUnrepresentablePath,
} from "../../data/review";
import {
  REVIEW_PAGED_COLLECTIONS,
  carriedPage,
  continuationOf,
  intentOnlyRefusal,
  intentReview,
  pageBounds,
  reviewProblemFromCause,
} from "../../data/review";
import type { ReviewRefusal } from "../../data/review";
import { KnowledgeStatements } from "./KnowledgeStatements";
import { SourceContent } from "./SourceContent";
import {
  type ReviewRead,
  ReviewOutcomeRegion,
  problemOf,
  readFrom,
  shownPayload,
} from "./ReviewOutcome";

export interface ReviewTarget {
  repo: string;
  master: string;
  leaf: string;
  // The reviewed subject, when the entry carried one. Absent, this is the TASK-CONTEXT review: the
  // surface asks the server for the task's own comparison and renders the complete source change
  // inventory, which is what a task with no recorded invariant -- or no datasets yet -- still has.
  selectorKind?: ReviewSelectorKind;
  selectorId?: string;
}

const TAKEOVER = "changeset-viewer";

const pane = (title: string, children: React.ReactNode) => (
  <section style={{ marginBottom: "1.25rem" }} data-pane={title}>
    <h3 style={{ margin: "0 0 0.4rem" }}>{title}</h3>
    {children}
  </section>
);

const muted = (text: string, testid?: string) => (
  <p style={{ color: "muted", margin: "0.2rem 0" }} data-testid={testid}>
    {text}
  </p>
);

const attribution = (author?: string, inputs: string[] = []) =>
  author === undefined
    ? `author: unresolved reference${inputs.length ? ` · inputs: ${inputs.join(", ")}` : ""}`
    : `author: ${author}${inputs.length ? ` · inputs: ${inputs.join(", ")}` : ""}`;

const unresolvedList = (entries: ReviewUnresolvedReference[]) =>
  entries.length ? (
    <ul style={{ margin: "0.2rem 0 0.6rem", paddingLeft: "1.1rem" }} data-testid="review-unresolved">
      {entries.map((entry, index) => (
        <li key={`${entry.field}:${entry.recorded_reference ?? index}`}>
          unresolved {entry.field}
          {entry.recorded_reference ? ` (${entry.recorded_reference})` : ""}: {entry.detail}
        </li>
      ))}
    </ul>
  ) : null;

// One mechanical field transition, and the three different facts its two values can be. A value the
// server did not send is the recorded fact that the field was absent on that side; a value that IS
// there and is empty is a recorded empty list, which is not the same fact and is not printed as a
// blank either. `(absent)` and `(recorded empty)` are the two words, so no row is ever silently
// blank and no reader has to decide which of the two a gap meant.
const fieldValue = (value?: string) =>
  value === undefined ? "(absent)" : value === "" ? "(recorded empty)" : value;

function assessmentBlock(entry: ReviewAssessmentDisplay) {
  return (
    <li key={entry.assessment_id} data-testid="review-assessment" data-binding={entry.binding_state}>
      <strong>{entry.disposition}</strong> · {entry.finding}
      <div style={{ color: "muted" }}>{entry.rationale}</div>
      <div style={{ color: "muted" }}>
        {attribution(entry.author_ref, entry.examined_inputs)} · binding: {entry.binding_state}
        {entry.role_ref ? ` · role: ${entry.role_ref}` : ""}
      </div>
    </li>
  );
}

function authoredEffect(effect: ReviewAuthoredEffect) {
  return (
    <li key={`${effect.record_kind}:${effect.record_id}`} data-testid="review-authored-effect">
      <strong>{effect.record_kind}</strong>
      {effect.label ? ` · ${effect.label}` : ""} · {effect.record_id}
      {effect.rationale ? <div>{effect.rationale}</div> : null}
      <div style={{ color: "muted" }}>
        {attribution(effect.author_ref, effect.examined_inputs)}
      </div>
      {unresolvedList(effect.unresolved)}
    </li>
  );
}

function signalBlock(signal: ReviewSignal) {
  return (
    <li key={signal.signal_id} data-testid="review-signal">
      <strong>{signal.condition}</strong> · input set: {signal.input_set} · {signal.signal_id}
      <div style={{ color: "muted" }}>
        extractor: {signal.extractor_version} · policy: {signal.policy_version}
      </div>
      {signal.relationship_paths.length ? (
        <div style={{ color: "muted" }}>paths: {signal.relationship_paths.join(", ")}</div>
      ) : null}
      {signal.scope_limitations.length ? (
        <div style={{ color: "muted" }}>
          scope limitations: {signal.scope_limitations.join(", ")}
        </div>
      ) : null}
    </li>
  );
}

// The mechanical half of pane 1, extracted so the pane's own body reads as a composition: the
// essential conditions each side recorded, how many retained revisions the selection reached on each
// side, and every field transition the comparison itself reported.
function KnowledgeFacts({ knowledge }: { knowledge: ReviewKnowledgePane }) {
  const conditions = knowledge.before_conditions.length || knowledge.after_conditions.length;
  return (
    <>
      {conditions ? (
        <div style={{ color: "muted" }} data-testid="review-conditions">
          before conditions: {knowledge.before_conditions.join("; ") || "none recorded"} · after
          conditions: {knowledge.after_conditions.join("; ") || "none recorded"}
        </div>
      ) : null}
      <p style={{ margin: "0.4rem 0" }} data-testid="review-revision-groups">
        retained revisions —{" "}
        {knowledge.revision_groups
          .map((group) => `${group.side}:${group.record_id}=${group.selected_revision_count}`)
          .join(" · ") || "none selected"}
      </p>
      <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-field-changes">
        {knowledge.field_changes.map((change) => (
          <li key={`${change.item_id}:${change.field}`}>
            {change.field}: {fieldValue(change.before_value)} → {fieldValue(change.after_value)}
          </li>
        ))}
      </ul>
    </>
  );
}

// The two record collections the pane shows *beside* the mechanical diff, each in its own list and
// under its own heading: an authored effect is never rendered in the shape of a detection fact.
function AuthoredRecords({ knowledge }: { knowledge: ReviewKnowledgePane }) {
  return (
    <>
      <h4 style={{ margin: "0.6rem 0 0.2rem" }}>Authored effects and preservation claims</h4>
      {knowledge.authored_effects.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {knowledge.authored_effects.map(authoredEffect)}
        </ul>
      ) : (
        muted("No authored effect, preservation claim or unresolved question is recorded here.")
      )}
      <h4 style={{ margin: "0.6rem 0 0.2rem" }}>Detection signals (facts, not findings)</h4>
      {knowledge.signals.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {knowledge.signals.map(signalBlock)}
        </ul>
      ) : (
        muted("No detection signal was supplied to this rendering.")
      )}
    </>
  );
}

function KnowledgePane({ payload }: { payload: ReviewPayload }) {
  const { knowledge } = payload;
  return pane(
    "Knowledge",
    <>
      <div style={{ color: "muted" }} data-testid="review-selection">
        {payload.comparison
          ? `comparison: ${payload.comparison.reference} · policy ${payload.comparison.policy_version}`
          : `no knowledge comparison was made · ${knowledge.selection_detail ?? "no subject selected"}`}
      </div>
      <KnowledgeStatements before={knowledge.before_statement} after={knowledge.after_statement} />
      <KnowledgeFacts knowledge={knowledge} />
      <AuthoredRecords knowledge={knowledge} />
      {knowledge.assessments.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {knowledge.assessments.map(assessmentBlock)}
        </ul>
      ) : (
        muted("UNASSESSED — no assessment is recorded against this subject.", "review-unassessed")
      )}
      {unresolvedList(knowledge.unresolved)}
    </>,
  );
}

// One inventory entry. The path is printed exactly as the server published it -- a tab or a newline
// inside a name is part of the address -- and the status and renderability are printed beside it,
// because a path whose content cannot be rendered is still a change that must be listed.
//
// The entry is also the way into its own content (ICR-R03): opening it reads the file at the two
// code trees the inventory published, and the generation ids travel with the request, so a row
// opened after the branch moved still shows the generation the reader was looking at.
function inventoryEntry(
  entry: ReviewChangedFile,
  repo: string,
  master: string,
  leaf: string,
  generation: { before?: string; after?: string },
  open: string | null,
  onOpen: (path: string | null) => void,
) {
  const notes = [
    entry.mode_change ? "mode changed" : null,
    entry.content === "unknown" ? null : `content: ${entry.content}`,
    entry.detail ?? null,
  ].filter((note): note is string => note !== null);
  const expandable = generation.before !== undefined && generation.after !== undefined;
  const isOpen = open === entry.path;
  return (
    <li key={entry.path} data-testid="review-inventory-entry" data-status={entry.status}>
      {expandable ? (
        <button
          type="button"
          data-testid="review-inventory-open"
          data-path={entry.path}
          aria-expanded={isOpen}
          onClick={() => onOpen(isOpen ? null : entry.path)}
        >
          {isOpen ? "▾ " : "▸ "}
          {entry.path}
        </button>
      ) : (
        <code>{entry.path}</code>
      )}{" "}
      · {entry.status}
      {notes.length ? <span style={{ color: "muted" }}> · {notes.join(" · ")}</span> : null}
      {isOpen && expandable ? (
        <SourceContent
          repo={repo}
          master={master}
          leaf={leaf}
          entry={entry}
          beforeCodeTreeId={generation.before as string}
          afterCodeTreeId={generation.after as string}
        />
      ) : null}
    </li>
  );
}

// One changed path this surface cannot name as text, printed by its exact byte form. It is a change
// like any other: it is listed, its status is shown, and the reason it has no name is stated rather
// than left as a gap in a list that would otherwise look complete.
//
// It carries no expansion control, and says so: this vocabulary carries text, so the only spelling
// that could address the row's content cannot be expressed in a request (ICR-R03's boundary). The row
// is for identification -- a reader can act on the bytes beside it with Git directly -- and the pane
// must not imply that clicking it would open anything.
function byteNamedEntry(entry: ReviewUnrepresentablePath) {
  return (
    <li key={entry.path_bytes} data-testid="review-inventory-byte-path" data-status={entry.status}>
      <code>{entry.path_bytes}</code> · {entry.status}
      {entry.mode_change ? " · mode changed" : ""}
      <div style={{ color: "muted" }}>{entry.detail}</div>
      <div style={{ color: "muted" }} data-testid="review-byte-path-not-addressable">
        this row's content is not openable through this surface: its name is carried as bytes for
        identification, and no expansion request can name it.
      </div>
    </li>
  );
}

// The complete source change inventory of the comparison's bound pair. It is rendered in all three
// of its states and never as an empty list: a measured empty set says the two trees agree, an
// unavailable measurement says nothing was observed and why, and a partial one says which entries
// could not be classified or carried as names.
//
// Every entry is openable while the inventory named both of its code trees: those two ids are the
// generation the content is read at, and an inventory that named no pair has nothing to open.
function Inventory({
  inventory,
  repo,
  master,
  leaf,
}: {
  inventory: ReviewSourceInventory;
  repo: string;
  master: string;
  leaf: string;
}) {
  const byByteForm = inventory.unrepresentable_paths ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const generation = {
    before: inventory.before_code_tree_id,
    after: inventory.after_code_tree_id,
  };
  return (
    <div data-testid="review-inventory" data-inventory-state={inventory.state}>
      <p style={{ margin: "0.4rem 0" }}>
        source change inventory ({inventory.state}
        {inventory.partial ? ", partial" : ""}): {inventory.listed_total} listed path(s)
        {byByteForm.length ? ` + ${byByteForm.length} by byte form` : ""} — {inventory.detail}
      </p>
      {inventory.entries.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {inventory.entries.map((entry) =>
            inventoryEntry(entry, repo, master, leaf, generation, open, setOpen),
          )}
        </ul>
      ) : null}
      {byByteForm.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }}>
          {byByteForm.map(byteNamedEntry)}
        </ul>
      ) : null}
      <p style={{ color: "muted", margin: "0.2rem 0", fontSize: "0.8rem" }}>
        reproduce: {inventory.command}
        {inventory.before_code_tree_id && inventory.after_code_tree_id
          ? ` · ${inventory.before_code_tree_id} → ${inventory.after_code_tree_id}`
          : ""}
      </p>
    </div>
  );
}

function SourcePane({
  payload,
  repo,
  master,
  leaf,
}: {
  payload: ReviewPayload;
  repo: string;
  master: string;
  leaf: string;
}) {
  const { source } = payload;
  return pane(
    "Source",
    <>
      <Inventory
        inventory={source.inventory}
        repo={repo}
        master={master}
        leaf={leaf}
      />
      <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-locations">
        {source.locations.map((location) => (
          <li key={`${location.claim_id}:${location.path}`} data-change-state={location.change_state}>
            {location.path} · role: {location.role ?? "unclassified (no role recorded)"}
            {location.before_only ? " · before-only" : ""} · {location.change_state} ·{" "}
            {location.resolution}
            {location.rationale ? <div style={{ color: "muted" }}>{location.rationale}</div> : null}
          </li>
        ))}
      </ul>
      <p style={{ margin: "0.4rem 0" }} data-testid="review-remaining">
        {source.remaining
          .map((count) =>
            count.value === undefined
              ? `${count.name}: not measured (${count.reason ?? "no reason recorded"})`
              : `${count.name}: ${count.value}`,
          )
          .join(" · ")}
      </p>
      {source.unattributed_changed_paths.length ? (
        <p style={{ margin: "0.2rem 0" }} data-testid="review-unattributed">
          changed paths with no registered attribution: {source.unattributed_changed_paths.join(", ")}
        </p>
      ) : null}
      {source.expansion_reference ? (
        <p style={{ color: "muted", margin: "0.2rem 0" }} data-testid="review-expansion">
          full selected-candidate diff: {source.expansion_reference}
          {source.expansion_command ? ` — ${source.expansion_command}` : ""}
        </p>
      ) : (
        muted("The comparison published no source expansion for this selection.")
      )}
      {unresolvedList(source.unresolved)}
    </>,
  );
}

function EvidencePane({ payload }: { payload: ReviewPayload }) {
  const { evidence } = payload;
  return pane(
    "Evidence and assessment",
    <>
      {evidence.evidence_state === "recorded" ? (
        <>
          <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-evidence">
            {evidence.evidence_links.map((link) => (
              <li key={link.claim_id}>
                evidence claim {link.claim_id}
                {link.assessment_refs.length
                  ? ` · assessments: ${link.assessment_refs.join(", ")}`
                  : ""}
                {unresolvedList(link.unresolved)}
              </li>
            ))}
          </ul>
          <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-observations">
            {evidence.observations.map((observation) => (
              <li key={observation.observation_id}>
                observation {observation.observation_id} · result: {observation.execution_result}
                <div style={{ color: "muted" }}>
                  candidate: {observation.tested_candidate ?? "not recorded"} · command:{" "}
                  {observation.command_identity ?? "not recorded"} · artifact:{" "}
                  {observation.result_artifact_ref ?? "not recorded"} (
                  {observation.result_artifact_digest ?? "no digest"}) · environment:{" "}
                  {observation.environment_identity ?? "not recorded"}
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : (
        muted("No recorded evidence links", "review-no-evidence")
      )}
      {evidence.source_inspection_available
        ? muted("Source-based inspection remains available in the Source pane.")
        : null}
      {evidence.assessments.length ? (
        <ul style={{ margin: "0.2rem 0", paddingLeft: "1.1rem" }} data-testid="review-assessments">
          {evidence.assessments.map(assessmentBlock)}
        </ul>
      ) : (
        muted("UNASSESSED — no assessment is recorded against this subject.", "review-unassessed")
      )}
      {unresolvedList(evidence.unresolved)}
    </>,
  );
}

function SubmissionBlock({ payload }: { payload: ReviewPayload }) {
  const { submission, staleness } = payload;
  return (
    <section style={{ marginBottom: "1.25rem" }} data-testid="review-submission">
      {staleness.state === "stale" ? (
        <p style={{ margin: "0.2rem 0" }} data-testid="review-stale">
          {staleness.statement} — previous input: {staleness.previous_comparison_ref}
        </p>
      ) : null}
      <p style={{ color: "muted", margin: "0.2rem 0" }} data-submission-state={submission.state}>
        assessment submission: {submission.state === "disabled_stale" ? "DISABLED" : "not offered"} —{" "}
        {submission.reason}
      </p>
      <p style={{ color: "muted", margin: "0.2rem 0" }}>next: {submission.next_action}</p>
      <p style={{ color: "muted", margin: "0.2rem 0" }}>
        dispositions the existing authority accepts: {submission.proposed_dispositions.join(", ")} —
        none is publication approval.
      </p>
    </section>
  );
}

// The surface's read phases and the rendering of every phase that is not panes live in
// `ReviewOutcome.tsx` (one owner for the outcome states). What stays here is how a target and a read
// phase become the controls' wiring.

function subjectLabel(
  selectorKind: ReviewSelectorKind | undefined,
  selectorId: string | undefined,
  instead: ReviewFailure | null,
): string {
  if (instead !== null) return "whole task (no subject selected)";
  if (selectorKind && selectorId) return `${selectorKind} ${selectorId}`;
  return "whole task (no subject selected)";
}

// The retry control belongs to the state that has no owner-published recovery route: a read that
// never reached the server. A typed refusal keeps its own next action instead.
const retryFor = (read: ReviewRead, load: () => Promise<void>) =>
  read.phase === "failed" ? () => void load() : undefined;

// The source-inventory offer belongs to a refusal that answers for the intent half alone, when the
// reader has not already taken it.
function insteadFor(
  read: ReviewRead,
  problem: ReviewFailure | null,
  instead: ReviewFailure | null,
  offer: (problem: ReviewFailure) => void,
): (() => void) | undefined {
  if (read.phase !== "refused" || problem === null || instead !== null) return undefined;
  if (!intentOnlyRefusal(problem.code)) return undefined;
  return () => offer(problem);
}

// The identity one read answers for: the task context AND the question asked of it. A comparison is
// only ever shown under the header it was read for, so this key is what a retained generation is
// stored with and checked against -- a payload read for another target is never rendered under this
// one's header, whatever a caller does to the props.
function targetKeyOf(
  repo: string,
  master: string,
  leaf: string,
  instead: ReviewFailure | null,
  selectorKind?: ReviewSelectorKind,
  selectorId?: string,
  page?: ReviewPageRequest,
): string {
  const question = instead !== null ? "task-context" : `${selectorKind ?? ""}:${selectorId ?? ""}`;
  // The page is part of the question, not a decoration on it: page 3 of one collection is a
  // different answer from page 1 of it, and a retained page must never be rendered under another
  // page's header.
  const position =
    page === undefined
      ? "whole"
      : `${page.of}:${page.continuation === undefined || page.continuation === null ? "first" : page.continuation}`;
  return `${repo}/${master}/${leaf}/${question}/${position}`;
}

// Which bounded collection this surface is paging, and at which cursor. `undefined` is the whole
// review -- the shape every read had before paging existed -- and it is what the "first page" control
// returns to rather than a cursor that would re-present the page already on screen.
interface ReviewPageRequest {
  of: ReviewPagedCollection;
  continuation?: string | null;
}

// The page control (ICR-R10): the active collection, the bounds and scope of the page on screen, and
// the one action that reaches the rest of it.
//
// "next page" is rendered exactly when the response published a remainder *and* the owner's cursor
// for it, and it sends that cursor back unchanged. A body that reported rows remaining without a
// cursor therefore offers no control rather than a button that would fetch nothing -- the
// non-conformance this packet names ("remaining=100 but no way to inspect them") is unrepresentable
// in the rendering, not merely discouraged. A page whose cursor was reset states the refusal and
// offers the first page of the comparison that is there now, so a moved generation is a stated
// action rather than a dead end.
// The refusal a *requested* page earned when the owner could not serve it (ICR-R10). A page value
// carries the owner's own counts and a refused read has none, so the server states the refusal
// instead -- and this renders it in full, with the code, the offending cursor and the owner's two
// identities, plus the one action that reaches the collection: its first page. Rendering "no page"
// here instead was a false sentence ("nothing remains to reach") over a request the reader had
// explicitly made, which is the shape this packet exists to remove.
function PageRefusalBlock({
  refusal,
  collection,
  onSelect,
}: {
  refusal: ReviewRefusal;
  collection: ReviewPagedCollection;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return (
    <section data-testid="review-page-refusal" data-page-refusal-code={refusal.code}>
      <p style={{ margin: "0.3rem 0" }}>
        {collection} could not be served as a page — {refusal.code}: {refusal.detail}
      </p>
      {refusal.expected !== undefined ? (
        <p style={{ margin: "0.2rem 0", color: "muted" }} data-testid="review-page-refusal-expected">
          expected: {refusal.expected}
        </p>
      ) : null}
      {refusal.observed !== undefined ? (
        <p style={{ margin: "0.2rem 0", color: "muted" }} data-testid="review-page-refusal-observed">
          observed: {refusal.observed}
        </p>
      ) : null}
      <p style={{ margin: "0.2rem 0" }} data-testid="review-page-refusal-action">
        {refusal.next_action}
      </p>
      <button
        type="button"
        data-testid="review-page-refusal-first-page"
        onClick={() => onSelect({ of: collection })}
      >
        first page of {collection}
      </button>
    </section>
  );
}

// The collection picker: which bounded collection the next read is a page of. Choosing one is a new
// question rather than a continuation, so it always starts at that collection's first page, and
// "whole review" is the state every read had before paging existed.
function PagePicker({
  selection,
  onSelect,
}: {
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  return (
    <span style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center" }}>
      <label style={{ color: "muted" }} htmlFor="review-page-collection">
        page over
      </label>
      <select
        id="review-page-collection"
        data-testid="review-page-collection"
        value={selection?.of ?? ""}
        onChange={(event) =>
          onSelect(
            event.target.value === ""
              ? undefined
              : { of: event.target.value as ReviewPagedCollection },
          )
        }
      >
        <option value="">whole review</option>
        {REVIEW_PAGED_COLLECTIONS.map((collection) => (
          <option key={collection} value={collection} data-testid="review-page-option">
            {collection}
          </option>
        ))}
      </select>
    </span>
  );
}

// The two reachable actions beside the bounds: advance with the cursor the server published, or
// restart this collection at its first page. Neither is offered unless it can do what it says --
// "next page" needs a published cursor, and "first page" needs a collection already being paged.
function PageActions({
  next,
  page,
  selection,
  onSelect,
}: {
  next: { of: ReviewPagedCollection; continuation: string } | null;
  page: ReviewCollectionPage | null | undefined;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  const paging = page !== null && page !== undefined && selection !== undefined;
  return (
    <>
      {next ? (
        <button
          type="button"
          data-testid="review-next-page"
          data-continuation={next.continuation}
          onClick={() => onSelect({ of: next.of, continuation: next.continuation })}
        >
          next page →
        </button>
      ) : null}
      {paging ? (
        <button
          type="button"
          data-testid="review-first-page"
          onClick={() => onSelect({ of: page.collection })}
        >
          first page
        </button>
      ) : null}
    </>
  );
}

// The refusal a requested page earned, when there is one: a page was asked for, the response carried
// no page, and the payload states why. It is one value so the three render branches below cannot
// disagree about whether a refusal is on screen.
function refusedPageOf(
  payload: ReviewPayload,
  selection: ReviewPageRequest | undefined,
): { refusal: ReviewRefusal; collection: ReviewPagedCollection } | null {
  // `carriedPage` collapses the two spellings of "no page": the server omits the key (its serializer
  // excludes None) while a hand-written body may send `null`. Comparing against `null` alone skipped
  // this whole branch for every real refused page, which is how the round-1 false sentence survived
  // its own case -- that case handed the component a `page: null` the route never sends.
  if (selection === undefined || carriedPage(payload) !== null) return null;
  const refusal = payload.page_refusal;
  return refusal === undefined || refusal === null
    ? null
    : { refusal, collection: selection.of };
}

// What the control says about the answer's shape: the page's own bounds, or -- when a page was asked
// for and refused -- nothing at all, because the bounds sentence describes a page and there is none.
function PageBoundsLine({
  payload,
  page,
  refused,
}: {
  payload: ReviewPayload;
  page: ReviewCollectionPage | null | undefined;
  refused: boolean;
}) {
  if (refused) return null;
  if (page) {
    return (
      <p style={{ margin: "0.3rem 0" }} data-testid="review-page-bounds">
        {pageBounds(payload)}
      </p>
    );
  }
  return (
    <p style={{ margin: "0.3rem 0" }} data-testid="review-page-bounds">
      whole review — no bounded collection was paged, so there is no remainder to reach
    </p>
  );
}

function PageControls({
  payload,
  selection,
  onSelect,
}: {
  payload: ReviewPayload;
  selection: ReviewPageRequest | undefined;
  onSelect: (page: ReviewPageRequest | undefined) => void;
}) {
  const page = carriedPage(payload);
  const next = continuationOf(payload);
  const reset = page?.reset ?? null;
  const refused = refusedPageOf(payload, selection);
  return (
    <section
      data-testid="review-page-controls"
      data-page-collection={page?.collection ?? ""}
      data-page-requested={selection?.of ?? ""}
      data-page-refused={refused === null ? "false" : "true"}
    >
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
        <PagePicker selection={selection} onSelect={onSelect} />
        <PageActions next={next} page={page} selection={selection} onSelect={onSelect} />
      </div>
      {refused === null ? null : (
        <PageRefusalBlock
          refusal={refused.refusal}
          collection={refused.collection}
          onSelect={onSelect}
        />
      )}
      <PageBoundsLine payload={payload} page={page} refused={refused !== null} />
      {reset ? (
        <p style={{ margin: "0.3rem 0" }} data-testid="review-page-reset">
          {reset.code}: {reset.detail} — {reset.next_action}
        </p>
      ) : null}
    </section>
  );
}

export function ReviewSurface({
  repo,
  master,
  leaf,
  selectorKind,
  selectorId,
  onBack,
}: ReviewTarget & { onBack: () => void }) {
  const [read, setRead] = useState<ReviewRead>({ phase: "loading" });
  // The last comparison this surface really read, with the identity it was read for. A read that
  // fails without an answer must not erase it: it stays on screen, labelled, and the failure is
  // stated beside it -- but only while the surface is still asking that same question.
  const [retained, setRetained] = useState<{ key: string; payload: ReviewPayload } | null>(null);
  // The refusal the reader answered by asking for the task's own source inventory instead. It is a
  // second, explicitly asked question -- never an automatic substitution -- so the refusal stays on
  // screen while its answer is shown.
  const [instead, setInstead] = useState<ReviewFailure | null>(null);
  // Which bounded collection this surface is paging and at which cursor (ICR-R10). It is part of the
  // question asked of the server, so it participates in the target key below rather than being
  // applied to the response afterwards.
  const [selection, setSelection] = useState<ReviewPageRequest | undefined>(undefined);
  const targetKey = targetKeyOf(repo, master, leaf, instead, selectorKind, selectorId, selection);

  const load = useCallback(async () => {
    setRead({ phase: "loading" });
    // A comparison read for another target is dropped the moment this question is asked: it belongs
    // to the header it was read for, and one read's answer is never rendered under another's.
    setRetained((previous) => (previous !== null && previous.key !== targetKey ? null : previous));
    try {
      const result = await intentReview(
        repo,
        master,
        leaf,
        instead ? undefined : selectorKind,
        instead ? undefined : selectorId,
        "",
        selection,
      );
      setRead(readFrom(result));
      if (result.state === "review" && result.payload) {
        setRetained({ key: targetKey, payload: result.payload });
      }
    } catch (cause) {
      setRead({ phase: "failed", problem: reviewProblemFromCause(cause) });
    }
  }, [repo, master, leaf, selectorKind, selectorId, instead, selection, targetKey]);

  useEffect(() => {
    void load();
  }, [load]);

  // The retained generation is used only when it was read for the question on screen now; the check
  // is belt-and-braces beside the reset in `load`, because a payload under a header it was not read
  // for is exactly the mismatch this surface must not be able to produce.
  const coherent = retained !== null && retained.key === targetKey ? retained.payload : null;
  const shown = shownPayload(read, coherent);
  const problem = problemOf(read);

  return (
    <div
      className="screen"
      data-testid="review-surface"
      data-comparison={shown?.comparison?.reference}
      data-review-target={`${repo}/${master}/${leaf}`}
    >
      <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.75rem" }}>
        <button type="button" onClick={onBack} data-testid="review-back">
          ← back
        </button>
        <strong>Intent review</strong>
        <span style={{ color: "muted" }} data-testid="review-subject">
          {repo} · {master} · {leaf} · {subjectLabel(selectorKind, selectorId, instead)}
        </span>
      </div>
      <ReviewOutcomeRegion
        read={read}
        instead={instead}
        shown={shown}
        lastCoherent={read.phase === "failed" ? coherent : null}
        onRetry={retryFor(read, load)}
        onOpenTaskContext={insteadFor(read, problem, instead, setInstead)}
      />
      {shown ? (
        <>
          <SubmissionBlock payload={shown} />
          <PageControls payload={shown} selection={selection} onSelect={setSelection} />
          <div className={TAKEOVER} style={{ display: "grid", gap: "1rem" }}>
            <KnowledgePane payload={shown} />
            <SourcePane payload={shown} repo={repo} master={master} leaf={leaf} />
            <EvidencePane payload={shown} />
          </div>
        </>
      ) : null}
    </div>
  );
}
