# Prepare coherence judgments

Curators read this topic before preparing a coherence judgment. It distinguishes the full catalog projection from leaf gates and defines the requirement, delivery and disposition fields.

In a full `memory_quality_check` answer, `finalFullCatalog` is the projection for later certification: affected closure, census certification and `finalizationEligible`. It is not a leaf gate. The leaf's memory gates are readiness for closeout, current coherence and integrity.

When publishing `curator_coherence`, set `semantic_requirement_revision` to the leaf's primary requirement as `<ID>@v<N>`. Set `delivery_attempt` to the Worker's delivery-attempt identifier from its attempt journal. Each judgment's `evidenceRef` names one existing file and its namespace, such as `code:src/example.py`.

Use the disposition that describes what happened to the artifact:

| Disposition | Meaning |
| --- | --- |
| `reconciled` | Authored content was corrected to agree with approved intent and code, with one history row. A citation pointing elsewhere changes content; a refreshed hash of the same anchor does not. |
| `extended`, `superseded` | Content was added or replaced under an existing requirement or decision, which you cite. |
| `preserved` | A record was reviewed and remains current, unchanged. |
| `no-content-impact` | A file card remains correct and its bytes are unchanged. |
| `no-route-impact` | An overview remains correct and its bytes are unchanged. |
| `contradicted` | Requirement and code cannot both hold. Report the blocker to its owner. |
| `capture-candidate` | Possible knowledge nobody requested. Put it in the report; it never closes a judgment alone. |
