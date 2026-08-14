"""Layout regression tests for the recordings list rows.

These are geometry tests, not logic tests: the bug they guard against is a
row label being handed a box narrower than the text it must draw, which Qt
renders as a chopped final glyph ("51s" drawing as "51c") rather than as any
kind of error. Nothing short of laying the widgets out and measuring them
catches that, so this module builds real widgets under the offscreen platform.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

# Must precede any Qt import — selects a platform plugin that needs no display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QLabel
    from PyQt6.QtGui import QFontMetrics
    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PyQt6 is a hard dependency in practice
    QT_AVAILABLE = False


def _get_app():
    """Return the process-wide QApplication, creating it on first use.

    Qt permits only one QApplication per process, and it must outlive every
    widget, so it is shared across tests rather than created per test.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        style = Path(__file__).resolve().parent.parent / "resources" / "style.qss"
        if style.exists():
            # The global stylesheet sets fonts, so widths measured without it
            # would not reflect what actually ships.
            app.setStyleSheet(style.read_text(encoding="utf-8"))
    return app


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 not available")
class TestRecordingsListRowLayout(unittest.TestCase):

    # A short name, an overlong one, and the widest possible duration string.
    CASES = [
        ("Standup", 33),
        ("Weekly Sync", 5025),
        ("A Realistically Long Meeting Name Here For Testing Overflow", 74),
    ]

    def _build_list(self, tmpdir):
        from app.ui.recordings_list import RecordingsList

        for i, (name, duration) in enumerate(self.CASES):
            d = Path(tmpdir) / f"rec{i}"
            d.mkdir()
            (d / "metadata.json").write_text(json.dumps({
                "directory": str(d),
                "name": name,
                "started_at": "2026-08-14T10:00:00",
                "duration": duration,
            }))
            (d / "transcript.json").write_text("{}")

        widget = RecordingsList(str(tmpdir))
        widget.refresh()
        return widget

    def test_no_row_label_is_narrower_than_its_text(self):
        """Every label must be at least as wide as the text it draws.

        This is the direct regression guard: a box narrower than its text is
        precisely the state that renders a half-drawn character.
        """
        app = _get_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            widget = self._build_list(tmpdir)
            for width in (450, 400, 340, 300, 260, 220):
                widget.resize(width, 450)
                widget.show()
                app.processEvents()
                for i in range(widget.list_widget.count()):
                    row = widget.list_widget.itemWidget(widget.list_widget.item(i))
                    for label in row.findChildren(QLabel):
                        needed = QFontMetrics(label.font()).horizontalAdvance(label.text())
                        self.assertGreaterEqual(
                            label.width(), needed,
                            f"at panel width {width}, label {label.text()!r} has a "
                            f"{label.width()}px box but needs {needed}px — it will "
                            f"render with its final glyph chopped",
                        )

    def test_duration_is_never_elided(self):
        """Duration must survive at every width.

        It is bounded and short, so it is given a fixed width while the date
        beside it absorbs all elision. An ellipsis here would mean the layout
        started sacrificing the wrong field.
        """
        app = _get_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            widget = self._build_list(tmpdir)
            expected = {"33s", "1h 23m 45s", "1m 14s"}
            for width in (450, 340, 260, 220):
                widget.resize(width, 450)
                widget.show()
                app.processEvents()
                shown = set()
                for i in range(widget.list_widget.count()):
                    row = widget.list_widget.itemWidget(widget.list_widget.item(i))
                    for label in row.findChildren(QLabel):
                        shown.add(label.text())
                for duration in expected:
                    self.assertIn(
                        duration, shown,
                        f"duration {duration!r} missing or elided at panel width {width}",
                    )

    def test_no_horizontal_scrollbar_from_long_names(self):
        """A long name must not widen every row into horizontal scrolling.

        QListWidget sizes all rows to the widest row's sizeHint, so one
        unbounded name would push the right-hand fields past the viewport edge.
        """
        app = _get_app()
        with tempfile.TemporaryDirectory() as tmpdir:
            widget = self._build_list(tmpdir)
            for width in (450, 340, 260, 220):
                widget.resize(width, 450)
                widget.show()
                app.processEvents()
                self.assertFalse(
                    widget.list_widget.horizontalScrollBar().isVisible(),
                    f"horizontal scrollbar appeared at panel width {width}",
                )


if __name__ == "__main__":
    unittest.main()
