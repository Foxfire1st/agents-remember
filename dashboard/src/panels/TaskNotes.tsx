// L9 (friction F-M): the read-only coordination-notes surface inside the task reader.
// Lists tasks/<repo>/<master>/notes/** for the doc's series — reports/ subfolders included —
// and renders one opened note as formatted markdown via grammar/Markdown, the same renderer
// the File Viewer's sidecar view uses. Task-doc reference strings that name an existing notes
// file resolve to openable links; non-matching references stay plain text. Everything here is
// GET-only component state: no store mutation, no write surface of any kind.
import { useEffect, useState } from "react";

import { css, cva } from "../../styled-system/css";
import {
  listNotes,
  readNote,
  resolveNoteReference,
  type NoteContent,
  type NotesListing,
} from "../data/notes";
import { Markdown } from "../grammar/Markdown";

const section = css({ display: "grid", gap: "0.3rem" });
const heading = css({
  margin: "0",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "amber",
});
const bullets = css({
  margin: "0",
  paddingLeft: "1.1rem",
  maxWidth: "78ch",
  display: "grid",
  gap: "0.2rem",
  fontSize: "0.84rem",
  lineHeight: "1.45",
});
// A resolved reference: the whole reference string becomes the openable link into the notes view.
const refLink = css({
  font: "inherit",
  textAlign: "left",
  color: "cyan",
  background: "transparent",
  border: "0",
  padding: "0",
  cursor: "pointer",
  textDecoration: "underline",
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const noteList = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.15rem" });
const noteRow = cva({
  base: {
    display: "flex",
    justifyContent: "space-between",
    gap: "0.5rem",
    width: "100%",
    textAlign: "left",
    font: "inherit",
    fontSize: "0.78rem",
    paddingInline: "0.4rem",
    paddingBlock: "0.2rem",
    background: "bg",
    border: "0",
    borderLeftWidth: "2px",
    borderLeftStyle: "solid",
    borderLeftColor: "grid",
    color: "ink",
    cursor: "pointer",
    _hover: { background: "oklch(0.7 0.1 200 / 0.12)", borderLeftColor: "amber" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  },
  variants: { open: { true: { borderLeftColor: "cyan", color: "cyan" } } },
});
const noteMeta = css({ color: "muted", fontSize: "0.72rem" });
const listCapHint = css({ color: "muted", fontSize: "0.72rem" });
// The opened note: a bounded scroll box so a long report never swallows the detail panel —
// the markdown inside is the File Viewer sidecar treatment.
const noteBox = css({
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  background: "bgPanel",
  maxHeight: "26rem",
  overflow: "auto",
});
const noteHead = css({
  position: "sticky",
  top: "0",
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: "0.5rem",
  paddingInline: "0.6rem",
  paddingBlock: "0.25rem",
  fontSize: "0.74rem",
  fontFamily: "mono",
  color: "cyan",
  background: "bgPanel",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const noteClose = css({
  font: "inherit",
  fontSize: "0.74rem",
  color: "muted",
  background: "transparent",
  border: "0",
  padding: "0",
  cursor: "pointer",
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
const noteBody = css({ padding: "0.6rem 0.8rem" });
const notePlain = css({
  margin: "0",
  padding: "0.6rem 0.8rem",
  fontSize: "0.78rem",
  lineHeight: "1.45",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
});
const notePlaceholder = css({ padding: "0.8rem", color: "muted", fontSize: "0.8rem" });
const truncatedBanner = css({
  paddingInline: "0.6rem",
  paddingBlock: "0.2rem",
  fontSize: "0.72rem",
  color: "amber",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});

// The series' notes surface + the doc's reference list, sharing one notes state so a resolved
// reference opens straight into the notes view. An unreachable notes API (or a series without a
// notes/ folder — the server answers an empty list) simply renders no notes section; references
// then all stay plain text.
export function TaskNotes({
  repo,
  master,
  references,
}: {
  repo: string;
  master: string;
  references: string[];
}) {
  const [listing, setListing] = useState<NotesListing | null>(null);
  const [openPath, setOpenPath] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setListing(null);
    setOpenPath(null);
    void listNotes(repo, master).then(
      (data) => {
        if (live) setListing(data);
      },
      () => {}, // unreachable API: no notes surface, references stay plain text
    );
    return () => {
      live = false;
    };
  }, [repo, master]);

  const notes = listing?.notes ?? [];
  const notePaths = notes.map((note) => note.path);
  const toggle = (path: string) => setOpenPath((current) => (current === path ? null : path));

  return (
    <>
      {references.length > 0 ? (
        <section className={section}>
          <h3 className={heading}>References</h3>
          <ul className={bullets}>
            {references.map((reference, index) => {
              const target = resolveNoteReference(reference, notePaths);
              return (
                <li key={reference}>
                  {target ? (
                    <button
                      type="button"
                      className={refLink}
                      onClick={() => setOpenPath(target)}
                      data-testid={`note-ref-${index + 1}`}
                      title={`open notes/${target}`}
                    >
                      <Markdown inline>{reference}</Markdown>
                    </button>
                  ) : (
                    <Markdown inline>{reference}</Markdown>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {notes.length > 0 ? (
        <section className={section}>
          <h3 className={heading}>Series notes</h3>
          <ul className={noteList}>
            {notes.map((note, index) => (
              <li key={note.path}>
                <button
                  type="button"
                  className={noteRow({ open: note.path === openPath })}
                  onClick={() => toggle(note.path)}
                  data-testid={`note-open-${index + 1}`}
                >
                  <span>{note.path}</span>
                  <span className={noteMeta}>{note.size.toLocaleString()} B</span>
                </button>
              </li>
            ))}
          </ul>
          {listing?.truncated ? (
            <span className={listCapHint}>deeper subfolders exist but are beyond the list cap</span>
          ) : null}
          {openPath ? (
            <NoteReader
              repo={repo}
              master={master}
              path={openPath}
              onClose={() => setOpenPath(null)}
            />
          ) : null}
        </section>
      ) : null}
    </>
  );
}

// One opened note, fetched on demand: markdown renders formatted (the sidecar treatment), other
// text renders as plain preformatted lines, a binary file degrades to a placeholder — the
// download-or-skip posture, never a crash and never raw bytes.
function NoteReader({
  repo,
  master,
  path,
  onClose,
}: {
  repo: string;
  master: string;
  path: string;
  onClose: () => void;
}) {
  const [note, setNote] = useState<NoteContent | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setNote(null);
    setFailed(false);
    void readNote(repo, master, path).then(
      (data) => {
        if (live) setNote(data);
      },
      () => {
        if (live) setFailed(true);
      },
    );
    return () => {
      live = false;
    };
  }, [repo, master, path]);

  return (
    <div className={noteBox} data-testid="note-view">
      <div className={noteHead}>
        <span>notes/{path}</span>
        <button type="button" className={noteClose} onClick={onClose} data-testid="note-close">
          × close
        </button>
      </div>
      {note?.truncated ? (
        <div className={truncatedBanner}>
          Showing the first 2 MiB of {note.size.toLocaleString()} bytes
        </div>
      ) : null}
      {failed ? (
        <div className={notePlaceholder}>Could not load this note.</div>
      ) : note === null ? (
        <div className={notePlaceholder}>Loading…</div>
      ) : note.language === "binary" ? (
        <div className={notePlaceholder}>
          Binary file — {note.size.toLocaleString()} bytes (not shown)
        </div>
      ) : note.language === "markdown" ? (
        <div className={noteBody}>
          <Markdown>{note.content}</Markdown>
        </div>
      ) : (
        <pre className={notePlain}>{note.content}</pre>
      )}
    </div>
  );
}
