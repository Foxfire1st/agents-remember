import { describe, expect, it } from "vitest";

import type { FileContent } from "../../data/files";
import {
  bodyBytes,
  keepScroll,
  releaseBodies,
  type OpenCode,
  type ViewerHistory,
} from "./viewerHistory";

function code(path: string, size: number, sidecarBody = ""): OpenCode {
  const content = {
    scope: "mainline",
    path,
    language: "markdown",
    size,
    truncated: false,
    content: "x".repeat(size),
    mediaType: null,
    tooLarge: false,
    onboarding: { status: "missing" },
  } as FileContent;
  return {
    kind: "code",
    content,
    sidecar: sidecarBody ? { state: "markdown", body: sidecarBody } : { state: "missing" },
    rendered: true,
    scrollTop: 0,
  };
}

function history(entries: OpenCode[], index = entries.length - 1): ViewerHistory {
  return { entries, index };
}

describe("the viewer history's body budget", () => {
  it("keeps every address and scroll position when the bodies fit", () => {
    const state = history([code("a.md", 10), code("b.md", 10)]);
    const kept = releaseBodies(state, 1024);
    expect(kept).toBe(state);
    expect(kept.entries.map((entry) => entry.kind === "code" && entry.content.path)).toEqual([
      "a.md",
      "b.md",
    ]);
  });

  it("reclaims the oldest bodies but never a count of navigable entries", () => {
    const entries = Array.from({ length: 20 }, (_, index) => code(`file-${index}.md`, 1024));
    const before = history(entries);
    const after = releaseBodies(before, 2 * 1024);
    expect(after.entries).toHaveLength(20);
    expect(after.entries.map((entry) => (entry.kind === "code" ? entry.content.path : ""))).toEqual(
      entries.map((entry) => entry.content.path),
    );
    expect(after.entries.filter((entry) => !entry.bodyReleased)).toHaveLength(2);
    expect(after.entries[0].bodyReleased).toBe(true);
    expect(after.entries[19].bodyReleased).toBeUndefined();
    expect(after.entries.reduce((sum, entry) => sum + bodyBytes(entry), 0)).toBeLessThanOrEqual(
      2 * 1024,
    );
  });

  it("reclaims a code entry's sidecar body too, not only its code content", () => {
    const entries = [
      code("a.md", 1024, "s".repeat(1024)),
      code("b.md", 1024, "t".repeat(1024)),
    ];
    const after = releaseBodies(history(entries), 2 * 1024);
    expect(after.entries).toHaveLength(2);
    expect(after.entries[0].bodyReleased).toBe(true);
    expect(after.entries[0].kind === "code" && after.entries[0].sidecar.state).toBe("empty");
    expect(after.entries[1].bodyReleased).toBeUndefined();
    const retained = after.entries.reduce((sum, entry) => {
      const content = entry.kind === "code" && !entry.bodyReleased ? entry.content.content.length : 0;
      const sidecar = entry.sidecar.state === "markdown" ? entry.sidecar.body.length : 0;
      return sum + content + sidecar;
    }, 0);
    expect(retained).toBeLessThanOrEqual(2 * 1024);
  });

  it("retains the current body even when it alone exceeds the budget", () => {
    const state = history([code("small.md", 10), code("huge.md", 4 * 1024)]);
    const after = releaseBodies(state, 1024);
    expect(after.entries[0].bodyReleased).toBe(true);
    expect(after.entries[1].bodyReleased).toBeUndefined();
    expect(after.index).toBe(1);
  });

  it("keeps the scroll position of the entry being left", () => {
    const state = history([code("a.md", 10), code("b.md", 10)]);
    const kept = keepScroll(state, 640);
    expect(kept.entries[1].scrollTop).toBe(640);
    expect(kept.entries[0].scrollTop).toBe(0);
  });
});
