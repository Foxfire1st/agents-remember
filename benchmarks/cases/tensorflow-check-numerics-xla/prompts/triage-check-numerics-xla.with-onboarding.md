You are evaluating a TensorFlow bug triage task.

Use `repos/tensorflow` as the target source checkout. Use the benchmark-local
`ar-coordination/` directory for Agents Remember context.

Execution discipline:
This is a non-interactive benchmark run. Do not ask the user questions, request
approval, browse the internet, use GitHub tools, or pause for follow-up. Make
reasonable assumptions from the available source and memory evidence and
complete the primary task in one final answer.

Constraints:
1. Do not final-answer with only the resolver or drift summary.
2. Continue immediately to the primary task below.
3. Mention the drift check only briefly as prerequisite status.
4. Final-answer only after the primary task completion criteria are satisfied.
5. Do not edit source files.

Run control:
The required C-08/C-02 onboarding drift gate is startup work only. Passing the
drift check is not task completion.

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
