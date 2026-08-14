import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CLAUDE_FRESH_SESSION_SNAPSHOT,
  CLAUDE_MODEL_ROWS,
  codexLiveSessionSnapshot,
  effortOption,
  modelRow,
  PI_MODEL_ROWS,
  SESSION_CAPABILITY_ERROR_BODIES,
} from "../test/fixtures/capabilityEnvelopes";
import type { CapabilitySnapshotWire } from "../types/harnessCapabilities";
import {
  classifySessionCapabilitiesResponse,
  cycleEffortTarget,
  deriveEffortMenu,
  effectiveSelection,
  fetchSessionCapabilities,
  visibleModelRows,
} from "./sessionCapabilities";

// Exact-session sourcing + the corrected effort-menu derivation (260715-FEUI-L4 R1 — the leaf's
// header note): the menu comes from the selected row's effortOptions (filtered sessionSettable)
// plus the nullable top-level selectedEffort — NEVER from configOptions presence.

afterEach(() => vi.unstubAllGlobals());

const snapshot = (overrides: Partial<CapabilitySnapshotWire> = {}): CapabilitySnapshotWire => ({
  models: CLAUDE_MODEL_ROWS,
  selectedModelKey: "claude-fable-5[1m]",
  selectedEffort: "high",
  configOptions: [],
  ...overrides,
});

describe("deriveEffortMenu (the header-note derivation)", () => {
  it("FRESH CLAUDE (the case that forced the correction): null selectedEffort + an omitted " +
    "thought_level configOption still yields the FULL menu with selected=null ('effort not echoed')", () => {
    // CLAUDE_FRESH_SESSION_SNAPSHOT has configOptions WITHOUT a thought_level entry — the
    // serializer omits it exactly when selectedEffort is null. The menu must not care.
    expect(
      CLAUDE_FRESH_SESSION_SNAPSHOT.configOptions.some((option) => option.category === "thought_level"),
    ).toBe(false);
    const menu = deriveEffortMenu(CLAUDE_FRESH_SESSION_SNAPSHOT);
    expect(menu).toMatchObject({ kind: "menu", selected: null });
    if (menu.kind !== "menu") throw new Error("expected a menu");
    expect(menu.options.map((option) => option.key)).toEqual(["low", "medium", "high", "xhigh", "max"]);
  });

  it("a model row WITHOUT effortOptions → 'no effort control for this model' — never inherited", () => {
    const menu = deriveEffortMenu(snapshot({ selectedModelKey: "haiku", selectedEffort: null }));
    expect(menu).toEqual({ kind: "no-effort-control", modelKey: "haiku" });
  });

  it("no selected model (or a key absent from the catalog) → no-selected-model", () => {
    expect(deriveEffortMenu(snapshot({ selectedModelKey: null }))).toEqual({ kind: "no-selected-model" });
    expect(deriveEffortMenu(snapshot({ selectedModelKey: "ghost-model" }))).toEqual({
      kind: "no-selected-model",
    });
  });

  it("filters sessionSettable ONLY, preserving advertised native order (never reordered)", () => {
    const rows = [
      modelRow("m1", {
        effortOptions: [
          effortOption("zeta"),
          effortOption("launch-only", { sessionSettable: false }),
          effortOption("alpha"),
        ],
        supportsEffort: true,
      }),
    ];
    const menu = deriveEffortMenu(snapshot({ models: rows, selectedModelKey: "m1", selectedEffort: null }));
    if (menu.kind !== "menu") throw new Error("expected a menu");
    // 'zeta' before 'alpha': advertised order survives; the launch-only token is gone.
    expect(menu.options.map((option) => option.key)).toEqual(["zeta", "alpha"]);
  });

  it("a row whose options are ALL launch-only renders as no-effort-control for the session", () => {
    const rows = [
      modelRow("m1", {
        effortOptions: [effortOption("launch-only", { sessionSettable: false })],
        supportsEffort: true,
      }),
    ];
    const menu = deriveEffortMenu(snapshot({ models: rows, selectedModelKey: "m1" }));
    expect(menu).toEqual({ kind: "no-effort-control", modelKey: "m1" });
  });

  it("Pi provider-qualified keys stay verbatim in the menu's model key", () => {
    const pi = snapshot({
      models: PI_MODEL_ROWS,
      selectedModelKey: "deepseek/deepseek-v4-pro",
      selectedEffort: "off",
    });
    const menu = deriveEffortMenu(pi);
    expect(menu).toMatchObject({ kind: "menu", modelKey: "deepseek/deepseek-v4-pro", selected: "off" });
  });

  it("re-gating on a STAGED row: the menu derives from that row, and its 'selected' marking is " +
    "null (the staged row has no echoed selection); defaultEffort pre-highlights", () => {
    const codex = codexLiveSessionSnapshot("gpt-5.6-sol", "low");
    const staged = deriveEffortMenu(codex, "gpt-5.6-terra");
    expect(staged).toMatchObject({
      kind: "menu",
      modelKey: "gpt-5.6-terra",
      selected: null,
      defaultEffort: "medium",
    });
    // The snapshot's own row keeps its echoed selection.
    expect(deriveEffortMenu(codex)).toMatchObject({ kind: "menu", selected: "low" });
  });
});

describe("visibleModelRows (the server's own visibility rule)", () => {
  it("hidden rows are excluded UNLESS currently selected", () => {
    const codex = codexLiveSessionSnapshot("gpt-5.6-sol", "low");
    expect(visibleModelRows(codex).map((row) => row.key)).not.toContain("codex-auto-review");
    const onHidden = codexLiveSessionSnapshot("codex-auto-review", "medium");
    expect(visibleModelRows(onHidden).map((row) => row.key)).toContain("codex-auto-review");
  });
});

describe("cycleEffortTarget (R7)", () => {
  const options = [effortOption("low"), effortOption("medium"), effortOption("high")];
  it.each([
    ["low", 1, "medium"],
    ["medium", 1, "high"],
    ["high", 1, "low"], // wraps
    ["low", -1, "high"], // wraps backward
    [null, 1, "low"], // not echoed: starts at the first advertised option
    [null, -1, "high"], // ...or the last, going down
    ["stale-key", 1, "low"], // a value no longer advertised restarts honestly
  ] as const)("from %s direction %s → %s", (current, direction, expected) => {
    expect(cycleEffortTarget(options, current, direction)).toBe(expected);
  });

  it("an empty menu cycles nowhere", () => {
    expect(cycleEffortTarget([], "low", 1)).toBeNull();
  });
});

describe("classifySessionCapabilitiesResponse (R1/R3/F16)", () => {
  it("200 with a valid bare snapshot → snapshot", () => {
    const outcome = classifySessionCapabilitiesResponse(200, CLAUDE_FRESH_SESSION_SNAPSHOT);
    expect(outcome.kind).toBe("snapshot");
  });

  it("200 with a malformed body is a TRANSPORT fact, never adopted", () => {
    expect(classifySessionCapabilitiesResponse(200, { models: "nope" }).kind).toBe("transport");
  });

  it.each([
    [404, SESSION_CAPABILITY_ERROR_BODIES.sessionGone.body, "session-gone"],
    [409, SESSION_CAPABILITY_ERROR_BODIES.noNativeControl.body, "no-native-control"],
    [503, SESSION_CAPABILITY_ERROR_BODIES.outage.body, "outage"],
  ] as const)("HTTP %s → %s with the verbatim detail", (status, body, kind) => {
    const outcome = classifySessionCapabilitiesResponse(status, body);
    expect(outcome.kind).toBe(kind);
    if ("detail" in outcome && "detail" in body) expect(outcome.detail).toBe(body.detail);
  });

  it("null status (fetch threw) → transport", () => {
    expect(classifySessionCapabilitiesResponse(null, undefined).kind).toBe("transport");
  });
});

describe("fetchSessionCapabilities", () => {
  it("GETs the EXACT-SESSION route — never the pre-session harness cache (R1)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      json: async () => CLAUDE_FRESH_SESSION_SNAPSHOT,
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const outcome = await fetchSessionCapabilities("seat-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/terminal/seat-1/capabilities");
    expect(fetchMock.mock.calls[0][0]).not.toContain("/api/harnesses/");
    expect(outcome.kind).toBe("snapshot");
  });

  it("a thrown fetch lands as transport with the error's own words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("socket hang up")));
    const outcome = await fetchSessionCapabilities("seat-1");
    expect(outcome).toEqual({ kind: "transport", detail: "socket hang up" });
  });
});

describe("effectiveSelection (the one marker derivation)", () => {
  const base = {
    liveSnapshot: {
      sessionId: "s",
      fetchedAt: 1000,
      payload: codexLiveSessionSnapshot("gpt-5.6-sol", "low"),
    },
    echoEvidence: {},
  };

  it("no cockpit / no snapshot / no echo → none", () => {
    expect(effectiveSelection(undefined)).toEqual({
      modelKey: null,
      effort: null,
      modelSource: "none",
      effortSource: "none",
    });
    expect(effectiveSelection({ liveSnapshot: undefined, echoEvidence: {} })).toMatchObject({
      modelSource: "none",
      effortSource: "none",
    });
  });

  it("snapshot alone is the marker", () => {
    expect(effectiveSelection(base)).toEqual({
      modelKey: "gpt-5.6-sol",
      effort: "low",
      modelSource: "snapshot",
      effortSource: "snapshot",
    });
  });

  it("a NEWER echo-verified value overlays its field only; an OLDER echo loses to the snapshot", () => {
    const newer = effectiveSelection({
      ...base,
      echoEvidence: { effort: { value: "xhigh", at: 2000 } },
    });
    expect(newer).toMatchObject({ modelKey: "gpt-5.6-sol", effort: "xhigh", effortSource: "echo" });
    const older = effectiveSelection({
      ...base,
      echoEvidence: { effort: { value: "xhigh", at: 500 } },
    });
    expect(older).toMatchObject({ effort: "low", effortSource: "snapshot" });
  });

  it("echo evidence without any snapshot still testifies (the pre-first-readback set)", () => {
    expect(
      effectiveSelection({ liveSnapshot: undefined, echoEvidence: { model: { value: "m2", at: 10 } } }),
    ).toMatchObject({ modelKey: "m2", modelSource: "echo", effort: null, effortSource: "none" });
  });
});
