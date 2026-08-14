import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingsListBatchTranscript(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_recording(self, name, transcribed):
        directory = self.recordings_dir / name
        directory.mkdir()
        metadata = {
            "id": name,
            "directory": str(directory),
            "name": name,
            "started_at": "2026-08-14T10:00:00",
            "duration": 5.0,
            "audio_files": {"combined": str(directory / "combined_audio.wav")},
        }
        with open(directory / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f)
        if transcribed:
            with open(directory / "transcript.json", "w", encoding="utf-8") as f:
                json.dump({"segments": []}, f)
        return metadata

    def _select_all(self, widget):
        widget.list_widget.selectAll()
        return widget.list_widget.selectedItems()

    def test_selected_untranscribed_returns_only_recordings_without_transcript(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        result = widget._selected_untranscribed(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["directory"]).name, "rec_a")

    def test_selected_transcribed_returns_only_recordings_with_transcript(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        result = widget._selected_transcribed(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["directory"]).name, "rec_b")

    def test_transcribe_selected_requested_emits_untranscribed_subset(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        seen = []
        widget.transcribe_selected_requested.connect(seen.append)
        untranscribed = widget._selected_untranscribed(items)
        widget.transcribe_selected_requested.emit(untranscribed)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(Path(seen[0][0]["directory"]).name, "rec_a")

    def test_export_selected_requested_emits_transcribed_subset(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        seen = []
        widget.export_selected_requested.connect(seen.append)
        transcribed = widget._selected_transcribed(items)
        widget.export_selected_requested.emit(transcribed)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(Path(seen[0][0]["directory"]).name, "rec_b")

    def test_selected_untranscribed_empty_when_all_transcribed(self):
        self._make_recording("rec_a", transcribed=True)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        self.assertEqual(widget._selected_untranscribed(items), [])

    def test_selected_transcribed_empty_when_none_transcribed(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=False)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        self.assertEqual(widget._selected_transcribed(items), [])

    def _open_context_menu(self, widget):
        """Select all items, trigger the real context menu handler with QMenu.exec
        patched out, and return the captured menu instance."""
        widget.list_widget.selectAll()
        first_item = widget.list_widget.item(0)
        position = widget.list_widget.visualItemRect(first_item).center()

        captured = {}

        def _fake_exec(self, *args, **kwargs):
            captured["menu"] = self
            return None

        with patch("PyQt6.QtWidgets.QMenu.exec", _fake_exec):
            widget._show_context_menu(position)

        return captured["menu"]

    @staticmethod
    def _action_by_text_prefix(menu, prefix):
        for action in menu.actions():
            if action.text().startswith(prefix):
                return action
        return None

    def test_context_menu_mixed_selection_enables_both_batch_actions(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)

        menu = self._open_context_menu(widget)

        transcribe_action = self._action_by_text_prefix(menu, "Transcribe")
        export_action = self._action_by_text_prefix(menu, "Export")

        self.assertIsNotNone(transcribe_action)
        self.assertIsNotNone(export_action)
        self.assertEqual(transcribe_action.text(), "Transcribe 1 Recordings")
        self.assertEqual(export_action.text(), "Export 1 Transcripts")
        self.assertTrue(transcribe_action.isEnabled())
        self.assertTrue(export_action.isEnabled())

        transcribe_seen = []
        export_seen = []
        widget.transcribe_selected_requested.connect(transcribe_seen.append)
        widget.export_selected_requested.connect(export_seen.append)

        transcribe_action.trigger()
        export_action.trigger()

        self.assertEqual(len(transcribe_seen), 1)
        self.assertEqual(len(transcribe_seen[0]), 1)
        self.assertEqual(Path(transcribe_seen[0][0]["directory"]).name, "rec_a")

        self.assertEqual(len(export_seen), 1)
        self.assertEqual(len(export_seen[0]), 1)
        self.assertEqual(Path(export_seen[0][0]["directory"]).name, "rec_b")

    def test_context_menu_all_transcribed_disables_transcribe_action(self):
        self._make_recording("rec_a", transcribed=True)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)

        menu = self._open_context_menu(widget)

        transcribe_action = self._action_by_text_prefix(menu, "Transcribe")
        export_action = self._action_by_text_prefix(menu, "Export")

        self.assertIsNotNone(transcribe_action)
        self.assertIsNotNone(export_action)
        self.assertEqual(transcribe_action.text(), "Transcribe 0 Recordings")
        self.assertFalse(transcribe_action.isEnabled())
        self.assertEqual(export_action.text(), "Export 2 Transcripts")
        self.assertTrue(export_action.isEnabled())


if __name__ == "__main__":
    unittest.main()
