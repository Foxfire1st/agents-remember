/** F06: drive the contributed handler, not a replacement host-state reader. */
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { PluginLifecycleRegistration } from "@getpaseo/plugin/server";
import { registerParentWakeEvents } from "./turn_events.ts";

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
      const server = { on(name: string, handler: (event: unknown, context: unknown) => void) {
        handlers.set(name, handler);
        return () => {};
      } } as unknown as Pick<PluginLifecycleRegistration, "on">;
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
        handlers.get("agent.turn_ended")!({ agent: { id: "seat", parentAgentId: "parent" }, timeline: [], outcome: { kind: "completed" }, turnId: null }, context);
        await settle();
        const readsAtEnd = reads;
        const seatBefore = sent.filter((message) => message.id === "seat").length;
        assert.equal(seatBefore, 0, "nothing wakes the seat before the wait");
        t.mock.timers.tick(60_000);
        await settle();
        const rows = readFileSync(join(home, "agents-remember", "turn-ends.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line) as { kind: string; reason: string; count: number });
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
  const server = { on(name: string, handler: (event: unknown, context: unknown) => void) {
    handlers.set(name, handler);
    return () => {};
  } } as unknown as Pick<PluginLifecycleRegistration, "on">;
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
    handlers.get("agent.turn_ended")!(
      { agent: { id: "seat", parentAgentId: "parent" }, timeline: [], outcome: { kind: "completed" }, turnId: null },
      context,
    );
    await settle();
    t.mock.timers.tick(60_000);
    await settle();
    assert.equal(sent.length, 0, "a limited seat is sent nothing before its reset");

    // The product removed the seat: the seat's own end rereads the whole record.
    writeRoles({ parent });
    handlers.get("agent.turn_ended")!(
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

    const rows = readFileSync(join(home, "agents-remember", "turn-ends.jsonl"), "utf8")
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
