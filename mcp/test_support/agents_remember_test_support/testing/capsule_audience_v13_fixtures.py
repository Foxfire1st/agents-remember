"""Strict current-v13 pins, separate admitted short-rule inverse and frozen path inverse."""

from agents_remember_test_support.testing.capsule_audience_v13_operation_inverse import (
    V13_OPERATION_SHORT_RULE_INVERSE,
)

V13_PRE_SHORT_OPERATION_SHA256 = {
    "orientation": "107f06ea46e2733be46cf43b46a47297a89e9112e6c07035278c27ccd9722d66",
    "planning": "b858d61bb9ee90411605a33f2f512453e68b8a4cb793036517f9fdfb196ca46e",
    "implementation": "bf4fc5b611fabe7612934c58db459b0f291a9e3913b94adf1118509910eb2bd8",
    "review": "88fba52076f3f7d9e7b4148fc218b64ff9f241bc60a367d695dec096ca500eac",
    "curation": "21f27c8c6dc62274cb6f72363fee217b1594a21c63302b128fd37320f37d919d",
    "coordination": "0087aa0dbd1e322454046dae01d4a8d230205831f22abeebabc7da644424b9d4",
    "authorized-closeout": "3e6725c0730cc1456dd5ba1bbcfe7edc1a53203e05f65dd58dac0ed842eb0035",
    "recovery": "e5707af48d38d94eafcc9458cf6a7dc87c094c9563eb3b899efc527171abe69f",
    "bootstrap": "1a9a92eef605905e95d0727f1efc72821ab490886da1d750190546ffc515b0da",
}

V13_PRE_SHORT_STAGE_SHA256 = {
    "orientation/architect": "3540125acebc32669bd8b7757e96470e646cd4f610d69585692a9e0a1250e747",
    "orientation/orchestrator": "ca95de826e9ea96dd1b183d81acfdc08419069dcf0336fa3286bd7327b6b3a85",
    "orientation/manager": "ca95de826e9ea96dd1b183d81acfdc08419069dcf0336fa3286bd7327b6b3a85",
    "orientation/worker": "e229cad7a791853bbe94f5b6d033257c6421335a6b4350bc92e59a83cd5b87e1",
    "orientation/curator": "a0a07f1c6a8a42cb155c707f5011502d6d3a54454c96827beb42ef48c3b59a02",
    "orientation/reviewer": "ca95de826e9ea96dd1b183d81acfdc08419069dcf0336fa3286bd7327b6b3a85",
    "orientation/investigator": "b7cae92c304f45eb8c344d6020cb21025a150b96ffe663554964e3bb2e5dc70f",
    "orientation/bootstrap": "85495ca8b48fa4b642527224613f29321209acacd8249de265f789422aadee85",
    "planning/architect": "a5dfaf91ee5c0438552de1c8c96c7e047346f472f58f229f57581a3588c406b1",
    "planning/orchestrator": "3a46e4b9048d8d560be5cb966da35dcf6cb035a2074a15b6a91a7ef7c0a943ec",
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
    "bootstrap/bootstrap": "9472403bc5f34c79b904a4baf63569789990684a42e0d66b9e208135ca9d1c83",
}

V13_OPERATION_SHA256 = {
    "orientation": "3120f8aea8dbc2fafb10fd9722e54e4e58c03f87dbe630a343e44bdb87f5cd30",
    "planning": "3ff5705f68745036538ab70641c1bb1de319e773239f7ffaaa5c3546820aa957",
    "implementation": "94c3d3c4628a450586a9522c436ee1c1f1e4478ee9b1e6af0feb8cb73582caa8",
    "review": "19b9d4df6cc34036f6eec72c7d6c31bcec5a0e88015077761b1019ced54f21be",
    "curation": "de311c01a980b8e41ce991849b2a94fce35faf2d5609de281d6812d4918fa76b",
    "coordination": "b6ce2f36eff1ebf1c013d6b26ab0675d74c6ab88c90a547612ac875242064418",
    "authorized-closeout": "c2f5de0efb06134f6edc9ba655863667b983906e7024a9231cdf99fba3c464e5",
    "recovery": "920faa49cc2de8111c07d8844827f9592a57e63d977d9c7830c17f80dfe29f24",
    "bootstrap": "1a9a92eef605905e95d0727f1efc72821ab490886da1d750190546ffc515b0da",
}

V13_STAGE_SHA256 = {
    "orientation/architect": "d52d7f894c30c1a0a188c92aaa1ba40b8273d96c5747d661dbe504c3ba3df108",
    "orientation/orchestrator": "882dd8d60faebb788285396e31854b8be006eb852d9fa0c9c5b08f2fd9a48708",
    "orientation/manager": "882dd8d60faebb788285396e31854b8be006eb852d9fa0c9c5b08f2fd9a48708",
    "orientation/worker": "7fdc90ea78d128f8a90282edcfb2f48824d8adf94e8f94a407afba13a96a835e",
    "orientation/curator": "ecda592b1da264c21c975adb5801b9a7800837b56dc4e7bad6839a42f251e44e",
    "orientation/reviewer": "882dd8d60faebb788285396e31854b8be006eb852d9fa0c9c5b08f2fd9a48708",
    "orientation/investigator": "8859b38155055803de969edafb6271a4a317e99ccccce8662c74754bc08c773c",
    "orientation/bootstrap": "85495ca8b48fa4b642527224613f29321209acacd8249de265f789422aadee85",
    "planning/architect": "305b9f0e82e74925233e5081055bb8b0b811c2fb82dfea80d0a10a2899c40aca",
    "planning/orchestrator": "941fada5456e8e3c2aca84506c8373a5c5cf5d478082c17a6686a621ec0708ba",
    "implementation/worker": "e1499007ec82ca03998ee94761e5e520222cfe2b657299a499900c7d7b1fb953",
    "review/reviewer": "d11455df6d395d9d4ac300ff111275bf03373dea8e8c6274a319c3ce1eacaae8",
    "review/manager": "885f58610dbd0539c9af530bb3882949e24a426b262559282328c6779ee4a033",
    "review/orchestrator": "885f58610dbd0539c9af530bb3882949e24a426b262559282328c6779ee4a033",
    "review/architect": "ee73475cf9a1c5b8985776dce685e35e745f18588f3db6869c80fe3ff4f34046",
    "curation/curator": "95a3eeb8804abd86145063e0ada839f14eabc641ab95c2b7a52299791b6bf605",
    "curation/manager": "4311e44f0879b06b19bcef0161d91ac1627a7328256378b6a1b3e8a0a86fc7f0",
    "coordination/architect": "8063c3e79c03780321eee73ff0657303d7fc6f1ba47008748becbe30067ab697",
    "coordination/orchestrator": "fc3e4dd0613f4f36c0c33c2fbab30827498f8a965a63d7524856e9da72cc921b",
    "coordination/manager": "5e1f0b7171c0d3b379657b8e1b8d070f3a7f9bc228245fa82d3de1e4b23a839f",
    "authorized-closeout/manager": "c2f5de0efb06134f6edc9ba655863667b983906e7024a9231cdf99fba3c464e5",
    "authorized-closeout/orchestrator": "c2f5de0efb06134f6edc9ba655863667b983906e7024a9231cdf99fba3c464e5",
    "authorized-closeout/architect": "c2f5de0efb06134f6edc9ba655863667b983906e7024a9231cdf99fba3c464e5",
    "recovery/architect": "c4b441e7eb516fb14d798e83feb5cf7ab54e2eb32e45e9d6c234718d0e5e3c88",
    "recovery/orchestrator": "c4b441e7eb516fb14d798e83feb5cf7ab54e2eb32e45e9d6c234718d0e5e3c88",
    "recovery/manager": "c4b441e7eb516fb14d798e83feb5cf7ab54e2eb32e45e9d6c234718d0e5e3c88",
    "recovery/worker": "48adf4af29a2dc921d985de149e258d86b7ae1d3b9c09d5cf92a189e5f7cbb48",
    "recovery/curator": "419829fd62dd6e1f46f69d9ac27a6f14d966b75ced64e73f4b3dd80f2326e315",
    "recovery/investigator": "48adf4af29a2dc921d985de149e258d86b7ae1d3b9c09d5cf92a189e5f7cbb48",
    "recovery/bootstrap": "c770ba6d2024ae702664e3340a30f1aaa7f153ae8b3f9e6fdcced923fcaea285",
    "recovery/reviewer": "48adf4af29a2dc921d985de149e258d86b7ae1d3b9c09d5cf92a189e5f7cbb48",
    "bootstrap/bootstrap": "9472403bc5f34c79b904a4baf63569789990684a42e0d66b9e208135ca9d1c83",
}

V13_PATH_INVERSE = {
    "reference/bootstrap/failure-states.md": "reference/bootstrap-failure-states.md",
    "reference/bootstrap/foundation-state.md": "reference/bootstrap-foundation-state.md",
    "reference/coordination/shared/requirement-design.md": "reference/coord-topic-architect-requirement-design.md",
    "reference/curator/card-formats-and-citations.md": "reference/curator-card-details.md",
    "reference/curator/change-routing.md": "reference/curator-change-routing.md",
    "reference/curator/current-prose-and-history.md": "reference/curator-current-prose-and-history.md",
    "reference/curator/family-obligations.md": "reference/curator-family-obligations.md",
    "reference/curator/memory-handover.md": "reference/curator-memory-handover.md",
    "reference/knowledge/commands.md": "reference/knowledge-commands.md",
    "reference/knowledge/use.md": "reference/knowledge-use.md",
}

V13_SHORT_RULE_INVERSE = {
    "operation/orientation": V13_OPERATION_SHORT_RULE_INVERSE["operation/orientation"],
    "orientation/architect": [
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
    "orientation/orchestrator": [
        (
            "Continue every part of your assignment that does not depend "
            "on the answer; do not end your turn on the question or hold a "
            "wait for the developer. The answer arrives as a message from "
            "that recipient. When no independent work remains, record your "
            "state in your report and send what you await, and from whom, "
            "to that recipient with `role_message` on "
            "`agents-remember-task`; the last line may repeat it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend "
            "on the answer; do not end your turn on the question or hold a "
            "wait for the developer. The answer arrives as a message from "
            "that recipient. When no independent work remains, record your "
            "state in your report and send what you await, and from whom, "
            "to that recipient with `role_message` on "
            "`agents-remember-task`; the last line may repeat it.\n"
            "\n"
            "Before notifying the recipient that your independent work is "
            "complete, read [Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a held question receipt, read [Handle "
            "held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n"
            "\n",
        )
    ],
    "orientation/manager": [
        (
            "Continue every part of your assignment that does not depend on the "
            "answer; do not end your turn on the question or hold a wait for "
            "the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state in "
            "your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the last "
            "line may repeat it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend on the "
            "answer; do not end your turn on the question or hold a wait for "
            "the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state in "
            "your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the last "
            "line may repeat it.\n"
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
    "orientation/worker": [
        (
            "Continue every part of your assignment that does not depend on the "
            "answer; do not end your turn on the question or hold a wait for the "
            "developer. The answer arrives as a message from that recipient. "
            "When no independent work remains, record your state in your report "
            "and send what you await, and from whom, to that recipient with "
            "`role_message` on `agents-remember-task`; the last line may repeat "
            "it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend on the "
            "answer; do not end your turn on the question or hold a wait for the "
            "developer. The answer arrives as a message from that recipient. "
            "When no independent work remains, record your state in your report "
            "and send what you await, and from whom, to that recipient with "
            "`role_message` on `agents-remember-task`; the last line may repeat "
            "it.\n"
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
    "orientation/curator": [
        (
            "Continue every part of your assignment that does not depend on the "
            "answer; do not end your turn on the question or hold a wait for "
            "the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state in "
            "your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the last "
            "line may repeat it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend on the "
            "answer; do not end your turn on the question or hold a wait for "
            "the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state in "
            "your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the last "
            "line may repeat it.\n"
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
    "orientation/reviewer": [
        (
            "Continue every part of your assignment that does not depend on "
            "the answer; do not end your turn on the question or hold a wait "
            "for the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state in "
            "your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the last "
            "line may repeat it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend on "
            "the answer; do not end your turn on the question or hold a wait "
            "for the developer. The answer arrives as a message from that "
            "recipient. When no independent work remains, record your state in "
            "your report and send what you await, and from whom, to that "
            "recipient with `role_message` on `agents-remember-task`; the last "
            "line may repeat it.\n"
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
    "orientation/investigator": [
        (
            "Continue every part of your assignment that does not depend "
            "on the answer; do not end your turn on the question or hold a "
            "wait for the developer. The answer arrives as a message from "
            "that recipient. When no independent work remains, record your "
            "state in your report and send what you await, and from whom, "
            "to that recipient with `role_message` on "
            "`agents-remember-task`; the last line may repeat it.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "Continue every part of your assignment that does not depend "
            "on the answer; do not end your turn on the question or hold a "
            "wait for the developer. The answer arrives as a message from "
            "that recipient. When no independent work remains, record your "
            "state in your report and send what you await, and from whom, "
            "to that recipient with `role_message` on "
            "`agents-remember-task`; the last line may repeat it.\n"
            "\n"
            "Before notifying the recipient that your independent work is "
            "complete, read [Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a held question receipt, read [Handle "
            "held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n"
            "\n",
        )
    ],
    "orientation/bootstrap": [],
    "operation/planning": V13_OPERATION_SHORT_RULE_INVERSE["operation/planning"],
    "planning/architect": [
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
    "planning/orchestrator": [
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
    "operation/implementation": V13_OPERATION_SHORT_RULE_INVERSE["operation/implementation"],
    "implementation/worker": [
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
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "operation/review": V13_OPERATION_SHORT_RULE_INVERSE["operation/review"],
    "review/reviewer": [
        (
            "\nFor a leaf:\n",
            "\n"
            ""
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "For a leaf:\n",
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
    "review/manager": [
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
        )
    ],
    "review/orchestrator": [
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n"
            "\n",
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n"
            "\n",
        )
    ],
    "review/architect": [
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
        )
    ],
    "operation/curation": V13_OPERATION_SHORT_RULE_INVERSE["operation/curation"],
    "curation/curator": [
        (
            "For an Agents Remember leaf, send the memory candidate directly to "
            "the Reviewer through `role_message` on `agents-remember-task`, using "
            "the handover's role and task arguments. Use only whole, unchanged "
            "agent identifiers from sender lines, start results or handovers.\n"
            "\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "For an Agents Remember leaf, send the memory candidate directly to "
            "the Reviewer through `role_message` on `agents-remember-task`, using "
            "the handover's role and task arguments. Use only whole, unchanged "
            "agent identifiers from sender lines, start results or handovers.\n"
            "\n",
        ),
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "curation/manager": [
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "operation/coordination": V13_OPERATION_SHORT_RULE_INVERSE["operation/coordination"],
    "coordination/architect": [
        (
            "\n"
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
            "the answer names `held` and its reason.\n",
            "\n"
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
            "the answer names `held` and its reason.\n",
        ),
        (
            "Before acting on a denied or unanswered harness request, read "
            "[Honor harness permission "
            "decisions](orientation.md#honor-harness-permission-decisions) "
            "for the waiting, denial and non-bypass rules.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before acting on a denied or unanswered harness request, read "
            "[Honor harness permission "
            "decisions](orientation.md#honor-harness-permission-decisions) "
            "for the waiting, denial and non-bypass rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "coordination/orchestrator": [
        (
            "\n"
            "The leaf resolver reaches a seat's single live agent. Two "
            "live occupants cause a refusal naming both; the tool never "
            "selects one. Report an empty-seat refusal once, with its "
            "text, through the role's recovery route; choose no "
            "candidate. A Manager uses its parent route, never itself. A "
            "role address never means you yourself.\n"
            "\n"
            "A message to a recipient already in a turn may be held until "
            "that turn ends. If the host delivers it into the running "
            "turn, that turn may be cancelled. If the host has not "
            "delivered it, the answer names `held` and its reason.\n",
            "\n"
            "The leaf resolver reaches a seat's single live agent. Two "
            "live occupants cause a refusal naming both; the tool never "
            "selects one. Report an empty-seat refusal once, with its "
            "text, through the role's recovery route; choose no "
            "candidate. A Manager uses its parent route, never itself. A "
            "role address never means you yourself.\n"
            "\n"
            "Before responding to a held message receipt, read [Handle "
            "held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "A message to a recipient already in a turn may be held until "
            "that turn ends. If the host delivers it into the running "
            "turn, that turn may be cancelled. If the host has not "
            "delivered it, the answer names `held` and its reason.\n",
        ),
        (
            "Before acting on a denied or unanswered harness request, "
            "read [Honor harness permission "
            "decisions](orientation.md#honor-harness-permission-decisions) "
            "for the waiting, denial and non-bypass rules.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before acting on a denied or unanswered harness request, "
            "read [Honor harness permission "
            "decisions](orientation.md#honor-harness-permission-decisions) "
            "for the waiting, denial and non-bypass rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "coordination/manager": [
        (
            "The leaf resolver reaches a seat's single live agent. Two live "
            "occupants cause a refusal naming both; the tool never selects "
            "one. Report an empty-seat refusal once, with its text, through "
            "the role's recovery route; choose no candidate. A Manager uses "
            "its parent route, never itself. A role address never means you "
            "yourself.\n"
            "\n"
            "A message to a recipient already in a turn may be held until that "
            "turn ends. If the host delivers it into the running turn, that "
            "turn may be cancelled. If the host has not delivered it, the "
            "answer names `held` and its reason.\n"
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
            "A message to a recipient already in a turn may be held until that "
            "turn ends. If the host delivers it into the running turn, that "
            "turn may be cancelled. If the host has not delivered it, the "
            "answer names `held` and its reason.\n"
            "\n",
        ),
        (
            "Before acting on a denied or unanswered harness request, read "
            "[Honor harness permission "
            "decisions](orientation.md#honor-harness-permission-decisions) for "
            "the waiting, denial and non-bypass rules.\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before acting on a denied or unanswered harness request, read "
            "[Honor harness permission "
            "decisions](orientation.md#honor-harness-permission-decisions) for "
            "the waiting, denial and non-bypass rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read "
            "[Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        ),
    ],
    "operation/authorized-closeout": V13_OPERATION_SHORT_RULE_INVERSE[
        "operation/authorized-closeout"
    ],
    "authorized-closeout/manager": [
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
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
            "Before responding to a held message receipt, read [Handle "
            "held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a "
            "full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "authorized-closeout/orchestrator": [
        (
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a full-store refusal, read "
            "[Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before handing over the verified closeout result, "
            "read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed "
            "step, read [Notify the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read "
            "[Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read "
            "[Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "authorized-closeout/architect": [
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
    "operation/recovery": V13_OPERATION_SHORT_RULE_INVERSE["operation/recovery"],
    "recovery/architect": [
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
    "recovery/orchestrator": [
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
    "recovery/manager": [
        (
            ""
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "recovery/worker": [
        (
            ""
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "recovery/curator": [
        (
            ""
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "recovery/investigator": [
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
    "recovery/bootstrap": [],
    "recovery/reviewer": [
        (
            ""
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal) "
            "for the pending handover, single retry and second-refusal report.\n",
            "Before sending the handover, read [Send a message that names the "
            "files](../reference/message-handover.md#send-a-message-that-names-the-files) "
            "for its payload and file rules.\n"
            "\n"
            "Before notifying a seat waiting on your completed step, read [Notify "
            "the seat waiting on a completed "
            "step](../reference/message-handover.md#notify-the-seat-waiting-on-a-completed-step).\n"
            "\n"
            "### Handle delivery receipts\n"
            "\n"
            "Before responding to a held message receipt, read [Handle held "
            "delivery](../reference/message-handover.md#handle-held-delivery).\n"
            "\n"
            "Before responding to a full-store refusal, read [Handle a full-store "
            "refusal](../reference/message-handover.md#handle-a-full-store-refusal).\n",
        )
    ],
    "operation/bootstrap": V13_OPERATION_SHORT_RULE_INVERSE["operation/bootstrap"],
    "bootstrap/bootstrap": [],
}


def inverse_v13_short_rules(text: str, key: str) -> str:
    """Restore only the exact admitted 543/546 spans, independently of path movement."""
    for current, previous in V13_SHORT_RULE_INVERSE[key]:
        assert text.count(current) == 1, key
        text = text.replace(current, previous, 1)
    return text


def inverse_v13_paths(text: str) -> str:
    """Undo only the explicitly pinned v13 path substitutions."""
    for current in sorted(V13_PATH_INVERSE, key=len, reverse=True):
        text = text.replace(current, V13_PATH_INVERSE[current])
    return text
