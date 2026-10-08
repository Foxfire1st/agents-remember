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
//   runtime-info  {}
//            -> {serverId}
//            The server id of the configured daemon and nothing else. An answer means that the
//            daemon is reachable and is the configured one; no runtime function is called.
//
//   workspace-open  {cwd: string, masterProject?: {directory, key, name}, task?: {key, title}}
//            -> {serverId, preparation?: "created" | "found" | "opened",
//                workspace: {id, directory, name, projectId, projectKind}}
//            Master/task placement creates or replays one directory workspace under the explicit
//            project, keyed by canonical master/task refs. Names follow the saved payload; an old
//            replay may restore older display names. Preparation comes from the public creation
//            observer; cwd-only Projects opens report "opened", without a creation outcome.
//            Cwd-only controller and persisted v1 calls
//            retain directory-open semantics. Explicit placement never falls back to that path.
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
//            then applies its own default model. `systemPrompt` and `mcpServers` are stored by
//            the runtime with the agent and applied again whenever it resumes the agent's
//            session: the text is added to the provider's system-level instructions where the
//            provider has such, and each tool server is started for the agent with exactly the
//            given command and environment. `parentAgentId` names the agent that starts this one:
//            the runtime records it as the new agent's parent (the label `paseo.parent-agent-id`,
//            which AGENT's `labels` shows) and refuses the creation when it has no such agent
//            loaded. The agent still runs in `workspaceId`, not in the parent's workspace.
//            `prompt` is the first message; without it the agent is left idle. The agent is
//            always created without a message, and the first message is sent to it afterwards
//            under a message id derived from `idempotencyKey`. A repeat of the call sends the
//            message to an agent that has had no message yet and nothing to one that has had
//            one; the runtime, which delivers a message id once, keeps two calls that run at the
//            same time from delivering it twice. An agent with tool servers is first given
//            TOOL_SERVER_START_MS, counted from this call's creation or lookup of it, and a
//            closed session is opened before that time starts: a harness that starts its tool
//            servers with the session offers a turn only the tools of the servers that have
//            answered when the turn begins, and the runtime reports nothing about them. An
//            archived agent is sent nothing. A first message that the runtime does not accept
//            for an agent that exists fails with `paseo_first_message_undelivered`; a repeat of
//            the call sends it. A closed session of an agent without a message that the runtime
//            cannot open again fails with `paseo_agent_without_message_lost`: no repeat can
//            deliver the message to that agent, and the caller starts a new one. `agent` is the
//            agent as created or found, before the message.
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
//   attentionReason, lastTurn}. `status` is the runtime's own word (initializing, idle, running,
//   error, closed). `attentionReason` is the runtime's own mark on the agent (finished, error,
//   permission, or null); it is the one trace of a failed turn that outlives a closed session.
//   `lastTurn` is null unless the agent is idle with an open session; then it is
//   {state: 'none'} when the agent has not run a turn, {state: 'replied', text} when the timeline
//   ends with the agent's reply (at most 3,000 characters of it), and {state: 'unreplied'} when
//   the last turn ended without one, which is what a cancelled turn leaves behind.
//
//   agent-send  {agentId: string, text: string, messageId: string, afterResume?: boolean}
//            -> {serverId, delivery: {delivered: true, taken: 'started' | 'steered' | 'replaced',
//                                     turnId: string | null}
//                        | {delivered: false, refused: 'not-found' | 'archived' | 'closed' | 'busy',
//                           detail: string, permissionPending?: true, permission?: string}}
//            One message to the agent with exactly that id, for a live agent with an open
//            session only: a missing, archived or closed agent is refused and left as it is,
//            because the runtime would un-archive or resume it to deliver. An idle agent starts a
//            turn with the message (`started`). An agent that is mid-turn is sent the message
//            with the runtime's steer behaviour, which hands it to the running turn (`steered`).
//            The runtime has no "steer or refuse": for a provider without steering it cancels
//            the running turn and starts another, and it reports which providers those are only
//            through their entry in its configuration (they extend its ACP driver). Such a
//            recipient, and one that is still initializing, is refused as `busy`. So is an
//            agent that waits for a permission decision (`permissionPending`, with the name of
//            the permission when the runtime gives one): the runtime answers every pending
//            permission of the recipient with a denial when it delivers a message. A permission
//            the agent asks for between this look and the send is still denied. `taken` says
//            what the runtime did, read from the turn the agent runs before and after the send;
//            `replaced` means the runtime cancelled the running turn all the same. `turnId` is
//            the turn that took the message, when it is still running. `messageId` is the
//            caller's id of the message; the runtime records it with the message in the timeline.
//            A connection lost while the message is being sent fails with
//            `paseo_send_outcome_unknown`: the runtime may have accepted it. Once the runtime
//            has accepted the message the command answers `delivered: true`, also when the
//            lookup afterwards fails (`turnId` is then null). `afterResume` says that the
//            caller has just resumed the agent's session and that the agent has tool servers:
//            the command then waits TOOL_SERVER_START_MS before it looks at the agent, for the
//            reason given under `agent-create`.
//
//   agent-wait  {agentId: string, messageId?: string, turnId?: string, steered?: boolean,
//                waitMs?: number}
//            -> {serverId, wait: {state: 'running', turnId: string | null}
//                              | {state: 'permission', permission: string}
//                              | {state: 'ended', outcome: 'finished' | 'failed' | 'cancelled',
//                                 text: string | null, textTruncated: boolean, error?: string,
//                                 laterTurn?: boolean}
//                              | {state: 'undecided', reason: string}
//                              | {state: 'unavailable', reason: 'not-found' | 'archived' | 'closed'}}
//            Waits, for at most `waitMs` and never past this call's own deadline, until the turn
//            that consumed the message `messageId` has ended, or the agent asks for a permission.
//            `turnId` is the turn the caller knows to hold the message (as `agent-send` or an
//            earlier `running` answer returned it). `running` means the time was up first; the
//            caller repeats the call with the `turnId` of the answer.
//            A message that began its turn was consumed by that turn: the wait ends when that
//            turn has ended, and a turn the agent runs afterwards is not followed. When the
//            caller names no turn and nothing stands behind the message yet, the turn may not
//            have begun: it is given five seconds to begin.
//            A message handed to a running turn (`steered`) is consumed by that turn or by the
//            next one: a harness can leave such a message unread until its turn has ended and
//            then run it as a turn of its own, which the runtime begins without recording a
//            message for it. A turn that went on to another step (a tool call) behind the
//            message has read it. A turn that ended without one is given five seconds in which
//            another can begin, and a turn the agent runs after it is followed, unless a newer
//            message of another turn stands behind ours in the timeline.
//            `ended` carries the outcome and the text of the turn that consumed the message:
//            the runtime's own event of that turn's end when this call saw it; otherwise
//            `failed` when the agent is in an error state, `finished` when the turn closed with
//            text of the agent, and `cancelled` when it did not. `text` is that turn's text
//            behind its last message or tool call (at most 20,000 characters). For a `steered`
//            message `laterTurn` says which turn that is: true for a turn that began after the
//            one that was running, false for the running turn itself.
//            `undecided` means that the message is not among the timeline entries read, so no
//            text can be said to answer it. Nothing is sent, resumed or un-archived.
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

import { createHash } from 'node:crypto'
import { realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const COMMANDS = {
  catalog: readCatalog,
  'runtime-info': readRuntimeInfo,
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
// How often a wait looks at the agent while a turn runs, how long a turn that has ended is given
// for a following turn to begin, and how often the agent is looked at in that time.
const WAIT_POLL_MS = 1000
const TURN_GAP_MS = 5000
const TURN_GAP_POLL_MS = 250
const REPLY_TAIL = 200
const REPLY_TEXT_LIMIT = 20000
const TURN_ENDS = { turn_completed: 'finished', turn_failed: 'failed', turn_canceled: 'cancelled' }
// agent-create and agent-send: how long an agent with tool servers is given between the opening
// of its session and a message, and what makes the first message's id out of the idempotency
// key. The build's tool server had answered its tool list 2.2 s after the creation of one agent,
// 3.2 s with three agents created at once and 5.3 s with six (261001-PNT master pass, 2026-10-02).
const TOOL_SERVER_START_MS = 6000
const FIRST_MESSAGE_SUFFIX = ':first-message'
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
  const cwd = requiredText(input, 'cwd')
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
        entry.status === 'ready' ? listProvider(api, entry, listingDeadline, cwd) : stillLoading(entry)
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

async function listProvider(api, entry, deadline, cwd) {
  const row = { id: entry.provider, label: nonEmpty(entry.label) ?? entry.provider }
  try {
    const listing = await within(
      api.providers.listModels(entry.provider),
      deadline,
      () => new Error('the runtime did not list the models in time')
    )
    if (listing?.error) return { ...row, models: [], listingError: text(listing.error) }
    const models = Array.isArray(listing?.models) ? listing.models : []
    const projected = await Promise.all(models.filter((model) => nonEmpty(model?.id)).map(async (model) => {
      const result = projectModel(model)
      try {
        const features = await within(api.providers.listFeatures({ provider: `${entry.provider}/${model.id}`, cwd }), deadline, () => new Error('Service tier discovery timed out'))
        if (features.error) throw new Error(features.error)
        return { ...result, serviceTiers: tierOptions(features.features) }
      } catch (error) {
        return { ...result, serviceTiers: [], serviceTierError: text(error?.message ?? error) }
      }
    }))
    return { ...row, models: projected }
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

function tierOptions(features) {
  const feature = Array.isArray(features) ? features.find((row) => row.id === 'service_tier' && row.type === 'select') : null
  return (feature?.options ?? []).map(({ id, label }) => ({ id, label }))
}

async function readRuntimeInfo({ daemon }) {
  return { serverId: serverIdOf(daemon) }
}

async function openWorkspace({ api, daemon }, input) {
  const cwd = requiredText(input, 'cwd')
  let workspace
  let preparation = null
  if (!('masterProject' in input) && !('task' in input)) {
    workspace = (await api.workspaces.open(cwd)).current()
    preparation = 'opened'
  } else {
    const { masterProject, task } = input
    if (!masterProject || typeof masterProject !== 'object' || Array.isArray(masterProject) ||
        !task || typeof task !== 'object' || Array.isArray(task)) {
      throw failure('invalid_bridge_payload', 'Master/task workspace placement must be complete.')
    }
    const directory = requiredText(masterProject, 'directory')
    const masterKey = requiredText(masterProject, 'key')
    const name = requiredText(masterProject, 'name')
    const taskKey = requiredText(task, 'key')
    const title = requiredText(task, 'title')
    const project = (await daemon.addProject(directory)).project
    if (!nonEmpty(project?.projectId)) {
      throw failure('paseo_bridge_invalid_reply', 'The Paseo runtime returned a project without an id.')
    }
    await daemon.renameProject(project.projectId, name)
    const idempotencyKey = 'ar-task-workspace:v2:' + createHash('sha256')
      .update(JSON.stringify([masterKey, taskKey])).digest('hex')
    const handle = await api.workspaces.create({
      source: { kind: 'directory', path: cwd, projectId: project.projectId }, idempotencyKey,
      onEvent(snapshot) {
        if (preparation === null) preparation = snapshot.workspace ? 'found' : 'created'
      }
    })
    // Creation replay holds an old descriptor; refresh before trusting membership or liveness.
    workspace = await handle.refresh()
    if (!workspace || workspace.archivingAt || workspace.archivedAt ||
        workspace.projectId !== project.projectId ||
        !nonEmpty(workspace.workspaceDirectory) || path.resolve(workspace.workspaceDirectory) !== path.resolve(cwd)) {
      throw failure('paseo_call_failed', 'The Paseo task workspace is unavailable or has different placement.')
    }
    const named = await handle.setTitle(title)
    workspace = { ...workspace, name: named.title }
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

async function createAgent({ api, daemon }, input) {
  const agentId = requiredText(input, 'agentId')
  const request = agentCreation(input, agentId)
  const serverId = serverIdOf(daemon)
  const present = await findAgent(api, daemon, agentId)
  if (present) {
    await sendFirstMessage(api, daemon, present, request)
    return { serverId, existing: true, agent: projectAgent(present) }
  }
  let created
  try {
    created = await request.create(api, daemon)
  } catch (error) {
    // The runtime answered with an error. Whether an agent exists under the id decides what that
    // means: it exists (a repeat that lost its creation record), or the runtime refused the
    // creation.
    if (daemon.getConnectionState().status !== 'connected') throw error
    const after = await findAgent(api, daemon, agentId)
    if (!after) throw error
    await sendFirstMessage(api, daemon, after, request)
    return {
      serverId,
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
  await sendFirstMessage(api, daemon, created, request)
  return { serverId, existing: false, agent: projectAgent(created) }
}

// Send the first message of a launch to an agent that has had no message yet. An agent that
// has had a message is taken to have this one. That holds as long as nothing else reaches the
// agent before its launch has answered: the role-message tool refuses a recipient whose start
// has not finished, but a message typed into the agent's own chat in that time comes first. The
// message id is the same on every run: the runtime keeps a delivery record per agent and message
// id, and two calls that run at once deliver it once. A closed session is opened first, so that
// its tool servers start before the wait: reading the timeline is the runtime's resume. A session
// that cannot be opened has its own failure: a harness may keep nothing of a session that never
// ran a turn, and then no repeat can deliver the message.
async function sendFirstMessage(api, daemon, agent, request) {
  if (!request.prompt || nonEmpty(agent.archivedAt) || nonEmpty(agent.lastUserMessageAt)) return
  const handle = api.agents.ref(agent.id)
  if (agent.status === CLOSED_SESSION) {
    try {
      await handle.timeline.refetch({ direction: 'tail', limit: 1 })
    } catch (error) {
      if (daemon.getConnectionState().status !== 'connected') throw error
      throw failure(
        'paseo_agent_without_message_lost',
        `Agent ${agent.id} exists in the Paseo runtime without its first message, and its ` +
          `closed session cannot be opened again (${text(error?.message ?? error)}).`
      )
    }
  }
  try {
    if (request.hasToolServers) await toolServerStart()
    // With the steer behaviour: should a turn run by now, the runtime's default would cancel it.
    await handle.send(request.prompt, {
      messageId: request.firstMessageId,
      activeTurnBehavior: 'steer'
    })
  } catch (error) {
    if (daemon.getConnectionState().status !== 'connected') throw error
    throw failure(
      'paseo_first_message_undelivered',
      `Agent ${agent.id} exists in the Paseo runtime, but its first message was not delivered ` +
        `(${text(error?.message ?? error)}). Repeat the request to send it.`
    )
  }
}

// The time a session's tool servers are given to start before a message begins a turn.
function toolServerStart() {
  return new Promise((resolve) => setTimeout(resolve, TOOL_SERVER_START_MS))
}

// Check the payload and return how the agent is created: through the public client, or, for a
// provider launched without a model, through the daemon client with the same request.
function agentCreation(input, agentId) {
  const workspaceId = requiredText(input, 'workspaceId')
  const provider = requiredText(input, 'provider')
  const model = optionalText(input, 'model')
  const thinkingOptionId = optionalText(input, 'thinkingOptionId')
  const featureValues = input.featureValues
  if (featureValues !== undefined && (featureValues === null || typeof featureValues !== 'object' ||
    Array.isArray(featureValues) || Object.keys(featureValues).length !== 1 || !nonEmpty(featureValues.service_tier))) {
    throw failure('invalid_bridge_payload', 'featureValues must contain only a nonempty service_tier string.')
  }
  const prompt = optionalText(input, 'prompt')
  const idempotencyKey = requiredText(input, 'idempotencyKey')
  const shared = { agentId, idempotencyKey, labels: requiredLabels(input) }
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
    ...(featureValues ? { featureValues } : {}),
    ...(systemPrompt ? { systemPrompt } : {}),
    ...(mcpServers ? { mcpServers } : {})
  }
  return {
    prompt,
    firstMessageId: `${idempotencyKey}${FIRST_MESSAGE_SUFFIX}`,
    hasToolServers: mcpServers !== null && Object.keys(mcpServers).length > 0,
    // The agent is created without a message; `sendFirstMessage` sends `prompt` afterwards.
    async create(api, daemon) {
      const workspace = api.workspaces.ref(workspaceId)
      let cwd
      if (featureValues) {
        cwd = (await workspace.refresh())?.workspaceDirectory
        if (!nonEmpty(cwd)) throw new Error(`Workspace ${workspaceId} has no available directory`)
        const features = await daemon.listProviderFeatures({ provider, cwd, ...(model ? { model } : {}) })
        if (features.error || !tierOptions(features.features).some(({ id }) => id === featureValues.service_tier)) {
          throw new Error(`Service tier ${featureValues.service_tier} is not offered for ${provider}/${model ?? 'native default'}${features.error ? ': ' + features.error : ''}`)
        }
      }
      if (model) {
        const handle = await workspace.agents.create({
          ...shared,
          config: { provider: `${provider}/${model}`, ...kept },
          title,
          ...(parentAgentId ? { parent: parentAgentId } : {})
        })
        return handle.current()
      }
      cwd = cwd ?? (await workspace.refresh())?.workspaceDirectory
      if (!nonEmpty(cwd)) throw new Error(`Workspace ${workspaceId} has no available directory`)
      return await daemon.createAgent({
        ...shared,
        config: { provider, cwd, title, ...kept },
        workspaceId,
        ...(parentAgentId ? { callerAgentId: parentAgentId } : {})
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
    attentionReason: nonEmpty(agent.attentionReason),
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
  if (input.afterResume === true) await toolServerStart()
  const before = await findAgent(api, daemon, agentId)
  if (!before) return refused('not-found', 'the host has no such agent')
  if (nonEmpty(before.archivedAt)) return refused('archived', 'the agent is archived')
  if (before.status === CLOSED_SESSION) return refused('closed', 'the session of the agent is closed')
  if (before.status === 'initializing') return refused('busy', 'the agent is still starting')
  if (pendingPermission(before) !== null) {
    const refusal = refused(
      'busy',
      'the agent waits for a permission decision, which a message would answer with a denial'
    )
    const permission = pendingPermissionName(before)
    Object.assign(refusal.delivery, { permissionPending: true, ...(permission ? { permission } : {}) })
    return refusal
  }
  const running = turnOf(before)
  if (running.active && !(await providerSteers(api, before.provider))) {
    return refused(
      'busy',
      'the agent is mid-turn and the host cannot hand a message to a running turn of this provider'
    )
  }
  // Always with the steer behaviour: should a turn begin between the lookup and the send, the
  // runtime's default would cancel it.
  try {
    await api.agents.ref(agentId).send(message, { messageId, activeTurnBehavior: 'steer' })
  } catch (error) {
    if (daemon.getConnectionState().status === 'connected') throw error
    throw failure(
      'paseo_send_outcome_unknown',
      'The connection to the Paseo daemon was lost while the message was being sent; it is not ' +
        'known whether the message was delivered.'
    )
  }
  // The runtime accepted the message. What the agent runs now is looked up for the answer only.
  const after = turnOf((await findAgent(api, daemon, agentId).catch(() => null)) ?? {})
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
  const messageId = optionalText(input, 'messageId')
  const steered = input.steered === true
  let followed = optionalText(input, 'turnId')
  const serverId = serverIdOf(daemon)
  const answer = (wait) => ({ serverId, wait })
  const stop =
    Date.now() +
    Math.min(positiveInteger(input.waitMs, WAIT_DEFAULT_MS), Math.max(0, deadline - Date.now() - WAIT_RESERVE_MS))
  let agent = await findAgent(api, daemon, agentId)
  const unavailable = waitUnavailable(agent)
  if (unavailable) return answer(unavailable)
  // The runtime's turn events are live only, so they are followed for as long as this call
  // waits: the end of each turn is kept for the outcome, and every start or end of a turn wakes
  // the wait, which otherwise looks at the agent at intervals.
  const ends = { byTurn: new Map(), last: null }
  let wake = () => {}
  let following = true
  const unsubscribe = api.agents.ref(agentId).timeline.subscribe((update) => {
    const event = update?.event
    if (!following || !event) return
    const outcome = TURN_ENDS[event.type]
    if (outcome) {
      ends.last = { outcome, ...(nonEmpty(event.error) ? { error: text(event.error) } : {}) }
      if (nonEmpty(event.turnId)) ends.byTurn.set(event.turnId, ends.last)
    }
    if (outcome || event.type === 'turn_started') wake()
  })
  const pause = (ms) =>
    new Promise((resolve) => {
      wake = resolve
      setTimeout(resolve, Math.max(0, ms))
    })
  const view = () => messageView(api, agentId, messageId)
  let quietSince = null
  let settled = null
  try {
    // Without the events the outcome is taken from the agent's state afterwards.
    await unsubscribe.ready.catch(() => {})
    for (;;) {
      agent = await findAgent(api, daemon, agentId)
      const gone = waitUnavailable(agent)
      if (gone) return answer(gone)
      const permission = pendingPermission(agent)
      if (permission !== null) return answer({ state: 'permission', permission })
      const running = turnOf(agent)
      if (running.active) {
        quietSince = null
        if (messageId && running.id !== null && running.id !== followed) {
          // Another turn than the one known to hold the message. A newer message began it, or
          // the message began a turn that is over: then the turn that consumed ours has ended.
          const seen = await view()
          if (!seen.found || seen.newer || (followed !== null && !steered)) {
            return answer(waitEnded(seen, ends, agent, steered, followed))
          }
          followed = running.id
        }
        if (Date.now() >= stop) return answer({ state: 'running', turnId: followed })
        await pause(Math.min(WAIT_POLL_MS, stop - Date.now()))
        continue
      }
      // No turn runs. What the timeline holds says whether one can still begin for the message.
      if (quietSince === null) {
        const seen = await view()
        if (!seen.found || seen.newer || !turnMayBegin(seen, steered, followed)) {
          settled = seen
          break
        }
        quietSince = Date.now()
      }
      if (Date.now() - quietSince >= TURN_GAP_MS) break
      if (Date.now() >= stop) return answer({ state: 'running', turnId: followed })
      await pause(TURN_GAP_POLL_MS)
    }
  } finally {
    following = false
    unsubscribe()
  }
  return answer(waitEnded(settled ?? (await view()), ends, agent, steered, followed))
}

// Whether, with no turn running, a turn can still begin that takes the message up. A message
// handed to a running turn: when that turn may not have read it. A message for which no turn is
// known: while nothing stands behind it, because the turn it began may not have started yet.
function turnMayBegin(seen, steered, followed) {
  if (steered) return !consumedBy(seen, steered, followed).read
  return followed === null && seen.nothingBehind
}

// The turn that consumed the message, and whether it is known to have read it. A message that
// began its turn: that turn. A message handed to a running turn: the last turn that ran behind
// it, by the timeline or, for a turn that left nothing there, by what this call followed
// (`followed`). Such a message was read when a later turn than its own ran, or when its own turn
// went on to another step behind it; otherwise a turn that takes it up may still begin.
function consumedBy(seen, steered, followed) {
  const { own, latest } = seen
  if (!steered) return { turn: own ?? latest, read: true }
  const after = latest !== null && latest !== own ? latest : followed
  const later = own !== null && after !== null && after !== own
  return { turn: later ? after : latest, later, read: later || seen.stepBehind }
}

// The answer of a wait once the turn that consumed the message is over: the text that turn left
// in the timeline, with the runtime's own event of its end when this call saw it.
function waitEnded(seen, ends, agent, steered, followed) {
  if (!seen.found) {
    return {
      state: 'undecided',
      reason: `the message is not among the last ${REPLY_TAIL} timeline entries of the agent`
    }
  }
  const { turn, later } = consumedBy(seen, steered, followed)
  // For a message handed to a running turn the answer says which turn it is about.
  const reply = { ...seen.closing(turn), ...(steered ? { laterTurn: later === true } : {}) }
  const event = turn === null ? ends.last : ends.byTurn.get(turn)
  if (event) return { state: 'ended', ...event, ...reply }
  // The agent's state now is that of its last turn; it says nothing of an earlier one.
  const failed = nonEmpty(agent.lastError) ? text(agent.lastError) : null
  const last = !seen.newer && (seen.latest === null || turn === null || turn === seen.latest)
  if (last && (agent.status === 'error' || failed)) {
    return { state: 'ended', outcome: 'failed', error: failed ?? 'the agent is in an error state', ...reply }
  }
  return { state: 'ended', outcome: reply.text === null ? 'cancelled' : 'finished', ...reply }
}

// What the agent's timeline holds behind one message. `found`: the message is among the entries
// read. `newer`: a newer message of another turn stands behind it; what is said below is about
// the entries between the two (all of the rest, when there is no newer one). `own`: the turn the
// message was recorded in. `latest`: the turn of the last entry. `nothingBehind`: there is no
// entry at all. `stepBehind`: a tool call lies behind the message, so the turn went on to
// another step after it. `closing(turn)`: the agent's text that closes that turn's entries. A
// message of the same turn as ours is not a newer one: the prompt that began a turn can be
// recorded after a message that was handed to it. Without a message id the whole tail is looked
// at; without turn ids, everything is one turn.
async function messageView(api, agentId, messageId) {
  const page = await api.agents
    .ref(agentId)
    .timeline.refetch({ direction: 'tail', limit: REPLY_TAIL, projection: 'projected' })
  const entries = (Array.isArray(page?.entries) ? page.entries : []).filter((entry) =>
    TURN_ITEMS.has(entry?.item?.type)
  )
  const mine = messageId
    ? entries.findLastIndex(
        (entry) =>
          entry.item.type === 'user_message' &&
          [entry.item.messageId, entry.item.clientMessageId].includes(messageId)
      )
    : -1
  if (messageId && mine < 0) return { found: false }
  const own = mine < 0 ? null : nonEmpty(entries[mine].turnId)
  const behind = entries.slice(mine + 1)
  const next = messageId
    ? behind.findIndex(
        (entry) => entry.item.type === 'user_message' && (own === null || nonEmpty(entry.turnId) !== own)
      )
    : -1
  const between = next < 0 ? behind : behind.slice(0, next)
  return {
    found: true,
    newer: next >= 0,
    own,
    latest: nonEmpty(between.at(-1)?.turnId),
    nothingBehind: between.length === 0,
    stepBehind: between.some((entry) => entry.item.type === 'tool_call'),
    closing: (turn) =>
      closingText(
        between.filter((entry) => turn === null || nonEmpty(entry.turnId) === turn).map((entry) => entry.item)
      )
  }
}

// The agent's text behind the last message or tool call of these items, at most REPLY_TEXT_LIMIT
// characters of it.
function closingText(items) {
  let first = items.length
  while (first > 0 && items[first - 1].type === 'assistant_message') first -= 1
  const reply = items
    .slice(first)
    .map((item) => (typeof item.text === 'string' ? item.text : ''))
    .join('')
    .trim()
  if (!reply) return { text: null, textTruncated: false }
  const points = Array.from(reply.slice(0, 2 * REPLY_TEXT_LIMIT))
  return {
    text: points.slice(0, REPLY_TEXT_LIMIT).join(''),
    textTruncated: points.length > REPLY_TEXT_LIMIT || reply.length > 2 * REPLY_TEXT_LIMIT
  }
}

// The turn an agent runs, as the runtime reports it.
function turnOf(agent) {
  const id = nonEmpty(agent?.activeTurn?.turnId)
  return { active: Boolean(agent?.activeTurn) || agent?.status === 'running', id }
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
  return pendingPermissionName(agent) ?? 'a tool'
}

// The name the runtime gives the first pending permission of an agent, or null.
function pendingPermissionName(agent) {
  const first = Array.isArray(agent?.pendingPermissions) ? agent.pendingPermissions[0] : null
  return nonEmpty(first?.name) ?? nonEmpty(first?.title) ?? null
}

// Whether the runtime hands a message to a running turn of this provider. It says so nowhere
// directly; a provider it drives through its ACP driver has no steering, and such a provider is
// an entry of the runtime's configuration that extends that driver.
async function providerSteers(api, provider) {
  const entries = (await api.config?.get())?.config?.providers ?? {}
  return entries[provider]?.extends !== DRIVER_WITHOUT_STEERING
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
  const serviceTier = agent.features?.find((feature) => feature.id === 'service_tier')?.value
  return {
    id: agent.id,
    provider: agent.provider ?? null,
    model: agent.model ?? null,
    thinkingOptionId: agent.effectiveThinkingOptionId ?? agent.thinkingOptionId ?? null,
    ...(typeof serviceTier === 'string' ? { serviceTier } : {}),
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
