# Phase 1: Folder Navigation & Collapsible Right Panel

## Goal

Implement the three low-risk backlog stories from Phase 1 of the provided plan:

- Story 1.1: Open Transcripts Folder from the File menu
- Story 1.2: Open Transcripts Folder from the recording list's context menu
- Story 3.1: Collapsible right panel

Stories 1.3 (Delete Recordings), 2.1/2.2 (Transcript Processing), 4.1 (Background
Task Optimization), and 3.2 (Minimized Recording Widget) are separate epics with
their own dependencies and are out of scope for this spec — each gets its own
brainstorm → spec → plan cycle later, per the user's own phased ordering.

## Background

- `app/main_window.py`'s File menu already has `Open Recordings Folder`
  (`_open_recordings_folder`, `app/main_window.py:1546`), which opens
  `config.get("output", "directory")`.
- `config.get("transcripts", "directory")` is a *separate*, global folder used
  only for exported Markdown summaries (`app/utils/transcript_export.py`) — it
  is not where a recording's own `transcript.json` lives (that's inside the
  recording's own subfolder, alongside its audio).
- `app/ui/recordings_list.py`'s context menu (`_show_context_menu`,
  `recordings_list.py:384`) currently has a single-item `"Open Folder"` action
  (`_open_folder`, opens `metadata["directory"]`, i.e. that one recording's own
  subfolder) and a multi-select menu that is Delete-only.
- `app/ui/speaker_name_panel.py` already persists a collapsed/expanded UI flag
  the same way this spec's panel-collapse state will: `config.get("ui",
  "speakers_collapsed")` / `config.set(...)`.
- The main window's central layout is a single `QSplitter` (`Qt.Horizontal`,
  `app/main_window.py:183`) with two children: the left controls panel and
  `right_panel` (recording header, calendar/meeting banners, and the
  transcript/notes/chat tabs), added at `app/main_window.py:263-305`.

## Story 1.1: Open Transcripts Folder (File menu)

Add a new `QAction("&Open Transcripts Folder")` in `_setup_menu`
(`app/main_window.py:118`), right after the existing `Open Recordings Folder`
action and before the separator that precedes Exit. Its handler,
`_open_transcripts_folder`, mirrors `_open_recordings_folder` exactly but reads
`config.get("transcripts", "directory")`:

```python
def _open_transcripts_folder(self):
    transcripts_dir = self.config.get("transcripts", "directory")
    os.makedirs(transcripts_dir, exist_ok=True)
    os.startfile(transcripts_dir)
```

(`_open_recordings_folder` does a local `import os`; both will use the
module-level `os` import already present at the top of `main_window.py` — no
new import needed for either.)

## Story 1.2: Recording-list context menu

In `_show_context_menu` (`recordings_list.py:384`):

**Single-item menu:** rename `"Open Folder"` to `"Open Recordings Folder"`.
Behavior is unchanged — it still opens that one recording's own subfolder via
`_open_folder(metadata["directory"])`. Add a new `"Open Transcripts Folder"`
action directly below it, calling a new `_open_transcripts_folder()` method
that opens the global `self.config.get("transcripts", "directory")` (the
widget already receives `config` — see Interfaces below).

**Multi-select menu:** currently Delete-only. Add both folder actions above
the delete action, in the same order as the single-item menu. They ignore the
selection entirely — `"Open Recordings Folder"` opens the *global* recordings
root (`config.get("output", "directory")`) here, since there's no single
recording to scope to; `"Open Transcripts Folder"` opens the same global
transcripts folder as everywhere else.

This means the single-item and multi-select menus use different folders for
`"Open Recordings Folder"` (one recording's subfolder vs. the global root) —
intentional, confirmed with the user, and each menu is internally consistent.

### Interfaces

`RecordingsList` does not currently hold a `Config` reference. `MainWindow`
constructs it as `RecordingsList(recordings_dir)`
(`app/main_window.py:244`). Add an optional `config=None` constructor
parameter, threaded through to a new `self.config` attribute, and pass
`self.config` from `MainWindow`. When `config` is `None` (as in existing
tests that construct `RecordingsList` directly), the transcripts-folder
actions must not crash — guard by disabling/hiding those two actions if
`self.config is None`.

## Story 3.1: Collapsible right panel

### Component: `CollapsibleSplitterHandle`

New file `app/ui/collapsible_splitter.py`, containing:

- `CollapsibleSplitter(QSplitter)` — overrides `createHandle()` to return a
  `CollapsibleSplitterHandle` for the handle between index 0 and 1 (the only
  handle in this two-pane splitter).
- `CollapsibleSplitterHandle(QSplitterHandle)` — adds one small `QToolButton`
  (▸ collapsed / ◂ expanded), vertically centered on the handle via a
  `QVBoxLayout` with stretches above and below. Clicking it calls back to the
  splitter's `toggle_collapse()` method rather than containing any collapse
  logic itself, keeping the state machine in one place.

`CollapsibleSplitter` owns the collapse state:

```python
class CollapsibleSplitter(QSplitter):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._collapsed = False
        self._expanded_size = None  # right-pane width to restore to

    def toggle_collapse(self):
        sizes = self.sizes()
        if self._collapsed:
            total = sum(sizes)
            restore = self._expanded_size or total // 3
            self.setSizes([total - restore, restore])
        else:
            self._expanded_size = sizes[1]
            self.setSizes([sum(sizes), 0])
        self._collapsed = not self._collapsed
        self.collapse_changed.emit(self._collapsed)

    def set_collapsed(self, collapsed, expanded_size=None):
        """Apply a restored state on startup without emitting/toggling twice."""
        ...

    collapse_changed = pyqtSignal(bool)
```

No animation — `setSizes` is instant. (Qt's `QSplitter` doesn't animate size
changes natively, and adding a `QPropertyAnimation` over splitter sizes is a
well-known source of jank; instant collapse matches the existing
`speakers_collapsed` panel's behavior, which is also instant.)

### Wiring in MainWindow

Replace the plain `QSplitter(Qt.Orientation.Horizontal)` at
`app/main_window.py:183` with `CollapsibleSplitter(Qt.Orientation.Horizontal)`.
After both panels are added (`app/main_window.py:305`), connect
`splitter.collapse_changed` to a new `_on_right_panel_collapse_changed(bool)`
that persists `config.set("ui", "right_panel_collapsed", collapsed)`.

On startup, after the splitter is fully built, read
`config.get("ui", "right_panel_collapsed")` and call
`splitter.set_collapsed(True)` if it was `True` — restoring the collapsed
state without an extra toggle/animation flicker on launch.

### Config default

Add `"right_panel_collapsed": False` to `DEFAULT_CONFIG["ui"]` in
`app/utils/config.py` (existing `"ui"` section already has `theme` and
`speakers_collapsed` — this is a same-shape addition, no migration needed
since it's a new key with a safe default that `_deep_merge` will backfill for
existing users).

## Testing

- **`app/utils/config.py`**: existing `tests/test_config.py` pattern covers
  new default keys already — no new test needed beyond confirming the key
  exists (covered implicitly by any test that round-trips `Config().data`).
- **`app/ui/collapsible_splitter.py`**: pure-logic-adjacent Qt widget, tested
  with the `QT_QPA_PLATFORM=offscreen` pattern used by
  `tests/test_recordings_list_layout.py`. Tests: toggling twice returns to the
  original sizes, collapsing zeroes the right pane, `collapse_changed` fires
  with the correct bool, `set_collapsed(True)` on a fresh splitter collapses
  without requiring a prior expand.
- **`RecordingsList` folder actions**: smoke-tested via `python -c` import
  plus a focused unit test that calls `_open_transcripts_folder` /
  `_open_folder` with `os.startfile` monkeypatched (matching how the codebase
  already avoids real filesystem/OS calls in its Qt tests — grep
  `monkeypatch` / `unittest.mock` usage in `tests/test_recordings_list*.py`
  for the existing pattern before writing these).
- **MainWindow menu actions**: covered by the existing
  `python -c "from app.main_window import MainWindow"` smoke test, which
  exercises `_setup_menu` and `_setup_ui` at construction time.

## Manual Verification

1. Launch the app. File menu → confirm both `Open Recordings Folder` and
   `Open Transcripts Folder` are present and each opens the correct folder in
   Explorer.
2. Right-click a single recording → confirm the two folder actions plus
   `View / Transcribe`, `Play Audio`, and `Delete Recording` are all present
   and each opens/acts correctly.
3. Select 2+ recordings, right-click → confirm both folder actions are
   present above `Delete N Recordings` and each opens the correct global
   folder.
4. Click the collapse toggle on the splitter handle → right panel disappears,
   left panel takes the full window. Click again → right panel returns to its
   prior width.
5. Collapse the panel, close and relaunch the app → panel opens already
   collapsed.

## Self-Review

- **Placeholder scan:** none found.
- **Internal consistency:** `RecordingsList`'s two different meanings of
  "Open Recordings Folder" (per-item vs. global) are stated explicitly in
  both the story section and confirmed as intentional, not a contradiction.
- **Scope check:** appropriately sized for one implementation plan — three
  small, largely-independent UI changes touching three files
  (`main_window.py`, `recordings_list.py`, a new `collapsible_splitter.py`)
  plus one config default.
- **Ambiguity check:** all three stories had genuine open questions
  (transcripts-folder target, recordings-folder symmetry, multi-select menu
  scope, collapse-to-zero vs. strip, toggle placement) — all resolved with
  the user before writing this spec.
