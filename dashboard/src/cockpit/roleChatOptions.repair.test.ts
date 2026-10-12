// Repair proof for L150-CODE-04: a settled options answer writes its execution to
// the failure reference only, never to the active execution authority. A newer
// Result receipt stays in charge when an older options reply settles later; the
// options answer's own execution value still reaches the launcher through the
// stored options, and saved request-scoped receipt adoption is untouched.
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { roleDocumentScope, roleOptionsScope, type RoleLaunchSelection } from "./roleLaunchModel";
import { useRoleChatOptionsLoader, type RoleChatOptionsLoaderArgs } from "./roleChatOptionsLoader";
import type { ActiveRequestsRef } from "./roleChatResult";

const SELECTION: RoleLaunchSelection = { role: "architect" };

function loaderArgs(over: Partial<RoleChatOptionsLoaderArgs> = {}): RoleChatOptionsLoaderArgs {
  return {
    active: true,
    boundSelection: undefined,
    selectionComplete: true,
    documentScope: roleDocumentScope(SELECTION),
    optionsScope: roleOptionsScope(SELECTION),
    optionsRefresh: 0,
    catalogRefresh: 0,
    bumpOptionsRefresh: vi.fn(),
    bumpCatalogRefresh: vi.fn(),
    currentSelectionRef: { current: SELECTION },
    activeRequestsRef: { current: {} } as ActiveRequestsRef,
    waitForLaunch: vi.fn(),
    setExecutionState: vi.fn(),
    setExecutionErrorState: vi.fn(),
    updateActiveRequest: vi.fn(),
    rememberExecution: vi.fn(),
    rememberFailureExecution: vi.fn(),
    fetchExecutionResult: vi.fn(async () => undefined),
    ...over,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("L150-CODE-04: options success writes failure reference only", () => {
  it("never overwrites the active execution with a delayed options execution", async () => {
    const parked: { answer: (value: Response) => void }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Promise<Response>((answer) => {
        parked.push({ answer });
      })),
    );
    const args = loaderArgs();
    renderHook((props: { args: RoleChatOptionsLoaderArgs }) => useRoleChatOptionsLoader(props.args), {
      initialProps: { args },
    });
    expect(parked).toHaveLength(1);
    const olderExecution = { status: "completed", requestId: "options-old-id" };
    await act(async () => {
      parked.splice(0).forEach((call) =>
        call.answer(
          new Response(
            JSON.stringify({
              roleDefaults: { agent: "codex", model: "gpt-a", available: true },
              agents: [],
              executions: [],
              execution: olderExecution,
            }),
            { status: 200 },
          ),
        ));
    });
    const setExecutionState = vi.mocked(args.setExecutionState);
    // The load-begin clearer is the only execution-state write: every call takes an
    // updater function, never the older options execution object.
    expect(setExecutionState).toHaveBeenCalled();
    for (const call of setExecutionState.mock.calls) {
      expect(typeof call[0]).toBe("function");
    }
    // The older execution lands on the failure reference only.
    expect(args.rememberFailureExecution).toHaveBeenCalledTimes(1);
    expect(args.rememberFailureExecution).toHaveBeenCalledWith(roleDocumentScope(SELECTION), olderExecution);
    expect(args.rememberExecution).not.toHaveBeenCalled();
  });

  it("still stores the options answer with its execution for the launcher (positive control)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(
        JSON.stringify({
          roleDefaults: { agent: "codex", model: "gpt-a", available: true },
          agents: [],
          executions: [],
          execution: { status: "completed", requestId: "options-old-id" },
        }),
        { status: 200 },
      )),
    );
    const args = loaderArgs();
    const { result } = renderHook((props: { args: RoleChatOptionsLoaderArgs }) => useRoleChatOptionsLoader(props.args), {
      initialProps: { args },
    });
    await act(async () => {});
    expect(result.current.optionsState?.value.execution).toEqual({
      status: "completed",
      requestId: "options-old-id",
    });
    expect(result.current.optionsErrorState).toBeNull();
  });
});
