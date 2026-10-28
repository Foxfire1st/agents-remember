// Synthetic stand-in for Paseo 0.12.0's
// @getpaseo/server/dist/server/server/plugins/plugin-process.js.
//
// Rule 1 (MIK-R216) finds the Paseo installation from process.argv[1] being this
// file's path: the `@getpaseo` directory above it is the root the discovery reads.
// Nothing here runs; only the path is used.
export function startPluginProcess() {}
