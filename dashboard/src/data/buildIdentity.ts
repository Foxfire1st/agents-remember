import type { ServingBuild } from "../types/projection";

/** Build-input fingerprint embedded in the JavaScript currently executing in this tab. */
export const CLIENT_DASHBOARD_BUILD = __AR_DASHBOARD_BUILD__;

/** ``null`` means a legacy server did not advertise a comparable shipped-bundle identity. */
export function clientMatchesServingBuild(build: ServingBuild): boolean | null {
  return build.dashboardBuild === undefined
    ? null
    : build.dashboardBuild === CLIENT_DASHBOARD_BUILD;
}

/**
 * The header stamp's commit label: the short hash, suffixed `*` (the git-prompt dirty
 * convention) when the serving checkout carried uncommitted code at boot (a fix-round
 * daemon serves its base commit's hash while running different code) — the word `dirty`
 * lives in the stamp's tooltip so the one-line top bar keeps its width budget. ``null``
 * off-checkout — the version carries identity.
 */
export function servingCommitLabel(build: ServingBuild): string | null {
  if (build.commit === undefined) return null;
  return build.dirty ? `${build.commit}*` : build.commit;
}
