# Inline Onboarding Block Template

Use this companion template when file-level onboarding is stored inline instead of as a mirrored markdown file.

It reuses the same semantic content model as `file-level-onboarding-template.md`. Only storage, comment syntax, placement, verification metadata, and digesting differ.

```text
<host-language block comment start>
@ar-onboarding
format: 1
sourceDigest: sha256:<digest-of-source-with-this-block-removed>
verifiedAt: <YYYY-MM-DDTHH:MM>
scope: file

Purpose:
- <What this source file is responsible for and why it matters.>

Logic:
- <Key behavior and control flow.>

Conventions:
- <Local patterns and style assumptions.>

Invariants And Boundaries:
- <Rules that must continue to hold.>

Todos:
- <Durable follow-up that is not tied to one active task.>

Docs References:
- <Short prose summary or `No relevant documentation found.`>

Cross-Repo References:
- <Short prose summary or `No meaningful cross-repo references found.`>
@ar-onboarding-end
<host-language block comment end>
```

Guidelines:

1. Keep the `@ar-onboarding` markers and metadata keys stable.
2. Adapt only the outer comment delimiters to the host language.
3. Place the block as high in the file as possible without breaking required leading syntax or tooling markers.
4. Recompute `sourceDigest` from the source body with the inline onboarding block removed.
