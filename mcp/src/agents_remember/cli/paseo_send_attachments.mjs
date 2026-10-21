// The images and text attachments one agent-send hands on with its message (MIK-R188 rule 9).
//
// Paseo's send takes `images: [{data, mimeType}]` and `attachments` beside the text
// (`SendMessageOptions`, Paseo's client package 0.11.1, `daemon-client.d.ts:149-158`; the bridge
// script is its one importer, so this module names no package). A text attachment
// must carry `mimeType: "text/plain"` (`TextAttachmentSchema`, protocol `messages.d.ts:772-788`).
// Only text attachments are handed on: the developer's tray sends no other kind.
//
// A payload without them, or with empty lists, adds nothing, so a send without them keeps the
// options it always had (`{messageId}`). A malformed entry refuses the whole payload before the
// agent is read: nothing is sent with part of what the caller gave.
import { failure } from './paseo_bridge_primitives.mjs'

export function sendExtras(input) {
  const images = listOf(input, 'images', image)
  const attachments = listOf(input, 'attachments', textAttachment)
  return {
    ...(images.length > 0 ? { images } : {}),
    ...(attachments.length > 0 ? { attachments } : {})
  }
}

function listOf(input, name, check) {
  const value = input[name]
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) throw invalid(`${name} must be a list`)
  return value.map((entry, index) => check(entry, `${name}[${index}]`))
}

function image(entry, where) {
  if (!isObject(entry)) throw invalid(`${where} must be an object`)
  const { data, mimeType } = entry
  if (typeof data !== 'string' || !data) throw invalid(`${where}.data must be non-empty base64 text`)
  if (typeof mimeType !== 'string' || !mimeType.startsWith('image/')) {
    throw invalid(`${where}.mimeType must name an image type`)
  }
  return { data, mimeType }
}

function textAttachment(entry, where) {
  if (!isObject(entry)) throw invalid(`${where} must be an object`)
  const { type, mimeType, title, text } = entry
  if (type !== 'text') throw invalid(`${where}.type must be "text"`)
  if (mimeType !== 'text/plain') throw invalid(`${where}.mimeType must be "text/plain"`)
  if (typeof text !== 'string') throw invalid(`${where}.text must be text`)
  if (!(title === undefined || title === null || typeof title === 'string')) {
    throw invalid(`${where}.title must be text or null`)
  }
  return { type, mimeType, ...(title === undefined ? {} : { title }), text }
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function invalid(detail) {
  return failure('invalid_bridge_payload', `The Paseo bridge payload is not a send: ${detail}.`)
}
