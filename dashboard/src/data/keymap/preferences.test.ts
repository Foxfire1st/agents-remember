import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  KEYMAP_STORAGE_KEY,
  bindingFor,
  normalizeBinding,
  resolveKeymap,
  useEffectiveKeymap,
} from "./preferences";

describe("effective keymap preferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("applies a valid user override to the one effective catalog", () => {
    const keymap = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: { "review.nextChange": "alt+n" },
      }),
    );
    expect(bindingFor(keymap, "review.nextChange")).toMatchObject({
      chord: "Alt+N",
      label: "alt+N",
      zones: ["review"],
    });
    expect(bindingFor(keymap, "review.previousChange")).toMatchObject({ chord: "K", label: "k" });
  });

  it("rejects corrupt, unknown, browser-owned, and colliding entries independently", () => {
    const corrupt = resolveKeymap("{");
    expect(bindingFor(corrupt, "review.nextChange")?.chord).toBe("J");
    expect(bindingFor(corrupt, "review.previousChange")?.chord).toBe("K");

    const keymap = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: {
          "review.nextChange": "Control+W",
          // The default of the other command: a duplicate in the same zone.
          "review.previousChange": "J",
          bogus: "Control+P",
        },
      }),
    );
    expect(bindingFor(keymap, "review.nextChange")).toMatchObject({ chord: "J", label: "j" });
    expect(bindingFor(keymap, "review.previousChange")).toMatchObject({ chord: "K", label: "k" });
    expect(bindingFor(keymap, "bogus")).toBeUndefined();
    expect(keymap.bindings.map((entry) => entry.commandId)).toEqual([
      "review.nextChange",
      "review.previousChange",
    ]);
  });

  it("restores the defaults for a stored object of another version or shape", () => {
    for (const stored of [
      { version: 2, bindings: { "review.nextChange": "N" } },
      { bindings: { "review.nextChange": "N" } },
      [{ version: 1 }],
      { version: 1, bindings: ["N"] },
      { version: 1, bindings: { "review.nextChange": 7 } },
    ]) {
      const keymap = resolveKeymap(JSON.stringify(stored));
      expect(bindingFor(keymap, "review.nextChange")?.chord).toBe("J");
      expect(bindingFor(keymap, "review.previousChange")?.chord).toBe("K");
    }
  });

  // An object stored by an earlier build can still bind commands that no longer exist and name a
  // composer profile. It loads, those entries have no effect, and its reviewer override applies.
  it("ignores bindings of commands it does not know and fields it does not read", () => {
    const raw = JSON.stringify({
      version: 1,
      bindings: {
        "palette.open": "Control+P",
        "session.next": "J",
        "composer.submit": "Escape",
        "focus.nextRegion": "K",
        "conversation.stop": "Control+W",
        "review.nextChange": "N",
      },
      composerProfile: "vim",
    });
    const keymap = resolveKeymap(raw);
    expect(keymap.bindings).toEqual([
      { chord: "N", label: "N", commandId: "review.nextChange", zones: ["review"] },
      { chord: "K", label: "k", commandId: "review.previousChange", zones: ["review"] },
    ]);
    expect(bindingFor(keymap, "palette.open")).toBeUndefined();
  });

  it("accepts a swap of the two reviewer bindings and rejects the same chord for both", () => {
    const swapped = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: { "review.nextChange": "K", "review.previousChange": "J" },
      }),
    );
    expect(bindingFor(swapped, "review.nextChange")?.chord).toBe("K");
    expect(bindingFor(swapped, "review.previousChange")?.chord).toBe("J");

    const same = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: { "review.nextChange": "N", "review.previousChange": "n" },
      }),
    );
    expect(bindingFor(same, "review.nextChange")?.chord).toBe("J");
    expect(bindingFor(same, "review.previousChange")?.chord).toBe("K");
  });

  it("accepts only the supported tinykeys subset", () => {
    expect(normalizeBinding("alt+arrowup")).toBe("Alt+ArrowUp");
    expect(normalizeBinding("Control++P")).toBeNull();
    expect(normalizeBinding("Control+ImaginaryKey")).toBeNull();
  });

  it.each(["Meta+W", "Meta+T", "Meta+N", "Meta+Q", "Meta+Shift+W"])(
    "rejects browser-owned Command chord %s",
    (chord) => {
      const keymap = resolveKeymap(
        JSON.stringify({ version: 1, bindings: { "review.nextChange": chord } }),
      );
      expect(bindingFor(keymap, "review.nextChange")?.chord).toBe("J");
    },
  );

  it("reads the stored overrides when the hook mounts", () => {
    window.localStorage.setItem(
      KEYMAP_STORAGE_KEY,
      JSON.stringify({ version: 1, bindings: { "review.previousChange": "P" } }),
    );
    const { result } = renderHook(() => useEffectiveKeymap());
    expect(bindingFor(result.current, "review.nextChange")?.chord).toBe("J");
    expect(bindingFor(result.current, "review.previousChange")?.chord).toBe("P");
  });

  it("reacts to native cross-tab storage notifications", async () => {
    const { result } = renderHook(() => useEffectiveKeymap());
    expect(bindingFor(result.current, "review.nextChange")?.chord).toBe("J");
    const raw = JSON.stringify({
      version: 1,
      bindings: { "review.nextChange": "N" },
    });

    act(() => {
      window.localStorage.setItem(KEYMAP_STORAGE_KEY, raw);
      window.dispatchEvent(
        new StorageEvent("storage", { key: KEYMAP_STORAGE_KEY, newValue: raw }),
      );
    });

    await waitFor(() => {
      expect(bindingFor(result.current, "review.nextChange")?.chord).toBe("N");
    });
  });
});
