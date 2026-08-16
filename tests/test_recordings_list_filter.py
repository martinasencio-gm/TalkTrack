"""Tests for the live name/date filter and Enter-triggered content search."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingsListFilter(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self, name, started_at="2026-08-14T10:00:00"):
        d = self.recordings_dir / name.replace(" ", "_")
        d.mkdir()
        metadata = {
            "directory": str(d),
            "name": name,
            "started_at": started_at,
            "duration": 60,
            "audio_files": {},
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def _visible_names(self, widget):
        names = []
        for i in range(widget.list_widget.count()):
            item = widget.list_widget.item(i)
            if not item.isHidden():
                names.append(item.data.__self__ and item.data(Qt.ItemDataRole.UserRole).get("name"))
        return names

    def test_typing_hides_non_matching_rows(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Weekly Standup")
        self._make_session("Budget Review")
        widget.refresh()

        widget._on_filter_changed("standup")

        hidden = [widget.list_widget.item(i).isHidden() for i in range(widget.list_widget.count())]
        visible_meta = [
            widget.list_widget.item(i).data(Qt.ItemDataRole.UserRole)["name"]
            for i in range(widget.list_widget.count())
            if not widget.list_widget.item(i).isHidden()
        ]
        self.assertEqual(visible_meta, ["Weekly Standup"])
        self.assertTrue(any(hidden))

    def test_filter_matches_on_date_too(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Standup", started_at="2026-08-14T10:00:00")
        self._make_session("Review", started_at="2026-01-02T10:00:00")
        widget.refresh()

        widget._on_filter_changed("2026-08-14")

        visible = [
            widget.list_widget.item(i).data(Qt.ItemDataRole.UserRole)["name"]
            for i in range(widget.list_widget.count())
            if not widget.list_widget.item(i).isHidden()
        ]
        self.assertEqual(visible, ["Standup"])

    def test_no_matches_shows_empty_message(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Standup")
        widget.refresh()

        widget._on_filter_changed("nonexistent query")

        self.assertFalse(widget._empty_label.isHidden())
        self.assertIn("nonexistent query", widget._empty_label.text())

    def test_clearing_filter_restores_all_rows(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Standup")
        self._make_session("Review")
        widget.refresh()

        widget._on_filter_changed("standup")
        widget.refresh()  # cleared -> RecordingsList.refresh() via SearchBar.cleared

        visible = sum(
            1 for i in range(widget.list_widget.count())
            if not widget.list_widget.item(i).isHidden()
        )
        self.assertEqual(visible, 2)
        self.assertTrue(widget._empty_label.isHidden())

    def test_filter_after_search_results_rebuilds_normal_view_first(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Standup")
        widget.refresh()

        widget._show_search_results([
            {"recording_id": "somedir", "text": "hello world", "start": 0.0, "speaker": "A"},
        ])
        self.assertTrue(widget._showing_search_results)

        widget._on_filter_changed("standup")

        self.assertFalse(widget._showing_search_results)
        visible = [
            widget.list_widget.item(i).data(Qt.ItemDataRole.UserRole)["name"]
            for i in range(widget.list_widget.count())
            if not widget.list_widget.item(i).isHidden()
        ]
        self.assertEqual(visible, ["Standup"])

    def test_search_with_no_results_shows_empty_message(self):
        widget = RecordingsList(self.recordings_dir)
        widget._show_search_results([])
        self.assertFalse(widget._empty_label.isHidden())


if __name__ == "__main__":
    unittest.main()
