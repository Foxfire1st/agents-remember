// Task workspace identity and placement. Archived descriptors are never reused or mutated.
import { createHash } from 'node:crypto'
import path from 'node:path'
import { failure, nonEmpty, requiredText, serverIdOf } from './paseo_bridge_primitives.mjs'

// At most eight archived generations, then one live candidate. No history is stored locally;
// the host owns workspace retention and its idempotency ledger.
const MAX_ARCHIVED_WORKSPACES = 8

export async function openWorkspace({ api, daemon }, input) {
  const cwd = requiredText(input, 'cwd')
  let workspace
  let preparation
  if (!('masterProject' in input) && !('task' in input)) {
    workspace = (await api.workspaces.open(cwd)).current()
    preparation = 'opened'
  } else {
    const placement = taskPlacement(input)
    const project = (await daemon.addProject(placement.directory)).project
    if (!nonEmpty(project?.projectId)) {
      throw failure('paseo_bridge_invalid_reply', 'The Paseo runtime returned a project without an id.')
    }
    await daemon.renameProject(project.projectId, placement.name)
    ;({ workspace, preparation } = await openTaskWorkspace(api, cwd, project.projectId, placement))
  }
  if (!nonEmpty(workspace?.id) || !nonEmpty(workspace.workspaceDirectory)) {
    throw failure(
      'paseo_bridge_invalid_reply',
      'The Paseo runtime returned a workspace without an id and a directory.'
    )
  }
  return {
    serverId: serverIdOf(daemon),
    ...(preparation ? { preparation } : {}),
    workspace: {
      id: workspace.id,
      directory: workspace.workspaceDirectory,
      name: workspace.name ?? null,
      projectId: workspace.projectId ?? null,
      projectKind: workspace.projectKind ?? null
    }
  }
}

function taskPlacement({ masterProject, task }) {
  if (!masterProject || typeof masterProject !== 'object' || Array.isArray(masterProject) ||
      !task || typeof task !== 'object' || Array.isArray(task)) {
    throw failure('invalid_bridge_payload', 'Master/task workspace placement must be complete.')
  }
  return {
    directory: requiredText(masterProject, 'directory'),
    masterKey: requiredText(masterProject, 'key'),
    name: requiredText(masterProject, 'name'),
    taskKey: requiredText(task, 'key'),
    title: requiredText(task, 'title')
  }
}

function workspaceKey(parts) {
  return 'ar-task-workspace:v2:' + createHash('sha256').update(JSON.stringify(parts)).digest('hex')
}

async function openTaskWorkspace(api, cwd, projectId, placement) {
  let idempotencyKey = workspaceKey([placement.masterKey, placement.taskKey])
  for (let archived = 0; archived <= MAX_ARCHIVED_WORKSPACES; archived += 1) {
    let preparation = null
    const handle = await api.workspaces.create({
      source: { kind: 'directory', path: cwd, projectId }, idempotencyKey,
      onEvent(snapshot) {
        if (preparation === null) preparation = snapshot.workspace ? 'found' : 'created'
      }
    })
    // Completed creation replay validates existence, but carries an old live descriptor.
    // The host omits archived workspaces from refresh's live-only listing. Keep the replay
    // identity before refresh clears it; use it only to select a successor, never as live.
    const replay = handle.current()
    const workspace = await handle.refresh()
    const descriptor = workspace ?? (preparation === 'found' ? replay : null)
    if (!descriptor || !nonEmpty(descriptor.id) || descriptor.projectId !== projectId ||
        !nonEmpty(descriptor.workspaceDirectory) || path.resolve(descriptor.workspaceDirectory) !== path.resolve(cwd)) {
      throw failure('paseo_call_failed', 'The Paseo task workspace is unavailable or has different placement.')
    }
    if (workspace && !workspace.archivingAt && !workspace.archivedAt) {
      const named = await handle.setTitle(placement.title)
      return { workspace: { ...workspace, name: named.title }, preparation }
    }
    if (archived === MAX_ARCHIVED_WORKSPACES) {
      throw failure('paseo_call_failed',
        `The Paseo task ${placement.taskKey} has ${archived + 1} archived workspaces; ` +
        `the successor limit is ${MAX_ARCHIVED_WORKSPACES}. No further workspace was created. ` +
        'Ask the host operator to inspect the archived workspace chain before starting again.')
    }
    // Chaining the previous key and immutable archived id converges across retries and callers.
    idempotencyKey = workspaceKey([idempotencyKey, descriptor.id])
  }
}
