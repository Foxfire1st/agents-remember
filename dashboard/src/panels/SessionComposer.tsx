import { useState } from "react";
import { Button, TextArea, TextField } from "react-aria-components";

import { css } from "../../styled-system/css";

// The context composer (slice 6e-3): a docked input that injects a block of text into the **active**
// session's stdin — "send to session" / "pass any amount of context into the chat" (the on-ramp to 6f
// highlight→feedback). Presentational + controlled: it owns only the draft text and reports `onSend`;
// `Chats` wires that to the active `TerminalConnection` and wraps it as a bracketed paste. React Aria
// `TextField`/`TextArea` + `Button` (coding-guidelines: use the React Aria primitive, don't hand-roll).
const form = css({
  display: "flex",
  alignItems: "flex-end",
  gap: "0.4rem",
  flexShrink: 0,
});
const field = css({ display: "flex", flex: "1", minWidth: "0" });
const area = css({
  font: "inherit",
  fontSize: "0.78rem",
  color: "inherit",
  width: "100%",
  resize: "none",
  minHeight: "2.25rem",
  maxHeight: "8rem",
  paddingInline: "0.5rem",
  paddingBlock: "0.35rem",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const send = css({
  font: "inherit",
  fontSize: "0.74rem",
  letterSpacing: "0.04em",
  flexShrink: 0,
  paddingInline: "0.7rem",
  paddingBlock: "0.35rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "rgba(232, 193, 112, 0.1)" },
  _disabled: { opacity: "0.4", cursor: "default", _hover: { background: "transparent" } },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function SessionComposer({ onSend }: { onSend: (text: string) => void }) {
  const [text, setText] = useState("");

  const submit = () => {
    const value = text.trim();
    if (!value) return;
    onSend(value);
    setText("");
  };

  return (
    <div className={form} data-testid="chats-composer">
      <TextField
        className={field}
        aria-label="Send context to the session"
        value={text}
        onChange={setText}
      >
        <TextArea
          className={area}
          rows={1}
          placeholder="Paste context to send to this session…  (⌘/Ctrl+Enter)"
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
        />
      </TextField>
      <Button
        className={send}
        isDisabled={text.trim().length === 0}
        onPress={submit}
        data-testid="chats-composer-send"
      >
        Send
      </Button>
    </div>
  );
}
