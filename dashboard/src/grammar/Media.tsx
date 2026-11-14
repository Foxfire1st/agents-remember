// The one picture/video primitive every viewer draws: the File Viewer, the diff viewer, the
// review's views of a change, the notes reader and the knowledge reader's code view. Under the
// picture or the player stand the type and the size.
import { css } from "../../styled-system/css";
import { type MediaKind, type MediaType, mediaCaption } from "../data/media";

const frame = css({
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: "0.25rem",
  padding: "0.6rem 0.8rem",
  minHeight: "0",
  maxWidth: "100%",
  overflow: "auto",
});
const picture = css({ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" });
const player = css({ maxWidth: "100%", maxHeight: "100%", background: "bg" });
const caption = css({ fontSize: "0.7rem", color: "muted", fontFamily: "mono" });
const notice = css({
  padding: "0.6rem 0.8rem",
  fontSize: "0.78rem",
  color: "muted",
});

export interface MediaViewProps {
  src: string;
  media: MediaType | null;
  kind: MediaKind;
  size: number | null | undefined;
  // A file above the bytes route's bound is stated, never requested.
  tooLarge?: boolean;
  alt?: string;
  testId?: string;
  fit?: boolean;
}

export function MediaView({
  src,
  media,
  kind,
  size,
  tooLarge = false,
  alt = "",
  testId = "media-view",
  fit = false,
}: MediaViewProps) {
  if (tooLarge) {
    const label = mediaCaption(media, size);
    return (
      <div className={notice} data-testid="media-too-large">
        {label} — too large to show
      </div>
    );
  }
  return (
    <figure className={frame} data-testid={testId} data-media-kind={kind}>
      {kind === "image" ? (
        <img
          className={picture}
          src={src}
          alt={alt}
          loading="lazy"
          referrerPolicy="no-referrer"
          data-testid="media-image"
        />
      ) : (
        <video
          className={player}
          src={src}
          controls
          preload="metadata"
          data-testid="media-video"
          style={fit ? { maxHeight: "none" } : undefined}
        >
          <track kind="captions" />
        </video>
      )}
      <figcaption className={caption} data-testid="media-caption">
        {mediaCaption(media, size)}
      </figcaption>
    </figure>
  );
}
