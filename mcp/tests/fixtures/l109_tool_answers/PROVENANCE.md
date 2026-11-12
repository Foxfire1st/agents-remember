# L109 table-case fixtures: provenance

Row cases for the four tools L109 rebuilds, measured with the product
counter against the row bounds in `test_tool_answer_table.py`.

- `read_ar_files_ordinary`: recorded wire answer (MIK-R108 shaping) — one
  path, named with its card, in the leaf's own code/memory worktrees.
- `read_ar_files_largest`: OPERATION-PRODUCED by
  `_produce_l109_read_ar_files_largest` — twenty converted governed files
  in one leaf-scoped call (the packet's Worker journey: cards and overviews
  arrive worktree-relative, so the answer holds its bound on any root
  length). File size does not move this answer (no text is carried);
  twenty paths is the tool's limit.
- `knowledge_read_ordinary`: recorded wire answer — one invariant view,
  five rows.
- `knowledge_read_largest`: OPERATION-PRODUCED by
  `_produce_l109_knowledge_read_largest` — the actually delivered largest
  `source_context` page of a deep family walk. Row files are
  memory-root-relative beside the answered absolute `memoryTree.memoryRoot`,
  so pages cut identically on any root length and the pin reproduces.
- `knowledge_diff_ordinary`: recorded wire answer — one changed file in a
  synthetic converted repo.
- `knowledge_diff_largest`: OPERATION-PRODUCED by
  `_produce_l109_knowledge_diff_largest` — an eighty-record
  repository-memory comparison (35 modified, 30 added, 5 deleted).
  Decision-record paths are authoritative index paths joined with the
  answered absolute `memoryRoot`.
- `context_packet_ordinary`: recorded wire answer — the hermetic
  entry-point world, providers off.
- `context_packet_largest`: recorded wire answer — the hermetic
  entry-point world with a leaf enclosure (roots, task, pair).

Volatile values are sanitized by `_sanitize_l109_answer`: the generation
scratch prefix reads `/gen` (or `/world` for the hermetic world), day
folders read `ar-tools-2000-01-01`, detail timestamps read zero, and the
random tool-log detail suffix reads `00000000`. Every other byte is the
produced answer.

Regenerate the three operation-produced files with:

```text
L109_REGEN=1 python -m pytest mcp/tests/test_tool_answer_table.py::L109LargestProductionTests
```

`L109LargestProductionTests` pins each produced file byte-for-byte; the
largest case of every row carries no overflow fallback.

## Production lifecycle metadata

- Leaf: `260928-MIK-L109` (`109_a-read-names-where-a-thing-is`).
- Lifecycle: `01M4KWZNK77H87DQGC1HX1R1QT`.
- Produced and pinned against base `6a8b5b78` (branch
  `ar/109_a-read-names-where-a-thing-is-7909ab1468`) on 2026-10-11;
  `knowledge_read_largest.json` re-derived on the moved base `4b6a2660`
  for the round-4 kind/role repair (only `kind`/`role` lines differ).
- Regeneration is hermetic: the same command on the same base reproduces
  every byte except the sanitized volatile values named above.
