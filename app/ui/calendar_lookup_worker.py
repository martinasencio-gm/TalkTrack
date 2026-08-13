"""Off-thread Outlook calendar lookup for a finished recording."""
import pythoncom
from PyQt6.QtCore import QThread, pyqtSignal

from app.integrations.outlook_calendar import find_overlapping_events


class CalendarLookupWorker(QThread):
    """Looks up overlapping calendar events for [started_at, stopped_at].

    Carries a `.session` attribute (set by the caller, not the constructor)
    so completion handlers can bind results to the recording session that
    was active when the lookup started, per the session-binding convention
    in transcription-pipeline.md.
    """

    finished = pyqtSignal(list)  # list of event dicts, possibly empty

    def __init__(self, started_at, stopped_at, parent=None):
        super().__init__(parent)
        self.started_at = started_at
        self.stopped_at = stopped_at
        self.session = None

    def run(self):
        # pywin32 requires CoInitialize per thread before any COM Dispatch
        # call; without it win32com.client.Dispatch raises
        # "CoInitialize has not been called", which find_overlapping_events's
        # broad except Exception silently swallows into []. See
        # app/recording/_process_com.py for the same per-thread COM-init
        # requirement on the process-loopback capture thread.
        pythoncom.CoInitialize()
        try:
            events = find_overlapping_events(self.started_at, self.stopped_at)
        finally:
            pythoncom.CoUninitialize()
        self.finished.emit(events)
