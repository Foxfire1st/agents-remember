import { useState } from "react";

import { css, cva } from "../../styled-system/css";
import { FLOW_MODELS, type FlowModel, type FlowNode, type FlowSegment, type Status } from "./flowModels";

// The Lifecycle Flow canvas (task 26; orchestration L0 generalizes it): STATIC design drawings of
// lifecycles/interactions, switched by a nav — the surface where a new lifecycle (any model in the
// `flowModels.ts` registry) is DRAWN and reviewed with the developer before it is built. Content lives
// in `flowModels.ts` (module-level data, no store reads); this file owns only the segment renderer
// and the model nav. Mounted dev-only at `/dev/flows` (dead-code-eliminated in production) — task 29
// removed it from the cockpit mode bar, and that stays true.
//
// Arrow colour: mint = wired today; amber dashed = proposed by the active series (per model legend).

const root = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", overflow: "auto", padding: "0.4rem 0.2rem 2rem", gap: "0.55rem" });
const header = css({ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" });
const h2 = css({ margin: "0", fontSize: "0.95rem", letterSpacing: "0.14em", color: "amber" });
const legend = css({ display: "flex", gap: "1.1rem", fontSize: "0.72rem", color: "muted", alignItems: "center" });
const swatch = cva({ base: { display: "inline-block", width: "1.6rem", height: "0", marginRight: "0.35rem", verticalAlign: "middle", borderTopWidth: "2px" }, variants: { status: { current: { borderTopStyle: "solid", borderTopColor: "mint" }, proposed: { borderTopStyle: "dashed", borderTopColor: "amber" } } } });
const takeaway = css({ fontSize: "0.76rem", color: "ink", opacity: "0.9", borderLeftWidth: "2px", borderLeftStyle: "solid", borderLeftColor: "amber", paddingLeft: "0.6rem", maxWidth: "82ch", lineHeight: "1.45" });

// The model nav: a two-plus-segment radiogroup mirroring the RailToggle/EffectsToggle idiom.
const nav = css({ display: "inline-flex", alignItems: "center", gap: "0", borderWidth: "1px", borderStyle: "solid", borderColor: "grid", borderRadius: "3px", overflow: "hidden", alignSelf: "flex-start" });
const navButton = cva({
  base: {
    font: "inherit",
    fontSize: "0.72rem",
    letterSpacing: "0.05em",
    paddingInline: "0.7rem",
    paddingBlock: "0.24rem",
    background: "transparent",
    color: "muted",
    border: "none",
    cursor: "pointer",
    _hover: { color: "ink" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
  },
  variants: { on: { true: { color: "amber", background: "rgba(232, 193, 112, 0.12)" }, false: {} } },
});

const chain = css({ display: "flex", flexDirection: "column", alignItems: "stretch", maxWidth: "640px", width: "100%", marginInline: "auto", marginTop: "0.3rem" });
const startNode = css({ alignSelf: "center", fontSize: "0.74rem", color: "muted", borderWidth: "1px", borderStyle: "dotted", borderColor: "grid", borderRadius: "999px", paddingInline: "0.7rem", paddingBlock: "0.2rem" });
const toolNode = css({ display: "flex", flexDirection: "column", gap: "0.1rem", background: "bg", borderWidth: "1px", borderStyle: "solid", borderColor: "grid", borderRadius: "3px", paddingInline: "0.7rem", paddingBlock: "0.45rem" });
// A gate node: a normal call with an approval/notification fused as a rider (amber left bar).
const ridesNode = css({ display: "flex", flexDirection: "column", gap: "0.1rem", background: "bg", borderWidth: "1px", borderStyle: "solid", borderColor: "grid", borderLeftWidth: "3px", borderLeftStyle: "solid", borderLeftColor: "amber", borderRadius: "3px", paddingInline: "0.7rem", paddingBlock: "0.45rem" });
const phaseTag = css({ fontSize: "0.63rem", letterSpacing: "0.08em", color: "muted", textTransform: "uppercase" });
const toolName = css({ fontFamily: "monospace", fontSize: "0.82rem", fontWeight: "600", color: "ink" });
const detailLine = css({ fontSize: "0.72rem", color: "muted" });
const ridesLine = css({ fontSize: "0.69rem", color: "amber", fontStyle: "italic", marginTop: "0.16rem" });
const connector = cva({ base: { display: "flex", alignItems: "center", gap: "0.4rem", alignSelf: "center", paddingBlock: "0.2rem", fontSize: "0.7rem", fontFamily: "monospace" }, variants: { status: { current: { color: "mint" }, proposed: { color: "amber" } } } });
const wire = cva({ base: { width: "0", height: "0.95rem", borderLeftWidth: "2px" }, variants: { status: { current: { borderLeftStyle: "solid", borderLeftColor: "mint" }, proposed: { borderLeftStyle: "dashed", borderLeftColor: "amber" } } } });
const rundownCard = css({ display: "flex", flexDirection: "column", gap: "0.25rem", background: "bg", borderWidth: "1px", borderStyle: "dashed", borderColor: "amber", borderRadius: "3px", paddingInline: "0.8rem", paddingBlock: "0.55rem", marginBlock: "0.2rem" });
const rundownTitle = css({ fontSize: "0.7rem", letterSpacing: "0.06em", color: "amber", textTransform: "uppercase" });
const rundownLine = cva({ base: { fontSize: "0.74rem", color: "ink", paddingLeft: "0.6rem", borderLeftWidth: "2px", borderLeftStyle: "solid", borderLeftColor: "grid", paddingBlock: "0.08rem" }, variants: { junction: { true: { borderLeftColor: "cyan", color: "cyan", fontFamily: "monospace", fontSize: "0.72rem" }, false: {} } } });
const divider = css({ alignSelf: "center", fontSize: "0.7rem", color: "muted", fontStyle: "italic", paddingBlock: "0.3rem", textAlign: "center" });

const DEFAULT_RIDES_NOTE = (rides: string) =>
  `⊘ auto-fires lifecycle_turn_end_notification · ${rides} — rides this call (agent never fires it); next AR tool clears it`;

function ToolNode({ n }: { n: FlowNode }) {
  const Cls = n.rides ? ridesNode : toolNode;
  return (
    <div className={Cls} data-testid={n.rides ? "flow-gate" : "flow-node"}>
      <span className={phaseTag}>{n.phase}{n.rides ? " · gate" : ""}</span>
      <span className={toolName}>{n.tool}</span>
      {n.detail ? <span className={detailLine}>{n.detail}</span> : null}
      {n.rides ? <span className={ridesLine}>{n.ridesNote ?? DEFAULT_RIDES_NOTE(n.rides)}</span> : null}
    </div>
  );
}

function Arrow({ to, status }: { to: string; status: Status }) {
  return (
    <div className={connector({ status })} data-edge={status}>
      <span className={wire({ status })} />
      <span>nextStep → {to}</span>
    </div>
  );
}

function Segment({ segment }: { segment: FlowSegment }) {
  switch (segment.kind) {
    case "start":
      return (
        <>
          <div className={startNode}>{segment.label}</div>
          {segment.next ? <Arrow to={segment.next} status={segment.nextStatus ?? "proposed"} /> : null}
        </>
      );
    case "node":
      return (
        <>
          <ToolNode n={segment} />
          {segment.next ? <Arrow to={segment.next} status={segment.nextStatus ?? "proposed"} /> : null}
        </>
      );
    case "rundown":
      return (
        <div className={rundownCard} data-testid="flow-rundown">
          <span className={rundownTitle}>{segment.title}</span>
          {segment.lines.map((r, i) => (
            <span key={i} className={rundownLine({ junction: Boolean(r.junction) })}>{r.line}</span>
          ))}
        </div>
      );
    case "divider":
      return <div className={divider}>{segment.label}</div>;
  }
}

export function FlowTab({ initialModel }: { initialModel?: string } = {}) {
  const fallback = FLOW_MODELS[0];
  const [modelId, setModelId] = useState<string>(
    FLOW_MODELS.some((m) => m.id === initialModel) ? (initialModel as string) : fallback.id,
  );
  const model: FlowModel = FLOW_MODELS.find((m) => m.id === modelId) ?? fallback;
  return (
    <div className={root} data-testid="flow-tab" data-model={model.id}>
      <div className={nav} role="radiogroup" aria-label="Flow model" data-testid="flow-nav">
        {FLOW_MODELS.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={m.id === model.id}
            className={navButton({ on: m.id === model.id })}
            onClick={() => setModelId(m.id)}
            data-testid={`flow-nav-${m.id}`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className={header}>
        <h2 className={h2}>LIFECYCLE FLOW · {model.title}</h2>
        <div className={legend}>
          <span><span className={swatch({ status: "current" })} />wired today</span>
          <span><span className={swatch({ status: "proposed" })} />this series</span>
        </div>
      </div>
      <p className={takeaway}>{model.takeaway}</p>

      <div className={chain}>
        {model.segments.map((segment, i) => (
          <Segment key={i} segment={segment} />
        ))}
      </div>
    </div>
  );
}
