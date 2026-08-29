# Collapsible transcript & inspector panels

## Goal

Make the two right-hand columns of the 3-column layout (Transcript, and the
Notes/Speakers/Summary "Inspector" column) independently collapsible, with
their collapsed/expanded state remembered across app restarts. Double-clicking
a recording always shows both, for that viewing session, without changing the
remembered default. User-resized column widths are remembered proportionally
to the screen, not as raw pixels.

## Background

- `app/main_window.py`'s `_setup_ui` builds one flat
  `QSplitter(Library | Transcript | Inspector)` with hardcoded initial sizes
  `[262, 776, 322]` (`app/main_window.py:388-442`).
- `app/ui/collapsible_splitter.py` already has a tested, unused
  `CollapsibleSplitter(QSplitter)` — a two-pane splitter whose handle carries a
  small arrow button (▸/◂) that calls `toggle_collapse()`, collapsing pane
  index 1 to zero width. `set_collapsed(bool)` applies a state without a
  double-toggle (for restoring on startup). `collapse_changed(bool)` fires on
  every toggle, however triggered.
- `config.py`'s `DEFAULT_CONFIG["ui"]` already has a `right_panel_collapsed`
  key, and `main_window.py` already has a stub
  `_on_right_panel_collapse_changed` that writes it — but neither is wired to
  anything. Both are leftovers from
  `docs/superpowers/specs/2026-08-14-phase1-folder-nav-collapsible-panel-design.md`,
  written for the app's older two-pane layout, never updated after the 3-column
  redesign superseded it. This spec finishes that wiring against the current
  layout instead of leaving it dead.
- `app/ui/inspector.py`'s `InspectorWidget` holds three `CollapsibleSection`
  widgets (Notes, Speakers, Summary — `app/ui/collapsible_section.py`, which
  already emits `toggled(bool)` and has `is_expanded()`/`set_expanded(bool)`).
  All three are currently forced open in code
  (`.set_expanded(True)` in `add_notes_panel`/`add_speakers_panel`/
  `add_summary_panel`) with no persistence.
- **Not in scope, and must not be touched:** `app/ui/speaker_name_panel.py`
  has its own, already-live, unrelated collapse toggle for the list of
  individual speaker-name rows *inside* the Speakers panel's content,
  persisted as `config["ui"]["speakers_collapsed"]`. This spec's new
  `speakers_section_expanded` key controls the outer Inspector section header
  (whether the whole Speakers panel is shown at all) — a different level of
  the UI. Keep the two keys and mechanisms distinct.
- A recording only actually loads for viewing on double-click
  (`recordings_list.py`'s `itemDoubleClicked` → `recording_selected` signal,
  or the context menu's "View" action — never single-click); it lands in
  `MainWindow._do_on_recording_selected` (`app/main_window.py:2187`). This is
  already the one hook point for "opening a recording."
- Debounced-write-on-resize already has a precedent: the mic-gain slider uses
  a 500ms single-shot `QTimer`, flushed on `closeEvent`
  (`.claude/rules/audio-pipeline.md`). Window geometry (`ui.window_geometry`)
  is restored clamped to the currently-connected screens via
  `app/utils/screen_utils.fit_geometry_to_screens`, called from
  `_restore_window_geometry` (`app/main_window.py:3245`), itself called before
  `_setup_ui()` in `__init__`.

## Structure: nested `CollapsibleSplitter`s

`CollapsibleSplitter` only ever collapses its own pane index 1, so two
independently collapsible panes (Transcript, Inspector) around an
always-visible Library pane means nesting two of them rather than writing a
new widget:

```
splitter1 = CollapsibleSplitter(  # collapses Inspector (its pane 1)
    splitter2,   # pane 0 — collapses Transcript (its own pane 1)
    inspector,   # pane 1
)
splitter2 = CollapsibleSplitter(  # collapses Transcript (its pane 1)
    library_panel,     # pane 0
    transcript_panel,  # pane 1
)
```

`splitter1` replaces `self.splitter` as the widget added to `main_layout` in
`_setup_ui`. Both splitters disable native drag-to-zero collapse on all their
panes (`setCollapsible(i, False)`) — the arrow button is the only collapse
path, so `_collapsed` always reflects reality and a stray drag can't leave the
splitter in a state the code doesn't know about.

Collapsing both at once (leaving only Library) is allowed — no guard.

## Persisted state

All five items below follow one rule: **only an explicit click on a collapse
arrow or a section header changes the saved value.** Nothing else — including
opening a recording — writes to these keys.

New/changed keys in `DEFAULT_CONFIG["ui"]` (`app/utils/config.py`):

```python
"transcript_collapsed": False,
"inspector_collapsed": False,        # replaces right_panel_collapsed
"notes_section_expanded": True,
"speakers_section_expanded": True,
"summary_section_expanded": True,
"panel_fractions": {
    "library": None,      # fraction of screen width; None = use the fixed
    "transcript": None,   # pixel defaults below until the user resizes
    "inspector": None,
},
```

`right_panel_collapsed` is removed from `DEFAULT_CONFIG`. A new pure migration
function in `app/utils/config_migration.py`, `apply_inspector_collapsed_migration`,
follows the existing `apply_close_to_tray_migration` shape: if the saved file
has `ui.right_panel_collapsed` and no `ui.inspector_collapsed` yet, copy the
value across. Wired into `Config.load()` alongside the other two migrations.

Wiring, all added after the splitters and inspector sections exist in
`_setup_ui`:

- `splitter1.collapse_changed.connect(self._on_inspector_collapse_changed)` →
  `config.set("ui", "inspector_collapsed", collapsed)`, **unless** a
  `self._suppress_collapse_persist` guard is set (see Double-click below).
- `splitter2.collapse_changed.connect(self._on_transcript_collapse_changed)` →
  same pattern, `ui.transcript_collapsed`, same guard.
- `inspector.notes_section.toggled.connect(...)` →
  `config.set("ui", "notes_section_expanded", expanded)`. Same for
  `speakers_section` and `summary_section`.

On startup (after `_setup_ui`, so the splitters/sections exist):
`splitter1.set_collapsed(config.get("ui", "inspector_collapsed"))`,
`splitter2.set_collapsed(config.get("ui", "transcript_collapsed"))`, and each
section's `set_expanded(config.get("ui", "<name>_expanded"))` — replacing the
current hardcoded `set_expanded(True)` calls in `inspector.py`'s
`add_*_panel` methods (config isn't available inside `InspectorWidget` today;
simplest is for `MainWindow` to apply the three `set_expanded` calls itself
right after constructing the sections, the same place the collapse-restore
calls live, rather than threading `config` into `InspectorWidget`).

## Double-click: temporary, non-persisting expand

`_do_on_recording_selected` gains a call, early in the method, that expands
both if currently collapsed:

```python
self._suppress_collapse_persist = True
try:
    self.splitter1.set_collapsed(False)
    self.splitter2.set_collapsed(False)
finally:
    self._suppress_collapse_persist = False
```

`set_collapsed(False)` is a no-op if already expanded (per its existing
"only toggle if state actually differs" contract), so this never fires a
spurious toggle on an already-open panel. The guard exists because
`set_collapsed` goes through the same `toggle_collapse` → `collapse_changed`
path a real button click does — there is no separate signal for
"changed programmatically" vs. "changed by the user," so the persistence
slots must be told to stand down for this one call. A test asserts that a
recording opened while `inspector_collapsed: true` is saved leaves that config
value untouched after the open.

The three Inspector sections (Notes/Speakers/Summary) are *not* force-expanded
by double-click — only the two outer columns are, matching "both panel[s]
should open" (two panels, not five).

## Proportional resize

Each splitter's user-driven resizes are saved as a fraction of the **current
screen's available width** (`self.screen().availableGeometry().width()` —
the same screen concept `window_geometry` restore already reasons about, via
`QScreen.availableGeometry()`), not the window's own width and not raw pixels.

- `splitter1.splitterMoved.connect(self._on_splitter1_moved)` and the same for
  `splitter2`. `QSplitter.splitterMoved` fires only for user drags, not for
  programmatic `setSizes()` calls (Qt does not emit it from `setSizes`), so
  this never fires during startup restore or during a collapse/expand
  toggle — no extra guard needed here, unlike the collapse-persistence path
  above.
- Each handler restarts a 500ms single-shot `QTimer` (mirroring the mic-gain
  slider convention). On timeout, read `sizes()`, divide by
  `self.screen().availableGeometry().width()`, and write the resulting
  fraction(s) into `config["ui"]["panel_fractions"]`:
  - `splitter2` (Library | Transcript) writes both `library` and `transcript`.
  - `splitter1` (● | Inspector) writes only `inspector` — the combined
    left region's internal split is whatever `splitter2` already holds; it
    isn't re-derived here.
- Pending timers are flushed in `closeEvent` (same place
  `_save_window_geometry` already runs), so a resize in the last 500ms before
  quitting isn't lost.

Restore: a new `_restore_panel_fractions()`, called right after `_setup_ui()`
in `__init__` (after `_restore_window_geometry()` has already moved/resized
the window, so `self.screen()` resolves the correct monitor). For each of the
three fractions that is not `None`, multiply by
`self.screen().availableGeometry().width()` and feed the resulting pixel
sizes into the relevant splitter's `setSizes()`. Any fraction still `None`
(fresh install, or that particular handle never dragged) leaves that
splitter's existing default sizes (`[262, 776]` / `[1038, 322]`, matching
today's `[262, 776, 322]`) alone. This restore runs before the
collapse-state restore above, so a collapsed splitter still ends up at
`[total, 0]` regardless of what the fraction restore just set.

## Testing

- `tests/test_collapsible_splitter.py` (existing): no change needed — the
  widget itself is unchanged.
- `tests/test_config.py`: new default keys covered by the existing
  round-trip pattern; a new test for `apply_inspector_collapsed_migration`
  (old key present → new key gets the value; old key absent → default holds;
  already-migrated file is left alone), matching the existing migration tests'
  shape.
- New `tests/test_panel_fraction_restore.py` (pure logic, no Qt): a small
  helper function factored out of `_restore_panel_fractions` that takes
  `(fractions_dict, screen_width, default_sizes)` and returns the sizes to
  pass to `setSizes` — testable without constructing `MainWindow`. Covers:
  all-`None` falls back to the given defaults; a set fraction overrides its
  slot; screen width of 0 or a missing key degrades to the default rather
  than dividing by zero or crashing.
- Smoke test (per `ways-of-working.md`'s UI convention, `QT_QPA_PLATFORM=offscreen`):
  construct `MainWindow`, confirm `splitter1`/`splitter2` exist and are
  `CollapsibleSplitter` instances, toggle each and confirm the corresponding
  config key flips, call `_do_on_recording_selected` with a fixture session
  while both are collapsed and confirm they end up expanded **and** the config
  keys remain `True` (the double-click-must-not-persist test called out
  above), and confirm a section header toggle persists its own key without
  touching the other two sections'.

## Manual Verification

1. Launch the app. Click the Transcript column's collapse arrow — it
   disappears, Library takes the freed width. Click again — it returns to its
   prior width. Same for the Inspector column's arrow.
2. Collapse both. Only the Library list remains. Reopen either.
3. Collapse the Inspector column, quit, relaunch — it opens already collapsed.
4. With Inspector collapsed, double-click a recording — Inspector opens for
   this view. Quit and relaunch without touching the arrow again — it opens
   collapsed again (the double-click did not overwrite the saved preference).
5. Drag the Transcript/Inspector boundary to a new width, quit, relaunch on
   the same monitor — same width. If tested on a different-resolution
   monitor/remote session, the column occupies the same proportion of that
   screen's width, not the same pixel count.
6. Collapse the Speakers section header inside the Inspector column
   specifically (not the inner speaker-rows toggle already inside that
   panel), quit, relaunch — it opens collapsed; the Notes and Summary
   sections are unaffected and keep whatever they were left at.

## Self-Review

- **Placeholder scan:** none found.
- **Internal consistency:** the double-click guard and the splitterMoved
  no-programmatic-signal claim are two different mechanisms for two different
  Qt signals (`collapse_changed` vs. `splitterMoved`) — called out separately
  so they aren't conflated during implementation.
- **Scope check:** one implementation plan's worth of work — one new nesting
  of an existing widget, one config migration, five new/changed config keys,
  and one new pure-logic module for the fraction math. No new Qt widgets.
- **Ambiguity check:** all five open questions (collapse-both allowed,
  double-click persistence, proportional-resize basis, Notes/Speakers
  section-default behavior, and the resulting contradiction between the
  user's initial wording and their own answer on that last point) were
  resolved directly with the user before writing this spec — Notes and
  Speakers are remembered like everything else, not force-collapsed.
