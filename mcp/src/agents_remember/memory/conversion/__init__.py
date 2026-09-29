"""The conversion of a memory tree into the text knowledge format, and the boundary crossing (MIK-R24).

* :mod:`.convert` -- the deterministic conversion (rules 1-4 and 6), driven by
  ``agents-remember knowledge-convert``; :mod:`.cards` (Markdown), :mod:`.citations` (references),
  :mod:`.legacy_db` (the read-only legacy database reader and export), :mod:`.code_objects` (the
  code objects it resolves in) and :mod:`.inputs` (reading and writing memory trees).
* :mod:`.base` -- the converted base of a comparison (rule 7).
* :mod:`.crossing` and :mod:`.crossing_sync` -- the crossing sync's structural merge (rule 8), bound
  into the managed sync through :mod:`.crossing_port`.
"""
