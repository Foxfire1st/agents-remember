import type { ReactNode } from "react";

import { css } from "../../../styled-system/css";

// Small structural vocabulary shared by the three L7 inspector panes. The panes own their data
// semantics; this file only keeps their headings, facts, raw evidence, and actions visually one
// system so the inspector does not turn into three unrelated mini-apps.

export const inspectorPane = css({
  display: "grid",
  gap: "0.65rem",
  alignContent: "start",
  minWidth: "0",
  color: "muted",
  fontSize: "0.7rem",
});

const section = css({ display: "grid", gap: "0.3rem", minWidth: "0" });
const heading = css({
  margin: "0",
  fontSize: "0.66rem",
  fontWeight: "600",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "cyan",
});
const fact = css({
  display: "grid",
  gridTemplateColumns: "minmax(5.2rem, auto) minmax(0, 1fr)",
  gap: "0.4rem",
  margin: "0",
  minWidth: "0",
  "& > dt": { color: "muted" },
  "& > dd": {
    margin: "0",
    minWidth: "0",
    overflowWrap: "anywhere",
    color: "ink",
  },
});
const note = css({
  margin: "0",
  color: "muted",
  lineHeight: "1.45",
  overflowWrap: "anywhere",
});
const raw = css({
  margin: "0",
  maxHeight: "12rem",
  overflow: "auto",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  fontSize: "0.64rem",
  color: "ink",
  padding: "0.35rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
});

export const inspectorAction = css({
  font: "inherit",
  fontSize: "0.66rem",
  width: "fit-content",
  maxWidth: "100%",
  paddingInline: "0.4rem",
  paddingBlock: "0.12rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { cursor: "not-allowed", opacity: 0.55 },
});

export function InspectorSection({
  title,
  children,
  testId,
}: {
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section className={section} data-testid={testId}>
      <h3 className={heading}>{title}</h3>
      {children}
    </section>
  );
}

export function InspectorFact({
  label,
  value,
  testId,
  title,
}: {
  label: string;
  value: ReactNode;
  testId?: string;
  title?: string;
}) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <dl className={fact}>
      <dt>{label}</dt>
      <dd data-testid={testId} title={title}>
        {value}
      </dd>
    </dl>
  );
}

export function InspectorNote({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className={note} data-testid={testId}>
      {children}
    </p>
  );
}

export function InspectorRaw({ value, testId }: { value: unknown; testId?: string }) {
  const rendered = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className={raw} data-testid={testId}>
      {rendered}
    </pre>
  );
}
