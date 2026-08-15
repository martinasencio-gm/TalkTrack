"""Read-only lookup of the local Outlook desktop calendar via COM.

Best-effort integration: any failure (Outlook not installed, not running,
COM error) degrades to "no matches" rather than raising, since this feature
is opt-in and optional. See docs/superpowers/specs/2026-08-13-calendar-tagging-and-import-design.md
"""
import logging
from datetime import datetime, timedelta

import win32com.client

logger = logging.getLogger(__name__)

_OL_FOLDER_CALENDAR = 9


def _event_overlaps_window(event_start, event_end, window_start, window_end,
                            tolerance_minutes=5):
    """True if [event_start, event_end] overlaps [window_start, window_end]
    once each side of the window is padded by tolerance_minutes."""
    tolerance = timedelta(minutes=tolerance_minutes)
    padded_start = window_start - tolerance
    padded_end = window_end + tolerance
    return event_start < padded_end and event_end > padded_start


def _to_datetime(com_time):
    """Normalize a pywintypes COM datetime (or plain datetime, in tests) to
    a stdlib datetime with tzinfo stripped for simple comparison."""
    return datetime(
        com_time.year, com_time.month, com_time.day,
        com_time.hour, com_time.minute, com_time.second,
    )


def find_overlapping_events(start: datetime, end: datetime, tolerance_minutes: int = 5):
    """Return calendar events overlapping [start, end] (padded by tolerance).

    Each result: {"subject": str, "start": datetime, "end": datetime,
                   "organizer": str, "attendees": list[str]}.
    Returns [] if Outlook is unavailable or any COM error occurs.
    """
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        calendar = namespace.GetDefaultFolder(_OL_FOLDER_CALENDAR)
        items = calendar.Items
        # Outlook requires Sort THEN IncludeRecurrences (in that order) for
        # recurrence expansion to work correctly on a subsequent Restrict.
        items.Sort("[Start]")
        items.IncludeRecurrences = True

        # Restrict to a padded window before iterating — without this, with
        # recurrences expanded, the loop below enumerates every occurrence of
        # every recurring meeting on the calendar indefinitely into the
        # future. Over-fetch a bit (Restrict's date filtering on recurring
        # items is unreliable) and let _event_overlaps_window filter
        # precisely below.
        tolerance = timedelta(minutes=tolerance_minutes)
        safety_margin = timedelta(days=1)
        restrict_start = start - tolerance - safety_margin
        restrict_end = end + tolerance + safety_margin
        restrict_filter = (
            "[Start] < '{}' AND [End] > '{}'".format(
                restrict_end.strftime("%m/%d/%Y %I:%M %p"),
                restrict_start.strftime("%m/%d/%Y %I:%M %p"),
            )
        )
        items = items.Restrict(restrict_filter)

        results = []
        for appt in items:
            # All-day events (Out of Office, holidays, "Focus Time" banners)
            # span midnight-to-midnight, so they'd spuriously "overlap" any
            # recording made that day and get suggested as if they were the
            # actual meeting. They're never a real meeting match — skip them.
            if getattr(appt, "AllDayEvent", False):
                continue
            try:
                event_start = _to_datetime(appt.Start)
                event_end = _to_datetime(appt.End)
            except (AttributeError, ValueError):
                continue
            if not _event_overlaps_window(event_start, event_end, start, end,
                                           tolerance_minutes):
                continue
            attendees_raw = (appt.RequiredAttendees or "").strip()
            attendees = (
                [a.strip() for a in attendees_raw.split(";") if a.strip()]
                if attendees_raw else []
            )
            results.append({
                "subject": appt.Subject or "",
                "start": event_start,
                "end": event_end,
                "organizer": appt.Organizer or "",
                "attendees": attendees,
            })
        return results
    except Exception:
        logger.debug("Outlook calendar lookup unavailable", exc_info=True)
        return []
