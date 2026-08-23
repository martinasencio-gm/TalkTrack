"""Tests for the All / Untranscribed / Tagged filter chip row above the
recordings list: live counts and single-select filtering, standalone and
combined with the existing text filter.
"""
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


class TestRecordingsListChips(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_session(self, name, with_transcript=False, tags=None,
                       started_at="2026-08-14T10:00:00"):
        d = self.recordings_dir / name.replace(" ", "_")
        d.mkdir()
        if with_transcript:
            (d / "transcript.json").write_text("{}", encoding="utf-8")
        metadata = {
            "directory": str(d),
            "name": name,
            "started_at": started_at,
            "duration": 60,
            "audio_files": {},
            "tags": tags or [],
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata

    def _visible_names(self, widget):
        return [
            widget.list_widget.item(i).data(Qt.ItemDataRole.UserRole)["name"]
            for i in range(widget.list_widget.count())
            if not widget.list_widget.item(i).isHidden()
        ]

    def test_counts_reflect_recording_status(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Transcribed", with_transcript=True)
        self._make_session("Untranscribed A", with_transcript=False)
        self._make_session("Untranscribed B", with_transcript=False, tags=["Sprint"])
        widget.refresh()

        self.assertEqual(widget.chip_all.text(), "All 3")
        self.assertEqual(widget.chip_untranscribed.text(), "Untranscribed 2")
        self.assertEqual(widget.chip_tagged.text(), "Tagged 1")

    def test_all_chip_is_selected_by_default(self):
        widget = RecordingsList(self.recordings_dir)
        self.assertTrue(widget.chip_all.isChecked())
        self.assertEqual(widget._active_chip_filter, "all")

    def test_untranscribed_chip_hides_transcribed_rows(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Has Transcript", with_transcript=True)
        self._make_session("No Transcript", with_transcript=False)
        widget.refresh()

        widget.chip_untranscribed.setChecked(True)

        self.assertEqual(self._visible_names(widget), ["No Transcript"])

    def test_tagged_chip_shows_only_tagged_rows(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Tagged Call", tags=["Sprint"])
        self._make_session("Untagged Call", tags=[])
        widget.refresh()

        widget.chip_tagged.setChecked(True)

        self.assertEqual(self._visible_names(widget), ["Tagged Call"])

    def test_switching_back_to_all_restores_every_row(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Has Transcript", with_transcript=True)
        self._make_session("No Transcript", with_transcript=False)
        widget.refresh()

        widget.chip_untranscribed.setChecked(True)
        widget.chip_all.setChecked(True)

        self.assertEqual(
            set(self._visible_names(widget)), {"Has Transcript", "No Transcript"}
        )

    def test_chip_filter_persists_across_refresh(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Has Transcript", with_transcript=True)
        self._make_session("No Transcript", with_transcript=False)
        widget.refresh()
        widget.chip_untranscribed.setChecked(True)

        widget.refresh()  # e.g. re-triggered after an unrelated delete elsewhere

        self.assertEqual(self._visible_names(widget), ["No Transcript"])
        self.assertTrue(widget.chip_untranscribed.isChecked())

    def test_chip_and_text_filter_combine(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Standup", with_transcript=False)
        self._make_session("Standup Old", with_transcript=True)
        self._make_session("Budget Review", with_transcript=False)
        widget.refresh()

        widget.chip_untranscribed.setChecked(True)
        widget._on_filter_changed("standup")

        self.assertEqual(self._visible_names(widget), ["Standup"])

    def test_chip_toggled_while_showing_search_results_rebuilds_normal_view(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("No Transcript", with_transcript=False)
        widget.refresh()

        widget._show_search_results([
            {"recording_id": "somedir", "text": "hello", "start": 0.0, "speaker": "A"},
        ])
        self.assertTrue(widget._showing_search_results)

        widget.chip_untranscribed.setChecked(True)

        self.assertFalse(widget._showing_search_results)
        self.assertEqual(self._visible_names(widget), ["No Transcript"])

    def test_untranscribed_chip_with_no_matches_shows_empty_message(self):
        widget = RecordingsList(self.recordings_dir)
        self._make_session("Has Transcript", with_transcript=True)
        widget.refresh()

        widget.chip_untranscribed.setChecked(True)

        self.assertFalse(widget._empty_label.isHidden())


if __name__ == "__main__":
    unittest.main()
