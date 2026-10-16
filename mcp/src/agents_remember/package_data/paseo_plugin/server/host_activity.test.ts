import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  HostActivityPublisher,
  createHostActivityPublisher,
  hostActivityPath,
  registerHostActivityEvents,
  type ActivityAgentSnapshot,
  type HostActivityPublication,
  type HostActivityReader,
} from "./host_activity.ts";
import type { TurnEndedFacts } from "./turn_ended.ts";

const FACTS: TurnEndedFacts = { role: "worker", task: "repo/master/leaf.json", reportPath: "/report.md" };
const TIME = "2026-10-15T00:01:00Z";

function harness() {
  const records: Record<string, TurnEndedFacts> = { known: FACTS };
  const writes: HostActivityPublication[] = [];
  const publisher = new HostActivityPublisher(() => records, value => writes.push(value), () => TIME, "boot-a");
  return { records, writes, publisher, latest: () => writes.at(-1)! };
}

function agent(fields: Partial<ActivityAgentSnapshot> = {}): ActivityAgentSnapshot {
  return { id: "known", status: "idle", pendingPermissions: [], ...fields };
}

function reader(agents: ActivityAgentSnapshot[]): HostActivityReader {
  return { agents: { async list() { return { entries: agents.map(agent => ({ agent })), pageInfo: { hasMore: false, nextCursor: null } }; } } };
}

test("a boot requires reconciliation and never invents an idle age", async () => {
  const h = harness();
  assert.deepEqual(h.latest(), { bootId: "boot-a", complete: false, agents: {}, ignoredEvents: 0 });
  await h.publisher.reconcile(reader([agent()]));
  assert.equal(h.latest().complete, true);
  assert.deepEqual(h.latest().agents, { known: { state: "idle" } });
  const restarted = new HostActivityPublisher(() => h.records, value => h.writes.push(value), () => TIME, "boot-b");
  assert.equal(h.latest().complete, false);
  await restarted.reconcile(reader([agent()]));
  assert.deepEqual(h.latest().agents.known, { state: "idle" });
  restarted.unload();
  assert.equal(h.latest().complete, false);
});

test("host transitions preserve permission waits through turn end and individual resolutions", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.turn_started", "known");
  assert.deepEqual(h.latest().agents.known, { state: "in-turn", eventAt: TIME });
  h.publisher.note("agent.permission_requested", "known", TIME, "p1");
  h.publisher.note("agent.permission_requested", "known", TIME, "p2");
  h.publisher.note("agent.turn_ended", "known");
  h.publisher.note("agent.permission_resolved", "known", TIME, "p1");
  assert.equal(h.latest().agents.known.state, "permission");
  h.publisher.note("agent.permission_resolved", "known", TIME, "p2");
  assert.equal(h.latest().agents.known.state, "idle");
  h.publisher.note("agent.turn_started", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "p3");
  h.publisher.note("agent.permission_resolved", "known", TIME, "p3");
  assert.equal(h.latest().agents.known.state, "in-turn");
  for (const event of ["agent.archived", "agent.closed"] as const) {
    h.publisher.note(event, "known");
    assert.equal(h.latest().agents.known.state, "ended");
    h.publisher.note("agent.created", "known");
    assert.equal(h.latest().agents.known.state, "idle");
  }
});

test("events during the full listing win; all pages are read and event time survives reconnect", async () => {
  const h = harness();
  let calls = 0;
  await h.publisher.reconcile({ agents: { async list(options) {
    calls++;
    assert.equal(options.filter.includeArchived, true);
    if (calls === 1) {
      assert.equal(options.page.cursor, undefined);
      h.publisher.note("agent.turn_started", "known");
      return { entries: [{ agent: agent() }], pageInfo: { hasMore: true, nextCursor: "next" } };
    }
    assert.equal(options.page.cursor, "next");
    return { entries: [{ agent: agent({ id: "unbound" }) }], pageInfo: { hasMore: false, nextCursor: null } };
  } } });
  assert.equal(calls, 2);
  assert.deepEqual(h.latest().agents, { known: { state: "in-turn", eventAt: TIME } });
  assert.equal(h.latest().ignoredEvents, 1);
  await h.publisher.reconcile(reader([agent({ activeTurn: { startedAt: "2026-10-15T00:00:00Z", turnId: "turn" } })]));
  assert.equal(h.latest().agents.known.eventAt, TIME);
});

test("failed reconciliation cannot make an old boot usable, and records bound retained rows", async () => {
  const h = harness();
  await assert.rejects(h.publisher.reconcile({ agents: { async list() { throw new Error("host unavailable"); } } }));
  assert.equal(h.latest().complete, false);
  h.publisher.note("agent.created", "unknown");
  assert.deepEqual(h.latest().agents, {});
  h.publisher.note("agent.created", "known");
  delete h.records.known;
  h.publisher.note("agent.created", "unknown");
  assert.deepEqual(h.latest().agents, {});
  assert.equal(h.latest().ignoredEvents, 2);
  await assert.rejects(h.publisher.reconcile({ agents: { async list() {
    return { entries: [], pageInfo: { hasMore: true, nextCursor: "repeated" } };
  } } }), /did not advance/);
});

test("all seven installed hook registrations publish with the SDK agent id", () => {
  const h = harness();
  const names: string[] = [];
  registerHostActivityEvents({ on(name) { names.push(name); return () => {}; } }, h.publisher);
  assert.deepEqual(names, ["agent.turn_started", "agent.turn_ended", "agent.permission_requested", "agent.permission_resolved", "agent.archived", "agent.created", "agent.closed"]);
});

test("the writer replaces one file and leaves no transition history", () => {
  const home = mkdtempSync(join(tmpdir(), "ar-host-activity-"));
  try {
    mkdirSync(join(home, "agents-remember"));
    writeFileSync(join(home, "agents-remember", "role-agents.json"), JSON.stringify({ agents: { known: FACTS } }));
    const publisher = createHostActivityPublisher(home);
    for (let index = 0; index < 100; index++) publisher.note(index % 2 ? "agent.turn_ended" : "agent.turn_started", "known");
    assert.equal(JSON.parse(readFileSync(hostActivityPath(home), "utf8")).agents.known.state, "idle");
    assert.deepEqual(readdirSync(join(home, "agents-remember")).sort(), ["host-activity.json", "role-agents.json"]);
  } finally { rmSync(home, { recursive: true }); }
});


test("a turn keeps its host start through permission requests and resolutions, including reconciliation", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  const startedAt = "2026-10-15T00:00:00Z";
  h.publisher.note("agent.turn_started", "known", startedAt);
  h.publisher.note("agent.permission_requested", "known", TIME, "permission");
  assert.deepEqual(h.latest().agents.known, { state: "permission", eventAt: startedAt });
  h.publisher.note("agent.permission_resolved", "known", "2026-10-15T00:02:00Z", "permission");
  assert.deepEqual(h.latest().agents.known, { state: "in-turn", eventAt: startedAt });
  const restarted = new HostActivityPublisher(() => h.records, value => h.writes.push(value));
  await restarted.reconcile(reader([agent({ activeTurn: { turnId: "turn", startedAt }, pendingPermissions: [{ id: "permission", provider: "claude", name: "permission", kind: "tool" }] })]));
  assert.deepEqual(h.latest().agents.known, { state: "permission", eventAt: startedAt });
  restarted.note("agent.permission_resolved", "known", TIME, "permission");
  assert.deepEqual(h.latest().agents.known, { state: "in-turn", eventAt: startedAt });
});
