// Native @getpaseo/plugin/server 0.12.0 lifecycle + PaseoApi.observeEvents.
// No roles, prompts, approvals, agent state changes or subscription snapshots
// invented as helper events. Every recorded native object is retained unchanged.
import {
  appendFileSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import type {
  PluginHandlerContext,
  PluginServerContext,
  PluginSessionOpenRequest,
} from "@getpaseo/plugin/server";

interface WindowRecord {
  agentId: string;
  projectRoot: string;
  readyAt: string | null;
  lastObservedAt: string | null;
  lastTurnEndedAt: string | null;
  errors: string[];
}
type Observation = ReturnType<PluginHandlerContext["paseo"]["observeEvents"]>;

class NativeRecorder {
  readonly root: string;
  private readonly owners = new Map<string, WindowRecord>();
  private observation: Observation | undefined;
  private readiness: Promise<unknown> | undefined;
  private snapshots = 0;
  private stopped = false;
  private readonly failures: string[] = [];

  constructor(home: string) {
    this.root = path.join(home, "agent-eval");
    mkdirSync(this.root, { recursive: true });
  }

  private marker(owner: WindowRecord) {
    writeFileSync(
      path.join(this.root, `${owner.agentId}.window.json`),
      JSON.stringify(owner),
    );
  }

  private fail(reason: unknown) {
    this.failures.push(String(reason));
    for (const owner of this.owners.values()) {
      owner.errors.push(String(reason));
      this.marker(owner);
    }
  }

  record(native: { type: string }, ownerId: unknown) {
    if (typeof ownerId !== "string") return;
    const owner = this.owners.get(ownerId);
    if (!owner) return;
    const observedAt = new Date().toISOString();
    // Real host callback clock, never a claimed provider event-source time.
    appendFileSync(
      path.join(this.root, "host-events.jsonl"),
      JSON.stringify({ observedAt, native }) + "\n",
    );
    owner.lastObservedAt = observedAt;
    if (native.type === "agent.turn_ended") owner.lastTurnEndedAt = observedAt;
    this.marker(owner);
  }

  private async attach(paseo: PluginHandlerContext["paseo"]) {
    if (this.stopped) throw new Error("eval recorder has stopped");
    if (this.failures.length) throw new Error(this.failures.join("; "));
    if (!this.readiness) {
      this.observation = paseo.observeEvents(
        ["agent.provider_subagents.update"],
        { notifications: false },
      );
      this.observation.subscribe({
        snapshot: () => {
          if (this.snapshots++ > 0)
            this.fail(
              "host event subscription restarted; events may be missing",
            );
        },
        update: (native) => {
          if (native.type !== "agent.provider_subagents.update") return;
          const payload = native.payload;
          const owner =
            payload.kind === "upsert"
              ? payload.subagent.parentAgentId
              : payload.parentAgentId;
          this.record(native, owner);
        },
        error: (error) => this.fail(error),
      });
      this.readiness = this.observation.ready;
    }
    await this.readiness;
  }

  async open(
    request: PluginSessionOpenRequest,
    paseo: PluginHandlerContext["paseo"],
  ) {
    const settings = JSON.parse(
      readFileSync(path.join(this.root, "recorder-settings.json"), "utf8"),
    );
    if (request.cwd !== settings.projectRoot) return;
    const owner = this.owners.get(request.agentId) ?? {
      agentId: request.agentId,
      projectRoot: request.cwd,
      readyAt: null,
      lastObservedAt: null,
      lastTurnEndedAt: null,
      errors: [],
    };
    this.owners.set(request.agentId, owner);
    await this.attach(paseo);
    if (!owner.readyAt) owner.readyAt = new Date().toISOString();
    this.marker(owner);
  }

  async stop() {
    this.stopped = true;
    this.fail("host recorder stopped");
    if (this.observation) await this.observation.release();
  }
}

export default function contribute(server: PluginServerContext) {
  const home = process.env.PASEO_HOME;
  if (!home)
    throw new Error("eval recorder needs the explicitly configured PASEO_HOME");
  const recorder = new NativeRecorder(home);
  const releaseOpen = server.before(
    "agent.session_open",
    async ({ request }, context) => {
      await recorder.open(request, context.paseo);
    },
  );
  const releaseEnd = server.on("agent.turn_ended", (event) => {
    recorder.record({ type: "agent.turn_ended", ...event }, event.agent.id);
  });
  return async () => {
    releaseOpen();
    releaseEnd();
    await recorder.stop();
  };
}
