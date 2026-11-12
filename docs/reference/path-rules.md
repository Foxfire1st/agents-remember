# Path Rules

Path rules decide which source files of a code repository are eligible for a
[card](../concepts.md#card). They stand in the memory settings file,
`<memory-root>/system/settings.json`, under the key `onboarding.pathRules`, or under `pathRules`
at the top level of the file. A file that holds the keys of a rule (`include`, `exclude`, `path`)
at its top level, without one of those two keys around them, loads without an error and applies
no rule: every source file stays eligible.

[Settings Reference](settings-json.md#memory-settings-file-keys) describes the other keys
of the file.

## The keys of a rule

The value of `onboarding.pathRules` is one rule, as an object, or a list of rules. Another value
refuses the load.

| Key | Value | Default | Meaning |
| --- | --- | --- | --- |
| `include.paths` | a pattern or a list of patterns | `["*"]` | The file is eligible when one pattern matches its path. |
| `include.fileTypes` | a file type or a list of file types | none | When stated, the file is eligible only with one of these types. |
| `exclude.paths` | a pattern or a list of patterns | none | The file is not eligible when one pattern matches its path. |
| `exclude.fileTypes` | a file type or a list of file types | none | The file is not eligible with one of these types. |
| `path` | a folder, relative to the root of the code repository that the memory repository belongs to | the whole repository | The rule holds for files under this folder. Its patterns are matched against the path below the folder. |
| `repo` | the same as `path` | — | Read only when the rule has no `path`. |
| `storage` | text | `onboarding.storage.default` | Read only when `onboarding.storage.mode` is `hybrid`: it names where the cards of the files that this rule includes are stored. |

`path` must be text, and so must a `repo` that is read and a `storage` other than `null`; another
value refuses the load. Another key in a rule loads without a message and is read by nothing.

The check for missing cards, the census of the cards that `memory_quality_check` takes
(`memoryCensus` in its answer), the [route index](../concepts.md#route-index) and
`read_ar_files` read the rules.

## How a file is matched

- A pattern follows shell matching: `*` matches any run of characters, also across `/`; `?`
  matches one character; `[abc]` matches one of the characters in the brackets, and `[!abc]` one
  character that is not among them. The default `*` therefore matches every path. Each `**/` in
  a pattern also matches no folder at all, so `**/generated/**` matches `generated/a.py` and
  `src/generated/a.py`.
- Paths are compared with regard to case.
- A file type is compared without regard to case, and the leading dot is optional: `py`, `.py`
  and `.PY` name the same type.
- The rules are tried in their order. The first rule whose `include` matches the file decides.
  When that rule also excludes the file, the file is not eligible and no later rule is tried.
- When the memory settings file holds at least one rule, a source file that no rule includes is
  not eligible.
- Without any rule, every file is eligible.
- Two values of `onboarding.storage.mode` change this. With `disabled`, no file is eligible. With
  `hybrid`, the `storage` of the rule that includes a file decides where its card is stored, and
  a file that no rule includes takes the value of `onboarding.storage.default`;
  [Settings Reference](settings-json.md#memory-settings-file-keys) describes the values.

## Include rules

This example is a complete memory settings file. It keeps the cards to three areas and four
file types:

```json
{
  "onboarding": {
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      }
    }
  }
}
```

With this file `src/a/b.py` is eligible, and `src/a/b.go` and `lib/x.py` are not.

## Exclude rules

This example is a complete memory settings file. It leaves out generated, vendor, build, cache,
editor, environment and binary files:

```json
{
  "onboarding": {
    "pathRules": {
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
  }
}
```

One rule may hold `include` and `exclude` together.

## More than one rule

This example is a complete memory settings file with a list of two rules. The first rule holds
for the folder `services/api`, the second for the folder `docs`:

```json
{
  "onboarding": {
    "pathRules": [
      {
        "path": "services/api",
        "include": {
          "paths": ["src/**"],
          "fileTypes": [".py"]
        }
      },
      {
        "path": "docs",
        "include": {
          "fileTypes": [".md"]
        }
      }
    ]
  }
}
```

A memory repository belongs to one code repository, so a rule without `path` holds for the
whole of that repository.

## Advice

Start with a small eligible area and add paths as real work touches them. Path rules keep
generated files out of the cards and keep the valuable source areas easy to maintain.
