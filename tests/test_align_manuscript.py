import unittest

from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from align_manuscript import (  # noqa: E402
    is_substantive_gap,
    normalize_for_alignment,
    scan_pages_in_manuscript,
    strict_consume_pages,
)


class TestAlignManuscript(unittest.TestCase):
    def test_normalize_removes_html_noise(self):
        raw = "第一行<br />第二行\n<p><strong>重点</strong></p>&nbsp;结尾"
        normalized = normalize_for_alignment(raw)
        self.assertIn("第一行", normalized)
        self.assertIn("第二行", normalized)
        self.assertIn("重点", normalized)
        self.assertNotIn("<", normalized)
        self.assertNotIn("&nbsp;", normalized)

    def test_scan_pages_happy_path(self):
        manuscript = "## 一\n\n第一页内容。\n\n## 二\n\n第二页内容。"
        pages = ["## 一\n\n第一页内容。", "## 二\n\n第二页内容。"]
        result = scan_pages_in_manuscript(manuscript, pages)
        self.assertGreater(result.cursor, 0)
        self.assertEqual([], result.issues)

    def test_strict_consume_detects_gap(self):
        manuscript = "## 一\n\n第一页内容。\n\n## 二\n\n第二页内容。"
        pages = ["## 二\n\n第二页内容。"]
        with self.assertRaises(ValueError):
            strict_consume_pages(manuscript, pages)

    def test_substantive_gap_heuristic(self):
        self.assertTrue(is_substantive_gap("## 三\n\n这是新章节内容"))
        self.assertFalse(is_substantive_gap("  \n\n   "))


if __name__ == "__main__":
    unittest.main()
