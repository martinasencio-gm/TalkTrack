"""Pure decision logic for meeting detection.

No Qt, no Windows, no I/O, no wall clock - time arrives inside the snapshot.
Every rule in the design lives here so it can be tested without a real meeting.

The model is necessary-condition-plus-corroboration rather than weighted
scoring: a meeting app producing sustained audio is the trigger, and something
independent (the microphone, the calendar, a window title) has to corroborate it
before anything happens. Numeric weights would have been arbitrary and much
harder to explain when a suggestion fires at the wrong moment.

Start and end are deliberately asymmetric in their timing. A premature start
suggestion is an annoyance the user dismisses; a premature stop loses audio that
cannot be recovered. So a start confirms in seconds and an end waits a minute.
"""
from collections import namedtuple

Decision = namedtuple("Decision", ["action", "meeting_name"])
NONE = Decision("none", None)

IDLE = "idle"
CANDIDATE = "candidate"
SUGGESTED = "suggested"
DISMISSED = "dismissed"
RECORDING = "recording"
ENDING = "ending"
PAUSED = "paused_by_detection"


class MeetingDetector:
    def __init__(self):
        self._state = IDLE
        self._active_since = None
        self._last_active = None
        self._end_suppressed = False

    @property
    def state(self):
        return self._state

    @property
    def active_since(self):
        """Snapshot timestamp when the current run of activity began."""
        return self._active_since

    # --- signal interpretation ------------------------------------------
    @staticmethod
    def _is_active(snapshot):
        return bool(snapshot["audio_apps"] or snapshot["mic_capture_apps"])

    @staticmethod
    def _is_confirmed(snapshot):
        """Mic capture, a current calendar event, or a meeting window title.

        Sustained audio alone is deliberately excluded: it is the trigger, and a
        trigger that confirmed itself would re-admit the notification-chime
        false positive this whole rule exists to prevent.
        """
        return bool(snapshot["mic_capture_apps"]
                    or snapshot["calendar_event"]
                    or snapshot["meeting_titles"])

    @staticmethod
    def _name(snapshot):
        event = snapshot.get("calendar_event")
        if event and event.get("subject"):
            return event["subject"]
        apps = snapshot["mic_capture_apps"] or snapshot["audio_apps"]
        return apps[0] if apps else None

    def _reset(self):
        self._state = IDLE
        self._active_since = None
        self._end_suppressed = False

    # --- events from outside --------------------------------------------
    def accept_start(self):
        self._state = RECORDING
        self._end_suppressed = False

    def dismiss_start(self):
        self._state = DISMISSED

    def choose_end(self, action):
        if action == "stop":
            self._reset()
        elif action == "pause":
            self._state = PAUSED
        elif action == "keep":
            self._state = RECORDING
            self._end_suppressed = True

    def note_recording_started(self, snapshot):
        """Recording began by some route other than accepting a suggestion.

        Only watch it for an ending if a meeting was actually active at the
        time. Otherwise an unrelated background call ending would prompt the
        user to stop a recording that has nothing to do with it.
        """
        if self._is_active(snapshot):
            self._state = RECORDING
            self._last_active = snapshot["timestamp"]
            if self._active_since is None:
                self._active_since = snapshot["timestamp"]
        else:
            self._state = IDLE
        self._end_suppressed = False

    def note_recording_stopped(self):
        if self._state in (RECORDING, ENDING, PAUSED):
            self._reset()

    # --- the tick --------------------------------------------------------
    def update(self, snapshot, settings):
        if settings.get("mode", "off") == "off":
            self._reset()
            return NONE

        now = snapshot["timestamp"]
        active = self._is_active(snapshot)
        if active:
            self._last_active = now
            if self._active_since is None:
                self._active_since = now
        else:
            self._active_since = None

        absent_for = float("inf") if self._last_active is None else now - self._last_active
        grace = settings.get("end_grace_seconds", 60)
        threshold = settings.get("threshold_seconds", 5)

        if self._state == IDLE:
            if active:
                self._state = CANDIDATE
            return NONE

        if self._state == CANDIDATE:
            if not active:
                self._state = IDLE
                return NONE
            if now - self._active_since >= threshold and self._is_confirmed(snapshot):
                if settings.get("mode") == "auto":
                    self._state = RECORDING
                    return Decision("start", self._name(snapshot))
                self._state = SUGGESTED
                return Decision("suggest_start", self._name(snapshot))
            return NONE

        if self._state in (SUGGESTED, DISMISSED):
            # The prompt is already on screen, or the user said no. Either way
            # nothing more happens until this meeting is well and truly over.
            if absent_for >= grace:
                self._reset()
            return NONE

        if self._state == RECORDING:
            if not settings.get("detect_end", True) or self._end_suppressed:
                return NONE
            if absent_for >= grace:
                if settings.get("mode") == "auto":
                    action = settings.get("end_action", "stop")
                    self._state = PAUSED if action == "pause" else IDLE
                    return Decision(action, None)
                self._state = ENDING
                return Decision("suggest_end", None)
            return NONE

        if self._state == ENDING:
            if active:
                self._state = RECORDING       # a blip, not an ending
            return NONE

        if self._state == PAUSED:
            if active:
                self._state = RECORDING
                return Decision("resume", None)
            return NONE

        return NONE
