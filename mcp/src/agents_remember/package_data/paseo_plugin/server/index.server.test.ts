/**
 * MIK-R190 rules 1 and 3: the plugin's contribution takes its one subagent
 * subscription from the first host API it is handed (L184-R1-F11).
 *
 * Run: `node --experimental-strip-types --test server/*.test.ts` from the plugin dir.
 * The cases drive the real `contribute` with a fake host context: a lifecycle hook
 * first, the snapshot RPC first, both, a readiness that fails and a readiness that
 * is deferred. Every read of `host-activity.json` is the file the publisher wrote.
 */
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { register } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { PluginServerContext } from "@getpaseo/plugin/server";
import { test } from "node:test";
import assert from "node:assert";

// The host bundles the plugin's entry, so `index.server.ts` imports its own modules
// without a `.ts` extension. Node's loader needs the extension, so this one hook
// resolves the same files: the case below drives the real `contribute`, not a copy.
const extensionHook = `
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context);
  } catch (error) {
    if (specifier.startsWith(".") && !/\\.[mc]?[jt]sx?$/.test(specifier)) {
      return next(specifier + ".ts", context);
    }
    throw error;
  }
}
`;
register("data:text/javascript," + encodeURIComponent(extensionHook));
const { default: contribute } = await import("../index.server.ts");

const WORKER = "11111111-1111-5111-8111-111111111111";

interface Observer {
  snapshot: () => void;
  update: (message: unknown) => void;
}

/** One owned subscription the fake host handed out. */
interface FakeHandle {
  readonly observers: Observer[];
  released: number;
}

/** The host API the plugin is handed, with every subscription it makes kept. */
function fakePaseo(
  options: { readonly ready?: "now" | "fails" | "deferred"; readonly roster?: boolean } = {},
) {
  const handles: FakeHandle[] = [];
  const state = {
    handles,
    readyMode: options.ready ?? "now",
    settleReady: (() => {}) as (value: unknown) => void,
    settleFailure: (() => {}) as (error: unknown) => void,
  };
  const observeEvents = () => {
    // One promise, settled the way this case says.
    let ready: Promise<unknown>;
    if (state.readyMode === "fails") {
      ready = Promise.reject(new Error("the host refused the subscription"));
    } else if (state.readyMode === "deferred") {
      ready = new Promise<unknown>((resolve, reject) => {
        state.settleReady = resolve;
        state.settleFailure = reject;
      });
    } else {
      ready = Promise.resolve({ subscriptionId: `sub-${handles.length + 1}` });
    }
    const handle = {
      subscriptionId: `sub-${handles.length + 1}`,
      ready,
      observers: [] as Observer[],
      released: 0,
      subscribe(observer: Observer) {
        handle.observers.push(observer);
        return () => {};
      },
      async release() {
        handle.released += 1;
      },
    };
    handles.push(handle);
    return handle;
  };
  return {
    paseo: {
      observeEvents,
      agents: {
        list: async () =>
          options.roster === true
            ? {
                entries: [
                  {
                    agent: {
                      id: WORKER,
                      status: "idle",
                      archivedAt: null,
                      activeTurn: null,
                      pendingPermissions: [],
                    },
                  },
                ],
                pageInfo: { hasMore: false, nextCursor: null },
              }
            : { entries: [], pageInfo: { hasMore: false, nextCursor: null } },
        ref: () => ({ send: async () => {} }),
      },
    },
    state,
    observes: (): number => handles.length,
    releases: (): number => handles.reduce((total, handle) => total + handle.released, 0),
  };
}

type FakePaseo = ReturnType<typeof fakePaseo>;

/** The contribution's host: the hooks it registers and the RPCs it answers. */
function fakeHost() {
  type Hook = (event: unknown, context: unknown) => unknown;
  type Rpc = (input: unknown, context: unknown) => unknown;
  const hooks = new Map<string, Hook[]>();
  const rpcs = new Map<string, Rpc>();
  const server = {
    on(name: string, handler: Hook) {
      const kept = hooks.get(name) ?? [];
      kept.push(handler);
      hooks.set(name, kept);
      return () => {};
    },
    handle(contract: { name: string }, handler: Rpc) {
      rpcs.set(contract.name, handler);
    },
    registerSettings() {
      throw new Error("this test registers no settings");
    },
    registerProvider() {
      throw new Error("this test registers no provider");
    },
    registerUsageSource() {
      throw new Error("this test registers no usage source");
    },
  } as unknown as PluginServerContext;
  return {
    server,
    hookNames: (): string[] => [...hooks.keys()],
    /** Every handler of one lifecycle event, as the host would run it. */
    async fire(kind: string, paseo: FakePaseo["paseo"]): Promise<void> {
      const handlers = hooks.get(kind);
      assert.ok(handlers !== undefined && handlers.length > 0, `no handler for ${kind}`);
      const event =
        kind === "agent.turn_started"
          ? {
              agent: { id: WORKER, workspaceId: null, parentAgentId: null, provider: "hermes", cwd: "/tmp", title: null },
              turnId: null,
            }
          : { agent: { id: WORKER, workspaceId: null, parentAgentId: null, provider: "hermes", cwd: "/tmp", title: null } };
      for (const handler of handlers) {
        await handler(event, { paseo, signal: new AbortController().signal });
      }
    },
    async rpc(name: string, input: unknown, paseo: FakePaseo["paseo"]): Promise<unknown> {
      const handler = rpcs.get(name);
      assert.ok(handler !== undefined, `no RPC named ${name}`);
      return handler(input, { paseo });
    },
  };
}

/** One `PASEO_HOME` around one case, with the role record the plugin reads. */
async function withHome(run: (home: string) => Promise<void>): Promise<void> {
  const home = mkdtempSync(join(tmpdir(), "ar-plugin-contribute-"));
  const previous = process.env.PASEO_HOME;
  mkdirSync(join(home, "agents-remember"), { recursive: true });
  writeFileSync(
    join(home, "agents-remember", "role-agents.json"),
    JSON.stringify({
      agents: { [WORKER]: { role: "worker", task: "repo/master/leaf.json", reportPath: "/tmp/report.md" } },
    }),
  );
  process.env.PASEO_HOME = home;
  try {
    await run(home);
  } finally {
    if (previous === undefined) delete process.env.PASEO_HOME;
    else process.env.PASEO_HOME = previous;
    rmSync(home, { recursive: true, force: true });
  }
}

/** The publication the plugin last wrote. */
function publication(home: string): { complete?: boolean; bootId?: string } {
  return JSON.parse(readFileSync(join(home, "agents-remember", "host-activity.json"), "utf8")) as {
    complete?: boolean;
    bootId?: string;
  };
}

/** The subagent fields the publication carries for one agent. */
function publishedAgents(
  home: string,
): Record<string, { runningSubagents?: number; subagentsSince?: string }> {
  const written = JSON.parse(readFileSync(join(home, "agents-remember", "host-activity.json"), "utf8")) as {
    agents?: Record<string, { runningSubagents?: number; subagentsSince?: string }>;
  };
  return written.agents ?? {};
}

/** One agent's list, as the bridge hands it to `ar.host-activity-subagents`. */
function subagentsList(running: boolean) {
  return {
    agentId: WORKER,
    subagents: running
      ? [{ id: "s1", createdAt: "2026-10-21T11:58:00.000Z", status: "running" }]
      : [],
  };
}

async function tick(): Promise<void> {
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test("L184-R1-F11: the one subscription is taken at the first usable hook or RPC, and released once", async (t) => {
  await t.test("hook-first, with no snapshot RPC at all", async () => {
    await withHome(async () => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const paseo = fakePaseo();
      try {
        assert.equal(paseo.observes(), 0, "contribution itself holds no host API");
        await host.fire("agent.turn_started", paseo.paseo);
        assert.equal(paseo.observes(), 1, "the first usable hook takes the one subscription");
        assert.equal(paseo.state.handles[0].observers.length, 1, "with one observer");
        await host.fire("agent.turn_started", paseo.paseo);
        assert.equal(paseo.observes(), 1, "a later hook attaches nothing again");
        assert.equal(paseo.state.handles[0].observers.length, 1, "and subscribes no second observer");
      } finally {
        cleanup();
      }
      assert.equal(paseo.releases(), 1, "the cleanup releases the one subscription");
    });
  });

  await t.test("hook-first on every lifecycle hook, not only a turn hook", async () => {
    // L184-R1-F11: a boot with no dashboard never calls the snapshot RPC, so the
    // first usable hook must be any of them, not only a turn's.
    for (const kind of [
      "agent.created",
      "agent.permission_requested",
      "agent.permission_resolved",
      "agent.archived",
      "agent.closed",
    ]) {
      await withHome(async () => {
        const host = fakeHost();
        const cleanup = contribute(host.server);
        const paseo = fakePaseo();
        try {
          await host.fire(kind, paseo.paseo);
          assert.equal(paseo.observes(), 1, `${kind} takes the one subscription`);
          assert.equal(paseo.state.handles[0].observers.length, 1, `${kind}: with one observer`);
          await host.fire("agent.created", paseo.paseo);
          assert.equal(paseo.observes(), 1, `${kind}: a later hook attaches nothing again`);
          assert.equal(paseo.state.handles[0].observers.length, 1, `${kind}: and subscribes no second observer`);
        } finally {
          cleanup();
        }
        assert.equal(paseo.releases(), 1, `${kind}: the cleanup releases the one subscription`);
      });
    }
  });

  await t.test("RPC-first, then a hook", async () => {
    await withHome(async (home) => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const paseo = fakePaseo();
      try {
        await host.rpc("ar.host-activity-snapshot", {}, paseo.paseo);
        assert.equal(paseo.observes(), 1, "the snapshot RPC takes the one subscription");
        assert.equal(paseo.state.handles[0].observers.length, 1);
        assert.equal(publication(home).complete, true, "the boot the RPC read is complete");
        await host.fire("agent.turn_started", paseo.paseo);
        assert.equal(paseo.observes(), 1, "the hook finds it already attached");
      } finally {
        cleanup();
      }
      assert.equal(paseo.releases(), 1, "the cleanup releases the one subscription");
    });
  });

  await t.test("a failed readiness is released, so the next hook recovers", async () => {
    await withHome(async () => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const failed = fakePaseo({ ready: "fails" });
      const good = fakePaseo();
      try {
        await host.fire("agent.turn_started", failed.paseo);
        await tick();
        assert.equal(failed.observes(), 1, "the hook tried the host's subscription");
        assert.equal(failed.releases(), 1, "and let the failed handle go");
        await host.fire("agent.turn_started", good.paseo);
        assert.equal(good.observes(), 1, "the next hook takes a fresh subscription");
        assert.equal(good.state.handles[0].observers.length, 1, "which is a working one");
      } finally {
        cleanup();
      }
    });
  });

  await t.test("a snapshot waits for readiness and publishes nothing unobserved", async () => {
    await withHome(async (home) => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const paseo = fakePaseo({ ready: "deferred" });
      try {
        const answered = host.rpc("ar.host-activity-snapshot", {}, paseo.paseo);
        await tick();
        assert.notEqual(
          publication(home).complete,
          true,
          "no complete publication is written before the subscription is ready",
        );
        paseo.state.settleReady({ subscriptionId: "sub-1" });
        await answered;
        assert.equal(publication(home).complete, true, "a ready subscription lets the boot be complete");
      } finally {
        cleanup();
      }
    });
  });

  await t.test("a snapshot whose subscription failed is published incomplete", async () => {
    await withHome(async (home) => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const paseo = fakePaseo({ ready: "fails" });
      try {
        await host.rpc("ar.host-activity-snapshot", {}, paseo.paseo);
        assert.equal(publication(home).complete, false, "an unobserved boot is not published as complete");
        assert.equal(paseo.releases(), 1, "and the failed handle is let go");
      } finally {
        cleanup();
      }
    });
  });
});

// ---------------------------------------------------------------------------
// L184-R1-F08: the reconciliation a list reply answers is carried by the wire,
// so an older reply can never revive what a newer one already consumed.
// ---------------------------------------------------------------------------

type SnapshotAnswer = { readonly bootId: string; readonly reconcile: number };

test("L184-R1-F08: a list reply answers only the reconciliation its snapshot began", async (t) => {
  await t.test("a reply naming its own earlier snapshot is refused after a newer one was consumed", async () => {
    await withHome(async (home) => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const paseo = fakePaseo({ roster: true });
      try {
        const first = (await host.rpc("ar.host-activity-snapshot", {}, paseo.paseo)) as SnapshotAnswer;
        assert.equal(typeof first.reconcile, "number", "the snapshot answers the reconciliation it began");
        assert.deepEqual(
          await host.rpc(
            "ar.host-activity-subagents",
            { reconcile: first.reconcile, lists: [subagentsList(true)] },
            paseo.paseo,
          ),
          { changed: true },
          "the first boot's own list applies under its number",
        );
        assert.equal(publishedAgents(home)[WORKER]?.runningSubagents, 1);

        const second = (await host.rpc("ar.host-activity-snapshot", {}, paseo.paseo)) as SnapshotAnswer;
        assert.equal(second.reconcile, first.reconcile + 1, "each snapshot begins its own");
        assert.deepEqual(
          await host.rpc(
            "ar.host-activity-subagents",
            { reconcile: second.reconcile, lists: [subagentsList(false)] },
            paseo.paseo,
          ),
          { changed: true },
          "the newer boot's complete list is the truth it answers",
        );
        assert.equal(publishedAgents(home)[WORKER]?.runningSubagents, undefined, "the child it omits is consumed");

        assert.deepEqual(
          await host.rpc(
            "ar.host-activity-subagents",
            { reconcile: first.reconcile, lists: [subagentsList(true)] },
            paseo.paseo,
          ),
          { changed: false },
          "the older reply is refused, not applied as a reconciliation of its own",
        );
        assert.equal(
          publishedAgents(home)[WORKER]?.runningSubagents,
          undefined,
          "and the consumed child is not revived",
        );

        // The same boot's bridge naming no number, after its own answer landed.
        assert.deepEqual(
          await host.rpc("ar.host-activity-subagents", { lists: [subagentsList(true)] }, paseo.paseo),
          { changed: false },
          "an unbound reply after the answer landed joins no reconciliation either",
        );
        assert.equal(publishedAgents(home)[WORKER]?.runningSubagents, undefined);
      } finally {
        cleanup();
      }
    });
  });

  await t.test("a reply naming no number is bound to the one unanswered reconciliation, and a late one to none", async () => {
    await withHome(async (home) => {
      const host = fakeHost();
      const cleanup = contribute(host.server);
      const paseo = fakePaseo({ roster: true });
      try {
        await host.rpc("ar.host-activity-snapshot", {}, paseo.paseo);
        assert.deepEqual(
          await host.rpc("ar.host-activity-subagents", { lists: [subagentsList(true)] }, paseo.paseo),
          { changed: true },
          "a bridge that names no number still answers the snapshot it just took",
        );
        assert.equal(publishedAgents(home)[WORKER]?.runningSubagents, 1);

        await host.rpc("ar.host-activity-snapshot", {}, paseo.paseo);
        assert.deepEqual(
          await host.rpc("ar.host-activity-subagents", { lists: [subagentsList(false)] }, paseo.paseo),
          { changed: true },
          "the next snapshot's list answers the reconciliation still unanswered",
        );
        assert.equal(publishedAgents(home)[WORKER]?.runningSubagents, undefined);

        // The gap-reply of the first snapshot, arriving after the second was consumed.
        assert.deepEqual(
          await host.rpc("ar.host-activity-subagents", { lists: [subagentsList(true)] }, paseo.paseo),
          { changed: false },
          "a reply that answers no reconciliation begins none",
        );
        assert.equal(
          publishedAgents(home)[WORKER]?.runningSubagents,
          undefined,
          "so the child the newer reply consumed stays consumed",
        );
      } finally {
        cleanup();
      }
    });
  });
});
