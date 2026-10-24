"""The control vocabulary shared by the product's role-execution and bridge seams.

These are the spellings a receipt, a held store, a report log and a provider-limit hold carry on
disk and across the bridge. They are defined once here because the tiers that write them
(``cli``), read them (``application``, ``canvas``, ``serving``) and type them on the wire must
agree on the same bytes. A tier may import this module; this module imports nothing internal.
"""

from __future__ import annotations

from typing import Final

#: Where a task's role-execution receipts live, under its ``notes/reports`` folder.
EXECUTIONS_DIRECTORY: Final = "paseo-native-executions"
#: The schema name every execution receipt of this line carries.
RECEIPT_SCHEMA: Final = "ar-role-execution/v1"
#: The report log one master's architect reads.
REPORT_LOG_NAME: Final = "reports.jsonl"
#: The held-message store's schema name.
HELD_STORE_SCHEMA: Final = "ar-held-messages/v1"
#: The reason a hold names when the provider limit is what waits.
PROVIDER_LIMIT_REASON: Final = "provider-limit"
#: The bridge's own three outcomes, as a caller classifies a failed call.
BRIDGE_TIMEOUT: Final = "paseo_bridge_timeout"
BRIDGE_REFUSED: Final = "paseo_call_failed"
SEND_OUTCOME_UNKNOWN: Final = "paseo_send_outcome_unknown"

__all__ = [
    "BRIDGE_REFUSED",
    "BRIDGE_TIMEOUT",
    "EXECUTIONS_DIRECTORY",
    "HELD_STORE_SCHEMA",
    "PROVIDER_LIMIT_REASON",
    "RECEIPT_SCHEMA",
    "REPORT_LOG_NAME",
    "SEND_OUTCOME_UNKNOWN",
]
