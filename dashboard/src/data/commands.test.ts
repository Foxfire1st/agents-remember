// The command registry (260715-FEUI-L1 S3/S5): registration order, when-gating, replace-by-id,
// and the default command set later leaves extend.
import { describe, expect, it, vi } from "vitest";

import {
  createCommandRegistry,
  registerDefaultCommands,
  type Command,
  type CommandContext,
} from "./commands";

function ctx(over: Partial<CommandContext> = {}): CommandContext {
  return {
    railCollapsed: false,
    inspectorCollapsed: false,
    paletteOpen: false,
    actions: {
      openPalette: vi.fn(),
      closePalette: vi.fn(),
      toggleRail: vi.fn(),
      toggleInspector: vi.fn(),
      focusRegion: vi.fn(),
      cycleRegion: vi.fn(),
      focusStageHeader: vi.fn(),
      focusTerminal: vi.fn(),
      switchSession: vi.fn(),
      cycleEffort: vi.fn(),
      submitComposer: vi.fn(),
    },
    ...over,
  };
}

const noop: Command["run"] = () => {};

describe("createCommandRegistry", () => {
  it("lists commands in registration order and filters by when-predicate", () => {
    const registry = createCommandRegistry();
    registry.register({ id: "b", title: "B", run: noop });
    registry.register({ id: "a", title: "A", when: (c) => c.railCollapsed, run: noop });
    expect(registry.list(ctx()).map((cmd) => cmd.id)).toEqual(["b"]);
    expect(registry.list(ctx({ railCollapsed: true })).map((cmd) => cmd.id)).toEqual(["b", "a"]);
  });

  it("runs by id, honors when-gates, and reports whether it ran", () => {
    const registry = createCommandRegistry();
    const run = vi.fn();
    registry.register({ id: "x", title: "X", when: (c) => c.paletteOpen, run });
    expect(registry.run("x", ctx())).toBe(false);
    expect(run).not.toHaveBeenCalled();
    expect(registry.run("x", ctx({ paletteOpen: true }))).toBe(true);
    expect(run).toHaveBeenCalledTimes(1);
    expect(registry.run("missing", ctx())).toBe(false);
  });

  it("replaces by id and unregisters only its own registration", () => {
    const registry = createCommandRegistry();
    const first = vi.fn();
    const second = vi.fn();
    const unregisterFirst = registry.register({ id: "same", title: "First", run: first });
    registry.register({ id: "same", title: "Second", run: second });
    unregisterFirst(); // stale — the replacement must survive
    expect(registry.run("same", ctx())).toBe(true);
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });
});

describe("registerDefaultCommands", () => {
  it("registers the v1 set with live panel/focus actions and honest stubs", () => {
    const registry = registerDefaultCommands(createCommandRegistry());
    const ids = registry.list(ctx()).map((cmd) => cmd.id);
    for (const id of [
      "palette.open",
      "keyboard.reference",
      "rail.toggle",
      "inspector.toggle",
      "focus.nextRegion",
      "focus.prevRegion",
      "focus.terminal",
      "session.prev",
      "session.next",
      "effort.decrease",
      "effort.increase",
      "composer.submit",
    ]) {
      expect(ids, `missing default command ${id}`).toContain(id);
    }
  });

  it("routes commands into the injected actions", () => {
    const registry = registerDefaultCommands(createCommandRegistry());
    const context = ctx();
    registry.run("rail.toggle", context);
    expect(context.actions.toggleRail).toHaveBeenCalledTimes(1);
    registry.run("focus.nextRegion", context);
    expect(context.actions.cycleRegion).toHaveBeenCalledWith(1);
    registry.run("keyboard.reference", context);
    expect(context.actions.openPalette).toHaveBeenCalledWith("keys");
    registry.run("session.next", context);
    expect(context.actions.switchSession).toHaveBeenCalledWith(1);
  });

  it("gates palette.open on the palette being closed", () => {
    const registry = registerDefaultCommands(createCommandRegistry());
    expect(registry.run("palette.open", ctx({ paletteOpen: true }))).toBe(false);
    expect(registry.run("palette.open", ctx())).toBe(true);
  });

  it("marks the keyboard reference as an in-palette page switch", () => {
    const registry = registerDefaultCommands(createCommandRegistry());
    expect(registry.get("keyboard.reference")?.keepsPaletteOpen).toBe(true);
    expect(registry.get("rail.toggle")?.keepsPaletteOpen).toBeUndefined();
  });
});
