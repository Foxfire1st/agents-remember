// Shared bridge value predicates: one owner.
export function nonEmpty(value) {
  return typeof value === 'string' && value.trim() ? value : null
}

export function requiredText(input, name) {
  const value = optionalText(input, name)
  if (!value) throw failure('invalid_bridge_payload', `The Paseo bridge payload needs a non-empty ${name}.`)
  return value
}

export function optionalText(input, name) {
  const value = input[name]
  if (value === undefined || value === null) return null
  if (typeof value !== 'string') {
    throw failure('invalid_bridge_payload', `The Paseo bridge payload field ${name} must be text.`)
  }
  return value.trim() ? value : null
}

export function failure(code, message) {
  return Object.assign(new Error(message), { code, bridgeFailure: true })
}

export function serverIdOf(daemon) {
  return daemon.getLastServerInfoMessage().serverId
}
