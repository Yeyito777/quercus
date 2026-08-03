from __future__ import annotations

import unittest

from quercus_tool.content import parse_html


class ContentTests(unittest.TestCase):
    def test_html_becomes_text_and_safe_deduplicated_links(self):
        body, links = parse_html(
            '<h1>Hello</h1><p>See <a href="/courses/1">course</a> and '
            '<a href="/courses/1">again</a>.</p><script>secret()</script>'
        )
        self.assertIn("Hello", body)
        self.assertIn("See course and again.", body)
        self.assertNotIn("secret", body)
        self.assertEqual(links, [{"url": "https://q.utoronto.ca/courses/1", "text": "course"}])

    def test_unsafe_schemes_are_not_extracted(self):
        _, links = parse_html('<a href="javascript:alert(1)">bad</a><a href="mailto:x@y">mail</a>')
        self.assertEqual(links, [])


if __name__ == "__main__":
    unittest.main()
