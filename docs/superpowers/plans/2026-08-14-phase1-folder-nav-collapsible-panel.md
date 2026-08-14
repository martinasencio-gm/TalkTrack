# Phase 1: Folder Navigation & Collapsible Right Panel Implementation Plan

> Steps use checkbox (`- [ ]`) syntax for tracking. Executed inline this session.

**Goal:** Implement Stories 1.1, 1.2, 3.1 per
`docs/superpowers/specs/2026-08-14-phase1-folder-nav-collapsible-panel-design.md`.

**Spec:** `docs/superpowers/specs/2026-08-14-phase1-folder-nav-collapsible-panel-design.md`

## Global Constraints

- Commits go directly to `master`. Conventional prefixes: `ui:`, `main:`, `config:`.
- Non-UI logic is TDD. UI/PyQt code: smoke test with `python -c` plus focused
  pure-helper/widget tests (offscreen QPA), no broad Qt widget test suite.
- `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q` is the
  full suite; run with global python, never bare `uv run`.

---

### Task 1: `ui.right_panel_collapsed` config default

**Files:** Modify `app/utils/config.py`

- [ ] Add `"right_panel_collapsed": False` to `DEFAULT_CONFIG["ui"]`.
- [ ] Run full suite, confirm still green.
- [ ] Commit: `config: add right_panel_collapsed default`

### Task 2: `CollapsibleSplitter` widget

**Files:** Create `app/ui/collapsible_splitter.py`, `tests/test_collapsible_splitter.py`

- [ ] Write failing tests: toggle collapses to 0 / restores prior width, toggling
  twice returns to original sizes, `collapse_changed` emits correct bool,
  `set_collapsed(True)` on a fresh splitter collapses without a prior expand.
- [ ] Confirm failure (`ModuleNotFoundError`).
- [ ] Implement `CollapsibleSplitter` / `CollapsibleSplitterHandle` per spec.
- [ ] Run tests, confirm pass.
- [ ] Commit: `ui: collapsible splitter widget`

### Task 3: Wire collapsible splitter into MainWindow

**Files:** Modify `app/main_window.py`

- [ ] Swap `QSplitter` for `CollapsibleSplitter` at the main splitter construction.
- [ ] Connect `collapse_changed` -> persist `ui.right_panel_collapsed`.
- [ ] On startup, after splitter built, apply saved collapsed state.
- [ ] Smoke test: `python -c "from app.main_window import MainWindow"`.
- [ ] Commit: `main: persist and restore right panel collapse state`

### Task 4: File menu "Open Transcripts Folder"

**Files:** Modify `app/main_window.py`

- [ ] Add `QAction` + `_open_transcripts_folder` handler mirroring
  `_open_recordings_folder`.
- [ ] Smoke test import.
- [ ] Commit: `main: add Open Transcripts Folder to File menu`

### Task 5: Recording-list context menu folder actions

**Files:** Modify `app/ui/recordings_list.py`, test: `tests/test_recordings_list_folder_actions.py`

- [ ] Add optional `config=None` param to `RecordingsList.__init__`, store `self.config`.
- [ ] Rename single-item `"Open Folder"` -> `"Open Recordings Folder"` (unchanged
  behavior); add `"Open Transcripts Folder"` below it (guarded when
  `self.config is None`).
- [ ] Add both folder actions to multi-select menu, above delete, opening global
  folders.
- [ ] Add `_open_transcripts_folder()` method.
- [ ] Tests: `os.startfile`/`os.makedirs` patched via `unittest.mock.patch`,
  verifying the right path is opened for each of the three menu paths, and that
  construction with `config=None` doesn't crash when building the menu.
- [ ] Update `MainWindow` construction call to pass `config=self.config`.
- [ ] Run full suite + smoke test.
- [ ] Commit: `ui: recording list folder navigation actions`

### Task 6: Manual verification

- [ ] Walk the 5 manual-verification steps from the spec.
- [ ] Commit any doc touch-ups if needed (unlikely).

## Self-Review

Spec coverage: 1.1 (Task 4), 1.2 (Task 5), 3.1 (Tasks 1-3). No placeholders.
Task ordering keeps the splitter change isolated before menu changes touch the
same file (`main_window.py`), minimizing merge/diff overlap within the session.
