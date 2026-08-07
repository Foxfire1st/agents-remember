import { useCallback, useEffect, useRef, useState } from "react";

import { css } from "../../../styled-system/css";
import { servedAgeSeconds, useNowMs } from "../../data/servedAges";
import type { OpenSession } from "../../data/sessions";
import type { AgentPickupNode, SupervisorHeartbeat } from "../../types/projection";
import {
  BusDeveloperReply,
  EMPTY_BUS_REPLY_STATE,
  type BusReplyState,
} from "./BusDeveloperReply";
import {
  InspectorFact,
  InspectorNote,
  InspectorSection,
  inspectorAction,
  inspectorPane,
} from "./InspectorPrimitives";
import { VirtualizedInspectorList } from "./VirtualizedInspectorList";

// Fleet-global by default. The focused-seat mode is a strict exact-identity filter and its empty
// state says what it cannot prove. Rows remain pending/unacked projection facts; the only write is
// a developer reply/decision through the existing operator-inbox POST.

const row = css({ display: "grid", gap: "0.12rem", minWidth: "0" });
const edge = css({ color: "ink", fontWeight: "600", overflowWrap: "anywhere" });
const meta = css({ color: "muted", overflowWrap: "anywhere" });
const messageKind = css({
  display: "inline-flex",
  width: "fit-content",
  paddingInline: "0.3rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  color: "amber",
});
const controls = css({ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0.4rem" });
const numeric = css({ fontVariantNumeric: "tabular-nums" });

function identity(role: string | undefined, agentId: string | undefined, absent: string): string {
  if (!role && !agentId) return absent;
  return [role, agentId].filter(Boolean).join(":");
}

export function pickupMatchesFocusedSeat(
  pickup: AgentPickupNode,
  session: OpenSession | undefined,
): boolean {
  if (!session) return false;
  return (
    pickup.deliveredToSession === session.id ||
    pickup.agentId === session.id ||
    pickup.ownerAgentId === session.id ||
    pickup.senderAgentId === session.id ||
    (session.lifecycleId !== undefined &&
      (pickup.lifecycleId === session.lifecycleId ||
        pickup.ownerLifecycleId === session.lifecycleId))
  );
}

function seconds(value: number | undefined): string {
  if (value === undefined) return "—";
  return `${Math.max(0, Math.round(value))}s`;
}

function PickupRow({
  pickup,
  nowMs,
  replyState,
  updateReplyState,
}: {
  pickup: AgentPickupNode;
  nowMs: number;
  replyState: BusReplyState;
  updateReplyState: (update: (current: BusReplyState) => BusReplyState) => void;
}) {
  const sender = identity(pickup.senderRole, pickup.senderAgentId, "sender unavailable");
  const owner = identity(pickup.ownerRole, pickup.ownerAgentId, "owner unavailable");
  const age = servedAgeSeconds(pickup, pickup.ageSeconds, nowMs);
  const replyable = pickup.messageKind === "decision-item" || pickup.messageKind === "escalation";
  return (
    <div className={row} data-testid={`bus-pickup-${pickup.entryId}`}>
      <span className={edge}>
        {sender} → {owner}
      </span>
      <span className={messageKind}>{pickup.messageKind}</span>
      <PickupMeta pickup={pickup} age={age} />
      {replyable ? (
        <BusDeveloperReply
          pickup={pickup}
          state={replyState}
          updateState={updateReplyState}
        />
      ) : null}
    </div>
  );
}

function PickupMeta({ pickup, age }: { pickup: AgentPickupNode; age: number | undefined }) {
  return (
    <>
      <span className={meta}>
        delivery {pickup.deliveryState} · state {pickup.state}
      </span>
      <span className={meta}>
        recipient {pickup.recipientRole ?? "—"} · agent {pickup.agentId ?? "—"} · lifecycle{" "}
        {pickup.lifecycleId ?? "—"}
      </span>
      <span className={meta}>owner lifecycle {pickup.ownerLifecycleId ?? "—"}</span>
      <span className={`${meta} ${numeric}`}>
        attempts {pickup.attemptCount} · last {pickup.lastAttemptAt ?? "—"} · next{" "}
        {pickup.nextAttemptAt ?? "—"}
      </span>
      <span className={`${meta} ${numeric}`}>
        unacknowledged age {seconds(age)} / ttl {seconds(pickup.ttlSeconds)} · escalated{" "}
        {pickup.escalatedAt ?? "—"}
      </span>
      {pickup.artifactPath ? <span className={meta}>artifact {pickup.artifactPath}</span> : null}
    </>
  );
}

function BusPickupSection({
  session,
  pickups,
  visible,
  focusedFilterActive,
  nowMs,
  replyStateByEntry,
  onToggleFilter,
  updateReplyState,
}: {
  session: OpenSession | undefined;
  pickups: readonly AgentPickupNode[];
  visible: readonly AgentPickupNode[];
  focusedFilterActive: boolean;
  nowMs: number;
  replyStateByEntry: Record<string, BusReplyState>;
  onToggleFilter: () => void;
  updateReplyState: (entryId: string, update: (current: BusReplyState) => BusReplyState) => void;
}) {
  return (
    <InspectorSection title="Pending pickup projection">
      <div className={controls}>
        <button
          type="button"
          className={inspectorAction}
          aria-pressed={focusedFilterActive}
          disabled={!session}
          onClick={onToggleFilter}
          data-testid="bus-focused-filter"
        >
          {focusedFilterActive ? "show fleet-global bus" : "filter to focused seat"}
        </button>
        <span>
          {visible.length} shown / {pickups.length} projected pending
        </span>
      </div>
      {visible.length > 0 ? (
        <VirtualizedInspectorList
          rows={visible}
          rowKey={(pickup) => pickup.id}
          renderRow={(pickup) => (
            <PickupRow
              pickup={pickup}
              nowMs={nowMs}
              replyState={replyStateByEntry[pickup.entryId] ?? EMPTY_BUS_REPLY_STATE}
              updateReplyState={(update) => updateReplyState(pickup.entryId, update)}
            />
          )}
          label={
            focusedFilterActive
              ? "Focused-seat pending pickup projection"
              : "Fleet pending pickup projection"
          }
          testId="bus-pickup-list"
        />
      ) : focusedFilterActive ? (
        <InspectorNote testId="bus-focused-empty">
          No projected pickup matches this focused seat's exact ids. The fleet-global bus still
          has {pickups.length} projected pending row{pickups.length === 1 ? "" : "s"}; this empty
          filter is not a bus-health verdict.
        </InspectorNote>
      ) : (
        <InspectorNote testId="bus-global-empty">
          No pending pickup rows are projected. Full history is unavailable, so this is not a
          bus-health verdict.
        </InspectorNote>
      )}
    </InspectorSection>
  );
}

function HeartbeatSection({ heartbeat }: { heartbeat: SupervisorHeartbeat | null }) {
  return (
    <InspectorSection title="Supervisor heartbeat" testId="bus-heartbeat">
      {heartbeat ? (
        <>
          <InspectorFact
            label="state"
            value={
              heartbeat.lastTickAt === null
                ? "never ticked"
                : heartbeat.stale
                  ? "stale"
                  : "active"
            }
            testId="bus-heartbeat-state"
          />
          <InspectorFact label="last tick" value={heartbeat.lastTickAt ?? "—"} />
          <InspectorFact
            label="age / cutoff"
            value={`${seconds(heartbeat.ageSeconds ?? undefined)} / ${seconds(heartbeat.staleCutoffSeconds)}`}
          />
          <InspectorFact
            label="pending / redeliverable"
            value={`${heartbeat.pendingInboxCount} / ${heartbeat.redeliverableInboxCount}`}
            testId="bus-heartbeat-counts"
          />
          <InspectorFact
            label="last sweep"
            value={
              heartbeat.lastSweepDurationSeconds === null
                ? "—"
                : seconds(heartbeat.lastSweepDurationSeconds)
            }
          />
        </>
      ) : (
        <InspectorNote>
          Supervisor heartbeat is not projected; no liveness or health claim is available.
        </InspectorNote>
      )}
    </InspectorSection>
  );
}

export function BusPane({
  session,
  pickups,
  heartbeat,
  ageClockActive = true,
}: {
  session: OpenSession | undefined;
  pickups: readonly AgentPickupNode[];
  heartbeat: SupervisorHeartbeat | null;
  ageClockActive?: boolean;
}) {
  const [focusedOnly, setFocusedOnly] = useState(false);
  // Reply interaction state lives above the virtual row and the focused-seat filter. TanStack
  // deliberately unmounts offscreen rows, but doing so must not erase or reassign a draft/status.
  const [replyStateByEntry, setReplyStateByEntry] = useState<Record<string, BusReplyState>>({});
  const activeEntryIds = useRef<Set<string>>(new Set());
  activeEntryIds.current = new Set(pickups.map((pickup) => pickup.entryId));
  const nowMs = useNowMs(10_000, ageClockActive);
  const focusedFilterActive = focusedOnly && session !== undefined;
  const visible = focusedFilterActive
    ? pickups.filter((pickup) => pickupMatchesFocusedSeat(pickup, session))
    : pickups;

  useEffect(() => {
    if (!session && focusedOnly) setFocusedOnly(false);
  }, [focusedOnly, session]);

  useEffect(() => {
    // The projection is a live pending set. Without pruning, state lifted above virtual rows would
    // retain departed inbox entries for the lifetime of the dashboard as polling replaces them.
    const currentEntryIds = new Set(pickups.map((pickup) => pickup.entryId));
    setReplyStateByEntry((current) => {
      const stale = Object.keys(current).filter((entryId) => !currentEntryIds.has(entryId));
      if (stale.length === 0) return current;
      return Object.fromEntries(
        Object.entries(current).filter(([entryId]) => currentEntryIds.has(entryId)),
      );
    });
  }, [pickups]);

  const updateReplyState = useCallback(
    (entryId: string, update: (current: BusReplyState) => BusReplyState) => {
      // A request may settle after polling removed its source row. Do not resurrect state for an
      // entry that is no longer in the authoritative pending projection.
      if (!activeEntryIds.current.has(entryId)) return;
      setReplyStateByEntry((current) => {
        const next = update(current[entryId] ?? EMPTY_BUS_REPLY_STATE);
        if (!next.open && next.draft === "" && next.status === "idle") {
          if (!(entryId in current)) return current;
          const rest = { ...current };
          delete rest[entryId];
          return rest;
        }
        return { ...current, [entryId]: next };
      });
    },
    [],
  );

  return (
    <div className={inspectorPane} data-testid="bus-pane">
      <BusPickupSection
        session={session}
        pickups={pickups}
        visible={visible}
        focusedFilterActive={focusedFilterActive}
        nowMs={nowMs}
        replyStateByEntry={replyStateByEntry}
        onToggleFilter={() => setFocusedOnly((current) => !current)}
        updateReplyState={updateReplyState}
      />
      <HeartbeatSection heartbeat={heartbeat} />

      <InspectorSection title="Projection limits (UA-3)">
        <InspectorNote testId="bus-limits-copy">
          Pending pickup metadata only. Full message bodies, consumed history, and escalation rung
          are not projected (UA-3). Developer posts do not acknowledge or consume the source row;
          recipient acknowledgment remains MCP-only. Session composer submits never traverse this
          inbox.
        </InspectorNote>
      </InspectorSection>
    </div>
  );
}
