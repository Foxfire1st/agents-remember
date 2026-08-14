// How a JSON module is typed, and the ONE narrowing the fixture needs.
//
// `resolveJsonModule` widens every literal in the payload: `"blocked"` becomes `string`, `1`
// becomes `number`. So `snapshot.json` can never be *assigned* to a mirror that types its
// vocabularies as literal unions (`State`, `Phase`, `AttentionSeverity`, …) — not because the
// payload is wrong, but because the import threw the literals away.
//
// The reflex was `snapshot as unknown as WorkspaceProjection`, which turns off assignability,
// excess-property checking and everything else: a served field the mirror had never heard of
// typechecked and passed, and so did a payload missing a field the mirror requires. `AsJsonModule`
// applies exactly the import's widening to the mirror and nothing else, so passing the fixture
// through `asServedProjection` is a full structural check of everything the widening does not
// touch — required fields, field types, array shapes, nesting — and the cast inside is narrow
// enough to name: it re-narrows the vocabularies, and only those.
//
// This lives beside the tests rather than in `types/projection.ts` because it is scaffolding for
// consuming the fixture, not part of the contract. Every test that reads `snapshot.json` should
// come through here; a second `as unknown as` elsewhere silently re-opens the hole for that file.

import type { WorkspaceProjection } from "../types/projection";

export type AsJsonModule<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean
      : T extends readonly (infer Item)[]
        ? AsJsonModule<Item>[]
        : T extends object
          ? { [K in keyof T]: AsJsonModule<T[K]> }
          : T;

/**
 * The fixture, read as the projection the server would have sent.
 *
 * The PARAMETER type is the check: a payload that does not satisfy `AsJsonModule<
 * WorkspaceProjection>` fails `tsc -b` at the call site, naming the field. The cast in the body
 * only puts back the literal types `resolveJsonModule` erased.
 */
export function asServedProjection(payload: AsJsonModule<WorkspaceProjection>): WorkspaceProjection {
  return payload as WorkspaceProjection;
}
