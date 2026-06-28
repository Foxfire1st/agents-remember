import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionComposer } from "./SessionComposer";

afterEach(cleanup);

function textarea(container: HTMLElement): HTMLTextAreaElement {
  const el = container.querySelector("textarea");
  if (!el) throw new Error("composer textarea not found");
  return el;
}

// SessionComposer (slice 6e-3) is pure + presentational (React Aria TextField/TextArea + Button, no
// backend, no xterm), so its behavior is driven directly — it reports onSend; Chats wires the actual
// stdin injection.
describe("SessionComposer (6e-3)", () => {
  it("sends the trimmed draft and clears on Send", () => {
    const onSend = vi.fn();
    const { getByTestId, container } = render(<SessionComposer onSend={onSend} />);
    const input = textarea(container);
    fireEvent.change(input, { target: { value: "  some context  " } });
    fireEvent.click(getByTestId("chats-composer-send"));
    expect(onSend).toHaveBeenCalledWith("some context");
    expect(input.value).toBe("");
  });

  it("sends on Cmd/Ctrl+Enter", () => {
    const onSend = vi.fn();
    const { container } = render(<SessionComposer onSend={onSend} />);
    const input = textarea(container);
    fireEvent.change(input, { target: { value: "ctx" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(onSend).toHaveBeenCalledWith("ctx");
  });

  it("does not send an empty / whitespace-only draft", () => {
    const onSend = vi.fn();
    const { getByTestId, container } = render(<SessionComposer onSend={onSend} />);
    fireEvent.click(getByTestId("chats-composer-send")); // empty → the button is disabled
    fireEvent.change(textarea(container), { target: { value: "   " } });
    fireEvent.keyDown(textarea(container), { key: "Enter", metaKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });
});
