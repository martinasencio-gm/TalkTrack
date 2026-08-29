# UI Patterns: reusable widget conventions and Qt gotchas

## Disclosure tiers (control placement)

Every control gets an explicit tier when it's added, so new features have a defined
home instead of landing in the nearest toolbar by default:

| Tier | Meaning |
|---|---|
| **T0** Always visible | The one primary action for the surface, plus live state |
| **T1** Contextual | Shown only when applicable (e.g. `diarize_btn.hide()` when there's no HF token) |
| **T2** Overflow | A `⋯` button, split-button menu (`QToolButton` + `MenuButtonPopup`), or right-click context menu |
| **T3** Settings | Sticky preferences set once, not per-use |

**Budget: at most 4 T0 controls per surface.** When a 5th earns its place, demote the
least-used existing T0 control rather than adding a 5th — that's what keeps a surface
from re-accumulating the clutter this budget exists to prevent.

The menu bar is out of scope for this budget — menus are already hidden by default and
are the correct destination for demoted actions, not a source of clutter.

Two established patterns for demoting a control without losing its function:
- **Checkbox → `QAction`**: a checkable `QAction` is a drop-in replacement for a
  `QCheckBox` inside a menu — same `isChecked()`/`setChecked()`/`setEnabled()`/`toggled`
  API, so the surrounding code barely changes. See `TranscriptViewer`'s
  `diarize_action`/`summarize_action`/`continue_action`.
- **Several related buttons → one split-button**: `QToolButton` with
  `setPopupMode(MenuButtonPopup)` and a `QMenu` — the button body performs the default
  action, the arrow opens the rest. See `TranscriptViewer.transcribe_btn` (Transcribe +
  its two settings) and `.play_all_btn`/`.export_btn`. QSS for this needs restating on
  `QToolButton` explicitly (`objectName="splitAction"` in `resources/style.qss`) since it
  doesn't inherit the `QPushButton` rules.
- **Several one-off buttons → one overflow menu**: an `InstantPopup` `QToolButton`
  carrying a vendored icon (never a Unicode glyph like "⋯" — Inter has no glyph for
  U+22EF and it renders as tofu; use `colored_pixmap(...)`). See
  `RecordingHeader.overflow_btn` (Rename/Add tag/Change meeting).

Before folding a phase of controls into a tier change, verify the premise against the
actual rendered surface (screenshot) and existing code comments — a plan written from
reading widget source in isolation can misjudge which states are simultaneous (e.g. two
labels that are actually mutually-exclusive `QStackedWidget` alternatives look like
"clutter" until you check), and a comment defending an existing design as intentional
outranks an assumption made while planning. Confirm a merge target is actually live
before consolidating into it — `grep` for real call sites, not just construction.

## Design tokens (`app/ui/tokens.py`)

Colour, type-scale, and spacing constants for widgets that draw their own frame/QSS
rather than living inside the main window's `resources/style.qss`-styled panels
(compact strip/pill, activity indicator, recording controls, tag dialog, batch
dialogs). Reference by name instead of hand-typing a hex string or a `NNpx` size —
that hand-typing is exactly how the same near-black surface color ended up as five
slightly different hex strings across files.

- Build QSS as an f-string and interpolate: `f"QLabel {{ color: {tokens.TEXT}; }}"`
  — note the doubled `{{`/`}}` for QSS's own rule-block braces versus single `{}` for
  the token substitution.
- A bare hex argument (e.g. `colored_pixmap("name", "#9184d9", 18)`) becomes the bare
  token name directly, not an f-string wrapping one substitution:
  `colored_pixmap("name", tokens.ACCENT, 18)`.
- This is a separate palette from the documented Catppuccin Mocha one below (`resources/style.qss`) — it's already a different, deliberately higher-contrast theme for
  always-on-top/floating chrome, not drift to reconcile. Don't unify the two palettes;
  just stop re-typing either one's hex values inline.
- Adding a new colour/size to one of these widgets: add it to `tokens.py` first (even
  if only one call site needs it today), then reference it — never add a fresh raw hex
  literal to one of the token-migrated files.

## CollapsibleSection (`app/ui/collapsible_section.py`)

- Reusable widget with a banded header (QFrame `#collapsibleHeader`) and collapsible content area.
- Emits `toggled(bool)` signal. Exposes `add_header_widget(widget)` for right-aligned extras (e.g., Refresh button).
- When collapsed, `setMaximumHeight(header_height)` so it can't claim empty space via layout stretch.
- **Dynamic stretch pattern**: when wrapping a CollapsibleSection in a parent QVBoxLayout, connect `toggled` to `setStretchFactor(widget, 1 if expanded else 0)`. This lets sibling sections absorb freed space when one is collapsed. See `MainWindow._setup_ui` for Audio Sources + Recordings example.
  - **Gotcha — all-collapsed case**: with only dynamic section stretches, collapsing every section leaves the layout with no claimant and Qt distributes the empty space oddly (centering one section, dropping the other to the bottom). Add a trailing `addStretch(0)` spacer and flip its stretch to 1 when no section is expanded, 0 otherwise — see `MainWindow._update_left_panel_stretch`.

## Left panel layout

- Fixed width 400px (`left_panel.setFixedWidth(400)`, objectName `leftPanel`). Intentionally non-draggable — prevents layout jitter on collapse/expand.
- Left-pane font size reduced to 9pt via QSS `#leftPanel QLabel, #leftPanel QRadioButton, ...`. Timer keeps 13pt via `#leftPanel #timerLabel`.
- Section title bands use `QFrame#collapsibleHeader` with `background-color: #313244` (Catppuccin surface0) and `border-radius: 4px`.

## DAW meter fill direction (`_VerticalMeter` in `meters_panel.py`)

- Bar fills **upward from bottom** as volume rises (standard DAW).
- Implementation: paint color zones over full height, then fill the **empty region ABOVE current level** with background color (`0` to `current_y`), NOT below. Getting this backwards makes the bar look like it's losing color as you speak.
- Peak hold line: 3px `#f5e0dc` (rosewater) — stands out against green/yellow/red and the dark empty region.

## Peak-sample vs RMS bar

- `_VerticalMeter` drives the bar from the **peak sample** (`20·log10(max|x|)`), not RMS. Reason: with an RMS bar, the peak-hold line floats several dB above the bar's top *always* (RMS < peak for any real signal), which reads as "the line doesn't match the bar". With a peak-sample bar, the hold line sits AT the bar's top while rising and only floats above during the hold/decay phase — the DAW convention.
- The held peak has its own state (`_peak_abs` + hold/decay via `peak_hold_value`) — independent of the bar. Don't unify them or the hold animation disappears.
- 2px outline in surface0 (`#313244`) around each channel so the meter frame is visible even when silent. Drawn last so it overdraws the top/bottom of the color fills; acceptable cosmetic loss at those exact edges.
- Scale ticks: `[0, -18, -40, -60]` at 11px. `-6` overlapped `0` at this font size; `-40` is useful enough to keep over a four-tick scale.

## Qt QSS gotchas

- Plain `QWidget` subclasses don't render `background-color` from QSS unless you set `WA_StyledBackground` attribute. Symptom: `#myPanel { background-color: X }` appears to apply everywhere or nowhere predictably.
- Cleaner alternatives: (a) wrap in a `QFrame` with an object name and style the frame, (b) custom `paintEvent` with `painter.drawRoundedRect`, (c) palette + `setAutoFillBackground(True)`.
- `QLabel`, `QFrame`, and similar DO render QSS backgrounds — scope them explicitly with `#parentId QLabel { ... }` to avoid cascade surprises.

## Palette

Catppuccin Mocha throughout. Common shades:
- Base: `#1e1e2e` — app bg
- Mantle: `#181825` — darker sections
- Surface0: `#313244` — bands, subtle lifts
- Text: `#cdd6f4`
- Blue accent: `#89b4fa`
- Red (clip/mute): `#f38ba8`
- Green (healthy): `#a6e3a1`
- Yellow (hot): `#f9e2af`
- Rosewater (peak line): `#f5e0dc`

## System tray (MainWindow)

- **Popup suppression while hidden**: any background-triggered `QMessageBox` in `main_window.py` must be guarded with `if self._is_hidden_to_tray()` — show the red/green tray overlay via `_flag_error_notification()` / `_flag_success_notification()` instead of popping a modal the user can't see. Applies to worker-completion / error callbacks. User-initiated popups (menu actions, delete prompts) don't need the guard since the window is visible.
- **`_really_quit` flag + `_confirm_exit`**: the X button and tray Quit both funnel through `closeEvent`. `_confirm_exit()` shows the exit dialog with a recording-aware body; setting `_really_quit = True` bypasses the dialog for reentrant calls. Don't add new direct-quit paths without going through this.
- **`changeEvent` must NOT hijack minimize**: it used to intercept `WindowMinimized` and swap in the compact strip or `hide()` to the tray, so the standard Windows gesture did something non-standard. Minimize now always minimizes to the taskbar. `changeEvent`'s only job is on the way back *up*: dismissing a strip that stands in for a minimized window. Don't reintroduce a minimize intercept.
- **Hiding vs. minimizing**: `hide()` removes the taskbar entry — that's the tray, reached only from the close button's "minimize instead" choice when `general.close_to_tray` is on. Compact bar and pill use `showMinimized()` so the window keeps its taskbar entry and can't be lost behind the floating strip.

## Window presentation chain (MainWindow)

- One ordered chain: `full → compact_bar → pill → full`, walked by `_advance_presentation()` via the Qt-free `app/ui/window_presentation.next_presentation()`. Double-click on the capture bar *or* the strip shrinks one step; `ui.double_click_target` picks which shrunken state a double-click from the full window lands on (so `"pill"` yields `full → pill → full`).
- **The strip doesn't own the chain.** `CompactStrip.mouseDoubleClickEvent` emits a single `shrink_requested` and changes nothing itself — MainWindow decides. Keep it that way, or the same gesture starts meaning different things on different surfaces.
- **`_strip_is_minimized_form`** distinguishes the strip's two roles: minimized stand-in for the window (dismissed when the user restores from the taskbar) vs. a free-floating panel opened from View ▸ Show Compact Strip (left alone). A direct View-menu toggle clears it.
- **The activity widget is suppressed while the strip is visible** (`_update_activity_visibility`). Compact/pill mode *is* a minimized window now, so without that check both floating widgets stack on screen while recording — and the strip already renders the busy states itself.
