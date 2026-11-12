// Repair proof for L150-CODE-05: the scope memos key on document content, not
// object identity. Fresh document-ref objects with identical repository/path keep
// memo-identical scopes (no extra options load); any actual field change —
// including the agent id — recomputes exactly the scope it belongs to.
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useRoleChatScopes } from "./roleChatScopes";
import type { RoleLaunchSelection } from "./roleLaunchModel";

const SPRINT = { repository: "repo", path: "sprint/task.json" };
const MASTER = { repository: "repo", path: "master/task.json" };

function selection(over: Partial<RoleLaunchSelection> = {}): RoleLaunchSelection {
  return {
    role: "manager",
    sprintDocumentRef: { ...SPRINT },
    masterDocumentRef: { ...MASTER },
    ...over,
  };
}

function hookProps(value: RoleLaunchSelection) {
  return { selection: value };
}

describe("L150-CODE-05: scope memos key on content", () => {
  it("keeps scope identities across equal-content ref replacement", () => {
    const { result, rerender } = renderHook(
      (props: { selection: RoleLaunchSelection }) => useRoleChatScopes(props.selection),
      { initialProps: hookProps(selection()) },
    );
    const first = result.current;
    rerender(hookProps(selection()));
    expect(result.current.documentScope).toBe(first.documentScope);
    expect(result.current.optionsScope).toBe(first.optionsScope);
    expect(result.current.documentScope).toEqual(first.documentScope);
  });

  it("ignores a feature-values-only override change", () => {
    const { result, rerender } = renderHook(
      (props: { selection: RoleLaunchSelection }) => useRoleChatScopes(props.selection),
      {
        initialProps: hookProps(
          selection({ agentOverride: { agentId: "codex", featureValues: { service_tier: "default" } } }),
        ),
      },
    );
    const first = result.current;
    rerender(
      hookProps(
        selection({ agentOverride: { agentId: "codex", featureValues: { service_tier: "priority" } } }),
      ),
    );
    expect(result.current.documentScope).toBe(first.documentScope);
    expect(result.current.optionsScope).toBe(first.optionsScope);
  });

  it("recomputes both scopes when any document field changes", () => {
    const { result, rerender } = renderHook(
      (props: { selection: RoleLaunchSelection }) => useRoleChatScopes(props.selection),
      { initialProps: hookProps(selection()) },
    );
    const first = result.current;
    rerender(hookProps(selection({ masterDocumentRef: { repository: "repo", path: "other/task.json" } })));
    expect(result.current.documentScope).not.toBe(first.documentScope);
    expect(result.current.optionsScope).not.toBe(first.optionsScope);
    expect(result.current.documentScope.masterDocumentRef).toEqual({ repository: "repo", path: "other/task.json" });
  });

  it("recomputes only the options scope when the agent id changes", () => {
    const { result, rerender } = renderHook(
      (props: { selection: RoleLaunchSelection }) => useRoleChatScopes(props.selection),
      { initialProps: hookProps(selection({ agentOverride: { agentId: "codex" } })) },
    );
    const first = result.current;
    expect(first.optionsScope.agentId).toBe("codex");
    rerender(hookProps(selection({ agentOverride: { agentId: "other" } })));
    expect(result.current.documentScope).toBe(first.documentScope);
    expect(result.current.optionsScope).not.toBe(first.optionsScope);
    expect(result.current.optionsScope.agentId).toBe("other");
  });

  it("recomputes both scopes when the role changes", () => {
    const { result, rerender } = renderHook(
      (props: { selection: RoleLaunchSelection }) => useRoleChatScopes(props.selection),
      { initialProps: hookProps(selection()) },
    );
    const first = result.current;
    rerender(hookProps(selection({ role: "worker" })));
    expect(result.current.documentScope).not.toBe(first.documentScope);
    expect(result.current.optionsScope).not.toBe(first.optionsScope);
  });
});
