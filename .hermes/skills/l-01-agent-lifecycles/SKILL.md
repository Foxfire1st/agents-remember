---
name: l-01-agent-lifecycles
description: "Route explicit role capsules for canonical AR work or taskless Projects Architect and System Specialist launches on the Paseo host."
---

# Role-capsule router

This is a thin router. It does not define a shared lifecycle or inject shared core instructions. A role capsule contains exactly the selected `roles/<role>.md` and one applicable `operations/<operation>.md`; canonical task facts and workspace bindings arrive separately in the AR handover. `composition-manifest.json` is the routing metadata authority.

## Select the supplied role and operation

The role launcher supports these seven role IDs: `architect`, `system-specialist`, `orchestrator`, `manager`, `worker`, `reviewer`, and `curator`. The manifest retains other registry entries, but this router does not launch them. If the handover lacks a supported role or one applicable operation, report the exact missing or unsupported field in your own chat and stop. Do not infer a role, operation, task, repository, or owner from a chat title, runtime directory, or nearby document.

For a task-bound launch, use only the canonical task reference and paired workspace supplied in the handover. Follow that role and operation's exact task-read arguments. Do not manufacture task IDs or parent relationships. Projects is a shared execution workspace, not a repository identity; leaf roles use the selected AR-paired task enclosure already bound by the launcher.

Architect and System Specialist may be started manually at Projects altitude without task references. A taskless Architect asks only for missing outcome or registered repository details. A taskless System Specialist asks only for the missing provider/system concern or report scope. Neither creates a fake sprint, master, or task. Put those questions in your own chat.

## Host, tools and decisions

Paseo is the host: it runs each role agent, keeps its session and shows its chat to the developer. AR owns which role serves which task. Call every AR tool on the tool server named `agents-remember-task`, which the AR build that launched you started for you; its tools are in your session. A harness shows them in its own way, so find them by the server's name in either spelling, `agents-remember-task` or `agents_remember_task`: as tools declared under a prefixed name (for example `mcp__agents_remember_task__server_info`), or, when your harness's own instructions list a server in that spelling behind its tool search or script tool, the way those instructions say. A tool that lists or proxies tool servers holds only the servers it was configured with: unless it lists `agents-remember-task` itself, do not use it for AR tools at all, not even to search for or describe a tool by its name, and take its answer "server not found" as speaking only for that tool. Do not connect to, list, describe or call a tool server named `agents-remember` or any of its tools: it belongs to another installation, and its tools carry the same tool names. A tool server can take some seconds to appear after a start or a resume: wait a few seconds and look once more before you report it missing.

Two tools on `agents-remember-task` connect role agents:

```text
role_start    start one role agent for one canonical selection, with you as its parent
role_message  send one message to one role agent; with wait, receive its reply
```

Use the agent IDs these tools return and the sender line of each message you receive; never invent a sender, recipient, parent, or delivery result. A message another agent sent you begins with a line `From <role> · <task> · agent <agent ID>`: your reply in that turn, in your own chat, is what the sender receives, so answer the message there. Do not create or message role agents any other way: an agent created outside `role_start` on `agents-remember-task` has no capsule and no binding. Put every question for the developer in your own chat, as your reply in this session; the developer reads it there and answers there. As the exception, an Orchestrator or Manager started by another agent sends what needs the developer's decision to that parent with `role_message` on `agents-remember-task`, following its handover's developerQuestions rule, keeps working and does not end its turn on the question; this exception takes precedence over the own-chat sentence for those two roles with a parent. AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired Git operations.

An Architect first delegates coordination to one Manager for one master, or to one Orchestrator on the sprint when two or more masters are worked on at the same time. The Orchestrator starts one Manager per master. Only the developer may choose direct coordination by the Architect. The developer may also ask for an Orchestrator above a single master. A role started from the dashboard has no parent agent and needs none. Existing approvals and rulings remain durable across reconnects and compaction; ask again only for new or changed scope, a real requirement conflict, or an unresolved human-pinned decision.

## Resume and report

After compaction or reconnect, restore the same role, operation, task reference, agent IDs, and report path from the handover artifact and durable records. Reconcile an uncertain start by repeating `role_start` on `agents-remember-task` with the same request ID before anything else; do not create a second owner or ask the developer to repeat supplied details. A finished turn, review, curation, semantic acceptance, and paired Git publication are separate facts. Report each only from its owning evidence.

## Inside an assignment

The roles order work at the boundaries of tasks and their execution graph: assignment, hand-over, independent review, curation and landing. Inside an assignment the seat organises its own work with its harness, including sub-agents; the seat answers for their work and alone performs boundary acts. A sub-agent holds no AR seat, starts no role, shares the seat's working folder, permissions and assignment, and cannot supply independent review of its seat's own work. Curator authoring still uses one writer.
