"""The curator writer for text knowledge (MIK-R12): every knowledge kind, written as files.

The curator's hand-off document drives it. It creates and updates invariant, family, decision,
incident and the six other facet records; realization and proof entries in the file sidecars; and the
leaf's history rows. It fills every mechanical field -- IDs, anchors with ``blob`` and ``content`` at
the code candidate C, revisions, origin with the hand-off evidence, canonical formatting -- and runs
the knowledge validator (MIK-R22) over the resulting tree before it writes anything.

* :mod:`.handoff` -- the document's sections and their shape;
* :mod:`.code_anchors` -- the code candidate tree C and anchor resolution;
* :mod:`.memory_state` -- the memory tree, the operation's edits, and the base;
* :mod:`.authoring` -- the edits, with the mechanical fields filled;
* :mod:`.requirement_links` -- the requirement endpoints a run's records link, resolved by their
  owner and reported, never refused (MIK-R13);
* :mod:`.reconsideration` -- the reconsideration rows (MIK-R14): ``still_rejected``, and ``raise``,
  which sets the decision under reconsideration and appends a question for the developer;
* :mod:`.open_questions` -- that question, appended to the leaf's task document through
  ``task_doc``;
* :mod:`.writer` -- :func:`write_knowledge`: validate, then write or refuse;
* :mod:`.report` -- what the operation reports.

``agents-remember knowledge-ingest`` and ``agents-remember knowledge-bootstrap`` reach it when the
memory tree they write is converted (it holds ``knowledge/layout.json``). Unconverted memory keeps the
installed database ingest until the cutover (MIK-R37).
"""

from __future__ import annotations

from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.report import WriteReport
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge

__all__ = ["Owner", "WriteReport", "WriteRequest", "write_knowledge"]
