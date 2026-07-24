import { useEffect, useRef, type ReactNode } from "react";

import { css } from "../../styled-system/css";
import { useElementVisible } from "./engine-room/useElementVisible";
import { useShouldAnimate } from "./engine-room/useShouldAnimate";

// A faint, effects-gated boomerang-video backdrop for empty-state canvases — mirrors the engine-room
// G6 backdrop (`engineRoomStyles` `backdrop`/`backdropVideo`). The clip is a pre-rendered
// forward+reverse boomerang, so `loop` is seamless (first frame == last). Absent under calm-cockpit /
// `prefers-reduced-motion` (useShouldAnimate) — the empty text then stands alone. aria-hidden +
// pointer-events:none: pure atmosphere, never state. Host container must be a flex column so the
// `flex:1` canvas fills the slot (Panel `fill`; Chats `terminalArea`). Any slow zoom is baked into the
// 60fps boomerang assets themselves; this component keeps the browser layer static.
const canvas = css({
  position: "relative",
  flex: "1",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  overflow: "hidden",
  minWidth: "0",
  minHeight: "0",
  textAlign: "center",
});
const backdrop = css({
  position: "absolute",
  inset: "0",
  zIndex: "0",
  pointerEvents: "none",
  overflow: "hidden",
});
const backdropVideo = css({
  display: "block",
  width: "100%",
  height: "100%",
  objectFit: "cover",
  opacity: "0.14",
  filter: "grayscale(1) sepia(1) saturate(2.6) hue-rotate(6deg) brightness(0.85) contrast(1.05)",
  mixBlendMode: "screen",
  maskImage: "radial-gradient(ellipse at center, #000 42%, transparent 100%)",
  WebkitMaskImage: "radial-gradient(ellipse at center, #000 42%, transparent 100%)",
});
const content = css({
  position: "relative",
  zIndex: "1",
  maxWidth: "34rem",
  paddingInline: "1rem",
  color: "muted",
  fontSize: "0.82rem",
});

export function EmptyStateBackdrop({
  src,
  children,
  opacity,
}: {
  src: string;
  children: ReactNode;
  // Override the default faint 0.14 wash (set in `backdropVideo`) when a particular clip reads darker
  // and needs a touch more presence — e.g. the File/Diff viewer's siege-tank loop.
  opacity?: number;
}) {
  const animate = useShouldAnimate();
  // Pause the decode while an ancestor cockpit layer is display:none (the DetailPanel empty state is
  // kept mounted, never unmounted — its battlecruiser loop kept decoding on the Engine Room tab).
  // autoPlay still covers the first visible mount; this tracks later hide/show flips.
  const canvasRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const visible = useElementVisible(canvasRef);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (visible) void video.play()?.catch(() => {});
    else video.pause();
  }, [visible, animate]);

  return (
    <div className={canvas} ref={canvasRef}>
      {animate ? (
        <div className={backdrop} aria-hidden="true" data-testid="empty-backdrop">
          <video
            ref={videoRef}
            className={backdropVideo}
            style={opacity != null ? { opacity } : undefined}
            src={src}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
          />
        </div>
      ) : null}
      <div className={content}>{children}</div>
    </div>
  );
}
