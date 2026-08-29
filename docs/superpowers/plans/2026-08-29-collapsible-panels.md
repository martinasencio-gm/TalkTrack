# Collapsible Panels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Transcript column and the Inspector column (Notes/Speakers/Summary) of TalkTrack's 3-column layout independently collapsible, with their collapsed/expanded state — and each Inspector section's expand state — remembered across restarts as the user's last explicit action; opening a recording always shows both outer columns for that viewing session without touching the saved preference; user-resized column widths persist as a fraction of the active screen's width.

**Architecture:** Replace the single flat `QSplitter` in `MainWindow._setup_ui` with two nested instances of the existing, currently-unused `CollapsibleSplitter` (`app/ui/collapsible_splitter.py`) — `splitter2` (Library | Transcript) nested as pane 0 of `splitter1` (splitter2 | Inspector). Five new/changed keys in `DEFAULT_CONFIG["ui"]` back the persistence, with a pure migration function carrying the old `right_panel_collapsed` value across. A new pure module, `app/ui/panel_fractions.py`, does the fraction↔pixel math so it's testable without Qt. `MainWindow` wires `collapse_changed`/`toggled`/`splitterMoved` signals to config writes, guarded by a suppression flag for the one case (opening a recording) that must change the view without changing the preference.

**Tech Stack:** PyQt6 (`QSplitter`, `QTimer`), existing `Config`/`config_migration` JSON persistence, `pytest` + `unittest`.

## Global Constraints

- Config default keys and their exact names/values, per `docs/superpowers/specs/2026-08-29-collapsible-panels-design.md`: `transcript_collapsed: False`, `inspector_collapsed: False` (replaces `right_panel_collapsed`, which is removed from `DEFAULT_CONFIG`), `notes_section_expanded: True`, `speakers_section_expanded: True`, `summary_section_expanded: True`, `panel_fractions: {"library": None, "transcript": None, "inspector": None}`.
- Only an explicit click on a collapse arrow or a section header may write these keys. Opening a recording (double-click) must visibly expand both outer columns without writing to `transcript_collapsed`/`inspector_collapsed`.
- Proportional resize is relative to the **active screen's available width** (`app/utils/screen_utils.get_active_screen(self).availableGeometry().width()`), never the window's own width.
- Do not touch `app/ui/speaker_name_panel.py`'s existing, unrelated `config["ui"]["speakers_collapsed"]` key (the inner per-speaker-row list toggle) — it is a different mechanism at a different UI level than the new `speakers_section_expanded` key.
- Never bare `uv run`; run tests with `.venv\Scripts\python.exe -m pytest tests/ -q`.
- Durable config writes already go through `Config.set()`/`Config.save()` (which uses `atomic_write_json`) — don't bypass it.
- Commits go directly to `master`, no feature branches (per `.claude/rules/ways-of-working.md`). Small, frequent commits; conventional prefixes (`feat:`, `ui:`, `config:`, `fix:`). Never `--amend`. No `Co-Authored-By` lines.

---

### Task 1: Config schema + migration

**Files:**
- Modify: `app/utils/config.py:99-116` (the `"ui"` block in `DEFAULT_CONFIG`), `app/utils/config.py:9-12` (imports), `app/utils/config.py:148-149` (`Config.load()` migration calls)
- Modify: `app/utils/config_migration.py` (add new function)
- Test: `tests/test_config_migration.py` (new test class), `tests/test_config.py` (new test class)

**Interfaces:**
- Produces: `apply_inspector_collapsed_migration(saved, merged)` in `app/utils/config_migration.py` — same pure `(saved_raw_dict_or_None, merged_dict) -> merged_dict` shape as `apply_close_to_tray_migration`.
- Produces: `DEFAULT_CONFIG["ui"]["transcript_collapsed"]`, `["inspector_collapsed"]`, `["notes_section_expanded"]`, `["speakers_section_expanded"]`, `["summary_section_expanded"]`, `["panel_fractions"]` — read by every later task via `self.config.get("ui", "<key>")` / `self.config.set("ui", "<key>", value)`.

- [ ] **Step 1: Write the failing migration tests**

Append to `tests/test_config_migration.py`:

```python
class TestInspectorCollapsedMigration(unittest.TestCase):
    def _merged(self, inspector_collapsed=False):
        return {"ui": {"inspector_collapsed": inspector_collapsed}}

    def test_fresh_install_leaves_default(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        result = apply_inspector_collapsed_migration(None, self._merged())
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_legacy_key_true_is_copied_across(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"right_panel_collapsed": True}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], True)

    def test_legacy_key_false_is_copied_across(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"right_panel_collapsed": False}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=True))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_already_migrated_is_left_alone(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"right_panel_collapsed": True, "inspector_collapsed": False}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_no_legacy_key_leaves_default(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"ui": {"theme": "dark"}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)

    def test_no_ui_section_leaves_default(self):
        from app.utils.config_migration import apply_inspector_collapsed_migration
        saved = {"general": {}}
        result = apply_inspector_collapsed_migration(saved, self._merged(inspector_collapsed=False))
        self.assertEqual(result["ui"]["inspector_collapsed"], False)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config_migration.py::TestInspectorCollapsedMigration -v`
Expected: FAIL — `ImportError: cannot import name 'apply_inspector_collapsed_migration'`

- [ ] **Step 3: Implement the migration function**

Append to `app/utils/config_migration.py`:

```python
def apply_inspector_collapsed_migration(saved, merged):
    """Carry the legacy ui.right_panel_collapsed value onto ui.inspector_collapsed.

    right_panel_collapsed was dead scaffolding from an older two-pane layout
    spec (docs/superpowers/specs/2026-08-14-phase1-folder-nav-collapsible-panel-design.md),
    never wired up. inspector_collapsed is the live key for the current
    3-column layout's Inspector column — this migration only matters for a
    settings.json that predates this feature but happened to carry the old
    unused key.
    """
    if not saved:
        return merged                      # brand-new install: defaults are correct
    ui = saved.get("ui") or {}
    if "inspector_collapsed" in ui:
        return merged                      # already migrated; respect their choice
    if "right_panel_collapsed" in ui:
        merged["ui"]["inspector_collapsed"] = ui["right_panel_collapsed"]
    return merged
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config_migration.py::TestInspectorCollapsedMigration -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Update DEFAULT_CONFIG and wire the migration into Config.load()**

In `app/utils/config.py`, change the import block (lines 9-12):

```python
from app.utils.config_migration import (
    apply_close_to_tray_migration,
    apply_inspector_collapsed_migration,
    apply_meeting_detection_migration,
)
```

Replace the `"ui"` block (lines 99-116):

```python
    "ui": {
        "theme": "dark",
        "speakers_collapsed": False,
        "transcript_collapsed": False,
        "inspector_collapsed": False,
        "notes_section_expanded": True,
        "speakers_section_expanded": True,
        "summary_section_expanded": True,
        # Fraction of the active screen's available width each column
        # occupied the last time the user dragged its splitter handle.
        # None = use the fixed pixel defaults in MainWindow._setup_ui.
        "panel_fractions": {
            "library": None,
            "transcript": None,
            "inspector": None,
        },
        "audio_sources_collapsed": False,
        "recordings_collapsed": False,
        "activity_widget_position": None,
        # Full window rect [x, y, w, h] and whether it was maximized, saved
        # on quit and restored (clamped to connected screens) on next launch.
        "window_geometry": None,
        "window_maximized": False,
        "compact_strip_visible": False,
        "compact_strip_position": None,
        "strip_variant": "full",
        # Where a double-click on the capture bar lands, as an entry point
        # into the fixed full -> compact_bar -> pill -> full chain.
        "double_click_target": "compact_bar",  # "compact_bar" | "pill"
    },
```

In `Config.load()`, add the new migration call after the existing two (line 149):

```python
        self._data = apply_meeting_detection_migration(saved, self._data)
        self._data = apply_close_to_tray_migration(saved, self._data)
        self._data = apply_inspector_collapsed_migration(saved, self._data)
```

- [ ] **Step 6: Write the failing default-value tests**

Append to `tests/test_config.py`:

```python
class TestCollapsiblePanelDefaults(unittest.TestCase):
    def test_new_ui_keys_have_expected_defaults(self):
        from app.utils.config import Config
        config = Config()
        self.assertEqual(config.get("ui", "transcript_collapsed"), False)
        self.assertEqual(config.get("ui", "inspector_collapsed"), False)
        self.assertEqual(config.get("ui", "notes_section_expanded"), True)
        self.assertEqual(config.get("ui", "speakers_section_expanded"), True)
        self.assertEqual(config.get("ui", "summary_section_expanded"), True)
        self.assertEqual(
            config.get("ui", "panel_fractions"),
            {"library": None, "transcript": None, "inspector": None},
        )

    def test_right_panel_collapsed_key_is_gone(self):
        from app.utils.config import DEFAULT_CONFIG
        self.assertNotIn("right_panel_collapsed", DEFAULT_CONFIG["ui"])

    def test_panel_fraction_round_trips_through_save_and_load(self):
        from app.utils.config import Config
        config = Config()
        config.set("ui", "panel_fractions", {"library": 0.2, "transcript": 0.6, "inspector": 0.2})
        reloaded = Config()
        self.assertEqual(
            reloaded.get("ui", "panel_fractions"),
            {"library": 0.2, "transcript": 0.6, "inspector": 0.2},
        )
```

This relies on `tests/conftest.py`'s autouse `_isolate_config` fixture (already present) to point `Config` at a throwaway settings file — no extra patching needed in this file.

- [ ] **Step 7: Run to verify it fails, then passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py::TestCollapsiblePanelDefaults -v`
Expected before Step 5: FAIL (`KeyError` on the new keys). After Step 5 is in place: PASS (3 tests).

- [ ] **Step 8: Run the full config test file to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_config_migration.py -v`
Expected: PASS, all tests (including pre-existing ones).

- [ ] **Step 9: Commit**

```bash
git add app/utils/config.py app/utils/config_migration.py tests/test_config.py tests/test_config_migration.py
git commit -m "config: add collapsible-panel keys, migrate right_panel_collapsed"
```

---

### Task 2: Pure pane-fraction math module

**Files:**
- Create: `app/ui/panel_fractions.py`
- Test: `tests/test_panel_fraction_restore.py`

**Interfaces:**
- Consumes: nothing (pure functions, no Qt, no Config).
- Produces: `fraction_for_size(pixel_size, screen_width) -> float | None`, `resolve_pane_size(fraction, screen_width, default_size) -> int`, `resolve_splitter_sizes(fractions, keys, screen_width, default_sizes) -> list[int]` — used by Task 7's `_restore_panel_fractions`/`_flush_splitter1_fraction`/`_flush_splitter2_fraction` on `MainWindow`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_panel_fraction_restore.py`:

```python
"""Pure fraction<->pixel math for the collapsible-panel proportional resize
feature (docs/superpowers/specs/2026-08-29-collapsible-panels-design.md).
No Qt involved — MainWindow wires these into splitter setSizes()/sizes()
calls, which is covered separately by tests/test_collapsible_panel_resize_persistence.py.
"""
import unittest

from app.ui.panel_fractions import fraction_for_size, resolve_pane_size, resolve_splitter_sizes


class TestFractionForSize(unittest.TestCase):
    def test_computes_fraction_of_screen_width(self):
        self.assertAlmostEqual(fraction_for_size(480, 1920), 0.25)

    def test_zero_screen_width_returns_none(self):
        self.assertIsNone(fraction_for_size(480, 0))

    def test_negative_screen_width_returns_none(self):
        self.assertIsNone(fraction_for_size(480, -1))

    def test_none_screen_width_returns_none(self):
        self.assertIsNone(fraction_for_size(480, None))


class TestResolvePaneSize(unittest.TestCase):
    def test_none_fraction_falls_back_to_default(self):
        self.assertEqual(resolve_pane_size(None, 1920, 322), 322)

    def test_fraction_scales_to_current_screen_width(self):
        self.assertEqual(resolve_pane_size(0.25, 1920, 322), 480)

    def test_zero_screen_width_falls_back_to_default(self):
        self.assertEqual(resolve_pane_size(0.25, 0, 322), 322)

    def test_negative_screen_width_falls_back_to_default(self):
        self.assertEqual(resolve_pane_size(0.25, -100, 322), 322)

    def test_result_is_floored_at_one_pixel(self):
        self.assertEqual(resolve_pane_size(0.0001, 100, 322), 1)


class TestResolveSplitterSizes(unittest.TestCase):
    def test_all_none_fractions_return_defaults(self):
        fractions = {"library": None, "transcript": None}
        sizes = resolve_splitter_sizes(fractions, ["library", "transcript"], 1920, [262, 776])
        self.assertEqual(sizes, [262, 776])

    def test_set_fraction_overrides_its_slot(self):
        fractions = {"library": 0.2, "transcript": None}
        sizes = resolve_splitter_sizes(fractions, ["library", "transcript"], 1000, [262, 776])
        self.assertEqual(sizes, [200, 776])

    def test_none_key_always_uses_default(self):
        # splitter1's pane 0 is splitter2 itself; its width is never saved
        # as its own fraction — only the paired "inspector" slot is.
        fractions = {"inspector": 0.3}
        sizes = resolve_splitter_sizes(fractions, [None, "inspector"], 1000, [1038, 322])
        self.assertEqual(sizes, [1038, 300])

    def test_missing_key_in_fractions_dict_falls_back_to_default(self):
        sizes = resolve_splitter_sizes({}, ["library", "transcript"], 1920, [262, 776])
        self.assertEqual(sizes, [262, 776])

    def test_round_trip_save_then_restore_recovers_the_same_size(self):
        original_size = 540
        screen_width = 1920
        fraction = fraction_for_size(original_size, screen_width)
        restored_size = resolve_pane_size(fraction, screen_width, 322)
        self.assertEqual(restored_size, original_size)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_panel_fraction_restore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ui.panel_fractions'`

- [ ] **Step 3: Write the implementation**

Create `app/ui/panel_fractions.py`:

```python
"""Pure fraction<->pixel math for saving/restoring splitter widths relative
to the active screen's resolution, not the window's own width or raw pixels.

No Qt here by design — MainWindow resolves the active QScreen
(app/utils/screen_utils.get_active_screen) and calls these with plain
numbers, keeping the math testable without constructing any widgets.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""


def fraction_for_size(pixel_size, screen_width):
    """Convert a pixel size into a fraction of the screen width, for saving.

    Returns None when screen_width is missing or non-positive — a caller
    with no usable screen shouldn't persist a divide-by-zero result.
    """
    if not screen_width or screen_width <= 0:
        return None
    return pixel_size / screen_width


def resolve_pane_size(fraction, screen_width, default_size):
    """Convert a saved fraction back into a pixel size, for restoring.

    Falls back to default_size when the fraction was never saved (None) or
    the current screen width is unusable. Floors at 1px so a tiny fraction
    can never produce a zero or negative setSizes() entry.
    """
    if fraction is None:
        return default_size
    if not screen_width or screen_width <= 0:
        return default_size
    return max(1, round(fraction * screen_width))


def resolve_splitter_sizes(fractions, keys, screen_width, default_sizes):
    """Resolve one splitter's setSizes() list from saved fractions.

    fractions: the ui.panel_fractions dict (key -> fraction or None).
    keys: the panel_fractions key for each pane, in setSizes() order; a
        None entry means "always use the default for this pane" — used for
        splitter1's pane 0, which holds splitter2 itself rather than a
        single fraction-tracked column.
    default_sizes: fallback pixel sizes, same order as keys.
    """
    sizes = []
    for key, default in zip(keys, default_sizes):
        if key is None:
            sizes.append(default)
        else:
            sizes.append(resolve_pane_size(fractions.get(key), screen_width, default))
    return sizes
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_panel_fraction_restore.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ui/panel_fractions.py tests/test_panel_fraction_restore.py
git commit -m "feat(ui): add pure fraction<->pixel math for proportional panel resize"
```

---

### Task 3: Inspector sections stop forcing themselves open

**Files:**
- Modify: `app/ui/inspector.py:100-146` (`add_notes_panel`, `add_speakers_panel`, `add_summary_panel`)
- Test: `tests/test_inspector.py` (new test class)

**Interfaces:**
- Consumes: `CollapsibleSection.set_expanded(bool)` (already exists, `app/ui/collapsible_section.py:100`).
- Produces: `InspectorWidget.notes_section` / `.speakers_section` / `.summary_section` now start collapsed (their `CollapsibleSection` default) after `add_*_panel` is called — MainWindow (Task 5) becomes solely responsible for setting their expanded state, from config.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inspector.py`:

```python
class TestInspectorSectionsStartCollapsed(unittest.TestCase):
    """add_*_panel used to force set_expanded(True) unconditionally — now
    MainWindow decides each section's initial state from config
    (ui.notes_section_expanded / speakers_section_expanded /
    summary_section_expanded), see app/main_window.py's
    _restore_panel_collapse_state.
    """

    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_add_notes_panel_does_not_force_expanded(self):
        inspector = InspectorWidget()
        inspector.add_notes_panel(QWidget())
        self.assertFalse(inspector.notes_section.is_expanded())

    def test_add_speakers_panel_does_not_force_expanded(self):
        inspector = InspectorWidget()
        inspector.add_speakers_panel(QWidget())
        self.assertFalse(inspector.speakers_section.is_expanded())

    def test_add_summary_panel_does_not_force_expanded(self):
        inspector = InspectorWidget()
        inspector.add_summary_panel(QWidget())
        self.assertFalse(inspector.summary_section.is_expanded())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_inspector.py::TestInspectorSectionsStartCollapsed -v`
Expected: FAIL — all three `assertFalse` calls fail because each section is still forced expanded.

- [ ] **Step 3: Remove the forced-expand calls**

In `app/ui/inspector.py`, remove the `set_expanded(True)` line from each method:

```python
    def add_notes_panel(self, panel):
        self.notes_section.content_layout().addWidget(panel)

    def add_speakers_panel(self, panel):
        self.speakers_section.content_layout().addWidget(panel)
```

And in `add_summary_panel`, remove the trailing `self.summary_section.set_expanded(True)` line (keep everything above it — the `ai_off_widget` construction and `self.ai_off_widget.setVisible(False)` line stay unchanged):

```python
        self.summary_section.content_layout().addWidget(self.ai_off_widget)
        self.ai_off_widget.setVisible(False)
```

(no `set_expanded(True)` call after this anymore)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_inspector.py -v`
Expected: PASS, all tests in the file (including the pre-existing `TestInspectorEmptyState`/`TestInspectorAiConfiguredState` classes, which don't assert on section-expanded state).

- [ ] **Step 5: Commit**

```bash
git add app/ui/inspector.py tests/test_inspector.py
git commit -m "ui: stop InspectorWidget forcing its sections open on add"
```

---

### Task 4: Nested CollapsibleSplitter restructuring

**Files:**
- Modify: `app/main_window.py:389-445` (the splitter construction in `_setup_ui`), `app/main_window.py:479-481` (delete the dead `_on_right_panel_collapse_changed` stub), `app/main_window.py:14-17` (drop the now-unused `QSplitter` import)
- Test: `tests/test_collapsible_panel_splitters.py` (new file)

**Interfaces:**
- Consumes: `CollapsibleSplitter` (`app/ui/collapsible_splitter.py`, already imported at `app/main_window.py:34`) — `.addWidget(widget)`, `.setSizes([...])`, `.setCollapsible(i, bool)`, `.setStretchFactor(i, n)`, `.is_collapsed()`, `.set_collapsed(bool)`, `.collapse_changed` signal, `.splitterMoved` signal (inherited from `QSplitter`).
- Produces: `self.splitter1` (outer: `self.splitter2` | `self.inspector`) and `self.splitter2` (inner: `self.library_panel` | `self.transcript_panel`) on `MainWindow` — read by Task 5 (persistence wiring), Task 6 (double-click guard), and Task 7 (resize persistence). `self.splitter` no longer exists.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collapsible_panel_splitters.py`:

```python
"""Task 4: the flat 3-pane QSplitter was replaced by two nested
CollapsibleSplitters so the Transcript and Inspector columns can collapse
independently. See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.ui.collapsible_splitter import CollapsibleSplitter

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestNestedSplitterStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_splitter1_and_splitter2_are_collapsible_splitters(self):
        window = _make_window(self)
        self.assertIsInstance(window.splitter1, CollapsibleSplitter)
        self.assertIsInstance(window.splitter2, CollapsibleSplitter)

    def test_splitter1_holds_splitter2_and_inspector(self):
        window = _make_window(self)
        self.assertIs(window.splitter1.widget(0), window.splitter2)
        self.assertIs(window.splitter1.widget(1), window.inspector)

    def test_splitter2_holds_library_and_transcript(self):
        window = _make_window(self)
        self.assertIs(window.splitter2.widget(0), window.library_panel)
        self.assertIs(window.splitter2.widget(1), window.transcript_panel)

    def test_main_window_has_no_flat_splitter_attribute(self):
        window = _make_window(self)
        self.assertFalse(hasattr(window, "splitter"))

    def test_collapsing_splitter2_zeroes_the_transcript_pane(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertEqual(window.splitter2.sizes()[1], 0)

    def test_collapsing_splitter1_zeroes_the_inspector_pane(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertEqual(window.splitter1.sizes()[1], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_splitters.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'splitter1'`

- [ ] **Step 3: Replace the flat splitter with nested CollapsibleSplitters**

In `app/main_window.py`, replace lines 389-445 (from `# Three-column splitter` through `main_layout.addWidget(self.splitter, 1)`) with:

```python
        # Three-column layout: two nested CollapsibleSplitters, each
        # collapsing its own pane 1 — see
        # docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
        # splitter2 = Library | Transcript (collapses Transcript)
        # splitter1 = splitter2 | Inspector (collapses Inspector)
        self.splitter2 = CollapsibleSplitter(Qt.Orientation.Horizontal)
        self.splitter2.setHandleWidth(1)
        self.splitter2.setStyleSheet("QSplitter::handle { background-color: #292b31; }")

        self.splitter1 = CollapsibleSplitter(Qt.Orientation.Horizontal)
        self.splitter1.setHandleWidth(1)
        self.splitter1.setStyleSheet("QSplitter::handle { background-color: #292b31; }")

        # Column A: Library
        self.library_panel = QWidget()
        library_layout = QVBoxLayout(self.library_panel)
        library_layout.setContentsMargins(0, 0, 0, 0)
        recordings_dir = self.config.get("output", "directory")
        self.recordings_list = RecordingsList(recordings_dir)
        library_layout.addWidget(self.recordings_list)
        self.splitter2.addWidget(self.library_panel)

        # Column B: Transcript
        self.transcript_panel = QWidget()
        transcript_layout = QVBoxLayout(self.transcript_panel)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        
        self.recording_header = RecordingHeader()
        transcript_layout.addWidget(self.recording_header)

        # Built here (not by TranscriptViewer) because it lives in the
        # Inspector's "Speakers" section, not the transcript column.
        from app.ui.speaker_name_panel import SpeakerNamePanel
        self.speaker_panel = SpeakerNamePanel(config=self.config)

        self.transcript_viewer = TranscriptViewer(config=self.config, speaker_panel=self.speaker_panel)
        transcript_layout.addWidget(self.transcript_viewer)
        self.splitter2.addWidget(self.transcript_panel)

        self.splitter2.setCollapsible(0, False)
        self.splitter2.setCollapsible(1, False)
        self.splitter2.setStretchFactor(0, 0)
        self.splitter2.setStretchFactor(1, 1)

        # Column C: Inspector
        self.inspector = InspectorWidget()

        self.notes_panel = NotesPanel()
        self.inspector.add_notes_panel(self.notes_panel)

        self.inspector.add_speakers_panel(self.speaker_panel)

        self.summary_panel = SummaryPanel()
        self.inspector.add_summary_panel(self.summary_panel)
        
        self.chat_panel = ChatPanel()
        self.inspector.add_chat_panel(self.chat_panel)

        self.splitter1.addWidget(self.splitter2)
        self.splitter1.addWidget(self.inspector)
        self.splitter1.setCollapsible(0, False)
        self.splitter1.setCollapsible(1, False)
        self.splitter1.setStretchFactor(0, 1)
        self.splitter1.setStretchFactor(1, 0)

        # Default sizes — library and inspector fixed-ish, transcript
        # absorbs resize slack (per the capture-bar design spec). Overridden
        # by _restore_panel_fractions() / _restore_panel_collapse_state() in
        # __init__ once config is available.
        self.splitter2.setSizes([262, 776])
        self.splitter1.setSizes([1038, 322])
        main_layout.addWidget(self.splitter1, 1)
```

Then delete the now-dead stub right after this method's body (lines 479-481 in the original file):

```python
    def _on_right_panel_collapse_changed(self, collapsed):
        self.config.set("ui", "right_panel_collapsed", collapsed)
```

(Task 5 adds its two proper replacements, `_on_transcript_collapse_changed` and `_on_inspector_collapse_changed`, in the same spot.)

Finally, drop `QSplitter` from the PyQt6.QtWidgets import (line 16), since `self.splitter` no longer exists and nothing else in the file references `QSplitter` directly:

```python
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QMenu, QMessageBox, QApplication, QInputDialog, QStatusBar
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_splitters.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (aside from the two pre-existing `tests/test_single_instance.py` failures if TalkTrack itself is running).

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py tests/test_collapsible_panel_splitters.py
git commit -m "ui: nest two CollapsibleSplitters for the Transcript and Inspector columns"
```

---

### Task 5: Collapse-state persistence wiring

**Files:**
- Modify: `app/main_window.py` (add two handler methods where the deleted stub was, near line 479; add a `_restore_panel_collapse_state` method; wire `collapse_changed`/`toggled` signals in `_connect_signals`; call the restore method from `__init__`)
- Test: `tests/test_collapsible_panel_persistence.py` (new file)

**Interfaces:**
- Consumes: `Config.get("ui", key)` / `Config.set("ui", key, value)` (Task 1); `window.splitter1` / `window.splitter2` (Task 4); `window.inspector.notes_section` / `.speakers_section` / `.summary_section` (`app/ui/inspector.py`, unchanged attribute names); `CollapsibleSplitter.collapse_changed` / `.set_collapsed(bool)` (`app/ui/collapsible_splitter.py`); `CollapsibleSection.toggled` / `.set_expanded(bool)` (`app/ui/collapsible_section.py`).
- Produces: `MainWindow._on_transcript_collapse_changed(collapsed)`, `MainWindow._on_inspector_collapse_changed(collapsed)`, `MainWindow._restore_panel_collapse_state()` — the second is called again, guarded, by Task 6's double-click handler.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collapsible_panel_persistence.py`:

```python
"""Task 5: collapsing/expanding the Transcript or Inspector column, or an
Inspector section header, persists to config; a fresh MainWindow restores
from it. See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestOuterColumnCollapsePersists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_collapsing_transcript_persists_transcript_collapsed(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))

    def test_expanding_transcript_persists_false(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()  # collapse
        window.splitter2.toggle_collapse()  # expand
        self.assertFalse(window.config.get("ui", "transcript_collapsed"))

    def test_collapsing_inspector_persists_inspector_collapsed(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

    def test_fresh_window_restores_a_collapsed_transcript_column(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))

        window2 = _make_window(self)
        self.assertTrue(window2.splitter2.is_collapsed())

    def test_fresh_window_restores_a_collapsed_inspector_column(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

        window2 = _make_window(self)
        self.assertTrue(window2.splitter1.is_collapsed())


class TestInspectorSectionCollapsePersists(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_collapsing_speakers_section_persists_only_that_key(self):
        window = _make_window(self)
        window.inspector.speakers_section.set_expanded(True)   # starts False; open it
        window.inspector.speakers_section.set_expanded(False)  # then collapse it explicitly

        self.assertFalse(window.config.get("ui", "speakers_section_expanded"))
        self.assertEqual(window.config.get("ui", "notes_section_expanded"), True)
        self.assertEqual(window.config.get("ui", "summary_section_expanded"), True)

    def test_fresh_window_restores_section_expand_state(self):
        window = _make_window(self)
        window.inspector.notes_section.set_expanded(True)
        self.assertTrue(window.config.get("ui", "notes_section_expanded"))

        window2 = _make_window(self)
        self.assertTrue(window2.inspector.notes_section.is_expanded())
        # Untouched sections keep their own default.
        self.assertFalse(window2.inspector.speakers_section.is_expanded())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_persistence.py -v`
Expected: FAIL — `config.get("ui", "transcript_collapsed")` stays `False` after `toggle_collapse()` because nothing writes it yet; restore tests fail because sections start at their `CollapsibleSection` default regardless of a prior window's state.

- [ ] **Step 3: Add the handler methods and the restore method**

In `app/main_window.py`, where the dead stub was deleted in Task 4 (right after `_setup_ui`'s closing `main_layout.addWidget(self.splitter1, 1)` block, before `def _setup_statusbar(self):`), add:

```python
    def _on_transcript_collapse_changed(self, collapsed):
        if getattr(self, "_suppress_collapse_persist", False):
            return
        self.config.set("ui", "transcript_collapsed", collapsed)

    def _on_inspector_collapse_changed(self, collapsed):
        if getattr(self, "_suppress_collapse_persist", False):
            return
        self.config.set("ui", "inspector_collapsed", collapsed)

    def _restore_panel_collapse_state(self):
        """Apply the saved collapse/expand state to both outer columns and
        all three Inspector sections. Called once from __init__, right
        after _setup_ui() builds the splitters and sections and before
        _connect_signals() wires the persistence handlers below — so this
        initial restore never itself gets written back to config."""
        self.splitter2.set_collapsed(self.config.get("ui", "transcript_collapsed"))
        self.splitter1.set_collapsed(self.config.get("ui", "inspector_collapsed"))
        self.inspector.notes_section.set_expanded(self.config.get("ui", "notes_section_expanded"))
        self.inspector.speakers_section.set_expanded(self.config.get("ui", "speakers_section_expanded"))
        self.inspector.summary_section.set_expanded(self.config.get("ui", "summary_section_expanded"))
```

- [ ] **Step 4: Call the restore method from `__init__`**

In `app/main_window.py`, change:

```python
        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._connect_signals()
```

to:

```python
        self._setup_menu()
        self._setup_ui()
        self._restore_panel_collapse_state()
        self._setup_statusbar()
        self._connect_signals()
```

(`_restore_panel_fractions()`, added in Task 7, is inserted before this same `_restore_panel_collapse_state()` call — fraction restore first, then collapse state, so a collapsed splitter always ends up at `[total, 0]` regardless of what the fraction restore just set. Task 7 covers that insertion; this task's line only adds `_restore_panel_collapse_state()`.)

- [ ] **Step 5: Wire the signals in `_connect_signals`**

In `app/main_window.py`, at the top of `_connect_signals` (line 523), change:

```python
    def _connect_signals(self):
        self.inspector.connect_provider_requested.connect(
            lambda: self._open_settings(initial_tab="AI Assistant")
        )
        self.transcript_viewer.open_last_requested.connect(self._open_last_recording)
```

to:

```python
    def _connect_signals(self):
        self.splitter2.collapse_changed.connect(self._on_transcript_collapse_changed)
        self.splitter1.collapse_changed.connect(self._on_inspector_collapse_changed)
        self.inspector.notes_section.toggled.connect(
            lambda expanded: self.config.set("ui", "notes_section_expanded", expanded)
        )
        self.inspector.speakers_section.toggled.connect(
            lambda expanded: self.config.set("ui", "speakers_section_expanded", expanded)
        )
        self.inspector.summary_section.toggled.connect(
            lambda expanded: self.config.set("ui", "summary_section_expanded", expanded)
        )

        self.inspector.connect_provider_requested.connect(
            lambda: self._open_settings(initial_tab="AI Assistant")
        )
        self.transcript_viewer.open_last_requested.connect(self._open_last_recording)
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_persistence.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Run the full suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (aside from the two pre-existing `tests/test_single_instance.py` failures if TalkTrack itself is running).

- [ ] **Step 8: Commit**

```bash
git add app/main_window.py tests/test_collapsible_panel_persistence.py
git commit -m "feat(ui): persist and restore collapsible-panel state across restarts"
```

---

### Task 6: Double-click force-open without persisting

**Files:**
- Modify: `app/main_window.py:103-104` (add the suppression flag), `app/main_window.py:2189-2201` (`_do_on_recording_selected`, add the force-expand call)
- Test: `tests/test_collapsible_panel_double_click.py` (new file)

**Interfaces:**
- Consumes: `self._suppress_collapse_persist` (read by Task 5's `_on_transcript_collapse_changed`/`_on_inspector_collapse_changed`); `self.splitter1.set_collapsed(bool)` / `self.splitter2.set_collapsed(bool)` (Task 4).
- Produces: `MainWindow._expand_panels_for_recording_view()` — a small, independently callable method, so this behavior is testable without driving all of `_do_on_recording_selected`'s file-loading side effects.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collapsible_panel_double_click.py`:

```python
"""Task 6: opening a recording always shows both outer columns for that
viewing session, without overwriting the user's saved collapse preference.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestExpandPanelsForRecordingView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_expands_a_collapsed_transcript_column(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()
        self.assertTrue(window.splitter2.is_collapsed())

        window._expand_panels_for_recording_view()

        self.assertFalse(window.splitter2.is_collapsed())

    def test_expands_a_collapsed_inspector_column(self):
        window = _make_window(self)
        window.splitter1.toggle_collapse()
        self.assertTrue(window.splitter1.is_collapsed())

        window._expand_panels_for_recording_view()

        self.assertFalse(window.splitter1.is_collapsed())

    def test_does_not_persist_the_expand(self):
        window = _make_window(self)
        window.splitter2.toggle_collapse()  # persists transcript_collapsed=True
        window.splitter1.toggle_collapse()  # persists inspector_collapsed=True
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

        window._expand_panels_for_recording_view()

        # Visually open now, but the saved preference must be untouched —
        # a fresh launch should still come up collapsed.
        self.assertFalse(window.splitter2.is_collapsed())
        self.assertFalse(window.splitter1.is_collapsed())
        self.assertTrue(window.config.get("ui", "transcript_collapsed"))
        self.assertTrue(window.config.get("ui", "inspector_collapsed"))

    def test_is_a_noop_when_already_expanded(self):
        window = _make_window(self)
        self.assertFalse(window.splitter1.is_collapsed())
        self.assertFalse(window.splitter2.is_collapsed())

        window._expand_panels_for_recording_view()  # must not raise or toggle anything

        self.assertFalse(window.splitter1.is_collapsed())
        self.assertFalse(window.splitter2.is_collapsed())

    def test_do_on_recording_selected_calls_the_expand_helper(self):
        from unittest.mock import patch
        window = _make_window(self)
        metadata = {
            "directory": window.config.get("output", "directory"),
            "audio_files": {},
        }
        with patch.object(window, "_expand_panels_for_recording_view") as mock_expand:
            window._do_on_recording_selected(metadata)
        mock_expand.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_double_click.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute '_expand_panels_for_recording_view'`

- [ ] **Step 3: Add the suppression flag**

In `app/main_window.py`, change:

```python
        self._closing = False
        self._silent_capture_warned = False
```

to:

```python
        self._closing = False
        # Set around any programmatic splitter.set_collapsed() call whose
        # effect must NOT be written to config — currently only the
        # double-click force-open below. CollapsibleSplitter fires the same
        # collapse_changed signal for a real click and a programmatic call;
        # this is how the persistence handlers (_on_transcript_collapse_changed
        # / _on_inspector_collapse_changed) tell the two apart.
        self._suppress_collapse_persist = False
        self._silent_capture_warned = False
```

- [ ] **Step 4: Add the helper method and call it from `_do_on_recording_selected`**

In `app/main_window.py`, add a new method right before `_do_on_recording_selected` (line 2189):

```python
    def _expand_panels_for_recording_view(self):
        """Force both the Transcript and Inspector columns open for this
        viewing session, without changing the user's saved collapse
        preference — see _do_on_recording_selected and
        docs/superpowers/specs/2026-08-29-collapsible-panels-design.md."""
        self._suppress_collapse_persist = True
        try:
            self.splitter2.set_collapsed(False)
            self.splitter1.set_collapsed(False)
        finally:
            self._suppress_collapse_persist = False

    def _do_on_recording_selected(self, metadata):
```

(the existing `def _do_on_recording_selected(self, metadata):` line is kept — only the new method is inserted above it)

Then, as the first line inside `_do_on_recording_selected`'s body, change:

```python
    def _do_on_recording_selected(self, metadata):
        # Clear any stale calendar-suggestion banner from the previously
        # displayed recording — see _on_recording_finished for why.
        self.calendar_banner.hide_and_clear()
```

to:

```python
    def _do_on_recording_selected(self, metadata):
        self._expand_panels_for_recording_view()

        # Clear any stale calendar-suggestion banner from the previously
        # displayed recording — see _on_recording_finished for why.
        self.calendar_banner.hide_and_clear()
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_double_click.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (aside from the two pre-existing `tests/test_single_instance.py` failures if TalkTrack itself is running).

- [ ] **Step 7: Commit**

```bash
git add app/main_window.py tests/test_collapsible_panel_double_click.py
git commit -m "feat(ui): opening a recording always expands both outer columns"
```

---

### Task 7: Proportional resize persistence

**Files:**
- Modify: `app/main_window.py:108-110` (add two debounce timers, mirroring `_gain_save_timer`), `app/main_window.py` (add `_restore_panel_fractions`, `_flush_splitter1_fraction`, `_flush_splitter2_fraction`, `_on_splitter1_moved`, `_on_splitter2_moved`; call `_restore_panel_fractions()` before `_restore_panel_collapse_state()` in `__init__`; wire `splitterMoved` in `_connect_signals`; flush both timers in `closeEvent`)
- Test: `tests/test_collapsible_panel_resize_persistence.py` (new file)

**Interfaces:**
- Consumes: `app.ui.panel_fractions.fraction_for_size` / `resolve_splitter_sizes` (Task 2); `app.utils.screen_utils.get_active_screen` (existing); `self.splitter1` / `self.splitter2` (Task 4).
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collapsible_panel_resize_persistence.py`:

```python
"""Task 7: user-resized column widths are saved as a fraction of the active
screen's available width, and restored the same way on the next launch.
Drives the flush/restore methods directly rather than simulating a real
mouse drag — QSplitter.splitterMoved wiring itself is a one-line connect,
not worth flaky drag simulation to cover.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


def _make_window(test_case):
    _get_app()
    from app.main_window import MainWindow
    window = MainWindow()

    def _close():
        window._really_quit = True
        if hasattr(window, "_meeting_poll_timer"):
            window._meeting_poll_timer.stop()
        if hasattr(window, "_com_poller") and window._com_poller:
            window._com_poller.stop()
        window.close()
    test_case.addCleanup(_close)
    return window


class TestFlushSplitterFraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_flush_splitter2_writes_library_and_transcript_fractions(self):
        window = _make_window(self)
        window.splitter2.resize(1000, 600)
        window.splitter2.setSizes([300, 700])

        window._flush_splitter2_fraction()

        fractions = window.config.get("ui", "panel_fractions")
        self.assertIsNotNone(fractions["library"])
        self.assertIsNotNone(fractions["transcript"])

    def test_flush_splitter1_writes_only_inspector_fraction(self):
        window = _make_window(self)
        window.splitter1.resize(1400, 600)
        window.splitter1.setSizes([1000, 400])

        window._flush_splitter1_fraction()

        fractions = window.config.get("ui", "panel_fractions")
        self.assertIsNotNone(fractions["inspector"])

    def test_flush_with_fewer_than_two_sizes_does_not_raise(self):
        window = _make_window(self)
        window.splitter1.setSizes([100])  # degenerate — must not crash
        window._flush_splitter1_fraction()  # no assertion needed; just must not raise


class TestRestorePanelFractions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_restore_applies_a_previously_saved_fraction(self):
        window = _make_window(self)
        window.splitter2.resize(1000, 600)
        window.splitter2.setSizes([300, 700])
        window._flush_splitter2_fraction()
        saved_fractions = window.config.get("ui", "panel_fractions")

        window2 = _make_window(self)
        window2.config.set("ui", "panel_fractions", saved_fractions)
        window2.splitter2.resize(1000, 600)
        window2._restore_panel_fractions()

        # setSizes() normalizes proportionally to actual widget width, so
        # assert the ratio rather than exact pixels.
        sizes = window2.splitter2.sizes()
        self.assertGreater(sizes[1], sizes[0])  # transcript still bigger than library

    def test_restore_with_no_saved_fractions_does_not_raise(self):
        window = _make_window(self)
        window._restore_panel_fractions()  # all-None defaults; must not raise


class TestSplitterMovedIsWired(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def test_dragging_splitter2_starts_the_debounce_timer(self):
        window = _make_window(self)
        self.assertFalse(window._panel_fraction_timer2.isActive())
        window.splitter2.splitterMoved.emit(300, 1)
        self.assertTrue(window._panel_fraction_timer2.isActive())

    def test_dragging_splitter1_starts_the_debounce_timer(self):
        window = _make_window(self)
        self.assertFalse(window._panel_fraction_timer1.isActive())
        window.splitter1.splitterMoved.emit(1000, 1)
        self.assertTrue(window._panel_fraction_timer1.isActive())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_resize_persistence.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute '_flush_splitter2_fraction'`

- [ ] **Step 3: Add the two debounce timers**

In `app/main_window.py`, change:

```python
        self._pending_gain = None  # holds latest slider value awaiting debounced save
        self._gain_save_timer = QTimer(self)
        self._gain_save_timer.setSingleShot(True)
        self._gain_save_timer.timeout.connect(self._flush_gain_to_config)
```

to:

```python
        self._pending_gain = None  # holds latest slider value awaiting debounced save
        self._gain_save_timer = QTimer(self)
        self._gain_save_timer.setSingleShot(True)
        self._gain_save_timer.timeout.connect(self._flush_gain_to_config)

        # Debounced saves for user-dragged splitter handles, mirroring the
        # gain-slider pattern above. One timer per splitter since either can
        # be dragged independently.
        self._panel_fraction_timer1 = QTimer(self)
        self._panel_fraction_timer1.setSingleShot(True)
        self._panel_fraction_timer1.timeout.connect(self._flush_splitter1_fraction)
        self._panel_fraction_timer2 = QTimer(self)
        self._panel_fraction_timer2.setSingleShot(True)
        self._panel_fraction_timer2.timeout.connect(self._flush_splitter2_fraction)
```

- [ ] **Step 4: Add the restore, flush, and moved-handler methods**

In `app/main_window.py`, add these methods anywhere in the class body (e.g. immediately after `_restore_panel_collapse_state`, added in Task 5):

```python
    def _restore_panel_fractions(self):
        """Apply saved screen-relative widths to both splitters. Called once
        from __init__, before _restore_panel_collapse_state() — so a
        collapsed splitter still ends up at [total, 0] regardless of what
        this just set. Any fraction still None (fresh install, or that
        handle never dragged) leaves the fixed pixel defaults from
        _setup_ui alone."""
        from app.ui.panel_fractions import resolve_splitter_sizes
        from app.utils.screen_utils import get_active_screen
        screen = get_active_screen(self)
        screen_width = screen.availableGeometry().width() if screen else 0
        fractions = self.config.get("ui", "panel_fractions") or {}
        self.splitter2.setSizes(
            resolve_splitter_sizes(fractions, ["library", "transcript"], screen_width, [262, 776])
        )
        self.splitter1.setSizes(
            resolve_splitter_sizes(fractions, [None, "inspector"], screen_width, [1038, 322])
        )

    def _on_splitter2_moved(self, pos, index):
        self._panel_fraction_timer2.start(500)

    def _on_splitter1_moved(self, pos, index):
        self._panel_fraction_timer1.start(500)

    def _flush_splitter2_fraction(self):
        from app.ui.panel_fractions import fraction_for_size
        from app.utils.screen_utils import get_active_screen
        sizes = self.splitter2.sizes()
        if len(sizes) < 2:
            return
        screen = get_active_screen(self)
        screen_width = screen.availableGeometry().width() if screen else 0
        fractions = dict(self.config.get("ui", "panel_fractions") or {})
        fractions["library"] = fraction_for_size(sizes[0], screen_width)
        fractions["transcript"] = fraction_for_size(sizes[1], screen_width)
        self.config.set("ui", "panel_fractions", fractions)

    def _flush_splitter1_fraction(self):
        from app.ui.panel_fractions import fraction_for_size
        from app.utils.screen_utils import get_active_screen
        sizes = self.splitter1.sizes()
        if len(sizes) < 2:
            return
        screen = get_active_screen(self)
        screen_width = screen.availableGeometry().width() if screen else 0
        fractions = dict(self.config.get("ui", "panel_fractions") or {})
        fractions["inspector"] = fraction_for_size(sizes[1], screen_width)
        self.config.set("ui", "panel_fractions", fractions)
```

- [ ] **Step 5: Call `_restore_panel_fractions()` before `_restore_panel_collapse_state()` in `__init__`**

In `app/main_window.py`, change (as introduced by Task 5, Step 4):

```python
        self._setup_menu()
        self._setup_ui()
        self._restore_panel_collapse_state()
        self._setup_statusbar()
        self._connect_signals()
```

to:

```python
        self._setup_menu()
        self._setup_ui()
        self._restore_panel_fractions()
        self._restore_panel_collapse_state()
        self._setup_statusbar()
        self._connect_signals()
```

- [ ] **Step 6: Wire `splitterMoved` in `_connect_signals`**

In `app/main_window.py`, change the block added by Task 5, Step 5:

```python
    def _connect_signals(self):
        self.splitter2.collapse_changed.connect(self._on_transcript_collapse_changed)
        self.splitter1.collapse_changed.connect(self._on_inspector_collapse_changed)
```

to:

```python
    def _connect_signals(self):
        self.splitter2.collapse_changed.connect(self._on_transcript_collapse_changed)
        self.splitter1.collapse_changed.connect(self._on_inspector_collapse_changed)
        self.splitter2.splitterMoved.connect(self._on_splitter2_moved)
        self.splitter1.splitterMoved.connect(self._on_splitter1_moved)
```

- [ ] **Step 7: Flush pending timers in `closeEvent`**

In `app/main_window.py`, change:

```python
        if self._gain_save_timer.isActive():
            self._gain_save_timer.stop()
            self._flush_gain_to_config()
        if self._meeting_poll_timer.isActive():
```

to:

```python
        if self._gain_save_timer.isActive():
            self._gain_save_timer.stop()
            self._flush_gain_to_config()
        if self._panel_fraction_timer2.isActive():
            self._panel_fraction_timer2.stop()
            self._flush_splitter2_fraction()
        if self._panel_fraction_timer1.isActive():
            self._panel_fraction_timer1.stop()
            self._flush_splitter1_fraction()
        if self._meeting_poll_timer.isActive():
```

- [ ] **Step 8: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_collapsible_panel_resize_persistence.py -v`
Expected: PASS (7 tests)

- [ ] **Step 9: Run the full suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (aside from the two pre-existing `tests/test_single_instance.py` failures if TalkTrack itself is running).

- [ ] **Step 10: Manual verification (real app, per the spec's Manual Verification section)**

Launch via `start.bat`, confirm `TalkTrack UI ready` in `~/.talktrack/talktrack.log`, then:
1. Click the Transcript column's collapse arrow — it disappears, Library takes the freed width. Click again — it returns. Same for Inspector.
2. Collapse both — only the Library list remains. Reopen either.
3. Collapse Inspector, quit, relaunch — it opens already collapsed.
4. With Inspector collapsed, double-click a recording — Inspector opens for this view. Quit and relaunch without touching the arrow again — it opens collapsed again.
5. Drag the Transcript/Inspector boundary to a new width, quit, relaunch on the same monitor — same width.
6. Collapse the Speakers section header inside Inspector (not the inner speaker-rows toggle), quit, relaunch — it opens collapsed; Notes and Summary are unaffected.

- [ ] **Step 11: Commit**

```bash
git add app/main_window.py tests/test_collapsible_panel_resize_persistence.py
git commit -m "feat(ui): persist and restore panel widths as a fraction of screen width"
```
