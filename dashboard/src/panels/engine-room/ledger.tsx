// The memory.md ledger popover + its warp-coupler trigger: the lookup table binding this side's
// code commit to its memory commit across the two physically distinct repos.
import { useRef, useState } from "react";
import { Dialog, Popover } from "react-aria-components";
import { motion } from "motion/react";

import { useShouldAnimate } from "./useShouldAnimate";
import {
  ledgerButton,
  ledgerButtonLabel,
  ledgerCard,
  ledgerCardHead,
  ledgerDate,
  ledgerHashCode,
  ledgerHashMem,
  ledgerMore,
  ledgerMsg,
  ledgerRowCss,
  ledgerScroll,
  ledgerSeam,
  ledgerShowMore,
  ledgerTable,
  warpCouplerBar,
  warpCouplerLabel,
  warpLinkGlyph,
  warpSurge,
} from "./styles";
import { LEDGER_PREVIEW, compactDate, short } from "./geometry";
import type { LedgerRefNode } from "../../types/projection";

function LedgerTable({ rows, total, currentCode }: {
  rows: LedgerRefNode[];
  total: number;
  currentCode?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  // prefix-tolerant: the ledger holds full 40-char SHAs, the node commit may be a short prefix
  const isCurrent = (code: string): boolean =>
    !!currentCode && (code.startsWith(currentCode) || currentCode.startsWith(code));
  const shown = expanded ? rows : rows.slice(0, LEDGER_PREVIEW);
  const hiddenServed = rows.length - shown.length; // served rows the collapsed view is hiding
  const beyondWindow = total - rows.length; // older rows that live only in the file
  return (
    <div className={ledgerCard} data-testid="ledger-popover">
      <div className={ledgerCardHead}>memory.md ledger · code ⇄ memory</div>
      <div className={ledgerScroll({ expanded })}>
        <table className={ledgerTable}>
          <tbody>
            {shown.map((row) => {
              const current = isCurrent(row.codeCommit);
              return (
                <tr
                  key={`${row.codeCommit}-${row.memoryCommit}`}
                  className={ledgerRowCss({ current })}
                  data-current={current || undefined}
                >
                  {/* 6 columns (Tier 2): date | message | code-hash ⇄ memory-hash | message | date —
                      hashes meet the centre seam, message + date-time fan outward per side. Messages
                      truncate (full text in `title`); an unprobed side shows an empty message/date cell. */}
                  <td className={ledgerDate}>{compactDate(row.codeDate)}</td>
                  <td className={ledgerMsg} title={row.codeSubject}>{row.codeSubject ?? ""}</td>
                  <td className={ledgerHashCode} title={row.codeCommit}>{short(row.codeCommit)}</td>
                  <td className={ledgerSeam} aria-hidden="true">⇄</td>
                  <td className={ledgerHashMem} title={row.memoryCommit}>{short(row.memoryCommit)}</td>
                  <td className={ledgerMsg} title={row.memorySubject}>{row.memorySubject ?? ""}</td>
                  <td className={ledgerDate}>{compactDate(row.memoryDate)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {hiddenServed > 0 ? (
        <button
          type="button"
          className={ledgerShowMore}
          data-testid="ledger-show-more"
          onClick={() => setExpanded(true)}
        >
          ▾ show {hiddenServed} more
        </button>
      ) : beyondWindow > 0 ? (
        <div className={ledgerMore}>+{beyondWindow} more in memory.md</div>
      ) : null}
    </div>
  );
}

function WarpSurgeBands({ x, cy }: { x: number; cy: number }) {
  return (
    <>
      <line className={warpSurge} data-dir="up" data-testid="warp-surge" x1={x} y1={cy - 26} x2={x} y2={cy - 4} />
      <line className={warpSurge} data-dir="down" data-testid="warp-surge" x1={x} y1={cy + 26} x2={x} y2={cy + 4} />
    </>
  );
}

function LedgerLinkButton({
  x,
  cy,
  triggerRef,
  label,
  ledgerRows,
  total,
  testid,
  onToggle,
}: {
  x: number;
  cy: number;
  triggerRef: React.RefObject<SVGRectElement | null>;
  label: string | undefined;
  ledgerRows: LedgerRefNode[];
  total: number;
  testid: string;
  onToggle: () => void;
}) {
  return (
    <>
      <rect
        ref={triggerRef}
        className={ledgerButton}
        x={x + 20}
        y={cy - 10}
        width={140}
        height={20}
        rx={4}
        role="button"
        tabIndex={0}
        aria-label={`open memory.md ledger — ${ledgerRows.length} of ${total} rows`}
        data-testid={`${testid}-ledger`}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
      />
      <text className={ledgerButtonLabel} x={x + 27} y={cy + 4}>{label ?? "ledger"} ▾</text>
    </>
  );
}

function couplerOpacity(visible: boolean, bound: boolean): number {
  return visible ? (bound ? 1 : 0.3) : 0;
}

function LedgerPopover({
  open,
  onOpenChange,
  anchorRef,
  rows,
  total,
  currentCode,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anchorRef: React.RefObject<SVGRectElement | null>;
  rows: LedgerRefNode[];
  total: number;
  currentCode: string | undefined;
}) {
  return (
    <Popover triggerRef={anchorRef} isOpen={open} onOpenChange={onOpenChange} placement="bottom" shouldFlip={false}>
      <Dialog aria-label="memory.md ledger lookup table">
        <LedgerTable rows={rows} total={total} currentCode={currentCode} />
      </Dialog>
    </Popover>
  );
}

function CouplerLabel({
  x,
  cy,
  hasLedger,
  label,
  triggerRef,
  ledgerRows,
  total,
  testid,
  onToggle,
}: {
  x: number;
  cy: number;
  hasLedger: boolean;
  label: string | undefined;
  triggerRef: React.RefObject<SVGRectElement | null>;
  ledgerRows: LedgerRefNode[];
  total: number;
  testid: string;
  onToggle: () => void;
}) {
  if (hasLedger) {
    return (
      <LedgerLinkButton
        x={x}
        cy={cy}
        triggerRef={triggerRef}
        label={label}
        ledgerRows={ledgerRows}
        total={total}
        testid={testid}
        onToggle={onToggle}
      />
    );
  }
  return label ? <text className={warpCouplerLabel} x={x + 13} y={cy + 4}>{label}</text> : null;
}

export function WarpCoupler({ x, bound, label, testid = "warp-coupler", rows, total = 0, currentCode, visible = true }: {
  x: number;
  bound: boolean;
  label?: string;
  testid?: string;
  rows?: LedgerRefNode[];
  total?: number;
  currentCode?: string;
  visible?: boolean;
}) {
  const animate = useShouldAnimate();
  const cy = 342;
  const triggerRef = useRef<SVGRectElement>(null);
  // the popover anchors to this invisible point HIGH in the scene (SVG coords → scales with the canvas),
  // not the coupler button, so it opens in its old upper position and grows DOWNWARD from there (the
  // coupler button stays the click trigger).
  const anchorRef = useRef<SVGRectElement>(null);
  const [open, setOpen] = useState(false);
  const ledgerRows = rows ?? [];
  const hasLedger = ledgerRows.length > 0; // a ledger-backed coupler opens its memory.md lookup table
  // Motion owns the coupler group's opacity: the bound dim (1 vs 0.3) AND the build-up `visible` gate (the
  // worktree coupler only appears once the memory worktree materialises) — one owner, no double-drive with
  // CSS. The warp-core surge bands are GSAP (data-fx='surge' + data-dir), driven by useEngineTimeline.
  const opacity = couplerOpacity(visible, bound);
  return (
    <>
      <motion.g
        data-testid={testid}
        data-bound={bound}
        initial={animate ? { opacity } : false}
        animate={{ opacity }}
        transition={{ duration: animate ? 0.45 : 0 }}
      >
        <line className={warpCouplerBar} x1={x} y1={312} x2={x} y2={372} />
        {/* invisible high anchor for the popover (upper position) — see anchorRef note above */}
        <rect ref={anchorRef} x={x + 90} y={58} width={1} height={1} fill="none" pointerEvents="none" aria-hidden="true" />
        {bound ? <WarpSurgeBands x={x} cy={cy} /> : null}
        {/* the ledger link icon — a drawn chain-link (two interlocking rings), not the contract node */}
        <g className={warpLinkGlyph} aria-hidden="true" data-testid="warp-link">
          <ellipse cx={x} cy={cy - 3} rx={5} ry={4} />
          <ellipse cx={x} cy={cy + 3} rx={5} ry={4} />
        </g>
        {/* the coupler label: a ledger-backed coupler renders it as a clickable BUTTON (rect + label + a ▾
            "open" caret) beside the link glyph, opening the memory.md popover; otherwise a plain label.
            A <button> can't live in svg, so the rect is the trigger and the label text sits on top. */}
        <CouplerLabel
          x={x}
          cy={cy}
          hasLedger={hasLedger}
          label={label}
          triggerRef={triggerRef}
          ledgerRows={ledgerRows}
          total={total}
          testid={testid}
          onToggle={() => setOpen((value) => !value)}
        />
      </motion.g>
      {/* anchored to the high anchorRef (not the coupler) so it sits in its old upper position and grows
          DOWNWARD as it expands; shouldFlip=false keeps it from flipping up when the tall window meets the
          viewport edge (the inner scroll covers it) */}
      {hasLedger ? (
        <LedgerPopover
          open={open}
          onOpenChange={setOpen}
          anchorRef={anchorRef}
          rows={ledgerRows}
          total={total}
          currentCode={currentCode}
        />
      ) : null}
    </>
  );
}
