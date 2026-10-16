import { taskReferenceKey } from "../data/taskReference";
// DEV-only: made-up task documents rich enough to draw every block of the three task-page
// skeletons (MIK-R127 rule 10) — a sprint with masters in every state, a master with tasks in
// work, waiting, completed and abandoned, and a task with steps, a review round, decisions,
// references and a report. All text is invented; nothing here is read from a real task.
import type {
  TaskDecisionNode,
  TaskEntry,
  TaskSummary,
  TaskExecutionNodeView,
  TaskReviewStateNode,
  TaskStepNode,
} from '../types/projection';
import { taskEntry, taskSummary } from '../test/fixtures/wire';

export const RICH_REPO = 'sandbox-app';
const SPRINT_DIR = 'sbx-sprint';
const MASTER_DIR = 'sbx-text-helpers';

const docPath = (dir: string, file: string): string => `${dir}/${file}.json`;

export const RICH_DOC_PATHS = {
  sprint: docPath(SPRINT_DIR, 'task'),
  master: docPath(MASTER_DIR, 'task'),
  task: docPath(MASTER_DIR, '01_slugify'),
};

function step(
  id: string,
  title: string,
  status: string,
  over: Partial<TaskStepNode> = {},
): TaskStepNode {
  return {
    id,
    title,
    status,
    substeps: [],
    noteTruncated: false,
    outcomeTruncated: false,
    ...over,
  };
}

function decisions(count: number, topic: string): TaskDecisionNode[] {
  return Array.from({ length: count }, (_, index) => ({
    at: `2026-10-${String(3 + index).padStart(2, '0')}T${String(9 + index).padStart(2, '0')}:15:00+02:00`,
    decision: `${topic}: ruling ${index + 1} keeps the helper pure and names the one caller that changes with it, so a later leaf can rely on the behaviour without reading this one again.`,
    rationale: `Made-up rationale ${index + 1}: the simpler rule covers every example of the packet, and the alternative would have needed a second pass over the same files.`,
    product_record: false,
  }));
}

function review(over: Partial<TaskReviewStateNode>): TaskReviewStateNode {
  return {
    round: 1,
    additionalRounds: 0,
    pending: false,
    findings: [],
    totalFindings: 0,
    openFindings: (over.findings ?? []).filter((finding) => finding.open).length,
    ...over,
  };
}

// --------------------------------------------------------------------------- the leaves

interface LeafSpec {
  number: string;
  slug: string;
  title: string;
  stage: TaskEntry['stage'];
  requirement: string;
  steps: TaskStepNode[];
  reviewState?: TaskReviewStateNode;
}

const plainSteps = (count: number, done: number): TaskStepNode[] =>
  Array.from({ length: count }, (_, index) =>
    step(
      `S${index + 1}`,
      `Made-up step ${index + 1} of the helper`,
      index < done ? 'done' : 'pending',
    ),
  );

const LEAVES: LeafSpec[] = [
  {
    number: 'L1',
    slug: '01_slugify',
    title: 'Add slugify() and its test',
    stage: 'started',
    requirement: 'SBX-R01',
    steps: [
      step('S1', 'Write `textkit/slug.py` with `slugify(text: str) -> str`', 'done', {
        note: 'The function lowercases first and collapses every run of other characters into one hyphen afterwards; the order matters for mixed-case runs, and the boundary case of an empty string returns an empty string without a special branch.',
      }),
      step('S2', 'Write `tests/test_slug.py`, including the boundary case', 'done', {
        outcome: 'Six cases, all pass.',
      }),
      step('S3', 'Run the unit tests from the repository root and report', 'inProgress', {
        substeps: [
          { id: 'S3.1', title: 'Run the suite once on a clean tree', status: 'done' },
          { id: 'S3.2', title: 'Attach the output to the report', status: 'inProgress' },
        ],
      }),
      step('S4', 'Independent review against the packet, and fix verification', 'pending'),
      // The product records a skip as a done step that carries its disposition.
      step('S5', 'Benchmark the helper on a long text', 'done', {
        disposition: {
          kind: 'intentionalSkip',
          reason: 'The packet asks for no performance evidence; the step was planned by mistake.',
          recordedAt: '2026-10-06T10:00:00+02:00',
          recordedVia: 'task_doc.skip_step',
        },
      }),
      step('S6', 'Curation of the knowledge the leaf changes, and landing', 'pending'),
    ],
    reviewState: review({
      round: 4,
      additionalRounds: 2,
      developerApproval:
        'Made-up approval: the ordinary limit of three rounds is raised by two for this leaf, the sealed list stays as it is, and rounds four and five verify repairs only.',
      totalFindings: 4,
      findings: [
        {
          findingId: 'SBX-L1-F01',
          open: true,
          descriptionTruncated: false,
          description:
            'The boundary test asserts the empty string only; the packet also names a text of punctuation alone, which must return an empty string and is not covered by any case.',
        },
        {
          findingId: 'SBX-L1-F02',
          open: true,
          descriptionTruncated: false,
          description: 'A non-string argument is coerced instead of raising TypeError.',
        },
        {
          findingId: 'SBX-L1-F03',
          open: false,
          descriptionTruncated: false,
          description: 'The module docstring named the wrong function.',
        },
        {
          findingId: 'SBX-L1-F04',
          open: false,
          descriptionTruncated: false,
          description: 'Two hyphens survived where a run crossed a line break.',
        },
      ],
    }),
  },
  {
    number: 'L2',
    slug: '02_word-count',
    title: 'Add word_count() and its test',
    stage: 'started',
    requirement: 'SBX-R02',
    steps: plainSteps(5, 1),
    reviewState: review({ round: 1, pending: true }),
  },
  {
    number: 'L3',
    slug: '03_truncate',
    title: 'Add truncate() with an ellipsis and its test',
    stage: 'started',
    requirement: 'SBX-R03',
    steps: plainSteps(4, 0),
  },
  {
    number: 'L4',
    slug: '04_wrap',
    title: 'Add wrap() for fixed-width lines',
    stage: 'planned',
    requirement: 'SBX-R04',
    steps: plainSteps(5, 0),
  },
  {
    number: 'L5',
    slug: '05_dedent',
    title: 'Add dedent() that keeps relative indentation',
    stage: 'planned',
    requirement: 'SBX-R05',
    steps: plainSteps(4, 0),
  },
  {
    number: 'L6',
    slug: '06_squeeze',
    title: 'Add squeeze() for repeated blank lines',
    stage: 'planned',
    requirement: 'SBX-R06',
    steps: plainSteps(3, 0),
  },
  {
    number: 'L7',
    slug: '07_title-case',
    title: 'Keep title_case() stable under the new helpers',
    stage: 'completed',
    requirement: 'SBX-R07',
    steps: plainSteps(3, 3),
  },
  {
    number: 'L8',
    slug: '08_strip-accents',
    title: 'Add strip_accents() for Latin letters',
    stage: 'completed',
    requirement: 'SBX-R08',
    steps: plainSteps(4, 4),
  },
  {
    number: 'L9',
    slug: '09_initials',
    title: 'Add initials() of a name',
    stage: 'completed',
    requirement: 'SBX-R09',
    steps: plainSteps(3, 3),
  },
  {
    number: 'L10',
    slug: '10_pig-latin',
    title: 'Add pig_latin() as a demonstration',
    stage: 'aborted',
    requirement: 'SBX-R10',
    steps: plainSteps(3, 0),
  },
];

function leafDoc(leaf: LeafSpec): TaskEntry {
  // As the projection counts: major steps only; nested detail remains on its step.
  const declared = leaf.steps;
  const done = declared.filter((entry) => entry.status === 'done').length;
  const packet = `requirements/${leaf.requirement}-v1.md`;
  return taskEntry({
    id: `SBX-M1-${leaf.number}`,
    title: leaf.title,
    reference: { repository: RICH_REPO, path: docPath(MASTER_DIR, leaf.slug) },
    stage: leaf.stage,
    parent: taskReferenceKey(masterRef(MASTER_DIR)),
    children: [],
    stepsDone: done,
    stepsTotal: declared.length,
    summary: taskSummary({
      bodyRevision: 'rich-1',
      createdAt: '2026-10-02T00:00:00+02:00',
      type: 'SubTask (sandbox fixture)',
      completedAt: leaf.stage === 'completed'
        ? `2026-10-${leaf.number.slice(1).padStart(2, '0')}T12:00:00Z` : undefined,
      steps: leaf.steps,
      reviewState: leaf.reviewState,
      objective: `Made-up objective of ${leaf.title.toLowerCase()}.`,
      requirements: [`[${leaf.requirement}@v1](${packet}) — sole owned primary.`],
      headerNotes: [
        { label: 'Master', value: '[SBX-M1](task.md)' },
        { label: 'Primary requirement', value: `[${leaf.requirement}@v1](${packet})` },
      ],
    }),
  });
}

function richTask(): TaskEntry {
  const leaf = LEAVES[0];
  const doc = leafDoc(leaf);
  return {
    ...doc,
    summary: taskSummary({
      ...doc.summary,
      statusNote:
        'In work since 2026-10-05. Review round 4 runs under the raised limit; two findings of the sealed list remain open and both concern the boundary cases of the packet. The leaf waits for nothing and lands before L4.',
      headerNotes: [
        ...(doc.summary?.headerNotes ?? []),
        {
          label: 'Dependencies',
          value:
            'Waits for nothing. L4 (wrap) and L5 (dedent) change the same package: either order, the later one keeps the other change.',
        },
        { label: 'Stage', value: 'Sandbox run, first wave' },
        {
          label: 'Authority',
          value:
            '[Sandbox direction](notes/SANDBOX-DIRECTION.md) S1, S2; the architect of the sandbox run, as recorded in notes/STATE.md of this made-up master',
        },
      ],
      headerNotesTotal: 5,
      objective: [
        '- `textkit/slug.py` provides `slugify(text: str) -> str`.',
        '- It lowercases the text and replaces every run of characters other than ASCII letters and digits with one hyphen.',
        '- Leading and trailing hyphens are removed, so a text of punctuation alone gives an empty string.',
        '- An argument that is not a string raises `TypeError`; nothing is coerced.',
        '- `tests/test_slug.py` covers the conforming, the non-conforming and the boundary example of the packet.',
      ].join('\n'),
      requirements: [
        '[SBX-R01@v1](requirements/SBX-R01-v1.md) — sole owned primary.',
        'The existing helper `title_case()` and its tests stay unchanged (preservation boundary of the packet).',
      ],
      design:
        'One pure function and no state. The text is lowercased first, then one regular expression replaces every run of other characters.\n\nThe hyphens at both ends are stripped last, which also settles the empty and the punctuation-only text.',
      codeExamples: [
        {
          id: 'E1',
          title: 'The one expression',
          distinctChange: 'collapses a run instead of replacing each character',
          why: 'two hyphens in a row are the non-conforming example of the packet',
          language: 'python',
          snippet: 'return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")',
        },
        {
          id: 'E2',
          title: 'The type guard',
          distinctChange: 'raises instead of coercing',
          why: 'the failure rule of the packet',
          language: 'python',
          snippet: 'if not isinstance(text, str):\n    raise TypeError("text must be a string")',
        },
      ],
      decisions: decisions(7, 'slugify'),
      openQuestions: [
        'Does a digit-only text keep its leading zeros? The packet gives no example.',
      ],
      references: [
        '[Primary packet](requirements/SBX-R01-v1.md)',
        '[Sandbox direction](notes/SANDBOX-DIRECTION.md)',
        '[Master](task.md)',
        'The standard library documentation of `re.sub`, read on 2026-10-04',
      ],
      sections: [
        {
          kind: 'freeform',
          heading: 'Notes on the boundary cases',
          body: 'An empty text and a text of punctuation alone both give an empty string.\n\n| Input | Output |\n| --- | --- |\n| `""` | `""` |\n| `"!!!"` | `""` |\n| `" A  b "` | `"a-b"` |',
        },
        {
          kind: 'freeform',
          heading: 'What the review asked',
          body: 'Round 1 asked for the punctuation-only case. Round 2 asked for the type guard. Both are repaired in the candidate of round 4.',
        },
      ],
    }),
  };
}

// --------------------------------------------------------------------------- the master

const MASTER_PRODUCT_RECORD: TaskDecisionNode = {
  at: '2026-10-10T12:00:00Z',
  decision: 'Finalize task lifecycle.',
  rationale: 'Made-up retained product record for the sandbox only.',
  product_record: true,
};

function richMaster(): TaskEntry {
  return taskEntry({
    id: 'SBX-M1',
    title: 'Sandbox master: text helpers for the sandbox app',
    reference: { repository: RICH_REPO, path: RICH_DOC_PATHS.master },
    stage: 'started',
    parent: taskReferenceKey(masterRef(SPRINT_DIR)),
    children: LEAVES.map((leaf) => ({
      repository: RICH_REPO,
      path: docPath(MASTER_DIR, leaf.slug),
    })).map(taskReferenceKey),
    stepsDone: 0,
    stepsTotal: 0,
    summary: taskSummary({
      bodyRevision: 'rich-1',
      createdAt: '2026-10-02T00:00:00+02:00',
      kind: 'master',
      form: 'master',
      type: 'Master (text helpers)',
      executionNature: 'organizational',
      statusNote:
        'In work, first wave. Three leaves are open at once because they touch different modules; the fourth waits for the review of L1, whose boundary rules the later helpers reuse. Nothing of this master is deferred to another one.',
      headerNotes: [
        { label: 'Parent portfolio', value: '[SBX-SPRINT](../sbx-sprint/task.md)' },
        { label: 'Source intake', value: 'Sandbox fixture of 2026-10-02' },
        {
          label: 'Authoring authority',
          value: '[Sandbox direction](notes/SANDBOX-DIRECTION.md) S1–S4',
        },
        {
          label: 'Execution boundary',
          value:
            'Implementation go given for the first wave. Each leaf owns one requirement and one module; a leaf that needs a second module asks the manager of the sandbox run before it edits it, and the answer is written into its decision log.',
        },
      ],
      headerNotesTotal: 4,
      objective: [
        '- Add small, independent text helpers to the sandbox app, one per leaf.',
        '- Every helper is a pure function with its own test module.',
        '- The work is disposable: it exists so that role agents started from the sandbox have a real task.',
      ].join('\n'),
      requirements: [
        '[SBX-R01@v1](requirements/SBX-R01-v1.md) — slugify, owned by L1.',
        '[SBX-R02@v1](requirements/SBX-R02-v1.md) — word_count, owned by L2.',
        'Every further helper has its own packet under `requirements/`, registered in `requirements/README.md`.',
      ],
      decisions: [...decisions(7, 'text helpers'), MASTER_PRODUCT_RECORD],
      references: [
        '[Sandbox direction](notes/SANDBOX-DIRECTION.md)',
        '[State of the run](notes/STATE.md)',
      ],
      sections: [
        {
          kind: 'subTasks',
          heading: 'Leaves',
          body: 'One leaf per helper. A leaf lands on its focused tests; the whole suite runs once when the master ends.',
        },
        {
          kind: 'sharedDecisions',
          heading: 'Shared decisions',
          body: 'Decisions that bind more than one leaf are recorded here and not in a leaf.',
        },
        {
          kind: 'freeform',
          heading: 'Execution order',
          body: '1. L1, L2 and L3 in the first wave.\n2. L4 after the review of L1.\n3. L5 and L6 last.',
        },
        {
          kind: 'freeform',
          heading: 'Risks',
          body: '| Risk | Answer |\n| --- | --- |\n| Two helpers edit `textkit/__init__.py` | the later leaf keeps both lines |\n| A helper grows a dependency | refused by the packet |',
        },
      ],
      discardedCount: 0,
    }),
  });
}

// --------------------------------------------------------------------------- the sprint

interface MasterSpec {
  dir: string;
  id: string;
  title: string;
  stage: TaskEntry['stage'];
  nature: 'organizational' | 'atomic';
  tasks: TaskEntry['stage'][];
}

const OTHER_MASTERS: MasterSpec[] = [
  {
    dir: 'sbx-date-helpers',
    id: 'SBX-M2',
    title: 'Sandbox master: date helpers',
    stage: 'started',
    nature: 'atomic',
    tasks: ['completed', 'completed', 'started', 'planned'],
  },
  {
    dir: 'sbx-path-helpers',
    id: 'SBX-M3',
    title: 'Sandbox master: path helpers for the sandbox app',
    stage: 'started',
    nature: 'organizational',
    tasks: ['started', 'planned', 'planned', 'planned', 'planned'],
  },
  {
    dir: 'sbx-cli',
    id: 'SBX-M4',
    title: 'Sandbox master: a command line for the helpers',
    stage: 'planned',
    nature: 'organizational',
    tasks: ['planned', 'planned', 'planned'],
  },
  {
    dir: 'sbx-docs',
    id: 'SBX-M5',
    title: 'Sandbox master: the handbook of the sandbox app',
    stage: 'planned',
    nature: 'atomic',
    tasks: ['planned', 'planned'],
  },
  {
    dir: 'sbx-bootstrap',
    id: 'SBX-M6',
    title: 'Sandbox master: repository bootstrap',
    stage: 'completed',
    nature: 'atomic',
    tasks: ['completed', 'completed', 'completed'],
  },
  {
    dir: 'sbx-ci',
    id: 'SBX-M7',
    title: 'Sandbox master: the test command',
    stage: 'completed',
    nature: 'atomic',
    tasks: ['completed', 'completed'],
  },
];

function otherLeaves(spec: MasterSpec): TaskEntry[] {
  return spec.tasks.map((stage, index) =>
    taskEntry({
      id: `${spec.id}-L${index + 1}`,
      title: `Made-up task ${index + 1}`,
      reference: {
        repository: RICH_REPO,
        path: docPath(spec.dir, `${String(index + 1).padStart(2, '0')}_task`),
      },
      parent: taskReferenceKey(masterRef(spec.dir)),
      stage,
    }),
  );
}

function otherMaster(spec: MasterSpec): TaskEntry {
  return taskEntry({
    id: spec.id,
    title: spec.title,
    reference: { repository: RICH_REPO, path: docPath(spec.dir, 'task') },
    stage: spec.stage,
    parent: taskReferenceKey(masterRef(SPRINT_DIR)),
    children: otherLeaves(spec).map((entry) => entry.key),
    stepsDone: 0,
    stepsTotal: 0,
    summary: taskSummary({
      bodyRevision: 'rich-1',
      createdAt: '2026-10-02T00:00:00+02:00',
      kind: 'master',
      form: 'master',
      type: 'Master',
      executionNature: spec.nature,
      objective: `Made-up objective of ${spec.title.toLowerCase()}.`,
    }),
  });
}

const masterRef = (dir: string) => ({ repository: RICH_REPO, path: `${dir}/task.json` });

function graphNode(
  dir: string,
  title: string,
  waveIndex: number,
  frontierState: TaskExecutionNodeView['frontierState'],
  predecessors: Array<{ dir: string; title: string; reason: string }> = [],
): TaskExecutionNodeView {
  return {
    nodeId: dir,
    kind: 'lump',
    masterRef: masterRef(dir),
    masterTitle: title,
    leafIds: [],
    leafTitles: [],
    waveIndex,
    frontierState,
    predecessors: predecessors.map((entry) => ({
      predecessorRef: masterRef(entry.dir),
      predecessorTitle: entry.title,
      reason: entry.reason,
    })),
  };
}

const registerRows = (count: number, cells: (index: number) => string[]): string =>
  Array.from({ length: count }, (_, index) => `| ${cells(index + 1).join(' | ')} |`).join('\n');

const SPRINT_GRAPH: TaskExecutionNodeView[] = [
  graphNode('sbx-bootstrap', 'Repository bootstrap', 0, 'landed'),
  graphNode('sbx-ci', 'The test command', 0, 'landed'),
  graphNode(MASTER_DIR, 'Text helpers for the sandbox app', 1, 'in-flight', [
    { dir: 'sbx-ci', title: 'The test command', reason: 'the helpers land on their tests' },
  ]),
  graphNode('sbx-date-helpers', 'Date helpers', 1, 'in-flight'),
  graphNode('sbx-path-helpers', 'Path helpers for the sandbox app', 1, 'in-flight'),
  graphNode('sbx-cli', 'A command line for the helpers', 2, 'waiting', [
    {
      dir: MASTER_DIR,
      title: 'Text helpers for the sandbox app',
      reason: 'calls slugify and wrap',
    },
    {
      dir: 'sbx-path-helpers',
      title: 'Path helpers for the sandbox app',
      reason: 'resolves its arguments',
    },
  ]),
  graphNode('sbx-docs', 'The handbook of the sandbox app', 3, 'waiting', [
    { dir: 'sbx-cli', title: 'A command line for the helpers', reason: 'documents the commands' },
  ]),
];

// A register is its table and nothing else: the product refuses a line above the header row.
const JUDGMENT_REGISTER = `| Judgment | State | Subject | Ruling | Evidence | Owner | Recorded |
| --- | --- | --- | --- | --- | --- | --- |
${registerRows(8, (n) => [
  `J-${n}`,
  n % 3 === 0 ? 'superseded' : 'current',
  `Order of master ${n} and its neighbour`,
  `Made-up ruling ${n}: the earlier master lands first because the later one calls it.`,
  `notes/judgment-${n}.md`,
  'architect',
  `2026-10-0${(n % 8) + 1}`,
])}`;

const PRIORITY_REGISTER = `| Priority | Rank | Reason | Since |
| --- | --- | --- | --- |
${registerRows(7, (n) => [
  `Made-up priority ${n} of the sandbox sprint`,
  String(n),
  `It unblocks ${n + 1} later tasks.`,
  `2026-10-0${(n % 8) + 1}`,
])}`;

const SPRINT_SECTIONS: TaskSummary['sections'] = [
  { kind: 'subTasks', heading: 'Commanded masters', body: '' },
  {
    kind: 'freeform',
    heading: 'Judgment Register (canonical judgment authority)',
    body: JUDGMENT_REGISTER,
  },
  {
    kind: 'freeform',
    heading: 'Priority Register (explicit judgment)',
    body: PRIORITY_REGISTER,
  },
  {
    kind: 'freeform',
    heading: 'How the sandbox is reset',
    body: 'The sandbox is one directory. `reset` stops what `start` started and deletes it; nothing outside the directory is written.',
  },
  {
    kind: 'freeform',
    heading: 'Ports of the sandbox',
    body: '| Service | Port |\n| --- | --- |\n| host | 6820 |\n| dashboard | 9797 |',
  },
];

const SPRINT_HEADER_NOTES: TaskSummary['headerNotes'] = [
  {
    label: 'Portfolio gate',
    value:
      'Each attached master keeps its own release and authority state. The sandbox sprint lands nothing outside the sandbox and is reset with it.',
  },
  {
    label: 'Core hard order',
    value:
      'The repository bootstrap landed first and the test command second; helpers follow in any order, the command line after the helpers it calls, and the handbook last because it documents what exists.',
  },
  { label: 'Stage', value: 'First wave released' },
];

interface SprintMaster {
  dir: string;
  id: string;
  title: string;
}

const sprintMasters = (): SprintMaster[] => [
  {
    dir: MASTER_DIR,
    id: 'SBX-M1',
    title: 'Text helpers for the sandbox app',
  },
  ...OTHER_MASTERS.map((spec) => ({
    dir: spec.dir,
    id: spec.id,
    title: spec.title.replace('Sandbox master: ', ''),
  })),
];

function richSprint(): TaskEntry {
  const masters = sprintMasters();
  return taskEntry({
    id: 'SBX-SPRINT',
    title: 'Sandbox sprint: helpers, a command line and a handbook for the sandbox app',
    reference: { repository: RICH_REPO, path: RICH_DOC_PATHS.sprint },
    stage: 'started',
    children: [taskReferenceKey({ repository: RICH_REPO, path: docPath(SPRINT_DIR, '00_architect-seat') }), ...masters.map((entry) => taskReferenceKey(masterRef(entry.dir)))],
    stepsDone: 0,
    stepsTotal: 0,
    summary: taskSummary({
      bodyRevision: 'rich-1',
      createdAt: '2026-10-02T00:00:00+02:00',
      kind: 'master',
      form: 'sprint',
      type: 'Orchestration Sprint',
      integrationBranch: 'sandbox-sprint',
      orchestrates: masters.map((entry) => entry.dir),
      statusNote:
        'Master membership is recorded in the list of commanded masters and in the execution graph. Three masters are in work; the command line waits for the text and the path helpers, and the handbook waits for the command line. Nothing is released beyond the first wave.',
      headerNotes: SPRINT_HEADER_NOTES,
      headerNotesTotal: SPRINT_HEADER_NOTES.length,
      objective:
        'Give the sandbox app a small set of helpers, a command line that calls them and a handbook that documents both. The sprint exists so that every form of the task page has a document that shows all of its blocks.',
      requirements: [
        'The repository bootstrap and the test command land before any helper.',
        '[SBX-R01@v1](requirements/SBX-R01-v1.md) — the first helper, owned by the text helpers.',
        'The command line calls only helpers that have landed; the handbook documents only what exists.',
      ],
      executionGraphView: { nodes: SPRINT_GRAPH },
      decisions: decisions(7, 'sandbox sprint'),
      references: [
        '[Sandbox direction](notes/SANDBOX-DIRECTION.md)',
        '[SBX-R01@v1](requirements/SBX-R01-v1.md)',
        'The README of the sandbox app',
      ],
      sections: SPRINT_SECTIONS,
    }),
  });
}

// The same entry collection served to the dashboard.
export function richTaskEntries(): TaskEntry[] {
  const task = richTask();
  const entries = [
    richSprint(),
    richMaster(),
    task,
    ...LEAVES.slice(1).map(leafDoc),
    ...OTHER_MASTERS.flatMap((spec) => [otherMaster(spec), ...otherLeaves(spec)]),
    taskEntry({
      id: 'SBX-SPRINT-A0',
      title: 'Architect seat of the sandbox sprint',
      reference: { repository: RICH_REPO, path: docPath(SPRINT_DIR, '00_architect-seat') },
      parent: taskReferenceKey(masterRef(SPRINT_DIR)),
      stage: 'started',
    }),
  ];
  for (const entry of entries) entry.counts = { ...entry.counts };
  const byKey = new Map(entries.map((entry) => [entry.key, entry]));
  for (const entry of entries) {
    for (const child of entry.children) {
      const childEntry = byKey.get(child);
      if (childEntry) entry.counts[childEntry.stage] += 1;
    }
  }
  return entries;
}

// What the page asks beside the snapshot, answered with made-up data: the notes folder
// count, the resolution of the references, the requirement packets and one role report.
function richTaskBody(url: string): TaskSummary | undefined {
  const path = new URL(url, 'http://dev.invalid').searchParams.get('path');
  return richTaskEntries().find(
    (entry) => entry.key === path,
  )?.summary;
}

export function richApiAnswer(url: string, body: string | undefined): unknown {
  if (url.includes('/api/task-document?')) return richTaskBody(url);
  if (url.includes('/api/notes/folder')) {
    return {
      repo: RICH_REPO,
      master: MASTER_DIR,
      path: '',
      entries: [],
      total: 14,
      offset: 0,
      limit: 50,
      remaining: 0,
      exists: true,
      reason: null,
    };
  }
  if (url.includes('/api/notes/resolve')) {
    const references = (JSON.parse(body ?? '{}') as { references?: string[] }).references ?? [];
    return {
      resolutions: references.map((reference) => ({
        reference,
        path: reference.includes('notes/')
          ? reference.replace(/^.*\(notes\/([^)]+)\).*$/, '$1')
          : null,
      })),
    };
  }
  if (url.includes('/api/requirements/list')) {
    return {
      repo: RICH_REPO,
      master: MASTER_DIR,
      document: '',
      registered: true,
      requirements: Array.from({ length: 10 }, (_, index) => {
        const id = `SBX-R${String(index + 1).padStart(2, '0')}`;
        return {
          name: `${id}-v1.md`,
          path: `${id}-v1.md`,
          address: `requirements/${id}-v1.md`,
          size: 2400,
          sha256: '0'.repeat(64),
        };
      }),
    };
  }
  if (url.includes('/api/role-launch/reports')) {
    return {
      repo: RICH_REPO,
      master: MASTER_DIR,
      doc: '',
      total: 2,
      explorer: null,
      reports: [
        {
          role: 'reviewer',
          requestId: '5e0b3a10-0000-4000-8000-000000000002',
          createdAt: '2026-10-06T09:12:00+02:00',
          status: 'completed',
          path: null,
          canonicalPath: null,
          available: true,
        },
        {
          role: 'worker',
          requestId: '5e0b3a10-0000-4000-8000-000000000001',
          createdAt: '2026-10-05T08:40:00+02:00',
          status: 'completed',
          path: null,
          canonicalPath: null,
          available: true,
        },
      ],
    };
  }
  return undefined;
}
