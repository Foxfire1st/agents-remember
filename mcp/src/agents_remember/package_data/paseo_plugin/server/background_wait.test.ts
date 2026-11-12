/**
 * The background wait: the declaration on any seat, its check time, both waits
 * in one end, the wait's two endings, and the silent end left as it was.
 *
 * Run: `node --experimental-strip-types --test server/background_wait.test.ts`
 * from the plugin directory. Every case drives the pure decision with a fake
 * clock and a fake seat, so no host is needed.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { HostActivityPublisher, type HostActivityPublication } from "./host_activity.ts";
import {
  BACKGROUND_WAIT_FIRST_LINE,
  BACKGROUND_WAIT_KEYWORD,
  BACKGROUND_WAKE_SENTENCE,
  BOTH_WAITS_FIRST_LINE,
  HELPER_WAKE_SENTENCE,
  readBackgroundDeclaration,
  type PublishedDeclaredWait,
} from "./background_wait.ts";
import {
  ACP_SEAT_WAKE_SENTENCE,
  SEAT_WAKE_FIRST_LINE,
  SUBAGENT_WAIT_CHECK_MS,
  SUBAGENT_WAIT_FIRST_LINE,
  SUBAGENT_WAIT_KEYWORD,
  TURN_ENDED_SILENT_LIMIT,
  createTurnEndedTracker,
  isPluginWakeMessage,
  noteAgentArchived,
  noteTurnStarted,
  openDeclaredWait,
  type TurnEndedAgent,
  type TurnEndedFacts,
  type TurnEndedSender,
  type TurnEndedTracker,
} from "./turn_ended.ts";
import { endSeatTurn, fakeClock, hostState, makeSender, type FakeClock, type SeatEnd, type SentMessage } from "./turn_test_fakes.ts";

const NOW = "2026-11-11T09:00:00.000Z";

const WORKER: TurnEndedFacts = {
  role: "worker",
  task: "agents-remember/master/60_leaf.json",
  reportPath: "/notes/reports/worker/aaa/role-launch/report.md",
};
const MANAGER: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/task.json",
  reportPath: "/notes/reports/manager/ddd/role-launch/report.md",
};
const RECORDS: Record<string, TurnEndedFacts> = { seat: WORKER, parent: MANAGER };

const ACP_SEAT: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "hermes" };
const CLAUDE_SEAT: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: "claude" };

type Row = { kind: string; reason: string; count: number };

/** Hermes is on ACP, Claude is not, as the host's acp-providers.json says. */
function tracker(
  rows: Row[],
  declaredWait?: (agentId: string, wait: PublishedDeclaredWait | null) => void,
): TurnEndedTracker {
  return createTurnEndedTracker(undefined, (row) => rows.push(row as Row), {
    acpProvider: (provider: string) => provider === "hermes",
    ...(declaredWait === undefined ? {} : { declaredWait }),
  });
}

function runSeat(
  agent: TurnEndedAgent,
  seatTracker: TurnEndedTracker,
  sender: TurnEndedSender,
  clock: FakeClock,
  timeline: readonly unknown[],
  readHost?: SeatEnd["readHost"],
): ReturnType<typeof endSeatTurn> {
  return endSeatTurn(seatTracker, sender, clock, { agent, records: RECORDS, timeline, readHost });
}

const PROCESS = "pytest lanes, pid 4242, log /var/tmp/run/lanes.log, ends about 09:20";

function endWith(text: string, opening = "go on"): unknown[] {
  return [
    { type: "user_message", text: opening },
    { type: "assistant_message", text },
  ];
}

function backgroundChecks(sent: SentMessage[]): SentMessage[] {
  return sent.filter((message) => message.text.startsWith(BACKGROUND_WAIT_FIRST_LINE));
}

function seatWakes(sent: SentMessage[]): SentMessage[] {
  return sent.filter((message) => message.text.startsWith(SEAT_WAKE_FIRST_LINE));
}

function kinds(rows: Row[]): string[] {
  return rows.map((row) => row.kind);
}

for (const [label, agent] of [
  ["an ACP seat", ACP_SEAT],
  ["a seat that is not ACP", CLAUDE_SEAT],
] as const) {
  test(`${label} declares a background wait: not silent, nothing to the parent, checked at three minutes`, async () => {
    const clock = fakeClock(NOW);
    const sender = makeSender();
    const rows: Row[] = [];
    const seatTracker = tracker(rows);

    const outcome = await runSeat(agent, seatTracker, sender, clock, endWith(`Started the lanes.\n${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`));
    assert.equal(outcome.kind, "declared");
    assert.equal(seatTracker.silentEnds.get("seat"), undefined, "the declared end adds no silent end");
    assert.equal(sender.sent.length, 0, "nothing goes out when the turn ends");
    assert.equal(clock.pending().length, 1, "one check is armed");
    assert.equal(clock.pending()[0].delayMs, 180_000, "three minutes by default");
    const declared = rows.find((row) => row.kind === "background-wait-declared");
    assert.ok(declared !== undefined);
    assert.equal(declared.reason, "declared a background wait; check in 3 minutes (default)");
    assert.equal(declared.count, 1);
    assert.equal(rows.some((row) => row.kind.startsWith("subagent-wait")), false, "no helper wait is declared");

    await clock.fire();
    assert.deepEqual(sender.sent.map((message) => message.agentId), ["seat"], "only the seat hears from the plugin");
    const checks = backgroundChecks(sender.sent);
    assert.equal(checks.length, 1);
    assert.equal(
      checks[0].text,
      `${BACKGROUND_WAIT_FIRST_LINE} Worker seat · ${WORKER.task}\n` +
        `you declared: ${PROCESS}\n` +
        "Report each background process's state here: what it is, whether it still runs by its process id or log file, and what it left. " +
        `Collect what finished. If a process still runs, end your turn with a new "${BACKGROUND_WAIT_KEYWORD}" line; otherwise go on with your stage.`,
    );
    assert.deepEqual(kinds(rows), ["background-wait-declared", "background-wait-check-sent"]);
    assert.equal(rows[1].reason, "asked for each process's state");
    assert.equal(seatWakes(sender.sent).length, 0, "never the no-hand-off message");
  });
}

test("a named check time is used, and a value outside 1 to 30 is clamped and the row says so", async () => {
  const cases: [string, number, string][] = [
    ["check in 1 minutes", 60_000, "declared a background wait; check in 1 minutes (named)"],
    ["check in 12 minutes", 720_000, "declared a background wait; check in 12 minutes (named)"],
    ["check in 30 minutes", 1_800_000, "declared a background wait; check in 30 minutes (named)"],
    ["check in 45 minutes", 1_800_000, "declared a background wait; check in 45 minutes is outside 1 to 30, clamped to 30"],
    ["check in 0 minutes", 60_000, "declared a background wait; check in 0 minutes is outside 1 to 30, clamped to 1"],
  ];
  for (const [phrase, delayMs, reason] of cases) {
    const clock = fakeClock(NOW);
    const sender = makeSender();
    const rows: Row[] = [];
    const seatTracker = tracker(rows);
    // The phrase may stand on the declaration line or below it.
    await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS}\n${phrase}`));
    assert.equal(clock.pending()[0]?.delayMs, delayMs, phrase);
    assert.equal(rows.find((row) => row.kind === "background-wait-declared")?.reason, reason, phrase);
    await clock.fire();
    assert.equal(backgroundChecks(sender.sent).length, 1, phrase);
  }
});

test("both waits in one ACP end: one check at three minutes asks about helpers and processes", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  const text = `${SUBAGENT_WAIT_KEYWORD} H1 reading the config (running)\n${BACKGROUND_WAIT_KEYWORD} ${PROCESS}, check in 10 minutes`;

  const outcome = await runSeat(ACP_SEAT, seatTracker, sender, clock, endWith(text));
  assert.equal(outcome.kind, "declared");
  assert.equal(clock.pending().length, 1, "one check for both waits");
  assert.equal(clock.pending()[0].delayMs, SUBAGENT_WAIT_CHECK_MS, "the earlier of the two times");
  assert.deepEqual(kinds(rows), ["subagent-wait-declared", "background-wait-declared"]);
  assert.equal(
    rows[1].reason,
    "declared a background wait; check in 10 minutes (named); the helper wait's check comes first, at 3 minutes",
  );

  await clock.fire();
  assert.equal(sender.sent.length, 1, "one message");
  const check = sender.sent[0].text;
  assert.ok(check.startsWith(`${BOTH_WAITS_FIRST_LINE} Worker seat · ${WORKER.task}`));
  assert.ok(check.includes("Report your helpers' progress here"));
  assert.ok(check.includes("Report each background process's state"));
  assert.ok(check.includes(`"${SUBAGENT_WAIT_KEYWORD}" line`));
  assert.ok(check.includes(`"${BACKGROUND_WAIT_KEYWORD}" line`));
  assert.deepEqual(kinds(rows).slice(2), ["subagent-wait-check-sent", "background-wait-check-sent"]);
  assert.equal(isPluginWakeMessage(check), true, "the check is the plugin's own message");
});

test("a seat that is not ACP may declare both waits too, and gets the one combined check", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  const text = `${SUBAGENT_WAIT_KEYWORD} H1 running\n${BACKGROUND_WAIT_KEYWORD} ${PROCESS}, check in 2 minutes`;
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(text));
  assert.deepEqual(kinds(rows), ["subagent-wait-declared", "background-wait-declared"]);
  assert.equal(clock.pending()[0].delayMs, 120_000, "the earlier of three and two minutes");
  await clock.fire();
  assert.equal(sender.sent.length, 1);
  assert.ok(sender.sent[0].text.startsWith(BOTH_WAITS_FIRST_LINE));
});

test("the seat's next end without a declaration ends the wait and is judged as a silent end", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`));
  await clock.fire();
  const check = backgroundChecks(sender.sent)[0];

  // The check opens a turn; the seat answers in its chat and ends without a declaration.
  noteTurnStarted(CLAUDE_SEAT, seatTracker, clock.now);
  const silent = await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith("The lanes finished: 812 passed.", check.text));
  assert.equal(silent.kind, "waiting");
  assert.equal(silent.kind === "waiting" ? silent.ordinal : 0, 1, "the check's turn neither reset nor advanced the run");
  assert.equal(seatTracker.declarations.has("seat"), false);
  const ended = rows.filter((row) => row.kind === "background-wait-ended");
  assert.equal(ended.length, 1);
  assert.equal(ended[0].reason, "a turn of the seat ended with no declaration");
  assert.equal(ended[0].count, 1);
});

test("an archived seat's background wait ends with it, and no check goes out", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  await runSeat(ACP_SEAT, seatTracker, sender, clock, endWith(`${SUBAGENT_WAIT_KEYWORD} H1\n${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`));
  noteAgentArchived("seat", seatTracker, clock.now);
  assert.equal(seatTracker.declarations.has("seat"), false);
  assert.deepEqual(
    rows.filter((row) => row.kind.endsWith("-wait-ended")).map((row) => [row.kind, row.reason]),
    [
      ["subagent-wait-ended", "the seat was archived"],
      ["background-wait-ended", "the seat was archived"],
    ],
  );
  await clock.fire();
  assert.equal(sender.sent.length, 0, "nothing is sent to an archived seat");
});

test("a declaring end on a seat the host reports archived ends the background wait and arms nothing", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`));
  // A new turn's rows: the same text again would be read as the earlier turn.
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${BACKGROUND_WAIT_KEYWORD} still running`, "and now?"), async () => [
    hostState({ id: "seat", archived: true }),
    hostState({ id: "parent" }),
  ]);
  assert.equal(clock.pending().length, 0, "the pending check is dropped with the wait");
  assert.deepEqual(
    rows.filter((row) => row.kind === "background-wait-ended").map((row) => row.reason),
    ["the seat was archived"],
  );
  assert.equal(rows.filter((row) => row.kind === "background-wait-declared").length, 1, "the archived end declares nothing");
});

test("an end that declares only one of two open waits ends the other", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  await runSeat(ACP_SEAT, seatTracker, sender, clock, endWith(`${SUBAGENT_WAIT_KEYWORD} H1\n${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`));
  await clock.fire();
  noteTurnStarted(ACP_SEAT, seatTracker, clock.now);
  const second = await runSeat(ACP_SEAT, seatTracker, sender, clock, endWith(`H1 done.\n${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`, sender.sent[0].text));
  assert.equal(second.kind, "declared");
  const ended = rows.filter((row) => row.kind.endsWith("-wait-ended"));
  assert.deepEqual(ended.map((row) => [row.kind, row.reason, row.count]), [
    ["subagent-wait-ended", "a turn of the seat ended without this declaration", 1],
  ]);
  assert.equal(seatTracker.declarations.get("seat")?.subagents, false);
  await clock.fire();
  assert.ok(sender.sent.at(-1)?.text.startsWith(BACKGROUND_WAIT_FIRST_LINE), "the next check asks about the process alone");
});

test("a background check due in a permission wait is re-armed for its own declared time", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS} check in 7 minutes`), async () => [
    hostState({ id: "seat", permissionWait: true }),
    hostState({ id: "parent" }),
  ]);
  // The end itself was a permission wait at the host, but the declaration is read first.
  assert.equal(clock.pending()[0].delayMs, 420_000);
  await clock.fire();
  assert.equal(sender.sent.length, 0);
  assert.equal(clock.pending()[0]?.delayMs, 420_000, "re-armed for seven minutes");
  assert.ok(rows.some((row) => row.kind === "background-wait-check-skipped" && row.reason === "permission-wait"));
});

test("an end with no declaration stays on the silent-end path, and its message names the background declaration", async () => {
  for (const [agent, acpSentence] of [
    [ACP_SEAT, true],
    [CLAUDE_SEAT, false],
  ] as const) {
    const clock = fakeClock(NOW);
    const sender = makeSender();
    const rows: Row[] = [];
    const seatTracker = tracker(rows);
    const outcome = await runSeat(agent, seatTracker, sender, clock, endWith("Started the lanes in the background."));
    assert.equal(outcome.kind, "waiting");
    assert.equal(clock.pending()[0].delayMs, 60_000, "the 60-second wait of a silent end");
    await clock.fire();
    const wakes = seatWakes(sender.sent);
    assert.equal(wakes.length, 1);
    assert.ok(wakes[0].text.endsWith(BACKGROUND_WAKE_SENTENCE), "the message ends with the background sentence");
    assert.equal(wakes[0].text.includes(ACP_SEAT_WAKE_SENTENCE), acpSentence, "the ACP sentence stays ACP-only");
    assert.equal(wakes[0].text.includes(HELPER_WAKE_SENTENCE), !acpSentence, "a seat not on ACP is told about the helper declaration");
    assert.ok(wakes[0].text.includes('"WAITING FOR SUBAGENTS:"'), "every seat is told how to declare a helper wait");
    assert.deepEqual(kinds(rows), ["seat-message"], "no wait row for an end that declared nothing");
  }
});

test("the keyword is read at the start of a line behind the markers, never in lower case or mid-line", () => {
  const read = (line: string) => readBackgroundDeclaration(`Some prose.\n${line}\nmore`);
  assert.equal(read(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`)?.named, PROCESS);
  assert.equal(read(`**${BACKGROUND_WAIT_KEYWORD}** ${PROCESS}`)?.named, PROCESS);
  assert.equal(read(`- \`${BACKGROUND_WAIT_KEYWORD}\` ${PROCESS}`)?.named, PROCESS);
  assert.equal(read(`> ${BACKGROUND_WAIT_KEYWORD}`)?.named, "");
  assert.equal(read(`${BACKGROUND_WAIT_KEYWORD.toLowerCase()} ${PROCESS}`), null);
  assert.equal(read(`I am ${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`), null);
  assert.equal(readBackgroundDeclaration(null), null);
  assert.equal(readBackgroundDeclaration(`${BACKGROUND_WAIT_KEYWORD} x`.padEnd(400, "y"))?.named.length, 300);
  // A check phrase above the declaration does not count.
  assert.equal(readBackgroundDeclaration(`check in 9 minutes\n${BACKGROUND_WAIT_KEYWORD} x`)?.askedMinutes, null);
  assert.equal(readBackgroundDeclaration(`${BACKGROUND_WAIT_KEYWORD} x, Check in 1 minute`)?.checkMinutes, 1);
});

test("the plugin recognises its background checks as its own, and a quoted one under a From line is not", () => {
  assert.equal(isPluginWakeMessage(`${BACKGROUND_WAIT_FIRST_LINE} Worker seat · task`), true);
  assert.equal(isPluginWakeMessage(`${BOTH_WAITS_FIRST_LINE} Worker seat · task`), true);
  assert.equal(isPluginWakeMessage(`${SUBAGENT_WAIT_FIRST_LINE} Worker seat · task`), true);
  assert.equal(
    isPluginWakeMessage(`From reviewer · agents-remember/master/60_leaf.json · agent 11111111\n${BACKGROUND_WAIT_FIRST_LINE}`),
    false,
  );
});

test("a Claude seat's helper declaration is a declared wait with the helper check", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seatTracker = tracker(rows);
  const outcome = await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${SUBAGENT_WAIT_KEYWORD} H1 scanning tests (running)`));
  assert.equal(outcome.kind, "declared");
  assert.deepEqual(kinds(rows), ["subagent-wait-declared"]);
  assert.equal(clock.pending()[0].delayMs, SUBAGENT_WAIT_CHECK_MS);
  await clock.fire();
  assert.ok(sender.sent[0].text.startsWith(SUBAGENT_WAIT_FIRST_LINE));
});

for (const keyword of [BACKGROUND_WAIT_KEYWORD, SUBAGENT_WAIT_KEYWORD]) {
  test(`a harness wake is the seat's own continuation, and its undeclared end ends the wait (${keyword})`, async () => {
    const clock = fakeClock(NOW);
    const sender = makeSender();
    const rows: Row[] = [];
    const seatTracker = tracker(rows);
    await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${keyword} ${PROCESS}`));
    // The harness itself opens the next turn when the tracked work ends, before the check.
    noteTurnStarted(CLAUDE_SEAT, seatTracker, clock.now);
    assert.equal(seatTracker.waits.has("seat"), false, "the unspent check is dropped at the turn's start");
    assert.equal(seatTracker.declarations.has("seat"), true, "the wait stays open through the woken turn");
    const woken = await runSeat(
      CLAUDE_SEAT,
      seatTracker,
      sender,
      clock,
      endWith("The run finished; continuing with the review.", "<task-notification>background command completed</task-notification>"),
    );
    assert.equal(seatTracker.declarations.has("seat"), false);
    const name = keyword === BACKGROUND_WAIT_KEYWORD ? "background" : "subagent";
    assert.deepEqual(
      rows.filter((row) => row.kind === `${name}-wait-ended`).map((row) => row.reason),
      ["a turn of the seat ended with no declaration"],
    );
    // The harness's wake is not the plugin's message: the woken end starts a fresh run.
    assert.equal(woken.kind, "waiting");
    assert.equal(woken.kind === "waiting" ? woken.ordinal : 0, 1);
    await clock.fire();
    assert.equal(sender.sent.filter((message) => !message.text.startsWith(SEAT_WAKE_FIRST_LINE)).length, 0, "no check was sent");
  });
}

test("the open wait is handed to the host-activity record as it opens, is checked and ends", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seen: [string, PublishedDeclaredWait | null][] = [];
  const seatTracker = tracker(rows, (agentId, wait) => seen.push([agentId, wait]));
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS}, check in 5 minutes`));
  const opened = {
    waitsFor: ["background"],
    since: NOW,
    checkDueAt: "2026-11-11T09:05:00.000Z",
    detail: `${PROCESS}, check in 5 minutes`,
  };
  assert.deepEqual(seen, [["seat", opened]]);
  assert.deepEqual(openDeclaredWait(seatTracker, "seat"), opened);

  // The check went out: the wait stays open, with no check due until the seat declares again.
  await clock.fire();
  const { checkDueAt: _due, ...afterCheck } = opened;
  assert.deepEqual(seen.at(-1), ["seat", afterCheck]);

  noteTurnStarted(CLAUDE_SEAT, seatTracker, clock.now);
  await runSeat(CLAUDE_SEAT, seatTracker, sender, clock, endWith("done", sender.sent[0].text));
  assert.deepEqual(seen.at(-1), ["seat", null], "the wait's end clears the record");
});

test("declarations evicted past the bound are announced as no longer waiting", async () => {
  const clock = fakeClock(NOW);
  const sender = makeSender();
  const rows: Row[] = [];
  const seen: [string, PublishedDeclaredWait | null][] = [];
  const seatTracker = tracker(rows, (agentId, wait) => seen.push([agentId, wait]));
  const seats = Array.from({ length: TURN_ENDED_SILENT_LIMIT + 2 }, (_, index) => `seat-${index}`);
  const records = Object.fromEntries([...seats.map((id) => [id, WORKER] as const), ["parent", MANAGER] as const]);
  for (const id of seats) {
    const agent: TurnEndedAgent = { id, parentAgentId: "parent", provider: "claude" };
    await endSeatTurn(seatTracker, sender, clock, { agent, records, timeline: endWith(`${BACKGROUND_WAIT_KEYWORD} ${PROCESS}`) });
  }
  assert.equal(seatTracker.declarations.size, TURN_ENDED_SILENT_LIMIT, "the store keeps its bound");
  for (const evicted of ["seat-0", "seat-1"]) {
    assert.equal(openDeclaredWait(seatTracker, evicted), null, `${evicted}'s wait was evicted`);
    assert.deepEqual(seen.filter(([agentId]) => agentId === evicted).at(-1), [evicted, null], `${evicted} is announced as no longer waiting`);
  }
  assert.notEqual(openDeclaredWait(seatTracker, "seat-2"), null, "the oldest kept seat still waits");
  assert.ok(!seen.some(([agentId, wait]) => agentId === "seat-2" && wait === null), "a kept seat is never announced as ended");
});

test("the host-activity record carries an open wait beside the host's idle state, and drops it when the seat ends", () => {
  const written: HostActivityPublication[] = [];
  const records = { seat: WORKER };
  const publisher = new HostActivityPublisher(() => records, (publication) => written.push(publication), () => NOW, "boot");
  publisher.note("agent.turn_ended", "seat", "2026-11-11T09:00:00.000Z");
  const wait: PublishedDeclaredWait = {
    waitsFor: ["background"],
    since: "2026-11-11T09:00:00.000Z",
    checkDueAt: "2026-11-11T09:03:00.000Z",
    detail: PROCESS,
  };
  publisher.noteDeclaredWait("seat", wait);
  assert.deepEqual(written.at(-1)?.agents.seat, { state: "idle", eventAt: "2026-11-11T09:00:00.000Z", declaredWait: wait });
  const count = written.length;
  publisher.noteDeclaredWait("seat", { ...wait });
  assert.equal(written.length, count, "an unchanged wait writes nothing");
  publisher.note("agent.archived", "seat");
  assert.equal(written.at(-1)?.agents.seat.declaredWait, undefined, "an ended agent shows no wait");
  publisher.noteDeclaredWait("seat", null);
  assert.equal(written.at(-1)?.agents.seat.declaredWait, undefined);
});
