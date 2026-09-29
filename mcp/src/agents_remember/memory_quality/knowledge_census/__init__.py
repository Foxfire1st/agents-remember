"""The migration census over text files: reading, integrity checks, measures, report (MIK-R20).

The file formats are :mod:`agents_remember.models.knowledge_files.census`. This package reads them
from a memory tree's bytes, so it depends on nothing above the models:

* :mod:`.files` -- census locations and :func:`read_censuses`;
* :mod:`.checks` -- the integrity checks the validator registers (MIK-R22 rule 9), including the
  append-only rules for route statuses and claim assessments;
* :mod:`.measures` -- ``N = T + F + U + P`` and the Doc12 measures;
* :mod:`.report` -- one report per census, sliced by route and claim kind, with route status.

The mechanical inventory and the census writer read Git and write a memory working tree; they are
in :mod:`agents_remember.memory.knowledge_census`.
"""

from __future__ import annotations

from agents_remember.memory_quality.knowledge_census.checks import (
    CENSUS_RULES,
    CensusFinding,
    check_censuses,
    record_ids_in,
)
from agents_remember.memory_quality.knowledge_census.files import (
    CensusTree,
    ParsedCensus,
    baseline_path,
    claims_path,
    inventory_path,
    read_censuses,
    route_status_path,
)
from agents_remember.memory_quality.knowledge_census.measures import (
    Counts,
    Measure,
    compute_measures,
)
from agents_remember.memory_quality.knowledge_census.report import (
    CensusReport,
    census_report,
    census_reports,
)

__all__ = [
    "CENSUS_RULES",
    "CensusFinding",
    "CensusReport",
    "CensusTree",
    "Counts",
    "Measure",
    "ParsedCensus",
    "baseline_path",
    "census_report",
    "census_reports",
    "check_censuses",
    "claims_path",
    "compute_measures",
    "inventory_path",
    "read_censuses",
    "record_ids_in",
    "route_status_path",
]
