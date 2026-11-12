/** F06: drive the contributed handler, not a replacement host-state reader. */
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";
import type { PluginLifecycleRegistration } from "@getpaseo/plugin/server";
import { registerParentWakeEvents } from "./turn_events.ts";
import { turnEndLogPath } from "./turn_log.ts";

const facts = { role: "worker", task: "agents-remember/master/01_leaf.json", reportPath: "/worker.md" };
const parent = { ...facts, role: "manager", reportPath: "/parent.md" };
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i += 1) await new Promise<void>((resolve) => setImmediate(resolve));
};

test("F06: incomplete pagination never admits a seat wake; a complete listing does", async (t) => {
  for (const mode of ["missing", "repeated", "capped", "complete"] as const) {
    await t.test(mode, async (t) => {
      t.mock.timers.enable({ apis: ["setTimeout"] });
      const home = mkdtempSync(join(tmpdir(), "ar-f06-"));
      const previous = process.env.PASEO_HOME;
      process.env.PASEO_HOME = home;
      mkdirSync(join(home, "agents-remember"));
      writeFileSync(join(home, "agents-remember", "role-agents.json"), JSON.stringify({ agents: { seat: facts, parent } }));
      const handlers = new Map<string, (event: unknown, context: unknown) => void>();
      const server = { addHook(name: string, handler: (event: unknown, context: unknown) => void) {
        handlers.set(name, handler);
        return () => {};
      } } as unknown as Pick<PluginLifecycleRegistration, "addHook">;
      const sent: { id: string; text: string }[] = [];
      let reads = 0;
      const context = { paseo: { agents: {
        ref: (id: string) => ({ async send(text: string) { sent.push({ id, text }); } }),
        list: async () => {
          reads += 1;
          return {
            // Include both records: suppression must be caused by incompleteness,
            // not by the absent-seat guard or a missing parent record.
            entries: ["seat", "parent"].map((id) => ({ agent: { id, labels: {}, activeTurn: null, pendingPermissions: [] } })),
            pageInfo: { hasMore: mode !== "complete", nextCursor: mode === "missing" ? null : mode === "repeated" ? "same" : `page-${reads}` },
          };
        },
      } } };
      const cleanup = registerParentWakeEvents(server, { home });
      try {
        handlers.get("agent.turn.after")!({ agent: { id: "seat", parentAgentId: "parent" }, timeline: [], outcome: { kind: "completed" }, turnId: null }, context);
        await settle();
        const readsAtEnd = reads;
        const seatBefore = sent.filter((message) => message.id === "seat").length;
        assert.equal(seatBefore, 0, "nothing wakes the seat before the wait");
        t.mock.timers.tick(60_000);
        await settle();
        const rows = readFileSync(turnEndLogPath(home), "utf8").trim().split("\n").map((line) => JSON.parse(line) as { kind: string; reason: string; count: number });
        if (mode === "complete") {
          assert.equal(readsAtEnd, 1);
          assert.equal(reads, 2, "one complete read at end, one at delivery");
          assert.equal(sent.filter((message) => message.id === "seat").length, 1);
          assert.deepEqual(rows.map((row) => [row.kind, row.count]), [["seat-message", 1]]);
        } else {
          assert.equal(readsAtEnd, mode === "missing" ? 1 : mode === "repeated" ? 2 : 8);
          assert.equal(reads, readsAtEnd, "no wait or delivery read was admitted");
          assert.equal(sent.filter((message) => message.id === "seat").length, 0);
          assert.deepEqual(rows.map((row) => [row.kind, row.reason, row.count]), [["left-alone", "host-state-unavailable", 0]]);
          assert.equal(sent.filter((message) => message.id === "parent").length, 1, "MIK-R172's ordinary parent line is preserved");
        }
      } finally {
        cleanup();
        if (previous === undefined) delete process.env.PASEO_HOME;
        else process.env.PASEO_HOME = previous;
        rmSync(home, { recursive: true, force: true });
      }
    });
  }
});

/**
 * L184-R1-F13: drive the contributed route, not the pure decision.
 *
 * A role seat's departure is observed by the reads the plugin already makes — the
 * role record at each end, and the record read again at a deferred action's own
 * moment — so a deferred resume left behind by a departed seat is reclaimed with
 * its timer instead of firing at a seat that is no longer in the record.
 */
test("L184-R1-F13: a seat that left the role record never receives its deferred resume", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const home = mkdtempSync(join(tmpdir(), "ar-f13-"));
  const previous = process.env.PASEO_HOME;
  process.env.PASEO_HOME = home;
  mkdirSync(join(home, "agents-remember"));
  const until = new Date(Date.now() + 5 * 60_000).toISOString();
  const writeRoles = (agents: Record<string, typeof facts>): void => {
    writeFileSync(join(home, "agents-remember", "role-agents.json"), JSON.stringify({ agents }));
  };
  writeRoles({ seat: facts, parent });
  writeFileSync(
    join(home, "agents-remember", "provider-limits.json"),
    JSON.stringify({
      reports: { r1: { until, sourceLabel: "s", accountLabel: "a", windows: [], observedAt: until } },
      agents: { seat: ["r1"] },
    }),
  );
  const handlers = new Map<string, (event: unknown, context: unknown) => void>();
  const server = { addHook(name: string, handler: (event: unknown, context: unknown) => void) {
    handlers.set(name, handler);
    return () => {};
  } } as unknown as Pick<PluginLifecycleRegistration, "addHook">;
  const sent: { id: string; text: string }[] = [];
  const context = { paseo: { agents: {
    ref: (id: string) => ({ async send(text: string) { sent.push({ id, text }); } }),
    list: async () => ({
      entries: ["seat", "parent"].map((id) => ({ agent: { id, labels: {}, activeTurn: null, pendingPermissions: [] } })),
      pageInfo: { hasMore: false, nextCursor: null },
    }),
  } } };
  const cleanup = registerParentWakeEvents(server, { home });
  try {
    // The limited seat's silent end: one wait, and the resume armed at its reset.
    handlers.get("agent.turn.after")!(
      { agent: { id: "seat", parentAgentId: "parent" }, timeline: [], outcome: { kind: "completed" }, turnId: null },
      context,
    );
    await settle();
    t.mock.timers.tick(60_000);
    await settle();
    assert.equal(sent.length, 0, "a limited seat is sent nothing before its reset");

    // The product removed the seat: the seat's own end rereads the whole record.
    writeRoles({ parent });
    handlers.get("agent.turn.after")!(
      { agent: { id: "seat", parentAgentId: "parent" }, timeline: [], outcome: { kind: "completed" }, turnId: null },
      context,
    );
    await settle();

    // The reset passes: the resume must not fire at a seat that is no longer a seat.
    t.mock.timers.tick(6 * 60_000);
    await settle();
    assert.deepEqual(
      sent.filter((message) => message.id === "seat"),
      [],
      "the resume never fires at a seat that left the record",
    );

    const rows = readFileSync(turnEndLogPath(home), "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { kind: string; agent: string; reason: string });
    assert.ok(
      rows.some(
        (row) =>
          row.kind === "wait-cancelled" &&
          row.agent === "seat" &&
          row.reason === "the seat left the role record before the resume",
      ),
      "the end that observed the departure records the reclaimed resume",
    );
  } finally {
    cleanup();
    if (previous === undefined) delete process.env.PASEO_HOME;
    else process.env.PASEO_HOME = previous;
    rmSync(home, { recursive: true, force: true });
  }
});

/** A hand-over to the parent, as the host's mapper writes the row. */
const HAND_OVER = (() => {
  const answer = { ok: true, status: "accepted", taken: "started", recipientAgentId: "parent", log: "/x.json" };
  return {
    type: "tool_call",
    callId: "exec-role_message",
    name: "agents-remember-task.role_message",
    status: "completed",
    error: null,
    detail: {
      type: "unknown",
      input: { recipient: "parent" },
      output: { content: [{ type: "text", text: JSON.stringify(answer) }], structuredContent: answer },
    },
  };
})();

/** The 0.15.4 refetch reply: projected entries, each row's item beside its turn id. */
function refetchReply(agentId: string, rows: ReadonlyArray<{ readonly item: unknown; readonly turnId: string }>) {
  return {
    agentId,
    direction: "tail",
    projection: "projected",
    hasOlder: false,
    hasNewer: false,
    entries: rows.map(({ item, turnId }, index) => ({
      provider: "codex",
      item,
      timestamp: "2026-11-11T08:00:00.000Z",
      seqStart: index + 1,
      seqEnd: index + 1,
      turnId,
    })),
    error: null,
  };
}

/**
 * One silent-capable end of turn `turn-1` through the contributed handler, the
 * seat's timeline served by `refetch`, and the seat's wait run out: what was sent
 * and the rows written.
 */
async function endTurnThroughRefetch(
  t: TestContext,
  refetch: (agentId: string, options: unknown) => Promise<unknown>,
): Promise<{ sent: { id: string; text: string }[]; kinds: string[] }> {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const home = mkdtempSync(join(tmpdir(), "ar-refetch-"));
  const previous = process.env.PASEO_HOME;
  process.env.PASEO_HOME = home;
  mkdirSync(join(home, "agents-remember"));
  writeFileSync(join(home, "agents-remember", "role-agents.json"), JSON.stringify({ agents: { seat: facts, parent } }));
  const handlers = new Map<string, (event: unknown, context: unknown) => Promise<void>>();
  const server = { addHook(name: string, handler: (event: unknown, context: unknown) => Promise<void>) {
    handlers.set(name, handler);
    return () => {};
  } } as unknown as Pick<PluginLifecycleRegistration, "addHook">;
  const sent: { id: string; text: string }[] = [];
  const context = { paseo: { agents: {
    ref: (id: string) => ({
      async send(text: string) { sent.push({ id, text }); },
      timeline: { refetch: (options: unknown) => refetch(id, options) },
    }),
    list: async () => ({
      entries: ["seat", "parent"].map((id) => ({ agent: { id, labels: {}, activeTurn: null, pendingPermissions: [] } })),
      pageInfo: { hasMore: false, nextCursor: null },
    }),
  } } };
  const cleanup = registerParentWakeEvents(server, { home });
  try {
    await handlers.get("agent.turn.after")!(
      {
        agent: { id: "seat", workspaceId: null, parentAgentId: "parent", provider: "codex", cwd: home, title: null },
        turnId: "turn-1",
        outcome: { kind: "completed" },
        timeline: [],
      },
      context,
    );
    await settle();
    t.mock.timers.tick(60_000);
    await settle();
    const kinds = readFileSync(turnEndLogPath(home), "utf8")
      .trim()
      .split("\n")
      .filter((line) => line !== "")
      .map((line) => (JSON.parse(line) as { kind: string }).kind);
    return { sent, kinds };
  } finally {
    cleanup();
    if (previous === undefined) delete process.env.PASEO_HOME;
    else process.env.PASEO_HOME = previous;
    rmSync(home, { recursive: true, force: true });
  }
}

/**
 * From Paseo 0.13 the `agent.turn.after` hook carries an empty
 * `timeline` (0.15.4 `plugins/lifecycle/index.js` publishes `timeline: []`). The
 * contributed handler reads the ended turn through `agents.ref(id).timeline.refetch`,
 * so a hand-over in the turn is still recognised and a silent end still waits.
 */
test("Paseo 0.15.4 hook shape: the turn read through refetch recognises a hand-over", async (t) => {
  for (const handedOver of [true, false]) {
    await t.test(handedOver ? "hand-over" : "silent end", async (t) => {
      const items = [
        { type: "user_message", text: "build the leaf", messageId: "m1" },
        ...(handedOver ? [HAND_OVER] : []),
        { type: "assistant_message", text: "done" },
      ];
      const refetches: unknown[] = [];
      const { sent, kinds } = await endTurnThroughRefetch(t, async (id, options) => {
        refetches.push([id, options]);
        return refetchReply(id, items.map((item) => ({ item, turnId: "turn-1" })));
      });
      assert.deepEqual(refetches, [["seat", { direction: "tail", limit: 0 }]], "the whole held timeline is read once");
      if (handedOver) {
        assert.deepEqual(sent, [], "a hand-over wakes neither the seat nor its parent");
        assert.ok(!kinds.includes("seat-message"), "no seat message after a hand-over");
      } else {
        assert.equal(sent.filter((message) => message.id === "seat").length, 1, "the silent end sends the seat back once");
        assert.deepEqual(kinds, ["seat-message"]);
      }
    });
  }
});

/**
 * The reply keeps each row's turn id beside the row. The rows the decision reads
 * carry it, so an earlier turn's hand-over in the held timeline does not answer
 * for the ended turn, which holds no user message of its own to cut at.
 */
test("the rows read through refetch keep their turn id, so an earlier turn's hand-over does not count", async (t) => {
  const { sent, kinds } = await endTurnThroughRefetch(t, async (id) =>
    refetchReply(id, [
      { item: { type: "user_message", text: "build the leaf", messageId: "m1" }, turnId: "turn-0" },
      { item: HAND_OVER, turnId: "turn-0" },
      { item: { type: "assistant_message", text: "handed over" }, turnId: "turn-0" },
      { item: { type: "assistant_message", text: "still on the tests" }, turnId: "turn-1" },
    ]),
  );
  const seat = sent.filter((message) => message.id === "seat");
  assert.equal(seat.length, 1, "the ended turn alone is judged, and it holds no hand-over");
  assert.match(seat[0].text, /you last said: still on the tests/);
  assert.deepEqual(kinds, ["seat-message"]);
});

/** A read that fails is an unreadable turn, never a turn that had no text. */
test("a refetch that throws gives an unreadable last line, not an empty one", async (t) => {
  const { sent, kinds } = await endTurnThroughRefetch(t, async () => {
    throw new Error("the daemon dropped the request");
  });
  const seat = sent.filter((message) => message.id === "seat");
  assert.equal(seat.length, 1, "the silent end is still sent back once");
  assert.match(seat[0].text, /you last said: unreadable/);
  assert.doesNotMatch(seat[0].text, /you last said: no text/);
  assert.deepEqual(kinds, ["seat-message"]);
});
