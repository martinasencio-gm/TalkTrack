"""Text formatting for meeting start/end recording prompts.

Shared by MeetingNotificationToast (app/ui/meeting_toast.py), which renders
these prompts.
"""


def _minutes_phrase(seconds):
    """"2 minutes", "1 minute", or None when it does not round to a minute."""
    minutes = int(seconds // 60)
    if minutes < 1:
        return None
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def format_start_text(meeting_name, elapsed_seconds):
    subject = meeting_name or "A meeting"
    phrase = _minutes_phrase(elapsed_seconds)
    when = "just now" if phrase is None else f"{phrase} ago"
    return f"{subject} started {when} - record it?"


def format_end_text(meeting_name, recorded_seconds):
    subject = meeting_name or "The meeting"
    phrase = _minutes_phrase(recorded_seconds) or "less than a minute"
    return f"{subject} ended - stop recording? ({phrase} captured)"
