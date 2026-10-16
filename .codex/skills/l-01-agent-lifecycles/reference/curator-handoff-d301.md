# Curator — handoff (D301)

Read the relevant section before the named step. Existing detail-source names below mean the separately delivered workspace paths linked in the capsule, not literal filesystem paths.

## Before memory review

The reviewer compares the leaf's base and candidate from Git (the code trees and the knowledge files of the
two memory trees), so the curator records no separate comparison.

## Before memory handover and repair passes

Each handover names the leaf, the thing's exact identity (the Worker's code head and change hash, and
the memory candidate's identity), its report or verdict path, and the whole next stretch requested.
The memory check keeps separate sealed reports, finding IDs and its own pass count: it opens
no code-review round, resets no code verdict and adds no IDs to the code baseline. Its ordinary limit is
the same number of passes as the code review. A pass beyond the settings limit
needs the architect's or the developer's word recorded by the Manager. Receive memory findings or PASS
directly from the Reviewer and repair within that lane; the Manager carries none of these handovers.
