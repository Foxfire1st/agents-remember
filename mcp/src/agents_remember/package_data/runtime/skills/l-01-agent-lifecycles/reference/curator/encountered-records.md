# Record weaknesses and document dependencies

Curators read this topic before authoring an encountered weakness or a document dependency. It describes their writer outcomes, confirmation evidence and worklist answers.

Record weaknesses and dependents through the writer, like other records. Before authoring either kind, read [The two record kinds a producer may name](../../templates/curator-handoff-list.md#the-two-record-kinds-a-producer-may-name) for record shapes, file and sentence bounds, and writer-filled confirmation values.

A producer entry of kind `weakness` becomes a `WKN-` record, or the report explains why it did not. The writer refuses an unknown entry kind and names the field.

A producer entry of kind `dependent` becomes a `DPD-` record.

Answer the worklist row for a weakness by deciding whether it stands, with its anchor moving with the code, or is closed. For a changed document, only the confirmed dependent record answers the row. Neither record refuses a task.

A weakness that your own task removes closes with that task: name the running task's ID as the closure's `task` and one sentence as its `reason`, and write no commit. A closure without a commit that names another task is refused. A closure by another task names that task's real code commit, and the writer refuses a value that is no commit of the code repository. After the closeout, the read of the closed record gives the code commit from the `Code-Commit` trailer of the memory commit that closed it; before it, the read says that the commit comes with the task's closeout.
