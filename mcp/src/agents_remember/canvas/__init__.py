"""The generated page's host (MIK-R104) and the review presenter (MIK-R105).

Four decisions, each in its own module so it can be read and refused in one place:

* :mod:`agents_remember.canvas.policy` -- the isolation policy, its finite limits, and the proof
  gate that decides whether untrusted scripts may be served at all;
* :mod:`agents_remember.canvas.protocol` -- the closed host protocol, its envelope and its
  refusals;
* :mod:`agents_remember.canvas.sessions` -- the host-created, bound and revocable sessions;
* :mod:`agents_remember.canvas.presenter` -- the dedicated presenter capability, its durable
  attempt record, its conversation and its confined publication.

The serving routes live in :mod:`agents_remember.serving.canvas`; the frame bytes, their binding and
their publication belong to MIK-R103's store, which this package consumes and never re-implements.
"""
