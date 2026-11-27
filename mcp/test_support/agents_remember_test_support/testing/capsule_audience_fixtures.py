"""Reviewed source-audience fixtures for capsule action and delivery checks.

Data only: source pins, action/source relations and exact Curator clauses.
The consumer tests own all compilation, identity and negative assertions.
"""

# Source-reviewed action prefixes; metadata step descriptions are not wording oracles.
# Fixed reviewed source and per-recipient byte pins. The expected projections were
# calculated from the retained source snapshot before running these tests; tests never
# read current audience markers to decide their expected recipients.

ROLE_POINTER_ACTIONS = {
    "architect": [
        (
            "reference/coordination/shared/requirement-design.md",
            "| Reqs/`task_doc`/`context_packet`/`knowledge_read` |",
        ),
        (
            "reference/tasks/document-authoring.md",
            "| Reqs/`task_doc`/`context_packet`/`knowledge_read` |",
        ),
        (
            "reference/knowledge/commands.md",
            "| Reqs/`task_doc`/`context_packet`/`knowledge_read` |",
        ),
        (
            "reference/knowledge/use.md",
            "| Reqs/`task_doc`/`context_packet`/`knowledge_read` |",
        ),
        (
            "reference/coordination/architect/models-and-settings.md",
            "| Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd |",
        ),
        (
            "reference/sandbox-actors.md",
            "| Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd |",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "| Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd |",
        ),
        (
            "reference/coordination/shared/seat-recovery.md",
            "| Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd |",
        ),
        (
            "reference/coordination/shared/third-rejected-round.md",
            "| Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd |",
        ),
        (
            "reference/coordination/architect/process-decisions.md",
            "| Process, claims, dev/child Qs, `role_message` |",
        ),
        (
            "reference/coordination/architect/claim-verification.md",
            "| Process, claims, dev/child Qs, `role_message` |",
        ),
        (
            "reference/coordination/architect/developer-decisions.md",
            "| Process, claims, dev/child Qs, `role_message` |",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "| Process, claims, dev/child Qs, `role_message` |",
        ),
        (
            "reference/message-handover.md",
            "| Process, claims, dev/child Qs, `role_message` |",
        ),
        (
            "reference/coordination/architect/curator-measurement.md",
            "| Meas., advisory, flat closeout |",
        ),
        (
            "reference/review/advisory-reviews.md",
            "| Meas., advisory, flat closeout |",
        ),
        (
            "reference/coordination/shared/closeout-recovery.md",
            "| Meas., advisory, flat closeout |",
        ),
    ],
    "orchestrator": [
        (
            "reference/coordination/shared/seat-recovery.md",
            "| Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), "
            "master/decisions (`task_doc`), closeout |",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "| Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), "
            "master/decisions (`task_doc`), closeout |",
        ),
        (
            "reference/tasks/document-authoring.md",
            "| Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), "
            "master/decisions (`task_doc`), closeout |",
        ),
        (
            "reference/coordination/shared/closeout-recovery.md",
            "| Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), "
            "master/decisions (`task_doc`), closeout |",
        ),
        (
            "reference/knowledge/commands.md",
            "| `knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs |",
        ),
        (
            "reference/message-handover.md",
            "| `knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs |",
        ),
        (
            "reference/sandbox-actors.md",
            "| `knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs |",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "| `knowledge_read`/`context_packet`, `role_message`, sandbox, child Qs |",
        ),
    ],
    "manager": [
        (
            "reference/coordination/shared/seat-recovery.md",
            "| Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), "
            "switch (`worktree_closeout_*`) |",
        ),
        (
            "reference/coordination/shared/third-rejected-round.md",
            "| Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), "
            "switch (`worktree_closeout_*`) |",
        ),
        (
            "reference/worker/sync-and-repairs.md",
            "| Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), "
            "switch (`worktree_closeout_*`) |",
        ),
        (
            "reference/coordination/shared/closeout-recovery.md",
            "| Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), "
            "switch (`worktree_closeout_*`) |",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
        (
            "reference/tasks/document-authoring.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
        (
            "reference/refused-write.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
        (
            "reference/knowledge/commands.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
        (
            "reference/message-handover.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
        (
            "reference/sandbox-actors.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
            "msgs, sandbox, child |",
        ),
    ],
    "worker": [
        ("reference/knowledge/use.md", "| Knowledge/CLI |"),
        ("reference/knowledge/commands.md", "| Knowledge/CLI |"),
        ("reference/knowledge/serving-build.md", "| Knowledge/CLI |"),
        ("reference/tasks/document-authoring.md", "| Task/report/list |"),
        ("templates/turn-report.md", "| Task/report/list |"),
        ("reference/writing/visual-explainers.md", "| Task/report/list |"),
        ("reference/message-handover.md", "| Msg/sync/repairs |"),
        ("reference/worker/sync-and-repairs.md", "| Msg/sync/repairs |"),
        ("reference/sandbox-actors.md", "| Sandbox/ask |"),
    ],
    "curator": [
        ("reference/knowledge/use.md", "| Knowledge/CLI |"),
        ("reference/knowledge/commands.md", "| Knowledge/CLI |"),
        ("reference/knowledge/serving-build.md", "| Knowledge/CLI |"),
        ("reference/curator/intent-reconciliation.md", "| Intake/worklist |"),
        ("reference/curator/worklist.md", "| Intake/worklist |"),
        ("reference/curator/knowledge-ingest.md", "| Write/route |"),
        ("reference/curator/publication-report.md", "| Write/route |"),
        ("reference/curator/family-obligations.md", "| Write/route |"),
        ("reference/curator/change-routing.md", "| Write/route |"),
        ("reference/curator/encountered-records.md", "| Write/route |"),
        ("reference/curator/current-prose-and-history.md", "| Cards/style |"),
        ("reference/curator/card-sentence-decisions.md", "| Cards/style |"),
        ("reference/curator/card-formats-and-citations.md", "| Cards/style |"),
        ("reference/curator/governing-sources.md", "| Cards/style |"),
        ("reference/writing/technical-english-profile.md", "| Cards/style |"),
        ("reference/curator/coherence-judgments.md", "| Coherence/msg |"),
        ("reference/curator/memory-handover.md", "| Coherence/msg |"),
        ("reference/message-handover.md", "| Coherence/msg |"),
        ("reference/curator/exceptions-and-recovery.md", "| Exceptions/sync |"),
        ("reference/curator/repository-foundation.md", "| Exceptions/sync |"),
        ("reference/worker/sync-and-repairs.md", "| Exceptions/sync |"),
        ("reference/sandbox-actors.md", "| Exceptions/sync |"),
    ],
    "reviewer": [
        ("reference/review/non-leaf-seams-and-catalogs.md", "| Non-leaf/rounds |"),
        ("reference/review/rounds-and-late-findings.md", "| Non-leaf/rounds |"),
        ("reference/review-baseline.md", "| Baseline/knowledge |"),
        ("reference/knowledge/use.md", "| Baseline/knowledge |"),
        ("reference/knowledge/commands.md", "| Baseline/knowledge |"),
        ("reference/message-handover.md", "| Templates & msg |"),
        ("reference/sandbox-actors.md", "| Sandbox/ask |"),
    ],
    "consolidator": [
        ("reference/knowledge/use.md", "| Knowledge/CLI |"),
        ("reference/knowledge/commands.md", "| Knowledge/CLI |"),
        ("reference/consolidation-pass.md", "| Pass/worklist |"),
        ("reference/curator/worklist.md", "| Pass/worklist |"),
        ("reference/curator/knowledge-ingest.md", "| Write/families |"),
        ("reference/curator/publication-report.md", "| Write/families |"),
        ("reference/curator/family-obligations.md", "| Write/families |"),
        ("reference/curator/governing-sources.md", "| Write/families |"),
        ("reference/writing/technical-english-profile.md", "| Write/families |"),
        ("reference/curator/coherence-judgments.md", "| Coherence/msg |"),
        ("reference/message-handover.md", "| Coherence/msg |"),
        ("reference/worker/sync-and-repairs.md", "| Exceptions/sync |"),
        ("reference/sandbox-actors.md", "| Exceptions/sync |"),
    ],
    "investigator": [
        ("reference/knowledge/commands.md", "| Knowledge readers |"),
        ("reference/knowledge/use.md", "| Knowledge readers |"),
        ("reference/instruction-shortfall-review.md", "| Research/shortfalls/msg |"),
        ("reference/sandbox-actors.md", "| Research/shortfalls/msg |"),
        ("reference/message-handover.md", "| Research/shortfalls/msg |"),
    ],
    "bootstrap": [
        ("reference/bootstrap/foundation-state.md", "| Report, `memory_init`, cmds |"),
        ("reference/knowledge/commands.md", "| Report, `memory_init`, cmds |"),
        ("reference/sandbox-actors.md", "| Settings, sandbox |"),
    ],
}

OPERATION_POINTER_ACTIONS = {
    "orientation": {
        "architect": [],
        "orchestrator": [],
        "manager": [],
        "worker": [],
        "curator": [],
        "reviewer": [],
        "consolidator": [],
        "investigator": [],
        "bootstrap": [],
    },
    "planning": {"architect": [], "orchestrator": []},
    "implementation": {"worker": []},
    "review": {"reviewer": [], "manager": [], "orchestrator": [], "architect": []},
    "curation": {
        "curator": [],
        "manager": [],
    },
    "coordination": {"architect": [], "orchestrator": [], "manager": []},
    "authorized-closeout": {"manager": [], "orchestrator": [], "architect": []},
    "recovery": {
        "architect": [],
        "orchestrator": [],
        "manager": [],
        "worker": [],
        "curator": [],
        "investigator": [],
        "bootstrap": [],
        "reviewer": [],
        "consolidator": [],
    },
    "consolidation": {"consolidator": []},
    "bootstrap": {
        "bootstrap": [
            (
                "reference/bootstrap/failure-states.md",
                "When hitting unfamiliar states (`memory_init`/`memory_baseline_status`/"
                "`user_settings_init`/`server_info`/`context_packet`/`runtime_install`), read",
            ),
        ]
    },
}


D656_OPERATION_SHA256 = {
    "orientation": "107f06ea46e2733be46cf43b46a47297a89e9112e6c07035278c27ccd9722d66",
    "planning": "3c23ea1f2b8b0415182630e6eccb28a7e5c3e6244c3b5964b19395bc35cb4d82",
    "implementation": "bf4fc5b611fabe7612934c58db459b0f291a9e3913b94adf1118509910eb2bd8",
    "review": "88fba52076f3f7d9e7b4148fc218b64ff9f241bc60a367d695dec096ca500eac",
    "curation": "21f27c8c6dc62274cb6f72363fee217b1594a21c63302b128fd37320f37d919d",
    "coordination": "6632ef75216968f20519012c038b9f8fa95553b6fee03d9ded1545fd1c313bd4",
    "authorized-closeout": "ff015323b5061eae86418af057b9695da9ac91ec37313b5e402f3e8129782b66",
    "recovery": "e5707af48d38d94eafcc9458cf6a7dc87c094c9563eb3b899efc527171abe69f",
    "bootstrap": "673691f0b744343bfb0be5e08506a8ebec19bd4d7e7a81438f48306c930efe9e",
}

D656_STAGE_SHA256 = {
    "orientation/architect": "3540125acebc32669bd8b7757e96470e646cd4f610d69585692a9e0a1250e747",
    "orientation/orchestrator": "ca95de826e9ea96dd1b183d81acfdc08419069dcf0336fa3286bd7327b6b3a85",
    "orientation/manager": "ca95de826e9ea96dd1b183d81acfdc08419069dcf0336fa3286bd7327b6b3a85",
    "orientation/worker": "e229cad7a791853bbe94f5b6d033257c6421335a6b4350bc92e59a83cd5b87e1",
    "orientation/curator": "a0a07f1c6a8a42cb155c707f5011502d6d3a54454c96827beb42ef48c3b59a02",
    "orientation/reviewer": "ca95de826e9ea96dd1b183d81acfdc08419069dcf0336fa3286bd7327b6b3a85",
    "orientation/investigator": "b7cae92c304f45eb8c344d6020cb21025a150b96ffe663554964e3bb2e5dc70f",
    "orientation/bootstrap": "85495ca8b48fa4b642527224613f29321209acacd8249de265f789422aadee85",
    "planning/architect": "42ca7b32b119f66ad21ed43b5d985b997bccea1787c4190b7e54a44f4622bd8d",
    "planning/orchestrator": "1d43e30ca193e814e73f1881ffc756075bd6a1116b19b0240587084cae756ffc",
    "implementation/worker": "4d94f63806f06d1d9669b882bfdccd3360016efaa6671c85e8e651abbbe47414",
    "review/reviewer": "e57cd885f31f3a1118a6ce3675a03067b4797433c8af78726fb1015bdd39f430",
    "review/manager": "ae9f97119e5bd164559b002067bff3b8ccac333aee977429a5429ecce8ecaffe",
    "review/orchestrator": "ae9f97119e5bd164559b002067bff3b8ccac333aee977429a5429ecce8ecaffe",
    "review/architect": "066fcb1673508bfdfdbdd632047938b3b0377545004c7e179e2d8e71efe6c729",
    "curation/curator": "f1327be96dca213b4dfaf5531c8bbe8c87c1f5bf08dfd2dcf6694c7e900ad3e6",
    "curation/manager": "c89da0e26c3b686ea7c0c2922389db33acd9cd80904224d3a553aa8f2f7d5801",
    "coordination/architect": "9308cbc27bae88da12c58f17aec28294fbdbae63798313a661eabf1210d9eabb",
    "coordination/orchestrator": "a34729d0c5451ba9db9023df628b265fac096520854ce79b90921b87620fa30f",
    "coordination/manager": "727aa405a0d0dff8308a9015f824df822943184a1fca48c356d6c39734682b5d",
    "authorized-closeout/manager": "ff015323b5061eae86418af057b9695da9ac91ec37313b5e402f3e8129782b66",
    "authorized-closeout/orchestrator": "ff015323b5061eae86418af057b9695da9ac91ec37313b5e402f3e8129782b66",
    "authorized-closeout/architect": "ff015323b5061eae86418af057b9695da9ac91ec37313b5e402f3e8129782b66",
    "recovery/architect": "48ce2a62c747c3393e05a52acdbf8cb74c07cb9a00533008c17be6c11b37d664",
    "recovery/orchestrator": "48ce2a62c747c3393e05a52acdbf8cb74c07cb9a00533008c17be6c11b37d664",
    "recovery/manager": "48ce2a62c747c3393e05a52acdbf8cb74c07cb9a00533008c17be6c11b37d664",
    "recovery/worker": "33e3cd99034cfb3426c59996174eafd7b14964cb66ab7f96a0502b7d5f940b67",
    "recovery/curator": "5d5df6e3abcca20bd988a7462d111bf201e92ba6c6113e952d4ca96d2d3db6e7",
    "recovery/investigator": "33e3cd99034cfb3426c59996174eafd7b14964cb66ab7f96a0502b7d5f940b67",
    "recovery/bootstrap": "c770ba6d2024ae702664e3340a30f1aaa7f153ae8b3f9e6fdcced923fcaea285",
    "recovery/reviewer": "33e3cd99034cfb3426c59996174eafd7b14964cb66ab7f96a0502b7d5f940b67",
    "bootstrap/bootstrap": "0a2e9bf4e8ea94c818fa302c5ab5604475c5cb999614cc397fe45752ab51a8d4",
}

R125_OPERATION_ROLES = {
    "orientation": (
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "consolidator",
        "investigator",
        "bootstrap",
    ),
    "planning": ("architect", "orchestrator"),
    "implementation": ("worker",),
    "review": ("reviewer", "manager", "orchestrator", "architect"),
    "curation": ("curator", "manager"),
    "coordination": ("architect", "orchestrator", "manager"),
    "authorized-closeout": ("manager", "orchestrator", "architect"),
    "recovery": (
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "investigator",
        "bootstrap",
        "reviewer",
        "consolidator",
    ),
    "consolidation": ("consolidator",),
    "bootstrap": ("bootstrap",),
}

CURATOR_MESSAGE_TIMING = ("| Coherence/msg |",)

CURATOR_MESSAGE_CONTENT = (
    "## Send a message that names the files",
    "Put the full message text in `body` and its files in `attach`.",
    "Keep the short `text` field within the settings' limit.",
    "### Message folder",
    "Messages are filed in the narrowest task shared by sender and recipient:",
    "same-leaf messages: `notes/messages/<leaf-slug>/`;",
    "shared-master or shared-sprint messages: that task's `notes/messages/`.",
    "A leaf workspace links that folder as `messages`.",
)

CURATOR_SERVING_STAGE_BOUNDARY = (
    "Decide/write via starting server or serving-build command-line interface (CLI) against "
    "clean main (name commit); leaf source never produces leaf memory (test on fixtures/scratch); "
    "install nothing, disable no guard."
)


# Reviewed semantic scope fields and required interpretation remain tested by deletion.
CURATOR_SEMANTIC_SCOPE_CLAUSES = (
    "Before an entry becomes a durable invariant, the curator adds `scope` alongside the producer's\nunchanged fields.",
    '"scope": {',
    '"applicability": "Retry attempts admitted by the shared deadline budget.",',
    '"conditions": ["The caller supplies its remaining deadline."],',
    '"exclusions": ["Interactive retries outside this budget."]',
    "All three keys are required; `[]` means the curator examined that field and found no clauses.",
    "Applicability describes\nwhere the obligation holds; conditions and exclusions describe its real boundaries.",
    "disposition and evidence provenance are not substitutes for semantic scope.",
)


# Section fragments and suffixes pinned on role index links.
ROLE_POINTER_QUALIFICATIONS = {
    (
        "architect",
        "reference/knowledge/use.md",
        "| Reqs/`task_doc`/`context_packet`/`knowledge_read` |",
    ): ("#architect-diagnose-before-proposing-a-repair", ""),
    (
        "architect",
        "reference/coordination/shared/closeout-recovery.md",
        "| Meas., advisory, flat closeout |",
    ): ("#flat-mode-direct-paired-closeout", ""),
    (
        "orchestrator",
        "reference/coordination/shared/closeout-recovery.md",
        "| Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), "
        "master/decisions (`task_doc`), closeout |",
    ): ("#recover-an-unfinished-closeout", ""),
    (
        "orchestrator",
        "reference/tasks/document-authoring.md",
        "| Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), "
        "master/decisions (`task_doc`), closeout |",
    ): ("#preserve-decisions-and-approved-intent", ""),
    (
        "manager",
        "reference/coordination/shared/closeout-recovery.md",
        "| Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), "
        "switch (`worktree_closeout_*`) |",
    ): ("#orchestrator-recipient-switch-and-closeout-notices", ""),
    (
        "manager",
        "reference/worker/sync-and-repairs.md",
        "| Seats (`retire_child`), 3rd, sync (`worktree_closeout_preview`), "
        "switch (`worktree_closeout_*`) |",
    ): ("#synchronize-the-leaf", ""),
    (
        "manager",
        "reference/refused-write.md",
        "| Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, "
        "msgs, sandbox, child |",
    ): ("#apply-the-procedure-to-a-task-document-or-folder", ""),
    (
        "worker",
        "templates/turn-report.md",
        "| Task/report/list |",
    ): ("#shape", ""),
    (
        "curator",
        "reference/worker/sync-and-repairs.md",
        "| Exceptions/sync |",
    ): ("#synchronize-the-leaf", ""),
    (
        "investigator",
        "reference/knowledge/use.md",
        "| Knowledge readers |",
    ): ("#investigator-distinguish-claims-observations-and-hypotheses", ""),
    (
        "investigator",
        "reference/message-handover.md",
        "| Research/shortfalls/msg |",
    ): ("#send-a-message-that-names-the-files", ""),
    (
        "bootstrap",
        "reference/bootstrap/foundation-state.md",
        "| Report, `memory_init`, cmds |",
    ): ("#read-the-state", ""),
}


# Fixed v13 owning sections; original clauses remain checked after inlining.
INLINE_RULE_CLAUSES = (
    (
        "reference/coord-topic-architect-bound-brief.md",
        "reference/coordination/architect/process-decisions.md",
        "## Keep briefs, masters, and side problems bounded",
        (
            "Keep every code or feature brief bounded to what was asked: a sighting next to the task "
            "does not widen it (the Curator's encountered-memory healing retains its separate rule).",
        ),
    ),
    (
        "reference/coord-topic-architect-bound-side-problem.md",
        "reference/coordination/architect/process-decisions.md",
        "## Keep briefs, masters, and side problems bounded",
        (
            "Give a hard, open-ended side problem a separate bounded step instead of folding it into "
            "another round.",
        ),
    ),
    (
        "reference/coord-topic-architect-compute-cost.md",
        "reference/coordination/architect/process-decisions.md",
        "## Weigh compute cost, review shortfalls, and document harness setup",
        (
            "Weigh context, machine, and paid-compute cost and choose the cheap shape; escalate gross "
            "waste once with a measurement and a proposed fix.",
        ),
    ),
    (
        "reference/coord-topic-architect-developer-check-failure.md",
        "roles/architect.md",
        "## Establish the work",
        ("Never bypass checks or freeze animations; gate repairs never free tasks under test.",),
    ),
    (
        "reference/coord-topic-architect-instruction-review.md",
        "reference/coordination/architect/process-decisions.md",
        "## Weigh compute cost, review shortfalls, and document harness setup",
        (
            "Offer the developer an instruction-shortfall review about every two weeks and at each "
            "master's end, and count a fix as made only when its words reach compiled first messages.",
        ),
    ),
    (
        "reference/coord-topic-architect-master-scope.md",
        "reference/coordination/architect/process-decisions.md",
        "## Keep briefs, masters, and side problems bounded",
        (
            "Finish the master's approved subject; do not park admitted work for a follow-up master "
            "or trim its promise to converge.",
        ),
    ),
    (
        "reference/coord-topic-architect-master-status-read.md",
        "reference/coordination/shared/master-state.md",
        "## Update the master after starts, handovers and landings",
        (
            "At each status read, compare the master page with the Manager's last handover, renew "
            "lagging parts and tell the Manager.",
            "Identify stalls from task progress: fifteen minutes without an active turn, or a ready "
            "task left unstarted.",
        ),
    ),
    (
        "reference/coord-topic-architect-page-proof.md",
        "roles/architect.md",
        "## Establish the work",
        ("Never bypass checks or freeze animations; gate repairs never free tasks under test.",),
    ),
    (
        "reference/coord-topic-architect-timing-result.md",
        "roles/architect.md",
        "## Delegate and decide",
        ("never gate landing on timing;",),
    ),
    (
        "reference/coord-topic-manager-count-and-route-failures.md",
        "roles/manager.md",
        "## Start leaf agents and preserve their work",
        (
            "Group failures (`FAILED`/`SUBFAILED`/`ERROR`) by node; "
            "collection/import failure is a landed-leaf regression (route repair at once).",
        ),
    ),
    (
        "reference/coord-topic-shared-compare-builds-before-diagnosing-a-tool-mismatch.md",
        "core/invariants.md",
        "## Keep continuity in durable state",
        (
            "Before reporting mismatched behavior as a defect, compare the serving tool build with the "
            "code.",
        ),
    ),
    (
        "reference/coord-topic-shared-count-evidence-from-the-producing-call.md",
        "core/invariants.md",
        "## Keep continuity in durable state",
        (
            "For each recorded time, read the clock in the same step. Append a correction instead of "
            "rewriting a wrong time.",
            "Read the producing call before counting.",
            "claim absence only from an unfiltered list.",
        ),
    ),
    (
        "reference/coord-topic-shared-identify-the-setup-behind-a-graphics-measurement.md",
        "core/invariants.md",
        "## Keep continuity in durable state",
        (
            "Name the graphics setup behind a browser measurement. Do not claim it represents the "
            "developer's browser without evidence for that claim.",
        ),
    ),
    (
        "reference/coord-topic-shared-remove-scratch-paths-safely.md",
        "core/every-role.md",
        "## Keep your role and authority",
        ("Never force-remove via shell variable.",),
    ),
    (
        "reference/coord-topic-shared-stop-a-process-or-operate-on-the-live-host.md",
        "core/every-role.md",
        "## Keep your role and authority",
        (
            "Signal processes by checked PID only; dashboard rebuilds, host restarts, "
            "and seat reloads need developer or Architect assignment.",
        ),
    ),
    (
        "reference/coord-topic-worker-map-evidence-to-report-files.md",
        "reference/worker/sync-and-repairs.md",
        "## Focused test selection and freeze report details",
        (
            "Map numbered evidence items to report files at intake, freeze and final checks, "
            "and name any gaps.",
        ),
    ),
    (
        "reference/coord-topic-worker-regenerate-producer-lists-for-each-freeze.md",
        "reference/worker/sync-and-repairs.md",
        "## Focused test selection and freeze report details",
        ("Prove whole-test-tree collection before freezing any product-code removal or rename.",),
    ),
    (
        "reference/curator-baseline-preservation.md",
        "operations/curation.md",
        "## Workflow",
        ("read scoped `knowledge_read` + full `knowledge_diff` before semantic changes.",),
    ),
    (
        "reference/curator-intake-details.md",
        "roles/curator.md",
        "# Curator",
        (
            "Open briefs (`read_ar_files`/`context_packet`: `task_context`; "
            "`knowledge_read`: `workspace.memoryRoot`).",
            "Curate uncommitted freeze (`knowledge_write` `commit: true` writes files, not Git).",
        ),
    ),
    (
        "reference/review-card-dates.md",
        "roles/reviewer.md",
        "## Bind the seam and catalogs",
        ("check card dates vs commits",),
    ),
    (
        "reference/review-knowledge-changes.md",
        "roles/reviewer.md",
        "## Bind the seam and catalogs",
        ("keep Curator producer fields",),
    ),
    (
        "reference/review-master-exit.md",
        "reference/review/non-leaf-seams-and-catalogs.md",
        "## Master, atomic-master, plan, and portfolio exit reviews",
        (
            "Account for every master requirement, leaf, substep and accepted small gap filled during the "
            "work.",
            "Completion against task documents is an affirmative duty, not a formatting check. Skipped or "
            "reshaped work needs a decision-log trail; a packet that reads as complete while silently "
            "omitting unfinished leaf work, a substep or an accepted gap-fill carries a blocking finding.",
        ),
    ),
    (
        "reference/review-memory-changes.md",
        "reference/review/rounds-and-late-findings.md",
        "## Omission repairs, whole-set findings, and memory changes",
        (
            "During memory review, inspect the quality result's reports of cards with substantial text "
            "removed (`cutCards`) and files added outside the knowledge (`addedFiles`), reporting a "
            "finding when text was removed without a named change or when a file was added outside "
            "the knowledge without a place in the task's requirement.",
        ),
    ),
    (
        "reference/review-omission-repairs.md",
        "reference/review/rounds-and-late-findings.md",
        "## Omission repairs, whole-set findings, and memory changes",
        (
            "Close an omission finding by listing and checking every behavior introduced by the diff.",
        ),
    ),
    (
        "reference/review-requirement-adjudication.md",
        "roles/reviewer.md",
        "## Bind the seam and catalogs",
        ("check `expectedKnowledgeEffects`",),
    ),
    (
        "reference/review-whole-set-findings.md",
        "reference/review/rounds-and-late-findings.md",
        "## Omission repairs, whole-set findings, and memory changes",
        (
            "For a finding about every member of a set, script a scan of the whole candidate and "
            "rerun it during verification.",
        ),
    ),
    (
        "reference/coord-topic-architect-harness-setup-documentation.md",
        "reference/coordination/architect/process-decisions.md",
        "## Weigh compute cost, review shortfalls, and document harness setup",
        (
            "Record any required harness setting or workaround at once in the master's harness list "
            "(symptom, cause, setting, and who makes it) for public documentation.",
        ),
    ),
    (
        "reference/curator-external-evidence.md",
        "reference/curator/exceptions-and-recovery.md",
        "## Quality-check card cuts and external citations",
        (
            "Cite an external document supporting a card as an `external` sidecar reference, never as a "
            "repository path with a Git blob. Name the document in the handover evidence for the record's "
            "`origin`, so each backed record identifies its supporting document.",
        ),
    ),
)


# Approved short rules delivered in the real shared digest, not behind step reads.
SHARED_SHORT_RULE_CLAUSES = (
    (
        "AU11.6",
        "core/authority.md",
        "Architect, Reviewer: Ceilings are `WIRE_TOKEN_CEILINGS` and "
        "`COMPILED_CAPSULE_TOKEN_CEILINGS` in "
        "`mcp/src/agents_remember/application/role_capsules/wire_budget.py`; "
        "only developer sets/raises them.",
        ("architect", "reviewer"),
        (
            "A role's Turn-1 wire ceiling is its entry in `WIRE_TOKEN_CEILINGS`, and a compiled "
            "capsule's ceiling is its entry in `COMPILED_CAPSULE_TOKEN_CEILINGS`, both in "
            "`mcp/src/agents_remember/application/role_capsules/wire_budget.py`; read the complete "
            "entry before you state it. Only the developer sets or raises a ceiling.",
        ),
    ),
    (
        "AC0.3",
        "core/acceptance.md",
        "Architect, Orchestrator, Manager: Take closeout trees by filename from the highest-numbered "
        "memory PASS and landing-check verdict, never from a message.",
        ("architect", "orchestrator", "manager"),
        (
            "Take closeout trees by filename from the highest-numbered memory PASS and landing-check "
            "verdict, never from a message.",
        ),
    ),
    (
        "AU11.4",
        "core/authority.md",
        "Pushing, PRs, and releases need developer delegation; never force a refused push; "
        "`--no-verify` needs developer's word.",
        ("architect", "orchestrator", "manager"),
        (
            "Never force a refused push. Using `--no-verify` requires the developer's word for that "
            "occasion. Send the failure through your parent to the developer.",
        ),
    ),
    (
        "AU10.4",
        "core/authority.md",
        "`held` = queued; never resend, withdraw via `withdraw_held_message_id`, "
        "open supplied artifacts first.",
        (
            "architect",
            "orchestrator",
            "manager",
            "worker",
            "curator",
            "reviewer",
            "investigator",
            "bootstrap",
        ),
        (
            "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
            "Count a handover as taken only when the recipient's reply, report or intake names the exact "
            "object and shows the requested work or its start. Open every supplied recipient artifact "
            "before you call one absent. Withdraw a superseded held message with "
            "`withdraw_held_message_id`. A receiver does not poll.",
        ),
    ),
    (
        "AC0.5",
        "core/acceptance.md",
        "Completion != acceptance: write artifact, name object/file, message recipient "
        "(own chat if parentless); no model completion row.",
        (
            "architect",
            "orchestrator",
            "manager",
            "worker",
            "curator",
            "reviewer",
            "investigator",
            "bootstrap",
        ),
        (
            "Name the finished object and its evidence file. A finished turn or a filed report alone does "
            "not notify it.",
        ),
    ),
    (
        "AU10.3",
        "core/authority.md",
        "End waits with `WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:` "
        "(process, id/log, expected end), sending no parent progress `role_message`.",
        (
            "architect",
            "orchestrator",
            "manager",
            "worker",
            "curator",
            "reviewer",
            "investigator",
            "bootstrap",
        ),
        (
            "If your harness offers helpers (sub-agents), they run on your own seat's model unless the "
            "developer names another.",
        ),
    ),
)

REMOVED_SHORT_RULE_ACTIONS = (
    (
        "architect",
        "reference/coordination/shared/requirement-design.md",
        "Before specifying a clickable control, read",
    ),
    (
        "architect",
        "reference/coordination/shared/requirement-design.md",
        "Before changing a dashboard dependency, read",
    ),
    (
        "architect",
        "reference/coordination/architect/process-decisions.md",
        "Before recording a harness setup requirement, read",
    ),
    (
        "architect",
        "reference/coordination/shared/landings-and-master-state.md",
        "Before selecting closeout trees, read",
    ),
    (
        "orchestrator",
        "reference/coordination/shared/landings-and-master-state.md",
        "Before selecting closeout trees, read",
    ),
    ("orchestrator", "reference/refused-write.md", "Before responding to a refused push, read"),
    (
        "manager",
        "reference/coordination/shared/landings-and-master-state.md",
        "Before selecting closeout trees, read",
    ),
    ("manager", "reference/refused-write.md", "Before responding to a refused push, read"),
    (
        "worker",
        "reference/sandbox-actors.md",
        "Before configuring or inspecting a sandbox harness, read",
    ),
    (
        "worker",
        "reference/sandbox-actors.md",
        "Before choosing the sandbox harness home or starting its actors, read",
    ),
    (
        "worker",
        "reference/message-handover.md",
        "Before notifying a seat waiting on a completed step, read",
    ),
    (
        "worker",
        "reference/message-handover.md",
        "Before responding to a held or full-store receipt, read",
    ),
    (
        "curator",
        "reference/curator/card-formats-and-citations.md",
        "Before citing external evidence supporting a card, read",
    ),
    (
        "curator",
        "reference/message-handover.md",
        "Before notifying a seat waiting on your completed step, read",
    ),
    (
        "curator",
        "reference/message-handover.md",
        "Before responding to a held message receipt, read",
    ),
    ("reviewer", "reference/review-baseline.md", "Before partitioning review routes, read"),
    ("reviewer", "reference/review-baseline.md", "Before checking baseline evidence, read"),
    ("reviewer", "reference/review-baseline.md", "Before sealing baseline findings, read"),
    ("reviewer", "reference/review-baseline.md", "Before selecting focused tests, read"),
    (
        "reviewer",
        "reference/message-handover.md",
        "Before notifying a seat waiting on a completed step, read",
    ),
    ("reviewer", "reference/message-handover.md", "Before handling a held receipt, read"),
    (
        "investigator",
        "reference/message-handover.md",
        "Before notifying a seat waiting on a completed step, read",
    ),
    ("investigator", "reference/message-handover.md", "Before handling a held receipt, read"),
)


# One whole-file read names all grouped actions and retains their complete owner clauses.
GROUPED_DETAIL_CLAUSES = (
    (
        "architect",
        "reference/coordination/shared/requirement-design.md",
        "| Reqs/`task_doc`/`context_packet`/`knowledge_read` |",
        "",
        (
            "Give a clickable control one named location and state where it does not appear; avoid "
            "“wherever X is shown”.",
            "The dashboard is the developer's tool: before deferring, gating, moving or removing anything "
            "it depends on, check that he can open and use it with no agent active, or bring the conflict "
            "to him.",
        ),
    ),
    (
        "reviewer",
        "reference/review-baseline.md",
        "| Baseline/knowledge |",
        "",
        (
            "Organise the route work yourself, using sub-agents if your harness offers them. No "
            "particular harness arrangement is required.",
            "Run the bound catalogs, required routes and exploratory mandate. Cover all three lenses "
            "explicitly, even to say a lens is clean: completion against task documents, scoped "
            "implementation evidence, and onboarding against code. Try to refute each claim before "
            "confirming it.",
            "Match evidence to the requirement's class:",
            "- Rendering or visibility needs proof from the mounted user interface.\n"
            "- Scheduling or ordering needs operation-level proof.\n"
            "- A persisted shape needs the parsed artifact.\n"
            "- Doctrine needs the file and the mechanism enforcing the rule.",
            "Evidence of the wrong class is a finding and cannot support PASS.",
            "A weakness record is never a finding, and a document's staleness is never a finding. A "
            "weakness found outside the task's scope and a document standing on code the task changed are "
            "recorded knowledge. They refuse no review, gate or closeout.",
            "An unrepaired weakness inside the task's scope is a finding. So is a document the task "
            "changed whose dependent record is not confirmed. A weakness record never substitutes for "
            "such a finding.",
            "An empty first-pass issue list is a valid terminating baseline.",
            "Select focused tests by",
            "which also names the fix-verification case.",
        ),
    ),
    (
        "worker",
        "reference/sandbox-actors.md",
        "| Sandbox/ask |",
        "",
        (
            "Do not write user-level harness configuration. Bind each sandbox actor per run, or stop and "
            "ask.",
            "Leave a user-level harness configuration file that an agent created unchanged and tell your "
            "parent, who routes one notice to the developer.",
            "Test actors need no extra permission under Agents Remember's rules; a harness's own approval "
            "review may still ask the developer. Use the cheapest model of the harness the run is set up "
            "for, and name it in the command and the record. Ordinary sandbox isolation applies.",
            "The product never edits a harness file the user owns. Ship a setting Agents Remember needs "
            "through its own launch settings, or document it on that harness's page.",
            "Keep credentials out of a sandbox-local harness home. Run model turns through the sandbox "
            "product's launch home.",
            "Before sandbox actors start, prove their tool-server binding with a stub. Count the host's "
            "agents before and after the first `role_start` on `agents-remember-task`.",
        ),
    ),
)
