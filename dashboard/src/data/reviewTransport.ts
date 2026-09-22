// The review reads' one transport decode: this route's typed answer whatever the status, or a named
// failure that still carries the owner's own words.
//
// The review routes answer with their typed result and map a refusal onto the change-set routes'
// 400/404/503 idiom (mcp/.../serving/review.py), so a refusal arrives as a non-2xx response whose
// *body* is still this route's typed answer. `getJson` (data/files.ts) is the shared client for the
// other serving routes: it reads `body.status` and throws, which is right for them and drops the
// review's refusal detail entirely -- a reader then sees "404 Not Found" where the route had named a
// missing dataset, its reason and the initialization action. This module is therefore the review
// reads' own transport owner, and data/review.ts stays the review client's public entry, delegating
// here. There is exactly one decode per review read: one GET, one body read, one classification.
//
// Nothing here selects, ranks, computes or substitutes anything. A body that is not this route's
// answer is reported as the failure it is (``unreadable``), never as an empty review, and a refusal
// keeps the code, reason, offending input and next action the owner published. Unrelated clients'
// semantics are untouched: `getJson` keeps throwing on a non-2xx for the routes that use it.

import { FilesApiError } from "./files";

// What a reader has to be able to tell apart, as one token per answer that is not a review. The
// surface's `loading` and `known-empty` states are not failures and are rendered as themselves; these
// are the failures, and `not-initialized`, `unavailable-history`, `validation`, `authority` and
// `network` are the ones the route's own vocabulary names. `not-found` and `domain-refused` carry the
// remaining codes verbatim rather than guessing them into a state, and the last two are the honest
// fallbacks: "an HTTP response that is not this route's answer" and "no HTTP response at all" are not
// the same failure, and neither of them is a review.
export type ReviewFailureToken =
  // candidate_dataset_absent: the named dataset half does not exist, so it was never initialized.
  | "not-initialized"
  // candidate_not_live | candidate_unresolved | review_adapter_unavailable: the recorded history
  // this review would read (the leaf's live candidate, its enclosure contract, or the adapter
  // itself) cannot be resolved here.
  | "unavailable-history"
  // bad-request: an input this route does not admit.
  | "validation"
  // bad-path: the named repository is outside the configured workspace authority.
  | "authority"
  // not-found: the port named a path it does not hold.
  | "not-found"
  // a subject- or comparison-level refusal that is none of the above, carried by its own code.
  | "domain-refused"
  // no HTTP response was received (the socket failed, the server is down, the request was refused).
  | "network"
  // an HTTP response this route did not produce: a proxy page, an unwired mount, an empty body.
  | "unreadable";

// One failure, in the owner's own terms. `code` is the owner's identity for it and is never
// re-spelled; `httpStatus` is present exactly when an HTTP response carried it.
export interface ReviewFailure {
  token: ReviewFailureToken;
  code: string;
  detail: string;
  offendingInput?: string;
  expected?: string;
  nextAction?: string;
  httpStatus?: number;
}

// The typed refusal's own fields (models/knowledge/review.py::ReviewRefusal, snake_case on the wire).
// Declared structurally rather than imported so this module depends on no client type: the import
// runs one way only (data/review.ts -> here), which is what keeps the review client acyclic.
export interface ReviewRefusalFacts {
  code: string;
  detail: string;
  next_action: string;
  offending_input?: string;
  expected?: string;
}

// The status strings and refusal codes the route publishes, each mapped onto the state a reader is
// shown. A code this table does not know is carried as `domain-refused` with its own code intact --
// never guessed into a state it might not be.
const TOKEN_BY_CODE: Record<string, ReviewFailureToken> = {
  candidate_dataset_absent: "not-initialized",
  candidate_not_live: "unavailable-history",
  candidate_unresolved: "unavailable-history",
  review_adapter_unavailable: "unavailable-history",
  unavailable: "unavailable-history",
  "bad-request": "validation",
  "bad-path": "authority",
  "not-found": "not-found",
};

// The refusals that answer for the *intent* half alone: the pair could not be compared, while the
// task's own source change inventory is measured from its two recorded Git trees and needs no
// dataset at all (the task-context route serves that inventory with `staleness: not_compared`).
// A caller may therefore offer the inventory as a separately asked question -- never as an
// automatic substitution, and never with a dataset this client chose.
const INTENT_ONLY_CODES = new Set([
  "candidate_dataset_absent",
  "comparison_refused",
  "subject_unresolved",
]);

export const intentOnlyRefusal = (code: string): boolean => INTENT_ONLY_CODES.has(code);

export const reviewFailureToken = (code: string): ReviewFailureToken =>
  TOKEN_BY_CODE[code] ?? (code === "" ? "unreadable" : "domain-refused");

// The one error a review read throws. It stays a `FilesApiError` so a caller that already caught
// that type keeps working, and it carries the whole failure so a caller can render it.
export class ReviewTransportError extends FilesApiError {
  constructor(readonly failure: ReviewFailure) {
    super(failure.httpStatus ?? 0, failure.code);
    this.name = "ReviewTransportError";
  }
}

interface ReviewBody {
  state?: unknown;
  status?: unknown;
  detail?: unknown;
  nextAction?: unknown;
  offendingInput?: unknown;
  expected?: unknown;
  path?: unknown;
}

const text = (value: unknown): string | undefined =>
  typeof value === "string" && value !== "" ? value : undefined;

// The route's own transport-level body (a query it did not admit, an unwired adapter, a port's
// not-found), read as the failure it names. A response with no such body is `unreadable`: it is not
// this route's answer, and reporting it as one would invent a review.
function failureFromBody(response: Response, body: ReviewBody | null): ReviewFailure {
  return namedFailure(text(body?.status), response, body);
}

function namedFailure(
  named: string | undefined,
  response: Response,
  body: ReviewBody | null,
): ReviewFailure {
  const status = `${response.status} ${response.statusText}`.trim();
  return {
    token: named === undefined ? "unreadable" : reviewFailureToken(named),
    code: named ?? status,
    detail:
      text(body?.detail) ??
      `the review route answered ${status} with no refusal body, so no reason was published`,
    offendingInput: text(body?.offendingInput) ?? text(body?.path),
    expected: text(body?.expected),
    nextAction: text(body?.nextAction),
    httpStatus: response.status,
  };
}

function networkFailure(cause: unknown): ReviewFailure {
  return {
    token: "network",
    code: "network",
    detail: `the review read could not reach the server: ${
      cause instanceof Error ? cause.message : String(cause)
    }`,
  };
}

// The one GET a review read makes. The body is read whatever the status: a body that carries this
// route's `state` *is* the answer (a payload, a subject list, or a typed refusal), and anything else
// is a failure. The caller admits the states it knows; an unadmitted one is reported, not rendered.
export async function getReviewJson<T>(url: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch (cause) {
    throw new ReviewTransportError(networkFailure(cause));
  }
  const body = (await response.json().catch(() => null)) as (ReviewBody & T) | null;
  if (body !== null && typeof body.state === "string") return body as T;
  throw new ReviewTransportError(failureFromBody(response, body));
}

// A typed refusal as the failure a reader is shown: the owner's code, reason, offending input and
// next action, unchanged. Its token is the same classification every other answer goes through, so
// the entry and the surface cannot come to disagree about what a code means.
export const reviewProblemFromRefusal = (refusal: ReviewRefusalFacts): ReviewFailure => ({
  token: reviewFailureToken(refusal.code),
  code: refusal.code,
  detail: refusal.detail,
  offendingInput: refusal.offending_input,
  expected: refusal.expected,
  nextAction: refusal.next_action,
});

// An answer whose `state` this client does not admit. It is named rather than rendered, because a
// body claiming a state this route does not declare is not a review and not a refusal.
export const unreadableAnswer = (state: string): ReviewFailure => ({
  token: "unreadable",
  code: state,
  detail: `the review route answered state "${state}", which this client admits as neither a review nor a refusal`,
});

// Any thrown cause as a failure. A `ReviewTransportError` carries its own; anything else reached the
// caller instead of a response.
export const reviewProblemFromCause = (cause: unknown): ReviewFailure =>
  cause instanceof ReviewTransportError ? cause.failure : networkFailure(cause);
