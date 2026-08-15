# Minimized Activity Widget — Design

(Backlog: Story 3.2, "Minimized Recording Widget")

## Background

TalkTrack already has a system tray icon (`app/ui/tray_icon.py`) that reflects
recording state via tooltip and a colored dot overlay, plus a "Minimize to
tray" setting (`general.minimize_to_tray`) that, when enabled, hides the main
window entirely on minimize instead of doing a normal OS minimize
(`app/main_window.py:2012-2023`).

Today, if a user minimizes while recording or while a transcription/
diarization job is running, there is no on-screen indicator that anything is
happening beyond the tray icon (easy to miss, and invisible at all if
"Minimize to tray" is off and the taskbar entry is buried).

## Goal

When the main window is minimized (or hidden to tray) while TalkTrack is
recording or transcribing, show a small floating always-on-top widget that
indicates the activity in progress. Clicking it restores the main window.
Idle minimizing is unaffected — existing "Minimize to tray" behavior stays
exactly as it is today.

## Architecture

New file `app/ui/activity_indicator.py`, following the existing
`tray_icon.py` pattern: Qt-free pure functions for decision/formatting logic
(independently unit-testable), plus a thin `ActivityIndicator(QWidget)` class
that renders and reports interaction. `MainWindow` owns one instance and is
the sole place that decides when it shows, hides, or updates.

### Pure functions

```python
def resolve_activity_state(recording_state, transcription_busy):
    """Return "recording" | "paused" | "transcribing" | None.

    Recording/paused always wins over transcribing — if both are happening
    (e.g. auto-transcribe kicked off for a prior recording while a new one
    is being captured), the widget shows the recording, not the transcript
    job. None means nothing to show.
    """

def format_activity_label(state, elapsed_seconds=None, progress_percent=None):
    """"MM:SS" for "recording"/"paused"; "NN%" for "transcribing"."""

def resolve_dot_color(state):
    """Hex color for the state dot: red/amber/blue (see Visual design)."""
```

### `ActivityIndicator(QWidget)`

- Window flags: `Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
  | Qt.WindowType.Tool` — floats above other windows, never appears in the
  taskbar or Alt-Tab.
- Fixed size ~130×36px, rounded-rect pill background, painted in
  `paintEvent` (QPainter — dot + label), same technique as
  `TrayIcon._compose_icon_with_dot`.
- `restore_requested = pyqtSignal()` — emitted on a genuine click.
- `position_changed = pyqtSignal(int, int)` — emitted after a drag ends,
  carrying the new top-left screen coordinates.
- `set_activity(state, elapsed_seconds=None, progress_percent=None)` —
  updates the label/dot color and (for "recording" only) starts a ~800ms
  QTimer that toggles the dot's opacity to pulse; any other state stops the
  pulse timer.
- `show_at(x, y)` — clamps `(x, y)` to the currently available virtual
  desktop geometry before showing, so a position saved on a monitor layout
  that's since changed (e.g. an unplugged second monitor) can't leave it
  permanently off-screen. Falls back to a default top-right-of-primary-screen
  position when no valid saved position exists.
- Drag handling: `mousePressEvent` records the press position and the
  widget's current position; `mouseMoveEvent` moves the widget with the
  mouse; `mouseReleaseEvent` compares total movement against a small pixel
  threshold (e.g. 4px) — under threshold emits `restore_requested`, over
  threshold emits `position_changed` with the final position instead.

## Trigger logic

One method on `MainWindow`, `_update_activity_visibility()`, is the single
place that decides show/hide/update:

```python
def _update_activity_visibility(self):
    busy_state = resolve_activity_state(self.recorder.state, self._transcription_busy())
    should_show = busy_state is not None and (self.isMinimized() or self.isHidden())
    if should_show:
        elapsed = int(self.recorder.get_elapsed_time()) if busy_state in ("recording", "paused") else None
        percent = self._current_transcription_percent if busy_state == "transcribing" else None
        if not self._activity_widget.isVisible():
            self._activity_widget.show_at(*self._activity_widget_position())
        self._activity_widget.set_activity(busy_state, elapsed, percent)
    elif self._activity_widget.isVisible():
        self._activity_widget.hide()
```

Called from every place that can change either half of the condition:

- `changeEvent` (`WindowStateChange` → minimized/restored)
- `_on_state_changed` (recording start/pause/resume/stop)
- `_on_recording_tick` (keep the elapsed label live while showing)
- transcription start / `progress_percent` / finished / error / cancelled
  handlers (keep the percent label live; make the widget appear/disappear/
  switch state if a transcription starts or ends while already minimized)

**`changeEvent`'s existing idle-path logic is unchanged**: the branch that
hides the window to tray when `general.minimize_to_tray` is enabled only
runs when `busy_state is None`. When busy, minimizing is left as a normal OS
minimize — the window stays in the taskbar, minimized — and
`_update_activity_visibility()` shows the widget alongside it, regardless of
the checkbox. The checkbox continues to control idle-minimize behavior
exactly as it does today; nothing about it changes in Settings.

Restoring — via a widget click, the tray's "Show TalkTrack" action, or the
taskbar icon — goes through the existing `_restore_from_tray` path
(`showNormal()` + tray overlay clear), extended to also hide the activity
widget.

**Scope decision:** if recording stops (and no transcription follows) while
still minimized, the widget simply disappears; the window does not
retroactively snap into "hidden to tray" even if that setting is on. The
tray-hide-vs-normal-minimize choice is only evaluated at the moment of the
minimize action itself.

## Visual design

Small rounded pill, dark background (`#1e1e2e`, matching the app's existing
theme), light text (`#cdd6f4`):

| State | Dot color | Pulse | Label |
|---|---|---|---|
| Recording | `#f38ba8` (red) | Yes, ~800ms toggle | `MM:SS` elapsed |
| Paused | `#f9e2af` (amber) | No | `MM:SS` (frozen at pause) |
| Transcribing | `#89b4fa` (blue) | No | `NN%` progress |

All three colors are already present in the app's palette (`resources/style.qss`,
`main.py`'s splash screen) — no new colors introduced.

Cursor: pointing-hand on hover to hint clickability; a move/drag cursor while
being dragged.

## Position persistence

New config key `ui.activity_widget_position` (nullable `[x, y]`, default
`None`). On `position_changed`, `MainWindow` saves it via
`self.config.set("ui", "activity_widget_position", [x, y])` +
`self.config.save()`. On show, `_activity_widget_position()` reads it back,
falling back to a default top-right-of-primary-screen position when unset —
`ActivityIndicator.show_at()` clamps whatever it's given to the current
screen geometry regardless.

## Error handling

- `closeEvent` explicitly closes the activity widget (mirrors the
  `self._com_poller.stop()` teardown already there) so no stray always-on-top
  window survives app exit.
- Multi-monitor changes between sessions: handled by the clamp in
  `show_at()`, not by validating the saved value at load time.
- The widget has no dependency on `QSystemTrayIcon` support — it works
  identically whether or not the system tray is available.

## Testing

- Pure functions (`resolve_activity_state`, `format_activity_label`,
  `resolve_dot_color`) unit-tested without Qt, mirroring
  `tests/test_tray_icon.py`'s existing style for `tray_icon.py`'s pure
  helpers.
- `MainWindow` wiring tested with a mocked `ActivityIndicator` (same
  technique as `tests/test_main_window_com_poller_lifecycle.py`), asserting
  `_update_activity_visibility()` shows/hides/updates at the right
  transitions: minimize while recording, minimize while idle (unchanged),
  recording stop with a queued transcription taking over while still
  minimized, restore clearing the widget.
- Manual verification (real Windows session): minimize while recording
  (widget appears, pulses, click restores + hides it); pause (dot changes to
  amber, stops pulsing); minimize while idle with "Minimize to tray" on and
  off (confirm both are byte-for-byte unchanged from current behavior); drag
  the widget, restart the app, confirm the position persisted and is
  clamped sanely if the monitor layout changed; start a transcription while
  minimized and confirm the widget switches to the blue "NN%" state and
  disappears when it finishes.

## Out of scope

- No stop/cancel control on the widget itself (locked decision — restoring
  the window is the only way to stop a recording or cancel a transcription).
- No changes to the "Minimize to tray" checkbox in Settings — it keeps
  controlling idle-minimize behavior exactly as today.
- No changes to the existing tray icon's own behavior, tooltip, or menu.

## Self-review

- No placeholders — every function name, signal, config key, and color
  value is pinned.
- Internal consistency: the trigger condition
  (`busy_state is not None and (isMinimized() or isHidden())`) is the single
  source of truth referenced by every call site listed in Trigger logic; no
  call site duplicates or diverges from it.
- Scope: touches one new file plus targeted additions to `main_window.py`'s
  existing `changeEvent`/`_on_state_changed`/tick/transcription-callback
  methods; no unrelated refactoring.
- Ambiguity: pulse interval (800ms), drag threshold (4px), pill size
  (130×36px), and all three colors are explicit values, not "reasonable
  defaults" left for the plan to invent.
