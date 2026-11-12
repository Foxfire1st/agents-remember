import type { RoleExecutionReceipt, RoleLauncherOptions } from "./roleLaunchModel";

// Local test catalog: deliberately different harness, mode and permission features.
export const stepUpOptions: RoleLauncherOptions = {
  roleDefaults: {
    agent: "codex", model: "base", mode: "auto", available: true, serviceTier: "fast",
    featureValues: { plan_mode: true },
    stepUp: { harness: "claude", model: "alternative" },
    choices: { mode: { source: "user settings" }, notes: { present: true, source: "user settings" } },
  },
  agents: [
    { id: "codex", label: "Codex", modes: [{ id: "auto", label: "Codex Auto" }], models: [
      { id: "base", label: "Base", isDefault: true, serviceTiers: [{ id: "priority", label: "Fast" }], features: [
        { id: "plan_mode", label: "Plan mode", type: "toggle", value: false, permissionBearing: true },
        { id: "service_tier", label: "Speed", type: "select", permissionBearing: false, options: [{ id: "priority", label: "Fast" }] },
      ] },
    ] },
    { id: "claude", label: "Claude", modes: [{ id: "default", label: "Claude Default" }], models: [
      { id: "alternative", label: "Alternative", isDefault: true, features: [
        { id: "auto_accept", label: "Auto accept", type: "toggle", value: false, permissionBearing: true },
      ] },
    ] },
  ],
  catalogOrigin: "local-component-test",
  executions: [],
};

export function stepUpReceipt(requestId: string): RoleExecutionReceipt {
  // Only the observed model/source is projected by today's receipt, not mode/features.
  return { status: "running", requestId, canStart: false, canRevive: false,
    stepUp: { model: "claude-observed", source: "user settings · configured step-up" } };
}