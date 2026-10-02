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
//                             efforts: [{id, label}], defaultEffort?}], listingError?}]}
//            The providers the runtime reports as ready and enabled, each with the models and
//            thinking options the runtime lists for it. `refresh` asks the runtime to rediscover
//            its providers first. A provider whose model listing fails keeps its row, with no
//            models and the runtime's error text.
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

import { realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const COMMANDS = {
  catalog: readCatalog
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
    const payload = await readPayload()
    const result = await within(
      (async () => {
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
  const snapshot = await within(
    (async () => {
      if (input.refresh === true) await api.providers.refresh()
      return await api.providers.waitForReady({ timeoutMs: Math.max(1, deadline - Date.now()) })
    })(),
    discoveryDeadline,
    () => failure('paseo_bridge_timeout', 'The Paseo runtime did not finish provider discovery in time.')
  )
  const offered = (Array.isArray(snapshot?.entries) ? snapshot.entries : []).filter(
    (entry) => typeof entry?.provider === 'string' && entry.status === 'ready' && entry.enabled === true
  )
  const providers = await Promise.all(
    offered.map((entry) => listProvider(api, entry, listingDeadline))
  )
  if (daemon.getConnectionState().status !== 'connected') {
    throw failure('paseo_daemon_unreachable', 'The connection to the Paseo daemon was lost while reading the catalog.')
  }
  const info = daemon.getLastServerInfoMessage()
  return {
    runtime: { serverId: info.serverId, version: typeof info.version === 'string' ? info.version : null },
    providers
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
