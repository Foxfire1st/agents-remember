/**
 * MIK-R216 rule 1: which providers are on an ACP connection, read from the Paseo
 * installation the plugin runs in (D613).
 *
 * The plugin does not carry Paseo's provider list. It finds the installation from
 * its own process — `process.argv[1]` is
 * `@getpaseo/server/dist/server/server/plugins/plugin-process.js` — and resolves
 * each provider id the way Paseo does: a built-in id to its class in
 * `PROVIDER_CLIENT_FACTORIES`, a configured provider that extends `acp` to the
 * class `addDerivedProviders` would build for its id, and one that extends a
 * built-in to that built-in's class. A provider is on an ACP connection when that
 * class is built on `ACPAgentClient`, and nowhere else: Paseo has no capability
 * flag for helpers, and a scan for the event's word cannot tell a module that
 * emits it from the one every ACP client imports.
 *
 * The result is published at `<PASEO_HOME>/agents-remember/acp-providers.json`,
 * staged and renamed the way `host-activity.json` is, so the launch can tell an
 * ACP seat from the others before it compiles the seat's prompt. The file also
 * carries the drop-outs: a provider that reported helpers natively is out of this
 * packet for good (D615), and the time of the first report is kept across starts.
 *
 * Every read failure is a verdict, never an exception: a provider the plugin
 * cannot place is not ACP with the reason recorded, so the seat keeps today's
 * behaviour and the launch receipt can say why (rule 1's failure behaviour).
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join, sep } from "node:path";

/** Rule 1's file beside the plugin's own `host-activity.json` and `role-agents.json`. */
export const ACP_PROVIDERS_FILE = "acp-providers.json";

/** The one schema the file carries and the launch reads. */
export const ACP_PROVIDERS_SCHEMA = "ar-acp-providers/v1";

/** The scope directory `argv1` sits inside; `server` and `protocol` lie beside it. */
const PASEO_SCOPE = "@getpaseo";

/** Rule 1's two files, under the `@getpaseo` root. */
const REGISTRY_RELATIVE = ["server", "dist", "server", "server", "agent", "provider-registry.js"];
const MANIFEST_RELATIVE = ["protocol", "dist", "provider-manifest.js"];

/** The base class every ACP connection is built on; `acp-agent.js` declares it. */
const ACP_CLIENT_CLASS = "ACPAgentClient";

/** What `addDerivedProviders` builds for a configured provider that extends `acp`. */
const GENERIC_ACP_CLASS = "GenericACPAgentClient";
const DERIVED_ACP_CLASSES: ReadonlyMap<string, string> = new Map([
  ["cursor", "CursorACPAgentClient"],
  ["kimi", "KimiACPAgentClient"],
  ["kiro", "KiroACPAgentClient"],
  ["traecli", "TraeACPAgentClient"],
]);

/** A chain longer than this is a cycle in a class tree we cannot read, not a provider. */
const CLASS_CHAIN_LIMIT = 16;

export interface AcpProviderVerdict {
  readonly class: string | null;
  readonly acp: boolean;
  readonly reason: string;
}

export interface AcpProvidersDocument {
  readonly schema: "ar-acp-providers/v1";
  readonly paseoVersion: string | null;
  readonly readAt: string;
  readonly files: readonly string[];
  readonly providers: Readonly<Record<string, AcpProviderVerdict>>;
  readonly dropouts: Readonly<Record<string, string>>;
}

export interface AcpProviderSet {
  /** The document as the last read or drop-out left it, exactly as it was written. */
  readonly document: AcpProvidersDocument;
  /** Rule 2's question of one seat: is this provider on an ACP connection? */
  isAcp(providerId: string): boolean;
  /** Rule 1 and D615: the provider reported helpers natively; it is out from now on. */
  noteNativeProvider(providerId: string): void;
  /** Choices follow Paseo: re-read the installation, keep the drop-outs, write. */
  refresh(): AcpProvidersDocument;
}

export type AcpProviderSetOptions = {
  /** `process.argv[1]`, or a fixture's stand-in for it. */
  readonly argv1?: string | null;
  readonly now?: () => Date;
  /** The home fallback: the plugin's `home` argument wins over `PASEO_HOME`. */
  readonly env?: NodeJS.ProcessEnv;
  /** Rule 7's `acp-provider-unread` row, once per provider per set. */
  readonly onUnread?: (providerId: string, reason: string) => void;
};

/** The path the plugin publishes, and the launch reads. */
function acpProvidersPath(home: string): string {
  return join(home, "agents-remember", ACP_PROVIDERS_FILE);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isIdentifierStart(char: string): boolean {
  return (char >= "a" && char <= "z") || (char >= "A" && char <= "Z") || char === "_" || char === "$";
}

function isIdentifierPart(char: string): boolean {
  return isIdentifierStart(char) || (char >= "0" && char <= "9");
}

/** The end of the string literal opening at `start`, escapes and all. */
function endOfString(text: string, start: number): number {
  const quote = text[start];
  let i = start + 1;
  while (i < text.length) {
    if (text[i] === "\\") {
      i += 2;
      continue;
    }
    if (text[i] === quote) return i + 1;
    i += 1;
  }
  return text.length;
}

/** The end of the comment opening at `start`, in either form. */
function endOfComment(text: string, start: number): number {
  if (text[start + 1] === "/") {
    const newline = text.indexOf("\n", start + 2);
    return newline < 0 ? text.length : newline + 1;
  }
  const close = text.indexOf("*/", start + 2);
  return close < 0 ? text.length : close + 2;
}

/**
 * One object literal's body, found by its binding name. A `}` inside a string, a
 * comment or a template literal is not the end of it.
 */
function objectBody(text: string, name: string): string | null {
  const binding = new RegExp(`\\b${name}\\s*=\\s*\\{`, "u").exec(text);
  if (binding === null) return null;
  const open = binding.index + binding[0].length - 1;
  let depth = 0;
  let i = open;
  while (i < text.length) {
    const char = text[i];
    if (char === "/" && (text[i + 1] === "/" || text[i + 1] === "*")) {
      i = endOfComment(text, i);
      continue;
    }
    if (char === "\"" || char === "'" || char === "`") {
      i = endOfString(text, i);
      continue;
    }
    if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(open + 1, i);
    }
    i += 1;
  }
  return null;
}

/**
 * Rule 1's built-in ids: the entries of `AGENT_PROVIDER_DEFINITIONS` and their own
 * `id`s only.
 *
 * Each entry is an object at depth 2 of the array text, and it carries a `modes`
 * array one level deeper whose members have `id`s of their own ("default",
 * "auto"). A plain match over the array body would claim those mode ids as
 * providers, so the body is walked by brace and bracket depth, with strings and
 * comments skipped.
 */
function builtinIdsOf(manifest: string): string[] {
  const array = /\bAGENT_PROVIDER_DEFINITIONS\s*=\s*\[/u.exec(manifest);
  if (array === null) return [];
  const ids: string[] = [];
  let depth = 1;
  let i = array.index + array[0].length;
  while (i < manifest.length && depth > 0) {
    const char = manifest[i];
    if (char === "/" && (manifest[i + 1] === "/" || manifest[i + 1] === "*")) {
      i = endOfComment(manifest, i);
      continue;
    }
    if (char === "\"" || char === "'" || char === "`") {
      i = endOfString(manifest, i);
      continue;
    }
    if (char === "[" || char === "{") {
      depth += 1;
      i += 1;
      continue;
    }
    if (char === "]" || char === "}") {
      depth -= 1;
      i += 1;
      continue;
    }
    if (depth !== 2 || !isIdentifierStart(char)) {
      i += 1;
      continue;
    }
    let end = i + 1;
    while (end < manifest.length && isIdentifierPart(manifest[end])) end += 1;
    const key = manifest.slice(i, end);
    i = end;
    if (key !== "id") continue;
    let value = i;
    while (value < manifest.length && /\s/u.test(manifest[value])) value += 1;
    if (manifest[value] !== ":") continue;
    value += 1;
    while (value < manifest.length && /\s/u.test(manifest[value])) value += 1;
    if (manifest[value] !== "\"" && manifest[value] !== "'") continue;
    const close = endOfString(manifest, value) - 1;
    ids.push(manifest.slice(value + 1, close));
    i = close + 1;
  }
  return ids;
}

/**
 * Rule 1's id-to-class map: the entries of `PROVIDER_CLIENT_FACTORIES`. The key
 * stands before its factory and is quoted for an id like `mock-slow`; a factory's
 * parameters may sit on their own lines.
 */
function factoryClasses(registry: string): Map<string, string> {
  const factories = new Map<string, string>();
  const body = objectBody(registry, "PROVIDER_CLIENT_FACTORIES");
  if (body === null) return factories;
  const entry = /("?)([A-Za-z0-9_$-]+)\1\s*:\s*\([^)]*\)\s*=>\s*new\s+([A-Za-z0-9_$]+)/gu;
  for (const match of body.matchAll(entry)) factories.set(match[2], match[3]);
  return factories;
}

/**
 * The `import { Class } from "./file.js"` lines of one module: how a class name
 * becomes the file that declares it. Only a relative specifier is a file beside
 * the module; a package's own export map is not read.
 */
function classImports(text: string, file: string): Map<string, string> {
  const classes = new Map<string, string>();
  const pattern = /import\s*(?:type\s*)?\{([^}]*)\}\s*from\s*["']([^"']+)["']/gu;
  for (const match of text.matchAll(pattern)) {
    const specifier = match[2];
    if (!specifier.startsWith(".")) continue;
    const target = join(dirname(file), specifier);
    for (const named of match[1].split(",")) {
      const name = named.trim().replace(/^type\s+/u, "").split(/\s+as\s+/u)[0].trim();
      if (name !== "") classes.set(name, target);
    }
  }
  return classes;
}

/** One file's own declaration of one class, and the class it extends, if any. */
function declaredBase(text: string, name: string): { found: boolean; base: string | null } {
  const declaration = new RegExp(`(?:export\\s+)?class\\s+${name}\\b([^{]*)\\{`, "u").exec(text);
  if (declaration === null) return { found: false, base: null };
  const base = /\bextends\s+([A-Za-z0-9_$.]+)/u.exec(declaration[1]);
  return { found: true, base: base === null ? null : base[1] };
}

/** The files one discovery read, and their text: rule 1's `files`, each one once. */
class SourceFiles {
  readonly files: string[] = [];
  private readonly texts = new Map<string, string>();

  read(path: string): string | null {
    const cached = this.texts.get(path);
    if (cached !== undefined) return cached;
    let text: string;
    try {
      text = readFileSync(path, "utf8");
    } catch {
      return null;
    }
    this.texts.set(path, text);
    this.files.push(path);
    return text;
  }
}

/** What one class name resolves to: whether the chain was read whole, and whether it is ACP. */
interface ClassChain {
  readonly resolved: boolean;
  readonly acp: boolean;
}

/** The installed Paseo as rule 1 reads it. */
interface PaseoSource {
  /** The `@getpaseo` root the plugin's own process names, or null when it names none. */
  readonly root: string | null;
  /** The registry and the manifest both read: without them no provider can be placed. */
  readonly readable: boolean;
  readonly paseoVersion: string | null;
  /** Every file the discovery read, absolute, as it was read. */
  readonly files: readonly string[];
  readonly builtinIds: readonly string[];
  /** Provider id to client class, from `PROVIDER_CLIENT_FACTORIES`. */
  readonly factories: ReadonlyMap<string, string>;
  /** Rule 1's class chain: ACP iff it reaches `ACPAgentClient`. */
  resolveClass(className: string): ClassChain;
}

/** The `@getpaseo` directory in `argv1`: the plugin's own process names it. */
function paseoRootOf(argv1: string | null): string | null {
  if (argv1 === null || argv1 === "") return null;
  const segments = argv1.split(/[\\/]+/u);
  const at = segments.lastIndexOf(PASEO_SCOPE);
  if (at <= 0) return null;
  return segments.slice(0, at + 1).join(sep);
}

/** The installed `@getpaseo/server` version, or null when its manifest cannot be read. */
function packageVersion(source: SourceFiles, path: string): string | null {
  const text = source.read(path);
  if (text === null) return null;
  try {
    const version = asRecord(JSON.parse(text))?.["version"];
    return typeof version === "string" && version !== "" ? version : null;
  } catch {
    return null;
  }
}

class PaseoDiscovery implements PaseoSource {
  readonly root: string | null;
  readonly readable: boolean;
  readonly paseoVersion: string | null;
  readonly builtinIds: readonly string[];
  readonly factories: ReadonlyMap<string, string>;
  private readonly source = new SourceFiles();
  private readonly classFiles = new Map<string, string>();
  private readonly chains = new Map<string, { found: boolean; base: string | null } | null>();

  constructor(argv1: string | null) {
    this.root = paseoRootOf(argv1);
    this.factories = new Map();
    this.builtinIds = [];
    const registryFile = this.root === null ? null : join(this.root, ...REGISTRY_RELATIVE);
    const manifestFile = this.root === null ? null : join(this.root, ...MANIFEST_RELATIVE);
    this.paseoVersion =
      this.root === null ? null : packageVersion(this.source, join(this.root, "server", "package.json"));
    const registry = registryFile === null ? null : this.source.read(registryFile);
    const manifest = manifestFile === null ? null : this.source.read(manifestFile);
    this.readable = registry !== null && manifest !== null;
    if (registry === null || manifest === null) return;
    // `registryFile` and `manifestFile` are both non-null here: they were read.
    this.builtinIds = builtinIdsOf(manifest);
    this.factories = factoryClasses(registry);
    for (const [name, file] of classImports(registry, registryFile as string)) {
      this.classFiles.set(name, file);
    }
  }

  get files(): readonly string[] {
    return this.source.files;
  }

  /**
   * Rule 1: a provider is on an ACP connection when its class is built on
   * `ACPAgentClient`. The chain is followed through each file's own imports,
   * because a base such as `ACPAgentClient` is imported by the provider that
   * extends it and not by the registry. A class no import names, or a file that
   * cannot be read, leaves the chain unread — which is not the same answer as a
   * chain that ends at a class with no base.
   */
  resolveClass(className: string): ClassChain {
    const seen = new Set<string>();
    let current: string | null = className;
    while (current !== null) {
      if (current === ACP_CLIENT_CLASS) return { resolved: true, acp: true };
      if (seen.has(current) || seen.size >= CLASS_CHAIN_LIMIT) break;
      seen.add(current);
      const declared = this.declarationOf(current);
      if (declared === null || !declared.found) break;
      current = declared.base;
    }
    return { resolved: current === null, acp: false };
  }

  private declarationOf(className: string): { found: boolean; base: string | null } | null {
    const cached = this.chains.get(className);
    if (cached !== undefined) return cached;
    const file = this.classFiles.get(className);
    if (file === undefined) {
      this.chains.set(className, null);
      return null;
    }
    const text = this.source.read(file);
    if (text === null) {
      this.chains.set(className, null);
      return null;
    }
    for (const [name, target] of classImports(text, file)) {
      if (!this.classFiles.has(name)) this.classFiles.set(name, target);
    }
    const declaration = declaredBase(text, className);
    this.chains.set(className, declaration);
    return declaration;
  }
}

/** One provider's verdict before the drop-outs are laid over it. */
interface ResolvedProvider {
  readonly verdict: AcpProviderVerdict;
  /** Rule 7: this provider's read failed, and the plugin logs why once. */
  readonly unread: boolean;
}

function unread(reason: string): ResolvedProvider {
  return { verdict: { class: null, acp: false, reason }, unread: true };
}

function classified(className: string, reason: string, source: PaseoSource): ResolvedProvider {
  const chain = source.resolveClass(className);
  if (!chain.resolved) return unread("class-unresolved");
  if (!chain.acp) return { verdict: { class: className, acp: false, reason: "class-not-acp" }, unread: false };
  return { verdict: { class: className, acp: true, reason }, unread: false };
}

/**
 * Rule 1's resolution of one provider id, in the order Paseo resolves it: a
 * built-in id first (Paseo skips a built-in in `addDerivedProviders`), then the
 * host's configuration entry, whose `extends` is `acp` or a built-in id and
 * nothing else. A provider a Paseo plugin registers is in neither list, so it
 * lands in the last case and is not ACP for this packet.
 */
function resolveProvider(
  providerId: string,
  source: PaseoSource,
  configured: Readonly<Record<string, unknown>> | null,
): ResolvedProvider {
  const builtIn = source.builtinIds.includes(providerId);
  if (builtIn) {
    const className = source.factories.get(providerId);
    if (className === undefined) return unread("class-unresolved");
    return classified(className, "built-in", source);
  }
  if (configured === null || !Object.hasOwn(configured, providerId)) {
    return { verdict: { class: null, acp: false, reason: "not-built-in-or-configured" }, unread: false };
  }
  const entry = asRecord(configured[providerId]);
  if (entry === null) return unread("entry-unreadable");
  const base = entry["extends"];
  if (typeof base !== "string" || base === "") return unread("entry-unreadable");
  if (base === "acp") {
    return classified(DERIVED_ACP_CLASSES.get(providerId) ?? GENERIC_ACP_CLASS, "extends-acp", source);
  }
  if (source.builtinIds.includes(base)) {
    const className = source.factories.get(base);
    if (className === undefined) return unread("class-unresolved");
    return classified(className, `extends-builtin:${base}`, source);
  }
  return unread(`extends-unknown:${base}`);
}

/**
 * The ids the file names: Paseo's built-ins, then the host configuration's own
 * providers, then the drop-outs. The last group is a provider the host reported
 * helpers for — a plugin provider, kept so the file says why that seat gets
 * nothing.
 */
function providerIdsOf(
  source: PaseoSource,
  configured: Readonly<Record<string, unknown>> | null,
  dropouts: Readonly<Record<string, string>>,
): string[] {
  const ids: string[] = [];
  const seen = new Set<string>();
  const add = (id: string): void => {
    if (seen.has(id)) return;
    seen.add(id);
    ids.push(id);
  };
  for (const id of source.builtinIds) add(id);
  if (configured !== null) for (const id of Object.keys(configured)) add(id);
  for (const id of Object.keys(dropouts)) add(id);
  return ids;
}

class AcpProviderSetState implements AcpProviderSet {
  private current: AcpProvidersDocument;
  private readonly dropouts: Record<string, string>;
  /** Rule 7: `acp-provider-unread` is written once per provider in a plugin process. */
  private readonly noted = new Set<string>();
  private readonly home: string;
  private readonly argv1: string | null;
  private readonly clock: () => Date;
  private readonly onUnread: (providerId: string, reason: string) => void;

  constructor(
    home: string,
    argv1: string | null,
    clock: () => Date,
    onUnread: (providerId: string, reason: string) => void,
  ) {
    this.home = home;
    this.argv1 = argv1;
    this.clock = clock;
    this.onUnread = onUnread;
    this.dropouts = this.readDropouts();
    // Rule 1: the read is the plugin's start, so the launch that follows it finds a file.
    this.current = this.publish();
  }

  get document(): AcpProvidersDocument {
    return this.current;
  }

  isAcp(providerId: string): boolean {
    return this.current.providers[providerId]?.acp === true;
  }

  /**
   * D615: the first report is the one that is kept. A provider already out stays
   * out with its first time, and no second write is made for it.
   */
  noteNativeProvider(providerId: string): void {
    if (Object.hasOwn(this.dropouts, providerId)) return;
    this.dropouts[providerId] = this.clock().toISOString();
    this.current = this.publish();
  }

  refresh(): AcpProvidersDocument {
    this.current = this.publish();
    return this.current;
  }

  /** One read of the installation, the configuration and the drop-outs, then the write. */
  private publish(): AcpProvidersDocument {
    const source = new PaseoDiscovery(this.argv1);
    const configured = this.configured();
    const providers: Record<string, AcpProviderVerdict> = {};
    for (const id of providerIdsOf(source, configured, this.dropouts)) {
      const resolved = source.readable
        ? resolveProvider(id, source, configured)
        : unread("paseo-unreadable");
      // Rule 1: a provider that reports helpers natively is not ACP, whatever its class is.
      const verdict =
        source.readable && Object.hasOwn(this.dropouts, id)
          ? { class: resolved.verdict.class, acp: false, reason: "reports-helpers" }
          : resolved.verdict;
      providers[id] = verdict;
      if (resolved.unread) this.noteUnread(id, resolved.verdict.reason);
    }
    const document: AcpProvidersDocument = {
      schema: ACP_PROVIDERS_SCHEMA,
      paseoVersion: source.paseoVersion,
      readAt: this.clock().toISOString(),
      files: [...source.files],
      providers,
      dropouts: { ...this.dropouts },
    };
    this.write(document);
    return document;
  }

  private noteUnread(providerId: string, reason: string): void {
    if (this.noted.has(providerId)) return;
    this.noted.add(providerId);
    try {
      this.onUnread(providerId, reason);
    } catch {
      // The caller writes the row; a failing log never costs the seat its verdict.
    }
  }

  /**
   * Rule 1's host configuration. `null` means the file could not be read, and then
   * only the built-in ids can be named: a configured id cannot be known, and one
   * that is not known is not guessed at.
   */
  private configured(): Record<string, unknown> | null {
    if (this.home === "") return null;
    let text: string;
    try {
      text = readFileSync(join(this.home, "config.json"), "utf8");
    } catch {
      return null;
    }
    let document: unknown;
    try {
      document = JSON.parse(text);
    } catch {
      return null;
    }
    // A readable file that names no provider is a host with no configured providers.
    const providers = asRecord(asRecord(asRecord(document)?.["agents"])?.["providers"]);
    return providers ?? {};
  }

  /** Rule 1: the earlier drop-outs are kept at each start, so a restart drops nobody back in. */
  private readDropouts(): Record<string, string> {
    if (this.home === "") return {};
    let text: string;
    try {
      text = readFileSync(acpProvidersPath(this.home), "utf8");
    } catch {
      return {};
    }
    let document: unknown;
    try {
      document = JSON.parse(text);
    } catch {
      return {};
    }
    const dropouts = asRecord(asRecord(document)?.["dropouts"]);
    if (dropouts === null) return {};
    const kept: Record<string, string> = {};
    for (const [id, at] of Object.entries(dropouts)) {
      if (typeof at === "string" && at !== "") kept[id] = at;
    }
    return kept;
  }

  /**
   * Rule 1's write, the way `host-activity.json` is written: staged beside the
   * target and renamed, so a reader never sees half a document. A home the plugin
   * was not given writes nothing, and the read still runs.
   */
  private write(document: AcpProvidersDocument): void {
    if (this.home === "") return;
    const target = acpProvidersPath(this.home);
    try {
      mkdirSync(dirname(target), { recursive: true });
      const staging = `${target}.tmp`;
      writeFileSync(staging, JSON.stringify(document) + "\n", "utf8");
      renameSync(staging, target);
    } catch (error) {
      // A seat's verdict is worth more than the file: the launch then adds nothing
      // and says why, which is rule 1's failure behaviour.
      console.error("agents-remember acp providers publication failed", error);
    }
  }
}

/**
 * Rule 1 at the plugin's start: read the installation, publish the file, and keep
 * answering from it. `home` is the plugin's own home, falling back to
 * `PASEO_HOME`; `argv1` is the process's own path into the Paseo installation.
 */
export function createAcpProviderSet(
  home: string | undefined,
  options: AcpProviderSetOptions = {},
): AcpProviderSet {
  const env = options.env ?? process.env;
  const resolvedHome = home !== undefined && home !== "" ? home : (env["PASEO_HOME"] ?? "");
  const argv1 = options.argv1 === undefined ? (process.argv[1] ?? null) : options.argv1;
  return new AcpProviderSetState(
    resolvedHome,
    argv1,
    options.now ?? (() => new Date()),
    options.onUnread ?? (() => {}),
  );
}
