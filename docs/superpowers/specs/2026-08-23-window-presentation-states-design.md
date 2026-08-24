# Window Presentation States — Design

## Problem

The minimize button doesn't minimize. `MainWindow.changeEvent` intercepts
`WindowMinimized` and, depending on `general.minimize_behavior`, either
switches to the floating compact strip or hides to the tray instead — so the
standard Windows gesture does something non-standard, and which non-standard
thing it does is buried in a settings combo.

Meanwhile the gesture that *should* shrink the app — double-clicking the
capture bar — is only half-wired: it reaches the compact bar, but from there
double-click goes back *up* to the full window, and the pill is reachable only
via a small minus button on the strip.

The two gestures are backwards. Minimize should minimize; double-click should
shrink progressively.

## Model

One ordered chain of presentation states:

| State | Window | Taskbar entry | Floating widget |
|---|---|---|---|
| `full` | visible | yes | none |
| `taskbar` | minimized | yes | none |
| `compact_bar` | minimized | yes | strip, full variant |
| `pill` | minimized | yes | strip, pill variant |
| `tray` | hidden | **no** | none |

- **Minimize button** → always `taskbar`. Not configurable.
- **Double-click** shrinks one step and wraps: `full → compact_bar → pill → full`.
- **Close (X) button** → the existing exit dialog's "minimize instead" choice
  goes to `tray` when `general.close_to_tray` is on, otherwise `taskbar`.
  This is the only remaining route to `tray`.

`compact_bar` and `pill` keep a taskbar entry. The window is genuinely
minimized rather than hidden, so the app can't be lost if the floating strip
ends up off-screen or behind something.

### The double-click chain and its entry point

`ui.double_click_target` picks where a double-click from `full` lands. The
chain itself is fixed, so the setting selects an entry point into it:

- `"compact_bar"` (default): `full → compact_bar → pill → full`
- `"pill"`: `full → pill → full` — the compact bar is still reachable from the
  pill's own expand button, just not from the double-click cycle.

## Components

### `app/ui/window_presentation.py` (new, Qt-free)

Pure transition helper, following the existing `resolve_activity_state` /
`resolve_compact_strip_state` convention:

```python
next_presentation(current, double_click_target) -> str
```

`current` is one of `"full" | "compact_bar" | "pill"`. Returns the next state
in the chain. From `"full"` it returns `double_click_target`; from
`"compact_bar"` it returns `"pill"`; from `"pill"` it returns `"full"`. An
unrecognized `double_click_target` falls back to `"compact_bar"` — a
hand-edited settings.json must not strand the gesture.

### `MainWindow`

- **`changeEvent`**: both hijack branches on `WindowMinimized` are deleted —
  the `compact_bar` branch and the `_should_hide_to_tray()` branch. Minimize
  becomes an ordinary Windows minimize. This is what makes the rest safe:
  `showMinimized()` no longer re-enters a handler that hides the window.
- **`_switch_to_compact_bar`**: `self.hide()` becomes `self.showMinimized()`,
  and sets `_strip_is_minimized_form = True`.
- **`_switch_to_full_ui`**: clears `_strip_is_minimized_form`.
- **`changeEvent` on un-minimize**: when `_strip_is_minimized_form` is set,
  hide the strip and uncheck `compact_strip_action`, so restoring from the
  taskbar lands on a clean full UI.
- **`_minimize_behavior` / `_should_hide_to_tray`**: replaced by a single
  `_close_to_tray()` reading `general.close_to_tray`, consulted only from
  `closeEvent`.

The `_strip_is_minimized_form` flag exists because the strip has two distinct
roles: the minimized representation of the window (this feature), and a
free-floating panel shown alongside the full window via View ▸ Show Compact
Strip. Only the first should be dismissed on restore, so a direct View-menu
toggle clears the flag.

### `SettingsDialog`

The "When minimized:" combo is removed. Two controls replace it in General:

- **"Double-click the capture bar shrinks to:"** — combo, *Compact bar* /
  *Pill*, writing `ui.double_click_target`.
- **"Hide to system tray when closing the window"** — checkbox, writing
  `general.close_to_tray`.

### Config

Added: `ui.double_click_target` (default `"compact_bar"`),
`general.close_to_tray` (default `False`).

Removed: `general.minimize_behavior`, `general.minimize_to_tray`. Both become
unreachable once the minimize hijack is gone, so they are deleted rather than
left as dead keys.

Migration in `app/utils/config_migration.py`, alongside the existing
meeting-detection one: an installed config with `minimize_behavior == "tray"`
or `minimize_to_tray == True` gets `close_to_tray = True`. Any other prior
value maps to `False`. A user who had minimize-to-tray on keeps a tray route;
they just reach it from the X button instead of the minimize button.

## Collisions this exposes

**Activity pill vs. compact strip.** `_update_activity_visibility` shows the
floating activity widget whenever the window is minimized-or-hidden and the app
is busy. Compact/pill mode now *is* minimized, so recording in compact mode
would stack two floating widgets on screen. The strip already renders
recording / paused / transcribing states itself, so the activity widget is
suppressed while the strip is visible:

```python
should_show = (busy_state is not None
               and (self.isMinimized() or self.isHidden())
               and not self.compact_strip.isVisible())
```

**`general.launch_in_compact_mode`.** Startup currently just checks the strip
action, leaving an un-shown window with no taskbar entry. It must now also
minimize the window and set `_strip_is_minimized_form`, so a compact-mode
launch is in the same state as a double-click into compact mode.

## Testing

Pure (TDD, `tests/test_window_presentation.py`):

- the three chain transitions, for both `double_click_target` values
- unrecognized target falls back to `compact_bar`
- migration: legacy tray settings map to `close_to_tray`, others don't; a
  brand-new install is untouched; an already-migrated config is respected

MainWindow-level (`tests/test_main_window_minimize.py`):

- minimizing leaves the window minimized and *not* hidden, with no strip,
  regardless of `double_click_target`
- double-click on the capture bar shows the strip, minimizes the window, and
  keeps it out of `isHidden()`
- capture bar → compact bar → pill → full window across three double-clicks
- restoring from the taskbar hides the strip and unchecks the menu action
- a View-menu strip toggle alongside the full window survives an un-minimize
- the activity widget stays hidden while the strip is visible during a
  recording

## Out of scope

The native Windows title bar is left alone — double-click there keeps its
standard maximize/restore behavior. The shrink gesture lives on the app's own
capture bar only.
