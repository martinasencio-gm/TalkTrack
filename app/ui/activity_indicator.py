"""Floating activity indicator shown when TalkTrack is minimized while busy.

Pure helpers are module-level and unit-testable, mirroring tray_icon.py's
pattern. The Qt widget (ActivityIndicator) comes in a later task and
composes them with QPainter.
"""
from app.recording.recorder import RecordingState


def resolve_activity_state(recording_state, transcription_busy):
    """Return "recording" | "paused" | "transcribing" | None.

    Recording/paused always wins over transcribing — if both are happening
    (e.g. auto-transcribe kicked off for a prior recording while a new one
    is being captured), the widget shows the recording, not the transcript
    job. None means nothing to show.
    """
    if recording_state == RecordingState.RECORDING:
        return "recording"
    if recording_state == RecordingState.PAUSED:
        return "paused"
    if transcription_busy:
        return "transcribing"
    return None


def format_activity_label(state, elapsed_seconds=None, progress_percent=None):
    """"MM:SS" for "recording"/"paused"; "NN%" for "transcribing"."""
    if state in ("recording", "paused"):
        total = max(0, int(elapsed_seconds or 0))
        minutes, seconds = divmod(total, 60)
        return f"{minutes:02d}:{seconds:02d}"
    if state == "transcribing":
        return f"{int(progress_percent or 0)}%"
    return ""


def resolve_dot_color(state):
    """Hex color for the state dot: red/amber/blue."""
    return {
        "recording": "#f38ba8",
        "paused": "#f9e2af",
        "transcribing": "#89b4fa",
    }.get(state)
