import { describe, expect, it } from "vitest";

import { constelColors } from "./constel";
import { CONSTEL_STATUSES } from "./model";

// The colour grammar's totality, asserted from the vocabulary rather than from a hand-written list
// of the five statuses that exist today. `model.ts` was made total over `State` by this leaf and
// this map — the step that turns a status into the pixels a developer actually reads — was left as
// `Record<string, string>` behind `COLORS[status] ?? COLORS.ok`. That default is the same claim
// `model.ts`'s comment rejects ("a default that says healthy is not a default, it is a claim"),
// and it is the second half of the original defect: an unclassified state produced `undefined`,
// the `??` caught it, and the node rendered in the cyan reserved for healthy work.
//
// `Record<ConstelStatus, string>` already fails `tsc -b` on a sixth status. This makes the same
// gap fail under vitest, so it is visible from either gate rather than only from the one someone
// remembered to run — the reason `model.test.ts` iterates `LIFECYCLE_STATES` instead of naming
// states, restated in this module's vocabulary.
describe("constelColors", () => {
  const stub = (name: string, fallback: string): string => `var(${name}|${fallback})`;

  it("gives every status in the vocabulary a colour of its own", () => {
    const colors = constelColors(stub);
    for (const status of CONSTEL_STATUSES) {
      expect(Object.keys(colors)).toContain(status);
      expect(colors[status]).toBeTruthy();
    }
    // No status may borrow another's hue: `ok` is the reading a developer skips past, so a status
    // that silently shares it is the defect wearing a different name.
    expect(new Set(Object.values(colors)).size).toBe(CONSTEL_STATUSES.length);
  });

  it("declares no colour for a status the vocabulary does not contain", () => {
    // The map is TOTAL, not defaulted. A key it has never heard of must come back undefined rather
    // than fall through to the healthy fill — the renderer only ever indexes it with a
    // `ConstelStatus` that `buildTopology` produced, so a miss here would mean the type lied, and
    // a lie is better read as a blank than as "nothing needs you".
    const colors: Partial<Record<string, string>> = constelColors(stub);
    expect(colors["from-a-newer-build"]).toBeUndefined();
  });

  it("asks for a themed token per status and offers a concrete fallback for each", () => {
    // The palette reads the theme; `mountConstel`'s reader is what turns an unset custom property
    // into the fallback. What this pins is the half that lives here: every status names its OWN
    // token and carries a real literal to fall back to — an empty fallback would render a node
    // with no fill, which reads as "nothing here" just as wrongly as the cyan did.
    const asked: [string, string][] = [];
    constelColors((name, fallback) => {
      asked.push([name, fallback]);
      return name;
    });
    expect(asked.length).toBe(CONSTEL_STATUSES.length);
    expect(new Set(asked.map(([name]) => name)).size).toBe(CONSTEL_STATUSES.length);
    for (const [name, fallback] of asked) {
      expect(name.startsWith("--")).toBe(true);
      expect(fallback).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });
});
