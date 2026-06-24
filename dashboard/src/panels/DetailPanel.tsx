import { useEffect, useState, type ReactNode } from "react";

import { css, cva, cx } from "../../styled-system/css";
import { useDashboard } from "../data/store";
import {
  findLifecycleEnclosure,
  groupEnclosuresByLifecycle,
  taskDocsForLifecycle,
  taskLabel,
} from "../data/taskIdentity";
import { Markdown } from "../grammar/Markdown";
import { Panel } from "../grammar/Panel";
import { ProgressFill } from "../grammar/ProgressFill";
import { TokenGauge } from "../grammar/TokenGauge";
import type {
  Phase,
  ProviderNode,
  SeriesNode,
  TaskCodeExampleNode,
  TaskDecisionNode,
  TaskDocNode,
  TaskSectionNode,
  TaskStepNode,
  TaskSubTaskRefNode,
} from "../types/projection";

import { EmptyStateBackdrop } from "./EmptyStateBackdrop";
import { GateResponder } from "./GateResponder";

// The l-01 phase vocabulary, in order (mcp/.../lifecycle_state.py). The stepper marks phases before
// the current as done — mc2's Request→Close mini-map.
const PHASES: Phase[] = [
  "request",
  "trust-checkpoint",
  "reframe-research",
  "decide",
  "build",
  "close",
];

const sizing = css({ flex: "1" });
const where = css({ fontSize: "0.76rem", color: "muted", marginBottom: "0.4rem" });

const stepper = css({
  listStyle: "none",
  margin: "0.4rem 0",
  padding: "0",
  display: "flex",
  flexWrap: "wrap",
  gap: "0.3rem",
});
const step = cva({
  base: {
    fontSize: "0.72rem",
    letterSpacing: "0.04em",
    paddingInline: "0.4rem",
    paddingBlock: "0.15rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    color: "oklch(0.6 0.02 250)",
  },
  variants: {
    state: {
      todo: {},
      done: { color: "cyan", borderColor: "cyan" },
      current: {
        color: "amber",
        borderColor: "amber",
        textShadow: "0 0 calc(5px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
      },
    },
  },
});

const badge = css({
  fontSize: "0.68rem",
  paddingInline: "0.35rem",
  paddingBlock: "0.05rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  color: "muted",
});

const series = css({ margin: "0.5rem 0" });
const taskHead = css({ fontSize: "0.82rem" });
const slices = css({ listStyle: "none", margin: "0.3rem 0 0", padding: "0", display: "grid", gap: "0.15rem" });
const slice = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
});
const sliceMeta = css({ color: "muted", fontSize: "0.72rem" });
// 6g: a clickable sub-task row (drill-in) and the breadcrumb back from a slice to the series.
const sliceButton = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  border: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "cyan",
  color: "ink",
  cursor: "pointer",
  _hover: { background: "oklch(0.7 0.1 200 / 0.12)", borderLeftColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
// 6g cross-master link: a row that jumps to a parallel/external series (amber "→"), distinct from
// the cyan in-series drill rows so leaving the current series reads as a deliberate hop.
const crossButton = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  width: "100%",
  textAlign: "left",
  font: "inherit",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  border: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
  color: "amber",
  cursor: "pointer",
  _hover: { background: "oklch(0.82 0.16 75 / 0.12)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const crumb = css({
  font: "inherit",
  fontSize: "0.76rem",
  color: "cyan",
  background: "transparent",
  border: "0",
  padding: "0",
  marginBottom: "0.5rem",
  cursor: "pointer",
  _hover: { textDecoration: "underline" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

const spine = css({ margin: "0.6rem 0" });
const spineHead = css({
  fontSize: "0.72rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "muted",
  marginBottom: "0.3rem",
});
const lanes = css({ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" });
const lane = cva({
  base: {
    display: "grid",
    gap: "0.25rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "3px",
    padding: "0.4rem 0.5rem",
    borderLeftWidth: "2px",
  },
  variants: { kind: { code: { borderLeftColor: "amber" }, memory: { borderLeftColor: "cyan" } } },
});
const laneTitle = css({ fontSize: "0.76rem", color: "ink" });
const laneRepo = css({ fontSize: "0.74rem", color: "muted" });
const laneMeta = css({
  display: "flex",
  flexWrap: "wrap",
  gap: "0.5rem",
  fontSize: "0.72rem",
  color: "muted",
});

const tokensRow = css({ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.5rem" });
const label = css({
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "muted",
});

const stepsList = css({ listStyle: "none", margin: "0.45rem 0 0", padding: "0", display: "grid", gap: "0.25rem" });
const stepRow = css({
  display: "grid",
  gridTemplateColumns: "auto 1fr",
  alignItems: "baseline",
  gap: "0.45rem",
  fontSize: "0.82rem",
});
const stepMarkBase = css({
  width: "0.62em",
  height: "0.62em",
  alignSelf: "center",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
});
// step status is data-driven; map by record so an unknown status renders as a neutral mark.
const STEP_MARK: Record<string, string> = {
  done: css({ background: "mint", borderColor: "mint" }),
  inProgress: css({ background: "amber", borderColor: "amber" }),
  blocked: css({ background: "alarm", borderColor: "alarm" }),
};
const STEP_TITLE: Record<string, string> = { done: css({ color: "oklch(0.6 0.02 250)" }) };
const substeps = css({
  gridColumn: "2",
  listStyle: "none",
  margin: "0.15rem 0 0",
  padding: "0",
  display: "grid",
  gap: "0.1rem",
  fontSize: "0.76rem",
  color: "muted",
});
const SUBSTEP: Record<string, string> = { inProgress: css({ color: "amber" }) };

const taskdoc = css({ display: "grid", gap: "0.75rem" });
const taskdocHead = css({ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" });
const taskdocTitle = css({ fontWeight: "600" });
const taskdocStatus = css({ color: "cyan", fontSize: "0.8rem" });
const taskdocSection = css({ display: "grid", gap: "0.3rem" });
const taskdocH = css({
  margin: "0",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "amber",
});
const taskdocBullets = css({
  margin: "0",
  paddingLeft: "1.1rem",
  maxWidth: "78ch",
  display: "grid",
  gap: "0.2rem",
  fontSize: "0.84rem",
  lineHeight: "1.45",
});
const taskdocCode = css({
  display: "grid",
  gap: "0.2rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.45rem 0.55rem",
});
const taskdocCodeHead = css({ color: "amber", fontSize: "0.82rem" });
const taskdocCodeMeta = css({ fontSize: "0.78rem", color: "muted" });
const taskdocSnippet = css({
  margin: "0.2rem 0 0",
  padding: "0.5rem 0.6rem",
  background: "bg",
  borderRadius: "2px",
  overflow: "auto",
  fontSize: "0.78rem",
  lineHeight: "1.45",
});
const taskdocDecisions = css({
  listStyle: "none",
  margin: "0",
  padding: "0",
  display: "grid",
  gap: "0.4rem",
  maxWidth: "78ch",
});
const taskdocDecision = css({ fontSize: "0.84rem" });
const taskdocDecisionMeta = css({ fontSize: "0.76rem", color: "muted" });

// The selected lifecycle: phase stepper, the chat-routed Gate Respond drawer or ask fallback, the
// task-document content (analytics.taskDocuments), the lifecycle → worktree → provider spine, and tokens.
export function DetailPanel({
  selectedId,
  onOpenLifecycle,
}: {
  selectedId: string | null;
  onOpenLifecycle?: (id: string) => void;
}) {
  const jump = onOpenLifecycle ?? (() => {});
  const lifecycle = useDashboard((s) => (selectedId ? s.lifecycles[selectedId] : undefined));
  const analytics = useDashboard((s) => s.analytics);
  const enclosures = useDashboard((s) => s.enclosures);
  const providers = useDashboard((s) => s.providers);
  // Drill state lives here (not in TaskContent) so the back control can sit in the sticky panel head.
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  useEffect(() => setOpenSlug(null), [selectedId]); // switching lifecycles closes any open sub-task
  const allDocs = analytics?.taskDocuments ?? [];
  const selectedSeries = selectedId
    ? analytics?.series.find((item) => item.seriesId === selectedId)
    : undefined;

  if (!lifecycle && !selectedSeries) {
    return (
      <Panel testid="detail-panel" title="Detail" className={sizing} fill>
        <EmptyStateBackdrop src="/assets/sc2-battlecruiser-boomerang.mp4">
          Select a task to inspect its phase, gate, and tokens.
        </EmptyStateBackdrop>
      </Panel>
    );
  }

  if (!lifecycle && selectedSeries) {
    const seriesDoc = seriesAsMasterDoc(selectedSeries);
    const seriesSlices = seriesSliceDocs(allDocs, selectedSeries.docPath);
    const openDoc = openSlug ? sliceForSlug(seriesSlices, openSlug) : undefined;
    const head = (
      <>
        <h2>{selectedSeries.title}</h2>
        {openDoc ? (
          <button
            type="button"
            className={crumb}
            onClick={() => setOpenSlug(null)}
            data-testid="series-breadcrumb"
          >
            ← {selectedSeries.title}
          </button>
        ) : null}
      </>
    );

    return (
      <Panel testid="detail-panel" head={head} className={sizing}>
        <div className={where}>series master · {selectedSeries.repository}</div>
        {openDoc ? (
          <TaskReader doc={openDoc} />
        ) : (
          <MasterOverview
            doc={seriesDoc}
            sliceDocs={seriesSlices}
            onOpen={setOpenSlug}
            onJump={jump}
          />
        )}
      </Panel>
    );
  }

  const activeLifecycle = lifecycle;
  if (!activeLifecycle) return null;

  const currentIdx = PHASES.indexOf(activeLifecycle.phase);
  const enclosuresByLifecycle = groupEnclosuresByLifecycle(Object.values(enclosures));
  const enclosure = findLifecycleEnclosure(activeLifecycle, enclosures, enclosuresByLifecycle);
  const directDocs = allDocs.filter((doc) => doc.lifecycleId === activeLifecycle.id);
  const docs = taskDocsForLifecycle(activeLifecycle, allDocs);
  const title = taskLabel(activeLifecycle, directDocs, enclosure);
  const groupName = enclosure ? (enclosure.worktreeGroup.split("/").filter(Boolean).pop() ?? "") : "";
  const engines = groupName
    ? Object.values(providers).filter((p) => p.scope === "worktree" && p.worktreeGroup === groupName)
    : [];

  // Master / slices split + the drilled-into doc. The sticky head carries the title plus the back
  // ("← series") or parent ("↑ parent series") up-link, so navigation stays put while the body scrolls.
  const master = docs.find((doc) => doc.kind === "master");
  const slices = docs.filter((doc) => doc.kind !== "master");
  const openDoc = openSlug ? sliceForSlug(slices, openSlug) : undefined;
  const head = (
    <>
      <h2>{title}</h2>
      {openDoc ? (
        <button
          type="button"
          className={crumb}
          onClick={() => setOpenSlug(null)}
          data-testid="series-breadcrumb"
        >
          ← {master ? master.title : "series"}
        </button>
      ) : master?.masterLifecycleId ? (
        <button
          type="button"
          className={crumb}
          onClick={() => jump(master.masterLifecycleId as string)}
          data-testid="master-parent-link"
        >
          ↑ {master.masterLifecycleId}
        </button>
      ) : null}
    </>
  );

  return (
    <Panel testid="detail-panel" head={head} className={sizing}>
      <div className={where}>
        {activeLifecycle.fleeting
          ? "fleeting · no worktree"
          : `persistent worktree · ${activeLifecycle.repoId ?? "—"}`}
        {activeLifecycle.inferred ? " · inferred" : ""}
      </div>

      <ol className={stepper} aria-label="phase">
        {PHASES.map((phase, i) => (
          <li
            key={phase}
            className={step({ state: i < currentIdx ? "done" : i === currentIdx ? "current" : "todo" })}
          >
            {phase}
          </li>
        ))}
      </ol>

      {activeLifecycle.gate || activeLifecycle.ask ? (
        <GateResponder
          lifecycleId={activeLifecycle.id}
          gateNode={activeLifecycle.gate}
          ask={activeLifecycle.ask}
          testId={activeLifecycle.gate ? "gate-review" : "gate-banner"}
        />
      ) : null}

      {openDoc ? <TaskReader doc={openDoc} /> : <TaskContent docs={docs} onOpen={setOpenSlug} onJump={jump} />}

      {enclosure ? (
        <div className={spine}>
          <div className={spineHead}>worktree · {groupName || enclosure.repoName}</div>
          <div className={lanes}>
            <SpineLane
              kind="code"
              title="code → CGC"
              repo={enclosure.repoName}
              engines={engines.filter((engine) => engine.role === "code")}
            />
            <SpineLane
              kind="memory"
              title="memory → GrepAI"
              repo={`ar-${enclosure.repoName}`}
              engines={engines.filter((engine) => engine.role === "memory")}
            />
          </div>
        </div>
      ) : null}

      <div className={tokensRow}>
        <span className={label}>tokens</span>
        <TokenGauge series={activeLifecycle.tokenSeries} />
      </div>
    </Panel>
  );
}

// Drill-in match key (6g): a SubTaskRef.file / a slice's docPath basename, minus extension. A
// master's index row resolves to the slice doc whose slug equals the ref's file stem.
const stripExt = (name: string): string => name.replace(/\.(md|json)$/i, "");
const sliceSlug = (doc: TaskDocNode): string => stripExt(doc.docPath.split("/").pop() ?? "");
const sliceForSlug = (sliceDocs: TaskDocNode[], slug: string): TaskDocNode | undefined =>
  sliceDocs.find((doc) => sliceSlug(doc) === slug);
const sliceForRef = (
  sliceDocs: TaskDocNode[],
  ref: TaskSubTaskRefNode,
): TaskDocNode | undefined =>
  ref.file ? sliceForSlug(sliceDocs, stripExt(ref.file)) : undefined;
const pathDir = (path: string): string => path.split("/").slice(0, -1).join("/");
const seriesSliceDocs = (sliceDocs: TaskDocNode[], seriesDocPath: string): TaskDocNode[] =>
  sliceDocs.filter((doc) => pathDir(doc.docPath) === pathDir(seriesDocPath));

type MasterDocView = Pick<
  TaskDocNode,
  "kind" | "title" | "status" | "objective" | "subTasks" | "sections" | "decisions" | "masterLifecycleId"
>;

const seriesAsMasterDoc = (seriesNode: SeriesNode): MasterDocView => ({
  kind: "master",
  title: seriesNode.title,
  status: seriesNode.status,
  objective: seriesNode.objective,
  subTasks: seriesNode.subTasks,
  sections: seriesNode.sections,
  decisions: seriesNode.decisions,
});

// The lifecycle's bound task documents (6g). A `master` (contract-paired, no lifecycleId of its
// own) shows its overview + a clickable sub-task index; clicking a slice drills into its full
// reader with a breadcrumb back. A master-less series lists clickable slices; a lone doc reads
// directly. Sub-tasks never enter the sidebar — they are reached here, from the series.
function TaskContent({
  docs,
  onOpen,
  onJump,
}: {
  docs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
}) {
  if (docs.length === 0) {
    return <p className="muted">No task document bound to this task.</p>;
  }
  const master = docs.find((doc) => doc.kind === "master");
  const sliceDocs = docs.filter((doc) => doc.kind !== "master");
  if (master) {
    return <MasterOverview doc={master} sliceDocs={sliceDocs} onOpen={onOpen} onJump={onJump} />;
  }
  if (sliceDocs.length === 1) {
    return <TaskReader doc={sliceDocs[0]} />;
  }
  return <SliceList sliceDocs={sliceDocs} onOpen={onOpen} />;
}

// The master overview: identity + objective, then its ordered render plan (`sections`). A
// `subTasks` section renders the clickable index in place; `sharedDecisions` renders the decision
// table. If no section drives the index but the master carries one, it is appended.
function MasterOverview({
  doc,
  sliceDocs,
  onOpen,
  onJump,
}: {
  doc: MasterDocView;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
}) {
  return (
    <div className={taskdoc}>
      <div className={taskdocHead}>
        <span className={badge}>{doc.kind}</span>
        <span className={taskdocTitle}>{doc.title}</span>
        <span className={taskdocStatus}>{doc.status}</span>
      </div>
      {/* Pinned navigation: the sub-task index sits above the description, always reachable. The
          authored `subTasks` section still renders its own copy in place (MasterSection). */}
      {doc.subTasks.length > 0 ? (
        <Section title="Sub-tasks">
          <SubTaskIndex refs={doc.subTasks} sliceDocs={sliceDocs} onOpen={onOpen} onJump={onJump} />
        </Section>
      ) : null}
      {doc.objective ? (
        <Section title="Objective">
          <Markdown>{doc.objective}</Markdown>
        </Section>
      ) : null}
      {doc.sections.map((section) => (
        <MasterSection
          key={section.heading}
          section={section}
          doc={doc}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
        />
      ))}
    </div>
  );
}

function MasterSection({
  section,
  doc,
  sliceDocs,
  onOpen,
  onJump,
}: {
  section: TaskSectionNode;
  doc: MasterDocView;
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
}) {
  return (
    <Section title={section.heading}>
      {section.body ? <Markdown>{section.body}</Markdown> : null}
      {section.kind === "subTasks" ? (
        <SubTaskIndex
          refs={doc.subTasks}
          sliceDocs={sliceDocs}
          onOpen={onOpen}
          onJump={onJump}
          testidPrefix="subtask-mid"
        />
      ) : null}
      {section.kind === "sharedDecisions" ? <DecisionList items={doc.decisions} /> : null}
    </Section>
  );
}

// The clickable series index: one row per master `SubTaskRef`. A row whose slice has been authored
// as a task document opens its reader; an un-migrated slice shows as a static index row.
function SubTaskIndex({
  refs,
  sliceDocs,
  onOpen,
  onJump,
  testidPrefix = "subtask-open",
}: {
  refs: TaskSubTaskRefNode[];
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
  onJump: (id: string) => void;
  testidPrefix?: string;
}) {
  if (refs.length === 0) {
    return <p className="muted">No sub-tasks indexed.</p>;
  }
  const orderedRefs = orderedByCreation(refs);
  return (
    <ul className={slices}>
      {orderedRefs.map((ref, index) => {
        const ordinal = index + 1;
        const match = sliceForRef(sliceDocs, ref);
        const label = `${ordinal}. ${ref.name}`;
        const meta = (
          <span className={sliceMeta}>
            {match && match.stepsTotal > 0 ? `${match.stepsDone}/${match.stepsTotal} · ` : ""}
            {ref.status}
          </span>
        );
        // A row whose ref points at another master is a parallel/external series → jump lifecycles.
        if (ref.linkedLifecycleId) {
          return (
            <li key={subTaskKey(ref, index)}>
              <button
                type="button"
                className={crossButton}
                onClick={() => onJump(ref.linkedLifecycleId as string)}
                data-testid={`${testidPrefix}-link-${ordinal}`}
                title={`open the ${ref.linkedLifecycleId} series`}
              >
                <span>→ {label}</span>
                {meta}
              </button>
            </li>
          );
        }
        return (
          <li key={subTaskKey(ref, index)}>
            {match ? (
              <button
                type="button"
                className={sliceButton}
                onClick={() => onOpen(sliceSlug(match))}
                data-testid={`${testidPrefix}-${ordinal}`}
              >
                <span>{label}</span>
                {meta}
              </button>
            ) : (
              <div
                className={slice}
                data-testid={`${testidPrefix}-${ordinal}`}
                title="not authored as a task document yet"
              >
                <span>{label}</span>
                {meta}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function subTaskKey(ref: TaskSubTaskRefNode, index: number): string {
  return `${ref.file || ref.name}:${index}`;
}

function orderedByCreation<T extends { createdAt?: string }>(items: T[]): T[] {
  if (!items.every((item) => item.createdAt)) return items;
  return [...items].sort((left, right) =>
    (left.createdAt as string).localeCompare(right.createdAt as string),
  );
}

// Fallback for a series with no master yet: the slice list, now clickable into each reader.
function SliceList({
  sliceDocs,
  onOpen,
}: {
  sliceDocs: TaskDocNode[];
  onOpen: (slug: string) => void;
}) {
  return (
    <div className={series}>
      <div className={taskHead}>
        <span className={badge}>series</span> {sliceDocs.length} task slices
      </div>
      <ul className={slices}>
        {orderedByCreation(sliceDocs)
          .map((doc) => (
            <li key={doc.docPath}>
              <button
                type="button"
                className={sliceButton}
                onClick={() => onOpen(sliceSlug(doc))}
                data-testid={`slice-open-${sliceSlug(doc)}`}
              >
                <span>{doc.title}</span>
                <span className={sliceMeta}>
                  {doc.stepsDone}/{doc.stepsTotal} · {doc.status}
                </span>
              </button>
            </li>
          ))}
      </ul>
    </div>
  );
}

function SpineLane({
  kind,
  title,
  repo,
  engines,
}: {
  kind: "code" | "memory";
  title: string;
  repo: string;
  engines: ProviderNode[];
}) {
  return (
    <div className={lane({ kind })}>
      <div className={laneTitle}>{title}</div>
      <div className={laneRepo}>{repo}</div>
      {engines.length > 0 ? (
        <div className={laneMeta}>
          {engines.map((engine) => (
            <span key={engine.id}>{engine.state}</span>
          ))}
        </div>
      ) : (
        <span className="muted">no isolated engine recorded</span>
      )}
    </div>
  );
}

function TaskReader({ doc }: { doc: TaskDocNode }) {
  return (
    <div className={taskdoc}>
      <div className={taskdocHead}>
        <span className={badge}>{doc.kind}</span>
        <span className={taskdocTitle}>{doc.title}</span>
        <span className={taskdocStatus}>{doc.status}</span>
        <ProgressFill completed={doc.stepsDone} total={doc.stepsTotal} label="steps done" />
      </div>
      {doc.steps.length > 0 ? (
        <Section title="Progress">
          <StepList steps={doc.steps} />
        </Section>
      ) : null}
      {doc.objective ? (
        <Section title="Objective">
          <Markdown>{doc.objective}</Markdown>
        </Section>
      ) : null}
      {doc.requirements.length > 0 ? (
        <Section title="Requirements">
          <Bullets items={doc.requirements} />
        </Section>
      ) : null}
      {doc.design ? (
        <Section title="Design">
          <Markdown>{doc.design}</Markdown>
        </Section>
      ) : null}
      {doc.steps.length > 0 ? (
        <Section title="Implementation steps">
          <StepList steps={doc.steps} />
        </Section>
      ) : null}
      {doc.codeExamples.length > 0 ? (
        <Section title="Proposed code">
          {doc.codeExamples.map((example) => (
            <CodeExample key={example.id} example={example} />
          ))}
        </Section>
      ) : null}
      {doc.decisions.length > 0 ? (
        <Section title="Decision log">
          <DecisionList items={doc.decisions} />
        </Section>
      ) : null}
      {doc.openQuestions.length > 0 ? (
        <Section title="Open questions">
          <Bullets items={doc.openQuestions} />
        </Section>
      ) : null}
      {doc.sections.map((section) => (
        <Section key={`${section.kind}:${section.heading}`} title={section.heading}>
          {section.body ? <Markdown>{section.body}</Markdown> : null}
        </Section>
      ))}
      {doc.references.length > 0 ? (
        <Section title="References">
          <Bullets items={doc.references} />
        </Section>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={taskdocSection}>
      <h3 className={taskdocH}>{title}</h3>
      {children}
    </section>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className={taskdocBullets}>
      {items.map((item) => (
        <li key={item}>
          <Markdown inline>{item}</Markdown>
        </li>
      ))}
    </ul>
  );
}

function StepList({ steps }: { steps: TaskStepNode[] }) {
  return (
    <ol className={stepsList}>
      {steps.map((s) => (
        <li key={s.id} className={stepRow}>
          <span className={cx(stepMarkBase, STEP_MARK[s.status] ?? "")} aria-hidden="true" />
          <span className={STEP_TITLE[s.status] ?? ""}>{s.title}</span>
          {s.substeps.length > 0 ? (
            <ul className={substeps}>
              {s.substeps.map((sub) => (
                <li key={sub.id} className={SUBSTEP[sub.status] ?? ""}>
                  {sub.title}
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function CodeExample({ example }: { example: TaskCodeExampleNode }) {
  return (
    <div className={taskdocCode}>
      <div className={taskdocCodeHead}>{example.title}</div>
      <div className={taskdocCodeMeta}>covers: {example.distinctChange}</div>
      <div className={taskdocCodeMeta}>why: {example.why}</div>
      {example.snippet ? <pre className={taskdocSnippet}>{example.snippet}</pre> : null}
    </div>
  );
}

function DecisionList({ items }: { items: TaskDecisionNode[] }) {
  return (
    <ul className={taskdocDecisions}>
      {items.map((item) => (
        <li key={`${item.at}:${item.decision}`}>
          <div className={taskdocDecision}>
            <Markdown inline>{item.decision}</Markdown>
          </div>
          <div className={taskdocDecisionMeta}>
            {item.at} — <Markdown inline>{item.rationale}</Markdown>
          </div>
        </li>
      ))}
    </ul>
  );
}
