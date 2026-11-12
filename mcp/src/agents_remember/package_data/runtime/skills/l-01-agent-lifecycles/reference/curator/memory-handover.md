# Curator memory handover

Curators use this file before sending a memory candidate for review or returning a repair. It identifies the candidate, review lane and direct recipient.

## Identify the reviewed trees

The Reviewer compares the leaf's base and candidate in Git: the code trees and the knowledge files in both memory trees. The Curator records no separate comparison.

## Send the whole requested review or repair

Each handover names the leaf, the Worker's exact code head and change hash, the memory candidate's identity, the report or verdict path, and the whole next stretch requested.

Before sending a memory candidate for review, read [Review boundaries](../../operations/review.md#boundaries) for separate reports, finding identifiers, pass count, code-state isolation and recorded extra-pass approval.

Receive memory findings or a passing verdict directly from the Reviewer, and repair within that lane. The Manager relays none of these handovers.
