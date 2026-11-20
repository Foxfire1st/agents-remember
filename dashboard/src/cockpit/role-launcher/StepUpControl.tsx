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

// The mode the receipt reports for the launched seat, never the one that was asked for: a
// receipt that names no observed mode says so instead of repeating the request.
function reportedMode(execution: RoleScopedExecution): { value: string; source?: string } | null {
  // role_launch_choices writes the observation to the receipt's top-level `choices`, where the
  // start answer and the stored receipt carry it; `sessionOptions` holds only what was asked for.
  const choice = execution.choices?.mode ?? execution.sessionOptions?.choices?.mode;
  const observed = choice?.observedValue;
  if (!observed) return null;
  return { value: observed, source: choice?.source ?? undefined };
}

export function StepUpStatus({ execution }: { execution?: RoleScopedExecution }) {
  if (!execution?.stepUp) return null;
  const mode = reportedMode(execution);
  return <>
    <span className={launcherChip} title={execution.stepUp.source}>Stepped-up · {execution.stepUp.model ?? "host default"}</span>
    {mode
      ? <span className={launcherChip} title={`The receipt reports the mode the host applied${mode.source ? `, from ${mode.source}` : ""}. Base previews are not applied-choice claims.`}>Mode · {mode.value}</span>
      : <span className={launcherChip} title="The step-up receipt reports the model, not an observed mode, features or user instructions. Base previews are not applied-choice claims.">Mode/features unreported</span>}
  </>;
}
