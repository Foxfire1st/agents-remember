// The cmdk command palette (260715-FEUI-L1 S3): a scaffold with the palette-page pattern
// established — page "commands" lists the registry (the one options source), page "keys" renders
// the SAME chord/reserved-set data the tinykeys layer binds (data/keymap), so the `?` reference
// can never drift from the real bindings. Deliberately NOT a portal: the overlay stays inside the
// sessions-view root so the [data-view="sessions"] WebTUI scope covers it and focus return stays
// local (R7 — the view's closePalette hands focus back to the invoker).
import { Command } from "cmdk";
import {
  useEffect,
  useRef,
  useState,
  type RefObject,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { css, cx } from "../../../styled-system/css";
import type {
  Command as PaletteCommand,
  CommandContext,
  CommandRegistry,
  PalettePage,
} from "../../data/commands";
import {
  bindingFor,
  keymapCommandIsActive,
  setComposerProfile,
  useEffectiveKeymap,
  type EffectiveKeymap,
} from "../../data/keymap/preferences";
import { PTY_RESERVED } from "../../data/keymap/reserved";

const overlay = css({
  position: "absolute",
  inset: "0",
  zIndex: "20",
  background: "oklch(0 0 0 / 0.35)",
});
const box = css({
  position: "absolute",
  top: "8%",
  left: "50%",
  transform: "translateX(-50%)",
  width: "min(560px, 92%)",
  maxHeight: "70%",
  display: "flex",
  flexDirection: "column",
  // V1 — the panel CLIPS to its own bounds: the keys reference is taller than the panel, and without
  // this its list rows + footer spilled onto a transparent background over the composer/StatusLine
  // (help text superimposed on live page text). The cmdk root becomes a bounded flex column so the
  // list scrolls inside the panel and the footer stays on the panel background.
  overflow: "hidden",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  boxShadow: "0 10px 40px oklch(0 0 0 / 0.5)",
  // cmdk is unstyled; its data-attributes carry the house look.
  "& [cmdk-root]": {
    display: "flex",
    flexDirection: "column",
    flex: "1",
    minHeight: "0",
    overflow: "hidden",
  },
  "& [cmdk-input]": {
    font: "inherit",
    fontSize: "0.8rem",
    width: "100%",
    padding: "0.5rem 0.6rem",
    background: "bg",
    color: "ink",
    border: "none",
    borderBottomWidth: "1px",
    borderBottomStyle: "solid",
    borderBottomColor: "grid",
    outline: "none",
    "&:focus-visible": {
      outline: "1px solid token(colors.amber)",
      outlineOffset: "-2px",
    },
  },
  "& [cmdk-list]": { flex: "1", minHeight: "0", overflow: "auto", padding: "0.3rem" },
  "& [cmdk-item]": {
    display: "flex",
    alignItems: "baseline",
    gap: "0.6rem",
    fontSize: "0.76rem",
    color: "muted",
    padding: "0.3rem 0.5rem",
    borderRadius: "2px",
    borderLeftWidth: "2px",
    borderLeftStyle: "solid",
    borderLeftColor: "transparent",
    cursor: "pointer",
  },
  "& [cmdk-item][data-selected='true']": {
    color: "amber",
    background: "bg",
    borderLeftColor: "amber",
  },
  "& [cmdk-group-heading]": {
    fontSize: "0.66rem",
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    color: "cyan",
    padding: "0.35rem 0.5rem 0.15rem",
  },
  "& [cmdk-empty]": { fontSize: "0.74rem", color: "muted", padding: "0.5rem 0.6rem" },
});
const chordTag = css({
  marginLeft: "auto",
  fontSize: "0.64rem",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.3rem",
  whiteSpace: "nowrap",
});
const pageHint = css({
  fontSize: "0.66rem",
  color: "muted",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  padding: "0.25rem 0.6rem",
});
const profileRow = css({
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "0.6rem",
  padding: "0.35rem 0.5rem",
  fontSize: "0.72rem",
  color: "muted",
});
const profileButton = css({
  font: "inherit",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.45rem",
  cursor: "pointer",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const validationNote = css({
  color: "alarm",
  fontSize: "0.68rem",
  padding: "0.25rem 0.5rem",
});

function paletteFilter(value: string, search: string, keywords?: string[]): number {
  const query = (search.startsWith("/") ? search.slice(1) : search).trim().toLocaleLowerCase();
  if (!query) return 1;
  const haystack = [value, ...(keywords ?? [])].join(" ").toLocaleLowerCase();
  return query.split(/\s+/).every((term) => haystack.includes(term)) ? 1 : 0;
}

function cyclePaletteFocus(
  event: ReactKeyboardEvent<HTMLDivElement>,
  dialogRef: RefObject<HTMLDivElement | null>,
): void {
  const candidates = Array.from(
    dialogRef.current?.querySelectorAll<HTMLElement>(
      "input:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex='-1'])",
    ) ?? [],
  ).filter((element) => !element.hasAttribute("disabled"));
  if (candidates.length === 0) return;
  const first = candidates[0];
  const last = candidates[candidates.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function handlePaletteKey(
  event: ReactKeyboardEvent<HTMLDivElement>,
  dialogRef: RefObject<HTMLDivElement | null>,
  query: string,
  page: PalettePage,
  onClose: () => void,
  onPage: (page: PalettePage) => void,
): void {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    onClose();
    return;
  }
  if (event.key === "Tab") {
    cyclePaletteFocus(event, dialogRef);
    return;
  }
  // The page pattern's back gesture: Backspace on an empty query leaves a sub-page.
  if (event.key === "Backspace" && query === "" && page !== "commands") {
    event.preventDefault();
    onPage("commands");
  }
}

function paletteCommandChord(
  keymap: EffectiveKeymap,
  cmdId: string,
  cmd: PaletteCommand,
): string | undefined {
  return keymapCommandIsActive(keymap, cmdId)
    ? (bindingFor(keymap, cmdId)?.label ?? cmd.chord)
    : undefined;
}

function PaletteCommandList({
  commands,
  keymap,
  onRun,
}: {
  commands: PaletteCommand[];
  keymap: EffectiveKeymap;
  onRun: (id: string) => void;
}) {
  return (
    <>
      <Command.Empty>no matching command</Command.Empty>
      {commands.map((cmd) => {
        const chord = paletteCommandChord(keymap, cmd.id, cmd);
        return (
          <Command.Item
            key={cmd.id}
            value={cmd.id}
            keywords={[cmd.title, ...(cmd.keywords ?? [])]}
            onSelect={() => onRun(cmd.id)}
            data-testid={`palette-cmd-${cmd.id}`}
          >
            <span>{cmd.title}</span>
            {chord ? <span className={chordTag}>{chord}</span> : null}
          </Command.Item>
        );
      })}
    </>
  );
}

function PaletteKeysList({
  keymap,
  onToggleProfile,
}: {
  keymap: EffectiveKeymap;
  onToggleProfile: () => void;
}) {
  return (
    <>
      <Command.Group heading="Chrome — the shell around the panes">
        {keymap.bindings.filter((entry) => entry.zones.includes("chrome")).map((entry) => (
          <Command.Item key={`chrome-${entry.chord}`} value={`chrome-${entry.chord}`} disabled>
            <span>{entry.commandId}</span>
            <span className={chordTag}>{entry.label}</span>
          </Command.Item>
        ))}
      </Command.Group>
      <Command.Group heading="Composer — the editor owns its keys">
        {keymap.bindings
          .filter(
            (entry) =>
              entry.zones.includes("composer") &&
              keymapCommandIsActive(keymap, entry.commandId),
          )
          .map((entry) => (
            <Command.Item key={`composer-${entry.chord}`} value={`composer-${entry.chord}`} disabled>
              <span>{entry.commandId}</span>
              <span className={chordTag}>{entry.label}</span>
            </Command.Item>
          ))}
      </Command.Group>
      <div className={profileRow} data-testid="composer-profile-control">
        <span>
          composer profile: {keymap.composerProfile} · vim Esc changes mode; F6 exits
        </span>
        <button
          type="button"
          className={profileButton}
          aria-pressed={keymap.composerProfile === "vim"}
          onClick={onToggleProfile}
          data-testid="composer-profile-toggle"
        >
          use {keymap.composerProfile === "vim" ? "emacs" : "vim"}
        </button>
      </div>
      {keymap.issues.length > 0 ? (
        <div className={validationNote} role="status" data-testid="keymap-validation">
          {keymap.issues.join(" · ")}
        </div>
      ) : null}
      <Command.Group heading="Terminal — everything passes through except exactly">
        {PTY_RESERVED.map((entry) => (
          <Command.Item key={`pty-${entry.chord}`} value={`pty-${entry.chord}`} disabled>
            <span>
              {entry.action}
              {entry.bound ? "" : " (reserved, unbound)"}
            </span>
            <span className={chordTag}>{entry.chord}</span>
          </Command.Item>
        ))}
      </Command.Group>
    </>
  );
}

function PaletteDialog({
  dialogRef,
  paletteInputRef,
  page,
  query,
  setQuery,
  keymap,
  commands,
  onKeyDown,
  onRun,
  onToggleProfile,
}: {
  dialogRef: RefObject<HTMLDivElement | null>;
  paletteInputRef: RefObject<HTMLInputElement | null>;
  page: PalettePage;
  query: string;
  setQuery: (query: string) => void;
  keymap: EffectiveKeymap;
  commands: PaletteCommand[];
  onKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => void;
  onRun: (id: string) => void;
  onToggleProfile: () => void;
}) {
  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className={cx(box, "sessions__palette")}
    >
      <Command
        label="Command palette"
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        shouldFilter={page === "commands"}
        filter={paletteFilter}
      >
        <Command.Input
          ref={paletteInputRef}
          aria-label="Command search"
          value={query}
          onValueChange={setQuery}
          placeholder={page === "keys" ? "keyboard reference — backspace to go back" : "type a command…"}
          data-testid="sessions-palette-input"
        />
        <Command.List>
          {page === "commands" ? (
            <PaletteCommandList commands={commands} keymap={keymap} onRun={onRun} />
          ) : (
            <PaletteKeysList keymap={keymap} onToggleProfile={onToggleProfile} />
          )}
        </Command.List>
        <div className={pageHint}>
          {page === "keys"
            ? "esc closes · backspace returns to commands · Esc is NEVER intercepted over the terminal"
            : "enter runs · esc closes · ? opens the keyboard reference"}
        </div>
      </Command>
    </div>
  );
}

export function CommandPalette({
  open,
  page,
  registry,
  getContext,
  initialQuery,
  onClose,
  onPage,
}: {
  open: boolean;
  page: PalettePage;
  registry: CommandRegistry;
  getContext: () => CommandContext;
  initialQuery?: string;
  onClose: () => void;
  onPage: (page: PalettePage) => void;
}) {
  const [query, setQuery] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const paletteInputRef = useRef<HTMLInputElement>(null);
  const keymap = useEffectiveKeymap();
  // A fresh query per open — slash-to-palette can seed "/", otherwise it starts empty.
  useEffect(() => {
    if (open) setQuery(initialQuery ?? "");
  }, [initialQuery, open]);
  useEffect(() => {
    if (open) paletteInputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const runItem = (id: string) => {
    const command = registry.get(id);
    if (!command?.keepsPaletteOpen) {
      // Close first so the ordinary invoker-restoration step cannot overwrite a focus command's
      // destination (for example, Focus terminal must finish inside xterm, not back on the row).
      onClose();
    }
    registry.run(id, getContext());
    if (command?.keepsPaletteOpen) setQuery("");
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) =>
    handlePaletteKey(event, dialogRef, query, page, onClose, onPage);

  const commands = registry.list(getContext());

  return (
    <div
      className={overlay}
      role="presentation"
      data-testid="sessions-palette"
      onClick={onClose}
    >
      <PaletteDialog
        dialogRef={dialogRef}
        paletteInputRef={paletteInputRef}
        page={page}
        query={query}
        setQuery={setQuery}
        keymap={keymap}
        commands={commands}
        onKeyDown={onKeyDown}
        onRun={runItem}
        onToggleProfile={() =>
          setComposerProfile(keymap.composerProfile === "vim" ? "emacs" : "vim")
        }
      />
    </div>
  );
}
