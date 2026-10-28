/**
 * MIK-R216: the declaration, the three-minute check and the ACP seat's message.
 *
 * Run: `node --experimental-strip-types --test server/subagent_wait.test.ts`
 * from the plugin directory. Every case drives the pure decision with a fake
 * clock and a fake seat, so no host is needed.
 */
import assert from "node:assert/strict";
import test from "node:test";
import {
  ACP_SEAT_WAKE_SENTENCE,
  SEAT_WAKE_FIRST_LINE,
  SUBAGENT_WAIT_CHECK_MS,
  SUBAGENT_WAIT_FIRST_LINE,
  SUBAGENT_WAIT_KEYWORD,
  TURN_ENDED_DEFERRED_LIMIT,
  composeSubagentWaitCheck,
  createTurnEndedTracker,
  declaresSubagentWait,
  isPluginWakeMessage,
  noteAgentArchived,
  noteTurnEnded,
  noteTurnStarted,
  type HostAgentState,
  type TurnEndedAgent,
  type TurnEndedFacts,
  type TurnEndedSender,
  type TurnEndedTracker,
} from "./turn_ended.ts";

const NOW = "2026-10-28T09:00:00.000Z";

const WORKER: TurnEndedFacts = {
  role: "worker",
  task: "agents-remember/master/203_leaf.json",
  reportPath: "/notes/reports/worker/aaa/role-launch/report.md",
};
const REVIEWER: TurnEndedFacts = {
  role: "reviewer",
  task: "agents-remember/master/203_leaf.json",
  reportPath: "/notes/reports/reviewer/bbb/role-launch/report.md",
};
const INVESTIGATOR: TurnEndedFacts = {
  role: "investigator",
  task: "agents-remember/master/203_leaf.json",
  reportPath: "/notes/reports/investigator/ccc/role-launch/report.md",
};
const MANAGER: TurnEndedFacts = {
  role: "manager",
  task: "agents-remember/master/task.json",
  reportPath: "/notes/reports/manager/ddd/role-launch/report.md",
};

const ACP = "hermes";
const CODEX = "codex";

const RECORDS: Record<string, TurnEndedFacts> = {
  seat: WORKER,
  reviewer: REVIEWER,
  investigator: INVESTIGATOR,
  parent: MANAGER,
};
const AGENT: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: ACP };

interface SentMessage {
  agentId: string;
  text: string;
}

function makeSender(): TurnEndedSender & { sent: SentMessage[] } {
  const sent: SentMessage[] = [];
  return {
    sent,
    sendMessage(agentId: string, text: string): Promise<void> {
      sent.push({ agentId, text });
      return Promise.resolve();
    },
  };
}

interface FakeTimer {
  callback: () => void;
  delayMs: number;
  cleared: boolean;
}

/** A clock and timer set the decision uses, so a test can end a wait by hand. */
function fakeClock() {
  let current = new Date(NOW);
  const timers: FakeTimer[] = [];
  return {
    timers,
    now: () => current,
    setTimeout: (callback: () => void, delayMs: number): unknown => {
      const timer: FakeTimer = { callback, delayMs, cleared: false };
      timers.push(timer);
      return timer;
    },
    clearTimeout: (handle: unknown): void => {
      (handle as FakeTimer).cleared = true;
    },
    async fire(): Promise<void> {
      for (const timer of timers.splice(0)) if (!timer.cleared) timer.callback();
      await new Promise<void>((resolve) => setImmediate(resolve));
    },
    pending(): FakeTimer[] {
      return timers.filter((timer) => !timer.cleared);
    },
  };
}

type Clock = ReturnType<typeof fakeClock>;

/** A tracker whose providers are ACP only where a case says so, and which keeps its rows. */
function acpTracker(
  rows: { kind: string; reason: string; count: number }[] = [],
  options: {
    readonly acp?: readonly string[];
    readonly limitedUntil?: (agentId: string, now: Date) => string | null;
  } = {},
): TurnEndedTracker {
  const acp = new Set(options.acp ?? [ACP]);
  return createTurnEndedTracker(
    undefined,
    (row) => rows.push(row as { kind: string; reason: string; count: number }),
    {
      acpProvider: (provider: string) => acp.has(provider),
      ...(options.limitedUntil === undefined ? {} : { limitedUntil: options.limitedUntil }),
    },
  );
}

/** The host's state for the seat, as a send reads it. */
function hostState(overrides: Partial<HostAgentState> & { readonly id: string }): HostAgentState {
  return { parentAgentId: null, inTurn: false, permissionWait: false, archived: false, ...overrides };
}

function runSeat(
  tracker: TurnEndedTracker,
  sender: TurnEndedSender,
  clock: Clock,
  timeline: readonly unknown[] = [],
  extra: {
    readonly outcome?: string;
    readonly agent?: TurnEndedAgent;
    readonly records?: Record<string, TurnEndedFacts>;
    readonly readHost?: () => Promise<readonly HostAgentState[] | null>;
  } = {},
): ReturnType<typeof noteTurnEnded> {
  return noteTurnEnded(extra.agent ?? AGENT, timeline, extra.records ?? RECORDS, tracker, sender, {
    outcome: extra.outcome ?? "completed",
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    readHost: extra.readHost ?? (async () => [hostState({ id: "seat" }), hostState({ id: "parent" })]),
  });
}

/** The declaration as a seat writes it, with its progress report below the line. */
function declaredTimeline(report = "H1 reading rule 3 (running), H2 scanning tests (done)"): unknown[] {
  return [
    { type: "user_message", text: "go on" },
    { type: "assistant_message", text: `${SUBAGENT_WAIT_KEYWORD} ${report}` },
  ];
}

function checkMessages(sent: SentMessage[]): SentMessage[] {
  return sent.filter((message) => message.text.startsWith(SUBAGENT_WAIT_FIRST_LINE));
}

function seatWakes(sent: SentMessage[]): SentMessage[] {
  return sent.filter((message) => message.text.startsWith(SEAT_WAKE_FIRST_LINE));
}

test("a declared ACP seat that stays idle is asked for progress at three minutes and not before", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();

  const outcome = await runSeat(tracker, sender, clock, declaredTimeline());
  assert.equal(outcome.kind, "declared");
  assert.equal(sender.sent.length, 0, "nothing goes out when the turn ends");
  assert.equal(clock.pending().length, 1, "one check is armed");
  assert.equal(clock.pending()[0].delayMs, SUBAGENT_WAIT_CHECK_MS, "three minutes");
  assert.equal(SUBAGENT_WAIT_CHECK_MS, 180_000);
  assert.equal(seatWakes(sender.sent).length, 0, "no no-hand-off message");

  await clock.fire();
  const checks = checkMessages(sender.sent);
  assert.equal(checks.length, 1, "the check went out");
  assert.equal(checks[0].agentId, "seat");
  assert.equal(
    checks[0].text,
    composeSubagentWaitCheck("seat", WORKER),
    "the check names the role, the id and the task",
  );
  assert.ok(checks[0].text.startsWith(`${SUBAGENT_WAIT_FIRST_LINE} Worker seat · ${WORKER.task}`));
  assert.ok(checks[0].text.includes("Report your helpers' progress here"));
  assert.ok(checks[0].text.includes(`"${SUBAGENT_WAIT_KEYWORD}" line`));
});

test("a seat that answers every check and declares again is asked again, never sent the no-hand-off message", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  await runSeat(tracker, sender, clock, declaredTimeline());
  await clock.fire();
  const check = checkMessages(sender.sent)[0];

  const answer = [
    { type: "user_message", text: check.text },
    { type: "assistant_message", text: `H1 done (no finding)\n${SUBAGENT_WAIT_KEYWORD} H2 running: 2 of 4 tools measured` },
  ];
  noteTurnStarted(AGENT, tracker, clock.now);
  const second = await runSeat(tracker, sender, clock, answer);
  assert.equal(second.kind, "declared");
  assert.equal(clock.pending().length, 1, "a new check is armed after the answer");
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 2);
  assert.equal(seatWakes(sender.sent).length, 0, "a declared end never gets the no-hand-off message");
});

test("a check that falls due in a running turn waits for the turn, and a declaring end arms a fresh one", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const rows: { kind: string; reason: string; count: number }[] = [];
  const tracker = acpTracker(rows);
  await runSeat(tracker, sender, clock, declaredTimeline());
  assert.equal(clock.pending().length, 1);

  // A turn opens before the check falls due: the pending check is dropped unspent.
  noteTurnStarted(AGENT, tracker, clock.now);
  assert.equal(tracker.waits.has("seat"), false, "the check is no longer owed");
  assert.ok(
    rows.some((row) => row.kind === "subagent-wait-check-skipped" && row.reason === "a turn of the seat started"),
    "the skip is written",
  );
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 0, "nothing goes into the running turn");

  const outcome = await runSeat(tracker, sender, clock, declaredTimeline("H1 still running"));
  assert.equal(outcome.kind, "declared", "the turn that declared is a declared wait");
  assert.equal(tracker.waits.has("seat"), true);
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 1, "three minutes after that end");
});

test("a check due while the host still shows the seat in a turn is skipped and not sent", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  await runSeat(tracker, sender, clock, declaredTimeline());
  // The host's listing says in-turn at the check's own moment, without a turn event.
  await runSeat(tracker, sender, clock, declaredTimeline(), {
    readHost: async () => [hostState({ id: "seat", inTurn: true })],
  });
  const pending = clock.pending().at(-1);
  assert.ok(pending !== undefined);
  pending.delayMs = 0;
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 0, "a check never enters a running turn");
});

test("a limited declared seat gets the check's text with the reset sentence, not the no-hand-off message", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const until = "2026-10-28T09:30:00.000Z";
  let limited = true;
  const tracker = acpTracker([], { limitedUntil: () => (limited ? until : null) });
  await runSeat(tracker, sender, clock, declaredTimeline());
  await clock.fire();
  assert.equal(sender.sent.length, 0, "a limited seat gets nothing at the check's moment");
  assert.equal(seatWakes(sender.sent).length, 0, "and never the no-hand-off message");

  // The resume is armed at the reset plus the lag; the limit is read again at its own moment.
  const resume = clock.pending().at(-1);
  assert.ok(resume !== undefined, "one resume is armed");
  limited = false;
  resume.delayMs = 0;
  await clock.fire();
  assert.equal(sender.sent.length, 1);
  assert.ok(sender.sent[0].text.startsWith(SUBAGENT_WAIT_FIRST_LINE), "the resume is built from the check");
  assert.ok(sender.sent[0].text.includes(`Your provider's usage limit reset at ${until}`));
  assert.ok(!sender.sent[0].text.startsWith(SEAT_WAKE_FIRST_LINE), "never the no-hand-off message");
});

test("a check due in a permission wait, or with an unreadable host state, is re-armed for three minutes", async () => {
  for (const readHost of [
    async () => [hostState({ id: "seat", permissionWait: true })],
    async () => null,
  ]) {
    const clock = fakeClock();
    const sender = makeSender();
    const rows: { kind: string; reason: string; count: number }[] = [];
    const tracker = acpTracker(rows);
    await runSeat(tracker, sender, clock, declaredTimeline(), { readHost });
    const pending = clock.pending().at(-1);
    assert.ok(pending !== undefined);
    pending.delayMs = 0;
    await clock.fire();
    assert.equal(checkMessages(sender.sent).length, 0, "nothing went out");
    const rearmed = clock.pending();
    assert.equal(rearmed.length, 1, "the check is armed again");
    assert.equal(rearmed[0].delayMs, SUBAGENT_WAIT_CHECK_MS);
    assert.ok(rows.some((row) => row.kind === "subagent-wait-check-skipped"), "the skip is written");
  }
});

test("a turn opened by the check does not reset the run, and a declared end neither counts nor resets it", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const rows: { kind: string; reason: string; count: number }[] = [];
  const tracker = acpTracker(rows);
  await runSeat(tracker, sender, clock, declaredTimeline());
  await clock.fire();
  const check = checkMessages(sender.sent)[0];
  noteTurnStarted(AGENT, tracker, clock.now);
  await runSeat(tracker, sender, clock, [
    { type: "user_message", text: check.text },
    { type: "assistant_message", text: `H1 still running\n${SUBAGENT_WAIT_KEYWORD} H1 running` },
  ]);
  assert.equal(tracker.silentEnds.get("seat"), undefined, "the declared end counts nothing");
  await clock.fire();

  // The seat answers without a declaration: a silent end after the usual wait.
  noteTurnStarted(AGENT, tracker, clock.now);
  const answer = checkMessages(sender.sent).at(-1);
  assert.ok(answer !== undefined);
  const silent = await runSeat(tracker, sender, clock, [
    { type: "user_message", text: answer.text },
    { type: "assistant_message", text: "H1 done, H2 done" },
  ]);
  assert.equal(silent.kind, "waiting");
  assert.equal(silent.kind === "waiting" ? silent.ordinal : 0, 1, "the run was not reset or advanced by the wait");
  assert.ok(rows.some((row) => row.kind === "subagent-wait-ended"), "the wait's end is written");
  await clock.fire();
  assert.equal(seatWakes(sender.sent).length, 1);
});

test("a declared end without a hand-off gives the parent nothing", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  await runSeat(tracker, sender, clock, declaredTimeline());
  await clock.fire();
  assert.deepEqual(
    sender.sent.map((message) => message.agentId),
    ["seat"],
    "only the seat hears from the plugin",
  );
  assert.equal(sender.sent.some((message) => message.agentId === "parent"), false);
});

test("a turn that writes a step and declares is still a wait, and a sibling hand-off reaches the parent", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const rows: { kind: string; reason: string; count: number }[] = [];
  const tracker = acpTracker(rows);
  const timeline = [
    { type: "user_message", text: "go on" },
    {
      type: "tool_call",
      name: "task_doc",
      status: "completed",
      detail: { ok: true },
      metadata: { kind: "other", title: "mcp__agents_remember_task__task_doc" },
    },
    { type: "assistant_message", text: `${SUBAGENT_WAIT_KEYWORD} H1 running` },
  ];
  const outcome = await runSeat(tracker, sender, clock, timeline);
  assert.equal(outcome.kind, "declared");
  assert.ok(rows.some((row) => row.kind === "subagent-wait-declared"));

  const sibling = [
    ...timeline,
    { type: "assistant_message", text: `${SUBAGENT_WAIT_KEYWORD} H1 running` },
    {
      type: "tool_call",
      name: "role_message",
      status: "completed",
      detail: { ok: true, status: "accepted", recipientAgentId: "reviewer" },
      metadata: { kind: "other", title: "mcp__agents_remember_task__role_message" },
    },
  ];
  const second = await runSeat(tracker, sender, clock, sibling);
  assert.equal(second.kind, "declared");
  assert.equal(sender.sent.at(-1)?.agentId, "parent", "the sibling hand-off keeps MIK-R172's line");
});

test("a declared Worker that answers a check with no signal gets the ACP seat message, and it names the declaration", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  await runSeat(tracker, sender, clock, declaredTimeline());
  await clock.fire();
  const check = checkMessages(sender.sent)[0];
  noteTurnStarted(AGENT, tracker, clock.now);
  await runSeat(tracker, sender, clock, [
    { type: "user_message", text: check.text },
    { type: "assistant_message", text: "H1 still running" },
  ]);
  await clock.fire();
  const wakes = seatWakes(sender.sent);
  assert.equal(wakes.length, 1);
  assert.ok(wakes[0].text.includes(ACP_SEAT_WAKE_SENTENCE), "the ACP sentence replaces the helper sentence");
  assert.ok(wakes[0].text.includes("On this host a helper's result does not come back") === false);
});

test("an ACP Worker that called delegate_task and ended with no signal gets the ACP message and no check", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  const timeline = [
    { type: "user_message", text: "go on" },
    { type: "tool_call", name: "delegate_task", status: "running", detail: {}, metadata: { kind: "other", title: "delegate_task: two helpers" } },
    { type: "assistant_message", text: "Helpers dispatched" },
  ];
  await runSeat(tracker, sender, clock, timeline);
  assert.equal(clock.pending().length, 1, "only the 60 s wait is armed");
  assert.equal(clock.pending()[0].delayMs, 60_000);
  await clock.fire();
  const wakes = seatWakes(sender.sent);
  assert.equal(wakes.length, 1);
  assert.ok(wakes[0].text.includes(ACP_SEAT_WAKE_SENTENCE));
  assert.equal(checkMessages(sender.sent).length, 0, "no check without a declaration");
});

test("an Investigator's declaration gets checks, and its silent end keeps today's parent line", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  const investigator: TurnEndedAgent = { id: "investigator", parentAgentId: "parent", provider: ACP };
  const readHost = async (): Promise<readonly HostAgentState[]> => [
    hostState({ id: "investigator" }),
    hostState({ id: "parent" }),
  ];
  const outcome = await runSeat(tracker, sender, clock, declaredTimeline(), { agent: investigator, readHost });
  assert.equal(outcome.kind, "declared");
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 1);

  const silent = await runSeat(
    tracker,
    sender,
    clock,
    [
      ...declaredTimeline(),
      { type: "assistant_message", text: "nothing found" },
    ],
    { agent: investigator, readHost },
  );
  assert.equal(silent.kind, "sent", "an Investigator is not sent back to its own work");
  assert.equal(seatWakes(sender.sent).length, 0);
});

test("the keyword is read at the start of any line behind the markers, and never in lower case or mid-line", () => {
  const withReport = (line: string): unknown[] => [
    { type: "assistant_message", text: `Some prose first.\n${line}\nH1 running\nH2 done` },
  ];
  assert.equal(declaresSubagentWait(withReport(`${SUBAGENT_WAIT_KEYWORD} H1 running`)), true);
  assert.equal(declaresSubagentWait(withReport(`   ${SUBAGENT_WAIT_KEYWORD} H1 running`)), true);
  assert.equal(declaresSubagentWait(withReport(`- ${SUBAGENT_WAIT_KEYWORD} H1 running`)), true);
  assert.equal(declaresSubagentWait(withReport(`**${SUBAGENT_WAIT_KEYWORD}** H1 running`)), true);
  assert.equal(declaresSubagentWait(withReport(`\`${SUBAGENT_WAIT_KEYWORD}\` H1 running`)), true);
  assert.equal(declaresSubagentWait(withReport(`"${SUBAGENT_WAIT_KEYWORD}" H1 running`)), true);
  assert.equal(declaresSubagentWait(withReport(`> ${SUBAGENT_WAIT_KEYWORD}`)), true);
  assert.equal(declaresSubagentWait(withReport("# WAITING FOR SUBAGENTS:")), true);
  assert.equal(declaresSubagentWait(withReport(`${SUBAGENT_WAIT_KEYWORD}`)), true);
  assert.equal(declaresSubagentWait(withReport(`${SUBAGENT_WAIT_KEYWORD.toLowerCase()} H1`)), false);
  assert.equal(declaresSubagentWait(withReport(`I am waiting: ${SUBAGENT_WAIT_KEYWORD} H1`)), false);
  assert.equal(declaresSubagentWait(withReport("waiting for subagents: H1")), false);
  assert.equal(declaresSubagentWait([{ type: "assistant_message", text: "H1 running" }]), false);
  // The report may follow on the same line or below it; the last assistant message decides.
  assert.equal(
    declaresSubagentWait([
      { type: "assistant_message", text: `${SUBAGENT_WAIT_KEYWORD} H1 running` },
      { type: "assistant_message", text: "H1 finished, H2 done" },
    ]),
    false,
    "only the last assistant message is read",
  );
});

test("a non-ACP seat's keyword gets nothing from this packet and today's message text", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const tracker = acpTracker();
  const codex: TurnEndedAgent = { id: "seat", parentAgentId: "parent", provider: CODEX };
  const outcome = await runSeat(tracker, sender, clock, declaredTimeline(), { agent: codex });
  assert.equal(outcome.kind, "waiting", "the end is judged as a silent end");
  assert.equal(clock.pending()[0].delayMs, 60_000);
  await clock.fire();
  const wakes = seatWakes(sender.sent);
  assert.equal(wakes.length, 1);
  assert.ok(wakes[0].text.includes("Go on with your stage in this turn, or send what blocks you to your parent with role_message."));
  assert.ok(!wakes[0].text.includes(ACP_SEAT_WAKE_SENTENCE), "today's text is unchanged");
  assert.equal(checkMessages(sender.sent).length, 0, "no check for a seat off ACP");
});

test("the plugin recognises its own check as its own message, and an external message is not", () => {
  assert.equal(isPluginWakeMessage(`${SUBAGENT_WAIT_FIRST_LINE} Worker seat · task`), true);
  assert.equal(isPluginWakeMessage(`${SEAT_WAKE_FIRST_LINE} worker seat · task`), true);
  assert.equal(
    isPluginWakeMessage(`From reviewer · agents-remember/master/203_leaf.json · agent 11111111\n${SUBAGENT_WAIT_FIRST_LINE}`),
    false,
  );
  assert.equal(isPluginWakeMessage("Please report your helpers."), false);
});

test("an archived seat's declared wait ends with it", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const rows: { kind: string; reason: string; count: number }[] = [];
  const tracker = acpTracker(rows);
  await runSeat(tracker, sender, clock, declaredTimeline());
  noteAgentArchived("seat", tracker, clock.now);
  assert.equal(tracker.declarations.has("seat"), false);
  assert.ok(rows.some((row) => row.kind === "subagent-wait-ended" && row.reason === "the seat was archived"));
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 0, "nothing is sent to an archived seat");
});

test("a limited check shed by the deferred fleet writes one rule-7 row and arms nothing", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const rows: { kind: string; reason: string; count: number }[] = [];
  const until = "2026-10-28T09:30:00.000Z";
  const tracker = acpTracker(rows, { limitedUntil: () => until });
  // The deferred fleet is full, so the one deferred action this seat's wait would take
  // is shed. One undelivered check is one decision: one row, of rule 7's kind.
  for (let index = 0; index < TURN_ENDED_DEFERRED_LIMIT; index += 1) {
    tracker.resumes.set(`other-${index}`, {} as never);
  }
  await runSeat(tracker, sender, clock, declaredTimeline());
  await clock.fire();
  assert.equal(sender.sent.length, 0, "nothing went out");
  assert.equal(clock.pending().length, 0, "no resume is armed for a shed check");
  const skipped = rows.filter((row) => row.kind === "subagent-wait-check-skipped");
  assert.equal(skipped.length, 1, "one undelivered check writes exactly one row of that kind");
  assert.equal(skipped[0]?.reason, "over-capacity");
  assert.equal(rows.filter((row) => row.kind === "send-skipped").length, 0, "no legacy kind for a check");
});

test("a declaring end on a seat the host reports archived ends the earlier wait and arms nothing", async () => {
  const clock = fakeClock();
  const sender = makeSender();
  const rows: { kind: string; reason: string; count: number }[] = [];
  const tracker = acpTracker(rows);
  await runSeat(tracker, sender, clock, declaredTimeline());
  assert.equal(tracker.declarations.has("seat"), true, "the first end declared a wait");
  assert.equal(clock.pending().length, 1, "and armed its check");

  await runSeat(tracker, sender, clock, declaredTimeline("H1 still running"), {
    readHost: async () => [hostState({ id: "seat", archived: true }), hostState({ id: "parent" })],
  });
  assert.equal(tracker.declarations.has("seat"), false, "no wait outlives its seat");
  assert.equal(clock.pending().length, 0, "the pending check is dropped with the wait");
  const ended = rows.filter((row) => row.kind === "subagent-wait-ended");
  assert.equal(ended.length, 1);
  assert.equal(ended[0]?.reason, "the seat was archived");
  assert.equal(rows.filter((row) => row.kind === "subagent-wait-declared").length, 1, "the archived end declares nothing");
  await clock.fire();
  assert.equal(checkMessages(sender.sent).length, 0, "nothing is sent to an archived seat");
});

