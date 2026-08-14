// 260715-FEUI-L1 S1 — the WebTUI×Panda spike's AUTOMATED assertions (R2, OQ-D). These are the
// falsifiable adopt-or-fallback criteria, run against the EXACT build configuration
// (webtui-scope.config.cjs — shared with postcss.config.cjs, never a copy):
//   (a) scope      — no WebTUI rule can match outside the [data-view="sessions"] cockpit root;
//   (b) tokens     — the ONE mapping file maps WebTUI palette vars onto existing podracer tokens
//                    only (no second color system, no raw color literals);
//   (c) freeze     — the unlayered html[data-effects="off"] !important freeze still wins: WebTUI
//                    ships no !important animation/transition declaration (a layered !important
//                    would out-cascade an unlayered one — CSS Cascade 5 reverses layer order for
//                    important declarations), and the freeze rule itself is intact;
//   (d) focus      — React Aria focus styling stays intact: WebTUI's `*{outline:none}` reset is
//                    scoped + counter-ruled by the mapping file, and the layer order keeps
//                    Panda's recipes/utilities (where _focusVisible lives) above `webtui`.
// If any of these fail, the spike is falsified → the Panda-recipe terminal-skin fallback (the
// leaf's recorded alternative).
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import postcss, { type Plugin, type Root, type Rule } from "postcss";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { SCOPE, webtuiPrefixOptions } = require("../../webtui-scope.config.cjs") as {
  SCOPE: string;
  webtuiPrefixOptions: Record<string, unknown>;
};
// postcss-prefix-selector ships no type declarations — load it the way postcss-load-config does.
const prefixSelector = require("postcss-prefix-selector") as (options: unknown) => Plugin;

const dashboardRoot = path.resolve(__dirname, "../..");
const webtuiDist = path.join(dashboardRoot, "node_modules/@webtui/css/dist");
const mappingFile = readFileSync(path.join(dashboardRoot, "src/styles/webtui.css"), "utf8");
const indexCss = readFileSync(path.join(dashboardRoot, "src/index.css"), "utf8");
const tokensCss = readFileSync(path.join(dashboardRoot, "src/styles/tokens.css"), "utf8");
const packageJson = JSON.parse(readFileSync(path.join(dashboardRoot, "package.json"), "utf8")) as {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
};

/** The WebTUI dist files the mapping file actually imports — parsed, so they can never drift. */
function importedWebtuiFiles(): string[] {
  const imports = [
    ...mappingFile.matchAll(/@import\s+"[^"]*@webtui\/css\/dist\/([^"]+)"\s+layer\(webtui\)/g),
  ];
  return imports.map(([, rel]) => path.join(webtuiDist, rel));
}

/** Run a css file through the exact prefixer configuration the build uses. */
function scopeWebtuiFile(file: string): Root {
  const css = readFileSync(file, "utf8");
  const result = postcss([prefixSelector(webtuiPrefixOptions)]).process(css, { from: file });
  return result.root;
}

const KEYFRAME_NAMES = new Set(["keyframes", "-webkit-keyframes", "-moz-keyframes", "-o-keyframes", "-ms-keyframes"]);

function isKeyframeChild(rule: Rule): boolean {
  return rule.parent?.type === "atrule" && KEYFRAME_NAMES.has((rule.parent as { name: string }).name);
}

describe("S1 spike (a): every WebTUI rule is confined to the cockpit root", () => {
  it("imports a non-empty pinned set of WebTUI files", () => {
    const files = importedWebtuiFiles();
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) expect(() => readFileSync(file)).not.toThrow();
  });

  it("prefixes every selector in every imported WebTUI file under the scope", () => {
    for (const file of importedWebtuiFiles()) {
      const root = scopeWebtuiFile(file);
      root.walkRules((rule) => {
        if (isKeyframeChild(rule)) return; // keyframe steps have no element selectors
        for (const selector of rule.selectors) {
          expect(
            selector.startsWith(SCOPE),
            `unscoped selector "${selector}" in ${path.basename(file)}`,
          ).toBe(true);
        }
      });
    }
  });

  it("collapses the library's global selectors (:root/html/body) onto the scope root itself", () => {
    const root = scopeWebtuiFile(path.join(webtuiDist, "base.css"));
    const selectors: string[] = [];
    root.walkRules((rule) => {
      selectors.push(...rule.selectors);
    });
    // No selector may keep a global anchor that would match the page outside the cockpit.
    for (const selector of selectors) {
      expect(selector).not.toMatch(/^(:root|html|body)\b/);
    }
    // The var-carrying :root rule became the scope root (the mapping overrides sit on the same node).
    expect(selectors).toContain(SCOPE);
  });
});

describe("S1 spike (b): one color system — WebTUI vars map onto podracer tokens", () => {
  const mappingBlock = /\[data-view="sessions"\]\s*\{([^}]+)\}/.exec(mappingFile)?.[1] ?? "";

  it("maps every WebTUI palette/base variable in the one mapping file", () => {
    for (const variable of [
      "--background0",
      "--background1",
      "--background2",
      "--background3",
      "--foreground0",
      "--foreground1",
      "--foreground2",
      "--box-border-color",
      "--font-family",
    ]) {
      expect(mappingBlock, `missing mapping for ${variable}`).toContain(`${variable}:`);
    }
  });

  it("references only existing token vars — no raw color literals (no second color system)", () => {
    const tokenNames = [...tokensCss.matchAll(/(--[\w-]+)\s*:/g)].map(([, name]) => name);
    const referenced = [...mappingBlock.matchAll(/var\((--[\w-]+)\)/g)].map(([, name]) => name);
    expect(referenced.length).toBeGreaterThan(0);
    for (const name of referenced) {
      expect(tokenNames, `mapping references unknown token ${name}`).toContain(name);
    }
    // Raw literals would start a second color system; color-mix over token vars is the one blend.
    expect(mappingBlock).not.toMatch(/oklch\(/);
    expect(mappingBlock).not.toMatch(/#[0-9a-fA-F]{3}/);
    expect(mappingBlock).not.toMatch(/rgb\(/);
  });
});

describe("S1 spike (c): the html[data-effects=off] determinism freeze still wins", () => {
  it("WebTUI ships no !important animation/transition declaration (layered !important would beat the unlayered freeze)", () => {
    for (const file of readdirSync(webtuiDist, { recursive: true, encoding: "utf8" })) {
      if (!file.endsWith(".css")) continue;
      const root = postcss.parse(readFileSync(path.join(webtuiDist, file), "utf8"));
      root.walkDecls((decl) => {
        if (/^(animation|transition)/.test(decl.prop)) {
          expect(decl.important, `${file}: ${decl.prop} is !important`).not.toBe(true);
        }
      });
    }
  });

  it("the freeze rule itself is intact and UNLAYERED in index.css", () => {
    // The freeze must stay outside every @layer block (unlayered normal+important beats layered).
    const freeze = /html\[data-effects="off"\][^{]*\{[^}]*animation:\s*none\s*!important[^}]*transition:\s*none\s*!important/s;
    expect(indexCss).toMatch(freeze);
    const freezeIndex = indexCss.search(freeze);
    const before = indexCss.slice(0, freezeIndex);
    const openBraces = (before.match(/\{/g) ?? []).length;
    const closeBraces = (before.match(/\}/g) ?? []).length;
    expect(openBraces, "freeze rule must sit at the top level, not inside a layer block").toBe(closeBraces);
  });
});

describe("S1 spike (d): layer order + focus-visible survival (React Aria intact)", () => {
  it("slots webtui between effects and tokens in the FIRST @layer statement", () => {
    const statement = /@layer\s+([^;{]+);/.exec(indexCss)?.[1];
    expect(statement).toBeDefined();
    const order = statement!.split(",").map((name) => name.trim());
    expect(order).toEqual(["reset", "base", "effects", "webtui", "tokens", "recipes", "utilities"]);
  });

  it("WebTUI's outline reset exists but the mapping file restores :focus-visible inside the scope", () => {
    // The threat is real: base.css resets * { outline: none } …
    const base = readFileSync(path.join(webtuiDist, "base.css"), "utf8");
    expect(base).toMatch(/outline\s*:\s*none/);
    // … and the counter-rule is present, scoped, and in the same webtui layer (Panda utilities,
    // a LATER layer, still wins for React Aria's _focusVisible styling).
    expect(mappingFile).toMatch(/\[data-view="sessions"\]\s+:focus-visible\s*\{[^}]*outline:\s*1px solid var\(--amber\)/);
  });
});

describe("S1 spike: exact version pins (0.x churn ruling)", () => {
  it("pins @webtui/css, cmdk, and tinykeys exactly (no range sigils)", () => {
    expect(packageJson.dependencies["@webtui/css"]).toBe("0.1.9");
    for (const name of ["@webtui/css", "cmdk", "tinykeys"]) {
      expect(packageJson.dependencies[name]).toMatch(/^\d/);
    }
    expect(packageJson.devDependencies["postcss-prefix-selector"]).toMatch(/^\d/);
  });
});
