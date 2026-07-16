// WebTUI scoping contract (260715-FEUI-L1 S1 spike): every WebTUI rule is prefixed under the
// cockpit root so no rule can match outside the sessions view. Shared between postcss.config.cjs
// (the build) and src/test/webtuiSpike.test.ts (the automated spike assertions) so the tests
// exercise the EXACT options the build uses — never a copy.
//
// Mechanics:
// - `includeFiles` limits the transform to WebTUI sources. The check is root-level
//   (root.source.input.file), and Vite's postcss-import inlines the `@import "@webtui/css/…"
//   layer(webtui)` statements into src/styles/webtui.css before this plugin runs — so the
//   matcher targets BOTH the mapping file (post-inline) and the raw dist files (the test path).
// - `transform` collapses the library's global selectors (:root/html/body) onto the scope root
//   itself and prefixes everything else as a descendant. Selectors already scoped (the mapping
//   file's own rules) are left alone.

const SCOPE = '[data-view="sessions"]';

/** @type {(prefix: string, selector: string, prefixed: string) => string} */
function transform(prefix, selector, prefixed) {
  if (selector.startsWith(SCOPE)) return selector; // the mapping file's already-scoped rules
  if (/^(:root|html|body)/.test(selector)) {
    return selector.replace(/^(html\s+body|:root\s+body|html|:root|body)/, prefix);
  }
  return prefixed;
}

const webtuiPrefixOptions = {
  prefix: SCOPE,
  includeFiles: [/webtui/],
  transform,
};

module.exports = { SCOPE, webtuiPrefixOptions };
