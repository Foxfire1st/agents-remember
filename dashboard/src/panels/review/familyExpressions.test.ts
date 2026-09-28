// A4's arithmetic, without a DOM: the family's changed expression excerpts and the dedup that makes
// the collection a family-level reading rather than a concatenation of the member rows.
//
// WHY THIS FILE ASSEMBLES ITS OWN PAYLOAD, WHICH `ReviewWorkspace.family.test.tsx` REFUSES TO DO.
// That module's cases read real captured route bodies, because what they check is the wire contract.
// This one checks one pure function's arithmetic, and the captured bodies do not contain every case
// the arithmetic has to get right: no capture holds two member revisions that name one address with
// the same recorded and observed bytes (the served family does -- `knowledge_curator_ingest.py` and
// `cli/knowledge_ingest.py` are each recorded under two member revisions -- and that live case is
// measured on the mounted product, not here). So the cases below state plainly which inputs are
// constructed: they are the shapes the arithmetic must handle, and they prove nothing about the wire.
//
// The three-way partition these cases pin is the shipped one, not this file's invention:
// `models/knowledge/read.py::ANCHOR_RESOLUTIONS` names the seven resolutions and
// `application/review_attribution.py` partitions them into resolved (`exact_recorded_blob`), the
// states where the recorded bytes are not at the recorded address, and the observations nobody made.

import { describe, expect, it } from "vitest";

import type { ReviewFamilyMember, ReviewFamilyMemberSource } from "../../data/review";
import { familyExpressionExcerpts, type FamilyMembershipRow } from "./familyExpressions";

let claimCounter = 0;

// A constructed claim follows the server's own locator rule: an observed address carries a whole-file
// locator, which is `whole_file` on the exact recorded blob and `unresolved` on any other reading.
function claim(over: Partial<ReviewFamilyMemberSource> & { detail: string }): ReviewFamilyMemberSource {
  claimCounter += 1;
  const observed = over.path !== undefined;
  return {
    claim_id: `claim-${claimCounter}`,
    invariant_revision_id: "rev",
    role: "primary-authority",
    rationale: "recorded by this case",
    locator: observed ? { kind: "file" } : undefined,
    resolved_ranges: [],
    locator_state: !observed
      ? "not_observed"
      : over.resolution === "exact_recorded_blob"
        ? "whole_file"
        : "unresolved",
    ...over,
  };
}

function member(revision: string, sources: ReviewFamilyMemberSource[], label?: string): ReviewFamilyMember {
  return {
    member_id: `member-${revision}`,
    invariant_revision_id: revision,
    display_label: label,
    state: "recorded",
    statement: "a statement",
    essential_conditions: [],
    exclusions: [],
    provenance: {},
    other_family_revision_ids: [],
    sources,
    detail: "membership",
  };
}

function rows(...entries: [FamilyMembershipRow["side"], ReviewFamilyMember][]): FamilyMembershipRow[] {
  return entries.map(([side, entry]) => ({ side, member: entry }));
}

describe("the family's changed expression excerpts (A4)", () => {
  it("collapses two member revisions that record one address with the same recorded and observed bytes", () => {
    // CONSTRUCTED: the live 2-members-1-address case, in the smallest shape that states it.
    const shared = {
      path: "src/one.py",
      recorded_source_identity: "a".repeat(40),
      observed_source_identity: "b".repeat(40),
      resolution: "recorded_blob_mismatch",
    };
    const collection = familyExpressionExcerpts(
      rows(
        ["before", member("rev-one", [claim({ ...shared, detail: "recorded at src/one.py" })], "member-one")],
        ["after", member("rev-two", [claim({ ...shared, detail: "recorded at src/one.py" })], "member-two")],
      ),
    );

    // Two rows in, one excerpt out -- and the count the collection carries is the DISTINCT set, not
    // the row count, which is what A4's "deduplicated changed expression excerpts" asks for.
    expect(collection.rows).toBe(2);
    expect(collection.excerpts).toHaveLength(1);
    expect(collection.excerpts[0].rows).toBe(2);
    expect(collection.excerpts[0].revisions).toEqual(["rev-one", "rev-two"]);
    expect(collection.excerpts[0].occurrences).toEqual([
      { side: "before", revision: "rev-one", label: "member-one" },
      { side: "after", revision: "rev-two", label: "member-two" },
    ]);
    expect(collection.membershipRowsWithChanged).toBe(2);
    expect(collection.membershipRowsWithoutChanged).toBe(0);
  });

  it("keeps two excerpts apart when one address carries two different recorded blobs", () => {
    // The prototype's key is the excerpt's address (`path + ':' + start`), so the key here may not be
    // the path alone: two recorded blobs at one path are two excerpts and must stay two rows.
    const collection = familyExpressionExcerpts(
      rows(
        [
          "before",
          member("rev-one", [
            claim({
              path: "src/one.py",
              recorded_source_identity: "a".repeat(40),
              observed_source_identity: "b".repeat(40),
              resolution: "recorded_blob_mismatch",
              detail: "the first recorded blob",
            }),
          ]),
        ],
        [
          "after",
          member("rev-two", [
            claim({
              path: "src/one.py",
              recorded_source_identity: "c".repeat(40),
              observed_source_identity: "b".repeat(40),
              resolution: "recorded_blob_mismatch",
              detail: "a different recorded blob at the same address",
            }),
          ]),
        ],
      ),
    );

    expect(collection.rows).toBe(2);
    expect(collection.excerpts).toHaveLength(2);
    expect(collection.excerpts.every((excerpt) => excerpt.path === "src/one.py")).toBe(true);
  });

  it("merges the rows that named one excerpt under different roles, naming every role", () => {
    // CONSTRUCTED, after the shape the captured `familyReview.walkFinal` body really records: one
    // member revision whose two claims name one address and one blob pair under two roles.
    const shared = {
      path: "src/batch.py",
      recorded_source_identity: "d".repeat(40),
      observed_source_identity: "e".repeat(40),
      resolution: "recorded_blob_mismatch",
    };
    const collection = familyExpressionExcerpts(
      rows(
        [
          "after",
          member("rev-one", [
            claim({ ...shared, role: "support", detail: "recorded once" }),
            claim({ ...shared, role: "enforcement", detail: "recorded once" }),
          ]),
        ],
      ),
    );

    expect(collection.rows).toBe(2);
    expect(collection.excerpts).toHaveLength(1);
    expect(collection.excerpts[0].roles).toEqual(["enforcement", "support"]);
    expect(collection.excerpts[0].occurrences).toHaveLength(1);
    expect(collection.excerpts[0].occurrences[0].side).toBe("after");
  });

  it("keeps both sides' readings of one divergent address, because the disagreement is the change", () => {
    // THE MEASURED SHAPE, not a guess: the captured `familyReview.walkFinal` body records member
    // revision `d24e5187` with `src/batch.py` under one recorded blob, resolved `exact_recorded_blob`
    // on the before snapshot and `recorded_blob_mismatch` on the after one. The observed identity is
    // not one value for that address -- the before read observed the recorded blob itself and the after
    // read observed different bytes -- and it is what the read FOUND, not part of the address's
    // identity. Keying the excerpt by it split this one address into two rows that could never be
    // paired, so the row named a single side and the per-side fact the collection exists for was lost.
    const recorded = "d".repeat(40);
    const collection = familyExpressionExcerpts(
      rows(
        [
          "before",
          member("rev-one", [
            claim({
              path: "src/batch.py",
              recorded_source_identity: recorded,
              observed_source_identity: recorded,
              resolution: "exact_recorded_blob",
              detail: "the before tree holds the recorded blob",
            }),
          ]),
        ],
        [
          "after",
          member("rev-one", [
            claim({
              path: "src/batch.py",
              recorded_source_identity: recorded,
              observed_source_identity: "e".repeat(40),
              resolution: "recorded_blob_mismatch",
              detail: "the after tree holds different bytes",
            }),
          ]),
        ],
      ),
    );

    expect(collection.rows).toBe(1);
    expect(collection.excerpts).toHaveLength(1);
    expect(collection.excerpts[0].readingsBySide).toEqual([
      { side: "before", resolutions: ["exact_recorded_blob"], observed: [recorded] },
      { side: "after", resolutions: ["recorded_blob_mismatch"], observed: ["e".repeat(40)] },
    ]);
    // The resolved side is not a changed row, and it is not silently dropped either.
    expect(collection.resolved).toBe(1);
    expect(collection.membershipRowsWithChanged).toBe(1);
    expect(collection.membershipRowsWithoutChanged).toBe(1);
    expect(collection.distinctRevisions).toBe(1);
  });

  it("treats two different observed blobs at one address as one excerpt, read once per side", () => {
    // The complement of the case above: both sides are changed, and each observed different bytes. That
    // two readings differ is a fact about the two trees, not about the address, so it is ONE excerpt
    // carrying two readings -- the identity is the address and its recorded bytes.
    const recorded = "f".repeat(40);
    const collection = familyExpressionExcerpts(
      rows(
        [
          "before",
          member("rev-one", [
            claim({
              path: "src/one.py",
              recorded_source_identity: recorded,
              observed_source_identity: "1".repeat(40),
              resolution: "recorded_blob_mismatch",
              detail: "the before tree holds other bytes",
            }),
          ]),
        ],
        [
          "after",
          member("rev-two", [
            claim({
              path: "src/one.py",
              recorded_source_identity: recorded,
              observed_source_identity: "2".repeat(40),
              resolution: "recorded_blob_mismatch",
              detail: "the after tree holds yet other bytes",
            }),
          ]),
        ],
      ),
    );

    expect(collection.rows).toBe(2);
    expect(collection.excerpts).toHaveLength(1);
    expect(collection.excerpts[0].readingsBySide).toEqual([
      { side: "before", resolutions: ["recorded_blob_mismatch"], observed: ["1".repeat(40)] },
      { side: "after", resolutions: ["recorded_blob_mismatch"], observed: ["2".repeat(40)] },
    ]);
    // One excerpt recorded by two membership rows, one on each side.
    expect(collection.excerpts[0].occurrences).toEqual([
      { side: "before", revision: "rev-one", label: undefined },
      { side: "after", revision: "rev-two", label: undefined },
    ]);
  });

  it("partitions the read's own resolutions: resolved, not the recorded bytes, and not measured", () => {
    const collection = familyExpressionExcerpts(
      rows(
        [
          "after",
          member("rev-one", [
            claim({ path: "src/exact.py", recorded_source_identity: "1".repeat(40), observed_source_identity: "1".repeat(40), resolution: "exact_recorded_blob", detail: "resolved" }),
            claim({ path: "src/stale.py", recorded_source_identity: "2".repeat(40), observed_source_identity: "3".repeat(40), resolution: "recorded_blob_mismatch", detail: "stale" }),
            claim({ path: "src/gone.py", recorded_source_identity: "4".repeat(40), resolution: "path_absent", detail: "unresolved" }),
            claim({ path: "src/unread.py", recorded_source_identity: "5".repeat(40), resolution: "recorded_object_unavailable", detail: "not measured" }),
            claim({ path: "src/unasked.py", recorded_source_identity: "6".repeat(40), resolution: "not_requested", detail: "not measured" }),
            claim({ detail: "this claim carries no address at all" }),
          ]),
        ],
      ),
    );

    // Only the states where the read did not find the recorded bytes at the address are rows; the
    // resolved one is not a change and the two unmeasured ones are counted apart rather than called
    // changed, because "never asked" and "different bytes" are different facts.
    expect(collection.rows).toBe(2);
    expect(collection.excerpts.map((excerpt) => excerpt.path)).toEqual(["src/gone.py", "src/stale.py"]);
    expect(collection.resolved).toBe(1);
    expect(collection.unmeasured).toBe(3);
    // A row states the read's own resolution rather than this function's reading of it.
    expect(collection.excerpts.map((excerpt) => excerpt.readingsBySide[0].resolutions[0]).sort()).toEqual([
      "path_absent",
      "recorded_blob_mismatch",
    ]);
    // An address the read found nowhere says so per side rather than printing a single observed value.
    expect(collection.excerpts.map((excerpt) => excerpt.readingsBySide[0].observed)).toEqual([[], ["3".repeat(40)]]);
  });

  it("reports an empty collection as a measured empty one, with the carried rows counted", () => {
    const collection = familyExpressionExcerpts(
      rows(
        ["before", member("rev-one", [claim({ path: "src/one.py", recorded_source_identity: "a".repeat(40), observed_source_identity: "a".repeat(40), resolution: "exact_recorded_blob", detail: "resolved" })])],
        ["after", member("rev-two", [])],
      ),
    );

    expect(collection.rows).toBe(0);
    expect(collection.excerpts).toEqual([]);
    expect(collection.resolved).toBe(1);
    expect(collection.membershipRows).toBe(2);
    expect(collection.distinctRevisions).toBe(2);
    expect(collection.membershipRowsWithChanged).toBe(0);
    expect(collection.membershipRowsWithoutChanged).toBe(2);
  });
});
