# Path Rules

Path rules decide which source paths and file types are eligible for onboarding.

Storage and path eligibility are separate:

- storage decides where onboarding lives
- path rules decide what should be managed

## Include Rules

Use `include.paths` and `include.fileTypes` to keep onboarding focused:

```json
{
  "include": {
    "paths": ["README.md", "docs/**", "src/**"],
    "fileTypes": [".md", ".py", ".ts", ".tsx"]
  }
}
```

## Exclude Rules

Start with generated, vendor, build, cache, IDE, environment, and binary artifacts excluded:

```json
{
  "exclude": {
    "paths": [
      "node_modules/**",
      "vendor/**",
      "dist/**",
      "build/**",
      "coverage/**",
      ".cache/**",
      ".pytest_cache/**",
      ".venv/**",
      ".idea/**",
      ".vscode/**",
      ".env",
      ".env.*",
      "**/generated/**",
      "**/*.generated.*",
      "**/*.Zone.Identifier",
      "**/*:Zone.Identifier"
    ],
    "fileTypes": [".png", ".zip"]
  }
}
```

## Scoping

In repo-local internal settings, an unscoped rule applies to that repository.

In external-memory settings, scope rules by repository when a settings file covers more than one repository. A one-repo memory repo can use unscoped path rules because the memory repo already maps to one code repository.

## Practical Advice

Prefer a smaller eligible surface at first. Add paths as real work touches them. Path rules are a quality control layer; they should keep generated noise out of onboarding and make high-value source areas easy to maintain.
