#!/usr/bin/env node
// The one bridge between Agents Remember and the Paseo runtime's agent, workspace and catalog
// functions (PNT-R02). paseo_bridge.py runs `node paseo_bridge.mjs <command>` with the JSON payload
// on standard input and reads one JSON object from standard output: the command's result with
// exit status 0, or `{ok: false, error: {code, message}}` with exit status 1.
//
// Commands. Each is one function in the "Commands" section and one row in COMMANDS; adding a
// command adds exactly that. A command receives `{api, daemon, deadline}` and its payload, and
// returns a JSON object. Connecting, the deadline, closing and error mapping are shared and live
// in `main`, `connect` and `failure`.
//
//   catalog  {refresh?: boolean}
//            -> {runtime: {serverId, version},
//                providers: [{id, label, models: [{id, label, description?, isDefault?,
//                             efforts: [{id, label}], defaultEffort?}], listingError?,
//                             acceptsToolServers?: false}]}
//            The providers the runtime reports as ready and enabled, each with the models and
//            thinking options the runtime lists for it. `refresh` asks the runtime to rediscover
//            its providers first. A provider whose model listing fails keeps its row, with no
//            models and the runtime's error text; so does a provider the runtime is still
//            discovering when the discovery time is up, provided another provider is ready.
//            `acceptsToolServers: false` marks a provider whose entry in the runtime's
//            configuration declares that it takes no tool servers from its host
//            (`options.supportsMcpServers: false`); the runtime refuses to create an agent with
//            tool servers for such a provider.
//
//   workspace-open  {cwd: string}
//            -> {serverId, workspace: {id, directory, name, projectId, projectKind}}
//            The runtime's workspace for the directory: the existing one is reused, otherwise the
//            runtime creates it. The runtime keys a workspace by the path text, so the caller
//            passes a resolved path.
//
//   agent-create  {agentId, idempotencyKey, workspaceId, provider, model?, thinkingOptionId?,
//                  title, labels, prompt?, systemPrompt?, parentAgentId?,
//                  mcpServers?: {<name>: {type: 'stdio', command, args, env}}}
//            -> {serverId, existing: boolean, agent: AGENT, creationError?}
//            One agent under the caller's agent id (a UUID) in that workspace's directory, with
//            the provider's default permission mode. An agent that already has the id is returned
//            with `existing: true` and nothing is created; so is one that exists although the
//            creation answered with an error (`creationError` carries the runtime's text). A
//            creation the runtime refuses, with no agent under the id afterwards, fails with
//            `paseo_call_failed`. Without `model` the creation goes through the daemon client
//            (below), because the public client requires a provider/model pair; the provider
//            then applies its own default model. `prompt` is the first message; without it the
//            agent is created idle. `systemPrompt` and `mcpServers` are stored by the runtime
//            with the agent and applied again whenever it resumes the agent's session: the text
//            is added to the provider's system-level instructions where the provider has such,
//            and each tool server is started for the agent with exactly the given command and
//            environment. `parentAgentId` names the agent that starts this one: the runtime
//            records it as the new agent's parent (the label `paseo.parent-agent-id`, which
//            AGENT's `labels` shows) and refuses the creation when it has no such agent loaded.
//            The agent still runs in `workspaceId`, not in the parent's workspace.
//
//   agent-get  {agentId: string}
//            -> {serverId, agent: AGENT | null}
//            The agent with exactly that id, archived or not; null when the runtime has none.
//
//   agent-archive  {agentId: string}
//            -> {serverId, agentId, found: boolean, archived: boolean, alreadyArchived?: boolean,
//                archivedAt?: string}
//            Archives a live agent. An agent that is already archived, or that the runtime does
//            not have, is not an error.
//
//   AGENT is {id, provider, model, thinkingOptionId, title, labels, workspaceId, cwd, status,
//   archivedAt, createdAt}: what the runtime reports as applied, `thinkingOptionId` being the
//   effective one.
//
//   agent-state  {agentId: string}
//            -> {serverId, agent: STATE | null}
//            What the runtime reports about the agent with exactly that id, archived or not; null
//            when it has none. A read with no effect on the agent: nothing is sent, resumed or
//            un-archived. The runtime keeps no outcome of the last turn, so that is taken from the
//            end of the agent's timeline, and the timeline is read only while the agent is idle
//            with an open session: reading the timeline of a closed session makes the runtime
//            resume it.
//
//   agent-resume  {agentId: string}
//            -> {serverId, agent: STATE | null, resume: {attempted: boolean, resumed: boolean,
//                error?: string}}
//            Opens the closed session of a live agent as the same agent, without a message. The
//            runtime loads an agent when its timeline is read; that read is the resume. An agent
//            that is missing, archived or already open is left alone (`attempted: false`), and
//            nothing is ever created or un-archived. A resume the runtime refuses is
//            `resumed: false` with the runtime's text in `error`. `agent` is the state afterwards.
//
//   STATE is {id, status, archivedAt, turnActive, pendingPermissions: [{name, kind}], lastError,
//   lastTurn}. `status` is the runtime's own word (initializing, idle, running, error, closed).
//   `lastTurn` is null unless the agent is idle with an open session; then it is
//   {state: 'none'} when the agent has not run a turn, {state: 'replied', text} when the timeline
//   ends with the agent's reply (at most 3,000 characters of it), and {state: 'unreplied'} when
//   the last turn ended without one, which is what a cancelled turn leaves behind.
//
//   agent-send  {agentId: string, text: string, messageId: string}
//            -> {serverId, delivery: {delivered: true, taken: 'started' | 'steered' | 'replaced',
//                                     turnId: string | null}
//                        | {delivered: false, refused: 'not-found' | 'archived' | 'closed' | 'busy',
//                           detail: string}}
//            One message to the agent with exactly that id, for a live agent with an open
//            session only: a missing, archived or closed agent is refused and left as it is,
//            because the runtime would un-archive or resume it to deliver. An idle agent starts a
//            turn with the message (`started`). An agent that is mid-turn is sent the message
//            with the runtime's steer behaviour, which hands it to the running turn (`steered`).
//            The runtime has no "steer or refuse": for a provider without steering it cancels
//            the running turn and starts another, and it reports which providers those are only
//            through their entry in its configuration (they extend its ACP driver). Such a
//            recipient, and one that is still initializing, is refused as `busy`. `taken` says
//            what the runtime did, read from the turn the agent runs before and after the send;
//            `replaced` means the runtime cancelled the running turn all the same. `turnId` is
//            the turn that took the message, when it is still running. `messageId` is the
//            caller's id of the message; the runtime records it with the message in the timeline.
//
//   agent-wait  {agentId: string, turnId?: string, waitMs?: number}
//            -> {serverId, wait: {state: 'running'}
//                              | {state: 'permission', permission: string}
//                              | {state: 'ended', outcome: 'finished' | 'failed' | 'cancelled',
//                                 text: string | null, textTruncated: boolean, error?: string}
//                              | {state: 'unavailable', reason: 'not-found' | 'archived' | 'closed'}}
//            Waits, for at most `waitMs` and never past this call's own deadline, until the turn
//            that took a message (`turnId`, as `agent-send` returned it) ends or the agent asks
//            for a permission. `running` means the time was up first; the caller repeats the
//            call. The outcome is the runtime's own turn event when the turn ends during this
//            call. A turn that ended before the call has left no such event; then it is `failed`
//            when the agent is in an error state, `finished` when the timeline ends with the
//            agent's reply, and `cancelled` otherwise. `text` is that reply, the agent's text
//            behind the last message or tool call (at most 20,000 characters). Nothing is sent,
//            resumed or un-archived.
//
// The runtime is addressed by the environment paseo_bridge.py derives from `paseoRuntime`:
//   AR_PASEO_INSTALL_PREFIX  install prefix; the client package is loaded from its node_modules
//   AR_PASEO_URL             ws://<listen>/ws
//   AR_PASEO_SERVER_ID       server id of the configured daemon home; any other daemon is refused
//   AR_PASEO_VERSION         the pinned version, sent as the client's app version
//   AR_PASEO_DEADLINE_MS     budget of this call; the script answers before the caller's hard stop
//
// Non-public Paseo entry points used here, with the reason (PNT-R02 item 2):
//   @getpaseo/client/internal/daemon-client  DaemonClient
//     The public createPaseoClient() builds this client and hides it. The bridge constructs it
//     itself and wraps it in the public createPaseoApi(), so that it can read the daemon's
//     server_info (getLastServerInfoMessage) and refuse a daemon that is not the configured one.
//     Every call a command makes goes through the public `api` unless a row above says otherwise.
//     `agent-create` is such a row: without a model it calls this client's createAgent(), because
//     the public client only creates an agent for a provider/model pair.

import { realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const COMMANDS = {
  catalog: readCatalog,
  'workspace-open': openWorkspace,
  'agent-create': createAgent,
  'agent-get': getAgent,
  'agent-archive': archiveAgent,
  'agent-state': readAgentState,
  'agent-resume': resumeAgent,
  'agent-send': sendToAgent,
  'agent-wait': waitForAgent
}

const DEFAULT_DEADLINE_MS = 55000
const CONNECT_TIMEOUT_MS = 10000
const CLOSE_TIMEOUT_MS = 1000
// Time kept back from the deadline (at most this much, and at most the given share of what is
// left) so that a slow provider listing becomes that provider's listingError instead of a timeout
// of the whole call, and so that the reply is written before the deadline.
const LISTING_RESERVE_MS = 10000
const REPLY_RESERVE_MS = 2000
const MESSAGE_LIMIT = 800
// agent-state and agent-resume: the runtime's word for an agent without a running harness
// process, the timeline entries that mark the end of a turn, how many entries are read from the
// end, and the length of the reply text that is returned.
const CLOSED_SESSION = 'closed'
const TURN_ITEMS = new Set(['user_message', 'assistant_message', 'tool_call'])
const TIMELINE_TAIL = 20
const FINAL_TEXT_LIMIT = 3000
// agent-send and agent-wait: the provider entries of the runtime's configuration that cannot
// take a message into a running turn, the default and the reserve of one wait, the timeline
// entries read for the reply, the length of the reply that is returned, and the runtime's
// events that end a turn.
const DRIVER_WITHOUT_STEERING = 'acp'
const WAIT_DEFAULT_MS = 30000
const WAIT_RESERVE_MS = 6000
const REPLY_TAIL = 200
const REPLY_TEXT_LIMIT = 20000
const TURN_ENDS = { turn_completed: 'finished', turn_failed: 'failed', turn_canceled: 'cancelled' }
// Standard output carries the reply and nothing else: the client's log lines, and anything a
// command or a package prints through the console, go to standard error.
const STDERR_LOGGER = { debug() {}, info: logToStderr, warn: logToStderr, error: logToStderr }
const STILL_LOADING = Symbol('providers still loading')
console.log = console.info = console.debug = console.error

await main()

async function main() {
  const command = process.argv[2]
  const deadline = Date.now() + positiveInteger(process.env.AR_PASEO_DEADLINE_MS, DEFAULT_DEADLINE_MS)
  const connection = { current: null }
  try {
    if (typeof command !== 'string' || !Object.hasOwn(COMMANDS, command)) {
      throw failure(
        'unsupported_bridge_command',
        `The Paseo bridge has no ${JSON.stringify(command ?? '')} command.`
      )
    }
    const result = await within(
      (async () => {
        const payload = await readPayload()
        connection.current = await connect(deadline)
        return await COMMANDS[command]({ ...connection.current, deadline }, payload)
      })(),
      deadline,
      () => failure('paseo_bridge_timeout', `The Paseo bridge call ${command} ran out of time.`)
    )
    await finish(0, result, connection.current)
  } catch (error) {
    await finish(1, { ok: false, error: describe(error, connection.current) }, connection.current)
  }
}

// ---------------------------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------------------------

async function readCatalog({ api, daemon, deadline }, input) {
  const remaining = deadline - Date.now()
  const discoveryDeadline = deadline - Math.min(LISTING_RESERVE_MS, remaining / 5)
  const listingDeadline = deadline - Math.min(REPLY_RESERVE_MS, remaining / 20)
  const discoveryTimeout = () =>
    failure('paseo_bridge_timeout', 'The Paseo runtime did not finish provider discovery in time.')
  if (input.refresh === true) await within(api.providers.refresh(), discoveryDeadline, discoveryTimeout)
  // waitForReady answers once no provider is loading. When the discovery time is up first, the
  // providers that are ready are still returned, as long as there is one.
  const settled = await within(
    api.providers.waitForReady({ timeoutMs: Math.max(1, deadline - Date.now()) }),
    discoveryDeadline,
    () => STILL_LOADING
  ).catch((error) => {
    if (error !== STILL_LOADING) throw error
    return null
  })
  const snapshot = settled ?? (await api.providers.snapshot())
  const enabled = (Array.isArray(snapshot?.entries) ? snapshot.entries : []).filter(
    (entry) => typeof entry?.provider === 'string' && entry.enabled === true
  )
  const ready = enabled.some((entry) => entry.status === 'ready')
  if (!ready && enabled.some((entry) => entry.status === 'loading')) throw discoveryTimeout()
  const providers = await Promise.all(
    enabled
      .filter((entry) => entry.status === 'ready' || entry.status === 'loading')
      .map((entry) =>
        entry.status === 'ready' ? listProvider(api, entry, listingDeadline) : stillLoading(entry)
      )
  )
  if (daemon.getConnectionState().status !== 'connected') {
    throw failure('paseo_daemon_unreachable', 'The connection to the Paseo daemon was lost while reading the catalog.')
  }
  // What the runtime reports about tool servers before an agent exists is the provider's entry
  // in its configuration.
  const entries = (await api.config?.get())?.config?.providers ?? {}
  const info = daemon.getLastServerInfoMessage()
  return {
    runtime: { serverId: info.serverId, version: typeof info.version === 'string' ? info.version : null },
    providers: providers.map((row) =>
      entries[row.id]?.options?.supportsMcpServers === false ? { ...row, acceptsToolServers: false } : row
    )
  }
}

function stillLoading(entry) {
  return {
    id: entry.provider,
    label: nonEmpty(entry.label) ?? entry.provider,
    models: [],
    listingError: 'the runtime was still listing the models of this provider when the call ended'
  }
}

async function listProvider(api, entry, deadline) {
  const row = { id: entry.provider, label: nonEmpty(entry.label) ?? entry.provider }
  try {
    const listing = await within(
      api.providers.listModels(entry.provider),
      deadline,
      () => new Error('the runtime did not list the models in time')
    )
    if (listing?.error) return { ...row, models: [], listingError: text(listing.error) }
    const models = Array.isArray(listing?.models) ? listing.models : []
    return { ...row, models: models.filter((model) => nonEmpty(model?.id)).map(projectModel) }
  } catch (error) {
    return { ...row, models: [], listingError: text(error?.message ?? error) }
  }
}

function projectModel(model) {
  const options = Array.isArray(model.thinkingOptions) ? model.thinkingOptions : []
  return {
    id: model.id,
    label: nonEmpty(model.label) ?? model.id,
    ...(nonEmpty(model.description) ? { description: model.description } : {}),
    ...(model.isDefault === true ? { isDefault: true } : {}),
    efforts: options
      .filter((option) => nonEmpty(option?.id))
      .map((option) => ({ id: option.id, label: nonEmpty(option.label) ?? option.id })),
    ...(nonEmpty(model.defaultThinkingOptionId) ? { defaultEffort: model.defaultThinkingOptionId } : {})
  }
}

async function openWorkspace({ api, daemon }, input) {
  const workspace = (await api.workspaces.open(requiredText(input, 'cwd'))).current()
  if (!nonEmpty(workspace?.id) || !nonEmpty(workspace.workspaceDirectory)) {
    throw failure(
      'paseo_bridge_invalid_reply',
      'The Paseo runtime returned a workspace without an id and a directory.'
    )
  }
  return {
    serverId: serverIdOf(daemon),
    workspace: {
      id: workspace.id,
      directory: workspace.workspaceDirectory,
      name: workspace.name ?? null,
      projectId: workspace.projectId ?? null,
      projectKind: workspace.projectKind ?? null
    }
  }
}

async function createAgent({ api, daemon }, input) {
  const agentId = requiredText(input, 'agentId')
  const request = agentCreation(input, agentId)
  const present = await findAgent(api, daemon, agentId)
  if (present) return { serverId: serverIdOf(daemon), existing: true, agent: projectAgent(present) }
  let created
  try {
    created = await request.create(api, daemon)
  } catch (error) {
    // The runtime answered with an error. Whether an agent exists under the id decides what that
    // means: it exists (a repeat that lost its creation record, or a first prompt that failed
    // after the agent was created), or the runtime refused the creation.
    if (daemon.getConnectionState().status !== 'connected') throw error
    const after = await findAgent(api, daemon, agentId)
    if (!after) throw error
    return {
      serverId: serverIdOf(daemon),
      existing: true,
      agent: projectAgent(after),
      creationError: text(error?.message ?? error)
    }
  }
  if (created?.id !== agentId) {
    throw failure(
      'paseo_bridge_invalid_reply',
      'The Paseo runtime created an agent under another id than the one given.'
    )
  }
  return { serverId: serverIdOf(daemon), existing: false, agent: projectAgent(created) }
}

// Check the payload and return how the agent is created: through the public client, or, for a
// provider launched without a model, through the daemon client with the same request.
function agentCreation(input, agentId) {
  const workspaceId = requiredText(input, 'workspaceId')
  const provider = requiredText(input, 'provider')
  const model = optionalText(input, 'model')
  const thinkingOptionId = optionalText(input, 'thinkingOptionId')
  const prompt = optionalText(input, 'prompt')
  const shared = {
    agentId,
    idempotencyKey: requiredText(input, 'idempotencyKey'),
    labels: requiredLabels(input)
  }
  const title = requiredText(input, 'title')
  const parentAgentId = optionalText(input, 'parentAgentId')
  const systemPrompt = optionalText(input, 'systemPrompt')
  const mcpServers = input.mcpServers ?? null
  const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
  const wellFormed =
    mcpServers === null ||
    (isRecord(mcpServers) &&
      Object.values(mcpServers).every(
        (server) =>
          isRecord(server) &&
          server.type === 'stdio' &&
          nonEmpty(server.command) &&
          Array.isArray(server.args) &&
          server.args.every((argument) => typeof argument === 'string') &&
          isRecord(server.env) &&
          Object.values(server.env).every((value) => typeof value === 'string')
      ))
  if (!wellFormed) {
    throw failure(
      'invalid_bridge_payload',
      'The Paseo bridge payload needs mcpServers as stdio definitions with command, args and env.'
    )
  }
  // What the runtime keeps with the agent and applies again on every resume of its session.
  const kept = {
    ...(thinkingOptionId ? { thinkingOptionId } : {}),
    ...(systemPrompt ? { systemPrompt } : {}),
    ...(mcpServers ? { mcpServers } : {})
  }
  return {
    async create(api, daemon) {
      const workspace = api.workspaces.ref(workspaceId)
      if (model) {
        const handle = await workspace.agents.create({
          ...shared,
          config: { provider: `${provider}/${model}`, ...kept },
          title,
          ...(parentAgentId ? { parent: parentAgentId } : {}),
          ...(prompt ? { prompt } : {})
        })
        return handle.current()
      }
      const cwd = (await workspace.refresh())?.workspaceDirectory
      if (!nonEmpty(cwd)) throw new Error(`Workspace ${workspaceId} has no available directory`)
      return await daemon.createAgent({
        ...shared,
        config: { provider, cwd, title, ...kept },
        workspaceId,
        ...(parentAgentId ? { callerAgentId: parentAgentId } : {}),
        ...(prompt ? { initialPrompt: prompt } : {})
      })
    }
  }
}

async function getAgent({ api, daemon }, input) {
  const agent = await findAgent(api, daemon, requiredText(input, 'agentId'))
  return { serverId: serverIdOf(daemon), agent: agent ? projectAgent(agent) : null }
}

async function archiveAgent({ api, daemon }, input) {
  const agentId = requiredText(input, 'agentId')
  const serverId = serverIdOf(daemon)
  const agent = await findAgent(api, daemon, agentId)
  if (!agent) return { serverId, agentId, found: false, archived: false }
  const archived = { serverId, agentId, found: true, archived: true }
  if (nonEmpty(agent.archivedAt)) {
    return { ...archived, alreadyArchived: true, archivedAt: agent.archivedAt }
  }
  let result
  try {
    result = await api.agents.ref(agentId).archive()
  } catch (error) {
    // Another caller may have archived or removed the agent between the lookup and this call.
    // What the runtime holds now decides: archived or gone is not an error; anything else is.
    if (daemon.getConnectionState().status !== 'connected') throw error
    const after = await findAgent(api, daemon, agentId)
    if (!after) return { serverId, agentId, found: false, archived: false }
    if (!nonEmpty(after.archivedAt)) throw error
    return { ...archived, alreadyArchived: true, archivedAt: after.archivedAt }
  }
  return { ...archived, alreadyArchived: false, archivedAt: result.archivedAt }
}

async function readAgentState({ api, daemon }, input) {
  const agentId = requiredText(input, 'agentId')
  const agent = await findAgent(api, daemon, agentId)
  return { serverId: serverIdOf(daemon), agent: agent ? await agentState(api, agent) : null }
}

async function resumeAgent({ api, daemon }, input) {
  const agentId = requiredText(input, 'agentId')
  const serverId = serverIdOf(daemon)
  const agent = await findAgent(api, daemon, agentId)
  const untouched = { attempted: false, resumed: false }
  if (!agent) return { serverId, agent: null, resume: untouched }
  if (nonEmpty(agent.archivedAt) || agent.status !== CLOSED_SESSION) {
    // Reading the timeline of an archived agent would load it, and an open session needs nothing.
    return { serverId, agent: await agentState(api, agent), resume: untouched }
  }
  let refusal = null
  try {
    await api.agents.ref(agentId).timeline.refetch({ direction: 'tail', limit: 1 })
  } catch (error) {
    if (daemon.getConnectionState().status !== 'connected') throw error
    refusal = text(error?.message ?? error) || 'The Paseo runtime did not resume the agent.'
  }
  const after = await findAgent(api, daemon, agentId)
  return {
    serverId,
    agent: after ? await agentState(api, after) : null,
    resume: {
      attempted: true,
      resumed: Boolean(after) && after.status !== CLOSED_SESSION,
      ...(refusal ? { error: refusal } : {})
    }
  }
}

// What the runtime says about one agent, plus how its last turn ended when that can be read
// without loading the agent: only an idle agent has an open session and no turn in progress.
async function agentState(api, agent) {
  const permissions = Array.isArray(agent.pendingPermissions) ? agent.pendingPermissions : []
  const readable = agent.status === 'idle' && !nonEmpty(agent.archivedAt)
  return {
    id: agent.id,
    status: agent.status ?? null,
    archivedAt: agent.archivedAt ?? null,
    turnActive: Boolean(agent.activeTurn),
    pendingPermissions: permissions.map((request) => ({
      name: nonEmpty(request?.name) ?? nonEmpty(request?.title) ?? 'a tool',
      kind: request?.kind ?? null
    })),
    lastError: nonEmpty(agent.lastError) ? text(agent.lastError) : null,
    lastTurn: readable ? await lastTurn(api, agent) : null
  }
}

// How the last turn ended, from the end of the timeline. A turn that finished ends with the
// agent's reply. The runtime records no cancellation; a cancelled turn leaves a user message or a
// tool call as the last entry instead. Entries of other kinds (reasoning, to-do lists, compaction)
// say nothing about the end of a turn and are skipped.
async function lastTurn(api, agent) {
  const page = await api.agents
    .ref(agent.id)
    .timeline.refetch({ direction: 'tail', limit: TIMELINE_TAIL, projection: 'projected' })
  const kinds = (Array.isArray(page?.entries) ? page.entries : [])
    .map((entry) => entry?.item)
    .filter((item) => TURN_ITEMS.has(item?.type))
  if (kinds.length === 0) {
    const ran = nonEmpty(agent.lastUserMessageAt) || page?.hasOlder === true
    return { state: ran ? 'unreplied' : 'none' }
  }
  let first = kinds.length
  while (first > 0 && kinds[first - 1].type === 'assistant_message') first -= 1
  const reply = kinds
    .slice(first)
    .map((item) => (typeof item.text === 'string' ? item.text : ''))
    .join('')
  if (!reply.trim()) return { state: 'unreplied' }
  // Cut by code points: twice the limit in UTF-16 units always holds the first 3,000 of them.
  const head = Array.from(reply.slice(0, 2 * FINAL_TEXT_LIMIT)).slice(0, FINAL_TEXT_LIMIT)
  return { state: 'replied', text: head.join('') }
}

async function sendToAgent({ api, daemon }, input) {
  const agentId = requiredText(input, 'agentId')
  const message = requiredText(input, 'text')
  const messageId = requiredText(input, 'messageId')
  const serverId = serverIdOf(daemon)
  const refused = (reason, detail) => ({ serverId, delivery: { delivered: false, refused: reason, detail } })
  const before = await findAgent(api, daemon, agentId)
  if (!before) return refused('not-found', 'the host has no such agent')
  if (nonEmpty(before.archivedAt)) return refused('archived', 'the agent is archived')
  if (before.status === CLOSED_SESSION) return refused('closed', 'the session of the agent is closed')
  if (before.status === 'initializing') return refused('busy', 'the agent is still starting')
  const running = turnOf(before)
  if (running.active && !(await providerSteers(api, before.provider))) {
    return refused(
      'busy',
      'the agent is mid-turn and the host cannot hand a message to a running turn of this provider'
    )
  }
  // Always with the steer behaviour: should a turn begin between the lookup and the send, the
  // runtime's default would cancel it.
  await api.agents.ref(agentId).send(message, { messageId, activeTurnBehavior: 'steer' })
  const after = turnOf((await findAgent(api, daemon, agentId)) ?? {})
  const replaced = running.active && after.id !== null && running.id !== null && after.id !== running.id
  return {
    serverId,
    delivery: {
      delivered: true,
      taken: !running.active ? 'started' : replaced ? 'replaced' : 'steered',
      turnId: after.id
    }
  }
}

async function waitForAgent({ api, daemon, deadline }, input) {
  const agentId = requiredText(input, 'agentId')
  const turnId = optionalText(input, 'turnId')
  const serverId = serverIdOf(daemon)
  const budget = Math.min(
    positiveInteger(input.waitMs, WAIT_DEFAULT_MS),
    Math.max(0, deadline - Date.now() - WAIT_RESERVE_MS)
  )
  let agent = await findAgent(api, daemon, agentId)
  const unavailable = waitUnavailable(agent)
  if (unavailable) return { serverId, wait: unavailable }
  let ended = null
  if (awaited(agent, turnId) && pendingPermission(agent) === null && budget > 0) {
    // The runtime's turn events are live only, so they are followed for as long as this call
    // waits; whichever comes first ends the wait: the event that ends the turn, the runtime's
    // own answer that the agent finished or asks for a permission, or the time.
    const handle = api.agents.ref(agentId)
    let turnEnded
    let following = true
    const turnEnd = new Promise((resolve) => (turnEnded = resolve))
    const unsubscribe = handle.timeline.subscribe((update) => {
      const event = update?.event
      const outcome = TURN_ENDS[event?.type]
      if (!following || !outcome) return
      if (turnId && nonEmpty(event.turnId) && event.turnId !== turnId) return
      ended = { outcome, ...(nonEmpty(event.error) ? { error: text(event.error) } : {}) }
      turnEnded()
    })
    try {
      // Without the events the outcome is taken from the agent's state afterwards.
      await unsubscribe.ready.catch(() => {})
      // The turn may have ended while the events were being subscribed to.
      agent = await findAgent(api, daemon, agentId)
      if (agent && awaited(agent, turnId) && pendingPermission(agent) === null) {
        // The runtime's wait is left behind when the turn's own event comes first.
        await Promise.race([turnEnd, handle.waitForFinish(budget).catch(() => null)])
      }
    } finally {
      following = false
      unsubscribe()
    }
    agent = await findAgent(api, daemon, agentId)
    const gone = waitUnavailable(agent)
    if (gone) return { serverId, wait: gone }
  }
  if (ended === null) {
    const permission = pendingPermission(agent)
    if (permission !== null) return { serverId, wait: { state: 'permission', permission } }
    if (awaited(agent, turnId)) return { serverId, wait: { state: 'running' } }
  }
  // The turn is over. Its reply is read only while the session is open and no turn runs:
  // the timeline of an agent that is mid-turn again is left alone.
  const reply = turnOf(agent).active ? { text: null, truncated: false } : await lastReply(api, agentId)
  const failure = nonEmpty(agent.lastError) ? text(agent.lastError) : null
  const inferred =
    agent.status === 'error' || failure
      ? { outcome: 'failed', error: failure ?? 'the agent is in an error state' }
      : { outcome: reply.text === null ? 'cancelled' : 'finished' }
  return {
    serverId,
    wait: { state: 'ended', ...(ended ?? inferred), text: reply.text, textTruncated: reply.truncated }
  }
}

// The turn an agent runs, as the runtime reports it.
function turnOf(agent) {
  const id = nonEmpty(agent?.activeTurn?.turnId)
  return { active: Boolean(agent?.activeTurn) || agent?.status === 'running', id }
}

// Whether the wait still has a turn to wait for: the turn that took the message, or, when the
// caller could not name it, whatever turn the agent runs.
function awaited(agent, turnId) {
  const running = turnOf(agent)
  if (!running.active) return false
  return !turnId || running.id === null || running.id === turnId
}

function waitUnavailable(agent) {
  if (!agent) return { state: 'unavailable', reason: 'not-found' }
  if (nonEmpty(agent.archivedAt)) return { state: 'unavailable', reason: 'archived' }
  if (agent.status === CLOSED_SESSION) return { state: 'unavailable', reason: 'closed' }
  return null
}

function pendingPermission(agent) {
  const pending = Array.isArray(agent?.pendingPermissions) ? agent.pendingPermissions : []
  if (pending.length === 0) return null
  return nonEmpty(pending[0]?.name) ?? nonEmpty(pending[0]?.title) ?? 'a tool'
}

// Whether the runtime hands a message to a running turn of this provider. It says so nowhere
// directly; a provider it drives through its ACP driver has no steering, and such a provider is
// an entry of the runtime's configuration that extends that driver.
async function providerSteers(api, provider) {
  const entries = (await api.config?.get())?.config?.providers ?? {}
  return entries[provider]?.extends !== DRIVER_WITHOUT_STEERING
}

// The reply that closes the agent's timeline: its text behind the last message or tool call. A
// message is recorded when the runtime accepts it, so this text follows the message the caller
// sent. Null when the timeline ends with something else, as a cancelled turn leaves it.
async function lastReply(api, agentId) {
  const page = await api.agents
    .ref(agentId)
    .timeline.refetch({ direction: 'tail', limit: REPLY_TAIL, projection: 'projected' })
  const items = (Array.isArray(page?.entries) ? page.entries : [])
    .map((entry) => entry?.item)
    .filter((item) => TURN_ITEMS.has(item?.type))
  let first = items.length
  while (first > 0 && items[first - 1].type === 'assistant_message') first -= 1
  const reply = items
    .slice(first)
    .map((item) => (typeof item.text === 'string' ? item.text : ''))
    .join('')
    .trim()
  if (!reply) return { text: null, truncated: false }
  const points = Array.from(reply.slice(0, 2 * REPLY_TEXT_LIMIT))
  return {
    text: points.slice(0, REPLY_TEXT_LIMIT).join(''),
    truncated: points.length > REPLY_TEXT_LIMIT || reply.length > 2 * REPLY_TEXT_LIMIT
  }
}

// The agent with exactly this id, or null. The runtime also resolves an id prefix and a title; an
// answer under another id is not the agent that was asked for. A lookup the runtime fails for
// another reason says nothing about the agent, so it is not reported as a refused call.
async function findAgent(api, daemon, agentId) {
  let result
  try {
    result = await api.agents.ref(agentId).refresh()
  } catch (error) {
    if (/^Agent not found\b/.test(String(error?.message ?? ''))) return null
    if (daemon.getConnectionState().status !== 'connected') throw error
    throw failure(
      'paseo_agent_lookup_failed',
      `The Paseo runtime could not look up agent ${agentId}: ${text(error?.message ?? error)}`
    )
  }
  return result?.agent?.id === agentId ? result.agent : null
}

function projectAgent(agent) {
  return {
    id: agent.id,
    provider: agent.provider ?? null,
    model: agent.model ?? null,
    thinkingOptionId: agent.effectiveThinkingOptionId ?? agent.thinkingOptionId ?? null,
    title: agent.title ?? null,
    labels: agent.labels ?? {},
    workspaceId: agent.workspaceId ?? null,
    cwd: agent.cwd ?? null,
    status: agent.status ?? null,
    archivedAt: agent.archivedAt ?? null,
    createdAt: agent.createdAt ?? null
  }
}

function serverIdOf(daemon) {
  return daemon.getLastServerInfoMessage().serverId
}

function requiredText(input, name) {
  const value = optionalText(input, name)
  if (!value) throw failure('invalid_bridge_payload', `The Paseo bridge payload needs a non-empty ${name}.`)
  return value
}

function optionalText(input, name) {
  const value = input[name]
  if (value === undefined || value === null) return null
  if (typeof value !== 'string') {
    throw failure('invalid_bridge_payload', `The Paseo bridge payload field ${name} must be text.`)
  }
  return value.trim() ? value : null
}

function requiredLabels(input) {
  const labels = input.labels
  const valid =
    labels !== null &&
    typeof labels === 'object' &&
    !Array.isArray(labels) &&
    Object.values(labels).every((value) => typeof value === 'string')
  if (!valid) {
    throw failure(
      'invalid_bridge_payload',
      'The Paseo bridge payload needs labels as an object of text values.'
    )
  }
  return labels
}

// ---------------------------------------------------------------------------------------------
// Shared: connection, deadline, reply
// ---------------------------------------------------------------------------------------------

async function connect(deadline) {
  const url = requiredEnvironment('AR_PASEO_URL')
  const expectedServerId = requiredEnvironment('AR_PASEO_SERVER_ID')
  const { DaemonClient, createPaseoApi } = await loadClientPackage()
  if (typeof globalThis.WebSocket !== 'function') {
    throw failure('paseo_bridge_unavailable', 'The Paseo bridge needs Node.js 22 or newer (global WebSocket).')
  }
  const daemon = new DaemonClient({
    url,
    clientId: `agents-remember-bridge-${globalThis.crypto.randomUUID()}`,
    clientType: 'cli',
    ...(nonEmpty(process.env.AR_PASEO_VERSION) ? { appVersion: process.env.AR_PASEO_VERSION } : {}),
    reconnect: { enabled: false },
    logger: STDERR_LOGGER,
    connectTimeoutMs: Math.max(1, Math.min(CONNECT_TIMEOUT_MS, deadline - Date.now()))
  })
  try {
    await daemon.connect()
  } catch (error) {
    await daemon.close().catch(() => {})
    throw failure(
      'paseo_daemon_unreachable',
      `The Paseo daemon at ${url} cannot be reached: ${text(error?.message ?? error)}`
    )
  }
  const serverId = daemon.getLastServerInfoMessage()?.serverId
  if (serverId !== expectedServerId) {
    await daemon.close().catch(() => {})
    throw failure(
      'paseo_runtime_mismatch',
      `The daemon at ${url} is ${serverId ?? 'unidentified'}, not the configured Paseo runtime ${expectedServerId}.`
    )
  }
  return { api: createPaseoApi(daemon), daemon }
}

async function loadClientPackage() {
  const prefix = requiredEnvironment('AR_PASEO_INSTALL_PREFIX')
  try {
    const modules = path.join(realpathSync(prefix), 'node_modules') + path.sep
    const resolve = createRequire(path.join(prefix, 'package.json')).resolve
    const [api, internal] = await Promise.all(
      ['@getpaseo/client', '@getpaseo/client/internal/daemon-client'].map((specifier) => {
        const entry = realpathSync(resolve(specifier))
        if (!entry.startsWith(modules)) throw new Error(`${specifier} resolves outside the install prefix`)
        return import(pathToFileURL(entry).href)
      })
    )
    if (typeof api.createPaseoApi !== 'function' || typeof internal.DaemonClient !== 'function') {
      throw new Error('the package does not export createPaseoApi and DaemonClient')
    }
    return { createPaseoApi: api.createPaseoApi, DaemonClient: internal.DaemonClient }
  } catch (error) {
    throw failure(
      'paseo_client_unavailable',
      `The Paseo client package cannot be loaded from ${prefix}; provision the Paseo runtime first. ` +
        text(error?.message ?? error)
    )
  }
}

// Settle with the promise, or reject with `onTimeout()` once the absolute deadline has passed.
function within(promise, deadline, onTimeout) {
  let timer
  const expired = new Promise((_resolve, reject) => {
    timer = setTimeout(() => reject(onTimeout()), Math.max(0, deadline - Date.now()))
  })
  return Promise.race([promise, expired]).finally(() => clearTimeout(timer))
}

async function finish(exitCode, reply, connection) {
  if (connection) {
    const closing = (async () => {
      await connection.api.dispose().catch(() => {})
      await connection.daemon.close().catch(() => {})
    })()
    await within(closing, Date.now() + CLOSE_TIMEOUT_MS, () => new Error('close timed out')).catch(() => {})
  }
  // Exit from the write callback: an abandoned call may still hold the socket open.
  process.stdout.write(JSON.stringify(reply), () => process.exit(exitCode))
}

function logToStderr(fields, message) {
  console.error(message ?? '', fields ?? '')
}

function failure(code, message) {
  return Object.assign(new Error(message), { code, bridgeFailure: true })
}

// A failure a command raised with `failure` keeps its code. Anything else the client threw is the
// runtime refusing the call, unless the connection is gone: then the daemon is unreachable.
function describe(error, connection) {
  if (error?.bridgeFailure === true) return { code: error.code, message: text(error.message) }
  const message = text(error?.message ?? error) || 'The Paseo runtime refused the call.'
  if (connection && connection.daemon.getConnectionState().status !== 'connected') {
    return {
      code: 'paseo_daemon_unreachable',
      message: text(`The connection to the Paseo daemon was lost: ${message}`)
    }
  }
  return { code: 'paseo_call_failed', message }
}

async function readPayload() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  const raw = Buffer.concat(chunks).toString('utf8')
  let payload
  try {
    payload = raw.trim() ? JSON.parse(raw) : {}
  } catch {
    throw failure('invalid_bridge_payload', 'The Paseo bridge payload is not JSON.')
  }
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    throw failure('invalid_bridge_payload', 'The Paseo bridge payload must be a JSON object.')
  }
  return payload
}

function requiredEnvironment(name) {
  const value = nonEmpty(process.env[name])
  if (!value) throw failure('paseo_bridge_unavailable', `The Paseo bridge was started without ${name}.`)
  return value
}

function positiveInteger(value, fallback) {
  const parsed = Number.parseInt(value ?? '', 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function nonEmpty(value) {
  return typeof value === 'string' && value.trim() ? value : null
}

function text(value) {
  return String(value ?? '').slice(0, MESSAGE_LIMIT)
}
