# Security Policy

## Scope

Agents Remember is a versioned software package, `agents-remember-mcp`. It brings three programs
to your machine, and it serves instructions to coding agents.

| Program | What it is | Where it listens |
| --- | --- | --- |
| Tool server | the MCP server `agents-remember-mcp`; a harness starts it as its own child process | nowhere on the network: it speaks with the harness over its standard input and output |
| Dashboard | the local web page that `agents-remember dashboard` serves | `127.0.0.1:8765` by default; `dashboard.port` in the settings file or `--port` sets the port |
| Host | the program that runs role agents and shows their chats; it is Paseo, installed and started by Agents Remember | `127.0.0.1:8766` by default; `paseoRuntime.listen` in the settings file sets the address |

Installing the package starts none of the three. A harness starts the tool server.
`agents-remember dashboard` starts the dashboard and, when the host is installed, the host; a
[Bootstrap call](docs/concepts.md#bootstrap-call) starts the dashboard too. The first Bootstrap
call, the tool `runtime_install` and `agents-remember paseo provision` install the host and start
it. When `dashboard.autoStart` in the settings file is `true`, the tool server starts the dashboard
and the installed host too.

All three are meant for the machine they run on. The defaults bind the dashboard and the host to
the loopback address, so another machine cannot reach them.

The repository also holds the skill that the tool server serves to agents; it holds the
instructions of the roles. A role agent is a chat of a harness that works with your account, your
logins and your files. An instruction that misleads such an agent is a security matter here, just
as a fault in the code is.

## What the product does to stay local

- **The dashboard has no login.** Whoever can reach its port can read the state it shows and use
  its actions. Its `--host` option binds another address; do not use it to expose the dashboard,
  and do not publish the port through a tunnel or a proxy.
- **The dashboard answers only under its own name.** A request that carries a `Host` header, reads
  included, must name in it `localhost`, `127.0.0.1`, `::1` or the address the dashboard bound,
  with the port it bound. Any other such request gets status 403 with the reason `foreign-host`.
  A request without a `Host` header is admitted. A page that reaches the loopback address under a
  foreign name sends that name in the header and is refused this way.
- **The dashboard refuses a state-changing request from another origin.** For a `POST`, `PUT`,
  `PATCH` or `DELETE` request that a browser sends, the header `Sec-Fetch-Site` must say
  `same-origin`; any other value gets status 403 with the reason `cross-site`. When a browser sends
  no such header, the `Origin` header decides: `null` is refused as `null-origin`, another origin
  as `foreign-origin`. A request with neither header, such as one from `curl` or from the command
  line, passes.
- **A refusal is visible.** The answer is a JSON object with `"state": "refused"`, the `reason`
  and a `detail` sentence.
- **The dashboard shows the host's chats only to listed origins.** The list is `paseoRuntime.embed`
  in the settings file: each entry pairs a `dashboardOrigin`, the dashboard's origin exactly as a
  browser reports it, with the `frameBaseUrl` at which a browser on that origin reaches the host.
  For a request from an origin that the list does not name, or from a page of another site, the
  dashboard answers with the reason `origin-not-listed` and names no address of the host.
- **The host is configured for local use.** Each time Agents Remember installs the host (see
  above), it sets three keys of the host's configuration to `false`: `daemon.relay.enabled`,
  `features.dictation.enabled` and `features.voiceMode.enabled`. So the host's relay, dictation and
  voice mode are off.
- **Downloads are checked.** When it installs the host, Agents Remember downloads one Node archive
  from `nodejs.org` and compares its SHA-256 digest with the digest the package carries. It installs
  the host's packages with `npm ci` from the lock file the package carries; `npm ci` takes them from
  `registry.npmjs.org` and checks each against the integrity hash of the lock file.
- **Private values stay in files.** A provider entry under `paseoRuntime.providers` describes a
  harness that the host can start, and its `env` object holds environment variables for that
  harness, which may be private values. Agents Remember writes such an entry into the host's
  configuration file, never onto a command line. Its reports name the ids of the entries and no
  value of `env`, except the refusal `harness-home-would-change`: it names the old and the new
  value of each harness home variable, such as `CODEX_HOME`, that a restart of the host would
  change. Protect the settings file accordingly.

[The Host](docs/host.md) describes the host, its ports and its settings.

## Supported Versions

Security fixes are applied to the current default branch.

We do not maintain separate security support windows for older versions of the package. Please
report issues against the latest branch state, and name the version of the package you run. The
tool `server_info` reports it under `version`: ask a chat of the harness in which you registered
the tool server to call it.

## What To Report Here

Please report issues in this repository if they involve the code or the content of this repository
itself, including:

- a way to reach the dashboard, the host or the tool server from another machine or from a page of
  another site
- a request that the dashboard should refuse and accepts
- a tool, a command or an instruction of a role that lets an agent pass an approval or a review
  that the workflow requires
- a private value that the product writes into a log, a report or a command line
- an install step that fetches or runs something without the check described above
- a skill file that instructs an agent to take unsafe actions
- workflow guidance that weakens approval or review safeguards
- documentation that encourages insecure handling of secrets, credentials, or production systems
- unsafe example commands or patterns that could damage user environments
- links or references that point contributors toward malicious or compromised resources
- prompt patterns that make the system materially easier to subvert

## What Not To Report Here

Please do not report general vulnerabilities in third-party tools here unless the issue is
specific to how this repository installs, configures or instructs people to use them.

Examples:

- a vulnerability in Claude Code, Codex, Cursor, or another harness should be reported to that
  vendor
- a vulnerability in Paseo itself should be reported to that project; how Agents Remember installs
  and configures it belongs here
- a vulnerability in a dependency used by another project should be reported to that project
- a weakness in a code repository that uses Agents Remember is out of scope unless it is caused
  by the code, instructions or examples from this repository

## Reporting a Vulnerability

Please use GitHub Private Vulnerability Reporting if it is enabled for this repository.
When it is, the tab "Security and quality" of the repository's page on GitHub shows the button "Report a vulnerability".

If private reporting is not available, contact the maintainers through a non-public channel and include:

- a clear description of the issue
- the affected file or files
- why the code or the content is unsafe
- a minimal reproduction or example interaction, if applicable
- the likely impact
- any suggested remediation

Please do not open public issues for vulnerabilities until the maintainers have had a reasonable chance to assess and address the report.

## What To Expect

The maintainers will aim to:

1. acknowledge receipt
2. confirm whether the report is in scope
3. assess impact based on what the fault lets another party do, or how the guidance could affect
   agent behavior
4. fix the code, or update or remove unsafe content
5. disclose the fix publicly when appropriate

Response time is best-effort. Agents Remember runs on your own machine and is not an operated
service, so a remedy reaches you as a change in the repository.

## Disclosure Philosophy

This repository exists to make agent behavior more reliable and more auditable over time. Security
issues here are therefore treated as faults of the software and as instruction-integrity issues
alike.

A change is security-relevant if it could reasonably cause an agent or user to:

- bypass an approval gate
- trust a stale card as current truth
- execute unsafe actions without adequate review
- expose sensitive information
- rely on compromised or untrusted references

It is also security-relevant if it could let another machine, another user or a page of another
site read or change what the dashboard, the host or the tool server hold.

If you find one of those cases, report it.
