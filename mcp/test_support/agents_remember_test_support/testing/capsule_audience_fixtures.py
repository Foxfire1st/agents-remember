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
            "Before authoring or amending a requirement and its task structure, read",
        ),
        (
            "reference/tasks/document-authoring.md",
            "Before authoring a task document or requirement packet, read",
        ),
        ("reference/knowledge/commands.md", "Before planning repository work, read"),
        (
            "reference/knowledge/use.md",
            (
                "Before diagnosing, call `knowledge_read` on "
                "`agents-remember-task` for the files involved; read"
            ),
        ),
        (
            "reference/coordination/architect/models-and-settings.md",
            "Before selecting a seat model and its authorized settings, read",
        ),
        (
            "reference/coordination/architect/process-decisions.md",
            "Before deciding or escalating a coordination issue, read",
        ),
        (
            "reference/coordination/architect/curator-measurement.md",
            "Before running or discussing a Curator measurement, read",
        ),
        (
            "reference/coordination/shared/seat-recovery.md",
            "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "Before updating the master after a start, handover or landing, read",
        ),
        (
            "reference/coordination/shared/third-rejected-round.md",
            "Before judging a third rejected round during direct coordination, read",
        ),
        (
            "reference/coordination/architect/developer-decisions.md",
            "Before relaying a developer decision from question through instruction delivery, read",
        ),
        (
            "reference/coordination/architect/claim-verification.md",
            "Before ruling on a reported claim, read",
        ),
        ("reference/message-handover.md", "Before handing work to a waiting seat, read"),
        (
            "reference/sandbox-actors.md",
            "Before configuring or inspecting a sandbox harness without changing user-owned "
            "configuration, read",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "Before answering a child's question or returning the developer's answer, read",
        ),
    ],
    "orchestrator": [
        (
            "reference/coordination/shared/seat-recovery.md",
            "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "Before updating the master after a start, handover or landing, read",
        ),
        ("reference/tasks/document-authoring.md", "Before using `task_doc append_decision`, read"),
        (
            "reference/knowledge/commands.md",
            "Before using a knowledge command or acting on its result, read",
        ),
        ("reference/message-handover.md", "Before handing work to a waiting seat, read"),
        (
            "reference/sandbox-actors.md",
            "Before configuring or inspecting a sandbox harness without changing "
            "user-owned configuration, read",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "Before handling a child's question or the developer's answer to it, read",
        ),
    ],
    "manager": [
        (
            "reference/knowledge/commands.md",
            "Before using a knowledge command or interpreting its result, read",
        ),
        (
            "reference/coordination/shared/seat-recovery.md",
            "Before recovering idle, unfinished, stopped or unavailable-provider work, read",
        ),
        ("reference/refused-write.md", "Before retiring or moving a task, read"),
        (
            "reference/coordination/shared/third-rejected-round.md",
            "Before judging repair progress at a third rejected round, an earlier coder switch "
            "or a Reviewer replacement, read",
        ),
        (
            "reference/coordination/shared/master-state.md",
            "Before updating master state through completion, read",
        ),
        (
            "reference/tasks/document-authoring.md",
            "Before editing, skipping or previewing task-document fields, or using `task_doc "
            "append_decision`, read",
        ),
        (
            "reference/coordination/shared/child-questions.md",
            "Before handling a child's question or returning the developer's answer, read",
        ),
        ("reference/message-handover.md", "Before handing work to a waiting seat, read"),
        (
            "reference/sandbox-actors.md",
            "Before configuring or inspecting a sandbox harness without changing user-owned "
            "configuration, read",
        ),
    ],
    "worker": [
        (
            "reference/tasks/document-authoring.md",
            "Before editing, skipping or previewing task-document fields, read",
        ),
        (
            "reference/knowledge/use.md",
            (
                "Before editing source, call `knowledge_read` on "
                '`agents-remember-task` with `view="source_context"` for each '
                "file you will edit; read"
            ),
        ),
        (
            "reference/knowledge/commands.md",
            "Before using a knowledge command or interpreting its result, read",
        ),
        (
            "reference/sandbox-actors.md",
            "Before configuring or inspecting a sandbox harness, choosing its launch home or "
            "starting its actors, read",
        ),
        ("templates/turn-report.md", "Before writing the freeze report, read the"),
        (
            "reference/writing/visual-explainers.md",
            "Before you add an optional walkthrough to your freeze report, read",
        ),
        (
            "reference/knowledge/serving-build.md",
            "Before a command-line run that decides or writes, read",
        ),
        ("reference/message-handover.md", "Before sending a handover, read"),
        (
            "reference/knowledge/use.md",
            (
                "Before diagnosing a failing test, call `knowledge_read` on "
                '`agents-remember-task` with `view="source_context"` for the '
                "test file; read"
            ),
        ),
    ],
    "curator": [
        (
            "reference/knowledge/use.md",
            (
                "Before checking code and memory, call `knowledge_read` on "
                "`agents-remember-task` for the files or records you check; "
                "read"
            ),
        ),
        ("reference/knowledge/use.md", "Before interpreting a knowledge read, read"),
        (
            "reference/curator/repository-foundation.md",
            "Before a repository's first or resumed knowledge foundation, read",
        ),
        (
            "reference/curator/intent-reconciliation.md",
            "Before reconciling current intent, ruled intent and implementation, read",
        ),
        ("reference/curator/worklist.md", "Before answering the persisted leaf worklist, read"),
        ("reference/curator/governing-sources.md", "Before explaining an assigned contract, read"),
        (
            "reference/curator/card-sentence-decisions.md",
            "Before editing a card or its paired notes, read",
        ),
        (
            "reference/curator/knowledge-ingest.md",
            "Before preparing or ingesting authored knowledge, read",
        ),
        (
            "reference/curator/family-obligations.md",
            "Before deciding family coverage or updating a family, read",
        ),
        (
            "reference/curator/change-routing.md",
            "Before routing a changed file or notes item, read",
        ),
        (
            "reference/curator/current-prose-and-history.md",
            "Before updating current overview prose or answering changed-file history, read",
        ),
        (
            "reference/curator/governing-sources.md",
            "Before authoring a governing decision record, read",
        ),
        ("reference/curator/coherence-judgments.md", "Before preparing a coherence judgment, read"),
        (
            "reference/writing/technical-english-profile.md",
            "Before drafting invariant statements, family guarantees or history reasons, read the",
        ),
        (
            "reference/knowledge/serving-build.md",
            "Before a command-line run that decides or writes, read",
        ),
        (
            "reference/curator/card-formats-and-citations.md",
            "Before creating or maintaining cards or overviews, read",
        ),
        (
            "reference/curator/card-formats-and-citations.md",
            "Before maintaining the entity catalog, read [entity catalog maintenance",
        ),
        ("reference/curator/card-formats-and-citations.md", "Before writing card history, read"),
        (
            "reference/curator/card-formats-and-citations.md",
            "Before checking or repairing citations, read",
        ),
        (
            "reference/knowledge/commands.md",
            "Before using a knowledge-system command or interpreting its result, read",
        ),
        (
            "reference/curator/publication-report.md",
            "Before interpreting an ingest result or claiming publication, read",
        ),
        (
            "reference/curator/memory-handover.md",
            "Before sending a memory candidate for review or returning a memory repair, read",
        ),
        (
            "reference/curator/encountered-records.md",
            "Before authoring an encountered weakness or document dependency, read",
        ),
        ("reference/message-handover.md", "Before sending a message, read"),
        ("reference/sandbox-actors.md", "Before configuring or inspecting a sandbox harness, read"),
    ],
    "reviewer": [
        (
            "reference/knowledge/use.md",
            (
                "When inspecting changes, call `knowledge_read` on "
                "`agents-remember-task` for each changed file; read"
            ),
        ),
        ("reference/knowledge/commands.md", "Before a knowledge-system command or check, read"),
        ("reference/review-baseline.md", "Before preparing a baseline review, read"),
        ("reference/message-handover.md", "Before handing over work, read"),
        ("reference/sandbox-actors.md", "Before setting up or inspecting a sandbox harness, read"),
    ],
    "investigator": [
        (
            "reference/knowledge/commands.md",
            "Before using a knowledge command or interpreting its result, read",
        ),
        (
            "reference/knowledge/use.md",
            (
                "Before investigating repository code, call `knowledge_read` "
                "on `agents-remember-task` for the files involved; read"
            ),
        ),
        (
            "reference/instruction-shortfall-review.md",
            "Before reviewing an instruction shortfall named by the concern, read",
        ),
        ("reference/sandbox-actors.md", "Before setting up or inspecting a sandbox harness, read"),
        ("reference/message-handover.md", "Before handing over work, read"),
    ],
    "bootstrap": [
        (
            "reference/bootstrap/foundation-state.md",
            "Before reading and reporting the knowledge foundation's state, read",
        ),
        ("reference/knowledge/commands.md", "Before using a knowledge command or its result, read"),
        ("reference/sandbox-actors.md", "Before setting up or inspecting a sandbox harness, read"),
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
    },
    "bootstrap": {
        # P5-18: roles/bootstrap.md:24 is the one state-reading pointer.
        "bootstrap": [
            (
                "reference/bootstrap/failure-states.md",
                "When a step returns a state you cannot name, read",
            ),
        ]
    },
}

ORDINARY_DETAIL_LINK_COUNTS = {
    # P5-18 (round 6): planning.md, coordination.md, curation.md and closeout.md no longer repeat
    # the role files' handover pointer; curation.md no longer links the reviewed-trees section
    # (curator.md:93 names it); manager.md:56 reaches master state's two sections in one sentence.
    "architect/authorized-closeout": {"reference/message-handover.md": 1},
    "architect/coordination": {"reference/message-handover.md": 2},
    "architect/orientation": {"reference/message-handover.md": 2},
    "architect/planning": {
        "reference/coordination/shared/requirement-design.md": 1,
        "reference/message-handover.md": 1,
    },
    "architect/recovery": {"reference/message-handover.md": 1},
    "architect/review": {"reference/message-handover.md": 2},
    "bootstrap/bootstrap": {
        "reference/knowledge/commands.md": 0,
        "reference/bootstrap/foundation-state.md": 1,
    },
    # P5-18 (round 5): curation.md no longer repeats the role file's card-scope, entity
    # catalog, change-routing, family, overview-prose, memory-handover, foundation and
    # read-code pointers; recovery.md:48, implementation.md:24/:47 and review.md:62 no longer
    # repeat the role files' handover pointer.
    "curator/curation": {
        "reference/curator/intent-reconciliation.md": 1,
        "reference/message-handover.md": 1,
    },
    "curator/orientation": {"reference/message-handover.md": 2},
    "curator/recovery": {"reference/message-handover.md": 1},
    "investigator/orientation": {"reference/message-handover.md": 2},
    "investigator/recovery": {"reference/message-handover.md": 1},
    "manager/authorized-closeout": {
        "reference/coordination/shared/master-state.md": 1,
        "reference/message-handover.md": 1,
    },
    "manager/coordination": {
        "reference/coordination/shared/master-state.md": 1,
        "reference/message-handover.md": 2,
    },
    "manager/curation": {
        "reference/coordination/shared/master-state.md": 1,
        "reference/message-handover.md": 1,
    },
    "manager/orientation": {
        "operations/orientation.md": 1,
        "reference/coordination/shared/master-state.md": 1,
        "reference/message-handover.md": 2,
    },
    "manager/recovery": {
        "reference/coordination/shared/master-state.md": 1,
        "reference/message-handover.md": 1,
    },
    "manager/review": {
        "reference/coordination/shared/master-state.md": 1,
        "reference/message-handover.md": 2,
    },
    "orchestrator/authorized-closeout": {"reference/message-handover.md": 1},
    "orchestrator/coordination": {"reference/message-handover.md": 2},
    "orchestrator/orientation": {"reference/message-handover.md": 2},
    "orchestrator/planning": {"reference/message-handover.md": 1},
    "orchestrator/recovery": {"reference/message-handover.md": 1},
    "orchestrator/review": {"reference/message-handover.md": 2},
    "reviewer/orientation": {"reference/message-handover.md": 2},
    "reviewer/recovery": {"reference/message-handover.md": 1},
    "reviewer/review": {"reference/message-handover.md": 2},
    "worker/implementation": {"reference/message-handover.md": 1},
    "worker/orientation": {"reference/message-handover.md": 2},
    "worker/recovery": {"reference/message-handover.md": 1},
}

D656_OPERATION_SHA256 = {
    "orientation": "107f06ea46e2733be46cf43b46a47297a89e9112e6c07035278c27ccd9722d66",
    "planning": "3c23ea1f2b8b0415182630e6eccb28a7e5c3e6244c3b5964b19395bc35cb4d82",
    "implementation": "bf4fc5b611fabe7612934c58db459b0f291a9e3913b94adf1118509910eb2bd8",
    "review": "88fba52076f3f7d9e7b4148fc218b64ff9f241bc60a367d695dec096ca500eac",
    "curation": "21f27c8c6dc62274cb6f72363fee217b1594a21c63302b128fd37320f37d919d",
    "coordination": "0087aa0dbd1e322454046dae01d4a8d230205831f22abeebabc7da644424b9d4",
    "authorized-closeout": "3e6725c0730cc1456dd5ba1bbcfe7edc1a53203e05f65dd58dac0ed842eb0035",
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
    "coordination/architect": "c1af86abc34b8eba28ec4cfd114f71ed19fe780f00ea18fa0c4ed140a8cc0985",
    "coordination/orchestrator": "fd4056642278d53100077fd4aa30109dbbb1a02dec6388c8a369db64766ee21f",
    "coordination/manager": "529c0871c328a56a349d892a89cc3db56c95b5002cd12137f4715ff83737b547",
    "authorized-closeout/manager": "3e6725c0730cc1456dd5ba1bbcfe7edc1a53203e05f65dd58dac0ed842eb0035",
    "authorized-closeout/orchestrator": "3e6725c0730cc1456dd5ba1bbcfe7edc1a53203e05f65dd58dac0ed842eb0035",
    "authorized-closeout/architect": "3e6725c0730cc1456dd5ba1bbcfe7edc1a53203e05f65dd58dac0ed842eb0035",
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
    ),
    "bootstrap": ("bootstrap",),
}

# P5-18: the full-store pointer lives in each operation, not in the Curator role.
CURATOR_MESSAGE_TIMING = ("Before sending a message, read",)

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
    "Decide and write through the starting server or its serving-build command-line interface (CLI). "
    "Development uses clean main and its stated serving commit as the reference build; name it. Leaf source "
    "never produces leaf memory: test leaf writer/check changes on fixtures or scratch. Install nothing; "
    "disable no guard."
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


# These role action clauses distinguish metadata reads from ordinary operation links.
ROLE_POINTER_QUALIFICATIONS = {
    (
        "architect",
        "reference/coordination/shared/master-state.md",
        "Before updating the master after a start, handover or landing, read",
    ): ("#update-the-master-after-starts-handovers-and-landings", ""),
    (
        "orchestrator",
        "reference/coordination/shared/master-state.md",
        "Before updating the master after a start, handover or landing, read",
    ): ("#update-the-master-after-starts-handovers-and-landings", ""),
    ("manager", "reference/refused-write.md", "Before retiring or moving a task, read"): (
        "#apply-the-procedure-to-a-task-document-or-folder",
        "",
    ),
    (
        "manager",
        "reference/coordination/shared/master-state.md",
        "Before updating master state through completion, read",
    ): ("#update-the-master-after-starts-handovers-and-landings", ""),
    (
        "curator",
        "reference/curator/governing-sources.md",
        "Before authoring a governing decision record, read",
    ): ("#preserve-governing-decisions", ""),
    (
        "curator",
        "reference/curator/card-formats-and-citations.md",
        "Before creating or maintaining cards or overviews, read",
    ): ("#choose-card-and-overview-scope", ""),
    (
        "curator",
        "reference/curator/card-formats-and-citations.md",
        "Before maintaining the entity catalog, read [entity catalog maintenance",
    ): ("#maintain-the-entity-catalog", ""),
    (
        "curator",
        "reference/curator/card-formats-and-citations.md",
        "Before writing card history, read",
    ): ("#choose-the-history-disposition", ""),
    (
        "curator",
        "reference/curator/card-formats-and-citations.md",
        "Before checking or repairing citations, read",
    ): ("#prepare-citation-input", ""),
    ("reviewer", "reference/message-handover.md", "Before handing over work, read"): (
        "#send-a-message-that-names-the-files",
        "",
    ),
    ("investigator", "reference/message-handover.md", "Before handing over work, read"): (
        "#send-a-message-that-names-the-files",
        "",
    ),
}


# Fixed v13 owning sections; original clauses remain checked after inlining.
INLINE_RULE_CLAUSES = (
    (
        "reference/coord-topic-architect-bound-brief.md",
        "roles/architect.md",
        "### Prepare and preserve each brief",
        (
            "A code or feature brief is bounded to what was asked and names where an outside discovery "
            "goes; a sighting next to the task does not widen it. Curator memory healing retains its "
            "separate rule.",
        ),
    ),
    (
        "reference/coord-topic-architect-bound-side-problem.md",
        "roles/architect.md",
        "### Prepare and preserve each brief",
        (
            "Give a hard, open-ended side problem a separate bounded step instead of folding it into "
            "another round.",
        ),
    ),
    (
        "reference/coord-topic-architect-compute-cost.md",
        "roles/architect.md",
        "### Checks and investigations",
        (
            "Weigh what each requirement, ruling or brief costs the reading agent's context, the machine "
            "and paid compute, and choose the cheap shape: a pointer to a file, a selective read, one "
            "call.",
            "Gross waste of context, machine or compute in the product or in a planned task goes to the "
            "developer once, with a measurement and the fix you propose.",
        ),
    ),
    (
        "reference/coord-topic-architect-developer-check-failure.md",
        "roles/architect.md",
        "### Checks and investigations",
        (
            "Passing by one of the developer's checks (a commit without his hook, a switched-off check) "
            "is never a formality: the failure is repaired, and the bypass stays his alone.",
            "For a check that blocks a legal operation, follow [Requirements and limits](../core/every-role.md#requirements-and-limits) "
            "for the bounded repair. Keep commit and landing guards separate from file guards.",
        ),
    ),
    (
        "reference/coord-topic-architect-instruction-review.md",
        "roles/architect.md",
        "## Role agents and messages",
        (
            "Offer the developer an instruction-shortfall review about every two weeks and at each "
            "master's end; count a fix as made only when its words reach the compiled first messages.",
        ),
    ),
    (
        "reference/coord-topic-architect-master-scope.md",
        "roles/architect.md",
        "## Delegate and decide",
        (
            "Finish the master's approved subject: do not park admitted work for a follow-up master or "
            "trim its promise to converge. New or changed scope still returns to the developer.",
        ),
    ),
    (
        "reference/coord-topic-architect-master-status-read.md",
        "roles/architect.md",
        "## Delegate and decide",
        (
            "At each status read, compare the master's page as the developer sees it with the Manager's "
            "last handover. Compare the status note, **In work now**, and task statuses. Renew a lagging "
            "part and tell the Manager.",
            "Identify stalls from task progress. These include fifteen minutes without an active turn, or "
            "a ready task left unstarted.",
        ),
    ),
    (
        "reference/coord-topic-architect-page-proof.md",
        "roles/architect.md",
        "### Checks and investigations",
        (
            "Do not disable, pause or freeze page scripts or animations to make a proof pass; capture the "
            "page while they run.",
        ),
    ),
    (
        "reference/coord-topic-architect-timing-result.md",
        "roles/architect.md",
        "## Delegate and decide",
        (
            "Do not make timing a separate landing condition or reopen an optimisation subject the "
            "developer has closed.",
            "When a timing or speed target of a task is missed, grant no further review round and no "
            "stronger coder for that miss: land the gains and record the miss. A defect found in the work "
            "is repaired as usual.",
        ),
    ),
    (
        "reference/coord-topic-manager-count-and-route-failures.md",
        "roles/manager.md",
        "## Start leaf agents and preserve their work",
        (
            "Count a lane's failures from its `FAILED`, `SUBFAILED` and `ERROR` lines, grouped by test "
            "node. Assign every failing node an owner.",
            "A reported test file that fails at collection or does not import is a regression of a landed "
            "leaf. Route its repair at once; do not leave it as a recorded mark.",
        ),
    ),
    (
        "reference/coord-topic-shared-compare-builds-before-diagnosing-a-tool-mismatch.md",
        "core/every-role.md",
        "## Evidence and handovers",
        (
            "Before reporting mismatched behavior as a defect, compare the serving tool build with the "
            "code.",
        ),
    ),
    (
        "reference/coord-topic-shared-count-evidence-from-the-producing-call.md",
        "core/every-role.md",
        "## Evidence and handovers",
        (
            "For each recorded time, read the clock in the same step. Append a correction instead of "
            "rewriting a wrong time.",
            "Read the producing call before counting.",
            "claim absence only from an unfiltered list.",
        ),
    ),
    (
        "reference/coord-topic-shared-identify-the-setup-behind-a-graphics-measurement.md",
        "core/every-role.md",
        "## Evidence and handovers",
        (
            "Name the graphics setup behind a browser measurement. Do not claim it represents the "
            "developer's browser without evidence for that claim.",
        ),
    ),
    (
        "reference/coord-topic-shared-remove-scratch-paths-safely.md",
        "core/every-role.md",
        "## Keep your role and authority",
        (
            "Avoid forced recursive removal through a shell variable. Use a fresh folder or an approved "
            "literal path.",
        ),
    ),
    (
        "reference/coord-topic-shared-stop-a-process-or-operate-on-the-live-host.md",
        "core/every-role.md",
        "## Keep your role and authority",
        (
            "Stop or signal a process only by a checked process identifier (PID) or the sandbox's own PID "
            "file. Rebuild the dashboard after a frontend change (`scripts/sync-dashboard.py`) only when "
            "the developer or the Architect assigns it.",
            "A host restart or seat reload is done only when the developer or the Architect assigns it, "
            "by the procedure the assignment names; if it names none, report that to whoever assigned it "
            "instead of choosing a command.",
            "On the developer's live host, only the process, dashboard, restart and seat-reload actions described above are admitted.",
        ),
    ),
    (
        "reference/coord-topic-worker-map-evidence-to-report-files.md",
        "roles/worker.md",
        "## Map and record evidence",
        (
            "At intake and at each freeze, map every numbered evidence item to its report file. Name "
            "missing evidence openly.",
        ),
    ),
    (
        "reference/coord-topic-worker-regenerate-producer-lists-for-each-freeze.md",
        "roles/worker.md",
        "## Report and freeze the candidate",
        (
            "At each freeze, regenerate the scripted Curator list and its binding for the frozen tree, "
            "ready for code review to return `PASS`.",
            "Before freezing a product-code removal or rename, prove that the whole test tree collects on "
            "the final bytes.",
        ),
    ),
    (
        "reference/curator-baseline-preservation.md",
        "roles/curator.md",
        "## Reconcile, author and check",
        (
            "Name the immutable baseline and candidate records through admitted, scoped `knowledge_read` "
            "on `agents-remember-task`. Open the named files with your own tools and read the full "
            "`knowledge_diff` comparison report before semantic changes. Preserve the baseline and read "
            "admitted records back under that authority.",
        ),
    ),
    (
        "reference/curator-intake-details.md",
        "roles/curator.md",
        "## Establish the assignment",
        (
            "Use the handover's `arMcpContext.readerArguments` and the actual tool schemas. "
            "`knowledge_read` on `agents-remember-task` takes `workspace.memoryRoot`. Both `context_packet` and `read_ar_files` "
            "take the exact supplied leaf `task_context`, with the canonical task reference and enclosure "
            "contract.",
            "The code freeze is a tree, usually uncommitted over its base. Curate and cite that tree. No "
            "Git commit precedes curation; the commit comes with closeout. `knowledge_write` on `agents-remember-task` with "
            "`commit: true` writes memory files, not a Git commit.",
        ),
    ),
    (
        "reference/review-card-dates.md",
        "roles/reviewer.md",
        "## Inspect and verify the candidate",
        ("Check card-date claims against the adding commit.",),
    ),
    (
        "reference/review-knowledge-changes.md",
        "roles/reviewer.md",
        "## Inspect and verify the candidate",
        (
            "For knowledge changes, inspect the before-and-after candidate, family interactions and "
            "unchanged sibling realizations. Preserve supplied Curator hand-off producer fields unchanged "
            "for co-resolution.",
        ),
    ),
    (
        "reference/review-master-exit.md",
        "roles/reviewer.md",
        "## Master and portfolio exit reviews",
        (
            "Account for every master requirement, leaf, substep and accepted small gap filled during the "
            "work.",
            "Completion against task documents is an affirmative duty, not a formatting check. Skipped or "
            "reshaped work needs a decision-log trail. A packet that reads as complete while silently "
            "omitting unfinished leaf work, a substep or an accepted gap-fill has a blocking finding.",
        ),
    ),
    (
        "reference/review-memory-changes.md",
        "roles/reviewer.md",
        "## Inspect and verify the candidate",
        (
            "When you review the Curator's memory change, inspect the quality result's reports of cards "
            "with substantial text removed (`cutCards`) and files added outside the knowledge "
            "(`addedFiles`).",
            "Report a finding when text was removed without a named change. Also report a finding when a "
            "file was added outside the knowledge without a place in the task's requirement.",
        ),
    ),
    (
        "reference/review-omission-repairs.md",
        "roles/reviewer.md",
        "## Inspect and verify the candidate",
        (
            "Close an omission finding by listing and checking every behavior introduced by the diff.",
        ),
    ),
    (
        "reference/review-requirement-adjudication.md",
        "roles/reviewer.md",
        "## Inspect and verify the candidate",
        (
            "When the leaf's task document declares `expectedKnowledgeEffects`, compare it with the "
            "leaf's requirement packet. The declared subjects and effects must match what the packet "
            "requires, with none missing and none invented. A mismatch is a finding.",
        ),
    ),
    (
        "reference/review-whole-set-findings.md",
        "roles/reviewer.md",
        "## Inspect and verify the candidate",
        (
            "For a finding about every member of a set, script a scan of the whole candidate and rerun it "
            "during verification.",
        ),
    ),
    (
        "reference/coord-topic-architect-harness-setup-documentation.md",
        "roles/architect.md",
        "## Establish the work",
        (
            "When a harness needs a setting or workaround before AR agents work on it, record it at once "
            "in the master's harness list (symptom, cause, setting, who makes it) for the public docs.",
        ),
    ),
    (
        "reference/curator-external-evidence.md",
        "roles/curator.md",
        "## Produce the handover",
        (
            "Cite an external document supporting a card as an `external` sidecar reference, never as a "
            "repository path with a Git blob. Name the document in the handover evidence for the record's "
            "`origin`, so each backed record identifies its supporting document.",
        ),
    ),
)


# Approved short rules delivered in the real shared digest, not behind step reads.
SHARED_SHORT_RULE_CLAUSES = (
    # P5-03 Reviewer 67:0: the ceiling rule moved from roles/architect.md into the digest,
    # named for the two roles that need it (v14 rule 4).
    (
        "AU11.6",
        "core/authority.md",
        "Architect, Reviewer: A role's capsule ceiling is its entry in `current_capsule_ceilings_words` "
        "of the [current budget record](skill://agents-remember/skills/l-01-agent-lifecycles/"
        "capsule-budgets-current.json), with the developer decision that set it; read the complete "
        "entry before you state it. Only the developer sets or raises a ceiling.",
        ("architect", "reviewer"),
        (
            "A role's capsule ceiling is its entry in `current_capsule_ceilings_words` of the [current "
            "budget record](../capsule-budgets-current.json), with the developer decision that set it; "
            "read the complete entry before you state it. Only the developer sets or raises a ceiling.",
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
        "Architect, Orchestrator, Manager: Never force a refused push. Using `--no-verify` requires "
        "the developer's word for that occasion. Send the failure through your parent to the "
        "developer.",
        ("architect", "orchestrator", "manager"),
        (
            "Never force a refused push. Using `--no-verify` requires the developer's word for that "
            "occasion. Send the failure through your parent to the developer.",
        ),
    ),
    (
        "AU10.4",
        "core/authority.md",
        "Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator: A held receipt "
        "means queued eventual delivery, possibly many turns later. Never resend it. Count a handover "
        "as taken only when the recipient's reply, report or intake names the exact object and shows the "
        "requested work or its start. Open every supplied recipient artifact before you call one absent. "
        "Withdraw a superseded held message with "
        "`withdraw_held_message_id`. A receiver does not poll.",
        ("architect", "orchestrator", "manager", "worker", "curator", "reviewer", "investigator"),
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
        "Completion is not acceptance. Write the artifact before handing it over and message its "
        "waiting recipient. Add no model-written completion row and no other role agent's runtime "
        "identity. A role with no parent reports in its own chat. Name the finished object and its "
        "evidence file. A finished turn or a filed report alone does not notify it.",
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
        "Collect your helpers' results and continue the work. Use the helper-wait procedure declared "
        "by your launch; otherwise preserve the harness's native collection of results within the turn. "
        "If your harness offers helpers (sub-agents), they run on your own seat's model unless the "
        "developer names another.",
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
        "Before authoring or amending a requirement and its task structure, read",
        " for caller and phase checks, approval, task structure, and control and dashboard design.",
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
        "Before preparing a baseline review, read",
        " for route partitioning, baseline evidence checks, sealing and focused-test selection. Review "
        "the Worker's targeted checks and regressions against the past for the requested scope. Full "
        "suites and drift reporting on `agents-remember-task` run only on an explicit developer "
        "request.",
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
        "Before configuring or inspecting a sandbox harness, choosing its launch home or starting its "
        "actors, read",
        " for per-run settings, user-owned configuration, the admitted actor model, launch-home "
        "selection, credentials, tool-server binding and host-agent counts.",
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
