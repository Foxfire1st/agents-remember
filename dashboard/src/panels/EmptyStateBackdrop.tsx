import { motion } from "motion/react";
import type { ReactNode } from "react";

import { css } from "../../styled-system/css";
import { useShouldAnimate } from "./engine-room/useShouldAnimate";

// A faint, effects-gated boomerang-video backdrop for empty-state canvases — mirrors the engine-room
// G6 backdrop (`engineRoomStyles` `backdrop`/`backdropVideo`). The clip is a pre-rendered
// forward+reverse boomerang, so `loop` is seamless (first frame == last). Absent under calm-cockpit /
// `prefers-reduced-motion` (useShouldAnimate) — the empty text then stands alone. aria-hidden +
// pointer-events:none: pure atmosphere, never state. Host container must be a flex column so the
// `flex:1` canvas fills the slot (Panel `fill`; Chats `terminalArea`). A slow 12s back-and-forth Motion
// scale yoyo (1→1.03, 6s each way, no CSS per the animation doctrine) gives the near-still clip gentle
// life on the same effects gate; scale stays ≥1 so `objectFit:cover` never reveals an edge.
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

export function EmptyStateBackdrop({ src, children }: { src: string; children: ReactNode }) {
  const animate = useShouldAnimate();
  return (
    <div className={canvas}>
      {animate ? (
        <div className={backdrop} aria-hidden="true" data-testid="empty-backdrop">
          <motion.video
            className={backdropVideo}
            src={src}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            initial={{ scale: 1 }}
            animate={{ scale: 1.03 }}
            transition={{ duration: 6, ease: "easeInOut", repeat: Infinity, repeatType: "reverse" }}
          />
        </div>
      ) : null}
      <div className={content}>{children}</div>
    </div>
  );
}
