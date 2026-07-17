// The cockpit command registry (260715-FEUI-L1 S3): the single options source behind BOTH
// surfaces — the cmdk palette and (later) chord dispatch. Commands are (id, title, when, run);
// `when` gates visibility AND runnability against a context snapshot the view supplies, so later
// leaves extend the palette by registering commands, never by editing the palette component.
// Pure and React-free: actions are injected through the context (the view owns the DOM).

import type { FocusRegion } from "./keymap/focus";

/** The palette pages (S3 palette-page pattern): one options source, two surfaces. */
export type PalettePage = "commands" | "keys";

/** The view facts + injected actions a command reads/drives. */
export interface CommandContext {
  railCollapsed: boolean;
  inspectorCollapsed: boolean;
  paletteOpen: boolean;
  actions: {
    openPalette: (page?: PalettePage) => void;
    closePalette: () => void;
    toggleRail: () => void;
    toggleInspector: () => void;
    focusRegion: (region: FocusRegion) => void;
    cycleRegion: (direction: 1 | -1) => void;
    focusStageHeader: () => void;
    focusTerminal: () => void;
    /** Stubs this leaf — L2 (session switch), L4 (effort), L5 (composer submit) wire them. */
    switchSession: (direction: 1 | -1) => void;
    cycleEffort: (direction: 1 | -1) => void;
    submitComposer: () => void;
  };
}

export interface Command {
  id: string;
  title: string;
  /** Extra palette search terms. */
  keywords?: readonly string[];
  /** Chord label surfaced next to the palette row (display-only; binding lives in keymap/). */
  chord?: string;
  /** True for commands that navigate WITHIN the palette (page switches) — selection keeps it open. */
  keepsPaletteOpen?: boolean;
  when?: (ctx: CommandContext) => boolean;
  run: (ctx: CommandContext) => void;
}

export interface CommandRegistry {
  /** Register (or replace, by id) a command; returns the matching unregister. */
  register: (command: Command) => () => void;
  get: (id: string) => Command | undefined;
  /** Registration-ordered commands whose `when` accepts the context. */
  list: (ctx: CommandContext) => Command[];
  /** Run by id when known + `when`-allowed; reports whether it ran. */
  run: (id: string, ctx: CommandContext) => boolean;
}

export function createCommandRegistry(): CommandRegistry {
  const commands = new Map<string, Command>();
  return {
    register(command) {
      commands.set(command.id, command);
      return () => {
        // Only remove if the id still points at THIS registration (a replacement wins).
        if (commands.get(command.id) === command) commands.delete(command.id);
      };
    },
    get(id) {
      return commands.get(id);
    },
    list(ctx) {
      return [...commands.values()].filter((cmd) => cmd.when?.(ctx) ?? true);
    },
    run(id, ctx) {
      const command = commands.get(id);
      if (!command) return false;
      if (command.when && !command.when(ctx)) return false;
      command.run(ctx);
      return true;
    },
  };
}

/**
 * The v1 command set (S3): palette/panel/focus commands are live; session-switch, effort, and
 * composer-submit are honest stubs routed through context actions so L2/L4/L5 replace the action,
 * not the command. Returns the registry for chaining.
 */
export function registerDefaultCommands(registry: CommandRegistry): CommandRegistry {
  const defaults: Command[] = [
    {
      id: "palette.open",
      title: "Open command palette",
      chord: "ctrl+k",
      when: (ctx) => !ctx.paletteOpen,
      run: (ctx) => ctx.actions.openPalette("commands"),
    },
    {
      id: "keyboard.reference",
      title: "Keyboard reference",
      keywords: ["keys", "shortcuts", "help", "?"],
      chord: "?",
      keepsPaletteOpen: true,
      run: (ctx) => ctx.actions.openPalette("keys"),
    },
    {
      id: "rail.toggle",
      title: "Toggle session rail",
      keywords: ["sidebar", "panel", "sessions"],
      run: (ctx) => ctx.actions.toggleRail(),
    },
    {
      id: "inspector.toggle",
      title: "Toggle inspector",
      keywords: ["evidence", "panel", "details"],
      run: (ctx) => ctx.actions.toggleInspector(),
    },
    {
      id: "focus.nextRegion",
      title: "Focus next region",
      chord: "F6",
      run: (ctx) => ctx.actions.cycleRegion(1),
    },
    {
      id: "focus.prevRegion",
      title: "Focus previous region",
      chord: "shift+F6",
      run: (ctx) => ctx.actions.cycleRegion(-1),
    },
    {
      id: "focus.stageHeader",
      title: "Focus stage header",
      chord: "esc (composer)",
      run: (ctx) => ctx.actions.focusStageHeader(),
    },
    {
      id: "focus.exitToChrome",
      title: "Exit terminal to chrome",
      chord: "F6 (terminal)",
      run: (ctx) => ctx.actions.focusStageHeader(),
    },
    {
      id: "focus.terminal",
      title: "Focus terminal",
      keywords: ["pty", "console"],
      run: (ctx) => ctx.actions.focusTerminal(),
    },
    {
      id: "session.prev",
      title: "Previous session",
      chord: "alt+↑",
      run: (ctx) => ctx.actions.switchSession(-1),
    },
    {
      id: "session.next",
      title: "Next session",
      chord: "alt+↓",
      run: (ctx) => ctx.actions.switchSession(1),
    },
    {
      // L4 R7: cycles the REQUESTED value through the current model's sessionSettable effort
      // options — no dialog; the requested-vs-effective chips carry the async honesty story.
      id: "effort.decrease",
      title: "Cycle effort down",
      keywords: ["effort", "thinking", "reasoning", "cycle"],
      chord: "alt+,",
      run: (ctx) => ctx.actions.cycleEffort(-1),
    },
    {
      id: "effort.increase",
      title: "Cycle effort up",
      keywords: ["effort", "thinking", "reasoning", "cycle"],
      chord: "alt+.",
      run: (ctx) => ctx.actions.cycleEffort(1),
    },
    {
      id: "composer.submit",
      title: "Submit composer (stub — L5)",
      chord: "ctrl+↵",
      run: (ctx) => ctx.actions.submitComposer(),
    },
  ];
  for (const command of defaults) registry.register(command);
  return registry;
}
