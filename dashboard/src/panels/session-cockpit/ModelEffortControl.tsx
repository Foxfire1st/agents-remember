import { useEffect, useState } from "react";
import { Button, Dialog, DialogTrigger, Popover } from "react-aria-components";

import { css } from "../../../styled-system/css";
import {
  deriveEffortMenu,
  effectiveSelection,
  visibleModelRows,
} from "../../data/sessionCapabilities";
import type { PerSessionCockpit, SnapshotErrorInfo } from "../../data/sessionCockpitStore";
import type { OpenSession } from "../../data/sessions";
import type { CapabilitySnapshotWire } from "../../types/harnessCapabilities";
import {
  acknowledgeSetAttention,
  refreshSessionSnapshot,
  sendSet,
  startPairChangeFlow,
} from "../../data/setClient";
import { deriveSetChips } from "../../data/setChips";
import {
  NO_EFFORT_CONTROL_COPY,
  NO_SELECTED_MODEL_COPY,
  snapshotErrorCopy,
} from "../../data/setControlsCopy";
import { AcceptanceChip } from "./AcceptanceChip";

// The ModelEffortControl (design §6): ONE control, mounted into the
// HeaderStrip slot, with the palette exposing the same actions. HONESTY INVARIANTS:
//   - options come from the EXACT-SESSION snapshot only (data/sessionCapabilities — never the
//     pre-session harness cache); the popover open always re-GETs.
//   - a GET failure renders the control DISABLED with the verbatim error + retry — never
//     an empty menu; a 409 names 'no native protocol control endpoint' as the disable reason.
//   - the effort menu is the selected row's sessionSettable options (native order); a row
//     without options says 'no effort control for this model'; keys verbatim (Pi provider/id).
//   - the trigger shows the model and effort the session is RUNNING — the freshest of
//     echo/snapshot evidence with the launch-resolved value as the fallback (one plain pair,
//     no provenance chrome; problems surface through the error/chip paths).
//   - requested ≠ effective everywhere: staging is a REQUEST; the marker moves only on evidence
//     (the chips row renders data/setChips — the same models every surface reads).
//   - selecting a new model re-gates the menu and pre-highlights that row's defaultEffort;
//     applying model+effort together runs the SERIALIZED pair flow.

const slotRow = css({ display: "inline-flex", alignItems: "baseline", gap: "0.3rem", minWidth: "0" });
const trigger = css({
  font: "inherit",
  fontSize: "0.7rem",
  display: "inline-flex",
  alignItems: "baseline",
  gap: "0.3rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.05rem",
  background: "transparent",
  color: "ink",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  whiteSpace: "nowrap",
  _hover: { borderColor: "amber", color: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const popover = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  boxShadow: "0 10px 40px oklch(0 0 0 / 0.5)",
  padding: "0.6rem 0.7rem",
  width: "min(440px, 92vw)",
  maxHeight: "70vh",
  overflowY: "auto",
  fontSize: "0.74rem",
  color: "muted",
});
const dialogBody = css({ display: "grid", gap: "0.5rem", outline: "none" });
const heading = css({
  fontSize: "0.64rem",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "cyan",
});
const optionList = css({ display: "flex", flexWrap: "wrap", gap: "0.3rem" });
const optionButton = css({
  font: "inherit",
  fontSize: "0.72rem",
  paddingInline: "0.45rem",
  paddingBlock: "0.12rem",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  "&[aria-pressed='true']": { color: "amber", borderColor: "amber" },
  "&[data-effective='true']": { color: "mint", borderColor: "mint" },
  "&[data-prehighlight='true']": { borderStyle: "dashed", borderColor: "cyan" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const noteLine = css({ fontSize: "0.68rem", color: "muted" });
const errorLine = css({ fontSize: "0.7rem", color: "alarm", whiteSpace: "pre-wrap", overflowWrap: "anywhere" });
const footerRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.4rem",
});
const applyButton = css({
  font: "inherit",
  fontSize: "0.72rem",
  paddingInline: "0.55rem",
  paddingBlock: "0.15rem",
  background: "transparent",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  cursor: "pointer",
  _disabled: { opacity: 0.5, cursor: "not-allowed" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const quietButton = css({
  font: "inherit",
  fontSize: "0.68rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.1rem",
  background: "transparent",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const chipsRow = css({ display: "inline-flex", gap: "0.25rem", minWidth: "0", overflow: "hidden" });

function isLive(session: OpenSession): boolean {
  return (session.status ?? "running") === "running";
}

function applyStagedPair(
  sessionId: string,
  stagedModel: string | null,
  stagedEffort: string | null,
  modelChanged: boolean,
  effortChanged: boolean,
  setOpen: (open: boolean) => void,
): void {
  if (modelChanged && effortChanged && stagedModel && stagedEffort) {
    startPairChangeFlow(sessionId, stagedModel, stagedEffort);
  } else if (modelChanged && stagedModel) {
    void sendSet(sessionId, "model", stagedModel);
  } else if (effortChanged && stagedEffort) {
    void sendSet(sessionId, "effort", stagedEffort);
  }
  setOpen(false);
}

function modelEffortWords(
  effective: ReturnType<typeof effectiveSelection>,
  session: OpenSession,
): { modelWord: string; effortWord: string | null } {
  return {
    modelWord: effective.modelKey ?? session.resolvedModel ?? "model —",
    effortWord: effective.effort ?? session.resolvedEffort ?? null,
  };
}

function menuModelKeyFor(
  stagedModel: string | null,
  snapshot: CapabilitySnapshotWire | undefined,
): string | null {
  return stagedModel ?? snapshot?.selectedModelKey ?? null;
}

function stagedEffortChanged(
  stagedEffort: string | null,
  modelChanged: boolean,
  effectiveEffort: string | null,
): boolean {
  return stagedEffort !== null && (modelChanged || stagedEffort !== effectiveEffort);
}

function useModelEffortDerived(
  session: OpenSession,
  cockpit: PerSessionCockpit | undefined,
  stagedModel: string | null,
  stagedEffort: string | null,
): {
  live: boolean;
  effective: ReturnType<typeof effectiveSelection>;
  snapshot: CapabilitySnapshotWire | undefined;
  snapshotError: SnapshotErrorInfo | undefined;
  modelWord: string;
  effortWord: string | null;
  sourceWord: string;
  menuModelKey: string | null;
  menu: ReturnType<typeof deriveEffortMenu> | null;
  modelChanged: boolean;
  effortChanged: boolean;
} {
  const live = isLive(session);
  const effective = effectiveSelection(cockpit);
  const snapshot = cockpit?.liveSnapshot?.payload;
  const snapshotError = cockpit?.snapshotError;
  const { modelWord, effortWord } = modelEffortWords(effective, session);
  const sourceWord =
    effective.modelSource === "none"
      ? "requested at launch — no readback yet"
      : `source: ${effective.modelSource}`;
  const menuModelKey = menuModelKeyFor(stagedModel, snapshot);
  const menu = snapshot ? deriveEffortMenu(snapshot, menuModelKey) : null;
  const modelChanged = stagedModel !== null && stagedModel !== effective.modelKey;
  const effortChanged = stagedEffortChanged(stagedEffort, modelChanged, effective.effort);
  return {
    live,
    effective,
    snapshot,
    snapshotError,
    modelWord,
    effortWord,
    sourceWord,
    menuModelKey,
    menu,
    modelChanged,
    effortChanged,
  };
}

function useModelEffortPopover(
  isOpen: boolean,
  sessionId: string,
  setStagedModel: (model: string | null) => void,
  setStagedEffort: (effort: string | null) => void,
): void {
  // Staged REQUESTS (never markers): reset per popover open and per session.
  useEffect(() => {
    setStagedModel(null);
    setStagedEffort(null);
  }, [isOpen, sessionId, setStagedModel, setStagedEffort]);

  // Popover open re-GETs the exact-session snapshot — the ONLY options source.
  useEffect(() => {
    if (isOpen) void refreshSessionSnapshot(sessionId);
  }, [isOpen, sessionId]);
}

function ModelEffortTrigger({
  modelWord,
  effortWord,
  sourceWord,
  effective,
}: {
  modelWord: string;
  effortWord: string | null;
  sourceWord: string;
  effective: ReturnType<typeof effectiveSelection>;
}) {
  return (
    <Button
      className={trigger}
      data-testid="model-effort-trigger"
      aria-label={`model and effort control — ${modelWord}${effortWord ? ` · ${effortWord}` : ""}`}
      aria-description={sourceWord}
      data-effective-source={effective.modelSource}
    >
      <span aria-hidden="true">⚙</span>
      <span data-testid="model-effort-trigger-model">{modelWord}</span>
      {effortWord ? (
        <>
          <span aria-hidden="true">·</span>
          <span data-testid="model-effort-trigger-effort">{effortWord}</span>
        </>
      ) : null}
    </Button>
  );
}

function SnapshotErrorBlock({
  snapshotError,
  session,
}: {
  snapshotError: SnapshotErrorInfo;
  session: OpenSession;
}) {
  return (
    <>
      <span className={heading}>control unavailable</span>
      <span className={errorLine} data-testid="model-effort-error">
        {snapshotErrorCopy(snapshotError)}
      </span>
      <span>
        <Button
          className={quietButton}
          onPress={() => void refreshSessionSnapshot(session.id)}
          data-testid="model-effort-retry"
        >
          retry
        </Button>
      </span>
    </>
  );
}

function ModelOptions({
  snapshot,
  effective,
  stagedModel,
  setStagedModel,
  setStagedEffort,
}: {
  snapshot: CapabilitySnapshotWire;
  effective: ReturnType<typeof effectiveSelection>;
  stagedModel: string | null;
  setStagedModel: (model: string | null) => void;
  setStagedEffort: (effort: string | null) => void;
}) {
  return (
    <>
      <span className={heading}>model</span>
      {snapshot.selectedModelKey === null ? (
        <span className={noteLine} data-testid="model-effort-no-selected-model">
          {NO_SELECTED_MODEL_COPY}
        </span>
      ) : null}
      <div className={optionList} data-testid="model-effort-models">
        {visibleModelRows(snapshot).map((row) => (
          <Button
            key={row.key}
            className={optionButton}
            aria-pressed={stagedModel === row.key}
            data-effective={effective.modelKey === row.key ? "true" : undefined}
            data-testid={`model-option-${row.key}`}
            onPress={() => {
              setStagedModel(row.key === effective.modelKey ? null : row.key);
              // Re-gate: a staged effort from another row's menu never survives.
              setStagedEffort(null);
            }}
          >
            {row.displayName}
            {effective.modelKey === row.key ? " ●" : ""}
          </Button>
        ))}
      </div>
    </>
  );
}

function EffortOptions({
  menu,
  session,
  effective,
  menuModelKey,
  stagedEffort,
  setStagedEffort,
  modelChanged,
}: {
  menu: ReturnType<typeof deriveEffortMenu> | null;
  session: OpenSession;
  effective: ReturnType<typeof effectiveSelection>;
  menuModelKey: string | null;
  stagedEffort: string | null;
  setStagedEffort: (effort: string | null) => void;
  modelChanged: boolean;
}) {
  if (!menu || menu.kind === "no-selected-model") {
    return (
      <span className={noteLine} data-testid="effort-menu-no-model">
        {NO_SELECTED_MODEL_COPY}
      </span>
    );
  }
  if (menu.kind === "no-effort-control") {
    return (
      <span className={noteLine} data-testid="effort-menu-none">
        {NO_EFFORT_CONTROL_COPY}
      </span>
    );
  }
  return (
    <div className={optionList} data-testid="model-effort-efforts">
      {menu.options.map((option) => {
        const isEffective =
          menuModelKey === effective.modelKey &&
          (effective.effort ?? session.resolvedEffort) === option.key;
        return (
          <Button
            key={option.key}
            className={optionButton}
            aria-pressed={stagedEffort === option.key}
            data-effective={isEffective ? "true" : undefined}
            data-prehighlight={
              modelChanged && stagedEffort === null && menu.defaultEffort === option.key
                ? "true"
                : undefined
            }
            data-testid={`effort-option-${option.key}`}
            onPress={() => setStagedEffort(stagedEffort === option.key ? null : option.key)}
          >
            {option.displayName}
            {isEffective ? " ●" : ""}
          </Button>
        );
      })}
    </div>
  );
}

function ApplyFooter({
  modelChanged,
  effortChanged,
  onApply,
  onCancel,
  snapshotLoading,
}: {
  modelChanged: boolean;
  effortChanged: boolean;
  onApply: () => void;
  onCancel: () => void;
  snapshotLoading: boolean | undefined;
}) {
  return (
    <div className={footerRow}>
      <Button
        className={applyButton}
        isDisabled={!modelChanged && !effortChanged}
        onPress={onApply}
        data-testid="model-effort-apply"
      >
        {modelChanged && effortChanged
          ? "apply model + effort (serialized)"
          : modelChanged
            ? "apply model"
            : effortChanged
              ? "apply effort"
              : "apply"}
      </Button>
      <Button className={quietButton} onPress={onCancel} data-testid="model-effort-cancel">
        cancel
      </Button>
      {snapshotLoading ? (
        <span className={noteLine} data-testid="model-effort-refreshing">
          refreshing snapshot…
        </span>
      ) : null}
    </div>
  );
}

function ChipsRow({
  session,
  cockpit,
  chips,
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  chips: ReturnType<typeof deriveSetChips>;
}) {
  return (
    <span className={chipsRow} data-testid="model-effort-chips">
      {chips.map((chip) => (
        <AcceptanceChip
          key={chip.id}
          chip={chip}
          onAcknowledge={
            chip.demandsAck ? () => acknowledgeSetAttention(session.id) : undefined
          }
          onRetry={
            chip.retryable && cockpit?.setRouteError
              ? () => {
                  const error = cockpit.setRouteError;
                  if (error) void sendSet(session.id, error.kind, error.requestedValue);
                }
              : undefined
          }
        />
      ))}
    </span>
  );
}

function ModelEffortSurface({
  isOpen,
  setOpen,
  session,
  cockpit,
  stagedModel,
  setStagedModel,
  stagedEffort,
  setStagedEffort,
  chips,
  derived,
  apply,
}: {
  isOpen: boolean;
  setOpen: (open: boolean) => void;
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  stagedModel: string | null;
  setStagedModel: (model: string | null) => void;
  stagedEffort: string | null;
  setStagedEffort: (effort: string | null) => void;
  chips: ReturnType<typeof deriveSetChips>;
  derived: ReturnType<typeof useModelEffortDerived>;
  apply: () => void;
}) {
  const {
    effective,
    snapshot,
    snapshotError,
    modelWord,
    effortWord,
    sourceWord,
    menuModelKey,
    menu,
    modelChanged,
    effortChanged,
  } = derived;
  return (
    <>
      <DialogTrigger isOpen={isOpen} onOpenChange={setOpen}>
        <ModelEffortTrigger
          modelWord={modelWord}
          effortWord={effortWord}
          sourceWord={sourceWord}
          effective={effective}
        />
        <ModelEffortDialog
          snapshotError={snapshotError}
          snapshot={snapshot}
          session={session}
          effective={effective}
          stagedModel={stagedModel}
          setStagedModel={setStagedModel}
          setStagedEffort={setStagedEffort}
          menu={menu}
          menuModelKey={menuModelKey}
          stagedEffort={stagedEffort}
          modelChanged={modelChanged}
          effortChanged={effortChanged}
          onApply={apply}
          onCancel={() => setOpen(false)}
          snapshotLoading={cockpit?.snapshotLoading}
        />
      </DialogTrigger>
      <ChipsRow session={session} cockpit={cockpit} chips={chips} />
    </>
  );
}

function ModelEffortDialog({
  snapshotError,
  snapshot,
  session,
  effective,
  stagedModel,
  setStagedModel,
  setStagedEffort,
  menu,
  menuModelKey,
  stagedEffort,
  modelChanged,
  effortChanged,
  onApply,
  onCancel,
  snapshotLoading,
}: {
  snapshotError: SnapshotErrorInfo | undefined;
  snapshot: CapabilitySnapshotWire | undefined;
  session: OpenSession;
  effective: ReturnType<typeof effectiveSelection>;
  stagedModel: string | null;
  setStagedModel: (model: string | null) => void;
  setStagedEffort: (effort: string | null) => void;
  menu: ReturnType<typeof deriveEffortMenu> | null;
  menuModelKey: string | null;
  stagedEffort: string | null;
  modelChanged: boolean;
  effortChanged: boolean;
  onApply: () => void;
  onCancel: () => void;
  snapshotLoading: boolean | undefined;
}) {
  return (
    <Popover className={popover} placement="bottom start">
      <Dialog className={dialogBody} aria-label="Model and effort control">
        {snapshotError ? (
          <SnapshotErrorBlock snapshotError={snapshotError} session={session} />
        ) : !snapshot ? (
          <span className={noteLine} data-testid="model-effort-loading">
            loading exact-session capabilities…
          </span>
        ) : (
          <>
            <ModelOptions
              snapshot={snapshot}
              effective={effective}
              stagedModel={stagedModel}
              setStagedModel={setStagedModel}
              setStagedEffort={setStagedEffort}
            />
            <span className={heading}>effort</span>
            <EffortOptions
              menu={menu}
              session={session}
              effective={effective}
              menuModelKey={menuModelKey}
              stagedEffort={stagedEffort}
              setStagedEffort={setStagedEffort}
              modelChanged={modelChanged}
            />
            <ApplyFooter
              modelChanged={modelChanged}
              effortChanged={effortChanged}
              onApply={onApply}
              onCancel={onCancel}
              snapshotLoading={snapshotLoading}
            />
          </>
        )}
      </Dialog>
    </Popover>
  );
}

export function ModelEffortControl({
  session,
  cockpit,
  open,
  onOpenChange,
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  /** Controlled open state (the palette commands open the same popover); optional. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = open ?? internalOpen;
  const setOpen = (next: boolean) => {
    setInternalOpen(next);
    onOpenChange?.(next);
  };
  const [stagedModel, setStagedModel] = useState<string | null>(null);
  const [stagedEffort, setStagedEffort] = useState<string | null>(null);
  useModelEffortPopover(isOpen, session.id, setStagedModel, setStagedEffort);
  const chips = deriveSetChips(cockpit);
  const {
    live,
    effective,
    snapshot,
    snapshotError,
    modelWord,
    effortWord,
    sourceWord,
    menuModelKey,
    menu,
    modelChanged,
    effortChanged,
  } = useModelEffortDerived(session, cockpit, stagedModel, stagedEffort);

  if (!session.harness || !live) return null;
  const apply = () =>
    applyStagedPair(session.id, stagedModel, stagedEffort, modelChanged, effortChanged, setOpen);

  return (
    <span className={slotRow} data-testid="model-effort-control">
      <ModelEffortSurface
        isOpen={isOpen}
        setOpen={setOpen}
        session={session}
        cockpit={cockpit}
        stagedModel={stagedModel}
        setStagedModel={setStagedModel}
        stagedEffort={stagedEffort}
        setStagedEffort={setStagedEffort}
        chips={chips}
        derived={{
          live,
          effective,
          snapshot,
          snapshotError,
          modelWord,
          effortWord,
          sourceWord,
          menuModelKey,
          menu,
          modelChanged,
          effortChanged,
        }}
        apply={apply}
      />
    </span>
  );
}
