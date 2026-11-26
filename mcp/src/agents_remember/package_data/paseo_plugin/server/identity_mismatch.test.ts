/**
 * L51-R4: identity-mismatch turn ends deliver the mismatch line straight to
 * the parent with zero seat wakes and no 60-second silent-end wait.
 *
 * Run: `node --experimental-strip-types --test server/identity_mismatch.test.ts`
 * from the plugin directory.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {
  IDENTITY_MISMATCH_LOG_KIND,
  IDENTITY_MISMATCH_PREFIX,
  composeIdentityMismatchParentLine,
  extractIdentityMismatchLine,
} from "./identity_mismatch.ts";
import {
  SEAT_WAKE_FIRST_LINE,
  TURN_ENDED_LAST_TEXT_LIMIT,
  TURN_ENDED_LINE_LIMIT,
  TURN_ENDED_WAIT_MS,
  createTurnEndedTracker,
  noteAgentArchived,
  noteTurnEnded,
  noteTurnStarted,
  openDeclaredWait,
  type TurnEndedAgent,
  type TurnEndedFacts,
  type TurnEndedTracker,
} from "./turn_ended.ts";
import { endSeatTurn, fakeClock, hostState, makeSender } from "./turn_test_fakes.ts";

const NOW = "2026-11-25T19:00:00.000Z";

const MANAGER: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/task.json",
  reportPath: "/notes/reports/manager/mgr-1/role-launch/report.md",
};

const REPLACEMENT_MANAGER: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/task.json",
  reportPath: "/notes/reports/manager/mgr-2/role-launch/report.md",
};

const MISMATCH =
  "IDENTITY MISMATCH: expected requestId=req-51 role=worker task=agents-remember/master/51_leaf.json; got requestId=req-49 role=worker task=agents-remember/master/49_leaf.json";

type Row = { kind: string; reason: string; count: number; agent: string; role: string };

function seatFacts(role: "worker" | "reviewer" | "curator"): TurnEndedFacts {
  return {
    role,
    task: "agents-remember/master/51_leaf.json",
    reportPath: `/notes/reports/${role}/seat-1/role-launch/report.md`,
  };
}

function makeTracker(rows: Row[]): TurnEndedTracker {
  return createTurnEndedTracker(undefined, (row) => rows.push(row as Row));
}

function timelineWith(text: string): unknown[] {
  return [
    { type: "user_message", text: "Verify your binding before working." },
    { type: "assistant_message", text },
  ];
}

test("extractIdentityMismatchLine recognizes plain and Markdown-wrapped prefixes, preserving payload", () => {
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: MISMATCH }), MISMATCH);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: `   ${MISMATCH}   ` }), MISMATCH);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: `> ${MISMATCH}` }), MISMATCH);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: `- ${MISMATCH}` }), MISMATCH);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: `\`${MISMATCH}\`` }), MISMATCH);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: `> \`${MISMATCH}\`` }), MISMATCH);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: `**${MISMATCH}**` }), MISMATCH);

  const interiorTicks = "IDENTITY MISMATCH: expected `req-51`; got `req-49`";
  assert.equal(
    extractIdentityMismatchLine({ kind: "text", text: interiorTicks }),
    interiorTicks,
    "trailing backtick inside an unwrapped line stays intact",
  );

  const overLimit = `${IDENTITY_MISMATCH_PREFIX} ${"x".repeat(TURN_ENDED_LAST_TEXT_LIMIT + 50)}`;
  const extracted = extractIdentityMismatchLine({ kind: "text", text: overLimit });
  assert.ok(extracted !== null);
  assert.equal(extracted.length, TURN_ENDED_LAST_TEXT_LIMIT);
});

test("extractIdentityMismatchLine ignores non-matching, lowercase, empty, and unreadable text", () => {
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: "identity mismatch: wrong case" }), null);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: "note: IDENTITY MISMATCH: mid-line" }), null);
  assert.equal(extractIdentityMismatchLine({ kind: "text", text: "Finished my step." }), null);
  assert.equal(extractIdentityMismatchLine({ kind: "empty" }), null);
  assert.equal(extractIdentityMismatchLine({ kind: "unreadable" }), null);
});

for (const role of ["worker", "reviewer", "curator"] as const) {
  test(`${role} ending with IDENTITY MISMATCH: delivers immediately to idle parent with zero wakes`, async () => {
    const clock = fakeClock(NOW);
    const sender = makeSender();
    const rows: Row[] = [];
    const tracker = makeTracker(rows);
    const facts = seatFacts(role);
    const records: Record<string, TurnEndedFacts> = { seat: facts, parent: MANAGER };
    const seat: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "claude" };

    const outcome = await endSeatTurn(tracker, sender, clock, {
      agent: seat,
      records,
      timeline: timelineWith(MISMATCH),
    });

    assert.equal(outcome.kind, "sent");
    assert.equal(clock.pending().length, 0, "no 60s wait or wake timer armed");
    assert.equal(tracker.silentEnds.get("seat"), undefined, "silentEnds not incremented");
    assert.equal(sender.sent.length, 1, "exactly one message delivered to parent");
    assert.equal(sender.sent[0].agentId, "parent");
    assert.ok(!sender.sent.some((m) => m.agentId === "seat"), "zero wake messages sent to seat");
    assert.ok(!sender.sent[0].text.startsWith(SEAT_WAKE_FIRST_LINE), "not a seat wake message");
    assert.ok(sender.sent[0].text.includes(`${role} seat · ${facts.task}`), "names seat role, id, and task");
    assert.ok(sender.sent[0].text.includes(MISMATCH), "delivers IDENTITY MISMATCH line verbatim");
    assert.ok(!sender.sent[0].text.includes("marked: silent end"), "carries no silent-end mark");
    assert.ok(sender.sent[0].text.length <= TURN_ENDED_LINE_LIMIT);
    assert.deepEqual(
      rows.map((row) => [row.kind, row.count, row.reason]),
      [[IDENTITY_MISMATCH_LOG_KIND, 0, MISMATCH]],
    );
  });
}

test("wrapped IDENTITY MISMATCH: line strips outer quote/backticks cleanly and preserves silentEnds count", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const tracker = makeTracker(rows);
  tracker.silentEnds.set("seat", 1);
  const facts = seatFacts("worker");
  const records: Record<string, TurnEndedFacts> = { seat: facts, parent: MANAGER };
  const seat: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "codex" };

  const outcome = await endSeatTurn(tracker, sender, clock, {
    agent: seat,
    records,
    timeline: timelineWith(`Checking seat identity...\n> \`${MISMATCH}\``),
  });

  assert.equal(outcome.kind, "sent");
  assert.equal(clock.pending().length, 0);
  assert.equal(tracker.silentEnds.get("seat"), 1, "existing silentEnds count is not incremented");
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0].agentId, "parent");
  assert.equal(sender.sent[0].text, composeIdentityMismatchParentLine("seat", facts, MISMATCH));
  assert.ok(!sender.sent[0].text.includes("marked: silent end"));
});

test("IDENTITY MISMATCH: while parent is in a turn is held and flushed when parent turn ends, with zero seat wakes", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const tracker = makeTracker(rows);
  const facts = seatFacts("worker");
  const grandparent: TurnEndedFacts = {
    role: "orchestrator",
    task: "agents-remember/master/task.json",
    reportPath: "/notes/reports/orchestrator/orch-1/role-launch/report.md",
  };
  const records: Record<string, TurnEndedFacts> = {
    seat: facts,
    parent: MANAGER,
    grandparent,
  };
  const seat: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "claude" };
  const parent: TurnEndedAgent = { id: "parent", parentAgentId: "grandparent", provider: "claude" };

  noteTurnStarted(parent, tracker, clock.now);

  const mismatchOutcome = await endSeatTurn(tracker, sender, clock, {
    agent: seat,
    records,
    timeline: timelineWith(MISMATCH),
    readHost: async () => [hostState({ id: "seat" }), hostState({ id: "parent", inTurn: true })],
  });

  assert.equal(mismatchOutcome.kind, "held");
  assert.equal(clock.pending().length, 0, "no wake timer armed for mismatched seat");
  assert.equal(sender.sent.length, 0, "nothing sent while non-steering parent is in a turn");
  assert.equal(tracker.silentEnds.get("seat"), undefined);

  await noteTurnEnded(
    parent,
    [{ type: "assistant_message", text: "Parent finishing its own turn." }],
    records,
    tracker,
    sender,
    {
      outcome: "completed",
      now: clock.now,
      setTimeout: clock.setTimeout,
      clearTimeout: clock.clearTimeout,
      readHost: async () => [
        hostState({ id: "seat" }),
        hostState({ id: "parent", inTurn: false }),
        hostState({ id: "grandparent", inTurn: false }),
      ],
    },
  );

  const toParent = sender.sent.filter((message) => message.agentId === "parent");
  assert.equal(toParent.length, 1, "held mismatch line flushed when parent turn ended");
  assert.ok(toParent[0].text.includes(MISMATCH));
  assert.ok(!toParent[0].text.includes("marked: silent end"));
  assert.equal(
    sender.sent.filter((message) => message.agentId === "seat").length,
    0,
    "zero wake messages sent to mismatched seat",
  );
});

test("IDENTITY MISMATCH: falls back to current Manager of master when recorded parent is archived", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const tracker = makeTracker(rows);
  const facts = seatFacts("reviewer");
  const records: Record<string, TurnEndedFacts> = {
    seat: facts,
    parent: MANAGER,
    "manager-2": REPLACEMENT_MANAGER,
  };
  const seat: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "claude" };

  noteAgentArchived("parent", tracker, clock.now);

  const outcome = await endSeatTurn(tracker, sender, clock, {
    agent: seat,
    records,
    timeline: timelineWith(MISMATCH),
    readHost: async () => [
      hostState({ id: "seat" }),
      hostState({ id: "parent", archived: true }),
      hostState({ id: "manager-2" }),
    ],
  });

  assert.equal(outcome.kind, "sent");
  assert.equal(clock.pending().length, 0);
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0].agentId, "manager-2");
  assert.ok(sender.sent[0].text.includes(MISMATCH));
});

test("IDENTITY MISMATCH: cancels any prior pending seat wake or open declared wait", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const tracker = makeTracker(rows);
  const facts = seatFacts("worker");
  const records: Record<string, TurnEndedFacts> = { seat: facts, parent: MANAGER };
  const seat: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "claude" };

  const declared = await endSeatTurn(tracker, sender, clock, {
    agent: seat,
    records,
    timeline: timelineWith("WAITING FOR SUBAGENTS: helper still running"),
  });
  assert.equal(declared.kind, "declared");
  assert.ok(openDeclaredWait(tracker, "seat") !== null);
  assert.equal(clock.pending().length, 1);

  const mismatch = await endSeatTurn(tracker, sender, clock, {
    agent: seat,
    records,
    timeline: [
      { type: "user_message", text: "Verify your binding before working." },
      { type: "assistant_message", text: "WAITING FOR SUBAGENTS: helper still running" },
      { type: "user_message", text: "Continue." },
      { type: "assistant_message", text: MISMATCH },
    ],
  });

  assert.equal(mismatch.kind, "sent");
  assert.equal(openDeclaredWait(tracker, "seat"), null, "open declared wait cleared");
  assert.equal(clock.pending().length, 0, "prior check timer cleared");
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0].agentId, "parent");
  assert.ok(sender.sent[0].text.includes(MISMATCH));
});

test("a normal silent leaf turn without IDENTITY MISMATCH: still waits 60s and wakes the seat", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const tracker = makeTracker(rows);
  const facts = seatFacts("worker");
  const records: Record<string, TurnEndedFacts> = { seat: facts, parent: MANAGER };
  const seat: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "claude" };

  const outcome = await endSeatTurn(tracker, sender, clock, {
    agent: seat,
    records,
    timeline: timelineWith("Pausing here without handing off."),
  });

  assert.deepEqual(outcome, { kind: "waiting", seat: "seat", ordinal: 1 });
  assert.equal(sender.sent.length, 0, "nothing sent before 60s wait elapses");
  assert.equal(clock.pending().length, 1);
  assert.equal(clock.pending()[0].delayMs, TURN_ENDED_WAIT_MS);

  await clock.fire();
  assert.equal(tracker.silentEnds.get("seat"), 1);
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0].agentId, "seat");
  assert.ok(sender.sent[0].text.startsWith(SEAT_WAKE_FIRST_LINE));
});
