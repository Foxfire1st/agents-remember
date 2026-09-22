// CAPTURED SERVER BODY -- do not hand-edit.
//
// The bytes below are the response body of the real route (`POST`-free GET to
// /api/review/intent?pageOf=records&continuation=<stale>&pageSize=3) over a real
// two-snapshot fixture whose candidate advanced after the cursor was issued, recorded by
// `temp/icr/probe-l10-pagination.py` on the candidate this leaf leaves.
//   raw body sha256: 3f000384e999ddfe8c5114080c3893f5bba6cafe8625c9c5e3223796ab488370 (13277 bytes)
//   the single normalisation: the per-run fixture repository uuid is written as
//   `<repository_id>`; no other byte differs.
//
// It is captured rather than assembled because the route serializes with
// `exclude_none=True`: a refused page OMITS the `page` key instead of sending `page: null`,
// and a hand-built body that sent the null hid exactly that difference from the mounted case
// (verification round 2, F12). The case that imports this asserts the key is absent and that
// the refusal still renders.
//
// Fidelity check (run from the code worktree, on the bytes this file was captured from):
//   PYTHONPATH=$PWD/mcp/src:$PWD/mcp/tests:$PWD/mcp/test_support \
//     <venv>/bin/python temp/icr/probe-l10-pagination.py  |  grep '^L10|raw-body'
//
// It is typed `unknown` on purpose. The route sends three fields this client's review mirror
// does not declare -- `evidence.channels` (ICR-R14), `knowledge.revision_selection`
// (ICR-R07) and `source.attribution` (ICR-R04) -- so annotating it as `ReviewResult` would
// need either a cast (which the dashboard's fixture guard exists to refuse) or a lossy
// projection of the very bytes the case is about. The mounted case feeds these bytes through
// the client's own decode exactly as the browser does, so nothing is cast and nothing is
// dropped; the missing mirror fields are recorded as a finding for those leaves' client work.

export const RECORDS_PAGE_REFUSAL_RESPONSE: unknown =
  {
  "operation": "read_knowledge_review",
  "payload": {
    "candidate": {
      "leaf_id": "260915-ks-l22",
      "master": "260915_knowledge-substrate",
      "repository_id": "<repository_id>",
      "task_ref": "260915_knowledge-substrate"
    },
    "comparison": {
      "after_code_tree_id": "d2161203387c952d9131144e4595cb9b6d8b57b9",
      "after_snapshot_digest": "15e9f5e96a6f0edd3c1cc25be76919f9d3064733cb321c77034e02e5bf16101a",
      "before_code_tree_id": "1cba611e998f892923c4b876ef2fe29983e03591",
      "before_snapshot_digest": "73945b6fa33a6130bb2320dce42c17394b92b9dc33eb101a0e24f9562e0d0a3d",
      "binding_digest": "56a4338c51f1bb51b4be841f5fa89b8138f5192ff17233e74dcc0dec2252aab4",
      "knowledge_compared": true,
      "policy_version": "recorded-two-snapshot-union/v1",
      "reference": "56a4338c51f1bb51b4be841f5fa89b8138f5192ff17233e74dcc0dec2252aab4",
      "selector_digest": "933c2c4ff908ad4f63ba2e4846650c8a5e71c8442d0edcb54c878932d829ce85"
    },
    "evidence": {
      "assessment_state": "unassessed",
      "assessments": [],
      "channels": [
        {
          "detail": "the review matrix this collection comes from could not be read: review_matrix: the continuation was minted at one snapshot and presented against another; the view does not re-resolve it and returns no page",
          "next_action": "open a new comparison: a cursor is a position in one comparison of two named snapshots, so the moved one cannot be continued; the surface serves the first page of the comparison that is there now and this response names the comparison the cursor was minted at",
          "owner": "knowledge_views.read_knowledge_view:review_matrix",
          "records": "authored_effects",
          "state": "unavailable",
          "unreadable": []
        }
      ],
      "evidence_links": [],
      "evidence_state": "none_recorded",
      "observations": [],
      "source_inspection_available": true,
      "unresolved": []
    },
    "knowledge": {
      "after_conditions": [],
      "after_statement": {
        "detail": "ambiguous revision selection for the invariant 473ba88c-c793-4ba7-981f-79d20a681029: the snapshots retain multiple legitimate heads (before heads [30000000-0000-4000-8000-b5ad77a0e279, 60000000-0000-4000-8000-bc5fecd4090c] and after heads [1a4c8866-244e-4e94-881e-17d2801f45d4, 60000000-0000-4000-8000-bc5fecd4090c]) with no authored successor ordering between them, so no revision was chosen; select one retained revision explicitly to compare it",
        "language": "text",
        "state": "unresolved"
      },
      "assessments": [],
      "authored_effects": [],
      "before_conditions": [],
      "before_statement": {
        "detail": "ambiguous revision selection for the invariant 473ba88c-c793-4ba7-981f-79d20a681029: the snapshots retain multiple legitimate heads (before heads [30000000-0000-4000-8000-b5ad77a0e279, 60000000-0000-4000-8000-bc5fecd4090c] and after heads [1a4c8866-244e-4e94-881e-17d2801f45d4, 60000000-0000-4000-8000-bc5fecd4090c]) with no authored successor ordering between them, so no revision was chosen; select one retained revision explicitly to compare it",
        "language": "text",
        "state": "unresolved"
      },
      "family_ids": [],
      "field_changes": [],
      "invariant_ids": [
        "473ba88c-c793-4ba7-981f-79d20a681029"
      ],
      "revision_groups": [
        {
          "record_id": "473ba88c-c793-4ba7-981f-79d20a681029",
          "selected_revision_count": 3,
          "side": "before"
        },
        {
          "record_id": "bef0ac6f-ec8d-4389-8df7-0c63a58237d8",
          "selected_revision_count": 1,
          "side": "before"
        },
        {
          "record_id": "d9effeb0-6df9-4a31-a744-56cb18c70e5d",
          "selected_revision_count": 1,
          "side": "before"
        },
        {
          "record_id": "1b894360-8c87-43f8-91e9-2143e5ec4284",
          "selected_revision_count": 1,
          "side": "before"
        },
        {
          "record_id": "bb4b2ac8-b467-4cf1-9be2-6cf92db4ad60",
          "selected_revision_count": 1,
          "side": "before"
        },
        {
          "record_id": "473ba88c-c793-4ba7-981f-79d20a681029",
          "selected_revision_count": 5,
          "side": "after"
        },
        {
          "record_id": "bef0ac6f-ec8d-4389-8df7-0c63a58237d8",
          "selected_revision_count": 1,
          "side": "after"
        },
        {
          "record_id": "d9effeb0-6df9-4a31-a744-56cb18c70e5d",
          "selected_revision_count": 1,
          "side": "after"
        },
        {
          "record_id": "1b894360-8c87-43f8-91e9-2143e5ec4284",
          "selected_revision_count": 1,
          "side": "after"
        },
        {
          "record_id": "bb4b2ac8-b467-4cf1-9be2-6cf92db4ad60",
          "selected_revision_count": 1,
          "side": "after"
        }
      ],
      "revision_selection": {
        "after_heads": [
          "1a4c8866-244e-4e94-881e-17d2801f45d4",
          "60000000-0000-4000-8000-bc5fecd4090c"
        ],
        "after_retained": [
          "1a4c8866-244e-4e94-881e-17d2801f45d4",
          "30000000-0000-4000-8000-b5ad77a0e279",
          "60000000-0000-4000-8000-bc5fecd4090c"
        ],
        "before_heads": [
          "30000000-0000-4000-8000-b5ad77a0e279",
          "60000000-0000-4000-8000-bc5fecd4090c"
        ],
        "before_retained": [
          "30000000-0000-4000-8000-b5ad77a0e279",
          "60000000-0000-4000-8000-bc5fecd4090c"
        ],
        "record_id": "473ba88c-c793-4ba7-981f-79d20a681029",
        "record_kind": "invariant",
        "state": "ambiguous",
        "statement": "ambiguous revision selection for the invariant 473ba88c-c793-4ba7-981f-79d20a681029: the snapshots retain multiple legitimate heads (before heads [30000000-0000-4000-8000-b5ad77a0e279, 60000000-0000-4000-8000-bc5fecd4090c] and after heads [1a4c8866-244e-4e94-881e-17d2801f45d4, 60000000-0000-4000-8000-bc5fecd4090c]) with no authored successor ordering between them, so no revision was chosen; select one retained revision explicitly to compare it"
      },
      "selection_state": "subject_selected",
      "signals": [],
      "unresolved": []
    },
    "limitations": [
      "limitation:records_present_outside_the_selection",
      "limitation:unattributed_changed_paths",
      "limitation:no_semantic_assessment_performed",
      "omitted:present_outside_the_declared_selection:2",
      "omitted:change_not_attributed_to_a_recorded_realization:1"
    ],
    "page_refusal": {
      "code": "comparison_page_reset",
      "detail": "review_matrix: the continuation was minted at one snapshot and presented against another; the view does not re-resolve it and returns no page",
      "expected": "408ac310f3b0c6242b64b5054263e9ba5782bc51d5ba3622185f067a060927f9",
      "next_action": "open a new comparison: a cursor is a position in one comparison of two named snapshots, so the moved one cannot be continued; the surface serves the first page of the comparison that is there now and this response names the comparison the cursor was minted at",
      "observed": "15e9f5e96a6f0edd3c1cc25be76919f9d3064733cb321c77034e02e5bf16101a",
      "offending_input": "review_matrix:408ac310f3b0c6242b64b5054263e9ba5782bc51d5ba3622185f067a060927f9:3"
    },
    "source": {
      "attributed_changed_paths": [
        "src/batch.py",
        "src/integration.py",
        "src/retry_interval.py",
        "src/synchronization.py"
      ],
      "attribution": {
        "attributed_total": 4,
        "changed_total": 5,
        "complete": true,
        "confirmed_unregistered_total": 1,
        "detail": "5 measured changed path(s) at the changed-path granularity: 4 attributed, 1 confirmed to have no valid registered attribution, and 0 of undetermined attribution. Snapshot inspection: before inspected; after inspected. Every required snapshot/scope completely inspected or legitimately known empty: True. This denominator is the whole measured change population of the bound pair. A resolved path-level mapping establishes that a recorded claim's bytes are at this path and never that every change inside it realizes that claim",
        "granularity": "changed_path",
        "paths": [
          {
            "bucket": "attributed",
            "detail": "2 registered realization claim(s) hold the recorded bytes at this path, and 2 purported mapping(s) name it without resolving to them (a stale recording, an unresolvable locator or a path that is not there); the path is counted once at the changed-path granularity, whatever the number of links",
            "link": "outside_selection_complete",
            "mapped_sides": [
              "before"
            ],
            "path": "src/batch.py",
            "unresolved_mapping_count": 2
          },
          {
            "bucket": "attributed",
            "detail": "1 registered realization claim(s) hold the recorded bytes at this path, and 0 purported mapping(s) name it without resolving to them (a stale recording, an unresolvable locator or a path that is not there); the path is counted once at the changed-path granularity, whatever the number of links",
            "link": "selected_subject",
            "mapped_sides": [
              "before"
            ],
            "path": "src/integration.py",
            "unresolved_mapping_count": 0
          },
          {
            "bucket": "attributed",
            "detail": "1 registered realization claim(s) hold the recorded bytes at this path, and 0 purported mapping(s) name it without resolving to them (a stale recording, an unresolvable locator or a path that is not there); the path is counted once at the changed-path granularity, whatever the number of links",
            "link": "selected_subject",
            "mapped_sides": [
              "after"
            ],
            "path": "src/retry_interval.py",
            "unresolved_mapping_count": 0
          },
          {
            "bucket": "attributed",
            "detail": "1 registered realization claim(s) hold the recorded bytes at this path, and 0 purported mapping(s) name it without resolving to them (a stale recording, an unresolvable locator or a path that is not there); the path is counted once at the changed-path granularity, whatever the number of links",
            "link": "selected_subject",
            "mapped_sides": [
              "before"
            ],
            "path": "src/synchronization.py",
            "unresolved_mapping_count": 0
          },
          {
            "bucket": "confirmed_unregistered",
            "detail": "no registered realization claim holds its recorded bytes at this path, and every required snapshot/scope was completely inspected or is a legitimately known-empty side, so the absence of a valid registered attribution is established rather than assumed",
            "mapped_sides": [],
            "path": "src/unmapped.py",
            "unresolved_mapping_count": 0
          }
        ],
        "sides": [
          {
            "detail": "every registered claim at the measured changed paths was read from this snapshot (4 mapping(s)), and each one's anchor was observed against this side's own bound code tree",
            "registered_mapping_count": 4,
            "side": "before",
            "state": "inspected"
          },
          {
            "detail": "every registered claim at the measured changed paths was read from this snapshot (3 mapping(s)), and each one's anchor was observed against this side's own bound code tree",
            "registered_mapping_count": 3,
            "side": "after",
            "state": "inspected"
          }
        ],
        "state": "measured",
        "subject_scope_complete": true,
        "unknown_attribution_total": 0
      },
      "expansion_command": "git diff --raw -z --no-renames 1cba611e998f892923c4b876ef2fe29983e03591 d2161203387c952d9131144e4595cb9b6d8b57b9",
      "expansion_reference": "diff_knowledge_scope:full-selected-candidate-source-diff",
      "inventory": {
        "after_code_tree_id": "d2161203387c952d9131144e4595cb9b6d8b57b9",
        "before_code_tree_id": "1cba611e998f892923c4b876ef2fe29983e03591",
        "command": "git -C /tmp/tmp16wo0r32/matrix/candidate-code diff --raw -z --no-renames 1cba611e998f892923c4b876ef2fe29983e03591 d2161203387c952d9131144e4595cb9b6d8b57b9",
        "detail": "the two requested code trees differ at 5 path(s); every one is listed with the change status Git reported for it, whether or not its content can be rendered",
        "entries": [
          {
            "content": "text",
            "mode_change": false,
            "path": "src/batch.py",
            "status": "modified"
          },
          {
            "content": "text",
            "mode_change": false,
            "path": "src/integration.py",
            "status": "modified"
          },
          {
            "content": "text",
            "mode_change": false,
            "path": "src/retry_interval.py",
            "status": "added"
          },
          {
            "content": "text",
            "mode_change": false,
            "path": "src/synchronization.py",
            "status": "deleted"
          },
          {
            "content": "text",
            "mode_change": false,
            "path": "src/unmapped.py",
            "status": "added"
          }
        ],
        "listed_total": 5,
        "partial": false,
        "state": "measured",
        "unrepresentable_paths": []
      },
      "locations": [],
      "relationships": [
        {
          "after": {
            "detail": "the after snapshot records invariant 473ba88c-c793-4ba7-981f-79d20a681029 with no governing route; an ungoverned identity is not placed in the repository root and no route is inferred from the paths its claims name",
            "item_coverage": "selected_both",
            "reached_via": [],
            "record_id": "473ba88c-c793-4ba7-981f-79d20a681029",
            "record_kind": "invariant",
            "side": "after",
            "state": "ungoverned"
          },
          "before": [
            {
              "detail": "the before snapshot records invariant 473ba88c-c793-4ba7-981f-79d20a681029 with no governing route; an ungoverned identity is not placed in the repository root and no route is inferred from the paths its claims name",
              "item_coverage": "selected_both",
              "reached_via": [],
              "record_id": "473ba88c-c793-4ba7-981f-79d20a681029",
              "record_kind": "invariant",
              "side": "before",
              "state": "ungoverned"
            }
          ],
          "gaps": [],
          "lineage": [],
          "pairing_basis": "same_governed_identity",
          "record_id": "473ba88c-c793-4ba7-981f-79d20a681029",
          "record_kind": "invariant",
          "relationship_kind": "governing_route",
          "statement": "the invariant 473ba88c-c793-4ba7-981f-79d20a681029 is associated with the same governing route on both snapshots (no governing route)",
          "transition": "unchanged"
        }
      ],
      "remaining": [
        {
          "name": "locations_remaining",
          "value": 20
        },
        {
          "name": "changed_paths_outside_selection",
          "value": 2
        },
        {
          "name": "unattributed_changed_paths",
          "value": 1
        },
        {
          "name": "unknown_attribution_changed_paths",
          "value": 0
        },
        {
          "name": "records_present_outside_selection",
          "value": 1
        },
        {
          "name": "references_unresolved",
          "value": 0
        }
      ],
      "unattributed_changed_paths": [
        "src/unmapped.py"
      ],
      "unknown_attribution_changed_paths": [],
      "unresolved": [
        {
          "detail": "this record is held by one snapshot and was not reached by the other side's declared selection; it is displayed as present outside the selection and never as a deletion",
          "field": "attribution",
          "recorded_reference": "1a4c8866-244e-4e94-881e-17d2801f45d4"
        }
      ]
    },
    "staleness": {
      "moved": [],
      "state": "current",
      "statement": "the displayed comparison is the candidate's current comparison"
    },
    "submission": {
      "next_action": "publish an assessment through the existing curator authority's publication action, which supplies the author, the role and the authority provenance",
      "none_is_approval": true,
      "proposed_dispositions": [
        "concern_found",
        "no_concern_found",
        "unresolved"
      ],
      "reason": "this increment mounts no serving route that publishes an assessment, so the surface displays only and does not grow a private write path to compensate",
      "state": "unavailable"
    },
    "surface_version": "knowledge-review-surface/1"
  },
  "repository_id": "<repository_id>",
  "state": "review"
};
