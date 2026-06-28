import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Dialog, Popover, TextArea, TextField, ToggleButton, ToggleButtonGroup } from "react-aria-components";

import { css, cx } from "../../styled-system/css";
import { useSelectionCapture } from "../data/selection";
import {
  createSession,
  deliverToSession,
  sessionStore,
  useSessions,
  type DeliveryStatus,
} from "../data/sessions";
import { fetchHarnesses, uploadSessionImage, type HarnessInfo } from "../data/terminal";

// Slice 6f — "send a context package by highlighting". Two stages, like a code editor's selection
// toolbar: (1) selecting cockpit content (on mouse-up) raises a small **"Add to chat"** pill anchored
// to it; (2) clicking the pill opens the composer box, which stays open until the operator clicks
// outside it or Sends. The composer lives on a *snapshot* of the selection (`useSelectionCapture`), so
// clicking into the message box never dismisses it. A selection only ever *raises* the pill; nothing
// reaches an agent until Send (the no-silent-action invariant). The target control offers the open
// chats **and** a create option per detected harness (so a new chat is an agent, not a shell into the
// void). Delivered as a bracketed paste + a trailing Enter over the live B2 `{type:stdin}` channel.
const popover = css({ maxWidth: "min(32rem, 94vw)" });
const dialog = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.45rem",
  padding: "0.55rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "4px",
  boxShadow: "0 6px 24px rgba(0,0,0,0.5)",
  outline: "none",
});
// Stage 1: the quiet pill — a grid-bordered rounded bar, content-sized, amber only on the action.
const dialogPill = css({ padding: "0.2rem", borderRadius: "999px", borderColor: "grid" });
// Stage 2: a predictable, fixed-width box (so it never tracks the selection's width).
const dialogComposer = css({ width: "min(27rem, 92vw)" });
const addButton = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.4rem",
  font: "inherit",
  fontSize: "0.74rem",
  letterSpacing: "0.02em",
  paddingInline: "0.7rem",
  paddingBlock: "0.32rem",
  borderRadius: "999px",
  border: "none",
  background: "transparent",
  color: "text",
  cursor: "pointer",
  _hover: { color: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const chatIcon = css({ flexShrink: 0, display: "block" });
const preview = css({
  margin: "0",
  maxHeight: "6.5rem",
  overflowY: "auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  font: "inherit",
  fontSize: "0.72rem",
  color: "muted",
  paddingInline: "0.4rem",
  paddingBlock: "0.3rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
});
const targetRow = css({ display: "flex", alignItems: "baseline", gap: "0.4rem", flexWrap: "wrap" });
const targetLabel = css({ fontSize: "0.68rem", letterSpacing: "0.04em", color: "muted", flexShrink: 0 });
const toggleGroup = css({ display: "flex", gap: "0.25rem", flexWrap: "wrap" });
const toggle = css({
  font: "inherit",
  fontSize: "0.7rem",
  paddingInline: "0.45rem",
  paddingBlock: "0.15rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  color: "muted",
  background: "transparent",
  cursor: "pointer",
  _selected: { borderColor: "amber", color: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const field = css({ display: "flex" });
const area = css({
  font: "inherit",
  fontSize: "0.82rem",
  lineHeight: "1.4",
  color: "inherit",
  width: "100%",
  resize: "vertical",
  minHeight: "5rem",
  maxHeight: "20rem",
  paddingInline: "0.55rem",
  paddingBlock: "0.45rem",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const sendButton = css({
  font: "inherit",
  fontSize: "0.74rem",
  letterSpacing: "0.04em",
  alignSelf: "flex-end",
  paddingInline: "0.9rem",
  paddingBlock: "0.32rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "rgba(232, 193, 112, 0.1)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { opacity: 0.6, cursor: "default" },
});
// A pasted screenshot: a small thumbnail + a remove affordance, shown above the message box.
const imageRow = css({ display: "flex", alignItems: "center", gap: "0.5rem" });
const thumb = css({
  maxHeight: "3rem",
  maxWidth: "5rem",
  objectFit: "cover",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
});
const removeImage = css({
  font: "inherit",
  fontSize: "0.7rem",
  color: "muted",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  _hover: { color: "amber" },
});
const statusNote = css({ fontSize: "0.68rem", color: "amber", alignSelf: "flex-end" });

function ChatIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" className={chatIcon}>
      <path
        d="M2 3.2h12v7.2H6.4L3.6 13V10.4H2z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}

type Target =
  | { key: string; kind: "session"; id: string; label: string }
  | { key: string; kind: "create"; harnessId?: string; prefix: string; label: string };

export function HighlightComposer({
  selectedLifecycleId,
  onSent,
}: {
  selectedLifecycleId?: string;
  onSent?: () => void;
}) {
  const { selection, clear } = useSelectionCapture();
  const sessions = useSessions((state) => state.sessions);
  const activeId = useSessions((state) => state.activeId);
  const [harnesses, setHarnesses] = useState<HarnessInfo[]>([]);
  const [mode, setMode] = useState<"pill" | "composer">("pill");
  const [message, setMessage] = useState("");
  const [targetKey, setTargetKey] = useState<string | null>(null);
  const [image, setImage] = useState<File | null>(null);
  const [status, setStatus] = useState<"sending" | "unconfirmed" | null>(null);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const sendingRef = useRef(false); // synchronous in-flight guard — `status` state is stale within a tick
  // The resolved delivery target, captured on the first Send so a Retry re-delivers to the SAME session
  // (and reuses the uploaded image) instead of creating a new chat / re-uploading each attempt.
  const deliveryRef = useRef<{ id: string; imagePath: string | null } | null>(null);

  // Detected harnesses become "new chat" options so a created chat is an agent, not a shell. Fetched
  // once — the composer stays mounted in CockpitShell (it just renders null without a selection).
  useEffect(() => {
    let active = true;
    void fetchHarnesses().then((list) => {
      if (active) setHarnesses(list);
    });
    return () => {
      active = false;
    };
  }, []);

  // A fresh capture starts back at the pill (the composer is opened by an explicit click).
  useEffect(() => {
    setMode("pill");
    setMessage("");
    setImage(null);
    setStatus(null);
    sendingRef.current = false;
    deliveryRef.current = null;
  }, [selection]);

  // Preview a pasted screenshot; revoke the object URL when it changes or the composer unmounts.
  const imagePreviewUrl = useMemo(() => (image ? URL.createObjectURL(image) : null), [image]);
  useEffect(
    () => () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    },
    [imagePreviewUrl],
  );

  if (!selection) return null;

  // Targets: every open chat, then a create option per detected harness, then a plain shell.
  const routedSessions = selectedLifecycleId
    ? sessions.filter((session) => session.lifecycleId === selectedLifecycleId)
    : sessions;
  const targets: Target[] = [
    ...routedSessions.map((s): Target => ({ key: `s:${s.id}`, kind: "session", id: s.id, label: s.label })),
    ...harnesses
      .filter((h) => h.detected)
      .map((h): Target => ({ key: `c:${h.id}`, kind: "create", harnessId: h.id, prefix: h.name, label: `＋ ${h.name}` })),
    { key: "c:terminal", kind: "create", prefix: "Terminal", label: "＋ Terminal" },
  ];
  // Default target: the active chat, else the first open chat, else the first create option (an agent
  // when one is detected) — so Enter always has somewhere to send, and never silently picks a shell.
  const defaultKey =
    (activeId && routedSessions.some((session) => session.id === activeId) && `s:${activeId}`) ||
    (routedSessions[0] && `s:${routedSessions[0].id}`) ||
    targets.find((t) => t.kind === "create")?.key ||
    null;
  const selectedKey = targets.find((t) => t.key === targetKey) ? targetKey : defaultKey;
  const selected = targets.find((t) => t.key === selectedKey) ?? null;

  const dismiss = () => {
    clear();
    setMode("pill");
  };

  const buildPackage = (imagePath?: string | null): string => {
    const note = message.trim();
    const parts = note ? [note, ""] : [];
    parts.push("--- from the dashboard ---", selection.text);
    // Carry an image by path: Claude Code auto-attaches an on-disk image path it finds in the message.
    // Follow-up (deferred): if the session cwd contains a space, the path auto-attach may split on it;
    // paths here are space-free, the robust fix is a space-free pastes root (tracked, non-blocking).
    if (imagePath) parts.push("", `Screenshot for context: ${imagePath}`);
    return parts.join("\n");
  };

  const finish = () => {
    deliveryRef.current = null;
    sendingRef.current = false;
    dismiss();
    onSent?.();
  };

  // Resolve the target session (creating one on demand), upload any pasted image, then deliver and
  // confirm the submit. The composer stays open showing "Sending…" until delivery is confirmed — nothing
  // is dropped silently; on success it dismisses + switches to Chats, on failure it offers a Retry that
  // re-delivers to the SAME session (and reuses the uploaded image) rather than re-creating it.
  const send = async () => {
    if (!selected || sendingRef.current) return; // ref guard covers the keyboard-Enter path (state is stale)
    sendingRef.current = true;
    setStatus("sending");
    try {
      let ctx = deliveryRef.current;
      if (!ctx) {
        let id: string;
        if (selected.kind === "session") {
          sessionStore.getState().setActive(selected.id);
          id = selected.id;
        } else {
          id = selectedLifecycleId
            ? await createSession(
                selected.prefix,
                selected.harnessId ? "harness" : "terminal",
                selected.harnessId,
                selectedLifecycleId,
              )
            : await createSession(selected.prefix, selected.harnessId ? "harness" : "terminal", selected.harnessId);
        }
        ctx = { id, imagePath: null };
        deliveryRef.current = ctx; // capture now so a Retry reuses this session, never re-creates it
      }
      if (image && !ctx.imagePath) {
        const path = await uploadSessionImage(ctx.id, image);
        if (!path) {
          setStatus("unconfirmed"); // upload failed — Retry reuses ctx.id and re-attempts the upload
          return;
        }
        ctx.imagePath = path;
      }
      const result: DeliveryStatus = await deliverToSession(ctx.id, buildPackage(ctx.imagePath));
      if (result === "delivered") finish();
      else setStatus("unconfirmed");
    } finally {
      sendingRef.current = false;
    }
  };

  return (
    <>
      <span
        ref={anchorRef}
        aria-hidden="true"
        style={{
          position: "fixed",
          left: selection.rect.left,
          top: selection.rect.top,
          width: selection.rect.width,
          height: selection.rect.height,
          pointerEvents: "none",
        }}
      />
      <Popover
        triggerRef={anchorRef}
        isOpen
        onOpenChange={(isOpen) => {
          if (!isOpen) dismiss();
        }}
        placement="top"
        offset={8}
        className={popover}
      >
        <Dialog
          aria-label="Send selection to a session"
          className={cx(dialog, mode === "pill" ? dialogPill : dialogComposer)}
          data-highlight-composer=""
          data-testid="highlight-composer"
        >
          {mode === "pill" ? (
            <Button className={addButton} onPress={() => setMode("composer")} data-testid="highlight-add-to-chat">
              <ChatIcon />
              Add to chat
            </Button>
          ) : (
            <>
              <pre className={preview}>{selection.text}</pre>
              {imagePreviewUrl ? (
                <div className={imageRow} data-testid="highlight-image">
                  <img src={imagePreviewUrl} alt="Pasted screenshot preview" className={thumb} />
                  <button type="button" className={removeImage} onClick={() => setImage(null)}>
                    remove image
                  </button>
                </div>
              ) : null}
              <div className={targetRow}>
                <span className={targetLabel}>Send to</span>
                <ToggleButtonGroup
                  className={toggleGroup}
                  selectionMode="single"
                  selectedKeys={selectedKey ? [selectedKey] : []}
                  onSelectionChange={(keys) => {
                    const key = [...keys][0];
                    if (typeof key === "string") setTargetKey(key);
                  }}
                >
                  {targets.map((target) => (
                    <ToggleButton
                      key={target.key}
                      id={target.key}
                      className={toggle}
                      data-testid={`highlight-target-${target.key}`}
                    >
                      {target.label}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </div>
              <TextField
                className={field}
                aria-label="Message to send with the selection"
                value={message}
                onChange={setMessage}
                autoFocus
              >
                <TextArea
                  className={area}
                  placeholder="Add a message…  (Enter sends · Shift+Enter = newline · paste an image)"
                  onPaste={(event) => {
                    const file = Array.from(event.clipboardData?.files ?? []).find((f) =>
                      f.type.startsWith("image/"),
                    );
                    if (file) {
                      event.preventDefault();
                      setImage(file);
                    }
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                />
              </TextField>
              {status ? (
                <span
                  className={statusNote}
                  role={status === "unconfirmed" ? "alert" : "status"}
                  data-testid="highlight-status"
                >
                  {status === "sending"
                    ? "Sending…"
                    : "Couldn't confirm delivery — the message may not have sent. Retry?"}
                </span>
              ) : null}
              <Button
                className={sendButton}
                onPress={() => void send()}
                isDisabled={status === "sending"}
                data-testid="highlight-send"
              >
                {status === "sending" ? "Sending…" : status === "unconfirmed" ? "Retry" : "Send"}
              </Button>
            </>
          )}
        </Dialog>
      </Popover>
    </>
  );
}
