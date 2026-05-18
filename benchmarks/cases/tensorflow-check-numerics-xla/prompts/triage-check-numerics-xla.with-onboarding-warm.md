You are evaluating a TensorFlow bug triage task.

Use `repos/tensorflow` as the target source checkout. Use the benchmark-local
`ar-coordination/` directory for Agents Remember context.

Warm-memory setup:
The benchmark harness has already validated that the pinned external-memory repo
matches the pinned source checkout for this run. Treat benchmark-local
onboarding and memory files as trusted current-state context.

Known benchmark-local paths:
- code_root: `repos/tensorflow`
- memory_root: `ar-coordination/memory-repos/ar-tensorflow`
- onboarding_root: `ar-coordination/memory-repos/ar-tensorflow/onboarding`

Warm read mode:
- Use `C-04-onboarding-read-mode`.
- Do not locate, list, or inventory memory or onboarding. The roots above are
  already known.
- Start by reading `<onboarding_root>/overview.md`.
- Before relying on a source file, read it with its deterministic sidecar:
  `<onboarding_root>/<repo-relative-source-path>.md`.
- If a candidate sidecar is absent, note that status and use the nearest
  governing `overview.md`.
- Use source search only for one named unresolved question at a time, scoped to
  the smallest relevant route first.

Execution discipline:
This is a non-interactive benchmark run. Do not ask the user questions, request
approval, browse the internet, use GitHub tools, or pause for follow-up. Make
reasonable assumptions from the available source and memory evidence and
complete the primary task in one final answer.

Constraints:
- Do not edit source files.
- Do not run C-02 drift detection.
- Do not run the C-08 resolver CLI.
- Do not final-answer with only setup or memory-status notes.
- Do read relevant benchmark-local onboarding files alongside the source files
  they describe.
- Use the benchmark-local memory repo only; do not use any parent workspace
  memory.

Ticket excerpt:
A user reports that `tf.debugging.check_numerics` raises
`InvalidArgumentError` for NaN/Inf in eager execution and under
`tf.function(jit_compile=False)`, but silently passes the same invalid values
through under `tf.function(jit_compile=True)`. The user says this removes a
numerical safety guard when XLA compilation is enabled.

Minimal reproducer shape:

```python
import tensorflow as tf

t = tf.constant([1.0, 2.0, float("nan")])

try:
    tf.debugging.check_numerics(t, "has nan")
except tf.errors.InvalidArgumentError:
    print("eager raised")

r = tf.function(
    lambda v: tf.debugging.check_numerics(v, "has nan"),
    jit_compile=True,
)(t)
print(r.numpy())
```

Primary task:
Triage the ticket against the pinned TensorFlow checkout. Identify the likely
subsystem boundaries involved, explain the most likely source-level cause, cite
the source evidence you found, and propose a concrete next investigation or fix
direction. Do not implement the fix.
