"""Off-thread Outlook calendar lookup for a finished recording."""
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
        events = find_overlapping_events(self.started_at, self.stopped_at)
        self.finished.emit(events)
