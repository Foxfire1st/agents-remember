import { taskReferenceKey } from "../../data/taskReference";
import { taskEntry, taskSummary } from './wire';

const sprintRef = { repository: 'repo', path: 'sprint/task.json' };
const masterRef = { repository: 'repo', path: 'master/task.json' };
const leafRef = { repository: 'repo', path: 'master/leaf.json' };
export const chatSprint = taskEntry({
  reference: sprintRef,
  id: 'SPRINT',
  title: 'Sprint',
  summary: taskSummary({ kind: 'master', form: 'sprint', orchestrates: ['master'] }),
  parent: undefined,
  children: [taskReferenceKey(masterRef)],
  worktrees: [],
});
export const chatMaster = taskEntry({
  reference: masterRef,
  id: 'MASTER',
  title: 'Master',
  summary: taskSummary({ kind: 'master', form: 'master', orchestrates: [] }),
  parent: taskReferenceKey(sprintRef),
  children: [taskReferenceKey(leafRef)],
  worktrees: [],
});
export const chatLeaf = taskEntry({
  reference: leafRef,
  id: 'LEAF',
  title: 'Leaf',
  summary: taskSummary({ kind: 'subTask', form: 'task', orchestrates: [] }),
  parent: taskReferenceKey(masterRef),
  children: [],
  worktrees: [],
});
