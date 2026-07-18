import type { ServingBuild } from "../types/projection";

/** Build-input fingerprint embedded in the JavaScript currently executing in this tab. */
export const CLIENT_DASHBOARD_BUILD = __AR_DASHBOARD_BUILD__;

/** ``null`` means a legacy server did not advertise a comparable shipped-bundle identity. */
export function clientMatchesServingBuild(build: ServingBuild): boolean | null {
  return build.dashboardBuild === undefined
    ? null
    : build.dashboardBuild === CLIENT_DASHBOARD_BUILD;
}
