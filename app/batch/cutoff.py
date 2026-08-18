"""The --until wall-clock cutoff for a batch run.

The cutoff is the latest time at which a *new* recording may be started,
not a deadline that kills work in flight: transcription can take longer
than the recording itself, and throwing away an hour of finished CPU work
at 07:00 to save a few minutes of overrun is a bad trade.
"""
import re
from datetime import datetime, timedelta

# Deliberately not datetime.strptime("%H:%M"): that also accepts "7" and
# rejects nothing useful, and the runner needs to tell a bare clock time
# from an absolute datetime before it can decide whether to roll forward.
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


class CutoffError(ValueError):
    """The --until value could not be understood."""


def parse_cutoff(value, now=None):
    """Resolve a --until value to an absolute datetime.

    A bare ``HH:MM`` means the *next* occurrence of that time, so a task
    firing at 23:00 with ``--until 07:00`` gets the whole night rather than
    a cutoff seventeen hours in the past. An explicit ``YYYY-MM-DDTHH:MM``
    is unambiguous and is honoured exactly as given, even if already past.
    """
    if not isinstance(value, str) or not value.strip():
        raise CutoffError("a cutoff time is required, e.g. --until 07:00")
    value = value.strip()
    now = now or datetime.now()

    match = _TIME_RE.match(value)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise CutoffError(f"{value!r} is not a valid time of day")
        cutoff = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cutoff <= now:
            cutoff += timedelta(days=1)
        return cutoff

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise CutoffError(
            f"{value!r} is not a time (HH:MM) or a datetime (YYYY-MM-DDTHH:MM)"
        ) from None


def may_start_another(cutoff, now=None):
    """Whether there is still time to begin another recording."""
    if cutoff is None:
        return True
    return (now or datetime.now()) < cutoff
