#!/usr/bin/env node
// Start `eve acp` inside one Eve application directory for a host that drives it over ACP.
//
//   eve-acp-launcher.mjs --app <eve application directory> [--env-file <file>] [--eve <command>]
//
// Paseo starts a provider command in its own working directory and names the agent's workspace
// as the session directory. Eve accepts neither: it must run inside its application directory and
// the session directory must be that directory. This launcher changes into the application
// directory and rewrites the session directory of `session/new` and `session/load` requests.
// The env file holds the developer's model keys; it is read at launch and never copied.
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { parseEnv } from "node:util";

const OPTIONS = new Set(["--app", "--env-file", "--eve"]);
const given = {};
const passthrough = [];
const argv = process.argv.slice(2);
for (let index = 0; index < argv.length; index += 1) {
  if (OPTIONS.has(argv[index]) && index + 1 < argv.length) {
    given[argv[index]] = argv[index + 1];
    index += 1;
  } else {
    passthrough.push(argv[index]);
  }
}

const eve = given["--eve"] ?? "eve";
// A host probes the provider's version with the bare command, without the options above.
const versionOnly = passthrough.includes("--version");
const app = given["--app"];
if (!versionOnly && (!app || !existsSync(app))) {
  console.error(`eve-acp-launcher: no Eve application directory at ${app ?? "(--app missing)"}`);
  process.exit(2);
}
const envFile = given["--env-file"];
const fileEnv = envFile && existsSync(envFile) ? parseEnv(readFileSync(envFile, "utf8")) : {};

const child = versionOnly
  ? spawn(eve, ["--version"], { stdio: ["pipe", "inherit", "inherit"] })
  : spawn(eve, ["acp", ...passthrough], {
      cwd: app,
      env: { ...process.env, ...fileEnv },
      stdio: ["pipe", "inherit", "inherit"],
    });
child.on("error", (error) => {
  console.error(`eve-acp-launcher: cannot start eve: ${error.message}`);
  process.exit(127);
});

createInterface({ input: process.stdin })
  .on("line", (line) => {
    let out = line;
    try {
      const message = JSON.parse(line);
      if ((message.method === "session/new" || message.method === "session/load") && message.params) {
        message.params = { ...message.params, cwd: app };
        out = JSON.stringify(message);
      }
    } catch {
      // not JSON: forward untouched
    }
    child.stdin.write(`${out}\n`);
  })
  .on("close", () => child.stdin.end());

for (const signal of ["SIGTERM", "SIGINT", "SIGHUP"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
