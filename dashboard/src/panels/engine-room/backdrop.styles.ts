// Engine Room atmospheric backdrop tokens: the faint blueprint video and the scene content layer.
import { css } from "../../../styled-system/css";

export const backdrop = css({ position: "absolute", inset: "0", zIndex: "0", pointerEvents: "none", overflow: "hidden" });
export const backdropVideo = css({
  width: "100%",
  height: "100%",
  objectFit: "cover",
  opacity: "0.14",
  filter: "grayscale(1) sepia(1) saturate(2.6) hue-rotate(6deg) brightness(0.85) contrast(1.05)",
  mixBlendMode: "screen",
  // Vignette the video edges only (a radial mask): with the `screen` blend, the faded edges fall back
  // to the dark stage, concentrating the boomerang in the centre. Scoped to the <video>, so the SVG
  // scene layered above (`stageContent`, a higher z-index) is untouched.
  maskImage: "radial-gradient(ellipse at center, #000 42%, transparent 100%)",
  WebkitMaskImage: "radial-gradient(ellipse at center, #000 42%, transparent 100%)",
});
// The scene content sits in its own layer above the backdrop.
export const stageContent = css({
  position: "relative",
  zIndex: "1",
  display: "flex",
  flexDirection: "column",
  gap: "0.45rem",
  flex: "1",
  minWidth: "0",
  minHeight: "0",
});
