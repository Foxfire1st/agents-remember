/** Bounded linear DOM inventory for prospective explainer loads (report-only). */

const MAX_INVENTORY_NODES = 20000;

function emptyIncompleteInventory() {
  return {
    incomplete: true,
    figures: {
      total: 0,
      byKind: { svg: 0, canvas: 0, driven: 0 },
      byTechnique: {},
    },
    codeExcerpts: {
      highlighted: 0,
      plain: 0,
    },
  };
}

export async function collectDomInventory(page) {
  try {
    return await page.evaluate((maxNodes) => {
      try {
        const CONTROL_ROLES = new Set(["button", "tab", "switch", "checkbox", "radio", "slider"]);
        const normalizeTechnique = (raw) =>
          String(raw || "")
            .trim()
            .toLowerCase()
            .replace(/\s+/g, "-");

        const rootNode = document.body || document.documentElement;
        if (!rootNode) {
          return {
            figures: {
              total: 0,
              byKind: { svg: 0, canvas: 0, driven: 0 },
              byTechnique: {},
            },
            codeExcerpts: { highlighted: 0, plain: 0 },
          };
        }

        // Single linear iterative DFS with a node-visit cap for O(N) figure and code inventory.
        const nodes = [];
        const parentIdx = [];
        const tags = [];
        const hasCodeDesc = [];
        const hasColorSpanDesc = [];
        const hasShikiMarker = [];
        const hasCodeExcerptAttr = [];
        const codeExcerptAttr = [];
        const isCodeLineRow = [];
        const isCodeLinesBox = [];

        const figureMap = new Map();
        const seenFigureIds = new Set();
        const ensureFigure = (ownerIdx) => {
          let rec = figureMap.get(ownerIdx);
          if (rec) return rec;
          const el = nodes[ownerIdx];
          const id = el && typeof el.id === "string" ? el.id.trim() : "";
          if (!id || seenFigureIds.has(id)) return null;
          seenFigureIds.add(id);
          rec = {
            svg: false,
            canvas: false,
            driven: false,
            ownTechnique: "",
            nestedTechniques: new Set(),
          };
          figureMap.set(ownerIdx, rec);
          return rec;
        };

        let incomplete = false;
        const stack = [
          {
            el: rootNode,
            parent: -1,
            nearestExplicit: -1,
            nearestContainer: -1,
            insideSvg: false,
          },
        ];

        while (stack.length > 0) {
          if (nodes.length >= maxNodes) {
            incomplete = true;
            break;
          }
          const frame = stack.pop();
          const el = frame.el;
          if (!el || el.nodeType !== 1) continue;

          const idx = nodes.length;
          const tag = el.tagName ? el.tagName.toLowerCase() : "";
          const id = typeof el.id === "string" ? el.id.trim() : "";
          const isExcludedRoot = tag === "html" || tag === "body" || tag === "main";
          const hasUsableId = Boolean(id) && !isExcludedRoot;

          const isExplicitFigure =
            hasUsableId &&
            (tag === "figure" ||
              el.hasAttribute("data-figure") ||
              el.hasAttribute("data-technique"));
          const isSvg = tag === "svg";
          const isCanvas = tag === "canvas";
          const isVideo = tag === "video";
          const isMagicMove =
            tag === "shiki-magic-move" ||
            (el.classList && el.classList.contains("shiki-magic-move-container"));
          const isSvgImg =
            tag === "img" && /\.svg(?:[?#]|$)/i.test(el.getAttribute("src") || "");
          const role = (el.getAttribute("role") || "").toLowerCase();
          const isControl =
            (tag === "button" ||
              tag === "select" ||
              tag === "textarea" ||
              (tag === "input" && (el.getAttribute("type") || "").toLowerCase() !== "hidden") ||
              CONTROL_ROLES.has(role)) &&
            !el.hasAttribute("data-evidence") &&
            !el.hasAttribute("data-evidence-claim") &&
            !el.hasAttribute("data-claim");

          const isIdContainer =
            hasUsableId &&
            !isExplicitFigure &&
            !isSvg &&
            !isCanvas &&
            !isVideo &&
            !isMagicMove &&
            !isControl;

          const nearestExplicit = isExplicitFigure ? idx : frame.nearestExplicit;
          const nearestContainer = isExplicitFigure
            ? -1
            : isIdContainer
              ? idx
              : frame.nearestContainer;

          nodes.push(el);
          parentIdx.push(frame.parent);
          tags.push(tag);
          hasCodeDesc.push(tag === "code");
          const styleAttr = (el.getAttribute("style") || "").toLowerCase();
          const classList = el.classList;
          const shikiMark =
            Boolean(classList && classList.contains("shiki")) ||
            el.hasAttribute("data-shiki") ||
            el.hasAttribute("data-highlighted");
          hasShikiMarker.push(shikiMark);
          const colorSpan =
            tag === "span" &&
            (/(?:^|;)\s*color\s*:/i.test(styleAttr) ||
              Boolean(classList && classList.contains("shiki")));
          hasColorSpanDesc.push(colorSpan || shikiMark);
          const hasExcerpt = el.hasAttribute("data-code-excerpt");
          hasCodeExcerptAttr.push(hasExcerpt);
          codeExcerptAttr.push(
            hasExcerpt ? (el.getAttribute("data-code-excerpt") || "").trim().toLowerCase() : "",
          );
          isCodeLineRow.push(
            Boolean(
              classList &&
                (classList.contains("code-line") || classList.contains("diff-line")),
            ),
          );
          isCodeLinesBox.push(
            Boolean(
              classList &&
                (classList.contains("code-lines") ||
                  classList.contains("excerpt-shell") ||
                  classList.contains("code-panel")),
            ),
          );

          if (isExplicitFigure) {
            const rec = ensureFigure(idx);
            if (rec) rec.ownTechnique = normalizeTechnique(el.getAttribute("data-technique"));
          } else if (el.hasAttribute("data-technique") && nearestExplicit >= 0) {
            const tech = normalizeTechnique(el.getAttribute("data-technique"));
            const ownerRec = figureMap.get(nearestExplicit);
            if (tech && ownerRec) ownerRec.nestedTechniques.add(tech);
          }

          if ((isSvg && !frame.insideSvg) || isCanvas || isVideo || isMagicMove || isSvgImg) {
            const ownerIdx =
              nearestExplicit >= 0
                ? nearestExplicit
                : nearestContainer >= 0
                  ? nearestContainer
                  : hasUsableId && (isSvg || isCanvas || isVideo || isMagicMove)
                    ? idx
                    : -1;
            if (ownerIdx >= 0) {
              const rec = ensureFigure(ownerIdx);
              if (rec) {
                if (isSvg || isSvgImg) rec.svg = true;
                if (isCanvas) rec.canvas = true;
                if (isMagicMove) rec.driven = true;
              }
            }
          }

          if (isControl) {
            const ownerIdx = nearestExplicit >= 0 ? nearestExplicit : nearestContainer;
            if (ownerIdx >= 0) {
              const rec = ensureFigure(ownerIdx);
              if (rec) rec.driven = true;
            }
          }

          const childInsideSvg = frame.insideSvg || isSvg;
          const children = el.children;
          for (let c = children.length - 1; c >= 0; c -= 1) {
            stack.push({
              el: children[c],
              parent: idx,
              nearestExplicit,
              nearestContainer,
              insideSvg: childInsideSvg,
            });
          }
        }

        // Bottom-up pass: propagate descendant code/syntax markers in O(N) without nested queries.
        for (let i = nodes.length - 1; i >= 0; i -= 1) {
          const p = parentIdx[i];
          if (p < 0) continue;
          const t = tags[i];
          if (t === "shiki-magic-move" || t === "pre" || hasCodeExcerptAttr[i]) continue;
          if (hasCodeDesc[i]) hasCodeDesc[p] = true;
          if (hasColorSpanDesc[i]) hasColorSpanDesc[p] = true;
        }

        // Top-down pass: classify code excerpts without double-counting nested structures.
        const claimedByCode = new Uint8Array(nodes.length);
        let highlighted = 0;
        let plain = 0;
        const unclaimedGroups = new Set();

        for (let i = 0; i < nodes.length; i += 1) {
          const p = parentIdx[i];
          if (p >= 0 && claimedByCode[p]) {
            claimedByCode[i] = 1;
            continue;
          }
          const t = tags[i];
          if (t === "shiki-magic-move") {
            claimedByCode[i] = 1;
            highlighted += 1;
            continue;
          }
          if (t === "pre") {
            const isCodePre = hasCodeDesc[i] || hasShikiMarker[i] || hasCodeExcerptAttr[i];
            if (isCodePre) {
              claimedByCode[i] = 1;
              if (codeExcerptAttr[i] === "plain") {
                plain += 1;
              } else if (
                codeExcerptAttr[i] === "highlighted" ||
                hasShikiMarker[i] ||
                hasColorSpanDesc[i]
              ) {
                highlighted += 1;
              } else {
                plain += 1;
              }
            }
            continue;
          }
          if (hasCodeExcerptAttr[i]) {
            claimedByCode[i] = 1;
            if (codeExcerptAttr[i] === "plain") {
              plain += 1;
            } else if (
              codeExcerptAttr[i] === "highlighted" ||
              hasShikiMarker[i] ||
              hasColorSpanDesc[i]
            ) {
              highlighted += 1;
            } else {
              plain += 1;
            }
            continue;
          }
          if (isCodeLinesBox[i] && (hasShikiMarker[i] || hasColorSpanDesc[i])) {
            claimedByCode[i] = 1;
            highlighted += 1;
            continue;
          }
          if ((isCodeLineRow[i] || t === "code") && (hasShikiMarker[i] || hasColorSpanDesc[i])) {
            let groupIdx = p >= 0 ? p : i;
            if (t === "code" && p >= 0 && isCodeLineRow[p] && parentIdx[p] >= 0) {
              groupIdx = parentIdx[p];
            }
            unclaimedGroups.add(groupIdx);
            claimedByCode[i] = 1;
          }
        }
        highlighted += unclaimedGroups.size;

        const byKind = { svg: 0, canvas: 0, driven: 0 };
        const techniqueCounts = new Map();
        for (const rec of figureMap.values()) {
          if (rec.svg) byKind.svg += 1;
          if (rec.canvas) byKind.canvas += 1;
          if (rec.driven) byKind.driven += 1;
          if (rec.ownTechnique) {
            techniqueCounts.set(
              rec.ownTechnique,
              (techniqueCounts.get(rec.ownTechnique) || 0) + 1,
            );
          } else {
            for (const tech of rec.nestedTechniques) {
              techniqueCounts.set(tech, (techniqueCounts.get(tech) || 0) + 1);
            }
          }
        }

        const byTechnique = {};
        for (const key of [...techniqueCounts.keys()].sort()) {
          byTechnique[key] = techniqueCounts.get(key);
        }

        const result = {
          figures: {
            total: figureMap.size,
            byKind,
            byTechnique,
          },
          codeExcerpts: {
            highlighted,
            plain,
          },
        };
        if (incomplete) result.incomplete = true;
        return result;
      } catch {
        return {
          incomplete: true,
          figures: {
            total: 0,
            byKind: { svg: 0, canvas: 0, driven: 0 },
            byTechnique: {},
          },
          codeExcerpts: {
            highlighted: 0,
            plain: 0,
          },
        };
      }
    }, MAX_INVENTORY_NODES);
  } catch {
    return emptyIncompleteInventory();
  }
}
