// What a fixture builder's override parameter is, and why it is not `Partial<T>`.
//
// THE DEFECT. `tsconfig.app.json` does not set `exactOptionalPropertyTypes`, so every optional slot
// of a `Partial<T>` also accepts an EXPLICIT `undefined` — and the builders' trailing `...over`
// writes it. `conversationPage({ capabilities: undefined })` therefore compiles, and answers a page
// whose REQUIRED `capabilities` is `undefined`: verbatim the defect `conversationWire.ts`'s header
// says it fixed (`undefined as unknown as ConversationCapabilities` — "worse: `capabilities` is
// REQUIRED, so this fixture stated that a required field is absent"), reachable again with no cast
// at all. `lifecycle({ state: undefined })`, `taskDoc({ steps: undefined })` and
// `projection({ enclosures: undefined })` are the same move. Nothing in `wireFixtureGuard.ts` sees
// any of them: there is no assertion for rule 1, no suppression for rule 5, and rule 4 measures
// property NAMES, which are all present and correct.
//
// WHY NOT JUST THE FLAG. `exactOptionalPropertyTypes` is the general fix and it was measured on
// this tree: 222 errors across 71 files, the bulk of them in app code (`panels/DetailPanel.tsx`,
// `panels/eventSummary.ts`, `data/submitMachine.ts`, `data/conversation-library/store.ts`) that
// this leaf does not own and that has other work in flight. Turning it on and clearing 222 sites is
// its own change; turning it on and clearing some of them is worse than not turning it on. So the
// narrow fix lives here, and it has one property the flag does not: it binds at the CALL SITE
// whichever tsconfig project the caller sits in, including `tsconfig.driver.json`'s Playwright
// suites and the guard's own in-memory program.
//
// WHAT IT DOES NOT COVER, so the next reader does not read it as more than it is:
//
//   * The override has to be a FRESH literal at the call site. Widen it through a variable first —
//
//         const over: Partial<ConversationPage> = { capabilities: undefined };
//         conversationPage(over);
//
//     — and `O` infers `Partial<ConversationPage>`, every slot admits `undefined` again, and this
//     compiles. `fixtureOverrides.test.ts` asserts that case as a KNOWN pass rather than leaving it
//     to be discovered. Only `exactOptionalPropertyTypes` closes it.
//
//   * ONE LEVEL DEEP ONLY. `conversationCapabilities` takes per-GROUP overrides
//     (`{ controls?: Partial<ConversationCapabilities["controls"]> }`), so
//     `conversationCapabilities({ controls: { interrupt: undefined } })` still writes `undefined`
//     onto a required leaf. Generalizing `Overrides` through a nesting level is a different and
//     fiddlier type than this one; it was left rather than half-done.
//
//   * EVERYTHING OUTSIDE THESE TWO MODULES. This binds the builders in `fixtures/wire.ts` and
//     `fixtures/conversationWire.ts`. Any other `Partial<WireType>` parameter in the tree — a
//     component prop, a store helper, an app-code decoder — still admits an explicit `undefined`,
//     and only the project-wide flag would change that.

/** The keys of `T` that carry no `?`. */
type RequiredKeys<T> = {
  [K in keyof T]-?: Record<never, never> extends Pick<T, K> ? never : K;
}[keyof T];

/**
 * The override literal `O`, with one extra demand laid over it: a key that is REQUIRED on the wire
 * type `T` may not be written as an explicit `undefined`.
 *
 * The mapped half is homomorphic, so it preserves `O`'s own optionality and leaves excess-property
 * checking to the `O extends Partial<T>` constraint at the declaration site — a field the mirror
 * does not declare still fails the same way it always did. A required key whose declared type
 * genuinely admits `undefined` (`foo: string | undefined`) passes through untouched; only a key
 * the server always sends collapses to `never`, which is what rejects the `undefined`.
 */
export type Overrides<O, T> = O & {
  [K in keyof O]: K extends RequiredKeys<T>
    ? undefined extends T[K & keyof T]
      ? O[K]
      : Exclude<O[K], undefined>
    : O[K];
};

/** What `O` infers to when a builder is called with no argument at all. */
export type NoOverrides = Record<never, never>;
