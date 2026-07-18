import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  KEYMAP_STORAGE_KEY,
  bindingFor,
  codeMirrorBinding,
  normalizeBinding,
  resolveKeymap,
  useEffectiveKeymap,
  writeKeymapPreferences,
} from "./preferences";

describe("effective keymap preferences", () => {
  beforeEach(() => {
    window.localStorage.clear();
    writeKeymapPreferences({});
  });

  it("applies a valid user override to the one effective catalog", () => {
    const keymap = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: { "palette.open": "ctrl+p" },
        composerProfile: "vim",
      }),
    );
    expect(bindingFor(keymap, "palette.open")).toMatchObject({
      chord: "Control+P",
      label: "ctrl+P",
    });
    expect(keymap.composerProfile).toBe("vim");
    expect(keymap.issues).toEqual([]);
  });

  it("rejects corrupt, unknown, browser-owned, and colliding entries independently", () => {
    const corrupt = resolveKeymap("{");
    expect(corrupt.issues[0]).toContain("not valid JSON");
    expect(bindingFor(corrupt, "palette.open")?.chord).toBe("Control+K");

    const keymap = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: {
          "palette.open": "Control+W",
          "composer.submit": "Escape",
          bogus: "Control+P",
        },
        composerProfile: "nano",
      }),
    );
    expect(bindingFor(keymap, "palette.open")?.chord).toBe("Control+K");
    expect(bindingFor(keymap, "composer.submit")?.chord).toBe("Control+Enter");
    expect(keymap.composerProfile).toBe("emacs");
    expect(keymap.issues.join(" ")).toMatch(/browser-reserved|duplicate/);
    expect(keymap.issues.join(" ")).toContain("unknown command");
    expect(keymap.issues.join(" ")).toContain("unknown composer profile");
  });

  it("accepts only the supported tinykeys subset and translates it for CodeMirror", () => {
    expect(normalizeBinding("alt+arrowup")).toBe("Alt+ArrowUp");
    expect(normalizeBinding("Control++P")).toBeNull();
    expect(normalizeBinding("Control+ImaginaryKey")).toBeNull();
    expect(codeMirrorBinding("Control+Enter")).toBe("Ctrl-Enter");
    expect(codeMirrorBinding("Alt+ArrowUp")).toBe("Alt-ArrowUp");
  });

  it("keeps F6 as the truthful Vim escape when both region commands attempt printable overrides", () => {
    const keymap = resolveKeymap(
      JSON.stringify({
        version: 1,
        bindings: {
          "focus.nextRegion": "K",
          "focus.prevRegion": "L",
        },
        composerProfile: "vim",
      }),
    );

    expect(keymap.composerProfile).toBe("vim");
    expect(bindingFor(keymap, "focus.nextRegion")?.chord).toBe("F6");
    expect(bindingFor(keymap, "focus.prevRegion")?.chord).toBe("Shift+F6");
    expect(keymap.issues).toEqual(
      expect.arrayContaining([
        expect.stringContaining("F6 is the fixed browser-safe composer escape"),
        expect.stringContaining("printable composer binding rejected"),
      ]),
    );
  });

  it.each(["Meta+W", "Meta+T", "Meta+N", "Meta+Q", "Meta+Shift+W"])(
    "rejects browser-owned Command chord %s",
    (chord) => {
      const keymap = resolveKeymap(
        JSON.stringify({ version: 1, bindings: { "palette.open": chord } }),
      );
      expect(bindingFor(keymap, "palette.open")?.chord).toBe("Control+K");
      expect(keymap.issues.join(" ")).toContain("browser-reserved");
    },
  );

  it("publishes same-tab writes through the public preference seam", async () => {
    const { result } = renderHook(() => useEffectiveKeymap());
    expect(bindingFor(result.current, "palette.open")?.chord).toBe("Control+K");

    act(() => {
      writeKeymapPreferences({
        bindings: { "palette.open": "Control+P" },
        composerProfile: "vim",
      });
    });

    await waitFor(() => {
      expect(bindingFor(result.current, "palette.open")?.chord).toBe("Control+P");
      expect(result.current.composerProfile).toBe("vim");
    });
  });

  it("reacts to native cross-tab storage notifications", async () => {
    const { result } = renderHook(() => useEffectiveKeymap());
    const raw = JSON.stringify({
      version: 1,
      bindings: { "palette.open": "Control+P" },
      composerProfile: "vim",
    });

    act(() => {
      window.localStorage.setItem(KEYMAP_STORAGE_KEY, raw);
      window.dispatchEvent(
        new StorageEvent("storage", { key: KEYMAP_STORAGE_KEY, newValue: raw }),
      );
    });

    await waitFor(() => {
      expect(bindingFor(result.current, "palette.open")?.chord).toBe("Control+P");
      expect(result.current.composerProfile).toBe("vim");
    });
  });
});
