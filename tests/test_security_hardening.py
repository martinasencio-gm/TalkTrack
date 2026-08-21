"""Tests for security hardening: path boundary checks and PowerShell escaping."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestSecurityHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.recordings_dir = self.tmp_dir / "recordings"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)

    def test_ps_quote_escapes_single_quotes(self):
        from app.utils.start_menu import _ps_quote

        self.assertEqual(_ps_quote("plain_path"), "plain_path")
        self.assertEqual(_ps_quote("John's PC"), "John''s PC")
        self.assertEqual(_ps_quote("C:\\Users\\O'Connor\\repo"), "C:\\Users\\O''Connor\\repo")
        self.assertEqual(_ps_quote(Path("C:/test/it's a path")), "C:\\test\\it''s a path" if os.name == "nt" else "C:/test/it''s a path")

    def test_recordings_list_is_safe_recording_path(self):
        from app.ui.recordings_list import RecordingsList

        list_widget = RecordingsList(recordings_dir=self.recordings_dir)
        self.addCleanup(list_widget.deleteLater)

        valid_session = self.recordings_dir / "recording_20260101_100000"
        valid_session.mkdir()

        external_dir = self.tmp_dir / "external_folder"
        external_dir.mkdir()

        # Valid subfolder
        self.assertTrue(list_widget._is_safe_recording_path(valid_session))
        # The recordings root itself is rejected
        self.assertFalse(list_widget._is_safe_recording_path(self.recordings_dir))
        # External paths are rejected
        self.assertFalse(list_widget._is_safe_recording_path(external_dir))
        # Relative traversal path pointing outside
        self.assertFalse(list_widget._is_safe_recording_path(self.recordings_dir / ".." / "external_folder"))

    def test_recordings_list_rejects_delete_outside_recordings_dir(self):
        from app.ui.recordings_list import RecordingsList, DELETE_BOTH

        list_widget = RecordingsList(recordings_dir=self.recordings_dir)
        self.addCleanup(list_widget.deleteLater)

        external_dir = self.tmp_dir / "important_external_dir"
        external_dir.mkdir()
        (external_dir / "secret.txt").write_text("do not delete", encoding="utf-8")

        metadata = {"directory": str(external_dir)}

        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn, \
             patch("app.ui.recordings_list._rmtree_robust") as mock_rmtree:
            list_widget._perform_delete(metadata, DELETE_BOTH)
            mock_warn.assert_called_once()
            mock_rmtree.assert_not_called()

        self.assertTrue(external_dir.exists())
        self.assertTrue((external_dir / "secret.txt").exists())

    def test_recordings_list_allows_delete_inside_recordings_dir(self):
        from app.ui.recordings_list import RecordingsList, DELETE_BOTH

        list_widget = RecordingsList(recordings_dir=self.recordings_dir)
        self.addCleanup(list_widget.deleteLater)

        session_dir = self.recordings_dir / "recording_20260101_120000"
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text("{}", encoding="utf-8")

        metadata = {"directory": str(session_dir)}

        with patch("app.ui.recordings_list._rmtree_robust") as mock_rmtree:
            list_widget._perform_delete(metadata, DELETE_BOTH)
            mock_rmtree.assert_called_once_with(str(session_dir))

    def test_recordings_list_open_folder_rejects_external_paths(self):
        from app.ui.recordings_list import RecordingsList

        list_widget = RecordingsList(recordings_dir=self.recordings_dir)
        self.addCleanup(list_widget.deleteLater)

        external_dir = self.tmp_dir / "external_folder"
        external_dir.mkdir()

        with patch("os.startfile") as mock_startfile:
            list_widget._open_folder(str(external_dir))
            mock_startfile.assert_not_called()

        valid_session = self.recordings_dir / "recording_1"
        valid_session.mkdir()

        with patch("os.startfile") as mock_startfile:
            list_widget._open_folder(str(valid_session))
            mock_startfile.assert_called_once()

    def test_recordings_list_play_audio_rejects_external_paths(self):
        from app.ui.recordings_list import RecordingsList

        list_widget = RecordingsList(recordings_dir=self.recordings_dir)
        self.addCleanup(list_widget.deleteLater)

        external_audio = self.tmp_dir / "external_audio.wav"
        external_audio.write_bytes(b"data")

        metadata = {"audio_files": {"combined": str(external_audio)}}

        with patch("os.startfile") as mock_startfile:
            list_widget._play_audio(metadata)
            mock_startfile.assert_not_called()

        valid_audio = self.recordings_dir / "recording_1" / "combined_audio.wav"
        valid_audio.parent.mkdir()
        valid_audio.write_bytes(b"data")

        metadata = {"audio_files": {"combined": str(valid_audio)}}

        with patch("os.startfile") as mock_startfile:
            list_widget._play_audio(metadata)
            mock_startfile.assert_called_once()


if __name__ == "__main__":
    unittest.main()
