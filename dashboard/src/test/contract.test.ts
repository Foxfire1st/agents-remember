import { describe, expect, it } from "vitest";

import type { WorkspaceProjection } from "../types/projection";
import snapshot from "../fixtures/snapshot.json";

// A runtime smoke of the served contract against the TS mirror (D7: no pydantic→TS codegen
// yet, so this guards the fixture + the obvious shape; projection.py stays source of truth).
const projection = snapshot as unknown as WorkspaceProjection;

const STATES = ["running", "paused", "blocked", "completed", "abandoned"];
const PHASES = ["request", "trust-checkpoint", "reframe-research", "decide", "build", "close"];

describe("projection contract fixture", () => {
  it("has the top-level projection shape", () => {
    expect(typeof projection.version).toBe("number");
    expect(typeof projection.generatedAt).toBe("string");
    expect(Array.isArray(projection.lifecycles)).toBe(true);
    expect(Array.isArray(projection.enclosures)).toBe(true);
    expect(Array.isArray(projection.providers)).toBe(true);
    expect(projection.metrics).toBeTypeOf("object");
    expect(projection.analytics).toBeTypeOf("object");
  });

  it("lifecycles carry required fields with valid enums", () => {
    expect(projection.lifecycles.length).toBeGreaterThan(0);
    for (const lifecycle of projection.lifecycles) {
      expect(typeof lifecycle.id).toBe("string");
      expect(STATES).toContain(lifecycle.state);
      expect(PHASES).toContain(lifecycle.phase);
      expect(typeof lifecycle.fleeting).toBe("boolean");
      expect(Array.isArray(lifecycle.actions)).toBe(true);
      expect(Array.isArray(lifecycle.tokenSeries)).toBe(true);
    }
  });
});
