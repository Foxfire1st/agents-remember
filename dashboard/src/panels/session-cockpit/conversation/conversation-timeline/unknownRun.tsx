// The collapsed "N identical unknown vendor events" row and the two keyboard-exclusion helpers
// shared by the feed's navigation contract.
import {
  runButton,
  runHead,
  runMember,
  runRow,
  runSummary,
} from "./styles";
import type { DisplayRow } from "../collapse";

export function isEditableTarget(target: HTMLElement): boolean {
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable ||
    target.closest("button,a,[contenteditable],.cm-editor") !== null
  );
}

export function inOverflowRegion(target: HTMLElement): boolean {
  // Labeled code/diff/output scroll regions own Home/End so they scroll rather than navigate.
  return target.closest('[role="group"], pre') !== null;
}

export function UnknownVendorRun({
  row,
  expanded,
  onToggle,
}: {
  row: Extract<DisplayRow, { kind: "unknown-run" }>;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className={runRow} data-testid="unknown-vendor-run">
      <div className={runHead}>
        <span
          className={runSummary}
          title={`${row.items.length} unknown vendor events (same summary) — ${row.summary}`}
        >
          {/* "same summary", not "identical": the events share this summary but each carries its
              own distinct evidence id (they are not duplicates), so the copy must not imply sameness. */}
          {row.items.length} unknown vendor events (same summary) — {row.summary}
        </span>
        <button
          type="button"
          className={runButton}
          aria-expanded={expanded}
          onClick={onToggle}
          data-testid="unknown-vendor-run-toggle"
        >
          {expanded ? "collapse" : "show each"}
        </button>
      </div>
      {expanded ? (
        <div>
          {row.items.map((item) => (
            <div className={runMember} key={item.itemId} data-testid="unknown-vendor-run-member">
              #{item.globalOrdinal} · {item.evidenceRef ?? item.itemId}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
