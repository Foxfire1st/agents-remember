import { AnimatePresence, motion } from "motion/react";

import { css } from "../../../styled-system/css";
import type { EngineProcessNode } from "../../types/projection";
import { EnclosureCanvas } from "./EnclosureCanvas";
import {
  fleetingBanner,
  fleetingChoice,
  fleetingChoices,
  fleetingLabel,
  fleetingReason,
} from "./engineRoomStyles";
import { useShouldAnimate } from "./useShouldAnimate";

const mapWrap = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.45rem",
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

// The Engine Room pod stage: the bird's-eye `EnclosureCanvas` (5g) inside the promote-in-place
// shell. The map is keyed-stable by worktreeGroup upstream (S0), so a blocked fleeting node
// solidifies in place into the contract-anchored enclosure (T4 morph, 5f S3) — never a teleport.
// The ghost banner fades as the node promotes; `layout` carries the morph. Honest motion: under
// data-effects=off / reduced-motion the shell is an instant swap (no tween).
export function EnclosureProcessMap({ node }: { node: EngineProcessNode }) {
  const animate = useShouldAnimate();
  return (
    <motion.div
      className={mapWrap}
      data-testid="process-map"
      layout={animate}
      initial={animate ? { opacity: 0, scale: 0.985 } : false}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
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
      <EnclosureCanvas node={node} />
    </motion.div>
  );
}
