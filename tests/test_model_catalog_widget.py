"""Pure-helper tests for the model catalog widget (no Qt widgets)."""
import unittest

from app.ai.model_catalog import CATALOG
from app.ui.model_catalog_widget import disk_warning, human_size, row_detail_line


class HelperTest(unittest.TestCase):
    def test_human_size_gb(self):
        self.assertEqual(human_size(1_929_903_264), "1.8 GB")
        self.assertEqual(human_size(4_683_073_344), "4.4 GB")

    def test_human_size_mb(self):
        self.assertEqual(human_size(500 * 1024 * 1024), "500.0 MB")

    def test_row_detail_line_has_size_context_ram_license(self):
        line = row_detail_line(CATALOG[0])
        self.assertIn("GB", line)
        self.assertIn("32k context", line)
        self.assertIn("~4 GB RAM", line)
        self.assertIn("Apache-2.0", line)

    def test_disk_warning_none_when_plenty_of_space(self):
        m = CATALOG[0]
        self.assertIsNone(disk_warning(m.size_bytes * 5, m))

    def test_disk_warning_message_when_tight(self):
        m = CATALOG[0]
        msg = disk_warning(int(m.size_bytes * 1.1), m)
        self.assertIsNotNone(msg)
        self.assertIn("free", msg.lower())


if __name__ == "__main__":
    unittest.main()
