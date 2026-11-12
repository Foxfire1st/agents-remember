# Judge progress at the third rejected round

Managers and Architects use this file to judge progress at a third rejected round, consider an earlier coder switch or Reviewer replacement, and distinguish coder rounds from automatic step-up counts. Each acts within its existing assigned authority.

Start with the Worker role's default. At the same coder's third rejected round, the Manager reads the sealed finding trail and judges observed progress. Under developer-chosen direct coordination, the Architect does this. A third rejection does not automatically require a stronger model.

Clear progress may justify one more bounded repair with a concrete recorded reason and a separately authorized further round. If the fixes remain too large or have barely moved, use the configured stronger model in `role.stepUp`. If none is configured, the Manager asks the Architect through its existing parent route. Under direct coordination, the Architect uses its own developer-decision route.

A stronger model grants no extra review round. Switching the coder earlier requires the developer's agreement; a low closed-finding count does not authorize it. Replace a Reviewer only on clear observed signs.

The three-failed-rounds rule counts the rounds of the current coder. The product's automatic step-up (`afterTries`) counts the task's failed review rounds of the matching lane, not only the current coder's, and does not reset when the coder changes; apply the round limits as written.

