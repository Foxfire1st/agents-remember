import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  ACP_PROVIDERS_FILE,
  ACP_PROVIDERS_SCHEMA,
  createAcpProviderSet,
  type AcpProviderSet,
  type AcpProvidersDocument,
} from "./acp_providers.ts";

/**
 * The synthetic Paseo 0.12.0 tree rule 1 reads: a stand-in for the shapes of the
 * installed `@getpaseo` packages, written for these tests, not copied source.
 */
const FIXTURE = fileURLToPath(new URL("./fixtures/paseo-0-12", import.meta.url));
/** The plugin's own process path: `argv1` inside the fixture installation. */
const FIXTURE_ARGV1 = join(
  FIXTURE,
  "@getpaseo",
  "server",
  "dist",
  "server",
  "server",
  "plugins",
  "plugin-process.js",
);
/** The installation this machine runs, when it has one. */
const INSTALLED = join(homedir(), ".local/share/agents-remember/paseo/prefix/node_modules/@getpaseo");
const READ_AT = new Date("2026-10-28T10:00:00.000Z");

/** One `PASEO_HOME` for one case, with the host configuration rule 1 reads. */
function withHome(providers: unknown, run: (home: string) => void): void {
  const home = mkdtempSync(join(tmpdir(), "ar-acp-providers-"));
  try {
    writeFileSync(join(home, "config.json"), JSON.stringify({ agents: { providers } }), "utf8");
    run(home);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
}

/** A provider set over one home, with a fixed clock and the unread rows kept. */
function acpProviders(home: string, argv1: string | null = FIXTURE_ARGV1, unread: string[] = []): AcpProviderSet {
  return createAcpProviderSet(home, {
    argv1,
    now: () => READ_AT,
    env: {},
    onUnread: (providerId, reason) => unread.push(`${providerId}:${reason}`),
  });
}

/** The file the launch reads, as the plugin last published it. */
function published(home: string): AcpProvidersDocument {
  return JSON.parse(readFileSync(join(home, "agents-remember", ACP_PROVIDERS_FILE), "utf8")) as AcpProvidersDocument;
}

test("a configured provider that extends acp is ACP, and so is a built-in built on ACPAgentClient", () => {
  // MIK-R216 rule 1: `hermes` is the host's own entry and extends `acp`; `copilot`
  // is the one built-in whose class is built on ACPAgentClient.
  withHome({ hermes: { extends: "acp", label: "Hermes", command: ["hermes", "acp"] } }, home => {
    const acp = acpProviders(home);

    assert.deepEqual(acp.document.providers["hermes"], {
      class: "GenericACPAgentClient",
      acp: true,
      reason: "extends-acp",
    });
    assert.deepEqual(acp.document.providers["copilot"], {
      class: "CopilotACPAgentClient",
      acp: true,
      reason: "built-in",
    });
    assert.equal(acp.isAcp("hermes"), true);
    assert.equal(acp.isAcp("copilot"), true);
  });
});

test("an entry that extends a built-in takes that built-in's verdict", () => {
  // MIK-R216 rule 1: `extends` is one step to a built-in id, so the base's class
  // decides, and a Claude base is not on an ACP connection.
  withHome({ review: { extends: "copilot" }, cheap: { extends: "claude" } }, home => {
    const acp = acpProviders(home);

    assert.deepEqual(acp.document.providers["review"], {
      class: "CopilotACPAgentClient",
      acp: true,
      reason: "extends-builtin:copilot",
    });
    assert.deepEqual(acp.document.providers["cheap"], {
      class: "ClaudeAgentClient",
      acp: false,
      reason: "class-not-acp",
    });
  });
});

test("only copilot among the built-ins is ACP, and a cursor entry that extends acp is too", () => {
  // MIK-R216 rule 1: the five helper-reporting built-ins keep today's behaviour;
  // `cursor` is the id `addDerivedProviders` builds its own ACP client for.
  withHome({ cursor: { extends: "acp", label: "Cursor", command: ["cursor-agent", "acp"] } }, home => {
    const acp = acpProviders(home);

    for (const id of ["claude", "codex", "opencode", "pi", "omp"]) {
      assert.equal(acp.isAcp(id), false, id);
    }
    assert.equal(acp.document.providers["claude"]?.reason, "class-not-acp");
    assert.equal(acp.isAcp("copilot"), true);
    assert.deepEqual(acp.document.providers["cursor"], {
      class: "CursorACPAgentClient",
      acp: true,
      reason: "extends-acp",
    });
    // The manifest's `modes` members carry ids of their own, and its dev-only
    // definitions are not built-in: none of them is a provider of the file.
    assert.equal(acp.document.providers["auto"], undefined);
    assert.equal(acp.document.providers["mock"], undefined);
  });
});

test("a provider that is neither built-in nor configured is not ACP", () => {
  // MIK-R216 rule 1: a provider a Paseo plugin registers is in neither list. The
  // file names only what it can name — Paseo's built-ins and the host's configured
  // providers — and the seat's question is answered from that.
  withHome({}, home => {
    const unread: string[] = [];
    const acp = acpProviders(home, FIXTURE_ARGV1, unread);

    assert.equal(acp.document.providers["hub-e2e"], undefined);
    assert.equal(acp.isAcp("hub-e2e"), false);
    assert.deepEqual(unread, []);
  });
});

test("an entry whose extends names nothing known is unread, and is logged once", () => {
  // MIK-R216 rule 1 and rule 7: an entry the plugin cannot place is not ACP, and
  // `acp-provider-unread` is written once per provider in a plugin process.
  withHome({ mystery: { extends: "gemini" }, broken: "not-an-object" }, home => {
    const unread: string[] = [];
    const acp = acpProviders(home, FIXTURE_ARGV1, unread);

    assert.deepEqual(acp.document.providers["mystery"], {
      class: null,
      acp: false,
      reason: "extends-unknown:gemini",
    });
    assert.deepEqual(acp.document.providers["broken"], {
      class: null,
      acp: false,
      reason: "entry-unreadable",
    });
    assert.deepEqual(unread, ["mystery:extends-unknown:gemini", "broken:entry-unreadable"]);

    acp.refresh();
    assert.deepEqual(unread, ["mystery:extends-unknown:gemini", "broken:entry-unreadable"]);
  });
});

test("the written file holds the schema, the Paseo version, the files read and every verdict", () => {
  // MIK-R216 rule 1: the publication names the version, each file the read touched,
  // and each provider with its class and its verdict.
  withHome({ hermes: { extends: "acp", label: "Hermes", command: ["hermes", "acp"] } }, home => {
    const acp = acpProviders(home);
    const document = published(home);

    assert.deepEqual(document, JSON.parse(JSON.stringify(acp.document)));
    assert.equal(document.schema, ACP_PROVIDERS_SCHEMA);
    assert.equal(document.paseoVersion, "0.12.0");
    assert.equal(document.readAt, READ_AT.toISOString());
    const agent = join(FIXTURE, "@getpaseo", "server", "dist", "server", "server", "agent");
    for (const file of [
      join(FIXTURE, "@getpaseo", "server", "package.json"),
      join(agent, "provider-registry.js"),
      join(FIXTURE, "@getpaseo", "protocol", "dist", "provider-manifest.js"),
      join(agent, "providers", "claude", "agent.js"),
      join(agent, "providers", "generic-acp-agent.js"),
    ]) {
      assert.ok(document.files.includes(file), file);
    }
    assert.equal(new Set(document.files).size, document.files.length);
    for (const [id, verdict] of Object.entries(document.providers)) {
      assert.equal(typeof verdict.reason, "string", id);
      assert.equal(typeof verdict.acp, "boolean", id);
      assert.ok(verdict.class === null || typeof verdict.class === "string", id);
    }
    assert.equal(document.providers["hermes"]?.class, "GenericACPAgentClient");
    assert.equal(document.providers["hermes"]?.acp, true);
    assert.deepEqual(document.dropouts, {});
    // The write is staged and renamed: the launch finds one file and no staging one.
    assert.equal(existsSync(join(home, "agents-remember", `${ACP_PROVIDERS_FILE}.tmp`)), false);
  });
});

test("a provider that reported helpers drops out with its time and stays out after a restart", () => {
  // MIK-R216 rule 1 and D615: the verdict already leaves a dropped-out provider out,
  // and the earlier drop-out survives the plugin's next start.
  withHome({ hermes: { extends: "acp", label: "Hermes", command: ["hermes", "acp"] } }, home => {
    const acp = acpProviders(home);
    assert.equal(acp.isAcp("hermes"), true);

    acp.noteNativeProvider("hermes");
    assert.deepEqual(acp.document.providers["hermes"], {
      class: "GenericACPAgentClient",
      acp: false,
      reason: "reports-helpers",
    });
    assert.equal(acp.isAcp("hermes"), false);
    assert.deepEqual(acp.document.dropouts, { hermes: READ_AT.toISOString() });
    assert.deepEqual(published(home).dropouts, { hermes: READ_AT.toISOString() });

    // The first report is the one that is kept: a second one writes nothing.
    acp.noteNativeProvider("hermes");
    assert.deepEqual(published(home).dropouts, { hermes: READ_AT.toISOString() });

    const restarted = acpProviders(home);
    assert.deepEqual(restarted.document.dropouts, { hermes: READ_AT.toISOString() });
    assert.equal(restarted.isAcp("hermes"), false);
    assert.equal(restarted.document.providers["hermes"]?.reason, "reports-helpers");
    assert.equal(restarted.isAcp("copilot"), true);
  });
});

test("a Paseo installation that cannot be read still writes the file, with every provider unread", () => {
  // MIK-R216 rule 1's failure behaviour: no source, no ACP seat for any provider,
  // and the file still says why, so the launch adds no instruction.
  const absent = join(FIXTURE, "absent", "@getpaseo", "server", "dist", "server", "server", "plugins", "plugin-process.js");
  withHome({ hermes: { extends: "acp" }, cursor: { extends: "acp" } }, home => {
    const unread: string[] = [];
    const acp = acpProviders(home, absent, unread);
    const document = published(home);

    assert.equal(document.schema, ACP_PROVIDERS_SCHEMA);
    assert.equal(document.paseoVersion, null);
    assert.deepEqual(document.files, []);
    assert.deepEqual(document.providers["hermes"], { class: null, acp: false, reason: "paseo-unreadable" });
    assert.deepEqual(document.providers["cursor"], { class: null, acp: false, reason: "paseo-unreadable" });
    assert.equal(acp.isAcp("cursor"), false);
    assert.deepEqual(unread, ["hermes:paseo-unreadable", "cursor:paseo-unreadable"]);
  });
});

test(
  "against the installed Paseo, hermes, copilot and cursor are ACP and the helper-reporting providers are not",
  { skip: existsSync(INSTALLED) ? false : `no installed Paseo at ${INSTALLED}` },
  () => {
    // MIK-R216 rule 1 read against this machine's own installation, with the host's
    // `hermes` entry and a `cursor` entry that extends `acp`. Absent Paseo is a skip,
    // never a failure: the packet does not require the installation.
    const argv1 = join(INSTALLED, "server", "dist", "server", "server", "plugins", "plugin-process.js");
    withHome(
      {
        hermes: { extends: "acp", label: "Hermes", command: ["hermes", "acp"] },
        cursor: { extends: "acp", label: "Cursor", command: ["cursor-agent", "acp"] },
      },
      home => {
        const acp = acpProviders(home, argv1);

        assert.equal(acp.document.paseoVersion, "0.12.0");
        for (const id of ["hermes", "copilot", "cursor"]) assert.equal(acp.isAcp(id), true, id);
        for (const id of ["claude", "codex", "opencode", "pi", "omp"]) assert.equal(acp.isAcp(id), false, id);
        assert.deepEqual(published(home).providers["hermes"], {
          class: "GenericACPAgentClient",
          acp: true,
          reason: "extends-acp",
        });
      },
    );
  },
);
