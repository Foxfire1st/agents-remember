#!/usr/bin/env node
/**
 * Renew the pinned-host fixture from an installed @getpaseo distribution.
 *
 * The fixture at `server/fixtures/paseo-0-16/` is a copy of the files the ACP discovery reads
 * out of the installed `@getpaseo` packages — not a hand-written stand-in. This script is the
 * only supported way to write it:
 *
 *   node server/fixtures/renew-paseo-fixture.mjs [<installed @getpaseo dir>] [--check]
 *
 * `<installed @getpaseo dir>` defaults to the prefix this build installs into
 * (`~/.local/share/agents-remember/paseo/prefix/node_modules/@getpaseo`) and must hold the
 * version `paseo_host/contract.json` pins. `--check` copies nothing: it fails when any fixture
 * file differs from its source, or when `fixture.json` does not describe the tree.
 *
 * What each file is for:
 *   - `@getpaseo/server/package.json` — the version the ACP providers file publishes.
 *   - `@getpaseo/server/dist/server/server/plugins/plugin-process.js` — the argv1 path the
 *     plugin's own process names.
 *   - `@getpaseo/server/dist/server/server/agent/provider-registry.js` — the class imports and
 *     `PROVIDER_CLIENT_FACTORIES`, the id-to-class map rule 1 reads.
 *   - `@getpaseo/server/dist/server/server/agent/create-agent-mode.js` — the mode vocabulary the
 *     registry's entries name.
 *   - `@getpaseo/server/dist/server/server/agent/providers/**` — one file per client class, so
 *     the discovery can read whether the class sits on an ACP connection.
 *   - `@getpaseo/protocol/dist/provider-manifest.js` — `AGENT_PROVIDER_DEFINITIONS`, with the
 *     nested `modes` members the depth scan must not mistake for provider ids.
 *
 * Owner: the host-contract route of the Paseo plugin (`paseo_host/contract.json`).
 * Test: `server/acp_providers.test.ts`. Consumers: that test only.
 */
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, "paseo-0-16");
const CONTRACT = join(HERE, "..", "..", "..", "paseo_host", "contract.json");
const DEFAULT_SOURCE = join(homedir(), ".local", "share", "agents-remember", "paseo", "prefix", "node_modules", "@getpaseo");

/** Every fixture path, relative to the fixture root (`@getpaseo/…`). */
export const FILES = [
  "@getpaseo/server/package.json",
  "@getpaseo/server/dist/server/server/plugins/plugin-process.js",
  "@getpaseo/server/dist/server/server/agent/create-agent-mode.js",
  "@getpaseo/server/dist/server/server/agent/provider-registry.js",
  "@getpaseo/server/dist/server/server/agent/providers/acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/generic-acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/copilot-acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/cursor-acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/kimi-acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/kiro-acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/trae-acp-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/codex-app-server-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/mock-load-test-agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/mock-slow-provider.js",
  "@getpaseo/server/dist/server/server/agent/providers/claude/agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/opencode/runtime-client.js",
  "@getpaseo/server/dist/server/server/agent/providers/pi/agent.js",
  "@getpaseo/server/dist/server/server/agent/providers/omp/agent.js",
  "@getpaseo/protocol/dist/provider-manifest.js",
];

const sha256 = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");
const version = (source, name) => JSON.parse(readFileSync(join(source, name, "package.json"), "utf8")).version;

function main(argv) {
  const check = argv.includes("--check");
  const source = argv.find((arg) => !arg.startsWith("--")) ?? DEFAULT_SOURCE;
  const pinned = JSON.parse(readFileSync(CONTRACT, "utf8")).version;
  if (!existsSync(source)) throw new Error(`no installed @getpaseo at ${source}; install the pinned host first`);
  const versions = {
    "@getpaseo/server": version(source, "server"),
    "@getpaseo/protocol": version(source, "protocol"),
    "@getpaseo/plugin": version(source, "plugin"),
    "@getpaseo/cli": version(source, "cli"),
  };
  for (const [name, found] of Object.entries(versions)) {
    if (found !== pinned) throw new Error(`${name} at ${source} is ${found}, not the pinned ${pinned}`);
  }
  const files = FILES.map((path) => {
    const from = join(source, path.replace(/^@getpaseo\//, ""));
    if (!existsSync(from)) throw new Error(`the distribution has no ${path}`);
    return { path, source: from, sha256: sha256(from), bytes: readFileSync(from).length };
  });
  const record = {
    schema: "ar-paseo-fixture/v1",
    description: "Copied source of the installed @getpaseo distribution: the files the ACP discovery reads.",
    source: { distribution: "@getpaseo", version: pinned, installedAt: source, canonicalPrefix: DEFAULT_SOURCE, versions },
    renewal: "node server/fixtures/renew-paseo-fixture.mjs [<installed @getpaseo dir>]; verify with --check",
    owner: "host-contract route (package_data/paseo_host/contract.json)",
    test: "server/acp_providers.test.ts",
    consumers: ["server/acp_providers.test.ts"],
    files: files.map(({ path, sha256: digest, bytes }) => ({ path, sha256: digest, bytes })),
  };
  if (check) {
    if (!existsSync(join(FIXTURE, "README.md"))) throw new Error("the fixture has lost its README.md");
    const wrong = files.filter(({ path, sha256: digest }) => {
      const here = join(FIXTURE, path);
      return !existsSync(here) || sha256(here) !== digest;
    });
    const recorded = JSON.parse(readFileSync(join(FIXTURE, "fixture.json"), "utf8"));
    if (JSON.stringify(recorded.files) !== JSON.stringify(record.files)) wrong.push({ path: "fixture.json" });
    if (wrong.length > 0) throw new Error(`the fixture is stale: ${wrong.map(({ path }) => path).join(", ")}`);
    process.stdout.write(`fixture current: ${files.length} files from @getpaseo ${pinned}\n`);
    return;
  }
  // Only the copied distribution is replaced: the README that documents this fixture is kept, so a
  // renewal never leaves the directory undocumented.
  rmSync(join(FIXTURE, "@getpaseo"), { recursive: true, force: true });
  mkdirSync(FIXTURE, { recursive: true });
  for (const { path, source: from } of files) {
    const to = join(FIXTURE, path);
    mkdirSync(dirname(to), { recursive: true });
    cpSync(from, to);
  }
  writeFileSync(join(FIXTURE, "fixture.json"), `${JSON.stringify(record, null, 2)}\n`);
  process.stdout.write(`wrote ${files.length} files (${files.reduce((sum, { bytes }) => sum + bytes, 0)} bytes) from @getpaseo ${pinned}\n`);
}

main(process.argv.slice(2));
