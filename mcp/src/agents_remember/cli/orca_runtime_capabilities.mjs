#!/usr/bin/env node
// One narrow, pinned Orca runtime boundary for public-schema operations omitted by the CLI.
// The runtime client owns pairing, compatibility, schemas, and transport; this file does not
// speak WebSocket or touch Orca persistence directly.

import path from 'node:path'
import { realpath } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const input = JSON.parse(await readStdin())
const command = process.argv[2]
const runtimeRoot = await resolveRuntimeRoot()
const runtimeModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'cli', 'runtime-client.js')).href
)
const pairingModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'cli', 'runtime', 'runtime-remote-pairing.js')).href
)
const compatModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'cli', 'runtime', 'remote-runtime-compat-gate.js')).href
)
const remoteRequestModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'shared', 'remote-runtime-client.js')).href
)
const protocolModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'shared', 'protocol-version.js')).href
)
const sharedModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'shared', 'agent-session-option-catalog.js')).href
)
const optionLaunchModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'shared', 'agent-session-option-launch.js')).href
)
const sessionProjectionModule = await import(
  pathToFileURL(path.join(runtimeRoot, 'out', 'shared', 'structured-agent-session-projection.js')).href
)
const RuntimeClient = runtimeModule.RuntimeClient ?? runtimeModule.default?.RuntimeClient
if (typeof RuntimeClient !== 'function') {
  throw new Error('Pinned Orca RuntimeClient export is unavailable.')
}
const client = new RuntimeClient()
const userDataPath = runtimeModule.getDefaultUserDataPath()
const environmentSelector = process.env.ORCA_ENVIRONMENT?.trim() || null
const remotePairing = pairingModule.resolveRemotePairing(
  userDataPath,
  process.env.ORCA_PAIRING_CODE ?? process.env.ORCA_REMOTE_PAIRING ?? null,
  environmentSelector
)
const remoteCompat = new compatModule.RemoteRuntimeCompatGate(userDataPath, environmentSelector)

try {
  if (command === 'catalog') {
    process.stdout.write(JSON.stringify(await readCatalog(input.workspaceSelector, input.agentId)))
  } else if (command === 'workspaces') {
    process.stdout.write(JSON.stringify(await listWorkspaces()))
  } else if (command === 'add-folder') {
    process.stdout.write(JSON.stringify(await addFolder(input.path, input.displayName)))
  } else if (command === 'launch-replay') {
    process.stdout.write(JSON.stringify(await launchReplay(input.request)))
  } else if (command === 'terminal-show') {
    process.stdout.write(JSON.stringify(await call('terminal.show', { terminal: input.handle })))
  } else if (command === 'terminal-status') {
    process.stdout.write(JSON.stringify(await call('terminal.agentStatus', { terminal: input.handle })))
  } else if (command === 'agent-options') {
    process.stdout.write(JSON.stringify(await call('agentSession.options', { sessionId: input.sessionId })))
  } else if (command === 'agent-history') {
    process.stdout.write(JSON.stringify(await readStructuredStatus(input.sessionId)))
  } else if (command === 'option-launch') {
    const resolved = optionLaunchModule.resolveAgentSessionOptionLaunch(
      input.agentId,
      input.sessionOptions ?? {}
    )
    process.stdout.write(JSON.stringify({
      args: resolved.args,
      appliedValues: resolved.appliedValues
    }))
  } else if (command === 'restart-resumable') {
    process.stdout.write(JSON.stringify(await call('agentSession.restartResumable', {})))
  } else if (command === 'restart-continue') {
    process.stdout.write(JSON.stringify(await call('agentSession.restartContinue', {
      sessionIds: [input.sessionId]
    })))
  } else {
    throw Object.assign(new Error('Unsupported Orca runtime boundary operation.'), {
      code: 'unsupported_boundary_operation'
    })
  }
} catch (error) {
  const code = typeof error?.code === 'string' ? error.code : 'orca_runtime_boundary_refused'
  const message = typeof error?.message === 'string' ? error.message : 'Orca refused the operation.'
  const safeMessage = message
    .replace(/orca:\/\/pair\?[^\s]*/gi, '[pairing capability redacted]')
    .replace(/https?:\/\/[^\s#]+#pairing=[^\s]*/gi, '[paired client URL redacted]')
    .slice(0, 1000)
  process.stdout.write(JSON.stringify({ ok: false, error: { code, message: safeMessage } }))
  process.exitCode = 1
}

async function call(method, params) {
  const capabilities = method === 'agent.launchReplay'
    ? [
        protocolModule.AGENT_LAUNCH_RUNTIME_CAPABILITY,
        protocolModule.STRUCTURED_AGENT_SESSION_RUNTIME_CAPABILITY
      ]
    : method.startsWith('agentSession.')
      ? [protocolModule.STRUCTURED_AGENT_SESSION_RUNTIME_CAPABILITY]
      : []
  let response
  if (capabilities.length > 0 && remotePairing) {
    const transport = {
      sendWebSocketRequest: (pairing, requestedMethod, requestedParams, timeoutMs, envelope) =>
        remoteRequestModule.sendRemoteRuntimeRequest(
          pairing,
          requestedMethod,
          requestedParams,
          timeoutMs,
          envelope,
          undefined,
          capabilities
        ),
      sendWebSocketRequestWithStatusPreflight: (
        pairing,
        requestedMethod,
        requestedParams,
        timeoutMs,
        validateStatus,
        envelope
      ) => remoteRequestModule.sendRemoteRuntimeRequestWithStatusPreflight(
        pairing,
        requestedMethod,
        requestedParams,
        timeoutMs,
        validateStatus,
        envelope,
        capabilities
      )
    }
    response = await remoteCompat.send({
      transport,
      pairing: remotePairing,
      method,
      params,
      timeoutMs: 180000
    })
  } else {
    response = await client.call(method, params)
  }
  if (!response || response.ok !== true || typeof response.result !== 'object') {
    if (response?.ok === false && typeof runtimeModule.RuntimeRpcFailureError === 'function') {
      throw new runtimeModule.RuntimeRpcFailureError(response)
    }
    throw new Error(`Orca runtime refused ${method}.`)
  }
  return response.result
}

async function readCatalog(workspaceSelector, selectedAgent) {
  if (typeof workspaceSelector !== 'string' || workspaceSelector.length === 0) {
    throw new Error('The exact Orca workspace selector is required for capability discovery.')
  }
  const agentIds = [...new Set(await call('preflight.detectAgents'))]
    .filter((value) => typeof value === 'string' && value.length > 0)
  const seeded = sharedModule.getAgentSessionOptionCatalog?.(selectedAgent) ?? null
  let models = []
  let catalogOrigin = 'unavailable'
  if (typeof selectedAgent === 'string' && agentIds.includes(selectedAgent)) {
    try {
      const result = await call('git.discoverCommitMessageModels', {
        worktree: workspaceSelector,
        agentId: selectedAgent
      })
      if (result.success === true && Array.isArray(result.models)) {
        catalogOrigin = result.catalogOrigin
        if (result.catalogOrigin === 'probe' && seeded) {
          const discoveredIds = new Set(
            result.models.flatMap((model) => typeof model?.id === 'string' ? [model.id] : [])
          )
          models = combineModels(seeded, result.models, sharedModule)
            .filter((model) => discoveredIds.has(model.id))
        }
      } else {
        catalogOrigin = 'unavailable'
      }
    } catch {
      catalogOrigin = 'unavailable'
    }
  }
  return {
    agents: agentIds.map((id) => ({ id, label: id })),
    selected: typeof selectedAgent === 'string' && agentIds.includes(selectedAgent)
      ? {
        id: selectedAgent,
          catalogOrigin,
          models: models.map((model) => ({
          id: model.id,
          label: model.label,
          ...(model.description ? { description: model.description } : {}),
          ...(model.isDefault ? { isDefault: true } : {}),
          ...modelOptions(model)
        }))
      }
      : null
  }
}

async function readStructuredStatus(sessionId) {
  const history = await call('agentSession.history', {
    sessionId,
    direction: 'tail',
    limit: 20
  })
  const page = history.page
  if (!page || !Array.isArray(page.items)) {
    throw new Error('Orca did not return the native agent history page.')
  }
  const summary = sessionProjectionModule.projectStructuredAgentSessionStatusSummary(
    page.items,
    Array.isArray(page.submissions) ? page.submissions : [],
    page.fence
  )
  const turns = page.items.flatMap((item) => {
    if (item?.body?.kind === 'turn') return [item.body]
    if (item?.body?.turnLifecycle && typeof item.body.turnLifecycle === 'object') {
      return [item.body.turnLifecycle]
    }
    return []
  })
  const lastTurn = turns.at(-1)
  return {
    status: summary.status,
    ...(typeof lastTurn?.state === 'string' ? { turnState: lastTurn.state } : {}),
    ...(typeof lastTurn?.outcome === 'string' ? { turnOutcome: lastTurn.outcome } : {}),
    ...(typeof summary.lastAssistantMessage === 'string'
      ? { lastAssistantMessage: summary.lastAssistantMessage.slice(0, 3000) }
      : {})
  }
}

function modelOptions(model) {
  const effort = (model.options ?? []).find(
    (option) => option.id === 'effort' && option.kind?.type === 'select'
  )
  if (!effort) return { efforts: [] }
  return {
    efforts: effort.kind.choices.map((choice) => ({ id: choice.value, label: choice.label })),
    ...(typeof effort.kind.defaultValue === 'string'
      ? { defaultEffort: effort.kind.defaultValue }
      : {})
  }
}

async function listWorkspaces() {
  const result = await call('worktree.list', { limit: 10000 })
  const worktrees = Array.isArray(result.worktrees) ? result.worktrees : []
  return {
    worktrees: worktrees.flatMap((entry) => {
      if (!entry || typeof entry.id !== 'string' || typeof entry.path !== 'string') return []
      return [{
        id: entry.id,
        path: entry.path,
        repoId: typeof entry.repoId === 'string' ? entry.repoId : null,
        displayName: typeof entry.displayName === 'string' ? entry.displayName : null
      }]
    })
  }
}

function combineModels(catalog, discoveredRows, shared) {
  const byId = new Map(
    discoveredRows
      .filter((model) => model && typeof model.id === 'string')
      .map((model) => [model.id, model])
  )
  const seededModels = catalog.models.map((seed) => {
    const live = byId.get(seed.id)
    return live
      ? { ...seed, ...live, options: projectThinkingLevels(seed.options ?? [], live, shared) }
      : seed
  })
  const fallbackOptions = catalog.unknownModelOptions ?? []
  const discovered = discoveredRows
    .filter((model) => model && typeof model.id === 'string' && typeof model.label === 'string')
    .map((model) => {
      const seeded = shared.findCatalogModel(catalog, model.id)
      return {
        id: model.id,
        label: model.label,
        ...(typeof model.description === 'string' ? { description: model.description } : {}),
        ...(model.isDefault === true ? { isDefault: true } : {}),
        options: projectThinkingLevels(seeded?.options ?? fallbackOptions, model, shared)
      }
    })
  if (catalog.discoveredModelsAreAuthoritative === true) {
    return shared.mergeDiscoveredAuthoritativeModels(seededModels, discovered)
  }
  return shared.mergeCatalogModels(seededModels, discovered)
}

function projectThinkingLevels(options, model, shared) {
  const levels = Array.isArray(model.thinkingLevels)
    ? model.thinkingLevels.filter(
      (level) => typeof level?.id === 'string' && typeof level?.label === 'string'
    )
    : []
  const effort = shared.findCatalogOption({ options }, 'effort')
  if (!levels.length || effort?.kind?.type !== 'select') return options

  const choices = levels.map((level) => ({ value: level.id, label: level.label }))
  const defaults = [model.defaultThinkingLevel, effort.kind.defaultValue]
  const defaultValue = defaults.find(
    (value) => typeof value === 'string' && choices.some((choice) => choice.value === value)
  )
  const kindWithoutDefault = { ...effort.kind }
  delete kindWithoutDefault.defaultValue
  return options.map((option) => (
    option.id === effort.id
      ? {
        ...option,
        kind: {
          ...kindWithoutDefault,
          choices,
          ...(typeof defaultValue === 'string' ? { defaultValue } : {})
        }
      }
      : option
  ))
}

async function addFolder(folderPath, displayName) {
  if (typeof folderPath !== 'string' || !path.isAbsolute(folderPath)) {
    throw new Error('Folder registration requires an absolute path.')
  }
  return await call('repo.add', {
    path: folderPath,
    kind: 'folder',
    ...(typeof displayName === 'string' && displayName.length > 0 ? { displayName } : {})
  })
}

async function launchReplay(request) {
  if (!request || typeof request !== 'object') {
    throw new Error('A bounded agent.launchReplay request is required.')
  }
  return await call('agent.launchReplay', request)
}

async function resolveRuntimeRoot() {
  const configured = process.env.AR_ORCA_RUNTIME_ROOT?.trim()
  if (configured) return await realpath(configured)
  const cliPath = process.env.AR_ORCA_CLI?.trim()
  if (!cliPath) throw new Error('Set AR_ORCA_CLI or AR_ORCA_RUNTIME_ROOT for the pinned Orca runtime boundary.')
  const realCli = await realpath(cliPath)
  const expectedSuffix = path.join('out', 'cli', 'index.js')
  if (!realCli.endsWith(expectedSuffix)) {
    throw new Error('AR_ORCA_CLI is not the pinned source CLI layout; set AR_ORCA_RUNTIME_ROOT explicitly.')
  }
  return path.dirname(path.dirname(path.dirname(realCli)))
}

async function readStdin() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  const raw = Buffer.concat(chunks).toString('utf8')
  return raw.length > 0 ? raw : '{}'
}
