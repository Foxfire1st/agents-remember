import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  HostActivityPublisher,
  PERMISSION_PROMPTS_LIMIT,
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

type PendingPrompt = ActivityAgentSnapshot["pendingPermissions"][number];

function prompt(id: string): PendingPrompt {
  return { id, provider: "claude", name: "Bash", kind: "tool" } as PendingPrompt;
}

/** A prompt the host shows as pending without naming: the publisher must never name it. */
function anonymousPrompt(): PendingPrompt {
  return { provider: "claude", name: "Bash", kind: "tool" } as unknown as PendingPrompt;
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
  assert.deepEqual(h.latest().agents.known, { state: "permission", eventAt: startedAt, permissionPrompts: ["permission"] });
  h.publisher.note("agent.permission_resolved", "known", "2026-10-15T00:02:00Z", "permission");
  assert.deepEqual(h.latest().agents.known, { state: "in-turn", eventAt: startedAt });
  const restarted = new HostActivityPublisher(() => h.records, value => h.writes.push(value));
  await restarted.reconcile(reader([agent({ activeTurn: { turnId: "turn", startedAt }, pendingPermissions: [{ id: "permission", provider: "claude", name: "permission", kind: "tool" }] })]));
  assert.deepEqual(h.latest().agents.known, { state: "permission", eventAt: startedAt, permissionPrompts: ["permission"] });
  restarted.note("agent.permission_resolved", "known", TIME, "permission");
  assert.deepEqual(h.latest().agents.known, { state: "in-turn", eventAt: startedAt });
});

test("two requested prompts publish two ids sorted, and the agent is in permission", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  const startedAt = "2026-10-15T00:00:00Z";
  h.publisher.note("agent.turn_started", "known", startedAt);
  h.publisher.note("agent.turn_ended", "known");
  // Requested out of order: the publication is sorted, so event order cannot leak.
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  assert.deepEqual(h.latest().agents.known, {
    state: "permission",
    eventAt: startedAt,
    permissionPrompts: ["prompt-a", "prompt-b"],
  });
});

test("resolving one prompt leaves exactly the other id: a second prompt is a second occurrence", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.turn_started", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
  assert.deepEqual(h.latest().agents.known, { state: "permission", eventAt: TIME, permissionPrompts: ["prompt-b"] });
  h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-b");
  assert.equal(h.latest().agents.known.state, "in-turn");
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
});

test("two requests for the same id stay one occurrence", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.turn_started", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-a"]);
  h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
  assert.equal(h.latest().agents.known.state, "in-turn");
});

test("archiving the agent drops the ids", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.turn_started", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-a", "prompt-b"]);
  h.publisher.note("agent.archived", "known", TIME);
  assert.equal(h.latest().agents.known.state, "ended");
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
});

test("an agent with no pending prompts publishes no permissionPrompts key at all", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  assert.deepEqual(h.latest().agents.known, { state: "idle" });
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  h.publisher.note("agent.turn_started", "known");
  h.publisher.note("agent.turn_ended", "known");
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  // A reconciled host snapshot with an empty pending list behaves the same.
  await h.publisher.reconcile(reader([agent({ status: "running", activeTurn: { turnId: "turn", startedAt: TIME } })]));
  assert.deepEqual(h.latest().agents.known, { state: "in-turn", eventAt: TIME });
});

test("a capped prompt list is incomplete until a native answer restores the whole set", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.turn_started", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "z");
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["z"]);
  assert.equal(h.latest().complete, true);
  for (let index = PERMISSION_PROMPTS_LIMIT; index > 0; index--) {
    h.publisher.note("agent.permission_requested", "known", TIME, `a-${String(index).padStart(2, "0")}`);
  }
  const published = h.latest().agents.known;
  assert.equal(published.state, "permission");
  assert.equal(published.permissionPrompts?.length, PERMISSION_PROMPTS_LIMIT);
  assert.deepEqual(published.permissionPrompts, Array.from({ length: PERMISSION_PROMPTS_LIMIT }, (_, index) => `a-${String(index + 1).padStart(2, "0")}`));
  assert.equal(h.latest().complete, false);

  h.publisher.note("agent.permission_resolved", "known", TIME, "a-01");
  assert.equal(h.latest().complete, true);
  assert.equal(h.latest().agents.known.permissionPrompts?.length, PERMISSION_PROMPTS_LIMIT);
  assert.equal(h.latest().agents.known.permissionPrompts?.includes("z"), true);
  await h.publisher.reconcile(reader([agent({ pendingPermissions: Array.from(
    { length: PERMISSION_PROMPTS_LIMIT - 1 }, (_, index) => prompt(`a-${String(index + 2).padStart(2, "0")}`),
  ) })]));
  assert.equal(h.latest().complete, true);
  assert.equal(h.latest().agents.known.permissionPrompts?.includes("z"), false);
});

test("a resolution during the first listing preserves the outstanding sibling prompt", async () => {
  const h = harness();
  // evidence/permission-listing-race.json: prompt-b is outstanding in the native
  // listing while prompt-a is answered before that listing returns.
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
    return {
      entries: [{ agent: agent({ status: "running", pendingPermissions: [prompt("prompt-b")] }) }],
      pageInfo: { hasMore: false, nextCursor: null },
    };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-b"] });
  assert.equal(h.latest().complete, true);
});

test("a request during the first listing survives a snapshot read before it", async () => {
  const h = harness();
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
    // The page was read before prompt-b existed, so only prompt-a is in it.
    return { entries: [{ agent: agent({ pendingPermissions: [prompt("prompt-a")] }) }], pageInfo: { hasMore: false, nextCursor: null } };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-a", "prompt-b"] });
  // The page contradicted an event that outranked it, so it proves no identity set:
  // the ids stay, and only a listing started after the request can settle them.
  assert.equal(h.latest().complete, false);
  await h.publisher.reconcile(reader([agent({ pendingPermissions: [prompt("prompt-a"), prompt("prompt-b")] })]));
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-a", "prompt-b"] });
  assert.equal(h.latest().complete, true);
});

test("two siblings, one answered during a listing: exactly the other id remains", async () => {
  const h = harness();
  const startedAt = "2026-10-15T00:00:00Z";
  h.publisher.note("agent.turn_started", "known", startedAt);
  h.publisher.note("agent.turn_ended", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
    return {
      entries: [{ agent: agent({ status: "running", pendingPermissions: [prompt("prompt-a"), prompt("prompt-b")] }) }],
      pageInfo: { hasMore: false, nextCursor: null },
    };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "permission", eventAt: startedAt, permissionPrompts: ["prompt-b"] });
  assert.equal(Object.hasOwn(h.latest().agents.known, "unknown"), false);
});

test("a listing that cannot name a prompt publishes no invented id and no completeness", async () => {
  const h = harness();
  // evidence/native-counterexamples-v2.json case permission-state-without-native-id.
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [anonymousPrompt()] })]));
  const unnamed = h.latest().agents.known;
  assert.equal(unnamed.state, "permission");
  assert.equal(Object.hasOwn(unnamed, "permissionPrompts"), false);
  assert.equal(h.latest().complete, false);
  // Nothing null, no "unknown" and no timestamp was minted as an identity.
  assert.doesNotMatch(JSON.stringify(h.latest()), /null|unknown/);

  // An exact id is published exactly, and retained when another observation cannot name all.
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-z")] })]));
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-z"]);
  assert.equal(h.latest().complete, true);
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-z"), anonymousPrompt()] })]));
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-z"]);
  assert.equal(h.latest().complete, false);

  // The next exact observation of the same agent publishes the truth again, whole.
  await h.publisher.reconcile(reader([agent()]));
  assert.deepEqual(h.latest().agents.known, { state: "idle" });
  assert.equal(h.latest().complete, true);
});

test("a permission request event without a native id keeps the wait and denies completeness", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.permission_requested", "known");
  assert.equal(h.latest().agents.known.state, "permission");
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  assert.equal(h.latest().complete, false);
  h.publisher.note("agent.permission_resolved", "known");
  assert.equal(h.latest().agents.known.state, "idle");
  assert.equal(h.latest().complete, true);
});

test("complete removal of every prompt ends the cause and restores completeness", async () => {
  const h = harness();
  const startedAt = "2026-10-15T00:00:00Z";
  await h.publisher.reconcile(reader([agent()]));
  h.publisher.note("agent.turn_started", "known", startedAt);
  h.publisher.note("agent.turn_ended", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-a", "prompt-b"]);
  // The host answers both without an event: one exact listing clears the cause.
  await h.publisher.reconcile(reader([agent()]));
  assert.equal(h.latest().agents.known.state, "idle");
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  assert.equal(h.latest().complete, true);
});

test("a reload reconciles by identity and never resurrects a resolved id", async () => {
  const h = harness();
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-a"), prompt("prompt-b")] })]));
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-a", "prompt-b"]);
  h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-b"]);
  // A reload whose snapshot still carries the answered sibling cannot bring it back.
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-a"), prompt("prompt-b")] })]));
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-b"] });
  assert.equal(h.latest().complete, true);
  // A second reload sees the host's truth and leaves exactly the same occurrence.
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-b")] })]));
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-b"]);
});

test("a request the host did not name during the first listing is never published as idle", async () => {
  const h = harness();
  // The measured race: the page was read before the request arrived, and the
  // request carried no native id for the listing to carry back.
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_requested", "known");
    return { entries: [{ agent: agent() }], pageInfo: { hasMore: false, nextCursor: null } };
  } } });
  const waited = h.latest().agents.known;
  assert.equal(waited.state, "permission", "an outstanding request must not publish as idle");
  assert.equal(Object.hasOwn(waited, "permissionPrompts"), false);
  assert.equal(h.latest().complete, false);
  // No identity was invented to carry the wait.
  assert.doesNotMatch(JSON.stringify(h.latest()), /null|unknown/);
  // A listing that started after the request is authoritative: the wait is over.
  await h.publisher.reconcile(reader([agent()]));
  assert.deepEqual(h.latest().agents.known, { state: "idle" });
  assert.equal(h.latest().complete, true);
});

test("an unnamed request answered inside the same listing is not a permanent wait", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_requested", "known");
    h.publisher.note("agent.permission_resolved", "known");
    return { entries: [{ agent: agent() }], pageInfo: { hasMore: false, nextCursor: null } };
  } } });
  // The ask and its answer cancel inside the window: the host's page is whole, and
  // the idle transition keeps the event time it was published with.
  assert.deepEqual(h.latest().agents.known, { state: "idle", eventAt: TIME });
  assert.equal(h.latest().complete, true);
});

test("a reload keeps a prompt requested between its page read and the response", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent()]));
  assert.equal(h.latest().complete, true);
  await h.publisher.reconcile({ agents: { async list() {
    // The reload's page was read before prompt-late existed.
    h.publisher.note("agent.permission_requested", "known", TIME, "prompt-late");
    return { entries: [{ agent: agent() }], pageInfo: { hasMore: false, nextCursor: null } };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-late"] });
  assert.equal(h.latest().complete, false);
  // The next reload started after the request and settles it whole.
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-late")] })]));
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-late"] });
  assert.equal(h.latest().complete, true);
});

test("a page that carries the id requested while it was open leaves the observation whole", async () => {
  const h = harness();
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
    // Same race, but the host's page does carry the id it named.
    return {
      entries: [{ agent: agent({ status: "running", pendingPermissions: [prompt("prompt-a")] }) }],
      pageInfo: { hasMore: false, nextCursor: null },
    };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-a"] });
  assert.equal(h.latest().complete, true);
});

test("a resolution ordered after a listing still clears the id that listing carried", async () => {
  const h = harness();
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-a")] })]));
  assert.deepEqual(h.latest().agents.known, { state: "permission", permissionPrompts: ["prompt-a"] });
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
    return {
      entries: [{ agent: agent({ status: "running", pendingPermissions: [prompt("prompt-a")] }) }],
      pageInfo: { hasMore: false, nextCursor: null },
    };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "idle", eventAt: TIME });
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  assert.equal(h.latest().complete, true);
});

test("two siblings during one listing: the answered one goes and the new one stays", async () => {
  const h = harness();
  const startedAt = "2026-10-15T00:00:00Z";
  h.publisher.note("agent.turn_started", "known", startedAt);
  h.publisher.note("agent.turn_ended", "known");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
    h.publisher.note("agent.permission_requested", "known", TIME, "prompt-c");
    return {
      entries: [{ agent: agent({ status: "running", pendingPermissions: [prompt("prompt-a"), prompt("prompt-b")] }) }],
      pageInfo: { hasMore: false, nextCursor: null },
    };
  } } });
  // prompt-a is settled by an event after the page, prompt-b comes from the page,
  // and prompt-c is only known to the event that this page never saw.
  assert.deepEqual(h.latest().agents.known, {
    state: "permission",
    eventAt: startedAt,
    permissionPrompts: ["prompt-b", "prompt-c"],
  });
  assert.equal(h.latest().complete, false);
  await h.publisher.reconcile(reader([agent({ pendingPermissions: [prompt("prompt-b"), prompt("prompt-c")] })]));
  assert.deepEqual(h.latest().agents.known, {
    state: "permission",
    eventAt: startedAt,
    permissionPrompts: ["prompt-b", "prompt-c"],
  });
  assert.equal(h.latest().complete, true);
});

test("a non-racing empty listing after a resolution is the legitimate clearing control", async () => {
  const h = harness();
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  await h.publisher.reconcile(reader([agent({ status: "running", pendingPermissions: [prompt("prompt-a")] })]));
  assert.deepEqual(h.latest().agents.known.permissionPrompts, ["prompt-a"]);
  h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
  assert.equal(h.latest().agents.known.state, "idle");
  // Nothing raced this page: the host's empty answer ends the occurrence, and the
  // idle transition keeps the event time it was already published with.
  await h.publisher.reconcile(reader([agent()]));
  assert.deepEqual(h.latest().agents.known, { state: "idle", eventAt: TIME });
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  assert.equal(h.latest().complete, true);
});

test("complete removal during a listing ends the cause and restores completeness", async () => {
  const h = harness();
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-a");
  h.publisher.note("agent.permission_requested", "known", TIME, "prompt-b");
  await h.publisher.reconcile({ agents: { async list() {
    h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-a");
    h.publisher.note("agent.permission_resolved", "known", TIME, "prompt-b");
    return {
      entries: [{ agent: agent({ status: "running", pendingPermissions: [prompt("prompt-a"), prompt("prompt-b")] }) }],
      pageInfo: { hasMore: false, nextCursor: null },
    };
  } } });
  assert.deepEqual(h.latest().agents.known, { state: "idle", eventAt: TIME });
  assert.equal(Object.hasOwn(h.latest().agents.known, "permissionPrompts"), false);
  assert.equal(h.latest().complete, true);
});
