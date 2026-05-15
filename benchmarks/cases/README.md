# Benchmark Cases

Each case folder contains the manifest, prompts, and author-provided result reports for one benchmark target.

```text
cases/<case-id>/
  case.json
  prompts/
  author-results/
```

The mature memory repo for a case is not stored here. The case manifest pins its URL and commit, and `prepare` clones it into the matching generated workspace:

```text
workspaces/<case-id>/ar-coordination/memory-repos/ar-<repo>/
```

The workspace itself is not committed. `prepare` creates it from the manifest plus templates, then clones the pinned code and memory repos into the paths C-08 uses during replay.
