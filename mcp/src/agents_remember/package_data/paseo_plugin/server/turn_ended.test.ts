/**
 * MIK-R117 rule 11: the plugin's turn-end decision, without a host.
 *
 * Run: `node --experimental-strip-types --test server/` from the plugin dir.
 * Every ruled case: the silent line, the return, the busy parent, the
 * running child, the permission wait, the unrecorded parent, the batch, the
 * limit, and exactly-once delivery.
 */
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { PluginLifecycleRegistration } from "@getpaseo/plugin/server";
import { registerParentWakeEvents } from "./turn_events.ts";
import { test } from "node:test";
import assert from "node:assert";
import {
  TURN_ENDED_LINE_LIMIT,
  composeTurnEndedLine,
  createTurnEndedTracker,
  flushHeldLines,
  noteAgentArchived,
  notePermissionRequested,
  notePermissionResolved,
  noteTurnEnded,
  noteTurnStarted,
  turnHasReturnToParent,
  type TurnEndedAgent,
  type TurnEndedFacts,
  type TurnEndedSender,
  type TurnEndedTracker,
} from "./turn_ended.ts";

const CHILD: TurnEndedFacts = {
  role: "worker",
  task: "agents-remember/master/01_leaf.json",
  reportPath: "/notes/reports/worker/aaa/role-launch/report.md",
};
const PARENT: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/01_leaf.json",
  reportPath: "/notes/reports/manager/bbb/role-launch/report.md",
};
const RECORDS: Record<string, TurnEndedFacts> = { child: CHILD, parent: PARENT };
const CHILD_AGENT: TurnEndedAgent = { id: "child", parentAgentId: "parent" };
const PARENT_AGENT: TurnEndedAgent = { id: "parent", parentAgentId: "grandparent" };

interface SentMessage {
  agentId: string;
  text: string;
}

function makeSender(failParents: string[] = []): TurnEndedSender & { sent: SentMessage[] } {
  const sent: SentMessage[] = [];
  return {
    sent,
    sendMessage(agentId: string, text: string): Promise<void> {
      if (failParents.includes(agentId)) return Promise.reject(new Error("parent busy"));
      sent.push({ agentId, text });
      return Promise.resolve();
    },
  };
}

test("a silent end sends one line with the four facts and no agent text", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent.length, 1);
  const message = sender.sent[0];
  assert.equal(message.agentId, "parent");
  assert.ok(message.text.includes("worker"), "names the role");
  assert.ok(message.text.includes("child"), "names the agent");
  assert.ok(message.text.includes(CHILD.task), "names the task");
  assert.ok(message.text.includes("turn ended"), "says the turn ended");
  assert.ok(message.text.includes(CHILD.reportPath), "names the report place");
  assert.ok(!message.text.includes("verdict text"), "carries no agent text");
  assert.ok(message.text.length <= TURN_ENDED_LINE_LIMIT, "keeps the limit");
});

test("a return to the parent by agent id gives no line", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const timeline = [
    { type: "tool_call", name: "role_message", arguments: { agent_id: "parent", text: "done" } },
  ];
  const outcome = await noteTurnEnded(CHILD_AGENT, timeline, RECORDS, tracker, sender);
  assert.equal(outcome.kind, "none");
  assert.equal(sender.sent.length, 0);
});

test("a return to the parent by role and task gives no line", async () => {
  assert.equal(
    turnHasReturnToParent(
      [{ type: "tool_call", name: "role_message", detail: { role: "manager", task: CHILD.task } }],
      "parent",
      PARENT,
    ),
    true,
  );
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(
    CHILD_AGENT,
    [{ type: "tool_call", name: "role_message", detail: { role: "manager", task: CHILD.task } }],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "none");
  assert.equal(sender.sent.length, 0);
});

test("a handover to a sibling is not a return: the parent still learns", async () => {
  assert.equal(
    turnHasReturnToParent(
      [{ type: "tool_call", name: "role_message", arguments: { agent_id: "reviewer" } }],
      "parent",
      PARENT,
    ),
    false,
  );
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(
    CHILD_AGENT,
    [{ type: "tool_call", name: "role_message", arguments: { agent_id: "reviewer" } }],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent.length, 1);
});

test("a parent in a turn gets the line once when it is free", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted(PARENT_AGENT, tracker);
  const held = await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(held.kind, "held");
  assert.equal(sender.sent.length, 0);
  const freed = await noteTurnEnded(PARENT_AGENT, [], { ...RECORDS, grandparent: PARENT }, tracker, sender);
  assert.equal(sender.sent.length, 2);
  const toParent = sender.sent.filter((message) => message.agentId === "parent");
  assert.equal(toParent.length, 1);
  assert.ok(toParent[0].text.includes("child"));
  const upward = sender.sent.filter((message) => message.agentId === "grandparent");
  assert.equal(upward.length, 1);
  assert.ok(upward[0].text.includes("parent"));
  assert.equal(freed.kind, "sent");
  assert.equal(tracker.held.length, 0);
});

test("a parent waiting for a permission gets the line on resolve", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  notePermissionRequested("parent", tracker);
  const held = await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(held.kind, "held");
  assert.equal(sender.sent.length, 0);
  await notePermissionResolved("parent", tracker, sender);
  assert.equal(sender.sent.length, 1);
  assert.ok(sender.sent[0].text.includes("child"));
});

test("a running child holds the line until it ends", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted({ id: "grandchild", parentAgentId: "child" }, tracker);
  const held = await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(held.kind, "held");
  assert.equal(sender.sent.length, 0);
  const records = { ...RECORDS, grandchild: CHILD };
  await noteTurnEnded({ id: "grandchild", parentAgentId: "child" }, [], records, tracker, sender);
  assert.equal(sender.sent.length, 2);
  assert.ok(sender.sent.some((message) => message.agentId === "child"));
  assert.ok(sender.sent.some((message) => message.agentId === "parent"));
});

test("a held line drops when one of its own begins again", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted({ id: "grandchild", parentAgentId: "child" }, tracker);
  await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(tracker.held.length, 1);
  noteTurnStarted({ id: "grandchild", parentAgentId: "child" }, tracker);
  assert.equal(tracker.held.length, 0);
});

test("an agent waiting for a permission is not reported", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  notePermissionRequested("child", tracker);
  const outcome = await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(outcome.kind, "none");
  assert.equal(sender.sent.length, 0);
  assert.equal(tracker.held.length, 0);
});

test("an agent whose parent the product did not start gives no line", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const developerChat: TurnEndedAgent = { id: "child", parentAgentId: "chat-1" };
  assert.equal((await noteTurnEnded(developerChat, [], RECORDS, tracker, sender)).kind, "none");
  const noParent: TurnEndedAgent = { id: "child", parentAgentId: null };
  assert.equal((await noteTurnEnded(noParent, [], RECORDS, tracker, sender)).kind, "none");
  assert.equal((await noteTurnEnded(CHILD_AGENT, [], { parent: PARENT }, tracker, sender)).kind, "none");
  assert.equal(sender.sent.length, 0);
});

test("held lines for one parent go out together as one message", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted(PARENT_AGENT, tracker);
  const records = { ...RECORDS, second: CHILD };
  await noteTurnEnded(CHILD_AGENT, [], records, tracker, sender);
  await noteTurnEnded({ id: "second", parentAgentId: "parent" }, [], records, tracker, sender);
  await noteTurnEnded(PARENT_AGENT, [], { ...records, grandparent: PARENT }, tracker, sender);
  const toParent = sender.sent.filter((message) => message.agentId === "parent");
  assert.equal(toParent.length, 1);
  const lines = toParent[0].text.split("\n");
  assert.equal(lines.length, 2);
  assert.ok(lines[0].includes("child") || lines[1].includes("child"));
  assert.ok(lines[0].includes("second") || lines[1].includes("second"));
});

test("the line keeps the limit of 1000 characters", () => {
  const long: TurnEndedFacts = { ...CHILD, reportPath: `/notes/${"r".repeat(2000)}.md` };
  const line = composeTurnEndedLine("child", long);
  assert.ok(line.length <= TURN_ENDED_LINE_LIMIT);
  assert.ok(line.includes("worker"));
});

test("an archived parent drops what waits for it", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted(PARENT_AGENT, tracker);
  await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(tracker.held.length, 1);
  noteAgentArchived("parent", tracker);
  assert.equal(tracker.held.length, 0);
});

test("a failed send keeps the line and delivers it once on retry", async () => {
  const tracker = createTurnEndedTracker();
  const failing = makeSender(["parent"]);
  const held = await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, failing);
  assert.equal(held.kind, "held");
  assert.equal(tracker.held.length, 1);
  const retry = makeSender();
  const outcome = await flushHeldLines(tracker, retry);
  assert.equal(outcome.sent.length, 1);
  assert.equal(retry.sent.length, 1);
  assert.equal(tracker.held.length, 0);
});

test("overlapping flushes deliver one held line once (L117-C1-F06)", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted(PARENT_AGENT, tracker);
  await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(tracker.held.length, 1);
  tracker.inTurn.delete("parent");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const gated: TurnEndedSender & { sent: SentMessage[] } = {
    sent: [],
    sendMessage(agentId: string, text: string): Promise<void> {
      gated.sent.push({ agentId, text });
      return gate.then(() => undefined);
    },
  };
  const first = flushHeldLines(tracker, gated);
  const second = flushHeldLines(tracker, gated);
  release();
  const [one, two] = await Promise.all([first, second]);
  assert.equal(gated.sent.length, 1);
  assert.equal(one.sent.length + two.sent.length, 1);
  assert.equal(tracker.held.length, 0);
});

test("overlapping turn ends for one parent deliver each line once", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const records = { ...RECORDS, second: CHILD };
  const [one, two] = await Promise.all([
    noteTurnEnded(CHILD_AGENT, [], records, tracker, sender),
    noteTurnEnded({ id: "second", parentAgentId: "parent" }, [], records, tracker, sender),
  ]);
  assert.equal(sender.sent.length, 2);
  const texts = sender.sent.map((message) => message.text).sort();
  assert.notEqual(texts[0], texts[1]);
  assert.equal(one.kind, "sent");
  assert.equal(two.kind, "sent");
  assert.equal(tracker.held.length, 0);
});

test("a restart drops a claimed line waiting behind another send (L117-C1-F06)", async () => {
  const tracker = createTurnEndedTracker();
  const second = { subject: "subject-b", parent: "parent-b", line: "line-b" };
  tracker.held.push(
    { subject: "subject-a", parent: "parent-a", line: "line-a" },
    second,
  );
  const sent: SentMessage[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const sender: TurnEndedSender = {
    sendMessage(agentId: string, text: string): Promise<void> {
      sent.push({ agentId, text });
      if (agentId === "parent-a") return gate.then(() => undefined);
      return Promise.resolve();
    },
  };
  const first = flushHeldLines(tracker, sender);
  assert.equal(sent.length, 1);
  assert.equal(tracker.held.length, 0);
  noteTurnStarted({ id: "descendant-b", parentAgentId: "subject-b" }, tracker);
  release();
  const outcome = await first;
  assert.equal(sent.length, 1);
  assert.equal(sent[0].agentId, "parent-a");
  assert.equal(tracker.held.length, 0);
  assert.deepEqual(outcome.dropped, [second]);
  tracker.inTurn.delete("descendant-b");
  const later = await flushHeldLines(tracker, sender);
  assert.equal(sent.length, 1);
  assert.equal(later.sent.length, 0);
});


test("contributed parent-wake handlers keep permission and turn gates and send with steer", async () => {
  const home = mkdtempSync(join(tmpdir(), "ar-wake-handlers-"));
  const previous = process.env.PASEO_HOME;
  process.env.PASEO_HOME = home;
  mkdirSync(join(home, "agents-remember"));
  writeFileSync(join(home, "agents-remember", "role-agents.json"), JSON.stringify({ agents: RECORDS }));
  type Handler = (event: unknown, context: unknown) => void;
  const handlers = new Map<string, Handler>();
  const server = { on(name: string, handler: Handler) { handlers.set(name, handler); return () => {}; } } as unknown as Pick<PluginLifecycleRegistration, "on">;
  const sent: unknown[] = [];
  const context = { paseo: { agents: { ref(id: string) { return { async send(text: string, options: unknown) { sent.push([id, text, options]); } }; } } } };
  const note = async (kind: string, id: string, parentAgentId: string | null = null) => {
    const handler = handlers.get(kind);
    assert.ok(handler, kind);
    handler({ agent: { id, parentAgentId }, timeline: [] }, context);
    await new Promise<void>(resolve => setImmediate(resolve));
  };
  try {
    registerParentWakeEvents(server);
    assert.deepEqual([...handlers.keys()], ["agent.turn_started", "agent.turn_ended", "agent.permission_requested", "agent.permission_resolved", "agent.archived"]);
    await note("agent.turn_started", "parent");
    await note("agent.turn_started", "child", "parent");
    await note("agent.turn_ended", "child", "parent");
    assert.deepEqual(sent, []);
    await note("agent.permission_requested", "parent");
    await note("agent.turn_ended", "parent");
    assert.deepEqual(sent, []);
    await note("agent.permission_resolved", "parent");
    assert.deepEqual(sent, [["parent", composeTurnEndedLine("child", CHILD), { activeTurnBehavior: "steer" }]]);
    await note("agent.turn_started", "child", "parent");
    await note("agent.turn_ended", "child", "parent");
    assert.equal(sent.length, 2);
    assert.deepEqual((sent[1] as unknown[])[2], { activeTurnBehavior: "steer" });
  } finally {
    if (previous === undefined) delete process.env.PASEO_HOME;
    else process.env.PASEO_HOME = previous;
    rmSync(home, { recursive: true, force: true });
  }
});
