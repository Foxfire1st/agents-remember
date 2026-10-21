// A passage the developer took in a Paseo chat (MIK-R188 rule 3). The plug-in's "⊕ add" posts it on
// the `ar.*` channel; the frame channel checks origin and source and hands it here. It becomes a text
// pick with the chat's provenance, so the recipient reads "from Manager · 14:05": the frame channel
// takes a pick only with its message's time. As the first pick of a tray, it makes that chat the
// default target (rule 7).
import { passageTitle } from './trayModel';
import { trayStore } from './trayStore';
import type { TrayPickInput } from './types';

export interface ChatPick {
  text: string;
  agentId: string;
  messageId: string | null;
  messageTime: string;
}

export function chatPickInput(pick: ChatPick): TrayPickInput | null {
  const text = pick.text.trim();
  if (!text || !pick.agentId) return null;
  return {
    kind: 'text',
    title: passageTitle(text),
    text,
    provenance: { source: 'chat', agentId: pick.agentId, messageId: pick.messageId, messageTime: pick.messageTime },
  };
}

// Takes a chat passage into the tray and shows it. Answers whether it entered (a second take adds
// nothing; a passage over a bound is refused by name).
export function takeChatPick(pick: ChatPick, store = trayStore): boolean {
  const input = chatPickInput(pick);
  return input ? store.getState().take(input) : false;
}
