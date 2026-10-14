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
      "You are the developer-facing **free chat**: read",
      `\`${WORKSPACE_ROOT}/ar-coordination/AGENTS.md\` as workspace`,
      "instructions. Answer a question inline; for work on a managed repository,",
      "create or resolve the sprint document, write the request of the developer into",
      "it, start an Architect with `role_start` (the call for an Architect names no",
      "document), and tell the developer that the agent is in the dashboard and which",
      "sprint document holds the request. Another role is started this way only when",
      "the developer names the role and the task.",
      "",
      "An agent whose first message is the hand-over of a role is not meant by this",
      "notice."
    ].join("\n");

    return {
      systemPrompt: `${event.systemPrompt}\n\n${directive}`
    };
  });
}
