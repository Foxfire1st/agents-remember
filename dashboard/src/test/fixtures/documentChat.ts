import { taskReferenceKey } from "../../data/taskReference";
import { taskEntry, taskSummary } from './wire';

const sprintRef = { repository: 'repo', path: 'sprint/task.json' };
const masterRef = { repository: 'repo', path: 'master/task.json' };
const leafRef = { repository: 'repo', path: 'master/leaf.json' };
const soloRef = { repository: 'repo', path: 'solo/task.json' };
const soloLeafRef = { repository: 'repo', path: 'solo/leaf.json' };
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
export const chatStandaloneMaster = taskEntry({
  reference: soloRef,
  id: 'SOLO',
  title: 'Solo master',
  summary: taskSummary({ kind: 'master', form: 'master', orchestrates: [] }),
  parent: undefined,
  children: [taskReferenceKey(soloLeafRef)],
  worktrees: [],
});
export const chatStandaloneLeaf = taskEntry({
  reference: soloLeafRef,
  id: 'SOLO-LEAF',
  title: 'Solo leaf',
  summary: taskSummary({ kind: 'subTask', form: 'task', orchestrates: [] }),
  parent: taskReferenceKey(soloRef),
  children: [],
  worktrees: [],
});
