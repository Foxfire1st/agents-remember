# Security Policy

## Scope

Agents Remember is a documentation and workflow repository. It does not ship an application runtime, hosted service, SDK, CLI, browser bundle, or installable package.

The repository primarily contains:

- markdown instruction files
- skill definitions
- workflow documentation
- onboarding templates and examples

That matters for security reporting. Most risks here are not traditional dependency or memory-corruption issues. The relevant concerns are things like:

- unsafe or misleading agent instructions
- prompt-injection-friendly guidance
- examples that encourage dangerous behavior
- accidental disclosure of secrets or sensitive data in docs
- workflow guidance that bypasses approval, review, or drift checks
- malicious links or untrusted external references embedded in skill files

## Supported Versions

Security fixes are applied to the current default branch.

Because this repository is a markdown-first instruction set rather than a versioned software product, we do not maintain separate security support windows for older documentation snapshots. Please report issues against the latest branch state.

## What To Report Here

Please report issues in this repository if they involve the content of this repository itself, including:

- a skill file that instructs an agent to take unsafe actions
- workflow guidance that weakens approval or review safeguards
- documentation that encourages insecure handling of secrets, credentials, or production systems
- unsafe example commands or patterns that could damage user environments
- links or references that point contributors toward malicious or compromised resources
- prompt patterns that make the system materially easier to subvert

## What Not To Report Here

Please do not report general vulnerabilities in third-party tools here unless the issue is specific to how this repository instructs people to use them.

Examples:

- a vulnerability in GitHub Copilot, Cursor, Claude Code, VS Code, or another agent host should be reported to that vendor
- a vulnerability in a dependency used by another project should be reported to that project
- a weakness in a consumer repository using this memory system is out of scope unless it is caused by instructions or examples from this repository

## Reporting a Vulnerability

Please use GitHub Private Vulnerability Reporting if it is enabled for this repository.

If private reporting is not available, contact the maintainers through a non-public channel and include:

- a clear description of the issue
- the affected file or files
- why the content is unsafe
- a minimal reproduction or example interaction, if applicable
- the likely impact
- any suggested remediation

Please do not open public issues for vulnerabilities until the maintainers have had a reasonable chance to assess and address the report.

## What To Expect

The maintainers will aim to:

1. acknowledge receipt
2. confirm whether the report is in scope
3. assess impact based on how the guidance could affect agent behavior
4. update or remove unsafe content
5. disclose the fix publicly when appropriate

Response time is best-effort. Since this repository is documentation and skill content rather than an operated service, remediation will usually mean revising guidance, examples, or workflow constraints.

## Disclosure Philosophy

This repository exists to make agent behavior more reliable and more auditable over time. Security issues here are therefore treated as instruction-integrity issues first.

A change is security-relevant if it could reasonably cause an agent or user to:

- bypass an approval gate
- trust stale onboarding as current truth
- execute unsafe actions without adequate review
- expose sensitive information
- rely on compromised or untrusted references

If you find one of those cases, report it.
