"""Guard against treating partial data or malformed symbols as market picks."""
from types import SimpleNamespace
import unittest

from scripts.daily_market_screen import extract_sse_text, shortlist_codes


class ShortlistTest(unittest.TestCase):
    def test_repairs_split_provider_delta(self):
        lines = [
            'event: response.output_text.delta',
            'data: {"delta":"hello',
            '',
        ]
        self.assertEqual(extract_sse_text(lines), "hello")

    def test_live_empty_partial_and_invalid(self):
        result = SimpleNamespace(snapshot_count=5000, snapshot_source="sina", picks=[])
        self.assertEqual(shortlist_codes(result), [])
        pick = SimpleNamespace(code="002384", price=10, excluded_by_risk=False)
        result.picks = [pick, pick]
        self.assertEqual(shortlist_codes(result), ["002384"])
        result.snapshot_count = 80
        with self.assertRaises(ValueError):
            shortlist_codes(result)
        result.snapshot_count = 5000
        result.snapshot_source = "last_good_cache"
        with self.assertRaises(ValueError):
            shortlist_codes(result)
        result.snapshot_source = "sina"
        pick.code = "002384,600519"
        with self.assertRaises(ValueError):
            shortlist_codes(result)


if __name__ == "__main__":
    unittest.main()
