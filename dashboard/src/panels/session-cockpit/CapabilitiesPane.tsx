import { css } from "../../../styled-system/css";
import {
  cacheStatusNote,
  capabilityCostNote,
  capabilityLoadingCopy,
  fetchHarnessCapabilities,
  type PerHarnessCapabilities,
  useCapabilityCatalog,
} from "../../data/capabilityCatalog";
import {
  deriveEffortMenu,
  effectiveSelection,
  modelRowByKey,
} from "../../data/sessionCapabilities";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { refreshSessionSnapshot } from "../../data/setClient";
import { EFFORT_NOT_ECHOED_COPY } from "../../data/setControlsCopy";
import type {
  CapabilityEnvelope,
  CapabilitySnapshotWire,
  ModelCapabilityWire,
} from "../../types/harnessCapabilities";
import {
  InspectorFact,
  InspectorNote,
  InspectorSection,
  inspectorAction,
  inspectorPane,
} from "./InspectorPrimitives";
import { VirtualizedInspectorList } from "./VirtualizedInspectorList";

// Read-only capability inspection keeps the two authorities separate: the live section is the
// exact-session snapshot; the pre-session section is the harness cache envelope. Refresh buttons
// call those existing reads explicitly and name native-discovery cost without time estimates.

const EMPTY_HARNESS: PerHarnessCapabilities = { fetchState: "idle" };
const modelRow = css({ display: "grid", gap: "0.12rem", minWidth: "0" });
const modelLead = css({ color: "ink", fontWeight: "600" });
const modelMeta = css({ color: "muted", overflowWrap: "anywhere" });
const actions = css({ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem" });

function modelFlags(model: ModelCapabilityWire): string {
  return [
    model.selectable ? "selectable" : "not selectable",
    model.hidden ? "hidden" : "visible",
    model.supportsEffort ? "supports effort" : "no effort support",
    model.provider ? `provider ${model.provider}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function CapabilityModelRow({
  model,
  selected,
}: {
  model: ModelCapabilityWire;
  selected: boolean;
}) {
  const efforts = model.effortOptions.map(
    (effort) =>
      `${effort.key} (${[
        effort.launchSettable ? "launch" : null,
        effort.sessionSettable ? "session" : null,
      ]
        .filter(Boolean)
        .join("+") || "read-only"})`,
  );
  return (
    <div className={modelRow}>
      <span className={modelLead}>
        {selected ? "current · " : ""}
        {model.displayName} · {model.key}
      </span>
      <span className={modelMeta}>{modelFlags(model)}</span>
      <span className={modelMeta}>
        resolved {model.resolvedModel ?? "—"} · default effort {model.defaultEffort ?? "—"}
      </span>
      <span className={modelMeta}>
        efforts {efforts.length > 0 ? efforts.join(" · ") : "— (none advertised for this model)"}
      </span>
      {model.description ? <span className={modelMeta}>{model.description}</span> : null}
    </div>
  );
}

export function CapabilitiesPane({
  session,
  cockpit,
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
}) {
  const harness = session.harness;
  const catalog = useCapabilityCatalog((state) =>
    harness ? (state.perHarness[harness] ?? EMPTY_HARNESS) : EMPTY_HARNESS,
  );
  const snapshot = cockpit?.liveSnapshot?.payload;
  const selection = effectiveSelection(cockpit);
  const effortMenu = snapshot ? deriveEffortMenu(snapshot) : undefined;
  const selectedModel = snapshot
    ? modelRowByKey(snapshot, snapshot.selectedModelKey)
    : undefined;
  const effortNotEchoed =
    selection.effort === null && effortMenu?.kind === "menu" && effortMenu.selected === null;
  const preSession = catalog.envelope;

  return (
    <div className={inspectorPane} data-testid="capabilities-pane">
      <LiveSnapshotSection
        session={session}
        cockpit={cockpit}
        snapshot={snapshot}
        selection={selection}
        effortMenu={effortMenu}
        effortNotEchoed={effortNotEchoed}
        selectedModel={selectedModel}
      />
      <PreSessionSection harness={harness} catalog={catalog} preSession={preSession} />
    </div>
  );
}

function LiveSnapshotSection({
  session,
  cockpit,
  snapshot,
  selection,
  effortMenu,
  effortNotEchoed,
  selectedModel,
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
  snapshot: CapabilitySnapshotWire | undefined;
  selection: ReturnType<typeof effectiveSelection>;
  effortMenu: ReturnType<typeof deriveEffortMenu> | undefined;
  effortNotEchoed: boolean;
  selectedModel: ReturnType<typeof modelRowByKey> | undefined;
}) {
  return (
    <InspectorSection title="Live exact-session snapshot">
      <SnapshotRefreshRow session={session} cockpit={cockpit} />
      {cockpit?.snapshotError ? (
        <InspectorNote testId="capabilities-live-error">
          {cockpit.snapshotError.status} · HTTP {cockpit.snapshotError.httpStatus ?? "—"} ·{" "}
          {cockpit.snapshotError.detail}
        </InspectorNote>
      ) : null}
      {snapshot ? (
        <SnapshotFacts
          snapshot={snapshot}
          selection={selection}
          effortMenu={effortMenu}
          effortNotEchoed={effortNotEchoed}
          selectedModel={selectedModel}
        />
      ) : cockpit?.snapshotLoading ? (
        <InspectorNote>Refreshing the exact-session snapshot.</InspectorNote>
      ) : (
        <InspectorNote>No exact-session capability snapshot has been read for this seat.</InspectorNote>
      )}
    </InspectorSection>
  );
}

function PreSessionSection({
  harness,
  catalog,
  preSession,
}: {
  harness: string | undefined;
  catalog: PerHarnessCapabilities;
  preSession: CapabilityEnvelope | undefined;
}) {
  return (
    <InspectorSection title="Pre-session harness envelope">
      {harness ? (
        <>
          <PreSessionRefreshRow harness={harness} catalog={catalog} />
          <InspectorNote testId="capabilities-refresh-cost">
            Refresh {capabilityCostNote(harness)}. No duration is assumed.
          </InspectorNote>
          <PreSessionNotes harness={harness} catalog={catalog} />
          {preSession ? (
            <>
              <InspectorFact
                label="cache status"
                value={`${preSession.cacheStatus} — ${cacheStatusNote(preSession.cacheStatus)}`}
                testId="capabilities-cache-status"
              />
              <InspectorFact
                label="install fingerprint"
                value={preSession.installFingerprint}
                title={preSession.installFingerprint}
                testId="capabilities-install-fingerprint"
              />
              <InspectorFact label="models" value={preSession.capabilities.models.length} />
            </>
          ) : catalog.fetchState === "idle" ? (
            <InspectorNote>
              No pre-session envelope is loaded for {harness}; explicit refresh runs native
              discovery.
            </InspectorNote>
          ) : null}
        </>
      ) : (
        <InspectorNote>This seat has no harness key, so no pre-session cache key exists.</InspectorNote>
      )}
    </InspectorSection>
  );
}

function SnapshotRefreshRow({
  session,
  cockpit,
}: {
  session: OpenSession;
  cockpit: PerSessionCockpit | undefined;
}) {
  return (
    <div className={actions}>
      <button
        type="button"
        className={inspectorAction}
        disabled={cockpit?.snapshotLoading ?? false}
        onClick={() => void refreshSessionSnapshot(session.id)}
        data-testid="capabilities-live-refresh"
      >
        {cockpit?.snapshotLoading ? "refreshing live snapshot…" : "refresh live snapshot"}
      </button>
      {cockpit?.liveSnapshot ? (
        <span>fetched {cockpit.liveSnapshot.fetchedAt}</span>
      ) : null}
    </div>
  );
}

function SnapshotFacts({
  snapshot,
  selection,
  effortMenu,
  effortNotEchoed,
  selectedModel,
}: {
  snapshot: CapabilitySnapshotWire;
  selection: ReturnType<typeof effectiveSelection>;
  effortMenu: ReturnType<typeof deriveEffortMenu> | undefined;
  effortNotEchoed: boolean;
  selectedModel: ModelCapabilityWire | undefined;
}) {
  return (
    <>
      <InspectorFact
        label="selected model"
        value={`${selection.modelKey ?? "—"} (${selection.modelSource})`}
        testId="capabilities-selected-model"
      />
      <InspectorFact
        label="selected effort"
        value={
          effortNotEchoed
            ? `${EFFORT_NOT_ECHOED_COPY} — a live model-gated menu exists; no current effort was echoed`
            : `${selection.effort ?? "—"} (${selection.effortSource})`
        }
        testId="capabilities-selected-effort"
      />
      {effortMenu?.kind === "no-effort-control" ? (
        <InspectorNote>No effort control for model {effortMenu.modelKey}.</InspectorNote>
      ) : null}
      {effortMenu?.kind === "no-selected-model" ? (
        <InspectorNote>No selected model was echoed, so no effort menu can be gated.</InspectorNote>
      ) : null}
      {selectedModel ? (
        <InspectorFact
          label="current row"
          value={`${selectedModel.displayName} · ${selectedModel.key}`}
        />
      ) : null}
      <VirtualizedInspectorList
        rows={snapshot.models}
        rowKey={(model) => model.key}
        renderRow={(model) => (
          <CapabilityModelRow
            model={model}
            selected={model.key === snapshot.selectedModelKey}
          />
        )}
        label="Live exact-session model capabilities"
        testId="capabilities-live-models"
      />
    </>
  );
}

function PreSessionRefreshRow({
  harness,
  catalog,
}: {
  harness: string;
  catalog: PerHarnessCapabilities;
}) {
  return (
    <div className={actions}>
      <button
        type="button"
        className={inspectorAction}
        disabled={catalog.fetchState === "loading" || catalog.fetchState === "refreshing"}
        onClick={() => void fetchHarnessCapabilities(harness, { refresh: true })}
        data-testid="capabilities-catalog-refresh"
      >
        {catalog.fetchState === "loading" || catalog.fetchState === "refreshing"
          ? "refreshing pre-session catalog…"
          : "refresh pre-session catalog"}
      </button>
    </div>
  );
}

function PreSessionNotes({
  harness,
  catalog,
}: {
  harness: string;
  catalog: PerHarnessCapabilities;
}) {
  if (catalog.fetchState === "loading" || catalog.fetchState === "refreshing") {
    return (
      <InspectorNote>
        {capabilityLoadingCopy(
          harness,
          catalog.fetchState === "refreshing" ? "refresh" : "initial",
        )}
      </InspectorNote>
    );
  }
  if (catalog.error) {
    return (
      <InspectorNote testId="capabilities-catalog-error">
        {catalog.error.status} · HTTP {catalog.error.httpStatus ?? "—"} · {catalog.error.detail}
      </InspectorNote>
    );
  }
  return null;
}
