import assert from "node:assert/strict";
import test from "node:test";
import { HostActivityPublisher, type HostActivityPublication } from "./host_activity.ts";
import {
  SUBAGENTS_PER_AGENT_LIMIT,
  SUBAGENTS_TOTAL_LIMIT,
  SubagentSubscription,
  SubagentTracker,
  type SubagentEventSource,
} from "./subagents.ts";
import type { TurnEndedFacts } from "./turn_ended.ts";

const WORKER = "11111111-1111-5111-8111-111111111111";
const OTHER = "22222222-2222-5222-8222-222222222222";
const RECORDS: Record<string, TurnEndedFacts> = {
  [WORKER]: { role: "worker", task: "repo/master/leaf.json", reportPath: "/tmp/report.md" },
};

function upsert(parentAgentId: string, id: string, status: string, createdAt: string) {
  return {
    type: "agent.provider_subagents.update",
    payload: { kind: "upsert", subagent: { id, parentAgentId, status, createdAt } },
  };
}

/** A publication and the tracker it renders, with every write kept. */
function harness() {
  const written: HostActivityPublication[] = [];
  const tracker = new SubagentTracker(() => RECORDS);
  const publisher = new HostActivityPublisher(
    () => RECORDS,
    publication => written.push(structuredClone(publication)),
    () => "2026-10-20T12:00:00.000Z",
    "boot-1",
    tracker,
  );
  // The publication holds one entry per agent of the role record, as a boot reconcile
  // leaves it; the subagent fields ride on that entry.
  publisher.note("agent.created", WORKER, "2026-10-20T11:00:00.000Z");
  written.length = 0;
  return { tracker, publisher, written };
}

test("a running upsert and a remove change the count and the oldest start", () => {
  const { tracker, publisher, written } = harness();

  assert.equal(tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z")), true);
  publisher.publishSubagents();
  const first = written.at(-1)?.agents[WORKER];
  assert.equal(first?.state, "idle");
  assert.equal(first?.runningSubagents, 1);
  assert.equal(first?.subagentsSince, "2026-10-20T11:58:00.000Z");

  tracker.noteUpdate(upsert(WORKER, "s2", "running", "2026-10-20T11:59:00.000Z"));
  publisher.publishSubagents();
  assert.equal(written.at(-1)?.agents[WORKER].runningSubagents, 2);
  assert.equal(written.at(-1)?.agents[WORKER].subagentsSince, "2026-10-20T11:58:00.000Z");

  tracker.noteUpdate({ type: "agent.provider_subagents.update", payload: { kind: "remove", parentAgentId: WORKER, subagentId: "s1" } });
  publisher.publishSubagents();
  assert.equal(written.at(-1)?.agents[WORKER].runningSubagents, 1);
  assert.equal(written.at(-1)?.agents[WORKER].subagentsSince, "2026-10-20T11:59:00.000Z");
});

test("a finished upsert drops the subagent it no longer reports running", () => {
  const { tracker, publisher, written } = harness();
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  publisher.publishSubagents();

  tracker.noteUpdate(upsert(WORKER, "s1", "completed", "2026-10-20T11:58:00.000Z"));
  publisher.publishSubagents();

  assert.equal(written.at(-1)?.agents[WORKER].runningSubagents, undefined);
  assert.equal(written.at(-1)?.agents[WORKER].subagentsSince, undefined);
});

test("a timeline row and an update for a parent outside the record neither count nor write", () => {
  const { tracker, publisher, written } = harness();
  const before = written.length;

  assert.equal(
    tracker.noteUpdate({
      type: "agent.provider_subagents.update",
      payload: { kind: "timeline", parentAgentId: WORKER, subagentId: "s1", seq: 1 },
    }),
    false,
  );
  assert.equal(tracker.noteUpdate(upsert(OTHER, "s9", "running", "2026-10-20T11:58:00.000Z")), false);
  publisher.publishSubagents();

  assert.equal(tracker.count(WORKER), 0);
  assert.equal(written.length, before);
});

test("the per-agent bound holds, and an unchanged count writes nothing", () => {
  const { tracker, publisher, written } = harness();
  for (let index = 0; index < SUBAGENTS_PER_AGENT_LIMIT + 5; index += 1) {
    tracker.noteUpdate(upsert(WORKER, `s${index}`, "running", `2026-10-20T11:00:${String(index % 60).padStart(2, "0")}.000Z`));
  }
  publisher.publishSubagents();
  const published = written.at(-1)?.agents[WORKER].runningSubagents;
  assert.equal(published, SUBAGENTS_PER_AGENT_LIMIT);

  const after = written.length;
  publisher.publishSubagents();
  assert.equal(written.length, after);
});

test("no subagent field moves eventAt", () => {
  const { tracker, publisher, written } = harness();
  publisher.note("agent.turn_ended", WORKER, "2026-10-20T11:57:00.000Z");
  const turnEndedAt = written.at(-1)?.agents[WORKER].eventAt;

  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  publisher.publishSubagents();

  assert.equal(written.at(-1)?.agents[WORKER].eventAt, turnEndedAt);
  assert.equal(written.at(-1)?.agents[WORKER].subagentsSince, "2026-10-20T11:58:00.000Z");
});

test("the parent's archive drops its subagents", () => {
  const { tracker, publisher, written } = harness();
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  publisher.publishSubagents();
  assert.equal(written.at(-1)?.agents[WORKER].runningSubagents, 1);

  tracker.dropAgent(WORKER);
  publisher.publishSubagents();

  assert.equal(written.at(-1)?.agents[WORKER].runningSubagents, undefined);
});

test("a child falling to zero tells the seat, once for the fall", () => {
  const { tracker } = harness();
  let told = 0;
  tracker.watch(WORKER, () => {
    told += 1;
  });
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  tracker.noteUpdate(upsert(WORKER, "s1", "completed", "2026-10-20T11:58:00.000Z"));
  tracker.noteUpdate(upsert(WORKER, "s1", "completed", "2026-10-20T11:58:00.000Z"));

  assert.equal(told, 1);
});

test("the list merges under the events, and an update since the subscription wins over it", () => {
  const { tracker, publisher, written } = harness();
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));

  tracker.merge({
    agentId: WORKER,
    subagents: [
      { id: "s1", status: "running", createdAt: "2026-10-20T11:58:00.000Z" },
      { id: "s2", status: "running", createdAt: "2026-10-20T11:59:00.000Z" },
      { id: "s3", status: "completed", createdAt: "2026-10-20T11:59:30.000Z" },
    ],
  });
  publisher.publishSubagents();

  // s1 is decided by the event; s2 joins from the list; a completed one never counts.
  assert.equal(tracker.count(WORKER), 2);
  assert.equal(written.at(-1)?.agents[WORKER].subagentsSince, "2026-10-20T11:58:00.000Z");
});

test("a re-established subscription publishes incomplete under a new boot id", () => {
  const { publisher, written } = harness();
  let restarted = 0;
  const observers: { snapshot: () => void }[] = [];
  const source = {
    observeEvents: () => ({
      subscriptionId: "sub",
      ready: Promise.resolve({ subscriptionId: "sub" }),
      subscribe: (observer: { snapshot: () => void }) => {
        observers.push(observer);
        return () => {};
      },
      release: async () => {},
    }),
  } as unknown as SubagentEventSource;
  const subscription = new SubagentSubscription(
    new SubagentTracker(() => RECORDS),
    () => {},
    () => {
      restarted += 1;
      publisher.invalidate();
    },
  );
  subscription.attach(source);
  subscription.attach(source);
  assert.equal(observers.length, 1);

  observers[0].snapshot();
  assert.equal(restarted, 0);
  observers[0].snapshot();

  assert.equal(restarted, 1);
  assert.equal(written.at(-1)?.complete, false);
  assert.notEqual(written.at(-1)?.bootId, "boot-1");
});

// ---------------------------------------------------------------------------
// MIK-R190 rules 1 and 3: the bounded reconciliation generation heals a
// reconnect (L184-R1-F08), and the list merge keeps the fleet total at its
// declared bound (L184-R1-F12).
// ---------------------------------------------------------------------------

/** One agent's complete list, as the bridge reads it. */
function listed(parent: string, from: number, count: number, status = "running") {
  return {
    agentId: parent,
    subagents: Array.from({ length: count }, (_, index) => ({
      id: `s${from + index}`,
      status,
      createdAt: `2026-10-20T11:${String(Math.floor((from + index) / 60)).padStart(2, "0")}:${String((from + index) % 60).padStart(2, "0")}.000Z`,
    })),
  };
}

test("L184-R1-F08: a complete list removes the children it no longer reports", () => {
  const { tracker } = harness();
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  tracker.noteUpdate(upsert(WORKER, "s2", "running", "2026-10-20T11:59:00.000Z"));
  assert.equal(tracker.count(WORKER), 2);

  // The connection gap missed s1's remove and s2's completion: the healed list is the truth.
  assert.equal(
    tracker.merge({ agentId: WORKER, subagents: [{ id: "s2", status: "completed", createdAt: "2026-10-20T11:59:00.000Z" }] }),
    true,
  );
  assert.equal(tracker.count(WORKER), 0, "nothing the list does not report running survives");

  tracker.noteUpdate(upsert(WORKER, "s3", "running", "2026-10-20T12:00:00.000Z"));
  assert.equal(tracker.merge({ agentId: WORKER, subagents: [] }), true);
  assert.equal(tracker.count(WORKER), 0, "a complete empty list heals to zero");
  assert.equal(tracker.counts(WORKER).since, undefined, "and publishes no start");
});

test("L184-R1-F08: only the events of the current reconciliation win over the list", () => {
  const { tracker } = harness();
  // A pre-gap event said running; the healed list says completed.
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  const generation = tracker.beginReconcile();
  tracker.merge(
    { agentId: WORKER, subagents: [{ id: "s1", status: "completed", createdAt: "2026-10-20T11:58:00.000Z" }] },
    generation,
  );
  assert.equal(tracker.count(WORKER), 0, "a prior-generation event loses to the healed list");

  // An event that lands inside the same reconciliation race keeps its fate.
  const racing = tracker.beginReconcile();
  tracker.noteUpdate(upsert(WORKER, "s2", "running", "2026-10-20T12:01:00.000Z"));
  tracker.merge(
    { agentId: WORKER, subagents: [{ id: "s2", status: "completed", createdAt: "2026-10-20T12:01:00.000Z" }] },
    racing,
  );
  assert.equal(tracker.count(WORKER), 1, "the update of this race wins over the older list");

  // And the same race's remove keeps a listed child out.
  const removing = tracker.beginReconcile();
  tracker.noteUpdate({
    type: "agent.provider_subagents.update",
    payload: { kind: "remove", parentAgentId: WORKER, subagentId: "s2" },
  });
  tracker.merge({ agentId: WORKER, subagents: [{ id: "s2", status: "running", createdAt: "2026-10-20T12:01:00.000Z" }] }, removing);
  assert.equal(tracker.count(WORKER), 0, "the race's tombstone keeps the child out");
});

test("L184-R1-F08: a reply from an earlier reconciliation never applies", () => {
  const { tracker } = harness();
  const stale = tracker.beginReconcile();
  const current = tracker.beginReconcile();
  assert.equal(tracker.mergeAll([listed(WORKER, 0, 1)], stale), false, "the stale reply is refused");
  assert.equal(tracker.count(WORKER), 0);
  assert.equal(tracker.mergeAll([listed(WORKER, 0, 1)], current), true);
  assert.equal(tracker.count(WORKER), 1, "the current reply applies");
});

test("L184-R1-F08: the fall to zero tells the seat once, however often the list heals", () => {
  const { tracker } = harness();
  let told = 0;
  tracker.watch(WORKER, () => {
    told += 1;
  });
  tracker.noteUpdate(upsert(WORKER, "s1", "running", "2026-10-20T11:58:00.000Z"));
  const generation = tracker.beginReconcile();
  tracker.merge({ agentId: WORKER, subagents: [] }, generation);
  assert.equal(told, 1, "the healing tells the seat once");
  tracker.merge({ agentId: WORKER, subagents: [] }, generation);
  assert.equal(told, 1, "a repeated empty list tells nothing more");
});

test("L184-R1-F12: the list merge keeps the stored and published fleet within the bound", () => {
  const parents = 65;
  const fleetSize = 128;
  const records: Record<string, TurnEndedFacts> = {};
  for (let index = 0; index < fleetSize; index += 1) {
    records[`p-${index}`] = {
      role: "worker",
      task: `repo/master/leaf-${index}.json`,
      reportPath: `/tmp/report-${index}.md`,
    };
  }
  // One agent outside every fleet below, so a merge past the cap has somewhere to go.
  records["p-extra"] = { role: "worker", task: "repo/master/leaf-extra.json", reportPath: "/tmp/report-extra.md" };
  const stored = (tracker: SubagentTracker): number => {
    let total = 0;
    for (const id of Object.keys(records)) total += tracker.count(id);
    return total;
  };

  const written: HostActivityPublication[] = [];
  const tracker = new SubagentTracker(() => records);
  const publisher = new HostActivityPublisher(
    () => records,
    publication => written.push(structuredClone(publication)),
    () => "2026-10-20T12:00:00.000Z",
    "boot-1",
    tracker,
  );
  for (let index = 0; index < parents; index += 1) {
    publisher.note("agent.created", `p-${index}`, "2026-10-20T11:00:00.000Z");
  }

  // 65 parents of 63 running children: 4,095 stored.
  tracker.mergeAll(Array.from({ length: parents }, (_, index) => listed(`p-${index}`, 0, 63)));
  assert.equal(stored(tracker), 4095);

  // One more child for each of two parents: the per-parent cap allows both, the fleet does not.
  tracker.mergeAll([listed("p-0", 63, 1), listed("p-1", 63, 1)]);
  assert.ok(stored(tracker) <= SUBAGENTS_TOTAL_LIMIT, `stored ${stored(tracker)} within ${SUBAGENTS_TOTAL_LIMIT}`);
  for (let index = 0; index < parents; index += 1) {
    assert.ok(tracker.count(`p-${index}`) <= SUBAGENTS_PER_AGENT_LIMIT, `p-${index} within its own cap`);
  }

  publisher.publishSubagents();
  const published = written.at(-1) as HostActivityPublication;
  const publishedTotal = Object.values(published.agents).reduce(
    (total, agent) => total + (agent.runningSubagents ?? 0),
    0,
  );
  assert.ok(publishedTotal <= SUBAGENTS_TOTAL_LIMIT, `published ${publishedTotal} within ${SUBAGENTS_TOTAL_LIMIT}`);
  assert.equal(publishedTotal, stored(tracker), "what is stored is what is published");

  // At the cap exactly, a further parent's child is shed rather than stored.
  for (const [size, each] of [
    [64, 64],
    [128, 32],
  ] as const) {
    const full = new SubagentTracker(() => records);
    full.mergeAll(Array.from({ length: size }, (_, index) => listed(`p-${index}`, 0, each)));
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT, `${size} x ${each} fills the fleet exactly`);
    full.merge(listed("p-extra", 0, 1));
    assert.equal(full.count("p-extra"), 0, `${size} x ${each}: nothing is stored past the bound`);
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT);
    // A removal frees its room, and the next merge may use it.
    full.merge(listed("p-0", 1, each - 1));
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT - 1, "a removed child frees its room");
    full.merge(listed("p-extra", 0, 1));
    assert.equal(full.count("p-extra"), 1, "the freed room is reused");
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT);

    // The event path shares the same total: a new child at the cap is refused,
    // and a remove frees its room for the next snapshot too.
    assert.equal(
      full.noteUpdate(upsert("p-extra", "event-1", "running", "2026-10-20T12:30:00.000Z")),
      false,
      "an event at the cap stores nothing",
    );
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT);
    full.noteUpdate({
      type: "agent.provider_subagents.update",
      payload: { kind: "remove", parentAgentId: "p-0", subagentId: "s1" },
    });
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT - 1, "an event's remove frees its room");
    assert.equal(
      full.noteUpdate(upsert("p-extra", "event-1", "running", "2026-10-20T12:30:00.000Z")),
      true,
      "and the next event may use it",
    );
    assert.equal(stored(full), SUBAGENTS_TOTAL_LIMIT);
  }
});
