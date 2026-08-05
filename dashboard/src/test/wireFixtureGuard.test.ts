import { describe, expect, it } from "vitest";

import {
  buildDashboardProgram,
  buildProgramWithVirtualFiles,
  collectWireFixtureFindings,
  dashboardRoot,
  declaresItselfAMirror,
  isFixtureSurface,
  reconcileWithRegistry,
  wireTypeNames,
  type SanctionedSite,
  type WireFixtureFinding,
} from "./wireFixtureGuard";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

// R6: "a test whose fixture is authored by the consumer cannot detect producer drift."
//
// `contract.test.ts` makes the MIRROR honest — it measures `types/projection.ts` against
// `fixtures/snapshot.json`, in three directions. This file makes the FIXTURES honest: it refuses
// the moves by which a test opts out of that mirror, so that a fixture which type-checks is a
// shape THE MIRROR can produce. The two are halves of one claim, and neither is worth much alone:
// a perfect mirror nobody is checked against, or fixtures checked against a mirror that lies.
//
// Say the chain exactly: fixture ⊆ mirror is held here and in `contract.test.ts`; mirror ⊆
// producer schema is held by the projection generator, its Python regressions, and
// `scripts/sync-projection-types.py --check`. `snapshot.json` itself remains a hand-maintained
// sample, so these guards still matter: they ensure dashboard fixture builders exercise the
// generated wire contract instead of bypassing it. `wire.ts`'s header carries the same chain.
//
// The mechanism is in `wireFixtureGuard.ts`; read its header for the five rules and why banning
// `as` is only the first of them. What lives HERE is the part a mechanism cannot supply itself:
//
//   1. THE REGISTRY. Every site that still does what the rules forbid, with the sentence that earns
//      it. It is exact (counts, not files) and bidirectional (an entry that stops matching fails
//      too), so a new consumer-authored fixture cannot appear silently and an old exemption cannot
//      outlive its reason.
//   2. THE PLANTED BYPASSES. An AST guard written without them has holes — this leaf has now proven
//      that twice. Every rule below is shown FAILING against a source that must never exist, over a
//      virtual program laid on the real tree so the planted files import the real wire types.
//   3. THE VACUITY CHECKS. The rules are all conditioned on "is this a wire type?", so a guard whose
//      vocabulary came back empty would pass everything in silence. The vocabulary is asserted
//      non-empty, asserted to contain the types the two proven defects lived on, and the convention
//      it is discovered by is asserted over `src/types/`.

// ── the registry ─────────────────────────────────────────────────────────────
// Keyed by `<file> :: <what was written>` — stable under line moves, so this list does not churn
// on every edit, while the COUNT still makes a second occurrence in a listed file fail.

const SANCTIONED_WIRE_SITES: Record<string, SanctionedSite> = {
  // ── the decode boundary ────────────────────────────────────────────────────
  // Outside the fixture surface, a cast to a wire type is the client saying "this JSON came from
  // the server and I am trusting it", which is a different act from a test authoring the server's
  // answer. These are the sites where untrusted JSON becomes typed; they are listed rather than
  // rewritten because narrowing them honestly is a runtime-validation problem, not a fixture one.
  "src/data/store.ts :: as LifecycleProjection": {
    count: 1,
    reason: "SSE patch decode: `/api/stream` lifecycle frame entering the store",
  },
  "src/data/store.ts :: as EnclosureNode": {
    count: 1,
    reason: "SSE patch decode: enclosure frame",
  },
  "src/data/store.ts :: as ProviderNode": {
    count: 1,
    reason: "SSE patch decode: provider frame",
  },
  "src/data/store.ts :: as Metrics": { count: 1, reason: "SSE patch decode: metrics frame" },
  "src/data/store.ts :: as Analytics": { count: 1, reason: "SSE patch decode: analytics frame" },
  "src/data/store.ts :: as ObserverEvent": {
    count: 1,
    reason: "raw `/api/events` line decode",
  },
  "src/data/seatEvents.ts :: as ObserverEvent": {
    count: 1,
    reason: "raw seat-event line decode before the applier inspects it",
  },
  "src/data/taskDocuments.ts :: as TaskDocNode": {
    count: 1,
    reason: "`GET /api/tasks/{id}` body decode",
  },
  "src/data/terminal.ts :: as { sessions?: TerminalCatalogRow[] }": {
    count: 1,
    reason: "`GET /api/terminal/sessions` envelope decode",
  },
  "src/data/terminalOpen.ts :: as HarnessControlState": {
    count: 1,
    reason: "open-response `controlState` narrowed from the wire's bare string",
  },
  "src/data/launchFlow.ts :: as TerminalOpenKind | null": {
    count: 1,
    reason: "launch-evidence kind narrowed from a persisted bare string",
  },
  "src/data/launchFlow.ts :: as HarnessControlState | null": {
    count: 2,
    reason: "launch-evidence controlState narrowed from a persisted bare string",
  },
  "src/data/submitClient.ts :: as SubmissionReceiptWire[\"acceptance\"]": {
    count: 1,
    reason: "submit receipt acceptance narrowed from the wire's bare string",
  },
  "src/data/submitClient.ts :: as ReconciliationResultWire[\"state\"]": {
    count: 1,
    reason: "reconciliation state narrowed from the wire's bare string",
  },
  "src/data/submitClient.ts :: as ReconciliationResultWire[\"submissionState\"]": {
    count: 1,
    reason: "reconciliation submissionState narrowed from the wire's bare string",
  },
  "src/data/sessionCapabilities.ts :: as Partial<CapabilitySnapshotWire>": {
    count: 1,
    reason: "capability snapshot decode, tolerant of a server that predates a field",
  },
  "src/data/capabilityCatalog.ts :: as Partial<CapabilityEnvelope>": {
    count: 1,
    reason: "capability envelope decode, tolerant of a server that predates a field",
  },
  "src/data/conversation/client.ts :: as ConversationPage": {
    count: 1,
    reason: "conversation page body decode",
  },
  "src/data/conversation/client.ts :: as ConversationTelemetry": {
    count: 1,
    reason: "conversation telemetry body decode",
  },
  "src/data/conversation/client.ts :: as InterruptOperation": {
    count: 1,
    reason: "interrupt operation body decode",
  },
  "src/data/conversation/stream.ts :: as ConversationEventEnvelope": {
    count: 1,
    reason: "conversation SSE frame decode",
  },
  "src/data/conversation-library/client.ts :: as ConversationLibraryPage": {
    count: 1,
    reason: "library list body decode",
  },
  "src/data/conversation-library/client.ts :: as HistoricalConversationPage": {
    count: 1,
    reason: "library read body decode",
  },
  "src/data/conversation-library/client.ts :: as OpenConversationOperation": {
    count: 1,
    reason: "library open body decode",
  },

  // ── the mirror's own derivations ───────────────────────────────────────────
  "src/types/projection.ts :: as StateCountField<S>": {
    count: 1,
    reason: "the bucket-name rule's runtime twin, re-narrowing what template concat widens",
  },
  "src/types/projection.ts :: as LifecycleStateCounts": {
    count: 1,
    reason: "`Object.fromEntries` answers a plain record; the KEYS come from the vocabulary",
  },

  // ── the fixture surface: the few casts that are the mechanism, not a fixture ─
  "src/test/servedProjection.ts :: as WorkspaceProjection": {
    count: 1,
    reason:
      "the sanctioned narrowing: the PARAMETER type did the structural check, and this only puts back the literal types `resolveJsonModule` erased",
  },
  "src/test/fixtures/conversationWire.ts :: as ActivePageCursor": {
    count: 1,
    reason: "brand mint: an opaque server-issued token with no structure to get wrong",
  },
  "src/test/fixtures/conversationWire.ts :: as ActiveEventCursor": {
    count: 1,
    reason: "brand mint: an opaque server-issued token with no structure to get wrong",
  },
  "src/test/fixtures/conversationWire.ts :: as LibraryConversationKey": {
    count: 1,
    reason: "brand mint: an opaque server-issued token with no structure to get wrong",
  },
  "src/topology/model.test.ts :: as State": {
    count: 1,
    reason:
      "the one deliberate WIDENING: `State` is a bare `str` server-side, so the mirror's closed union is narrower than the wire by construction, and this test exists to prove an unlisted member still classifies",
  },

  // ── compiler suppressions ──────────────────────────────────────────────────
  "src/test/contract.test.ts :: @ts-expect-error": {
    count: 3,
    reason:
      "the inverted pins: each asserts a field the server CANNOT send is absent from the mirror, and an unused `@ts-expect-error` is itself a compile error — so these fail the moment a field comes back",
  },
};

// ── the sweep over the real tree ─────────────────────────────────────────────

const ROOT = dashboardRoot();
const program = buildDashboardProgram(ROOT);
const findings = collectWireFixtureFindings(program, ROOT);
const vocabulary = wireTypeNames(program, ROOT);

function render(list: readonly string[]): string {
  return list.length === 0 ? "" : `\n  ${list.join("\n  ")}`;
}

describe("the guard has something to police", () => {
  it("discovers a wire vocabulary rather than an empty one", () => {
    // Every rule is conditioned on "is this a wire type?". An empty vocabulary is not a clean
    // tree, it is a guard that has stopped running.
    expect(vocabulary.length).toBeGreaterThan(100);
  });

  it("knows the types the two proven defects lived on", () => {
    // `refusedPolarity` was invented on `EngineProcessEdge`; `createdAt` on `TaskSubTaskRefNode`.
    expect(vocabulary).toContain("src/types/projection.ts:EngineProcessEdge");
    expect(vocabulary).toContain("src/types/projection.ts:TaskSubTaskRefNode");
    expect(vocabulary).toContain("src/types/event.ts:ObserverEvent");
    expect(vocabulary).toContain("src/data/conversation/types.ts:ConversationCapabilities");
  });

  it("watches exactly the modules that declare themselves mirrors", () => {
    // Both directions of the discovery rule, in one assertion. A module that LOSES its marker
    // silently narrows every rule below — that is the failure this catches, and it is invisible
    // otherwise because a narrower vocabulary just makes the guard find less. A NEW mirror module
    // fails here too, which is the moment to decide what its fixtures are allowed to do.
    //
    // KNOWN GAP, stated rather than papered over: a handful of API clients — `data/changeset.ts`,
    // `data/files.ts`, `data/notes.ts`, `data/harnessCatalog.ts`, `data/submissionLifecycleClient.ts`
    // — declare wire-shaped response types INLINE, next to client-side option and handler types
    // (`MasterChangesetOptions`, `FetchLike`, `ListQuery`). They carry no marker and are not
    // vocabulary here, so a fixture for those routes is unguarded. Treating "the header cites a
    // .py file" as the rule was measured and rejected: it sweeps up the option types too, which
    // are not wire shapes and would make the guard wrong rather than wider. The real fix is to
    // move those response types into a marker-carrying module; that is a refactor of app code,
    // not of fixtures, and it is left for whoever needs those routes guarded.
    const modules = [...new Set(vocabulary.map((entry) => entry.split(":")[0]))].sort();
    expect(modules).toEqual([
      "src/data/conversation-library/types.ts",
      "src/data/conversation/types.ts",
      "src/types/event.ts",
      "src/types/harnessCapabilities.ts",
      "src/types/projection.ts",
      "src/types/terminalCatalog.ts",
      "src/types/terminalOpen.ts",
    ]);
  });

  it("keeps the marker convention it discovers wire modules by", () => {
    // The vocabulary is found, not listed: a module whose first line says it mirrors a server
    // contract is a wire module. `src/types/` is the directory that convention lives in, so a
    // file there that stops declaring itself would quietly narrow every rule.
    const typesDir = path.join(ROOT, "src", "types");
    const unmarked = readdirSync(typesDir)
      .filter((entry) => entry.endsWith(".ts"))
      .filter((entry) => !declaresItselfAMirror(readFileSync(path.join(typesDir, entry), "utf-8")));
    expect(unmarked, "every src/types module must declare what it mirrors").toEqual([]);
  });

  it("classifies the fixture surface the rules are strict on", () => {
    expect(isFixtureSurface("src/panels/RailChat.test.tsx")).toBe(true);
    expect(isFixtureSurface("src/test/fixtures/wire.ts")).toBe(true);
    expect(isFixtureSurface("src/dev/fixtures.ts")).toBe(true);
    expect(isFixtureSurface("src/panels/engine-room/fixtures.ts")).toBe(true);
    expect(isFixtureSurface("e2e-production/cockpit.production.spec.ts")).toBe(true);
    expect(isFixtureSurface("perf/cockpit.perf.spec.ts")).toBe(true);
    expect(isFixtureSurface("src/data/store.ts")).toBe(false);
    expect(isFixtureSurface("src/panels/RailChat.tsx")).toBe(false);
  });
});

describe("no dashboard test asserts against a payload the server cannot produce", () => {
  const verdict = reconcileWithRegistry(findings, SANCTIONED_WIRE_SITES);

  it("has no wire fixture that is not type-checked against the mirror", () => {
    expect(
      verdict.unregistered,
      `A fixture opted out of the wire mirror. Build it with src/test/fixtures/wire.ts (or\n` +
        `conversationWire.ts), annotate it, or \`satisfies\` it — do not cast it.${render(verdict.unregistered)}\n`,
    ).toEqual([]);
  });

  it("keeps every sanctioned site earned — none stale, none grown", () => {
    expect(verdict.spent, `sanctioned site no longer present${render(verdict.spent)}\n`).toEqual([]);
    expect(
      verdict.miscounted,
      `a new cast is hiding behind an existing exemption${render(verdict.miscounted)}\n`,
    ).toEqual([]);
  });

  it("gives every sanctioned site a written reason", () => {
    expect(verdict.unreasoned).toEqual([]);
  });
});

// ── the planted bypasses ─────────────────────────────────────────────────────
// One virtual program carrying sources that must never exist on disk, laid over the real tree so
// they import the REAL wire types. Each rule is shown biting; then the honest forms are shown NOT
// biting, because a guard that flags `satisfies` is a guard nobody will keep.

const PLANTED_DIR = "src/__wire_guard_planted__";

const PLANTED_FILES: Record<string, string> = {
  [`${PLANTED_DIR}/casts.test.ts`]: [
    `import type { Analytics, LifecycleProjection, TaskDocNode as RenamedDoc } from "../types/projection";`,
    ``,
    `type LocalAlias = LifecycleProjection;`,
    `declare const loose: Record<string, unknown>;`,
    `declare const template: LifecycleProjection;`,
    `declare function sink(node: LifecycleProjection): void;`,
    ``,
    `// 1 — the plain assertion: skips excess-property checking, so the impossible field compiles.`,
    `export const direct = { id: "LC", refusedPolarity: "amber" } as LifecycleProjection;`,
    `// 2 — the double cast: skips assignability as well, so the missing fields compile too.`,
    `export const double = { id: "LC" } as unknown as LifecycleProjection;`,
    `// 3 — evasion: rename the type on import.`,
    `export const renamed = loose as RenamedDoc;`,
    `// 4 — evasion: hide it behind a local type alias.`,
    `export const aliased = loose as LocalAlias;`,
    `// 5 — evasion: wrap it in a generic so the wire name is not the head of the type.`,
    `export const wrapped = loose as Partial<RenamedDoc>;`,
    `export const listed = loose as LifecycleProjection[];`,
    `export const keyed = loose as Record<string, Analytics>;`,
    `// 6 — evasion: name no type at all and let the slot supply it.`,
    `sink({ id: "LC", refusedPolarity: "amber" } as never);`,
    `sink(loose as any);`,
    `sink(loose as unknown as LifecycleProjection);`,
    `// 7 — evasion: reach the type through a value instead of a name.`,
    `export const queried = loose as typeof template;`,
    `// 8 — fail-closed: a name the checker cannot bind is reported, not skipped.`,
    `export const unbound = loose as ThisTypeIsNotDeclaredAnywhere;`,
    ``,
  ].join("\n"),

  [`${PLANTED_DIR}/freshness.test.ts`]: [
    `import type { LifecycleProjection } from "../types/projection";`,
    ``,
    `declare const base: LifecycleProjection;`,
    `declare function sink(node: LifecycleProjection): void;`,
    ``,
    `// 9 — evasion with no assertion anywhere: an object literal loses FRESHNESS when it lands in`,
    `// a variable, and excess-property checking only applies to fresh literals. This compiles.`,
    `const smuggled = { ...base, refusedPolarity: "amber" };`,
    `sink(smuggled);`,
    ``,
    `// 10 — evasion with no assertion anywhere: Object.assign answers an INTERSECTION, which is`,
    `// assignable to the wire type however many extra fields it carries. This compiles too.`,
    `sink(Object.assign({ ...base }, { refusedPolarity: "amber" }));`,
    ``,
    `// 11 — evasion with no assertion and no shape: \`any\` assigns to anything. This is also what`,
    `// a VACUOUS check looks like — a parameter type cannot narrow an argument that is already any.`,
    `declare const text: string;`,
    `sink(JSON.parse(text));`,
    ``,
  ].join("\n"),

  [`${PLANTED_DIR}/suppression.test.ts`]: [
    `import type { LifecycleProjection } from "../types/projection";`,
    ``,
    `declare const base: LifecycleProjection;`,
    ``,
    `// 11 — evasion: keep the annotation, and silence the compiler instead.`,
    `// ${"@"}ts-expect-error the field is impossible and this comment is what lets it compile`,
    `export const suppressed: LifecycleProjection = { ...base, refusedPolarity: "amber" };`,
    ``,
  ].join("\n"),

  [`${PLANTED_DIR}/union.test.ts`]: [
    `import type { SubTaskRow, TaskSubTaskRefNode } from "../types/projection";`,
    ``,
    `declare const ref: TaskSubTaskRefNode;`,
    `declare function sink(row: SubTaskRow): void;`,
    ``,
    `// 12 — the union hole, and the one the mirror's own comment calls the proven defect.`,
    `// \`SubTaskRow = TaskSubTaskRefNode | SeriesSubTaskNode\` are two \`extra="forbid"\` server`,
    `// models differing in exactly one field each. TypeScript's excess-property check against a`,
    `// non-discriminated union only asks that each property exist in SOME member, so this row —`,
    `// no cast, no suppression, a FRESH literal — compiles while being a blend neither can send.`,
    `export const rows: SubTaskRow[] = [{`,
    `  number: "01", name: "first", file: "./01-first.md", status: "done", scope: "leaf",`,
    `  linkedLifecycleId: "LC-1",                  // only TaskSubTaskRefNode`,
    `  createdAt: "2026-07-02T00:00:00+00:00",     // only SeriesSubTaskNode`,
    `}];`,
    ``,
    `// 13 — the same blend arriving NON-fresh, where TypeScript checks nothing at all.`,
    `const blended = { ...ref, createdAt: "2026-07-02T00:00:00+00:00" };`,
    `sink(blended);`,
    ``,
    `// 14 — a property NO member of the union declares, also non-fresh.`,
    `const invented = { ...ref, refusedPolarity: "amber" };`,
    `sink(invented);`,
    ``,
  ].join("\n"),

  [`${PLANTED_DIR}/helper.ts`]: [
    `import type { LifecycleProjection } from "../types/projection";`,
    ``,
    `export function smuggle(base: LifecycleProjection) {`,
    `  return { ...base, refusedPolarity: "amber" };`,
    `}`,
    ``,
  ].join("\n"),

  [`${PLANTED_DIR}/twoModule.test.ts`]: [
    `import type { LifecycleProjection } from "../types/projection";`,
    `import { smuggle } from "./helper";`,
    ``,
    `declare const base: LifecycleProjection;`,
    `declare function sink(node: LifecycleProjection): void;`,
    ``,
    `// 15 — the smuggling happens in ANOTHER planted module. This is a harness assertion as much`,
    `// as a rule one: the virtual directory is not on disk, and \`moduleResolution: bundler\` asks`,
    `// \`directoryExists\` before it will look inside a folder, so before the host overrode it this`,
    `// import did not resolve, \`smuggle\` was \`any\`, and rule 3 fired for the wrong reason — the`,
    `// test looked like it had caught something.`,
    `sink(smuggle(base));`,
    ``,
  ].join("\n"),

  [`${PLANTED_DIR}/honest.test.ts`]: [
    `import type {`,
    `  LifecycleProjection,`,
    `  SeriesSubTaskNode,`,
    `  SubTaskRow,`,
    `  TaskSubTaskRefNode,`,
    `} from "../types/projection";`,
    `import { lifecycle } from "../test/fixtures/wire";`,
    ``,
    `declare const base: LifecycleProjection;`,
    `declare function sink(node: LifecycleProjection): void;`,
    `declare function sinkRow(row: SubTaskRow): void;`,
    `declare const ref: TaskSubTaskRefNode;`,
    `declare const series: SeriesSubTaskNode;`,
    ``,
    `export const annotated: LifecycleProjection = { ...base, id: "LC-1" };`,
    `export const checked = { ...base, id: "LC-2" } satisfies LifecycleProjection;`,
    `export const narrowed = { phase: "build" } as const;`,
    `export const mocked = { ok: true } as unknown as Response;`,
    `sink(lifecycle({ id: "LC-3" }));`,
    `sink({ ...base, id: "LC-4" });`,
    ``,
    `// Either side of the union on its own is a shape the server DOES send. A guard that flagged`,
    `// these would be telling every sub-task fixture in the tree that it is impossible.`,
    `sinkRow(ref);`,
    `sinkRow(series);`,
    `sinkRow({ ...ref, status: "done" });`,
    `sinkRow({ ...series, status: "done" });`,
    `export const refRows: SubTaskRow[] = [`,
    `  { number: "01", name: "a", file: "./a.md", status: "done", scope: "leaf", linkedLifecycleId: "LC" },`,
    `];`,
    `export const seriesRows: SubTaskRow[] = [`,
    `  { number: "02", name: "b", file: "./b.md", status: "done", scope: "leaf", createdAt: "T" },`,
    `];`,
    `export const sharedRows: SubTaskRow[] = [`,
    `  { number: "03", name: "c", file: "./c.md", status: "done", scope: "leaf" },`,
    `];`,
    ``,
  ].join("\n"),
};

const plantedFindings = collectWireFixtureFindings(
  buildProgramWithVirtualFiles(PLANTED_FILES, ROOT),
  ROOT,
);

function plantedIn(file: string): WireFixtureFinding[] {
  return plantedFindings.filter((finding) => finding.file === `${PLANTED_DIR}/${file}`);
}

function detailsIn(file: string): string[] {
  return plantedIn(file).map((finding) => `${finding.rule}: ${finding.detail}`);
}

describe("the guard is shown able to fail", () => {
  it("catches the plain cast and the double cast", () => {
    const casts = plantedIn("casts.test.ts");
    expect(casts.some((f) => f.key.endsWith(":: as LifecycleProjection"))).toBe(true);
    expect(casts.filter((f) => f.rule === "wire-cast").length).toBeGreaterThan(0);
    // The double cast is caught twice over: by its outer `as LifecycleProjection` (rule 1) and by
    // its inner `as unknown` sitting in a wire-typed slot (rule 2).
    expect(detailsIn("casts.test.ts")).toContain(
      "wire-position-cast: asserted `as unknown` where LifecycleProjection is expected",
    );
  });

  it("follows an import rename, a local alias, and a generic wrapper", () => {
    const keys = plantedIn("casts.test.ts").map((finding) => finding.key);
    expect(keys, "renamed on import").toContain(`${PLANTED_DIR}/casts.test.ts :: as RenamedDoc`);
    expect(keys, "hidden behind a local alias").toContain(
      `${PLANTED_DIR}/casts.test.ts :: as LocalAlias`,
    );
    expect(keys, "wrapped in Partial<>").toContain(
      `${PLANTED_DIR}/casts.test.ts :: as Partial<RenamedDoc>`,
    );
    expect(keys, "wrapped in an array").toContain(
      `${PLANTED_DIR}/casts.test.ts :: as LifecycleProjection[]`,
    );
    expect(keys, "wrapped in a Record<>").toContain(
      `${PLANTED_DIR}/casts.test.ts :: as Record<string, Analytics>`,
    );
    expect(keys, "reached through a value's type").toContain(
      `${PLANTED_DIR}/casts.test.ts :: as typeof template`,
    );
  });

  it("catches an assertion that names no wire type but lands in a wire slot", () => {
    // The rule that exists because banning `as WireType` is not enough: `as never` and `as any`
    // are assignable to anything and name nothing. Three such sites were live in this tree.
    const details = detailsIn("casts.test.ts");
    expect(details).toContain(
      "wire-position-cast: asserted `as never` where LifecycleProjection is expected",
    );
    expect(details).toContain(
      "wire-position-cast: asserted `as any` where LifecycleProjection is expected",
    );
  });

  it("catches a smuggled field where there is no assertion to ban", () => {
    // Freshness loss and `Object.assign` are the two ways a value reaches a wire slot carrying a
    // field the mirror never declared, with no `as` anywhere for a syntactic rule to find. Both
    // were verified to compile cleanly under this repo's TypeScript before the rule was written.
    const smuggles = plantedIn("freshness.test.ts").filter(
      (finding) => finding.rule === "wire-excess-property",
    );
    expect(smuggles).toHaveLength(2);
    for (const finding of smuggles) {
      expect(finding.detail).toContain("refusedPolarity");
      expect(finding.detail).toContain("LifecycleProjection");
    }
  });

  it("catches an `any` reaching a wire slot, where there is nothing to compare", () => {
    // The third assertion-free evasion, and the one that also describes a VACUOUS check: handing
    // `any` to a function whose parameter type is the wire type proves nothing, because a
    // parameter type cannot narrow an argument that is already `any`. `fixtures/wire.ts::reparsed`
    // was doing exactly that — `asServedProjection(JSON.parse(…))` looked checked and was not.
    expect(detailsIn("freshness.test.ts")).toContain(
      "wire-any-value: an `any`-typed value reaches LifecycleProjection, so nothing checks it",
    );
  });

  it("catches a fixture blending two mutually exclusive members of a union slot", () => {
    // The hole this closes: `soleWireObjectTarget` returned null whenever the slot was a union, so
    // rules 3 and 4 never ran on one — and a union is precisely where TypeScript's own excess
    // check goes weak, because it only requires each property to exist in SOME member. The slot is
    // now measured against EVERY member: a value no single member could hold is a finding.
    const blends = plantedIn("union.test.ts").filter(
      (finding) => finding.rule === "wire-excess-property" && finding.detail.startsWith("combines"),
    );
    // Once for the reviewer's fresh literal, once for the same blend arriving non-fresh.
    expect(blends).toHaveLength(2);
    for (const finding of blends) {
      expect(finding.detail).toContain("linkedLifecycleId");
      expect(finding.detail).toContain("createdAt");
      expect(finding.detail).toContain("no single member of SubTaskRow declares");
      expect(finding.detail).toContain("TaskSubTaskRefNode and SeriesSubTaskNode");
    }
  });

  it("catches a property no member of a union slot declares", () => {
    expect(detailsIn("union.test.ts")).toContain(
      "wire-excess-property: carries refusedPolarity, which SubTaskRow does not declare",
    );
  });

  it("can express a bypass that spans two modules", () => {
    // A harness assertion. `buildProgramWithVirtualFiles` lays files over a directory that does not
    // exist on disk, and `moduleResolution: bundler` consults `directoryExists` before looking
    // inside a folder — so a planted file importing another planted file used to resolve to
    // nothing, degrade to `any`, and trip rule 3. The test passed for the wrong reason, which is
    // the same as a harness that cannot express the case at all.
    const twoModule = plantedIn("twoModule.test.ts");
    expect(twoModule.map((finding) => finding.rule)).toEqual(["wire-excess-property"]);
    expect(twoModule[0].detail).toBe(
      "carries refusedPolarity, which LifecycleProjection does not declare",
    );
  });

  it("catches a suppressed compiler diagnostic", () => {
    expect(plantedIn("suppression.test.ts").map((finding) => finding.rule)).toEqual([
      "compiler-suppression",
    ]);
  });

  it("reports a type name it cannot resolve rather than passing it", () => {
    // Fail-closed: "the guard did not understand this" must read as a failure. A cast the checker
    // cannot bind is the shape an unparsed or half-broken file takes.
    expect(detailsIn("casts.test.ts")).toContain("wire-cast: asserts <unresolved>");
  });
});

describe("the guard leaves the honest forms alone", () => {
  it("passes an annotated literal, a `satisfies` check, a builder call and a DOM mock", () => {
    // A guard that also flags the fix is a guard that gets deleted. `satisfies` and a type
    // annotation both do FULL checking — required fields and excess properties, both directions —
    // which is exactly what this whole file exists to force fixtures back onto.
    expect(detailsIn("honest.test.ts")).toEqual([]);
  });
});

describe("the registry cannot be quietly outgrown", () => {
  const site = (key: string): WireFixtureFinding => ({
    file: "src/x.test.ts",
    line: 1,
    rule: "wire-cast",
    key,
    detail: "",
    fixtureSurface: true,
  });

  it("reports a site no entry covers", () => {
    expect(reconcileWithRegistry([site("a")], {}).unregistered).toEqual(["a  (1×)"]);
  });

  it("reports an entry that no longer matches anything", () => {
    expect(reconcileWithRegistry([], { a: { count: 1, reason: "r" } }).spent).toEqual(["a"]);
  });

  it("reports a second cast hiding behind a one-site exemption", () => {
    // The reason the registry counts rather than lists files: an exemption earned by one decode
    // boundary must not shelter the next fixture someone drops into the same file.
    const verdict = reconcileWithRegistry([site("a"), site("a")], { a: { count: 1, reason: "r" } });
    expect(verdict.miscounted).toEqual(["a  registry 1× vs found 2×"]);
    expect(verdict.unregistered).toEqual([]);
  });

  it("reports an entry with no reason written next to it", () => {
    expect(reconcileWithRegistry([site("a")], { a: { count: 1, reason: "  " } }).unreasoned).toEqual(
      ["a"],
    );
  });
});
