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
  TURN_ENDED_DEPARTED_LIMIT,
  TURN_ENDED_LAST_TEXT_LIMIT,
  TURN_ENDED_LINE_LIMIT,
  TURN_ENDED_PARENT_LIMIT,
  TURN_ENDED_PROVIDER_LIMIT,
  TURN_ENDED_STARTED_LIMIT,
  composeTurnEndedLine,
  createTurnEndedTracker,
  flushHeldLines,
  lastLineOfTurn,
  noteAgentArchived,
  notePermissionRequested,
  notePermissionResolved,
  noteTurnEnded,
  noteTurnStarted,
  partitionHeldLines,
  turnHasReturnToParent,
  turnRows,
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
const MANAGER: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/task.json",
  reportPath: "/notes/reports/manager/ccc/role-launch/report.md",
};
const MANAGER2: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/task.json",
  reportPath: "/notes/reports/manager/ddd/role-launch/report.md",
};
const RECORDS: Record<string, TurnEndedFacts> = { child: CHILD, parent: PARENT };
const MANAGER2_RECORDS: TurnEndedFacts = MANAGER2;
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

test("a silent end sends one line with the facts and the agent's last line", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const timeline = [
    {
      type: "assistant_message",
      text: "Handing over now.\n\nD316-v2-worker-state-note.md, sha 402a7932",
    },
  ];
  const outcome = await noteTurnEnded(CHILD_AGENT, timeline, RECORDS, tracker, sender);
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent.length, 1);
  const message = sender.sent[0];
  assert.equal(message.agentId, "parent");
  assert.ok(message.text.includes("worker"), "names the role");
  assert.ok(message.text.includes("child"), "names the agent");
  assert.ok(message.text.includes(CHILD.task), "names the task");
  assert.ok(message.text.includes("turn ended"), "says the turn ended");
  assert.ok(message.text.includes(CHILD.reportPath), "names the report place");
  // MIK-R172 rule 3: the line carries the agent's last line of text.
  assert.ok(
    message.text.includes("last line: D316-v2-worker-state-note.md, sha 402a7932"),
    "carries the last line",
  );
  assert.ok(!message.text.includes("Handing over now."), "carries the last line only");
  assert.ok(message.text.length <= TURN_ENDED_LINE_LIMIT, "keeps the limit");
});

test("the last line is shortened to 300 characters", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const long = "s".repeat(TURN_ENDED_LAST_TEXT_LIMIT + 200);
  const outcome = await noteTurnEnded(
    CHILD_AGENT,
    [{ type: "assistant_message", text: long }],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "sent");
  const text = sender.sent[0].text;
  assert.ok(text.includes(`last line: ${"s".repeat(TURN_ENDED_LAST_TEXT_LIMIT)}`));
  assert.ok(!text.includes("s".repeat(TURN_ENDED_LAST_TEXT_LIMIT + 1)), "not one character more");
});

test("an empty last text says no text and an unreadable one says so", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.ok(sender.sent[0].text.includes("last line: no text"));
  assert.deepEqual(lastLineOfTurn(null as unknown as readonly unknown[]), { kind: "unreadable" });
  const unreadable = composeTurnEndedLine("child", CHILD, { kind: "unreadable" });
  assert.ok(unreadable.includes("last line: unreadable"));
});

test("a refused return gives the line (MIK-R172 rule 4)", async () => {
  const refusal = { type: "plain_text", text: JSON.stringify({ ok: false, status: "refused", refusal: "recipient-archived" }) };
  const timeline = [
    { type: "tool_call", name: "role_message", arguments: { agent_id: "parent", text: "done" }, detail: refusal },
  ];
  assert.equal(turnHasReturnToParent(timeline, "parent", PARENT), false);
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(CHILD_AGENT, timeline, RECORDS, tracker, sender);
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0].agentId, "parent");
  assert.ok(sender.sent[0].text.includes("turn ended"));
});

test("a failed tool call for the return gives the line", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(
    CHILD_AGENT,
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "failed",
        error: { message: "host refused" },
        arguments: { agent_id: "parent" },
      },
    ],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent.length, 1);
});

test("an agent whose recorded parent is archived wakes the current Manager of its master", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const records = { ...RECORDS, "manager-2": MANAGER };
  noteAgentArchived("parent", tracker);
  const outcome = await noteTurnEnded(CHILD_AGENT, [], records, tracker, sender);
  assert.equal(outcome.kind, "sent", JSON.stringify(outcome));
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0].agentId, "manager-2");
  assert.ok(sender.sent[0].text.includes("child"));
});

test("an agent whose recorded parent is missing wakes the current Manager of its master", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(
    CHILD_AGENT,
    [],
    { child: CHILD, "manager-2": MANAGER },
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent[0].agentId, "manager-2");
});

test("a line held for a parent that is archived moves to the alternate Manager", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const records = { ...RECORDS, "manager-2": MANAGER };
  noteTurnStarted(PARENT_AGENT, tracker);
  await noteTurnEnded(CHILD_AGENT, [], records, tracker, sender);
  assert.equal(tracker.held.length, 1);
  noteAgentArchived("parent", tracker);
  assert.equal(tracker.held.length, 1);
  assert.equal(tracker.held[0].parent, "manager-2");
  await noteTurnEnded(PARENT_AGENT, [], { ...records, grandparent: PARENT }, tracker, sender);
  assert.ok(sender.sent.some((message) => message.agentId === "manager-2"));
});

test("an agent the agent list does not hold is named in the host log once", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const first = await noteTurnEnded(CHILD_AGENT, [], { parent: PARENT }, tracker, sender);
  assert.equal(first.kind, "none");
  assert.ok(first.kind === "none" && first.log !== undefined, "names the reason");
  assert.ok(first.kind === "none" && first.log!.includes("child"));
  assert.ok(first.kind === "none" && first.log!.includes("role-agents.json"));
  const second = await noteTurnEnded(CHILD_AGENT, [], { parent: PARENT }, tracker, sender);
  assert.equal(second.kind === "none" ? second.log : "logged twice", undefined);
  assert.equal(sender.sent.length, 0);
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

test("an agent whose recorded parent is not a role agent wakes the current Manager", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  // The host names a parent AR did not start as a role agent: the leaf's line
  // still reaches whoever waits for it (MIK-R172 rule 5, MIK-R117 rule 10).
  const developerChat: TurnEndedAgent = { id: "child", parentAgentId: "chat-1" };
  assert.equal((await noteTurnEnded(developerChat, [], RECORDS, tracker, sender)).kind, "sent");
  assert.equal(sender.sent[0].agentId, "parent");
  // No parent at all: nothing is owed.
  const noParent: TurnEndedAgent = { id: "child", parentAgentId: null };
  assert.equal((await noteTurnEnded(noParent, [], RECORDS, tracker, sender)).kind, "none");
  assert.equal(sender.sent.length, 1);
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
  const line = composeTurnEndedLine("child", long, { kind: "empty" });
  assert.ok(line.length <= TURN_ENDED_LINE_LIMIT);
  assert.ok(line.includes("worker"));
});

test("an archived parent keeps what waits for it until a current Manager sends it", async () => {
  // L172-R1-F02: a line owed to the recorded parent must survive an ordinary
  // Manager replacement. Archive first, successor later: the line stays owed and
  // the successor receives it, once.
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted(PARENT_AGENT, tracker);
  await noteTurnEnded(CHILD_AGENT, [], RECORDS, tracker, sender);
  assert.equal(tracker.held.length, 1);
  noteAgentArchived("parent", tracker);
  assert.equal(tracker.held.length, 1, "the line stays owed while no Manager is current");
  const withSuccessor = { ...RECORDS, manager2: MANAGER2_RECORDS };
  noteTurnStarted({ id: "manager2", parentAgentId: null }, tracker);
  tracker.inTurn.delete("manager2"); // the successor is idle again
  const outcome = await flushHeldLines(tracker, sender, withSuccessor);
  assert.equal(outcome.sent.length, 1);
  assert.equal(sender.sent[0]?.agentId, "manager2");
  assert.equal(tracker.held.length, 0);
});

test("a held line reaches the successor when the archive has no alternate yet (L172-R1-F02)", async () => {
  // Successor first, archive second: the same single delivery.
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const records = { ...RECORDS, manager2: MANAGER2_RECORDS };
  noteTurnStarted(PARENT_AGENT, tracker);
  noteTurnStarted({ id: "manager2", parentAgentId: null }, tracker);
  await noteTurnEnded(CHILD_AGENT, [], records, tracker, sender);
  assert.equal(tracker.held.length, 1, "the parent is in a turn, so the line waits");
  noteAgentArchived("parent", tracker);
  tracker.inTurn.delete("manager2"); // the successor is idle again
  const outcome = await flushHeldLines(tracker, sender, records);
  assert.equal(outcome.sent.length, 1);
  assert.equal(sender.sent[0]?.agentId, "manager2");
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
    assert.deepEqual(sent, [["parent", composeTurnEndedLine("child", CHILD, { kind: "empty" }), { activeTurnBehavior: "steer" }]]);
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

test("a return in an earlier turn does not silence this turn's line (L172-R1-F01)", async () => {
  const timeline = [
    { type: "user_message", text: "turn one", turnId: "turn1" },
    {
      type: "tool_call",
      name: "role_message",
      status: "completed",
      turnId: "turn1",
      detail: { recipientAgentId: "parent" },
    },
    { type: "assistant_message", text: "handed over", turnId: "turn1" },
    { type: "user_message", text: "turn two", turnId: "turn2" },
    { type: "assistant_message", text: "second turn says this", turnId: "turn2" },
  ];
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const outcome = await noteTurnEnded(CHILD_AGENT, timeline, RECORDS, tracker, sender, "turn2");
  assert.equal(outcome.kind, "sent");
  assert.match(sender.sent[0]?.text ?? "", /last line: second turn says this/);
});

test("an empty ended turn says no text, never an earlier turn's text (L172-R1-F01)", async () => {
  const timeline = [
    { type: "assistant_message", text: "stale earlier turn", turnId: "turn1" },
    { type: "user_message", text: "turn two", turnId: "turn2" },
  ];
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  await noteTurnEnded(CHILD_AGENT, timeline, RECORDS, tracker, sender, "turn2");
  assert.match(sender.sent[0]?.text ?? "", /last line: no text/);
  assert.doesNotMatch(sender.sent[0]?.text ?? "", /stale earlier turn/);
});

test("the turn boundary is the last user message when rows carry no turn id (L172-R1-F01)", () => {
  const timeline = [
    { type: "assistant_message", text: "old" },
    { type: "user_message", text: "prompt" },
    { type: "assistant_message", text: "new" },
  ];
  const rows = turnRows(timeline, null);
  assert.equal(rows.length, 2);
  assert.equal(lastLineOfTurn(rows).kind, "text");
});

test("a return addressed by role and canonical repository/path is an accepted return (L172-R1-F03)", () => {
  const parent: TurnEndedFacts = {
    role: "manager",
    task: "agents-remember:260928_maintained-invariant-knowledge/task.json",
    reportPath: "/r.md",
  };
  const accepted = turnHasReturnToParent(
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "completed",
        detail: {
          role: "manager",
          sprint_document_ref: { repository: "agents-remember", path: "260713_improved-agentic-system/task.json" },
          master_document_ref: {
            repository: "agents-remember",
            path: "260928_maintained-invariant-knowledge/task.json",
          },
        },
      },
    ],
    "parent-id",
    parent,
  );
  assert.equal(accepted, true);
  const wrongTask = turnHasReturnToParent(
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "completed",
        detail: { role: "manager", master_document_ref: { repository: "agents-remember", path: "other/task.json" } },
      },
    ],
    "parent-id",
    parent,
  );
  assert.equal(wrongTask, false);
  const otherRepository = turnHasReturnToParent(
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "completed",
        detail: {
          role: "manager",
          master_document_ref: {
            repository: "another-repository",
            path: "260928_maintained-invariant-knowledge/task.json",
          },
        },
      },
    ],
    "parent-id",
    parent,
  );
  assert.equal(otherRepository, false, "the same path under another repository is not this parent");
  const noRepository = turnHasReturnToParent(
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "completed",
        detail: { role: "manager", master_document_ref: { path: "260928_maintained-invariant-knowledge/task.json" } },
      },
    ],
    "parent-id",
    parent,
  );
  assert.equal(noRepository, true, "a path with no repository stated still names the task");
  const sprintElsewhere = turnHasReturnToParent(
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "completed",
        detail: {
          role: "manager",
          sprint_document_ref: { repository: "another-repository", path: "260713_improved-agentic-system/task.json" },
          master_document_ref: {
            repository: "agents-remember",
            path: "260928_maintained-invariant-knowledge/task.json",
          },
        },
      },
    ],
    "parent-id",
    parent,
  );
  assert.equal(sprintElsewhere, true, "another reference's repository does not veto the master reference");
  const wrongRole = turnHasReturnToParent(
    [
      {
        type: "tool_call",
        name: "role_message",
        status: "completed",
        detail: {
          role: "reviewer",
          master_document_ref: {
            repository: "agents-remember",
            path: "260928_maintained-invariant-knowledge/task.json",
          },
        },
      },
    ],
    "parent-id",
    parent,
  );
  assert.equal(wrongRole, false);
});

test("the tracker's historical stores stay bounded (L172-R1-F04)", () => {
  const tracker = createTurnEndedTracker();
  for (let index = 0; index < 400; index += 1) {
    noteTurnStarted({ id: `agent-${index}`, parentAgentId: `parent-${index}` }, tracker);
    noteAgentArchived(`agent-${index}`, tracker);
  }
  assert.ok(tracker.started.length <= TURN_ENDED_STARTED_LIMIT);
  assert.ok(tracker.archived.size <= TURN_ENDED_DEPARTED_LIMIT);
  assert.ok(tracker.parentOf.size <= TURN_ENDED_PARENT_LIMIT);
});

test("two long owed lines travel whole, in messages within the cap (L172-R1-F05)", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const longPath = `/notes/reports/worker/${"p".repeat(380)}/role-launch/report.md`;
  const records: Record<string, TurnEndedFacts> = {
    ...RECORDS,
    child: { ...CHILD, reportPath: longPath },
    child2: { ...CHILD, reportPath: longPath },
  };
  // Two subjects whose lines each exceed half the cap, held while the parent works.
  noteTurnStarted(PARENT_AGENT, tracker);
  for (const subject of ["child", "child2"]) {
    await noteTurnEnded(
      { id: subject, parentAgentId: "parent" },
      [{ type: "assistant_message", text: `${subject}: ${"y".repeat(300)}` }],
      records,
      tracker,
      sender,
    );
  }
  assert.equal(tracker.held.length, 2);
  const lineLengths = tracker.held.map((line) => line.line.length);
  assert.ok(lineLengths.every((length) => length > TURN_ENDED_LINE_LIMIT / 2 && length <= TURN_ENDED_LINE_LIMIT));
  tracker.inTurn.delete("parent");
  const outcome = await flushHeldLines(tracker, sender, records);
  assert.equal(outcome.sent.length, 2, "two messages, one per whole line");
  for (const message of sender.sent) {
    assert.ok(message.text.length <= TURN_ENDED_LINE_LIMIT);
    assert.ok(message.text.startsWith("turn ended:"));
    assert.ok(!message.text.endsWith("\n"));
  }
  assert.equal(tracker.held.length, 0);
  const joined = sender.sent.map((message) => message.text).join("\n");
  assert.match(joined, /child: y{200,}/);
  assert.match(joined, /child2: y{200,}/);
  assert.match(joined, /newest report: \/notes\/reports\/worker\/p{380}\/role-launch\/report\.md/);
});

test("a batch of short lines is packed into one message (L172-R1-F05 control)", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted(PARENT_AGENT, tracker);
  const records: Record<string, TurnEndedFacts> = { ...RECORDS, child2: CHILD };
  for (const subject of ["child", "child2"]) {
    await noteTurnEnded(
      { id: subject, parentAgentId: "parent" },
      [{ type: "assistant_message", text: `${subject} says one line` }],
      records,
      tracker,
      sender,
    );
  }
  tracker.inTurn.delete("parent");
  const outcome = await flushHeldLines(tracker, sender, records);
  assert.equal(outcome.sent.length, 1, "both short lines fit one message");
  assert.match(sender.sent[0]?.text ?? "", /child says one line/);
  assert.match(sender.sent[0]?.text ?? "", /child2 says one line/);
});

test("partitionHeldLines never yields a message over the cap", () => {
  const lines = [
    { subject: "a", parent: "p", line: "z".repeat(600) },
    { subject: "b", parent: "p", line: "w".repeat(600) },
    { subject: "c", parent: "p", line: "short" },
  ];
  const { messages, count } = partitionHeldLines(lines);
  assert.equal(count, 3);
  assert.equal(messages.length, 2);
  for (const message of messages) assert.ok(message.text.length <= TURN_ENDED_LINE_LIMIT);
});

test("a line for a parent in a turn waits for that turn and is not sent into it (MIK-R172@v2 rule 6)", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted({ id: "parent", parentAgentId: null, provider: "provider-without-steering" }, tracker);
  const outcome = await noteTurnEnded(
    { id: "child", parentAgentId: "parent", provider: "provider-without-steering" },
    [{ type: "assistant_message", text: "child handover" }],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "held");
  assert.equal(sender.sent.length, 0, "nothing is sent while the parent's turn runs");
  assert.equal(tracker.held.length, 1);
  // The parent's turn ends: the held line goes out once, and no steer was needed.
  const flushed = await noteTurnEnded(
    { id: "parent", parentAgentId: null, provider: "provider-without-steering" },
    [{ type: "assistant_message", text: "manager finished" }],
    RECORDS,
    tracker,
    sender,
  );
  assert.ok(sender.sent.some((message) => message.agentId === "parent"));
  assert.equal(tracker.held.length, 0);
  assert.equal(flushed.kind === "sent" || flushed.kind === "none", true);
});

test("a parent whose provider is known to steer gets the line at once (MIK-R172@v2 rule 6)", async () => {
  const tracker = createTurnEndedTracker(new Set(["provider-that-steers"]));
  const sender = makeSender();
  noteTurnStarted({ id: "parent", parentAgentId: null, provider: "provider-that-steers" }, tracker);
  const outcome = await noteTurnEnded(
    { id: "child", parentAgentId: "parent", provider: "provider-that-steers" },
    [{ type: "assistant_message", text: "child handover" }],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "sent");
  assert.equal(sender.sent.length, 1);
  assert.equal(tracker.held.length, 0);
});

test("a parent whose provider is unknown is treated as one that does not steer", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  noteTurnStarted({ id: "parent", parentAgentId: null }, tracker);
  const outcome = await noteTurnEnded(
    { id: "child", parentAgentId: "parent" },
    [{ type: "assistant_message", text: "child handover" }],
    RECORDS,
    tracker,
    sender,
  );
  assert.equal(outcome.kind, "held");
  assert.equal(sender.sent.length, 0);
});

test("held lines are sent in the order the turns ended, across a requeue (rule 6)", async () => {
  const tracker = createTurnEndedTracker();
  const sender = makeSender();
  const records = { ...RECORDS, child2: CHILD, child3: CHILD };
  noteTurnStarted({ id: "parent", parentAgentId: null }, tracker);
  for (const subject of ["child", "child2", "child3"]) {
    await noteTurnEnded(
      { id: subject, parentAgentId: "parent" },
      [{ type: "assistant_message", text: `${subject} says` }],
      records,
      tracker,
      sender,
    );
  }
  assert.equal(tracker.held.length, 3);
  // A failed send re-queues its line; the order must survive that.
  const failing = makeSender(["parent"]);
  await flushHeldLines(tracker, failing, records);
  assert.equal(tracker.held.length, 3);
  tracker.inTurn.delete("parent");
  const outcome = await flushHeldLines(tracker, sender, records);
  assert.equal(outcome.sent.length, 1, "all three short lines fit one message");
  const text = sender.sent[0]?.text ?? "";
  const first = text.indexOf("child says");
  const second = text.indexOf("child2 says");
  const third = text.indexOf("child3 says");
  assert.ok(first >= 0 && second > first && third > second, "turn-end order is kept");
});

test("a second batch is not sent into the parent turn the first batch started (L172-R4-F01)", async () => {
  const tracker = createTurnEndedTracker();
  const sends: { inTurnBeforeSend: boolean }[] = [];
  const sender = {
    sent: [] as SentMessage[],
    sendMessage: async (id: string, text: string) => {
      sends.push({ inTurnBeforeSend: tracker.inTurn.has(id) });
      // The host starts the parent's turn on delivery, and the plugin learns it.
      noteTurnStarted({ id, parentAgentId: null, provider: "nonsteering" }, tracker);
      void text;
    },
  };
  const longPath = `/reports/${"p".repeat(360)}.md`;
  const records = { a: { ...CHILD, reportPath: longPath }, b: { ...CHILD, reportPath: longPath }, parent: PARENT };
  noteTurnStarted(PARENT_AGENT, tracker); // busy: both lines are held
  for (const subject of ["a", "b"]) {
    await noteTurnEnded(
      { id: subject, parentAgentId: "parent", provider: "nonsteering" },
      [{ type: "assistant_message", text: `${subject}: ${"y".repeat(300)}` }],
      records,
      tracker,
      sender,
    );
  }
  assert.equal(tracker.held.length, 2);
  tracker.inTurn.delete("parent"); // free again: the batch is claimed
  await flushHeldLines(tracker, sender, records);
  assert.equal(sends.length, 1, "only the first batch goes out");
  assert.equal(sends.filter((entry) => entry.inTurnBeforeSend).length, 0, "no send into a running turn");
  assert.equal(tracker.held.length, 1, "the unsent tail stays owed");
  // The parent's turn ends: the retained line goes out once.
  await noteTurnEnded(
    { id: "parent", parentAgentId: null, provider: "nonsteering" },
    [{ type: "assistant_message", text: "manager finished" }],
    records,
    tracker,
    { sendMessage: async (id: string, text: string) => { sends.push({ inTurnBeforeSend: tracker.inTurn.has(id) }); void text; } },
  );
  assert.equal(sends.length, 2, "exactly one delivery after the turn ended");
  assert.equal(tracker.held.length, 0);
});

test("turn-end order survives an in-flight prior flush (L172-R4-F02, schedule one)", async () => {
  const tracker = createTurnEndedTracker();
  const sent: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let held = false;
  const gatedSender = {
    sendMessage: async (_id: string, text: string) => {
      if (!held) { held = true; await gate; } // the first send is in flight
      sent.push(text);
    },
  };
  const records = { a: CHILD, b: CHILD, parent: PARENT };
  const first = noteTurnEnded(
    { id: "a", parentAgentId: "parent" },
    [{ type: "assistant_message", text: "FIRST-TURN" }],
    records,
    tracker,
    gatedSender,
  );
  // A later turn-end arrives while the first flush is still in flight.
  const second = noteTurnEnded(
    { id: "b", parentAgentId: "parent" },
    [{ type: "assistant_message", text: "SECOND-TURN" }],
    records,
    tracker,
    gatedSender,
  );
  release();
  await Promise.all([first, second]);
  assert.equal(sent.length, 2);
  assert.match(sent[0] ?? "", /FIRST-TURN/);
  assert.match(sent[1] ?? "", /SECOND-TURN/);
});

test("turn-end order survives Manager rebinding (L172-R4-F02, schedule two)", async () => {
  const tracker = createTurnEndedTracker();
  const sent: string[] = [];
  const sender = { sendMessage: async (_id: string, text: string) => { sent.push(text); } };
  const records = { a: CHILD, b: CHILD, parent: PARENT, manager2: MANAGER2_RECORDS };
  noteTurnStarted({ id: "manager2", parentAgentId: null }, tracker);
  tracker.inTurn.delete("manager2");
  noteTurnStarted(PARENT_AGENT, tracker); // the parent is busy, so the line waits
  await noteTurnEnded(
    { id: "a", parentAgentId: "parent" },
    [{ type: "assistant_message", text: "FIRST-TURN" }],
    records,
    tracker,
    sender,
  );
  const seqBefore = tracker.held.map((line) => line.seq);
  noteAgentArchived("parent", tracker); // rebinds to manager2, keeping its place
  assert.deepEqual(tracker.held.map((line) => line.seq), seqBefore);
  await noteTurnEnded(
    { id: "b", parentAgentId: "manager2" },
    [{ type: "assistant_message", text: "SECOND-TURN" }],
    records,
    tracker,
    sender,
  );
  assert.equal(sent.length, 2);
  assert.match(sent[0] ?? "", /FIRST-TURN/);
  assert.match(sent[1] ?? "", /SECOND-TURN/);
});

test("a real failed send keeps the debt and is retried in order, once (L172-R4-F02 retry)", async () => {
  const tracker = createTurnEndedTracker();
  const attempts: string[] = [];
  const failing = {
    sendMessage: async (_id: string, text: string) => {
      attempts.push(text);
      throw new Error("host refused the send");
    },
  };
  const outcome = await noteTurnEnded(
    { id: "a", parentAgentId: "parent" },
    [{ type: "assistant_message", text: "FIRST-TURN" }],
    { a: CHILD, parent: PARENT },
    tracker,
    failing,
  );
  assert.equal(attempts.length, 1, "the send was really attempted");
  assert.equal(outcome.kind, "held", "a failed send leaves the line owed");
  assert.equal(tracker.held.length, 1);
  const retry = makeSender();
  const flushed = await flushHeldLines(tracker, retry);
  assert.equal(flushed.sent.length, 1);
  assert.equal(retry.sent.length, 1, "exactly one delivery on retry");
  assert.equal(tracker.held.length, 0);
});

test("provider history is reclaimed on archive and capped (L172-R4-F03)", () => {
  const tracker = createTurnEndedTracker();
  for (let index = 0; index < 100; index += 1) {
    noteTurnStarted({ id: `agent-${index}`, parentAgentId: null, provider: "nonsteering" }, tracker);
    noteAgentArchived(`agent-${index}`, tracker);
  }
  assert.equal(tracker.providerOf.size, 0, "an archived agent's provider entry is reclaimed");
  for (let index = 0; index < TURN_ENDED_PROVIDER_LIMIT + 50; index += 1) {
    noteTurnStarted({ id: `live-${index}`, parentAgentId: null, provider: "nonsteering" }, tracker);
  }
  assert.equal(tracker.providerOf.size, TURN_ENDED_PROVIDER_LIMIT, "the cap holds beyond the population");
});
