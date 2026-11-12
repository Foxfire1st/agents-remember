import { roleRequestKey, type RoleDefaults, type RoleDocumentScope } from "./roleLaunchModel";

// The last settled role defaults of each document scope this page has seen (MIK-R215 rule 4).
// They are AR's settings for the role, not part of Paseo's answer, so the saved catalog cannot
// carry them; while a fresh answer is on its way the launcher shows them beside the saved choices.
// The page keeps one entry per role and document it has opened, for as long as it is open.
const remembered = new Map<string, RoleDefaults>();

export function rememberRoleDefaults(scope: RoleDocumentScope, defaults: RoleDefaults): void {
  remembered.set(roleRequestKey(scope), defaults);
}

export function rememberedRoleDefaults(scope: RoleDocumentScope): RoleDefaults | undefined {
  return remembered.get(roleRequestKey(scope));
}

export function forgetRememberedRoleDefaults(): void {
  remembered.clear();
}
