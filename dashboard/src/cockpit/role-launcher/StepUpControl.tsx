import { launcherButton, launcherChip } from "../../grammar/viewTabLook";
import { roleDocumentScope, type RoleDefaults, type RoleLaunchSelection, type RoleScopedExecution } from "../roleLaunchModel";

/** One row control uses only the configured alternative, never permission overrides. */
export function StepUpControl({ defaults, selection, disabled, onLaunch }: {
  defaults: RoleDefaults;
  selection: RoleLaunchSelection;
  disabled: boolean;
  onLaunch: (selection: RoleLaunchSelection) => Promise<void>;
}) {
  if (!defaults.stepUp) return null;
  return <button type="button" aria-label="Step up role" title="Start the configured step-up with a reason" className={launcherButton({ selected: false })} disabled={disabled} onClick={() => {
    const reason = window.prompt("Why does this seat need its configured step-up? Give one sentence.");
    if (reason?.trim()) void onLaunch({ ...roleDocumentScope(selection), stepUp: reason.trim() });
  }}>Step up</button>;
}

export function StepUpStatus({ execution }: { execution?: RoleScopedExecution }) {
  if (!execution?.stepUp) return null;
  return <>
    <span className={launcherChip} title={execution.stepUp.source}>Stepped-up · {execution.stepUp.model ?? "host default"}</span>
    <span className={launcherChip} title="The step-up receipt reports the model, not applied mode, features or user instructions. Base previews are not applied-choice claims.">Mode/features unreported</span>
  </>;
}
