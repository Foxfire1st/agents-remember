const WORKSPACE_ROOT = "<PATH/TO/YOUR/PROJECTS_FOLDER>";

function setupRequired(systemPrompt) {
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

export default function (pi) {
  pi.on("before_agent_start", (event) => {
    if (WORKSPACE_ROOT.startsWith("<")) {
      return setupRequired(event.systemPrompt);
    }

    const directive = [
      "MANDATORY FIRST ACTION for this workspace",
      "",
      "You are not allowed to read, write, or execute code on any repository",
      "until you read `ar-coordination/AGENTS.md` and started its `l-01` procedure.",
      "",
      `Read and follow \`${WORKSPACE_ROOT}/ar-coordination/AGENTS.md\` before working in any sibling project.`,
      "Treat those rules as workspace instructions.",
      "",
      "Until the build/job decision is made, read managed-repo source with the",
      "`read_ar_files` MCP tool — not the harness's native read tool. One call returns",
      "each file paired with its onboarding plus the repository and governing route",
      "overviews. Native read is reserved as the edit precondition once building begins.",
      "",
      "During the `l-01` deep research step, keep a tally of the retrieval strategies",
      "used from `c-04-retrieval-strategy-router` (Semantics, Relationship, Intent), and",
      "keep a running count of your `read_ar_files` calls. Include the onboarding files",
      "inspected and every CGC, GrepAI, and `read_ar_files` call made as evidence."
    ].join("\n");

    return {
      systemPrompt: `${event.systemPrompt}\n\n${directive}`
    };
  });
}
