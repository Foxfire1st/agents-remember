import { act, fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SessionsView } from "./SessionsView";
import { seedReadyComposerSession } from "./test-utils";

vi.mock("../../Terminal", async () => {
  const { useEffect } = await import("react");
  const { mockTerminalMounts, mockTerminalUnmounts } = await import(
    "./test-utils"
  );
  return {
    Terminal: ({ sessionId, readOnly }: { sessionId: string; readOnly?: boolean }) => {
      useEffect(() => {
        mockTerminalMounts.push(sessionId);
        return () => {
          mockTerminalUnmounts.push(sessionId);
        };
      }, [sessionId]);
      return (
        <div
          data-testid={`mock-terminal-${sessionId}`}
          data-read-only={String(readOnly ?? false)}
        />
      );
    },
  };
});

describe("focus model (S4, design §5.3)", () => {
  it("F6 skips the default-closed inspector, then includes it after deliberate reopen", async () => {
    seedReadyComposerSession();
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content");
    const regionOf = (element: Element | null) =>
      element?.closest("[data-region]")?.getAttribute("data-region");

    fireEvent.keyDown(document.body, { key: "F6", code: "F6" });
    expect(regionOf(document.activeElement)).toBe("rail");
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    expect(regionOf(document.activeElement)).toBe("stage");
    expect(document.activeElement).toBe(composer);
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    // The statusline region is gone. With the inspector default-closed the
    // cycle is rail → stage → rail, so F6 from the stage skips the closed inspector and wraps to rail.
    expect(regionOf(document.activeElement)).toBe("rail");

    fireEvent.click(getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    (
      getByTestId("sessions-rail").querySelector(
        "[data-focus-target]",
      ) as HTMLElement
    ).focus();
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    expect(regionOf(document.activeElement)).toBe("stage");
    fireEvent.keyDown(document.activeElement as Element, {
      key: "F6",
      code: "F6",
    });
    expect(regionOf(document.activeElement)).toBe("inspector");
  });

  it("persists a deliberate inspector opt-in across a remount", async () => {
    const first = render(<SessionsView active />);
    fireEvent.click(first.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        first
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    first.unmount();

    const second = render(<SessionsView active />);
    expect(
      second
        .getByTestId("sessions-toggle-inspector")
        .getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("keeps deliberate inspector intent across responsive collapse, narrow reload, and recovery", async () => {
    let rootWidth = 1400;
    const observers: Array<{
      callback: ResizeObserverCallback;
      active: boolean;
    }> = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        entry: { callback: ResizeObserverCallback; active: boolean };
        constructor(callback: ResizeObserverCallback) {
          this.entry = { callback, active: true };
          observers.push(this.entry);
        }
        observe() {}
        unobserve() {}
        disconnect() {
          this.entry.active = false;
        }
      },
    );
    const widthSpy = vi
      .spyOn(HTMLElement.prototype, "clientWidth", "get")
      .mockImplementation(function (this: HTMLElement) {
        if (this.getAttribute("data-testid") === "sessions-view")
          return rootWidth;
        if (this.getAttribute("data-testid") === "sessions-stage") return 900;
        return 0;
      });
    const resize = () => {
      act(() => {
        for (const observer of observers) {
          if (observer.active) observer.callback([], {} as ResizeObserver);
        }
      });
    };

    const first = render(<SessionsView active />);
    fireEvent.click(first.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        first
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "1",
    );

    rootWidth = 1000;
    resize();
    await waitFor(() =>
      expect(
        first
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "1",
    );
    first.unmount();

    const second = render(<SessionsView active />);
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "1",
    );

    rootWidth = 1400;
    resize();
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    expect(
      second.getByRole("separator", { name: "Resize inspector" }),
    ).not.toBeNull();

    rootWidth = 1000;
    resize();
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    // Reopenable below the threshold, then a deliberate close cancels width recovery.
    fireEvent.click(second.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    fireEvent.click(second.getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        second
          .getByTestId("sessions-toggle-inspector")
          .getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(window.localStorage.getItem("cockpit.chats.inspector-open.v1")).toBe(
      "0",
    );
    rootWidth = 1400;
    resize();
    expect(
      second
        .getByTestId("sessions-toggle-inspector")
        .getAttribute("aria-expanded"),
    ).toBe("false");
    widthSpy.mockRestore();
  });

  it("palette collapse restores an inspector invoker to the visible toggle", async () => {
    const { getByTestId } = render(<SessionsView active />);
    fireEvent.click(getByTestId("sessions-toggle-inspector"));
    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("true"),
    );
    const invoker = getByTestId("sessions-inspector").querySelector(
      "[data-focus-target]",
    ) as HTMLElement;
    invoker.focus();
    fireEvent.keyDown(invoker, { key: "k", code: "KeyK", ctrlKey: true });
    const input = getByTestId("sessions-palette-input");
    fireEvent.change(input, { target: { value: "inspector" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });

    await waitFor(() =>
      expect(
        getByTestId("sessions-toggle-inspector").getAttribute("aria-expanded"),
      ).toBe("false"),
    );
    expect(document.activeElement).toBe(
      getByTestId("sessions-toggle-inspector"),
    );
  });

  it("Shift+F6 cycles backward", async () => {
    // With the statusline region removed the cycle is rail → stage →
    // inspector. Land on the stage composer, then step BACKWARD to the rail (the previous region).
    seedReadyComposerSession();
    const { findByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content") as HTMLElement;
    composer.focus();
    fireEvent.keyDown(composer, {
      key: "F6",
      code: "F6",
      shiftKey: true,
    });
    expect(
      document.activeElement
        ?.closest("[data-region]")
        ?.getAttribute("data-region"),
    ).toBe("rail");
  });

  it("Esc from the composer lands on the stage header", async () => {
    seedReadyComposerSession();
    const { findByTestId, getByTestId } = render(<SessionsView active />);
    const composer = (
      await findByTestId("session-composer-editor")
    ).querySelector(".cm-content") as HTMLElement;
    composer.focus();
    fireEvent.keyDown(composer, { key: "Escape", code: "Escape" });
    const header = getByTestId("sessions-stage").querySelector(
      "[data-stage-header]",
    );
    expect(document.activeElement).toBe(header);
  });
});
