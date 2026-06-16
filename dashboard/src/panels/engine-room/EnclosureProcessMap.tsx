import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";
import gsap from "gsap";

import { css } from "../../../styled-system/css";
import type { CommitRefNode, EngineProcessNode, ProviderBootNode } from "../../types/projection";
import {
  conduitChevron,
  conduitLine,
  conduitSvg,
  couplerBar,
  engineMeta,
  engineName,
  engineSilhouette,
  engineState,
  fleetingBanner,
  fleetingChoice,
  fleetingChoices,
  fleetingLabel,
  fleetingReason,
  nodeBox,
  nodeBranch,
  nodeCommit,
  nodeLabel,
  sectionLabel,
} from "./engineRoomStyles";
import { useShouldAnimate } from "./useShouldAnimate";

type ConduitState =
  | "nominal"
  | "complete"
  | "running"
  | "blocked"
  | "failed"
  | "stale"
  | "skipped"
  | "planned"
  | "unknown";

type RuntimeState = "nominal" | "configured" | "indexing" | "down" | "unknown";

const mapWrap = css({
  display: "grid",
  gap: "0.45rem",
  padding: "0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  background: "bg",
});

const row = css({
  display: "grid",
  gridTemplateColumns: "1fr 1.4rem minmax(0, 1fr) 1.4rem auto",
  gap: "0.4rem",
  alignItems: "center",
});

const couplerRow = css({ display: "flex", alignItems: "center", gap: "0.5rem" });
const couplerLine = css({ flex: "1", height: "1px", background: "token(colors.amber)", opacity: "0.4" });
const engineWrap = css({ display: "flex", alignItems: "center", gap: "0.4rem" });
const memoryNote = css({ color: "muted", fontSize: "0.72rem", paddingInline: "0.2rem" });

function conduitState(value: string): ConduitState {
  switch (value) {
    case "nominal":
    case "complete":
    case "running":
    case "blocked":
    case "failed":
    case "stale":
    case "skipped":
    case "planned":
      return value;
    default:
      return "unknown";
  }
}

function runtimeState(value: string | undefined): RuntimeState {
  switch (value) {
    case "nominal":
    case "configured":
    case "indexing":
    case "down":
      return value;
    default:
      return "unknown";
  }
}

function edgeState(node: EngineProcessNode, kind: string): ConduitState {
  return conduitState(node.edges.find((edge) => edge.kind === kind)?.state ?? "unknown");
}

// A pre-contract blocked-start node (5f §2.1): the reducer marks it with a "contract not yet written"
// missing-fact. It is provisional, not fake — shown distinctly until it promotes (the morph is S3).
function isFleeting(node: EngineProcessNode): boolean {
  return node.missingFacts.some((fact) => /contract not yet written/i.test(fact));
}

// Birth motion (5f S2). The lane connector draws on (GSAP stroke-dashoffset) when it carries real
// flow; planned/blocked edges stay put. Honest-motion gate: under data-effects=off / reduced-motion
// the line renders fully drawn with no tween (snapshot-stable).
function SvgConduit({ state }: { state: ConduitState }) {
  const lineRef = useRef<SVGLineElement>(null);
  const flowRef = useRef<SVGCircleElement>(null);
  const animate = useShouldAnimate();
  const draws = state === "nominal" || state === "complete" || state === "running";
  const flows = state === "running"; // T8/T9: a travelling energy packet while the conduit seeds/clones
  useEffect(() => {
    const line = lineRef.current;
    if (!line) return;
    if (!animate || !draws) {
      gsap.set(line, { strokeDashoffset: 0 });
      return;
    }
    const tween = gsap.fromTo(
      line,
      { strokeDasharray: 24, strokeDashoffset: 24 },
      {
        strokeDashoffset: 0,
        duration: 0.35,
        ease: "power1.out",
        onComplete: () => gsap.set(line, { clearProps: "strokeDasharray,strokeDashoffset" }),
      },
    );
    return () => {
      tween.kill();
      gsap.set(line, { clearProps: "strokeDasharray,strokeDashoffset" });
    };
  }, [state, animate, draws]);
  useEffect(() => {
    const flow = flowRef.current;
    if (!flow) return;
    if (!animate || !flows) {
      gsap.set(flow, { opacity: 0 }); // honest motion: no travelling packet under data-effects=off
      return;
    }
    gsap.set(flow, { opacity: 1 });
    const tween = gsap.fromTo(
      flow,
      { attr: { cx: 0 } },
      { attr: { cx: 24 }, duration: 0.9, ease: "none", repeat: -1 },
    );
    return () => {
      tween.kill();
    };
  }, [animate, flows]);
  return (
    <svg
      className={conduitSvg}
      viewBox="0 0 24 2"
      preserveAspectRatio="none"
      role="presentation"
      aria-hidden="true"
    >
      <line x1="0" y1="1" x2="24" y2="1" ref={lineRef} data-state={state} className={conduitLine({ state })} />
      {flows ? (
        <circle ref={flowRef} cx="0" cy="1" r="1.3" className={conduitChevron} data-testid="conduit-flow" />
      ) : null}
    </svg>
  );
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

function CommitNode({ label, refNode }: { label: string; refNode: CommitRefNode }) {
  return (
    <div className={nodeBox({ factState: refNode.factState })}>
      <span className={nodeLabel}>{label}</span>
      <span className={nodeBranch}>{refNode.branch ?? "—"}</span>
      {refNode.commit ? (
        <span className={nodeCommit}>
          @ {refNode.commit.slice(0, 8)}
          {refNode.dirty ? " · dirty" : ""}
          {refNode.behindSource ? ` · ${refNode.behindSource} behind` : ""}
        </span>
      ) : null}
    </div>
  );
}

function EngineUnit({
  engine,
  role,
}: {
  engine: ProviderBootNode | undefined;
  role: "code" | "memory";
}) {
  const label = role === "code" ? "CGC" : "GrepAI";
  const runtime = runtimeState(engine?.runtimeState);
  return (
    <div className={engineWrap}>
      <span
        className={engineSilhouette({ runtimeState: runtime })}
        role="img"
        aria-label={`${label} engine ${engine ? runtime : "not started"}`}
      />
      <span className={engineMeta}>
        <span className={engineName}>{label}</span>
        <span className={engineState}>{engine ? runtime : "not started"}</span>
      </span>
    </div>
  );
}

export function EnclosureProcessMap({ node }: { node: EngineProcessNode }) {
  const animate = useShouldAnimate();
  const codeEngine = node.providers.find((provider) => provider.role === "code");
  const memoryEngine = node.providers.find((provider) => provider.role === "memory");
  const externalMemory = node.memoryMode === "external" && node.memorySource && node.memoryWorktree;
  return (
    <motion.div
      className={mapWrap}
      data-testid="process-map"
      layout={animate}
      initial={animate ? { opacity: 0, scale: 0.985 } : false}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
    >
      {/* T4 promotion morph (5f S3): the map is keyed-stable by worktreeGroup (S0), so a blocked
          fleeting node solidifies in place into the contract-anchored enclosure — never a teleport.
          The ghost banner fades out as the node promotes; `layout` carries the size morph. Gated:
          under data-effects=off / reduced-motion it is an instant swap (no tween). */}
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
      <span className={sectionLabel}>Official line → enclosure</span>
      <div className={row} data-testid="code-lane">
        <CommitNode label="Code source" refNode={node.codeSource} />
        <SvgConduit state={edgeState(node, "worktree-add")} />
        <CommitNode label="Code worktree" refNode={node.codeWorktree} />
        <SvgConduit state={edgeState(node, "cgc-seed")} />
        <EngineUnit engine={codeEngine} role="code" />
      </div>
      <div className={couplerRow}>
        <span className={couplerLine} aria-hidden="true" />
        <span className={couplerBar}>contract · {node.taskId}</span>
        <span className={couplerLine} aria-hidden="true" />
      </div>
      {externalMemory && node.memorySource && node.memoryWorktree ? (
        <div className={row} data-testid="memory-lane">
          <CommitNode label="Memory source" refNode={node.memorySource} />
          <SvgConduit state={edgeState(node, "ledger-map")} />
          <CommitNode label="Memory worktree" refNode={node.memoryWorktree} />
          <SvgConduit state={edgeState(node, "grepai-clone")} />
          <EngineUnit engine={memoryEngine} role="memory" />
        </div>
      ) : (
        <div className={memoryNote} data-testid="memory-lane-absent">
          Memory: {node.memoryMode} — no external memory lane
        </div>
      )}
    </motion.div>
  );
}
