# Skills Reference

Agents Remember ships its skills inside the package. The tool server publishes every
[skill](../concepts.md#skill) as an MCP resource, so a harness reads the skill text from the
running server. It does so through SEP-2640, the skills extension of the Model Context Protocol,
which the tool server declares under the id `io.modelcontextprotocol/skills`.

The [source repository](https://github.com/Foxfire1st/agents-remember) of Agents Remember on
GitHub holds the skills in two trees, the tree that an author edits and the generated copy that
the package serves:

```text
agents-remember/skills/                                  # the source tree, which an author edits
mcp/src/agents_remember/package_data/runtime/skills/     # the generated copy, which the package serves
```

The source tree and the generated copy keep the same paths and bytes. The generated copy is what
an installed build serves. A contributor who edits a skill renews the generated copy with
`python3 scripts/sync-skills.py`;
[CONTRIBUTING.md](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md) holds
the rules for contributors.

## Shipped skills

The folder `skills/` holds one skill.

| Skill | Purpose |
| --- | --- |
| `l-01-agent-lifecycles` | The instructions of the role agents. It holds one role file for each of the eight roles (`architect`, `investigator`, `orchestrator`, `manager`, `worker`, `reviewer`, `curator`, `bootstrap`), nine operation files (each holds the instructions for one kind of work, for example planning, review or closeout), the instructions for a chat without a role, the report templates and the review criteria. The folders `roles/`, `operations/`, `templates/` and `criteria/` of the skill hold the role files, the operation files, the templates and the review criteria; the review criteria are the catalogs by which the Reviewer judges a change. A role agent on the host (Paseo) receives one role file and one operation; see [Role capsules](role-capsules.md). [Roles and role agents](../roles.md) describes the roles and how a chat without a role starts work. |

## Setup And Repository Preparation

The public documentation describes setup and repository preparation:
[Getting Started](../getting-started.md) starts setup, [Onboard An Existing Repository](../guides/onboard-existing-repo.md)
initializes memory, and [Adopt Existing Memory](../guides/adopt-existing-memory.md) adopts the
first baseline. The `l-01-agent-lifecycles` skill carries the role and operation instructions. Its
file
[`reference/curator/repository-foundation.md`](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/repository-foundation.md)
tells the Curator how to write a repository's [knowledge foundation](../concepts.md#knowledge-foundation),
its first set of knowledge records, and its file
[`reference/repository-research.md`](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/repository-research.md)
holds the source inventory, the bounded research and the rules of evidence for that work.

Closing out a task creates the code and memory commits in one transaction, and the worktree
lifecycle keeps landing behind its approval gates; the `l-01-agent-lifecycles` skill holds
the instructions for both.

## Reading Served Skills

A harness that reads MCP resources lists the served skills and reads one file at a time. The
resource `skill://index.json` lists them, and so does the list method of the extension.
Each file of a skill is an MCP resource with the address `skill://<origin>/<skill>/<path>`, which
a harness reads with the MCP method `resources/read`. The origin names the publisher: the tool
server's is `agents-remember/skills`, so the file `SKILL.md` of the shipped skill is
`skill://agents-remember/skills/l-01-agent-lifecycles/SKILL.md`. Two MCP servers that serve one
skill name keep distinct addresses through their origins.

For the first-run path, a chat without a role needs one tool call — `role_start(role="bootstrap",
request_id=<uuid>)` — and no skill read at all: the launched Bootstrap role agent receives its
role capsule with its first message. You make the setup decisions with that role agent in its
chat in the dashboard; at the end it names the Architect as your next step.
