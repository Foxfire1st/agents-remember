"""The scheduled random-order path is deterministic and therefore reproducible."""

from __future__ import annotations

import unittest

from agents_remember_test_support.testing.random_order import shuffle_items


class RandomOrderTests(unittest.TestCase):
    def test_the_same_seed_produces_the_same_order(self) -> None:
        first = list(range(30))
        second = list(range(30))

        shuffle_items(first, 260731)
        shuffle_items(second, 260731)

        self.assertEqual(first, second)
        self.assertNotEqual(first, list(range(30)))

    def test_a_recorded_seed_replays_the_exact_order(self) -> None:
        items = ["alpha", "beta", "gamma", "delta", "epsilon"]

        shuffle_items(items, 42)

        self.assertEqual(items, ["delta", "beta", "gamma", "epsilon", "alpha"])


if __name__ == "__main__":
    unittest.main()
