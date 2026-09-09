"""Guard against treating partial data or malformed symbols as market picks."""
from types import SimpleNamespace
import unittest

from scripts.daily_market_screen import extract_sse_text, shortlist_codes


class ShortlistTest(unittest.TestCase):
    def test_utf8_stream_preserves_unicode_line_separators(self):
        import json
        from requests import Response
        value = "\u4e2d\u6587\u2028\u4e70\u5165"
        payload = ('data: ' + json.dumps({"type": "response.output_text.delta", "delta": value}, ensure_ascii=False) + '\n\n').encode('utf-8')
        response = Response()
        response.iter_content = lambda **kwargs: iter([payload[:19], payload[19:]])
        self.assertEqual(extract_sse_text(response.iter_lines(decode_unicode=False, delimiter=b"\n")), value)

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
