// The slice of Pi's extension API this file uses. Pi ships no type package to install beside a
// project-local extension, so the contract it hands us is written down here instead of being
// implied by untyped parameters -- `tsconfig.json` in this directory checks the file against it.
interface BeforeAgentStartEvent {
  systemPrompt: string;
}

interface BeforeAgentStartResult {
  systemPrompt: string;
  message?: {
    customType: string;
    content: string;
    display: boolean;
  };
}

interface Pi {
  on(
    event: "before_agent_start",
    handler: (event: BeforeAgentStartEvent) => BeforeAgentStartResult,
  ): void;
}

const WORKSPACE_ROOT = "<PATH/TO/YOUR/PROJECTS_FOLDER>";

function setupRequired(systemPrompt: string): BeforeAgentStartResult {
  const message = [
    "Agents Remember setup is incomplete.",
    "Replace <PATH/TO/YOUR/PROJECTS_FOLDER> in .pi/extensions/agents-remember-start.ts before using this starter package."
  ].join(" ");

  return {
    message: {
      customType: "agents-remember-setup",
      content: message,
      display: true
    },
    systemPrompt: `${systemPrompt}\n\n${message}`
  };
}

export default function (pi: Pi): void {
  pi.on("before_agent_start", (event: BeforeAgentStartEvent): BeforeAgentStartResult => {
    if (WORKSPACE_ROOT.startsWith("<")) {
      return setupRequired(event.systemPrompt);
    }

    const directive = [
      "**Agents Remember — session start.**",
      "",
      "If `AR_SPAWN_ROLE` is set, or your first user message is a role brief from an",
      "orchestrating agent: **ignore this notice entirely — your brief is your session",
      "start.**",
      "",
      "Otherwise you are the developer-facing session, i.e. the **architect**: read",
      `\`${WORKSPACE_ROOT}/ar-coordination/AGENTS.md\` and treat those rules as workspace`,
      "instructions, then run your lifecycle at",
      "`skills/l-01-agent-lifecycles/roles/architect.md` — trust checkpoint before",
      "relying on memory, `read_ar_files` (paired source+onboarding) until the build",
      "decision, retrieval-strategy tally as evidence, notify-and-stop at every",
      "developer hand-off."
    ].join("\n");

    return {
      systemPrompt: `${event.systemPrompt}\n\n${directive}`
    };
  });
}
