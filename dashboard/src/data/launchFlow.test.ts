import { afterEach, describe, expect, it, vi } from "vitest";

import {
  capabilityEnvelope,
  CLAUDE_FRESH_SESSION_SNAPSHOT,
  effortOption,
  modelRow,
  preSessionSnapshot,
} from "../test/fixtures/capabilityEnvelopes";
import {
  BAD_KIND,
  INVALID_NON_NATIVE,
  INVALID_PARTIAL_PAIR,
  LAUNCH_CONFLICT,
  LEAF_TAKEN,
  OPENED_STARTING,
  OPENED_VENDOR_DEFAULTS,
} from "../test/fixtures/openResponses";
import {
  chooseEffort,
  chooseModel,
  chooseVendorDefaults,
  classifyOpenResponse,
  EMPTY_SELECTION,
  launchableEfforts,
  launchSelectionBody,
  openHostedSession,
  selectionComplete,
} from "./launchFlow";

afterEach(() => vi.unstubAllGlobals());

// ── Complete-pair rules (R4) ───────────────────────────────────────────────────────────────────

describe("selection reducers — complete pair or vendor defaults, never partial", () => {
  const claude = capabilityEnvelope("claude", "hit").capabilities;
  const codex = capabilityEnvelope("codex", "hit").capabilities;
  const pi = capabilityEnvelope("pi", "hit").capabilities;

  it("selecting a model with an advertised launch default re-gates effort to THAT default", () => {
    const sol = chooseModel(codex, "gpt-5.6-sol");
    expect(sol).toEqual({ modelKey: "gpt-5.6-sol", effort: "low", vendorDefaults: false });
    // switching models re-gates to the NEW row's advertised default — never carried over
    const spark = chooseModel(codex, "gpt-5.3-codex-spark");
    expect(spark.effort).toBe("high");
  });

  it("a model with NO advertised default demands an explicit choice (effort null)", () => {
    // Claude rows advertise no defaultEffort in the recorded catalog
    const fable = chooseModel(claude, "claude-fable-5[1m]");
    expect(fable.modelKey).toBe("claude-fable-5[1m]");
    expect(fable.effort).toBeNull();
    expect(selectionComplete(fable)).toBe(false);
  });

  it("a defaultEffort that is NOT launch-settable is not silently selected", () => {
    const snapshot = preSessionSnapshot([
      modelRow("m1", {
        effortOptions: [
          effortOption("a", { launchSettable: false }),
          effortOption("b"),
        ],
        supportsEffort: true,
        defaultEffort: "a",
      }),
    ]);
    expect(chooseModel(snapshot, "m1").effort).toBeNull(); // demands an explicit choice
  });

  it("an unadvertised model cannot be selected (dynamic-only)", () => {
    expect(chooseModel(claude, "ar-unknown-model")).toEqual(EMPTY_SELECTION);
  });

  it("chooseEffort accepts only the selected model's launch-settable menu", () => {
    const selected = chooseModel(codex, "gpt-5.6-sol");
    expect(chooseEffort(codex, selected, "ultra").effort).toBe("ultra");
    expect(chooseEffort(codex, selected, "turbo").effort).toBe("low"); // unadvertised → unchanged
  });

  it("launchableEfforts filters launchSettable WITHOUT reordering (advertised native order)", () => {
    const model = modelRow("m", {
      effortOptions: [
        effortOption("z"),
        effortOption("mid", { launchSettable: false }),
        effortOption("a"),
        effortOption("q"),
      ],
      supportsEffort: true,
    });
    expect(launchableEfforts(model).map((option) => option.key)).toEqual(["z", "a", "q"]);
  });

  it("the observed Haiku row (no effortOptions) can never form a complete pair", () => {
    const haiku = chooseModel(claude, "haiku");
    expect(haiku.effort).toBeNull();
    expect(selectionComplete(haiku)).toBe(false);
    const row = claude.models.find((model) => model.key === "haiku");
    expect(launchableEfforts(row!)).toEqual([]);
  });

  it("Pi provider-qualified keys are used VERBATIM — the provider is never stripped", () => {
    const selected = chooseModel(pi, "deepseek/deepseek-v4-flash");
    expect(selected.modelKey).toBe("deepseek/deepseek-v4-flash");
    expect(chooseModel(pi, "deepseek-v4-flash")).toEqual(EMPTY_SELECTION); // bare id ≠ a row
  });

  it("launchSelectionBody emits BOTH knobs or NEITHER — a partial pair is unrepresentable", () => {
    expect(
      launchSelectionBody({ modelKey: "sonnet", effort: "high", vendorDefaults: false }),
    ).toEqual({ model: "sonnet", effort: "high" });
    expect(launchSelectionBody(chooseVendorDefaults())).toEqual({});
    expect(() =>
      launchSelectionBody({ modelKey: "sonnet", effort: null, vendorDefaults: false }),
    ).toThrow(/incomplete/);
    expect(() =>
      launchSelectionBody({ modelKey: null, effort: "high", vendorDefaults: false }),
    ).toThrow(/incomplete/);
  });

  it("the fresh-Claude exact-session fixture keeps selectedEffort null (no launch-effort echo)", () => {
    expect(CLAUDE_FRESH_SESSION_SNAPSHOT.selectedEffort).toBeNull();
    expect(CLAUDE_FRESH_SESSION_SNAPSHOT.selectedModelKey).toBe("claude-fable-5[1m]");
    // only the model config category exists until a set is echo-verified
    expect(CLAUDE_FRESH_SESSION_SNAPSHOT.configOptions.map((option) => option.category)).toEqual([
      "model",
    ]);
  });
});

// ── Response classification (R5/R8) — every fixture path ──────────────────────────────────────

describe("classifyOpenResponse", () => {
  it("200 + session → opened, carrying the REQUESTED pair (starting) verbatim", () => {
    const outcome = classifyOpenResponse(200, OPENED_STARTING);
    expect(outcome).toMatchObject({
      path: "opened",
      session: "launch-1",
      controlState: "starting",
      resolvedModel: "claude-fable-5[1m]",
      resolvedEffort: "max",
    });
  });

  it("200 vendor-defaults → opened with both knobs null", () => {
    expect(classifyOpenResponse(200, OPENED_VENDOR_DEFAULTS)).toMatchObject({
      path: "opened",
      resolvedModel: null,
      resolvedEffort: null,
    });
  });

  it("400 launch-selection-invalid → verbatim detail (partial pair / non-native only)", () => {
    expect(classifyOpenResponse(400, INVALID_PARTIAL_PAIR)).toEqual({
      path: "launch-selection-invalid",
      detail: "model and effort must be provided together",
    });
    expect(classifyOpenResponse(400, INVALID_NON_NATIVE)).toEqual({
      path: "launch-selection-invalid",
      detail: "model/effort launch selection requires an AR native harness session",
    });
  });

  it("400 bad-kind → open-refused with the verbatim status + detail", () => {
    expect(classifyOpenResponse(400, BAD_KIND)).toEqual({
      path: "open-refused",
      status: "bad-kind",
      detail: "harness not installed: 'codex'",
    });
  });

  it("409 leaf-taken → names the owning session", () => {
    expect(classifyOpenResponse(409, LEAF_TAKEN)).toEqual({
      path: "leaf-taken",
      leafKey: LEAF_TAKEN.leafKey,
      ownerSession: "worker-l3-live",
    });
  });

  it("409 launch-selection-conflict → the LIVE retained pair, provenance untouched", () => {
    expect(classifyOpenResponse(409, LAUNCH_CONFLICT)).toEqual({
      path: "launch-selection-conflict",
      session: "launch-1",
      detail: LAUNCH_CONFLICT.detail,
      liveModel: "claude-fable-5[1m]",
      liveEffort: "max",
      controlState: "ready",
    });
  });

  it("transport loss / 5xx / unrecognized → outcome-unknown (F9: resolve via the catalog)", () => {
    expect(classifyOpenResponse(null, undefined).path).toBe("outcome-unknown");
    expect(classifyOpenResponse(500, { detail: "boom" }).path).toBe("outcome-unknown");
    expect(classifyOpenResponse(502, undefined).path).toBe("outcome-unknown");
    expect(classifyOpenResponse(200, { nonsense: true }).path).toBe("outcome-unknown");
    expect(classifyOpenResponse(409, { status: "something-new" }).path).toBe("outcome-unknown");
  });
});

describe("openHostedSession", () => {
  it("POSTs the complete pair and classifies the answer", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(OPENED_STARTING), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const outcome = await openHostedSession("launch-1", {
      harness: "claude",
      selection: { modelKey: "claude-fable-5[1m]", effort: "max", vendorDefaults: false },
      label: "scout",
    });
    expect(outcome.path).toBe("opened");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/terminal/launch-1");
    expect(JSON.parse(init.body as string)).toEqual({
      kind: "harness",
      harness: "claude",
      model: "claude-fable-5[1m]",
      effort: "max",
      label: "scout",
    });
  });

  it("a vendor-defaults launch sends NEITHER knob", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(OPENED_VENDOR_DEFAULTS), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await openHostedSession("launch-defaults-1", {
      harness: "claude",
      selection: chooseVendorDefaults(),
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect("model" in body).toBe(false);
    expect("effort" in body).toBe(false);
  });

  it("a thrown fetch becomes outcome-unknown — the caller keeps the id and reconciles", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("socket hang up")));
    const outcome = await openHostedSession("launch-x", {
      harness: "pi",
      selection: { modelKey: "deepseek/deepseek-v4-flash", effort: "max", vendorDefaults: false },
    });
    expect(outcome.path).toBe("outcome-unknown");
  });
});
