// Panda first (tokens/recipes/utilities codegen), then the WebTUI scoper (260715-FEUI-L1 S1):
// postcss-prefix-selector confines every WebTUI rule to the sessions-view root. Options live in
// webtui-scope.config.cjs so the spike tests assert the exact build configuration. Vite's
// internal postcss-import runs before both, inlining src/styles/webtui.css's layered @imports.
// eslint-disable-next-line @typescript-eslint/no-require-imports -- CJS config, require() is the mechanism
const { webtuiPrefixOptions } = require('./webtui-scope.config.cjs');

module.exports = {
  plugins: {
    '@pandacss/dev/postcss': {},
    'postcss-prefix-selector': webtuiPrefixOptions,
  },
}
