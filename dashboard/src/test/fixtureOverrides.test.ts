import ts from "typescript";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { buildProgramWithVirtualFiles, dashboardRoot } from "./wireFixtureGuard";

// The companion to `wireFixtureGuard.test.ts`, for the one impossible fixture no RULE can see.
//
// `wireFixtureGuard.ts` measures property NAMES: it catches a field the mirror never declared, an
// `any` with no shape at all, and every syntactic way of opting out. It cannot catch a fixture
// whose names are all correct and whose VALUE is `undefined` on a field the server always sends —
// `conversationPage({ capabilities: undefined })` names nothing wrong, asserts nothing, and
// suppresses nothing. Yet it produces the exact page `conversationWire.ts`'s header says it fixed:
// one whose REQUIRED `capabilities` is absent.
//
// `exactOptionalPropertyTypes` is what makes that a compile error project-wide. It is not on here —
// measured at 222 errors across 71 files, most of them in app code this leaf does not own — so the
// builders carry the constraint themselves (`fixtures/overrides.ts`). This file is the proof that
// they do, and it runs over the guard's in-memory program precisely BECAUSE that program does not
// set the flag: whatever these assertions catch, they catch without it.

const ROOT = dashboardRoot();
const PROBE_DIR = "src/__override_probe__";

const PROBE_FILES: Record<string, string> = {
  // Every one of these is a fixture the server could not have produced. All four are the same
  // move — an explicit `undefined` written into a slot the wire type declares REQUIRED — and all
  // four compiled before `Overrides` replaced the builders' bare `Partial<Node>`.
  [`${PROBE_DIR}/impossible.test.ts`]: [
    `import { lifecycle, projection, taskDoc } from "../test/fixtures/wire";`,
    `import { conversationPage } from "../test/fixtures/conversationWire";`,
    ``,
    `export const a = conversationPage({ capabilities: undefined });`,
    `export const b = lifecycle({ state: undefined });`,
    `export const c = taskDoc({ steps: undefined });`,
    `export const d = projection({ enclosures: undefined });`,
    ``,
  ].join("\n"),

  // Excess-property checking is the property `Overrides` must not cost. It is what makes the
  // builders worth having at all — it is where both of this leaf's proven defects would have died.
  [`${PROBE_DIR}/excess.test.ts`]: [
    `import { lifecycle, taskDoc } from "../test/fixtures/wire";`,
    ``,
    `export const a = lifecycle({ refusedPolarity: "amber" });`,
    `export const b = taskDoc({ createdAtButMisspelled: "2026-07-02T00:00:00+00:00" });`,
    ``,
  ].join("\n"),

  // A guard that also rejects the honest forms is a guard someone deletes. Everything below is a
  // shape the server does send, and none of it may cost a diagnostic.
  [`${PROBE_DIR}/honest.test.ts`]: [
    `import { action, lifecycle, observerEvent, projection, taskDoc } from "../test/fixtures/wire";`,
    `import { conversationItem, conversationPage } from "../test/fixtures/conversationWire";`,
    `import type { LifecycleProjection } from "../types/projection";`,
    ``,
    `// no argument at all`,
    `export const a = lifecycle();`,
    `export const b = projection();`,
    `// an ordinary override`,
    `export const c = lifecycle({ id: "LC-1", state: "running" });`,
    `export const d = taskDoc({ steps: [] });`,
    `// an OPTIONAL field written as \`undefined\` — the mirror marks it \`?\`, so absent is a shape`,
    `// the server sends and this must keep compiling.`,
    `export const e = lifecycle({ enclosure: undefined, staleSeconds: undefined });`,
    `export const f = taskDoc({ lifecycleId: undefined });`,
    `// a builder whose override is REQUIRED rather than defaulted`,
    `export const g = action({ action: "approve" });`,
    `export const h = observerEvent({ kind: "gate.opened" });`,
    `export const i = conversationItem({ itemId: "it-1", globalOrdinal: 1 });`,
    `export const j = conversationPage({ items: [] });`,
    `// a pre-widened override: \`O\` infers \`Partial<…>\` and every slot admits undefined again.`,
    `// This is the documented residue, asserted here so it is a KNOWN pass rather than a surprise.`,
    `const widened: Partial<LifecycleProjection> = { id: "LC-2" };`,
    `export const k = lifecycle(widened);`,
    ``,
  ].join("\n"),
};

const program = buildProgramWithVirtualFiles(PROBE_FILES, ROOT);

function diagnosticsFor(file: string): string[] {
  const fileName = path.join(ROOT, `${PROBE_DIR}/${file}`);
  const source = program.getSourceFile(fileName);
  if (!source) throw new Error(`the probe program did not load ${file}`);
  return program
    .getSemanticDiagnostics(source)
    .map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, " "));
}

describe("a builder override cannot state that a required field is absent", () => {
  it("rejects an explicit `undefined` on all four required slots", () => {
    // One diagnostic per call, and no more: the constraint must bite exactly where it is aimed.
    const rejected = diagnosticsFor("impossible.test.ts");
    expect(rejected).toHaveLength(4);
    for (const message of rejected) {
      expect(message).toContain("Type 'undefined' is not assignable to type 'never'");
    }
  });

  it("still rejects a field the mirror does not declare", () => {
    // The constraint is laid OVER `O extends Partial<Node>`, not in place of it. If generic
    // inference had swallowed excess-property checking, these two would compile and the builders
    // would have lost the property they exist for.
    const rejected = diagnosticsFor("excess.test.ts");
    expect(rejected).toHaveLength(2);
    expect(rejected[0]).toContain("refusedPolarity");
    expect(rejected[1]).toContain("createdAtButMisspelled");
    for (const message of rejected) {
      expect(message).toContain("Object literal may only specify known properties");
    }
  });

  it("leaves every shape the server does send alone", () => {
    expect(diagnosticsFor("honest.test.ts")).toEqual([]);
  });
});
