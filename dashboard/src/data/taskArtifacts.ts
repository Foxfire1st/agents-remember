import type { TaskDecisionNode } from "../types/projection";

export type TaskArtifactReaderTarget =
  | {
      kind: 'notes';
      repo: string;
      master: string;
      // The open note's notes-relative path. Absent when the reader opens on a folder
      // (the rule-3 root link, the rule-5 explorer link): the rail shows the tree and
      // the pane waits for a selection.
      path?: string;
      // Open directly on a folder instead of a note. Set for the root/explorer links.
      folder?: boolean;
    }
  | {
      kind: 'requirements';
      repo: string;
      master: string;
      document: string;
      path: string;
    }
  | {
      kind: 'task-report';
      repo: string;
      master: string;
      role: string;
      requestId: string;
      taskRepository: string;
      taskPath: string;
    }
  | {
      kind: 'decisions';
      repo: string;
      master: string;
      title: string;
      // The log's entries in time order, as the page holds them; the whole-log page
      // shows them newest first with product-written records muted.
      decisions: TaskDecisionNode[];
    };
