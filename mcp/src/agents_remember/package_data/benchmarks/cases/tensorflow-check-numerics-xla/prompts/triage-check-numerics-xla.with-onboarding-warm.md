You are evaluating a TensorFlow bug triage task.

Use `repos/tensorflow` as the target source checkout. Use the benchmark-local
`ar-coordination/` directory for Agents Remember context.

Warm-memory setup:
The benchmark harness has already validated that the pinned external-memory repo
matches the pinned source checkout for this run. Treat benchmark-local
onboarding and memory files as trusted current-state context.

Provider setup:
The benchmark-local context providers are already installed, indexed, and
running from benchmark harness provider settings.
GrepAI is available for semantic discovery over `ar-coordination/memory-repos`,
and CodeGraphContext is available for relationship discovery over
`repos/tensorflow`.

Known benchmark-local paths:
- code_root: `repos/tensorflow`
- memory_root: `ar-coordination/memory-repos/ar-tensorflow`
- onboarding_root: `ar-coordination/memory-repos/ar-tensorflow/onboarding`

Execution discipline:
This is a non-interactive benchmark run. Do not ask the user questions, request
approval, browse the internet, use GitHub tools, or pause for follow-up. Make
reasonable assumptions from the available source and memory evidence and
complete the primary task in one final answer.

Constraints:
- Do not edit source files.
- Do not final-answer with only setup or memory-status notes.
- Follow the active benchmark-local Agents Remember workflow from
  `ar-coordination/AGENTS.md` normally, using the available MCP and provider
  tools when that workflow calls for them.
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
