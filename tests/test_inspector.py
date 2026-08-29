"""InspectorWidget.set_empty_state() used to just hide the section list,
leaving blank space with no message — and it was only ever called from one
narrow error-recovery path in MainWindow, never on startup or when the
loaded recording is cleared. This covers the widget's own empty/non-empty
swap; the MainWindow call sites are plain wiring, verified by reading the
diff (main_window.py's _on_recording_selected / _on_recording_deleted /
_on_recording_files_changed / _on_transcription_error).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication, QWidget

from app.ui.inspector import InspectorWidget

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestInspectorEmptyState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_starts_in_empty_state(self):
        inspector = InspectorWidget()
        self.assertFalse(inspector.empty_widget.isHidden())
        self.assertTrue(inspector.scroll_area.isHidden())

    def test_set_empty_state_false_shows_sections(self):
        inspector = InspectorWidget()
        inspector.set_empty_state(False)
        self.assertTrue(inspector.empty_widget.isHidden())
        self.assertFalse(inspector.scroll_area.isHidden())

    def test_set_empty_state_true_restores_the_message(self):
        inspector = InspectorWidget()
        inspector.set_empty_state(False)
        inspector.set_empty_state(True)
        self.assertFalse(inspector.empty_widget.isHidden())
        self.assertTrue(inspector.scroll_area.isHidden())

    def test_empty_state_icon_renders(self):
        inspector = InspectorWidget()
        self.assertFalse(inspector.empty_icon.pixmap().isNull())


class TestInspectorAiConfiguredState(unittest.TestCase):
    """SummaryPanel's "Generate Summary" button used to no-op silently with
    no AI provider configured (MainWindow._run_summarize: `if provider is
    None: return`). set_ai_configured() swaps in a "Connect a provider"
    message instead of showing a button that does nothing.
    """

    @classmethod
    def setUpClass(cls):
        _get_app()

    def _inspector_with_panels(self):
        inspector = InspectorWidget()
        summary_panel = QWidget()
        inspector.add_summary_panel(summary_panel)
        return inspector, summary_panel

    def test_before_add_summary_panel_is_a_noop(self):
        inspector = InspectorWidget()
        # Must not raise even though _summary_panel doesn't exist yet.
        inspector.set_ai_configured(True)

    def test_configured_shows_panels_hides_ai_off_message(self):
        inspector, summary_panel = self._inspector_with_panels()
        inspector.set_ai_configured(True)
        self.assertFalse(summary_panel.isHidden())
        self.assertTrue(inspector.ai_off_widget.isHidden())

    def test_unconfigured_hides_panels_shows_ai_off_message(self):
        inspector, summary_panel = self._inspector_with_panels()
        inspector.set_ai_configured(False)
        self.assertTrue(summary_panel.isHidden())
        self.assertFalse(inspector.ai_off_widget.isHidden())

    def test_ai_off_widget_starts_hidden(self):
        inspector, _ = self._inspector_with_panels()
        self.assertTrue(inspector.ai_off_widget.isHidden())

    def test_connect_provider_button_emits_signal(self):
        inspector, _ = self._inspector_with_panels()
        received = []
        inspector.connect_provider_requested.connect(lambda: received.append(True))
        for child in inspector.ai_off_widget.findChildren(object):
            if hasattr(child, "text") and callable(child.text) and child.text() == "Connect a provider":
                child.click()
                break
        self.assertEqual(received, [True])


class TestInspectorSectionsStartCollapsed(unittest.TestCase):
    """add_*_panel used to force set_expanded(True) unconditionally — now
    MainWindow decides each section's initial state from config
    (ui.notes_section_expanded / speakers_section_expanded /
    summary_section_expanded), see app/main_window.py's
    _restore_panel_collapse_state.
    """

    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_add_notes_panel_does_not_force_expanded(self):
        inspector = InspectorWidget()
        inspector.add_notes_panel(QWidget())
        self.assertFalse(inspector.notes_section.is_expanded())

    def test_add_speakers_panel_does_not_force_expanded(self):
        inspector = InspectorWidget()
        inspector.add_speakers_panel(QWidget())
        self.assertFalse(inspector.speakers_section.is_expanded())

    def test_add_summary_panel_does_not_force_expanded(self):
        inspector = InspectorWidget()
        inspector.add_summary_panel(QWidget())
        self.assertFalse(inspector.summary_section.is_expanded())


if __name__ == "__main__":
    unittest.main()
