import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSession, deliverToSession, pasteDraftToSession, sessionStore } from "../data/sessions";
import { useSelectionCapture } from "../data/selection";
import { fetchHarnesses, uploadSessionImage } from "../data/terminal";
import { HighlightComposer } from "./HighlightComposer";

vi.mock("../data/selection", () => ({ useSelectionCapture: vi.fn() }));
vi.mock("../data/sessions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/sessions")>();
  return { ...actual, createSession: vi.fn(), deliverToSession: vi.fn(), pasteDraftToSession: vi.fn() };
});
vi.mock("../data/terminal", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/terminal")>();
  return { ...actual, fetchHarnesses: vi.fn(), uploadSessionImage: vi.fn() };
});

const SELECTION = {
  text: "a blocked finding",
  rect: { left: 10, top: 10, width: 40, height: 14 } as DOMRect,
};
const clear = vi.fn();
const HARNESSES = [
  { id: "claude", name: "Claude Code", detected: true },
  { id: "codex", name: "Codex", detected: true },
  { id: "pi", name: "Pi.dev", detected: false },
];

beforeEach(() => {
  vi.mocked(useSelectionCapture).mockReturnValue({ selection: SELECTION, clear });
  vi.mocked(createSession).mockResolvedValue("created-id");
  vi.mocked(deliverToSession).mockResolvedValue("delivered");
  vi.mocked(pasteDraftToSession).mockResolvedValue("delivered");
  vi.mocked(uploadSessionImage).mockResolvedValue("/cwd/.dashboard-pastes/a.png");
  vi.mocked(fetchHarnesses).mockResolvedValue(HARNESSES);
  // jsdom has no object-URL impl; the composer previews a pasted image with it.
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  sessionStore.setState({ sessions: [], activeId: null, count: 0 });
});

describe("HighlightComposer (6f-1)", () => {
  it("renders nothing until there is a selection", () => {
    vi.mocked(useSelectionCapture).mockReturnValue({ selection: null, clear });
    const { queryByTestId } = render(<HighlightComposer />);
    expect(queryByTestId("highlight-composer")).toBeNull();
  });

  it("a selection raises the 'Add to chat' pill, then clicking it opens the composer box", async () => {
    const { findByTestId, queryByTestId } = render(<HighlightComposer />);
    expect(await findByTestId("highlight-add-to-chat")).not.toBeNull();
    expect(queryByTestId("highlight-send")).toBeNull();
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-send")).not.toBeNull();
  });

  it("offers a create option per *detected* harness, plus a shell", async () => {
    const { findByTestId, getByTestId, queryByTestId } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-target-c:claude")).not.toBeNull();
    expect(getByTestId("highlight-target-c:codex")).not.toBeNull();
    expect(getByTestId("highlight-target-c:terminal")).not.toBeNull();
    expect(queryByTestId("highlight-target-c:pi")).toBeNull();
  });

  it("with no chat open, Enter creates the default detected harness (not a shell) and delivers", async () => {
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-target-c:claude");
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    await waitFor(() => expect(createSession).toHaveBeenCalledWith("Claude Code", "harness", "claude"));
    await waitFor(() =>
      expect(deliverToSession).toHaveBeenCalledWith(
        "created-id",
        expect.stringContaining("a blocked finding"),
      ),
    );
  });

  it("tags a created chat with the selected lifecycle", async () => {
    const { findByTestId, getByRole } = render(<HighlightComposer selectedLifecycleId="LC1" />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-target-c:claude");
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    await waitFor(() =>
      expect(createSession).toHaveBeenCalledWith("Claude Code", "harness", "claude", "LC1"),
    );
  });

  it("picking a different harness create option targets that harness", async () => {
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    fireEvent.click(await findByTestId("highlight-target-c:codex"));
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    await waitFor(() => expect(createSession).toHaveBeenCalledWith("Codex", "harness", "codex"));
  });

  it("with a chat open, Enter delivers to it and dismisses once delivery is confirmed", async () => {
    sessionStore.getState().add("Terminal", "s1");
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-send");
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    await waitFor(() =>
      expect(deliverToSession).toHaveBeenCalledWith("s1", expect.stringContaining("a blocked finding")),
    );
    expect(sessionStore.getState().activeId).toBe("s1");
    await waitFor(() => expect(clear).toHaveBeenCalled()); // dismiss only after "delivered"
  });

  it("filters open chat targets to the selected lifecycle", async () => {
    sessionStore.getState().add("Terminal", "s1", "LC1");
    sessionStore.getState().add("Terminal", "s2", "LC2");
    const { findByTestId, getByRole, queryByTestId } = render(
      <HighlightComposer selectedLifecycleId="LC1" />,
    );
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-target-s:s1")).not.toBeNull();
    expect(queryByTestId("highlight-target-s:s2")).toBeNull();
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    await waitFor(() =>
      expect(deliverToSession).toHaveBeenCalledWith("s1", expect.stringContaining("a blocked finding")),
    );
  });

  it("pill click pastes directly into the viewed leaf chat — a selection alone never pastes", async () => {
    const leafKey = "repo/master/L8";
    vi.mocked(useSelectionCapture).mockReturnValue({ selection: { ...SELECTION, leafKey }, clear });
    sessionStore.getState().hydrate([
      { id: "leaf-chat", label: "Claude Code 1", kind: "harness", leafKey, status: "running" },
    ]);
    const { findByTestId, queryByTestId } = render(
      <HighlightComposer viewedLeafKey={leafKey} leafChatActive />,
    );

    const pill = await findByTestId("highlight-add-to-chat"); // the affordance stays visible
    expect(pasteDraftToSession).not.toHaveBeenCalled(); // no auto-paste on selection
    fireEvent.click(pill);
    await waitFor(() =>
      expect(pasteDraftToSession).toHaveBeenCalledWith("leaf-chat", expect.stringContaining("a blocked finding")),
    );
    expect(queryByTestId("highlight-send")).toBeNull(); // no selector/composer stage
    expect(deliverToSession).not.toHaveBeenCalled(); // draft only, no submit
    await waitFor(() => expect(clear).toHaveBeenCalled()); // dismissed after the confirmed paste
  });

  it("opens the generic composer when the direct pill paste is not confirmed", async () => {
    const leafKey = "repo/master/L8";
    vi.mocked(pasteDraftToSession).mockResolvedValue("unconfirmed");
    vi.mocked(useSelectionCapture).mockReturnValue({ selection: { ...SELECTION, leafKey }, clear });
    sessionStore.getState().hydrate([
      { id: "leaf-chat", label: "Claude Code 1", kind: "harness", leafKey, status: "running" },
    ]);
    const { findByTestId } = render(<HighlightComposer viewedLeafKey={leafKey} leafChatActive />);

    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    expect(await findByTestId("highlight-send")).not.toBeNull(); // fell back to the visible composer
    expect(clear).not.toHaveBeenCalled();
  });

  it("keeps the generic composer fallback when the selected text is outside the viewed leaf", async () => {
    vi.mocked(useSelectionCapture).mockReturnValue({
      selection: { ...SELECTION, leafKey: "repo/master/OTHER" },
      clear,
    });
    sessionStore.getState().hydrate([
      { id: "leaf-chat", label: "Claude Code 1", kind: "harness", leafKey: "repo/master/L8", status: "running" },
    ]);
    const { findByTestId } = render(<HighlightComposer viewedLeafKey="repo/master/L8" leafChatActive />);

    expect(await findByTestId("highlight-add-to-chat")).not.toBeNull();
    expect(pasteDraftToSession).not.toHaveBeenCalled();
  });

  it("keeps the composer open with a retry status when delivery is not confirmed", async () => {
    vi.mocked(deliverToSession).mockResolvedValue("unconfirmed");
    sessionStore.getState().add("Terminal", "s1");
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-send");
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    expect(await findByTestId("highlight-status")).not.toBeNull();
    expect((await findByTestId("highlight-send")).textContent).toBe("Retry");
    expect(clear).not.toHaveBeenCalled(); // not dismissed on a failed delivery
  });

  it("Retry on a create-target re-delivers to the SAME session and never re-creates it", async () => {
    vi.mocked(deliverToSession).mockResolvedValue("unconfirmed");
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-target-c:claude");
    fireEvent.keyDown(getByRole("textbox"), { key: "Enter" });
    await waitFor(() => expect(deliverToSession).toHaveBeenCalledTimes(1));
    expect(createSession).toHaveBeenCalledTimes(1);
    const retry = await findByTestId("highlight-send");
    expect(retry.textContent).toBe("Retry");
    fireEvent.click(retry);
    await waitFor(() => expect(deliverToSession).toHaveBeenCalledTimes(2));
    expect(createSession).toHaveBeenCalledTimes(1); // the prior session is reused, not orphaned
    expect(vi.mocked(deliverToSession).mock.calls.every((c) => c[0] === "created-id")).toBe(true);
  });

  it("two synchronous Enter presses create only one session (the in-flight ref guard)", async () => {
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    await findByTestId("highlight-target-c:claude");
    const box = getByRole("textbox");
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.keyDown(box, { key: "Enter" }); // same tick, before any re-render / disabled state flushes
    await waitFor(() => expect(deliverToSession).toHaveBeenCalled());
    expect(createSession).toHaveBeenCalledTimes(1);
  });

  it("a pasted image is previewed, uploaded, and its path injected into the package", async () => {
    sessionStore.getState().add("Terminal", "s1");
    const { findByTestId, getByRole } = render(<HighlightComposer />);
    fireEvent.click(await findByTestId("highlight-add-to-chat"));
    const box = getByRole("textbox");
    const file = new File([new Uint8Array([1, 2, 3])], "shot.png", { type: "image/png" });
    fireEvent.paste(box, { clipboardData: { files: [file] } });
    expect(await findByTestId("highlight-image")).not.toBeNull();
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(uploadSessionImage).toHaveBeenCalledWith("s1", file));
    await waitFor(() =>
      expect(deliverToSession).toHaveBeenCalledWith(
        "s1",
        expect.stringContaining("Screenshot for context: /cwd/.dashboard-pastes/a.png"),
      ),
    );
  });
});
