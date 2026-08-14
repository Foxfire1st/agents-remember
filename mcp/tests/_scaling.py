"""Shared scaling / reclamation regression helpers (260707-HFX2-L12 R9 / CS-6 D2/D3).

Every durable store, append-only log, projection reader, or process/session
launcher this hotfix bounds inherits its CS-6 regression floor from here, so a
future surface gets the same assertions for free instead of re-deriving them:

- ``assert_subquadratic`` -- prove a hot-path cost grows sub-quadratically in its
  input-size variable N by measuring at **>= 2 sizes** (never a single-N smoke,
  which is exactly how the L7 dead-seat storm hid under a generous single-N
  wall-clock bound). The cost is whatever ``cost_at(n)`` returns: a deterministic
  OPERATION COUNT (rows folded, full-file reads, bytes parsed) is preferred
  because it is immune to CI timing noise; wall-clock seconds are the fallback.
- ``assert_bounded_file_size`` -- prove a compactor/retention keeps an on-disk
  store at or below a byte ceiling after reclamation (CS-6 D3).
- ``assert_bounded_count`` -- prove a process/session/row/read population stays
  under a concurrency, dedupe, or per-sweep budget cap (CS-6 D1/D2). The
  canonical CS-6 store assertion is "<= 1 full-store read per sweep": count the
  reads and assert the count does not grow with the finding/fleet size.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

# Sub-quadratic ceiling exponent: a true O(n^2) mechanism blows past n^1.7 between
# two sizes, while ordinary linear-with-fixed-overhead behaviour stays under it.
DEFAULT_SUBQUADRATIC_EXPONENT = 1.7


@dataclass(frozen=True)
class GrowthCeiling:
    """The growth ceiling a measured cost must stay under.

    ``exponent`` and ``tolerance`` are not two independent knobs -- together they *are* the
    ceiling: the largest cost ratio allowed for a given size ratio is
    ``size_ratio**exponent * (1 + tolerance)``, and ``tolerance`` doubles as the absolute
    floor for the degenerate case where the small-N cost measures zero. Keeping them one
    object means a caller loosening the bound has to say so once, in a named place, rather
    than nudging half of it at a call site.
    """

    exponent: float = DEFAULT_SUBQUADRATIC_EXPONENT
    tolerance: float = 0.5

    def limit_for(self, size_ratio: float) -> float:
        """The largest cost ratio still under the ceiling when sizes grew ``size_ratio``x."""
        return (size_ratio**self.exponent) * (1.0 + self.tolerance)

    @property
    def absolute_floor(self) -> float:
        """The cost ceiling to use when the small-N cost is ~0 and no ratio is meaningful."""
        return max(1e-6, self.tolerance)


SUBQUADRATIC = GrowthCeiling()
"""The default sub-quadratic ceiling (n^1.7 with 50% headroom for CI noise)."""


def measure_scaling(
    cost_at: Callable[[int], float | None],
    sizes: Sequence[int],
    *,
    repeat: int = 1,
) -> dict[int, float]:
    """Return ``{n: cost}`` for each ``n`` in ``sizes``.

    ``cost_at(n)`` returns the cost to record. When it returns ``None`` the
    wall-clock time of the call is used instead (best-of-``repeat`` to damp noise);
    when it returns a number that number is the cost verbatim (use a deterministic
    operation count here whenever the mechanism exposes one).
    """
    costs: dict[int, float] = {}
    for n in sizes:
        best: float | None = None
        for _ in range(max(1, repeat)):
            start = perf_counter()
            reported = cost_at(n)
            elapsed = perf_counter() - start
            cost = float(reported) if reported is not None else elapsed
            best = cost if best is None else min(best, cost)
        costs[n] = float(best if best is not None else 0.0)
    return costs


def assert_subquadratic(
    cost_at: Callable[[int], float | None],
    sizes: Sequence[int],
    *,
    ceiling: GrowthCeiling = SUBQUADRATIC,
    repeat: int = 1,
    label: str = "cost",
) -> dict[int, float]:
    """Assert ``cost_at(n)`` stays under ``ceiling`` across ``sizes``.

    Compares the smallest and largest measured sizes: an O(n^2) mechanism yields a
    ratio near ``(n_hi/n_lo)**2`` which blows past the ceiling; a linear/constant
    mechanism passes comfortably. Requires >= 2 distinct sizes so a genuine quadratic
    cannot hide behind a single generous bound (CS-6 D3). Returns the measured costs
    for optional further assertions.
    """
    ordered = sorted(set(sizes))
    if len(ordered) < 2:
        raise ValueError(
            "assert_subquadratic needs >= 2 distinct sizes "
            "(CS-6 D3: reclamation/scaling is proven at multiple sizes, never a single-N smoke)"
        )
    costs = measure_scaling(cost_at, ordered, repeat=repeat)
    n_lo, n_hi = ordered[0], ordered[-1]
    c_lo, c_hi = costs[n_lo], costs[n_hi]
    size_ratio = n_hi / n_lo
    if c_lo <= 0:
        # Small-N cost is effectively zero (an O(1)-in-N mechanism at the small
        # end); a quadratic would still register a large c_hi. Guard with an
        # absolute-floor comparison instead of an unstable ratio.
        assert c_hi <= ceiling.absolute_floor, (
            f"{label}: small-N cost ~0 but large-N cost {c_hi} at n={n_hi} "
            f"suggests superlinear growth (costs: {costs})"
        )
        return costs
    actual_ratio = c_hi / c_lo
    limit = ceiling.limit_for(size_ratio)
    assert actual_ratio <= limit, (
        f"{label}: cost grew {actual_ratio:.1f}x from n={n_lo} to n={n_hi} "
        f"(sizes grew {size_ratio:.1f}x); sub-quadratic (n^{ceiling.exponent}) ceiling "
        f"is {limit:.1f}x. Costs: {costs}. This is the CS-6 D2 accidentally-quadratic "
        f"signature (L7 precedent)."
    )
    return costs


def assert_bounded_file_size(path: Path, max_bytes: int, *, label: str = "store") -> int:
    """Assert an on-disk store is at or below ``max_bytes`` (CS-6 D3 reclamation)."""
    size = path.stat().st_size if path.exists() else 0
    assert size <= max_bytes, (
        f"{label}: on-disk size {size} bytes exceeds bound {max_bytes} -- "
        f"reclamation/compaction did not bound the store ({path})"
    )
    return size


def assert_bounded_count(actual: int, cap: int, *, label: str = "count") -> None:
    """Assert a process/session/row/read population stays at or below ``cap``.

    The canonical CS-6 store regression: run a sweep over an N-row store with K
    findings and assert the full-store read count stays <= a small constant,
    independent of K/N (CS-6 D1/D2 -- no accidental quadratic, no unbounded
    fan-out).
    """
    assert actual <= cap, (
        f"{label}: {actual} exceeds cap {cap} -- unbounded fan-out / no load-shed / "
        f"accidental quadratic (CS-6 D1/D2)"
    )
