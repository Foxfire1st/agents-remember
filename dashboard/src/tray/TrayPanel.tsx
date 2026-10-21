// AR's tray (MIK-R188 rules 5–11). The same component docks in the chat column above Paseo's message
// box and at the bottom right of pages without a chat column. Shown, it is minimised to one line or
// expanded to the full view (rule 5, 15f92a8f).
//
// It is non-modal (rule 4): it takes no focus when it opens, never clears the page's selection, and
// nothing in it calls `removeAllRanges()`. While it is open, Ctrl+V of an image anywhere in the
// dashboard becomes an image pick (rule 6), minimised or expanded: one `paste` handler on
// `clipboardData.files`, no permission, no screen capture.
import { useEffect, useState } from 'react';

import { pickUnavailableText, usePickNotice } from '../cockpit/frameTrayChannel';
import { TrayChip } from './TrayChip';
import { TrayLineRefusals, TrayOutcome, TrayOutcomeLine, TraySentLog } from './TraySent';
import { TrayTargets, targetParts, targetsLabel, targetsTitle } from './TrayTargets';
import { previewLine } from './trayModel';
import { DEFAULT_BOUNDS, MIB, trayPreferences, useCopyOnHighlight, useTrayBounds, type TrayBounds } from './trayPreferences';
import { defaultSendDeps, sendTray } from './traySend';
import { loadSeats, useChatNames, type ChatName } from './traySeats';
import { trayImageStore, trayStore, useTray } from './trayStore';
import * as look from './trayStyles';

// Images from a paste event, or none. Text pastes are left to the page. The store refuses, by name,
// an image of a type the server does not take.
export function pastedImages(event: ClipboardEvent): File[] {
  return Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'));
}

export function useTrayPaste(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const onPaste = (event: ClipboardEvent) => {
      const images = pastedImages(event);
      if (images.length === 0) return;
      event.preventDefault();
      for (const image of images) void trayStore.getState().addImage(image);
    };
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [active]);
}

export function sendNow(): Promise<boolean> {
  return sendTray(trayStore, defaultSendDeps(trayImageStore, trayPreferences.getState().bounds));
}

// The one named notice of a chat whose text cannot be picked (a Paseo page the plug-in's pinned
// adapter does not read). Object and panel picks keep working.
function PickNotices() {
  const reasons = [usePickNotice('document'), usePickNotice('chats')].filter((reason): reason is string => !!reason);
  return (
    <>
      {[...new Set(reasons)].map((reason) => (
        <p key={reason} role="status" className={look.notice} data-testid="tray-pick-notice">
          {pickUnavailableText(reason)}
        </p>
      ))}
    </>
  );
}

// The store's notices: the storage that keeps the tray in memory only, and a refusal by name. The line
// shows them as the full view does, so a pick refused while the tray is minimised is still named.
function StoreNotices() {
  const persistence = useTray((state) => state.persistence);
  const refusal = useTray((state) => state.refusal);
  return (
    <>
      {persistence.state === 'memory-only' ? (
        <p role="status" className={look.notice} data-testid="tray-memory-only">
          {persistence.detail}
        </p>
      ) : null}
      {refusal ? (
        <p role="alert" className={look.refusalLine} data-testid="tray-refusal">
          {refusal}{' '}
          <button type="button" className={look.textButton} onClick={() => trayStore.getState().dismissRefusal()}>
            dismiss
          </button>
        </p>
      ) : null}
    </>
  );
}

function TrayNotices() {
  return (
    <>
      <PickNotices />
      <StoreNotices />
    </>
  );
}

const keepSelection = (event: React.MouseEvent) => event.preventDefault();

function CloseButton() {
  return (
    <button
      type="button"
      className={look.close}
      aria-label="Close the AR tray; its picks stay"
      onMouseDown={keepSelection}
      onClick={() => trayStore.getState().setOpen(false)}
    >
      ×
    </button>
  );
}

// The minimised tray (rule 5, 15f92a8f): one line, "⊕ Attached · n", that a click expands. It holds
// no picks, target, message, Send, preference or log; the last Send's answer stands beside it, and
// each refusal of that Send under it, by name.
export function TrayLine() {
  const count = useTray((state) => state.picks.length);
  const outcome = useTray((state) => state.outcome);
  useTrayPaste(true);
  return (
    <section className={look.line} data-tray-root aria-label="AR tray" data-testid="tray-line">
      <div className={look.head}>
        <button
          type="button"
          className={look.lineOpen}
          aria-label={`Expand the AR tray, ${count} ${count === 1 ? 'pick' : 'picks'}`}
          onMouseDown={keepSelection}
          onClick={() => trayStore.getState().setMinimised(false)}
        >
          ⊕ Attached · {count}
        </button>
        {outcome ? <TrayOutcomeLine outcome={outcome} /> : null}
        <span className={look.spacer} />
        <CloseButton />
      </div>
      {outcome ? <TrayLineRefusals outcome={outcome} /> : null}
      <StoreNotices />
    </section>
  );
}

function TrayPicks() {
  const picks = useTray((state) => state.picks);
  if (picks.length === 0)
    return (
      <div className={look.empty} data-testid="tray-empty">
        <b>Paste a screenshot here</b> (Ctrl+V), or press ⊕ on an invariant, a block, a task or a file line, or highlight
        text and press “⊕ add”.
      </div>
    );
  return (
    <ul className={look.chips} aria-label="Picks in the tray">
      {picks.map((pick) => (
        <TrayChip key={pick.id} pick={pick} />
      ))}
    </ul>
  );
}

// Send names one chat by its role and task; its name for assistive technology keeps the agent tag.
function sendWords(targets: readonly string[], name: (agentId: string) => ChatName): { said: string; named: string } {
  if (targets.length === 0) return { said: 'Send', named: 'Send' };
  return { said: `Send to ${targetParts(targets, name).short}`, named: `Send to ${targetsLabel(targets, name)}` };
}

function SendRow() {
  const picks = useTray((state) => state.picks);
  const text = useTray((state) => state.text);
  const targets = useTray((state) => state.targets);
  const sending = useTray((state) => state.sending);
  const name = useChatNames();
  const nothing = picks.length === 0 && text.trim() === '';
  const disabled = sending || nothing || targets.length === 0;
  const why = targets.length === 0 ? 'Choose a chat to send to' : nothing ? 'Add a pick or write your text' : targetsTitle(targets, name);
  const { said, named } = sendWords(targets, name);
  // The one-message line stands on its own line above Send, never a column beside it (MIK-R188 rule
  // 5, d9a1ec8f).
  return (
    <>
      <p className={look.preview} data-testid="tray-preview">
        {previewLine(picks, text)}
      </p>
      <div className={look.sendRow}>
        <button
          type="button"
          className={look.send}
          disabled={disabled}
          title={why}
          aria-label={sending || named === said ? undefined : named}
          onClick={() => void sendNow()}
        >
          {sending ? 'Sending…' : said}
        </button>
      </div>
    </>
  );
}

// The tray's bounds (rule 11, e6ac7a3e), beside copy on highlight behind one closed disclosure. Picks
// are counted; images and messages are set in whole MiB and kept in bytes. A value that is not a
// positive whole number is not taken: the field says so until it loses focus.
const BOUND_FIELDS: readonly { name: keyof TrayBounds; label: string; unit: number }[] = [
  { name: 'maxPicks', label: 'Picks per message', unit: 1 },
  { name: 'maxImageBytes', label: 'MiB per image', unit: MIB },
  { name: 'maxMessageBytes', label: 'MiB per message', unit: MIB },
];

function BoundField({ name, label, unit }: (typeof BOUND_FIELDS)[number]) {
  const value = useTrayBounds()[name];
  const [draft, setDraft] = useState<string | null>(null);
  const fallback = String(DEFAULT_BOUNDS[name] / unit);
  return (
    <div className={look.boundRow}>
      <label className={look.boundLabel}>
        <span className={look.boundName}>{label}</span>
        <input
          type="number"
          min={1}
          step={1}
          className={look.boundInput}
          value={draft ?? String(value / unit)}
          placeholder={fallback}
          aria-invalid={draft !== null || undefined}
          onChange={(event) => {
            const whole = event.target.value.trim() === '' ? Number.NaN : Number(event.target.value);
            setDraft(trayPreferences.getState().setBound(name, whole * unit) ? null : event.target.value);
          }}
          onBlur={() => setDraft(null)}
        />
      </label>
      <span className={look.dim}>default {fallback}</span>
    </div>
  );
}

function TrayBoundsControl() {
  const [open, setOpen] = useState(false);
  const bounds = useTrayBounds();
  const changed = BOUND_FIELDS.some(({ name }) => bounds[name] !== DEFAULT_BOUNDS[name]);
  return (
    <div>
      <button type="button" className={look.textButton} aria-expanded={open} onClick={() => setOpen((now) => !now)}>
        Bounds {open ? '▾' : '▸'}
      </button>
      {open ? (
        <div role="group" aria-label="The tray's bounds" className={look.bounds}>
          {BOUND_FIELDS.map((field) => (
            <BoundField key={field.name} {...field} />
          ))}
          {changed ? (
            <button type="button" className={look.textButton} onClick={() => trayPreferences.getState().resetBounds()}>
              Back to defaults
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TrayFoot() {
  const copyOnHighlight = useCopyOnHighlight();
  const targets = useTray((state) => state.targets);
  const outcome = useTray((state) => state.outcome);
  const logged = targets.length ? targets : (outcome?.results.map((result) => result.agentId) ?? []);
  return (
    <div className={look.foot}>
      <label>
        <input
          type="checkbox"
          checked={copyOnHighlight}
          onChange={(event) => trayPreferences.getState().setCopyOnHighlight(event.target.checked)}
        />{' '}
        Copy when I highlight
      </label>
      <TrayBoundsControl />
      <TraySentLog targets={logged} refresh={outcome?.at ?? null} />
    </div>
  );
}

export function TrayPanel() {
  const count = useTray((state) => state.picks.length);
  const text = useTray((state) => state.text);
  const outcome = useTray((state) => state.outcome);
  useTrayPaste(true);
  // The seat list names the targets and the chats passages came from ("from Manager · 14:05").
  // Each opening reads it again from its first page: seats start and end while the page lives.
  useEffect(() => {
    void loadSeats();
  }, []);
  return (
    <section className={look.panel} data-tray-root aria-label="AR tray" data-testid="tray-panel">
      <div className={look.head}>
        <span className={look.headLabel}>AR tray</span>
        <span className={look.dim}>{count || 'empty'}</span>
        <span className={look.spacer} />
        <TrayTargets />
        <button
          type="button"
          className={look.minimise}
          aria-label="Minimise the AR tray"
          title="Minimise: one line with the count"
          onMouseDown={keepSelection}
          onClick={() => trayStore.getState().setMinimised(true)}
        >
          –
        </button>
        <CloseButton />
      </div>
      <TrayNotices />
      <TrayPicks />
      <textarea
        className={look.wish}
        rows={2}
        aria-label="Your message with these picks"
        placeholder="Your message with these picks"
        value={text}
        onChange={(event) => trayStore.getState().setText(event.target.value)}
      />
      <SendRow />
      {outcome ? <TrayOutcome outcome={outcome} /> : null}
      <TrayFoot />
    </section>
  );
}
