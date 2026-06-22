import { motion } from "motion/react";

import { css } from "../../../styled-system/css";
import type { EngineProcessNode, LedgerNode, ProviderNode } from "../../types/projection";
import { EnclosureCanvas } from "./EnclosureCanvas";
import {
  abandonRecord,
  backdrop,
  backdropVideo,
  cleanupRecord,
  dissolveShell,
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

// 05o — a pre-contract / stale-base blocked-start node (5f §2.1) is now drawn entirely inside the canvas
// as the big red `FleetingEnclosure` box (EnclosureCanvas), so this shell no longer renders an HTML banner.

// t18 — abandon: the enclosure dissolves (the shell dims + desaturates) and keeps an "abandoned" record.
function AbandonRecord({ node }: { node: EngineProcessNode }) {
  return (
    <div className={abandonRecord} data-testid="abandon-record">
      <span>⛌ Abandoned — {node.summary || "worktree retired without integration"}</span>
    </div>
  );
}

// 5h H4 — cleanup teardown: the SUCCESS counterpart. The worktree LANDED, so it de-materialises back
// into the official line (the same `.dissolve`), but the record reads success — and names the `origin-main`
// tip it rejoined (the "back into main" seam), never faked (dropped when the probe couldn't resolve it).
function CleanupRecord({ node }: { node: EngineProcessNode }) {
  const mainTip = node.landing?.find((ref) => ref.kind === "origin-main" && ref.factState !== "missing");
  return (
    <div className={cleanupRecord} data-testid="cleanup-record">
      <span>
        ✓ Landed{mainTip ? ` → ${mainTip.label}` : ""} — {node.summary || "worktree retiring into the official line"}
      </span>
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
  // Teardown = the enclosure de-materialising (the `.dissolve` shell): t18 abandon (failure, no landing)
  // and 5h H4 cleanup (success, landed → retiring back into the official line). Both dim + desaturate; the
  // record + tone differ. data-abandoned is kept for the existing abandon test; data-teardown is the hook.
  const abandoned = node.phase === "abandoned";
  const teardown = abandoned ? "abandon" : node.phase === "cleanup-pending" ? "cleanup" : null;
  return (
    <motion.div
      className={mapWrap}
      data-testid="process-map"
      data-abandoned={abandoned || undefined}
      data-teardown={teardown ?? undefined}
      layout={animate}
      initial={false}
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
        {/* 05o — a fleeting (born-blocked) enclosure now renders inside the canvas as the big red
            `FleetingEnclosure` box (podstage `.fbox`); the old HTML banner strip is gone. */}
        {teardown === "abandon" ? <AbandonRecord node={node} /> : null}
        {teardown === "cleanup" ? <CleanupRecord node={node} /> : null}
        {/* abandon dissolves the WHOLE enclosure (failure, nothing landed); a landed CLEANUP only
            de-materialises the WORKTREE side — main stays bright. That fade lives inside the canvas
            (the worktree refs go `planned`), so cleanup does NOT wrap in the full-dim dissolve shell. */}
        {teardown === "abandon" ? (
          <motion.div
            className={dissolveShell}
            data-testid="dissolve"
            initial={animate ? { opacity: 1, filter: "grayscale(0)" } : false}
            animate={{ opacity: 0.4, filter: "grayscale(0.75)" }}
            transition={{ duration: animate ? 0.6 : 0, ease: "easeOut" }}
          >
            <EnclosureCanvas node={node} workspaceEngines={workspaceEngines} officialLedger={officialLedger} />
          </motion.div>
        ) : (
          <EnclosureCanvas node={node} workspaceEngines={workspaceEngines} officialLedger={officialLedger} />
        )}
      </div>
    </motion.div>
  );
}
