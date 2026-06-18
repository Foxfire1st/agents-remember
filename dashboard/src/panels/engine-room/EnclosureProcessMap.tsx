import { AnimatePresence, motion } from "motion/react";

import { css } from "../../../styled-system/css";
import type { EngineProcessNode, LedgerNode, ProviderNode } from "../../types/projection";
import { EnclosureCanvas } from "./EnclosureCanvas";
import {
  abandonRecord,
  backdrop,
  backdropVideo,
  dissolveShell,
  fleetingBanner,
  fleetingChoice,
  fleetingChoices,
  fleetingLabel,
  fleetingReason,
  stageContent,
} from "./engineRoomStyles";
import { useShouldAnimate } from "./useShouldAnimate";

const mapWrap = css({
  position: "relative", // G6: stacking context for the atmospheric backdrop
  overflow: "hidden", // clip the backdrop video to the rounded box
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  padding: "0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  background: "bg",
});

// A pre-contract blocked-start node (5f §2.1): the reducer marks it with a "contract not yet
// written" missing-fact. Provisional, not fake — shown distinctly until it promotes (the morph is S3).
function isFleeting(node: EngineProcessNode): boolean {
  return node.missingFacts.some((fact) => /contract not yet written/i.test(fact));
}

function FleetingBanner({ node }: { node: EngineProcessNode }) {
  return (
    <div className={fleetingBanner} data-testid="fleeting-banner">
      <span className={fleetingLabel}>⚠ Fleeting · blocked — creation gated, contract not yet written</span>
      <span className={fleetingReason}>{node.summary}</span>
      {node.nextAction ? (
        <span className={fleetingChoices}>
          <span className={fleetingChoice}>recover: {node.nextAction}</span>
        </span>
      ) : null}
    </div>
  );
}

// t18 — abandon: the enclosure dissolves (the shell dims + desaturates) and keeps an "abandoned" record.
function AbandonRecord({ node }: { node: EngineProcessNode }) {
  return (
    <div className={abandonRecord} data-testid="abandon-record">
      <span>⛌ Abandoned — {node.summary || "worktree retired without integration"}</span>
    </div>
  );
}

// The Engine Room pod stage: the bird's-eye `EnclosureCanvas` (5g) inside the promote-in-place
// shell. The map is keyed-stable by worktreeGroup upstream (S0), so a blocked fleeting node
// solidifies in place into the contract-anchored enclosure (T4 morph, 5f S3) — never a teleport.
// The ghost banner fades as the node promotes; `layout` carries the morph. Honest motion: under
// data-effects=off / reduced-motion the shell is an instant swap (no tween).
export function EnclosureProcessMap({ node, workspaceEngines = [], officialLedger }: {
  node: EngineProcessNode;
  workspaceEngines?: ProviderNode[];
  officialLedger?: LedgerNode;
}) {
  const animate = useShouldAnimate();
  // t18 — an abandoned enclosure renders as a dim, desaturated record (its static end-state).
  const abandoned = node.phase === "abandoned";
  return (
    <motion.div
      className={mapWrap}
      data-testid="process-map"
      data-abandoned={abandoned || undefined}
      layout={animate}
      initial={animate ? { opacity: 0, scale: 0.985 } : false}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      {/* G6: faint blueprint boomerang backdrop — only when effects are on (absent + lazy when calm). */}
      {animate ? (
        <div className={backdrop} aria-hidden="true" data-testid="backdrop">
          <video
            className={backdropVideo}
            src="/assets/blueprint-boomerang.mp4"
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
          />
        </div>
      ) : null}
      <div className={stageContent}>
        <AnimatePresence initial={false}>
          {isFleeting(node) ? (
            <motion.div
              key="fleeting"
              layout={animate}
              initial={animate ? { opacity: 0 } : false}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: animate ? 0.3 : 0 }}
            >
              <FleetingBanner node={node} />
            </motion.div>
          ) : null}
        </AnimatePresence>
        {abandoned ? <AbandonRecord node={node} /> : null}
        {abandoned ? (
          <div className={dissolveShell} data-testid="dissolve">
            <EnclosureCanvas node={node} workspaceEngines={workspaceEngines} officialLedger={officialLedger} />
          </div>
        ) : (
          <EnclosureCanvas node={node} workspaceEngines={workspaceEngines} officialLedger={officialLedger} />
        )}
      </div>
    </motion.div>
  );
}
