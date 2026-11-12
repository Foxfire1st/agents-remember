"""Nine raw-operation inverse spans for the current v13 source epoch."""

V13_OPERATION_SHORT_RULE_INVERSE = {
    "operation/orientation": [
        (
            "Continue every part of your assignment that does not depend on "
            "the answer; do not end your turn on the question or hold a wait "
            "for the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state "
            "in your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the "
            "last line may repeat it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend on "
            "the answer; do not end your turn on the question or hold a wait "
            "for the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state "
            "in your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the "
            "last line may repeat it.\n"
            "\n"
            "Before notifying the recipient that your independent work is "
            "complete, read [Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a held question receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n"
            "\n",
        )
    ],
    "operation/planning": [
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "operation/implementation": [
        (
            "\n1. Freeze the candidate and tell the Manager to start the Reviewer.\n",
            "\n"
            ""
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "1. Freeze the candidate and tell the Manager to start the "
            "Reviewer.\n",
        ),
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle "
            "held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "operation/review": [
        (
            "For a leaf:\n\n",
            ""
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "For a leaf:\n"
            "\n",
        ),
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n"
            "\n",
        ),
    ],
    "operation/curation": [
        (
            "For an Agents Remember leaf, send the memory candidate directly to "
            "the Reviewer through `role_message` on `agents-remember-task`, "
            "using the handover's role and task arguments. Use only whole, "
            "unchanged agent identifiers from sender lines, start results or "
            "handovers.\n"
            "\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "For an Agents Remember leaf, send the memory candidate directly to "
            "the Reviewer through `role_message` on `agents-remember-task`, "
            "using the handover's role and task arguments. Use only whole, "
            "unchanged agent identifiers from sender lines, start results or "
            "handovers.\n"
            "\n",
        ),
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "operation/coordination": [
        (
            "The leaf resolver reaches a seat's single live agent. Two live "
            "occupants cause a refusal naming both; the tool never selects "
            "one. Report an empty-seat refusal once, with its text, through "
            "the role's recovery route; choose no candidate. A Manager uses "
            "its parent route, never itself. A role address never means you "
            "yourself.\n"
            "\n"
            "A message to a recipient already in a turn may be held until "
            "that turn ends. If the host delivers it into the running turn, "
            "that turn may be cancelled. If the host has not delivered it, "
            "the answer names `held` and its reason.\n"
            "\n",
            "The leaf resolver reaches a seat's single live agent. Two live "
            "occupants cause a refusal naming both; the tool never selects "
            "one. Report an empty-seat refusal once, with its text, through "
            "the role's recovery route; choose no candidate. A Manager uses "
            "its parent route, never itself. A role address never means you "
            "yourself.\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "A message to a recipient already in a turn may be held until "
            "that turn ends. If the host delivers it into the running turn, "
            "that turn may be cancelled. If the host has not delivered it, "
            "the answer names `held` and its reason.\n"
            "\n",
        ),
        (
            "\n"
            "<!-- ar:roles architect,orchestrator,manager -->\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "<!-- ar:roles all -->\n",
            "\n"
            "<!-- ar:roles architect,orchestrator,manager -->\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n"
            "<!-- ar:roles all -->\n",
        ),
    ],
    "operation/authorized-closeout": [
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle "
            "a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before handing over the verified closeout result, read "
            "[Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, "
            "read [Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read "
            "[Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle "
            "a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "operation/recovery": [
        (
            ""
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "operation/bootstrap": [],
}
