from django.test import SimpleTestCase

from billing.services.denomination_service import compute_change


class ComputeChangeTests(SimpleTestCase):
    def test_zero_amount_needs_no_notes(self):
        result = compute_change(0, {50: 1})
        self.assertEqual(result.breakdown, {})
        self.assertEqual(result.notes_used, 0)

    def test_exact_change_with_ample_supply_matches_wireframe_style_breakdown(self):
        available = {500: 5, 50: 5, 20: 5, 10: 5, 5: 5, 2: 5, 1: 5}
        result = compute_change(643, available)
        self.assertIsNotNone(result)
        self.assertEqual(sum(value * count for value, count in result.breakdown.items()), 643)
        for value, count in result.breakdown.items():
            self.assertLessEqual(count, available[value])

    def test_greedy_would_fail_but_dp_finds_the_combination(self):
        # Greedy picks the 50 first (it fits and is largest), leaving a
        # remainder of 10 that nothing else covers -- but 60 is reachable
        # using three 20s instead. The DP must find that combination.
        available = {50: 1, 20: 3}
        result = compute_change(60, available)
        self.assertIsNotNone(result)
        self.assertEqual(result.breakdown, {20: 3})

    def test_impossible_change_returns_none(self):
        # Only a 50 note is available; 30 cannot be made from it.
        available = {50: 1}
        self.assertIsNone(compute_change(30, available))

    def test_never_exceeds_available_inventory(self):
        available = {10: 2, 1: 3}
        result = compute_change(23, available)
        self.assertIsNotNone(result)
        for value, count in result.breakdown.items():
            self.assertLessEqual(count, available[value])
        self.assertEqual(sum(value * count for value, count in result.breakdown.items()), 23)

    def test_minimizes_total_notes_used(self):
        # 100 can be made as 5x20 (5 notes) or 10x10 (10 notes) -- the
        # fewest-notes combination should win.
        available = {20: 5, 10: 10}
        result = compute_change(100, available)
        self.assertEqual(result.breakdown, {20: 5})
        self.assertEqual(result.notes_used, 5)

    def test_denominations_with_zero_count_are_ignored(self):
        result = compute_change(10, {10: 0, 5: 2})
        self.assertEqual(result.breakdown, {5: 2})
