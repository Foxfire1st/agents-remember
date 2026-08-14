// The mechanism behind `wireFixtureGuard.test.ts`: an AST + type-checker sweep that answers one
// question over the whole dashboard tree — "can a test assert against a payload the server could
// never send?"
//
// WHY A GUARD AND NOT JUST `tsc`. TypeScript already refuses an impossible field on a wire-typed
// object literal; `{ refusedPolarity: "amber" } satisfies EngineProcessEdge` does not compile. The
// leak is that a fixture can OPT OUT, and every opt-out is one token long:
//
//   `as EngineProcessEdge`          — an assertion skips excess-property checking entirely
//   `as unknown as LifecycleProjection` — and skips assignability as well
//   `as never` / `as any`           — assignable to the wire slot without naming a wire type at all
//   `// @ts-expect-error`           — the error is raised and then swallowed
//   `const raw = {…}; use(raw)`     — a literal loses FRESHNESS when it lands in a variable, and
//                                     excess-property checking only applies to fresh literals
//   `Object.assign(base, {…})`      — the result is an intersection, which is assignable
//   `use(JSON.parse(text))`         — `any` assigns to anything, with no assertion and no shape
//
// The last three are not assertions at all and no ban on `as` can see them; all three were
// confirmed against this repo's TypeScript before this file was written. So the guard is five
// rules, and four of them exist because banning `as` alone is not enough.
//
// WHAT MAKES IT FAIL-CLOSED
//   * The wire vocabulary is DISCOVERED, never listed: every exported type in a module that opens
//     with `// TypeScript mirror of` / `// Browser mirror of` (the house marker, carried by all
//     seven of them today) plus everything under `src/types/`. A new mirror module is covered the
//     day it is written, and `src/types/` is additionally required to carry the marker so the
//     convention cannot quietly lapse.
//   * A type reference the checker cannot resolve is REPORTED rather than skipped, so "the guard
//     did not understand this" reads as a failure instead of as a pass.
//   * The sanctioned-site registry is exact and bidirectional: it counts occurrences, so a second
//     cast in an already-listed file fails, and an entry that stops matching fails too. Nothing is
//     escapable by file, only by site, and only with a reason written next to it.
//
// SCOPE. Rule 1 runs over every scanned file. Rules 2–5 run over the FIXTURE SURFACE only — tests,
// fixture modules, the dev gallery, the Playwright suites. Outside it, a cast to a wire type is the
// decode boundary saying "I am trusting the server", which is a different (and legitimate) act from
// a test authoring the server's answer; those sites are registry-listed rather than rewritten.
//
// ── WHAT THIS DOES NOT COVER ─────────────────────────────────────────────────────────────────────
// Recorded so the next reader knows the SHAPE of what is uncovered rather than inferring
// completeness from a clean run. Each of these was reproduced against this tree; none is fixed here.
//
//   1. RULE 4 READS FOUR NODE KINDS. `Identifier`, `CallExpression`, `PropertyAccessExpression` and
//      (for the union case only) `ObjectLiteralExpression`. `ElementAccessExpression` — `rows[0]` —
//      escapes, and so do `AwaitExpression`, `NewExpression` and `NonNullExpression` (`rows.at(0)!`).
//      This is the widest of the holes, because array indexing and `await` are the two most
//      idiomatic fixture accessors in this suite: `const row = rows[0]` reaching a wire slot is
//      unread today, and so is `sink(await built())`.
//
//   2. A GENERIC HELPER DEFEATS RULES 1 AND 4 TOGETHER. `function make<T>(shape: object): T { return
//      shape as T; }` names no wire type at the assertion (rule 1 sees a type PARAMETER, not
//      vocabulary), and `make<LifecycleProjection>({ … })` answers the wire type exactly, so rule 4
//      has no undeclared property to weigh. One helper re-opens every evasion the five rules close.
//
//   3. A NEW UNMARKED MIRROR MODULE IS INVISIBLE. The vocabulary is discovered from the house
//      marker, and the test's seven-module assertion pins the modules that HAVE one — so a mirror
//      that LOSES its marker fails loudly, while one that never carried a marker never appears and
//      the assertion still passes. The discovery mechanism is fail-closed in one direction only.
//      Live instances today: `data/harnessCatalog.ts`, `data/submissionLifecycleClient.ts`,
//      `data/changeset.ts`, `data/files.ts`, `data/notes.ts` (see the test's KNOWN GAP note for why
//      widening the rule to "the header cites a .py file" was measured and rejected). Both of the
//      impossible fixtures this leaf removed — a `control` on the harness catalog row and a
//      `bridgeEpoch` on `WithdrawalResultWire` — lived in exactly this blind spot.
//
//   4. TYPE PREDICATES AND ASSERTION FUNCTIONS NARROW WITH NO `as` ANYWHERE. `function isPage(v:
//      unknown): v is ConversationPage { return true; }` and `function assertPage(v: unknown):
//      asserts v is ConversationPage {}` both put an arbitrary value into a wire slot carrying a
//      wire type, with nothing syntactic to ban and nothing structural to compare.
//
//   5. A VALUE, NOT A NAME. Every rule here measures property NAMES. An override whose names are
//      all correct and whose VALUE is `undefined` on a field the server always sends is invisible
//      to all five — `conversationPage({ capabilities: undefined })` asserts nothing, suppresses
//      nothing and declares nothing undeclared. `exactOptionalPropertyTypes` is the general answer
//      and is NOT set on this project (measured: 222 errors across 71 files); the builders carry
//      the constraint themselves instead. See `fixtures/overrides.ts` and `fixtureOverrides.test.ts`.

import ts from "typescript";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

export type WireFixtureRule =
  | "wire-cast"
  | "wire-position-cast"
  | "wire-excess-property"
  | "wire-any-value"
  | "compiler-suppression";

export interface WireFixtureFinding {
  /** Repo-relative POSIX path of the offending file. */
  readonly file: string;
  readonly line: number;
  readonly rule: WireFixtureRule;
  /** Stable identity of the site: survives line moves, so the registry does not churn. */
  readonly key: string;
  readonly detail: string;
  readonly fixtureSurface: boolean;
}

/** One site allowed to do what the rules forbid, and the sentence that earns it. */
export interface SanctionedSite {
  readonly count: number;
  readonly reason: string;
}

// ── what counts as a wire type ───────────────────────────────────────────────

/** The house marker every mirror module carries on its first line. */
const MIRROR_MARKER = /^\s*\/\/\s*(TypeScript|Browser) mirror of\b/;

export function declaresItselfAMirror(sourceText: string): boolean {
  return MIRROR_MARKER.test(sourceText.split("\n", 1)[0] ?? "");
}

export function isWireModule(relativePath: string, sourceText: string): boolean {
  return relativePath.startsWith("src/types/") || declaresItselfAMirror(sourceText);
}

// ── what counts as the fixture surface ───────────────────────────────────────
// Everything a CONSUMER writes to describe the server's answer. `src/dev/` is in it because the
// gallery fixtures are hydrated straight into the store, and `fixtures.ts` anywhere is in it
// because that is where a per-file literal goes when it is promoted to a shared one.

export function isFixtureSurface(relativePath: string): boolean {
  return (
    /\.(test|spec)\.tsx?$/.test(relativePath) ||
    /(^|\/)fixtures?(\.ts|\.tsx|\/)/.test(relativePath) ||
    relativePath.startsWith("src/test/") ||
    relativePath.startsWith("src/dev/") ||
    /^e2e([^/]*)\//.test(relativePath) ||
    relativePath.startsWith("perf/")
  );
}

// ── program construction ─────────────────────────────────────────────────────

const SCANNED_ROOTS = ["src", "e2e", "e2e-production", "e2e-chats", "perf"];

const GUARD_COMPILER_OPTIONS: ts.CompilerOptions = {
  target: ts.ScriptTarget.ES2022,
  lib: ["lib.es2022.d.ts", "lib.dom.d.ts", "lib.dom.iterable.d.ts"],
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  allowImportingTsExtensions: true,
  resolveJsonModule: true,
  jsx: ts.JsxEmit.ReactJSX,
  strict: true,
  skipLibCheck: true,
  noEmit: true,
};

function walkTypeScriptFiles(dir: string, out: string[]): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "node_modules" || entry === "dist") continue;
      walkTypeScriptFiles(full, out);
    } else if (/\.tsx?$/.test(entry) && !entry.endsWith(".d.ts")) {
      out.push(full);
    }
  }
  return out;
}

/** The dashboard root, resolved from this file rather than from the process cwd. */
export function dashboardRoot(): string {
  return path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
}

export function dashboardSourceFiles(root = dashboardRoot()): string[] {
  return SCANNED_ROOTS.flatMap((dir) => walkTypeScriptFiles(path.join(root, dir), []));
}

export function buildDashboardProgram(root = dashboardRoot()): ts.Program {
  return ts.createProgram(dashboardSourceFiles(root), GUARD_COMPILER_OPTIONS);
}

/**
 * A program whose extra files live only in memory, laid over the real tree so they can import the
 * REAL wire types. This is how the planted-bypass tests run the production rules against sources
 * that must never exist on disk.
 */
export function buildProgramWithVirtualFiles(
  virtualFiles: Readonly<Record<string, string>>,
  root = dashboardRoot(),
): ts.Program {
  const absolute = new Map<string, string>();
  // The directories the virtual files live in, every level of them. `moduleResolution: bundler`
  // asks `directoryExists` before it will look inside a folder, and the planted directory is not on
  // disk — so without this a planted file importing ANOTHER planted file fails to resolve and
  // degrades to `any`. Rule 3 then fires for the wrong reason and the test looks like it caught
  // something. A harness that cannot express a two-module case would silently prove nothing later.
  const directories = new Set<string>();
  for (const [relative, text] of Object.entries(virtualFiles)) {
    const fileName = path.join(root, relative);
    absolute.set(fileName, text);
    for (let dir = path.dirname(fileName); dir.startsWith(root); dir = path.dirname(dir)) {
      if (directories.has(dir)) break;
      directories.add(dir);
    }
  }
  const host = ts.createCompilerHost(GUARD_COMPILER_OPTIONS, true);
  const realGetSourceFile = host.getSourceFile.bind(host);
  const realFileExists = host.fileExists.bind(host);
  const realReadFile = host.readFile.bind(host);
  const realDirectoryExists = host.directoryExists?.bind(host);
  host.getSourceFile = (fileName, languageVersion, onError, shouldCreate) => {
    const text = absolute.get(path.normalize(fileName));
    return text === undefined
      ? realGetSourceFile(fileName, languageVersion, onError, shouldCreate)
      : ts.createSourceFile(fileName, text, languageVersion, true);
  };
  host.fileExists = (fileName) => absolute.has(path.normalize(fileName)) || realFileExists(fileName);
  host.readFile = (fileName) => absolute.get(path.normalize(fileName)) ?? realReadFile(fileName);
  host.directoryExists = (directoryName) =>
    directories.has(path.normalize(directoryName).replace(/[\\/]+$/, "")) ||
    (realDirectoryExists?.(directoryName) ?? false);

  // The wire modules are pulled in explicitly: a virtual file that never imports them would
  // otherwise leave the vocabulary empty and every rule vacuous.
  const wireModules = dashboardSourceFiles(root).filter((file) =>
    isWireModule(toRelative(root, file), readFileSync(file, "utf-8")),
  );
  return ts.createProgram([...absolute.keys(), ...wireModules], GUARD_COMPILER_OPTIONS, host);
}

function toRelative(root: string, fileName: string): string {
  return path.relative(root, fileName).split(path.sep).join("/");
}

// ── the sweep ────────────────────────────────────────────────────────────────

/** A type reference the checker could not resolve — reported, never skipped. */
const UNRESOLVED = "<unresolved>";

class WireVocabulary {
  private readonly symbols = new Set<ts.Symbol>();

  constructor(
    private readonly checker: ts.TypeChecker,
    program: ts.Program,
    root: string,
  ) {
    for (const sourceFile of program.getSourceFiles()) {
      if (sourceFile.isDeclarationFile) continue;
      const relative = toRelative(root, sourceFile.fileName);
      if (relative.startsWith("..") || relative.includes("node_modules")) continue;
      if (!isWireModule(relative, sourceFile.getFullText())) continue;
      const moduleSymbol = checker.getSymbolAtLocation(sourceFile);
      if (!moduleSymbol) continue;
      for (const exported of checker.getExportsOfModule(moduleSymbol)) {
        const typeLike =
          ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias | ts.SymbolFlags.Enum;
        if (exported.getFlags() & typeLike) this.symbols.add(exported);
      }
    }
  }

  get size(): number {
    return this.symbols.size;
  }

  has(symbol: ts.Symbol | undefined): boolean {
    return symbol !== undefined && this.symbols.has(symbol);
  }

  private unalias(symbol: ts.Symbol | undefined): ts.Symbol | undefined {
    if (!symbol) return undefined;
    if (!(symbol.flags & ts.SymbolFlags.Alias)) return symbol;
    try {
      return this.checker.getAliasedSymbol(symbol);
    } catch {
      return symbol;
    }
  }

  /**
   * The wire type a written type ANNOTATION reaches, following import aliases and local
   * `type X = …` indirection, and descending into arrays, unions, type arguments and members —
   * so `Partial<TaskDocNode>`, `LifecycleProjection[]`, `Record<string, Analytics>` and a local
   * alias of any of them all answer the same way. Returns `UNRESOLVED` for a name the checker
   * cannot bind, which the caller reports rather than ignores.
   */
  mentionedByTypeNode(node: ts.TypeNode, depth = 0, seen = new Set<ts.Symbol>()): string | null {
    if (depth > 12) return UNRESOLVED;
    let found: string | null = null;
    const visit = (child: ts.Node): void => {
      if (found) return;
      if (ts.isTypeReferenceNode(child)) {
        const name = ts.isQualifiedName(child.typeName) ? child.typeName.right : child.typeName;
        // `as const` is the one type name here that is not a hole: it can only NARROW a literal,
        // and an impossible field is still rejected through it (verified against this TypeScript).
        if (name.getText() === "const") return;
        const symbol = this.unalias(this.checker.getSymbolAtLocation(name));
        // A name the checker cannot bind comes back as an "unknown" symbol with NO declarations
        // rather than as `undefined`. Either way the guard cannot say what the type is, so it says
        // so — "not understood" has to read as a failure, not as a pass.
        if (!symbol || (symbol.getDeclarations() ?? []).length === 0) {
          found = UNRESOLVED;
          return;
        }
        if (this.has(symbol)) {
          found = symbol.getName();
          return;
        }
        if (!seen.has(symbol)) {
          seen.add(symbol);
          for (const declaration of symbol.getDeclarations() ?? []) {
            if (!ts.isTypeAliasDeclaration(declaration)) continue;
            const inner = this.mentionedByTypeNode(declaration.type, depth + 1, seen);
            if (inner) {
              found = inner;
              return;
            }
          }
        }
      }
      if (ts.isTypeQueryNode(child)) {
        const queried = this.checker.getTypeAtLocation(child);
        const symbol = queried.aliasSymbol ?? queried.getSymbol();
        if (this.has(symbol)) {
          found = symbol!.getName();
          return;
        }
      }
      ts.forEachChild(child, visit);
    };
    visit(node);
    return found;
  }

  /** The wire type a RESOLVED type reaches — used for contextual types, which have no syntax. */
  mentionedByType(type: ts.Type, depth = 0, seen = new Set<ts.Type>()): string | null {
    if (depth > 8 || seen.has(type)) return null;
    seen.add(type);
    const symbol = type.aliasSymbol ?? type.getSymbol();
    if (this.has(symbol)) return symbol!.getName();
    if (type.isUnionOrIntersection()) {
      for (const member of type.types) {
        const hit = this.mentionedByType(member, depth + 1, seen);
        if (hit) return hit;
      }
    }
    const reference = type as ts.TypeReference;
    if (reference.typeArguments) {
      for (const argument of reference.typeArguments) {
        const hit = this.mentionedByType(argument, depth + 1, seen);
        if (hit) return hit;
      }
    }
    return null;
  }

  /**
   * The wire OBJECT shapes a contextual type admits, once `undefined`/`null` are stripped — or
   * null when the slot is absorbing (an index signature), not a wire type, or carries even one
   * member this cannot read.
   *
   * A UNION answers with EVERY member rather than with nothing. That is the point: TypeScript's
   * excess-property check against a non-discriminated union only requires each property to exist
   * in SOME member, so `SubTaskRow = TaskSubTaskRefNode | SeriesSubTaskNode` accepts a row bearing
   * `linkedLifecycleId` (only the first declares it) AND `createdAt` (only the second does) — a
   * blend of two `extra="forbid"` server models that neither one could ever send. That pair is the
   * mirror's own worked example: `types/projection.ts` carries the comment explaining that the two
   * models were once collapsed into one interface, and this is the shape of the defect it caused.
   *
   * Still conservative in the other direction: one member the guard cannot name (an anonymous
   * literal in a discriminated union, say) abandons the whole slot rather than measuring a value
   * against half a union.
   */
  wireSlotTarget(type: ts.Type): WireSlot | null {
    const candidates = (type.isUnion() ? type.types : [type]).filter(
      (member) => !(member.flags & (ts.TypeFlags.Undefined | ts.TypeFlags.Null)),
    );
    if (candidates.length === 0) return null;
    const members: ts.Type[] = [];
    for (const candidate of candidates) {
      const member = this.wireObject(candidate);
      if (!member) return null;
      members.push(member);
    }
    // The name a finding reports is the one WRITTEN at the slot, so a union alias names itself.
    const alias = type.aliasSymbol;
    const name =
      alias && this.has(alias)
        ? alias.getName()
        : members.map((member) => nameOfType(member) ?? UNRESOLVED).join(" | ");
    return { name, members };
  }

  /** One union member (or a whole non-union slot) read as a wire object type. */
  private wireObject(type: ts.Type): ts.Type | null {
    let target = type;
    if (!this.has(target.aliasSymbol ?? target.getSymbol())) {
      // `Partial<TaskDocNode>` is the shape every builder here takes, and it declares exactly the
      // wire type's property NAMES — so unwrap a single-argument alias and keep reading. Anything
      // with two arguments (`Record<string, Analytics>`) is left alone; it absorbs keys anyway.
      const [sole] = target.aliasTypeArguments ?? [];
      if (!sole || (target.aliasTypeArguments ?? []).length !== 1) return null;
      if (!this.has(sole.aliasSymbol ?? sole.getSymbol())) return null;
      target = sole;
    }
    if (this.checker.getIndexInfosOfType(target).length > 0) return null;
    return target;
  }
}

/** A wire-typed slot: the name written there, and every object shape it admits. */
export interface WireSlot {
  readonly name: string;
  /** One member for an ordinary slot; two or more when the slot is a union of wire types. */
  readonly members: readonly ts.Type[];
}

function nameOfType(type: ts.Type): string | undefined {
  return (type.aliasSymbol ?? type.getSymbol())?.getName();
}

/**
 * Lines carrying a real compiler-suppression directive.
 *
 * Scanned as TOKENS rather than matched against raw lines, which matters twice over: this repo's
 * `contract.test.ts` discusses `@ts-expect-error` in prose three times without using it, and this
 * guard's own test file carries planted directives inside template literals. A line-wise regex
 * calls both of those a suppression; the scanner sees one as comment prose and the other as part
 * of a string token. The pattern mirrors TypeScript's own `commentDirectiveRegEx`, which honours
 * the directive only in a single-line comment.
 */
function directiveLines(sourceFile: ts.SourceFile): number[] {
  const text = sourceFile.getFullText();
  const seen = new Set<number>();
  const lines: number[] = [];
  const consider = (ranges: readonly ts.CommentRange[] | undefined): void => {
    for (const range of ranges ?? []) {
      if (range.kind !== ts.SyntaxKind.SingleLineCommentTrivia || seen.has(range.pos)) continue;
      seen.add(range.pos);
      if (!DIRECTIVE.test(text.slice(range.pos, range.end))) continue;
      lines.push(sourceFile.getLineAndCharacterOfPosition(range.pos).line + 1);
    }
  };
  const visit = (node: ts.Node): void => {
    consider(ts.getLeadingCommentRanges(text, node.getFullStart()));
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return lines.sort((left, right) => left - right);
}

// Mirrors TypeScript's own `commentDirectiveRegEx`, which honours a suppression only in a
// SINGLE-LINE comment. Read from real comment TRIVIA rather than from raw lines, which matters
// twice over: `contract.test.ts` discusses `@ts-expect-error` in prose three times without using
// it, and this guard's own test file carries planted directives inside template literals. A
// line-wise regex calls both of those a suppression; trivia sees neither.
const DIRECTIVE = /^\/\/\/?\s*@ts-(expect-error|ignore)\b/;

/**
 * Every type name the guard treats as a wire type. Exported so the guard's own tests can refuse a
 * VACUOUS run: if the mirror-marker convention lapsed, this would come back empty and all four
 * rules would pass in silence.
 */
export function wireTypeNames(program: ts.Program, root = dashboardRoot()): string[] {
  const checker = program.getTypeChecker();
  const names: string[] = [];
  for (const sourceFile of program.getSourceFiles()) {
    if (sourceFile.isDeclarationFile) continue;
    const relative = toRelative(root, sourceFile.fileName);
    if (relative.startsWith("..") || relative.includes("node_modules")) continue;
    if (!isWireModule(relative, sourceFile.getFullText())) continue;
    const moduleSymbol = checker.getSymbolAtLocation(sourceFile);
    if (!moduleSymbol) continue;
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      const typeLike = ts.SymbolFlags.Interface | ts.SymbolFlags.TypeAlias | ts.SymbolFlags.Enum;
      if (exported.getFlags() & typeLike) names.push(`${relative}:${exported.getName()}`);
    }
  }
  return names.sort();
}

export function collectWireFixtureFindings(
  program: ts.Program,
  root = dashboardRoot(),
): WireFixtureFinding[] {
  const checker = program.getTypeChecker();
  const vocabulary = new WireVocabulary(checker, program, root);
  const findings: WireFixtureFinding[] = [];

  for (const sourceFile of program.getSourceFiles()) {
    if (sourceFile.isDeclarationFile) continue;
    const relative = toRelative(root, sourceFile.fileName);
    if (relative.startsWith("..") || relative.includes("node_modules")) continue;
    const fixtureSurface = isFixtureSurface(relative);
    const lineOf = (node: ts.Node): number =>
      sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1;
    const push = (
      rule: WireFixtureRule,
      line: number,
      key: string,
      detail: string,
    ): void => {
      findings.push({ file: relative, line, rule, key: `${relative} :: ${key}`, detail, fixtureSurface });
    };

    const visit = (node: ts.Node): void => {
      if (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) {
        const written = node.type.getText();
        const asserted = vocabulary.mentionedByTypeNode(node.type);
        if (asserted) {
          // RULE 1 — the assertion names a wire type (directly, through an alias, or inside a
          // generic). Everywhere, because a builder that does this in app code is a fixture too.
          push("wire-cast", lineOf(node), `as ${written}`, `asserts ${asserted}`);
        } else if (fixtureSurface && written !== "const") {
          // RULE 2 — the assertion does NOT name a wire type but lands in a wire-typed slot:
          // `as never`, `as any`, `as unknown`. Rule 1 is blind to these by construction.
          const contextual = checker.getContextualType(node);
          const target = contextual ? vocabulary.mentionedByType(contextual) : null;
          if (target) {
            push(
              "wire-position-cast",
              lineOf(node),
              `as ${written} into ${target}`,
              `asserted \`as ${written}\` where ${target} is expected`,
            );
          }
        }
      }

      // RULES 3 and 4 — what reaches a wire-typed slot, read from the VALUE rather than from any
      // syntax. Both exist because the two remaining evasions carry no assertion at all.
      if (fixtureSurface && isCandidateForExcessCheck(node)) {
        const contextual = checker.getContextualType(node);
        const slot = contextual ? vocabulary.wireSlotTarget(contextual) : null;
        // A FRESH object literal is TypeScript's own job and is skipped — EXCEPT against a union,
        // which is exactly where TypeScript stops doing that job properly: its excess-property
        // check there only asks that each property exist in SOME member, so a fresh literal can
        // carry fields from two mutually exclusive server models and still compile.
        const fresh = ts.isObjectLiteralExpression(node);
        if (slot && (!fresh || slot.members.length > 1)) {
          const valueType = checker.getTypeAtLocation(node);
          const targetName = slot.name;
          // RULE 3 — an `any`-typed value reaching a wire-typed slot. `JSON.parse`, an untyped
          // helper and a `: any` local all answer `any`, and `any` assigns to anything WITHOUT an
          // assertion for rule 1 to find and without properties for rule 4 to weigh. It is also
          // the shape a "checked" helper takes when the check is vacuous — passing `any` to a
          // function whose parameter type is the wire type proves nothing, which is the mistake
          // `fixtures/wire.ts::reparsed` was making before this rule was written.
          if (valueType.flags & ts.TypeFlags.Any) {
            push(
              "wire-any-value",
              lineOf(node),
              `any into ${targetName}`,
              `an \`any\`-typed value reaches ${targetName}, so nothing checks it`,
            );
          }
          // RULE 4 — a shape the wire slot could never hold. Two ways that happens: a property no
          // member of the slot declares (excess-property checking extended to NON-FRESH values,
          // which is what TypeScript stops doing once a literal has been through a variable or
          // `Object.assign`), or — on a union — a blend of properties no SINGLE member declares.
          const verdict = excessPropertyVerdict(checker, vocabulary, valueType, slot);
          if (verdict) {
            push(
              "wire-excess-property",
              lineOf(node),
              `${targetName} + ${verdict.properties.join(",")}`,
              verdict.detail,
            );
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);

    if (fixtureSurface) {
      // RULE 5 — a fixture file may not silence the compiler without saying why. An
      // `@ts-expect-error` above a wire-typed literal makes the impossible field compile.
      for (const line of directiveLines(sourceFile)) {
        push("compiler-suppression", line, "@ts-expect-error", "compiler diagnostic suppressed");
      }
    }
  }
  return findings;
}

/**
 * Expressions worth an excess-property reading: things that carry a shape but are not fresh —
 * plus the object literal, which IS fresh and is admitted only so the union case can be read (see
 * the `fresh` guard at the call site, which is what keeps this from second-guessing `tsc`).
 */
function isCandidateForExcessCheck(node: ts.Node): node is ts.Expression {
  return (
    ts.isIdentifier(node) ||
    ts.isCallExpression(node) ||
    ts.isPropertyAccessExpression(node) ||
    ts.isObjectLiteralExpression(node)
  );
}

interface ExcessPropertyVerdict {
  /** The property names that make the value impossible — what the finding's key is built from. */
  readonly properties: string[];
  readonly detail: string;
}

function excessPropertyVerdict(
  checker: ts.TypeChecker,
  vocabulary: WireVocabulary,
  valueType: ts.Type,
  slot: WireSlot,
): ExcessPropertyVerdict | null {
  // A union VALUE says nothing this rule can act on, and neither does `any`/`unknown`. An
  // INTERSECTION is read on purpose: `Object.assign(node, { … })` answers `Wire & { … }`, which is
  // assignable to the wire type and is the second of the two ways a value smuggles a field past
  // `tsc`.
  if (valueType.isUnion()) return null;
  if (valueType.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) return null;
  const shaped = (valueType.flags & ts.TypeFlags.Object) !== 0 || valueType.isIntersection();
  if (!shaped) return null;
  if (!valueType.isIntersection() && vocabulary.has(valueType.aliasSymbol ?? valueType.getSymbol())) {
    return null;
  }
  if (checker.getIndexInfosOfType(valueType).length > 0) return null;

  const carried = checker.getPropertiesOfType(valueType).map((property) => property.name);
  const members = slot.members.map((member) => ({
    name: nameOfType(member) ?? slot.name,
    properties: new Set(checker.getPropertiesOfType(member).map((property) => property.name)),
  }));

  // (a) A property NO member declares. For an ordinary one-member slot this is the whole rule, and
  // it reads exactly as it did before unions were handled.
  const undeclared = carried.filter((name) => !members.some((member) => member.properties.has(name)));
  if (undeclared.length > 0) {
    return {
      properties: undeclared,
      detail: `carries ${undeclared.join(", ")}, which ${slot.name} does not declare`,
    };
  }

  // (b) Every property exists in SOME member, but no SINGLE member declares them all — the value is
  // a blend of two mutually exclusive server models, which is what TypeScript's excess-property
  // check against a non-discriminated union lets through.
  if (members.length < 2) return null;
  if (members.some((member) => carried.every((name) => member.properties.has(name)))) return null;
  const blended = carried.filter(
    (name) => members.filter((member) => member.properties.has(name)).length < members.length,
  );
  return {
    properties: blended,
    detail:
      `combines ${blended.join(", ")}, which no single member of ${slot.name} declares — ` +
      `${members.map((member) => member.name).join(" and ")} are mutually exclusive shapes`,
  };
}

// ── registry reconciliation ──────────────────────────────────────────────────

export interface RegistryVerdict {
  /** Sites the guard found that no registry entry covers — a new fixture hole. */
  readonly unregistered: string[];
  /** Registry entries that no longer match any site — a stale exemption. */
  readonly spent: string[];
  /** Entries whose occurrence count moved — a new cast hiding behind an old exemption. */
  readonly miscounted: string[];
  /** Entries with no written reason. */
  readonly unreasoned: string[];
}

export function reconcileWithRegistry(
  findings: readonly WireFixtureFinding[],
  registry: Readonly<Record<string, SanctionedSite>>,
): RegistryVerdict {
  const observed = new Map<string, number>();
  for (const finding of findings) observed.set(finding.key, (observed.get(finding.key) ?? 0) + 1);

  const unregistered: string[] = [];
  const miscounted: string[] = [];
  for (const [key, count] of observed) {
    const entry = registry[key];
    if (!entry) unregistered.push(`${key}  (${count}×)`);
    else if (entry.count !== count) miscounted.push(`${key}  registry ${entry.count}× vs found ${count}×`);
  }
  const spent = Object.keys(registry).filter((key) => !observed.has(key));
  const unreasoned = Object.entries(registry)
    .filter(([, entry]) => entry.reason.trim().length === 0)
    .map(([key]) => key);

  return { unregistered: unregistered.sort(), spent: spent.sort(), miscounted: miscounted.sort(), unreasoned };
}
