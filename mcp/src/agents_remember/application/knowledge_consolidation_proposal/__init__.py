"""The reader view that proposes the list of a consolidation pass (``consolidation_proposal``).

:mod:`.store` reads the families, rules and realization folders of a tree from its index once;
:mod:`.history_facts` reads what only history files hold (a master's writes, past passes);
:mod:`.first_written` reads when each family record was first committed; :mod:`.proposal` chooses
the families of each way; :mod:`.view` bounds the answer and writes the full lists to a report.
"""

from __future__ import annotations

from .refusal import ProposalRefusal
from .view import PROPOSAL_VIEW, ProposalRequest, proposal_view

__all__ = ["PROPOSAL_VIEW", "ProposalRefusal", "ProposalRequest", "proposal_view"]
