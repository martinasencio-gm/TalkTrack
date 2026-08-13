# Calendar Rename, LLM Transcript Export & Calendar Remap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suggest renaming a recording to its tagged calendar event's subject; export a
human/LLM-readable Markdown transcript (with calendar/notes/summary/action-item context) to a
separate, user-configurable folder, kept current on every save; and let the user remap an
already-tagged recording to a different calendar event.

**Architecture:** A new pure module (`app/utils/transcript_export.py`, no Qt dependency) builds
the Markdown+YAML-frontmatter document from plain dicts. A new `MainWindow._export_transcript`
reads everything for a given session fresh from disk (transcript.json, speaker_names.json,
calendar_event.json, notes.txt, summary.md, action_items.json) and calls into that module — this
avoids any risk of exporting stale in-memory state for a session that's no longer the one
displayed in the UI (the existing "previous session's notes saved on switch" flow needs exactly
this). Calendar remap reuses the existing `CalendarLookupWorker` / `CalendarSuggestionBanner` /
`_on_calendar_tag_requested` machinery from the prior calendar-tagging feature almost unchanged.

**Tech Stack:** PyQt6, existing `app/utils/atomic_io.py` durable-write helpers, `unittest`.

## Global Constraints

- Every durable write of user data (`settings.json`, the new `.md` export, anything under a
  recording directory) goes through `app/utils/atomic_io.py` (`atomic_write_text` /
  `atomic_write_json`) — never a bare `open(..., "w")`. (CLAUDE.md, "Coding Conventions".)
- Non-UI logic is TDD'd (failing test → confirm fail → implement → confirm pass). PyQt/UI code is
  smoke-tested only via a `python -c` snippet — no Qt widget test classes beyond pure-helper
  tests. (`.claude/rules/ways-of-working.md`, "Testing".)
- Run the suite with the **venv** Python in this environment
  (`.venv/Scripts/python.exe -m unittest <module> -v`, with `QT_QPA_PLATFORM=offscreen` set for
  anything importing PyQt6) — the global interpreter in this environment has neither the
  project's dependencies nor `pytest` installed. Prefer `python -m unittest tests.<module> -v`
  for a single test file's dotted module path; `python -m pytest tests/ -v` remains the
  documented full-suite command for whichever environment actually has pytest.
- `_export_transcript` must never read or write a session other than the one explicitly passed
  in — it defaults to `self._current_session` but accepts an explicit session dict for the one
  call site where the current session has already moved on
  (`_on_recording_selected`'s "save the outgoing session's notes" step).
- Session-scoped background writes never touch the UI (no widget calls) — follow the existing
  `_write_transcript_for_session` pattern (`app/main_window.py:1021`) of reading everything the
  write needs from disk rather than from `self.transcript_viewer` / `self.notes_panel` internals.
- Every export failure (bad path, disk full, permission denied, malformed data) is caught and
  logged; it must never raise into a caller or block the primary save it's attached to.
- Commit per task, conventional prefixes (`feat:`, `fix:`, `ui:`, `config:`, `docs:`), never
  `--amend`, never `Co-Authored-By:`, straight to `master`.

---

### Task 1: `transcripts.directory` config default + directory creation

**Files:**
- Modify: `app/utils/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG["transcripts"]["directory"]` (str, default
  `str(Path(__file__).parent.parent.parent / "transcripts")`, mirroring
  `DEFAULT_CONFIG["output"]["directory"]`'s existing pattern one line above it).
- Produces: `Config.load()` creates `self._data["transcripts"]["directory"]` on disk the same
  way it already does for `output.directory`, falling back to the default on `OSError`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`, in a new `TestTranscriptsDefaults` class (mirroring the existing
`TestCalendarDefaults` class already in that file):

```python
class TestTranscriptsDefaults(ConfigTestCase):
    def test_transcripts_directory_has_a_default(self):
        cfg = Config()
        self.assertTrue(cfg.get("transcripts", "directory"))

    def test_transcripts_directory_round_trips(self):
        cfg = Config()
        new_dir = str(Path(self._tmp.name) / "my_transcripts")
        cfg.set("transcripts", "directory", new_dir)
        cfg2 = Config()
        self.assertEqual(cfg2.get("transcripts", "directory"), new_dir)

    def test_transcripts_directory_created_on_load(self):
        target = Path(self._tmp.name) / "fresh_transcripts_dir"
        self.assertFalse(target.exists())
        self._write_settings({"transcripts": {"directory": str(target)}})
        Config()
        self.assertTrue(target.exists())

    def test_invalid_transcripts_dir_falls_back_to_default(self):
        self._write_settings({"transcripts": {"directory": "Z:\\no\\such\\drive\\path"}})
        try:
            cfg = Config()
        except OSError:
            self.fail("Config() raised OSError for an invalid transcripts directory")
        self.assertEqual(cfg.get("transcripts", "directory"),
                         DEFAULT_CONFIG["transcripts"]["directory"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_config -v`
Expected: the four new tests FAIL with `KeyError: 'transcripts'` (section doesn't exist yet).

- [ ] **Step 3: Add the config default**

In `app/utils/config.py`, add a new top-level section to `DEFAULT_CONFIG` right after
`"output"` (after line 26, before `"transcription"`):

```python
    "transcripts": {
        "directory": str(Path(__file__).parent.parent.parent / "transcripts"),
    },
```

- [ ] **Step 4: Create the directory on load, with fallback**

In `Config.load()`, right after the existing `output.directory` creation block (currently lines
90-94), add the same pattern for the new section:

```python
        try:
            os.makedirs(self._data["output"]["directory"], exist_ok=True)
        except OSError:
            self._data["output"]["directory"] = DEFAULT_CONFIG["output"]["directory"]
            os.makedirs(self._data["output"]["directory"], exist_ok=True)
        try:
            os.makedirs(self._data["transcripts"]["directory"], exist_ok=True)
        except OSError:
            self._data["transcripts"]["directory"] = DEFAULT_CONFIG["transcripts"]["directory"]
            os.makedirs(self._data["transcripts"]["directory"], exist_ok=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_config -v`
Expected: all tests in the file PASS (existing tests plus the four new ones).

- [ ] **Step 6: Commit**

```bash
git add app/utils/config.py tests/test_config.py
git commit -m "config: add transcripts.directory setting with default + auto-creation"
```

---

### Task 2: `app/utils/transcript_export.py` — pure Markdown export builder

**Files:**
- Create: `app/utils/transcript_export.py`
- Test: `tests/test_transcript_export.py`

**Interfaces:**
- Consumes: nothing from other new tasks — pure functions over plain dicts/strings.
- Produces (consumed by Task 5):
  - `sanitize_filename_component(text: str) -> str`
  - `export_path_for(title: str, timestamp_iso: str, transcripts_dir) -> pathlib.Path`
  - `build_export_markdown(metadata, transcript_data, speaker_names, calendar_event, notes, summary_markdown, action_items) -> str`
  - `export_transcript(metadata, transcript_data, speaker_names, calendar_event, notes, summary_markdown, action_items, transcripts_dir) -> None`

Where:
- `metadata`: dict with at least `directory` (str) and `started_at` (ISO string), matching
  `metadata.json`'s existing schema (`app/recording/recorder.py` / `import_session.py`).
- `transcript_data`: dict matching `transcript.json`'s schema — `{"segments": [...], "language": str, "duration": float}`,
  each segment dict having `start` (float seconds), `end` (float), `text` (str), `speaker` (str,
  may be empty) — the exact shape `TranscriptResult.to_dict()` produces
  (`app/transcription/transcriber.py:68-79`).
- `speaker_names`: `{speaker_id: name}` dict, matching `speaker_names.json`.
- `calendar_event`: dict matching `calendar_event.json`'s schema (`subject`, `organizer`,
  `attendees: list[str]`, `start`/`end` ISO strings) or `None`.
- `notes`: plain string (verbatim `notes.txt` contents) or empty string.
- `summary_markdown`: plain string (verbatim `summary.md` contents) or `None`.
- `action_items`: list of dicts (matching `action_items.json`'s schema — each item has at least
  `task`; `assignee` and `due` are optional keys) or `None`/empty list.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcript_export.py`:

```python
"""Tests for the pure LLM-transcript-export builder."""
import unittest
from pathlib import Path

from app.utils.transcript_export import (
    sanitize_filename_component,
    export_path_for,
    build_export_markdown,
)


class TestSanitizeFilenameComponent(unittest.TestCase):
    def test_strips_invalid_windows_characters(self):
        self.assertEqual(
            sanitize_filename_component('Q3: Roadmap/Sync? <final>'),
            "Q3_Roadmap_Sync_final",
        )

    def test_collapses_whitespace_to_single_underscores(self):
        self.assertEqual(sanitize_filename_component("a   b\tc\nd"), "a_b_c_d")

    def test_caps_length_at_60_chars(self):
        long_title = "x" * 200
        result = sanitize_filename_component(long_title)
        self.assertEqual(len(result), 60)

    def test_empty_input_returns_untitled(self):
        self.assertEqual(sanitize_filename_component(""), "Untitled")

    def test_whitespace_only_input_returns_untitled(self):
        self.assertEqual(sanitize_filename_component("   "), "Untitled")


class TestExportPathFor(unittest.TestCase):
    def test_builds_timestamped_sanitized_path(self):
        path = export_path_for("Q3 Roadmap Sync", "2026-08-13T14:00:00", Path("C:/transcripts"))
        self.assertEqual(path, Path("C:/transcripts/Q3_Roadmap_Sync_20260813_1400.md"))

    def test_missing_timestamp_still_produces_a_path(self):
        path = export_path_for("Focus Block", "", Path("C:/transcripts"))
        self.assertEqual(path, Path("C:/transcripts/Focus_Block_00000000_0000.md"))


class TestBuildExportMarkdown(unittest.TestCase):
    def _metadata(self):
        return {
            "directory": "C:/recordings/rec_20260813_140000",
            "started_at": "2026-08-13T14:00:00",
            "duration": 1834,
        }

    def _transcript(self):
        return {
            "segments": [
                {"start": 3.0, "end": 8.0, "text": "Let's get started.", "speaker": "SPEAKER_00"},
                {"start": 12.0, "end": 15.0, "text": "Sounds good.", "speaker": "SPEAKER_01"},
            ],
            "language": "en",
            "duration": 1834,
        }

    def test_frontmatter_includes_core_fields(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertIn('title: "rec_20260813_140000"', md)
        self.assertIn("recording_date: \"2026-08-13T14:00:00\"", md)
        self.assertIn("duration_seconds: 1834", md)
        self.assertIn('source_directory: "rec_20260813_140000"', md)

    def test_calendar_block_omitted_when_no_event(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("calendar:", md)

    def test_calendar_block_present_and_used_as_title_when_tagged(self):
        event = {
            "subject": "Q3 Roadmap Sync",
            "organizer": "jane.doe@example.com",
            "attendees": ["jane.doe@example.com", "john.smith@example.com"],
        }
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, event, "", None, None
        )
        self.assertIn('title: "Q3 Roadmap Sync"', md)
        self.assertIn("calendar:", md)
        self.assertIn('subject: "Q3 Roadmap Sync"', md)
        self.assertIn('organizer: "jane.doe@example.com"', md)
        self.assertIn("- \"jane.doe@example.com\"", md)
        self.assertIn("- \"john.smith@example.com\"", md)

    def test_speakers_block_omitted_when_no_names(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("speakers:", md)

    def test_speakers_block_present_when_names_given(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {"SPEAKER_00": "Jane Doe"}, None, "", None, None
        )
        self.assertIn("speakers:", md)
        self.assertIn('SPEAKER_00: "Jane Doe"', md)

    def test_summary_section_omitted_when_none(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("# Summary", md)

    def test_summary_section_present_when_given(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", "The team discussed Q3.", None
        )
        self.assertIn("# Summary", md)
        self.assertIn("The team discussed Q3.", md)

    def test_action_items_section_omitted_when_empty(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, []
        )
        self.assertNotIn("# Action Items", md)

    def test_action_items_rendered_as_checklist(self):
        items = [
            {"assignee": "Jane", "task": "Send the deck", "due": "2026-08-20"},
            {"task": "Follow up with legal"},
        ]
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, items
        )
        self.assertIn("# Action Items", md)
        self.assertIn("- [ ] Jane: Send the deck (due 2026-08-20)", md)
        self.assertIn("- [ ] Follow up with legal", md)

    def test_notes_section_omitted_when_blank(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "", None, None
        )
        self.assertNotIn("# Notes", md)

    def test_notes_section_present_when_given(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {}, None, "Follow up on budget.", None, None
        )
        self.assertIn("# Notes", md)
        self.assertIn("Follow up on budget.", md)

    def test_transcript_section_uses_speaker_names_and_timestamps(self):
        md = build_export_markdown(
            self._metadata(), self._transcript(), {"SPEAKER_00": "Jane Doe"}, None, "", None, None
        )
        self.assertIn("# Transcript", md)
        self.assertIn("**[00:00:03] Jane Doe:** Let's get started.", md)
        self.assertIn("**[00:00:12] SPEAKER_01:** Sounds good.", md)

    def test_empty_segments_still_produces_transcript_header(self):
        empty_transcript = {"segments": [], "language": "", "duration": 0}
        md = build_export_markdown(
            self._metadata(), empty_transcript, {}, None, "", None, None
        )
        self.assertIn("# Transcript", md)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m unittest tests.test_transcript_export -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.utils.transcript_export'`.

- [ ] **Step 3: Implement `app/utils/transcript_export.py`**

```python
"""Pure builder for the human/LLM-readable Markdown transcript export.

No Qt dependency — every input is a plain dict/string/list so this module
stays unit-testable without a QApplication. app/main_window.py is
responsible for reading the source JSON/text files from a recording's
directory and calling export_transcript() with the results.
"""
import os
from pathlib import Path

from app.utils.atomic_io import atomic_write_text

_MAX_TITLE_LEN = 60
_INVALID_CHARS = '\\/:*?"<>|'


def _format_time(seconds):
    """HH:MM:SS from a float seconds offset. Local copy — transcriber.py
    imports PyQt6, and this module must stay Qt-free."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def sanitize_filename_component(text):
    """Strip characters invalid in Windows filenames, collapse whitespace
    to single underscores, cap length. Empty/whitespace-only input becomes
    'Untitled' rather than an empty filename component."""
    text = text.strip()
    if not text:
        return "Untitled"
    cleaned = "".join(" " if c in _INVALID_CHARS else c for c in text)
    collapsed = "_".join(cleaned.split())
    return collapsed[:_MAX_TITLE_LEN] if collapsed else "Untitled"


def export_path_for(title, timestamp_iso, transcripts_dir):
    """<transcripts_dir>/<sanitized-title>_<YYYYMMDD>_<HHMM>.md

    Timestamp comes from the recording's started_at, not wall-clock export
    time, so re-exporting the same recording overwrites the same file
    instead of accumulating duplicates. A missing/unparseable timestamp
    falls back to an all-zero stamp rather than raising.
    """
    stamp = "00000000_0000"
    if timestamp_iso:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_iso)
            stamp = dt.strftime("%Y%m%d_%H%M")
        except ValueError:
            pass
    filename = f"{sanitize_filename_component(title)}_{stamp}.md"
    return Path(transcripts_dir) / filename


def _yaml_str(value):
    """Quote a string for a YAML scalar. Values here are display text, not
    attacker-controlled YAML syntax, so simple double-quoting (escaping only
    embedded double quotes/backslashes) is sufficient."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_export_markdown(metadata, transcript_data, speaker_names,
                           calendar_event, notes, summary_markdown, action_items):
    """Render the full Markdown+YAML-frontmatter export document."""
    directory_name = Path(metadata.get("directory", "")).name
    title = (calendar_event or {}).get("subject") or metadata.get("name") or directory_name

    lines = ["---"]
    lines.append(f"title: {_yaml_str(title)}")
    started_at = metadata.get("started_at", "")
    if started_at:
        lines.append(f"recording_date: {_yaml_str(started_at)}")
    duration = metadata.get("duration") or transcript_data.get("duration") or 0
    lines.append(f"duration_seconds: {int(duration)}")
    lines.append(f"source_directory: {_yaml_str(directory_name)}")

    if calendar_event:
        lines.append("calendar:")
        lines.append(f"  subject: {_yaml_str(calendar_event.get('subject', ''))}")
        organizer = calendar_event.get("organizer", "")
        if organizer:
            lines.append(f"  organizer: {_yaml_str(organizer)}")
        attendees = calendar_event.get("attendees", [])
        if attendees:
            lines.append("  attendees:")
            for name in attendees:
                lines.append(f"    - {_yaml_str(name)}")

    if speaker_names:
        lines.append("speakers:")
        for speaker_id, name in speaker_names.items():
            lines.append(f"  {speaker_id}: {_yaml_str(name)}")

    lines.append("---")
    lines.append("")

    if summary_markdown:
        lines.append("# Summary")
        lines.append("")
        lines.append(summary_markdown.strip())
        lines.append("")

    if action_items:
        lines.append("# Action Items")
        lines.append("")
        for item in action_items:
            task = item.get("task", "").strip()
            if not task:
                continue
            assignee = item.get("assignee", "").strip()
            due = item.get("due", "").strip()
            entry = f"{assignee}: {task}" if assignee else task
            if due:
                entry += f" (due {due})"
            lines.append(f"- [ ] {entry}")
        lines.append("")

    if notes and notes.strip():
        lines.append("# Notes")
        lines.append("")
        lines.append(notes.strip())
        lines.append("")

    lines.append("# Transcript")
    lines.append("")
    for seg in transcript_data.get("segments", []):
        speaker_id = seg.get("speaker", "")
        display = speaker_names.get(speaker_id, speaker_id) if speaker_id else ""
        timestamp = _format_time(seg.get("start", 0))
        prefix = f"**[{timestamp}] {display}:**" if display else f"**[{timestamp}]**"
        lines.append(f"{prefix} {seg.get('text', '').strip()}")

    return "\n".join(lines) + "\n"


def export_transcript(metadata, transcript_data, speaker_names, calendar_event,
                       notes, summary_markdown, action_items, transcripts_dir):
    """Build and write the export file. Best-effort: every failure is
    swallowed after logging, never raised into the caller — this is a
    convenience copy, not the app's source of truth for the transcript."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        os.makedirs(transcripts_dir, exist_ok=True)
        directory_name = Path(metadata.get("directory", "")).name
        title = (calendar_event or {}).get("subject") or metadata.get("name") or directory_name
        path = export_path_for(title, metadata.get("started_at", ""), transcripts_dir)
        markdown = build_export_markdown(
            metadata, transcript_data, speaker_names, calendar_event,
            notes, summary_markdown, action_items,
        )
        atomic_write_text(path, markdown)
    except OSError:
        logger.exception("Failed to export transcript for %s", metadata.get("directory"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m unittest tests.test_transcript_export -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/utils/transcript_export.py tests/test_transcript_export.py
git commit -m "feat: add pure Markdown builder for LLM-ready transcript export"
```

---

### Task 3: Settings dialog — Transcript Export Folder field

**Files:**
- Modify: `app/ui/settings_dialog.py`

**Interfaces:**
- Consumes: `config.get("transcripts", "directory")` / `config.set("transcripts", "directory", ...)` (Task 1).
- Produces: `self.transcripts_dir_edit` (QLineEdit), `self.transcripts_browse_btn` (QPushButton) —
  no other task depends on these names, this is a leaf UI addition.

- [ ] **Step 1: Add the field to the Output tab**

In `app/ui/settings_dialog.py`, right after the existing `output_form.addRow("Output Directory:", dir_row)`
line (line 190) and before `self.format_combo = QComboBox()` (line 192), add a second row:

```python
        transcripts_dir_row = QHBoxLayout()
        self.transcripts_dir_edit = QLineEdit()
        transcripts_dir_row.addWidget(self.transcripts_dir_edit)
        self.transcripts_browse_btn = QPushButton("Browse...")
        self.transcripts_browse_btn.clicked.connect(self._browse_transcripts_dir)
        transcripts_dir_row.addWidget(self.transcripts_browse_btn)
        output_form.addRow("Transcript Export Folder:", transcripts_dir_row)
```

- [ ] **Step 2: Load the value**

In the settings-load method, right after the existing `self.output_dir_edit.setText(self.config.get("output", "directory"))`
line (line 424), add:

```python
        self.transcripts_dir_edit.setText(self.config.get("transcripts", "directory"))
```

- [ ] **Step 3: Save the value**

In the settings-save method, right after the existing `self.config.set("output", "directory", self.output_dir_edit.text())`
line (line 504), add:

```python
        self.config.set("transcripts", "directory", self.transcripts_dir_edit.text())
```

- [ ] **Step 4: Add the browse handler**

Right after the existing `_browse_output_dir` method (line 785-790), add:

```python
    def _browse_transcripts_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select Transcript Export Folder", self.transcripts_dir_edit.text()
        )
        if directory:
            self.transcripts_dir_edit.setText(directory)
```

- [ ] **Step 5: Smoke-test**

Run (with the venv Python, offscreen platform so it doesn't need a real display):

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.settings_dialog import SettingsDialog
from app.utils.config import Config
app = QApplication([])
cfg = Config()
dlg = SettingsDialog(cfg)
assert dlg.transcripts_dir_edit.text() == cfg.get('transcripts', 'directory')
print('OK: transcripts dir field wired')
"
```

Expected: prints `OK: transcripts dir field wired` with no traceback. (If `SettingsDialog.__init__`
needs additional arguments beyond `config`, check the class definition and adjust the snippet —
do not change production code to make the smoke test pass.)

- [ ] **Step 6: Commit**

```bash
git add app/ui/settings_dialog.py
git commit -m "ui: add transcript export folder setting to Output tab"
```

---

### Task 4: `RecordingHeader` — "Change" button for calendar remap

**Files:**
- Modify: `app/ui/recording_header.py`
- Test: `tests/test_recording_header.py`

**Interfaces:**
- Produces: `RecordingHeader.change_calendar_requested` (pyqtSignal, no args), emitted when the
  user clicks the new "Change" button. Consumed by Task 7.
- The button is shown/hidden in lockstep with the existing `calendar_label` (visible only when
  `calendar_event` is truthy).

- [ ] **Step 1: Add the signal and button**

In `app/ui/recording_header.py`, add the new signal next to the existing one (line 47):

```python
    name_changed = pyqtSignal(str)  # emitted when user renames the recording
    change_calendar_requested = pyqtSignal()  # emitted when user clicks "Change" on the calendar line
```

In `_setup_ui`, replace the calendar-label block (lines 92-97):

```python
        # Calendar event line
        self.calendar_label = QLabel("")
        self.calendar_label.setObjectName("recordingCalendarInfo")
        self.calendar_label.setStyleSheet("color: #89b4fa; font-size: 12px;")
        self.calendar_label.hide()
        layout.addWidget(self.calendar_label)
```

with:

```python
        # Calendar event line + remap button
        calendar_row = QHBoxLayout()
        self.calendar_label = QLabel("")
        self.calendar_label.setObjectName("recordingCalendarInfo")
        self.calendar_label.setStyleSheet("color: #89b4fa; font-size: 12px;")
        self.calendar_label.hide()
        calendar_row.addWidget(self.calendar_label)

        self.change_calendar_btn = QPushButton("Change")
        self.change_calendar_btn.setObjectName("changeCalendarButton")
        self.change_calendar_btn.setFixedWidth(60)
        self.change_calendar_btn.clicked.connect(self.change_calendar_requested.emit)
        self.change_calendar_btn.hide()
        calendar_row.addWidget(self.change_calendar_btn)

        calendar_row.addStretch()
        layout.addLayout(calendar_row)
```

- [ ] **Step 2: Show/hide the button with the calendar line**

In `set_recording`, replace the existing calendar-event branch (lines 131-136):

```python
        if calendar_event:
            self.calendar_label.setText(_format_calendar_line(calendar_event))
            self.calendar_label.show()
        else:
            self.calendar_label.clear()
            self.calendar_label.hide()
```

with:

```python
        if calendar_event:
            self.calendar_label.setText(_format_calendar_line(calendar_event))
            self.calendar_label.show()
            self.change_calendar_btn.show()
        else:
            self.calendar_label.clear()
            self.calendar_label.hide()
            self.change_calendar_btn.hide()
```

- [ ] **Step 3: Smoke-test**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "
from PyQt6.QtWidgets import QApplication
from app.ui.recording_header import RecordingHeader
app = QApplication([])
header = RecordingHeader()
header.set_recording({'directory': 'C:/recordings/rec1', 'started_at': '2026-08-13T14:00:00'},
                      calendar_event={'subject': 'Q3 Sync', 'attendees': ['Jane']})
assert header.change_calendar_btn.isVisible(), 'button should show when tagged'
header.set_recording({'directory': 'C:/recordings/rec2', 'started_at': '2026-08-13T14:00:00'},
                      calendar_event=None)
assert not header.change_calendar_btn.isVisible(), 'button should hide when untagged'
received = []
header.change_calendar_requested.connect(lambda: received.append(True))
header.change_calendar_btn.click()
assert received == [True]
print('OK: change_calendar_btn wired')
"
```

Expected: prints `OK: change_calendar_btn wired` with no traceback or AssertionError. Note:
`isVisible()` on a widget inside a shown top-level window reflects real visibility; since
`RecordingHeader` here is never `.show()`n as a top-level widget, prefer asserting
`not header.change_calendar_btn.isHidden()` / `.isHidden()` instead if `isVisible()` reads False
for both cases in the offscreen platform (a known Qt quirk for un-shown parents) — adjust the
snippet's assertions to whichever of `isVisible()`/`isHidden()` actually distinguishes the two
states when you run it, and confirm the fix is checking the right one before moving on.

- [ ] **Step 4: Run the existing pure-helper tests to confirm no regression**

Run: `.venv/Scripts/python.exe -m unittest tests.test_recording_header -v`
Expected: all existing tests still PASS (this task didn't touch any of the pure helper functions).

- [ ] **Step 5: Commit**

```bash
git add app/ui/recording_header.py
git commit -m "ui: add Change button to RecordingHeader for calendar remap"
```

---

### Task 5: `MainWindow._export_transcript` — LLM export wired into every save point

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `app.utils.transcript_export.export_transcript` (Task 2),
  `config.get("transcripts", "directory")` (Task 1).
- Produces: `MainWindow._export_transcript(session=None)` — reads everything for `session`
  (defaulting to `self._current_session`) fresh from disk and calls
  `transcript_export.export_transcript`. No other task calls this yet; Task 6 and Task 7 don't
  need it directly (rename/remap only need `_maybe_suggest_rename`, but Task 7 does call this
  after a successful remap tag — see that task).

**Global constraint reminder:** this must read from disk, not from `self.transcript_viewer` /
`self.notes_panel` state, because the one caller in `_on_recording_selected` runs after
`self._current_session` has already moved to the *new* session while the notes save it follows
is for the *old* one.

- [ ] **Step 1: Add the import**

At the top of `app/main_window.py`, add alongside the other `app.utils`/`app.transcription` imports (near line 19-25):

```python
from app.utils import transcript_export
```

- [ ] **Step 2: Implement `_export_transcript`**

Add this method right after `_write_transcript_for_session` (after line 1041, before
`_load_calendar_event`):

```python
    def _export_transcript(self, session=None):
        """Best-effort LLM-readable Markdown export for a session, reading
        everything fresh from disk. Deliberately does not touch
        self.transcript_viewer / self.notes_panel — the caller in
        _on_recording_selected runs this for a session that is no longer
        the one those widgets currently display."""
        session = session if session is not None else self._current_session
        if not session or not session.get("directory"):
            return
        directory = Path(session["directory"])

        transcript_path = directory / "transcript.json"
        if not transcript_path.exists():
            return  # nothing transcribed yet — nothing useful to export
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        speaker_names = {}
        names_path = directory / "speaker_names.json"
        if names_path.exists():
            try:
                with open(names_path, "r", encoding="utf-8") as f:
                    speaker_names = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        calendar_event, _ = self._load_calendar_event(session)

        notes = ""
        notes_path = directory / "notes.txt"
        if notes_path.exists():
            try:
                notes = notes_path.read_text(encoding="utf-8")
            except OSError:
                pass

        summary_markdown = None
        summary_path = directory / "summary.md"
        if summary_path.exists():
            try:
                summary_markdown = summary_path.read_text(encoding="utf-8")
            except OSError:
                pass

        action_items = None
        actions_path = directory / "action_items.json"
        if actions_path.exists():
            try:
                with open(actions_path, "r", encoding="utf-8") as f:
                    action_items = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        transcript_export.export_transcript(
            session, transcript_data, speaker_names, calendar_event,
            notes, summary_markdown, action_items,
            self.config.get("transcripts", "directory"),
        )
```

- [ ] **Step 3: Wire into `_save_transcript`**

In `_save_transcript` (lines 1274-1292), add the export call after the existing writes succeed —
right before `self.recordings_list.refresh()`:

```python
        self.recordings_list.refresh()
```

becomes:

```python
        self._export_transcript()
        self.recordings_list.refresh()
```

This single call site covers three of the spec's triggers: initial transcription completion
(`_save_transcript` is called at line 1112), per-segment edit/undo/redo (via the
`transcript_viewer.transcript_changed` signal connected at line 351), and speaker rename (via
`_save_speaker_names` calling `_save_transcript` at line 1305).

- [ ] **Step 4: Wire into notes save for a freshly finished recording**

In `_on_recording_finished` (around line 823), the notes save is already for
`self._current_session` (set at line 803) — add the export call right after:

```python
        self.notes_panel.set_session_dir(session["directory"], keep_editor_text=True)
        self.notes_panel.save_notes()
```

becomes:

```python
        self.notes_panel.set_session_dir(session["directory"], keep_editor_text=True)
        self.notes_panel.save_notes()
        self._export_transcript()
```

- [ ] **Step 5: Wire into notes save for the outgoing session on recording switch**

In `_on_recording_selected`, capture the outgoing session before it's overwritten. Change:

```python
    def _on_recording_selected(self, metadata):
        """Load a past recording for viewing/transcription."""
        # Clear any stale calendar-suggestion banner from the previously
        # displayed recording — see _on_recording_finished for why.
        self.calendar_banner.hide_and_clear()
        self._calendar_banner_session = None

        self._current_session = metadata
```

to:

```python
    def _on_recording_selected(self, metadata):
        """Load a past recording for viewing/transcription."""
        # Clear any stale calendar-suggestion banner from the previously
        # displayed recording — see _on_recording_finished for why.
        self.calendar_banner.hide_and_clear()
        self._calendar_banner_session = None

        previous_session = self._current_session
        self._current_session = metadata
```

Then, at the existing notes-save-before-switch step (line 1219, "Persist any edits to the
previously loaded recording's notes"):

```python
        # Persist any edits to the previously loaded recording's notes
        # before the editor is repointed, then load this recording's notes.
        self.notes_panel.save_notes()
        self.notes_panel.set_session_dir(metadata["directory"])
```

becomes:

```python
        # Persist any edits to the previously loaded recording's notes
        # before the editor is repointed, then load this recording's notes.
        self.notes_panel.save_notes()
        self._export_transcript(previous_session)
        self.notes_panel.set_session_dir(metadata["directory"])
```

`previous_session` is `None` on the very first call (no recording loaded yet) — `_export_transcript`
already early-returns on a falsy session, so this is safe without an extra guard here.

- [ ] **Step 6: Wire into summary and action-item saves**

In `_on_summary_ready` (lines 1712-1719):

```python
    def _on_summary_ready(self, summary):
        self.summary_panel.set_summary(summary)
        if self._current_session:
            path = Path(self._current_session["directory"]) / "summary.md"
            try:
                atomic_write_text(path, summary)
            except OSError:
                self.status_label.setText("Failed to save summary.")
```

becomes:

```python
    def _on_summary_ready(self, summary):
        self.summary_panel.set_summary(summary)
        if self._current_session:
            path = Path(self._current_session["directory"]) / "summary.md"
            try:
                atomic_write_text(path, summary)
                self._export_transcript()
            except OSError:
                self.status_label.setText("Failed to save summary.")
```

In `_on_action_items_changed` (lines 1725-1731):

```python
    def _on_action_items_changed(self, items):
        if self._current_session:
            path = Path(self._current_session["directory"]) / "action_items.json"
            try:
                atomic_write_json(path, items, indent=2)
            except OSError:
                self.status_label.setText("Failed to save action items.")
```

becomes:

```python
    def _on_action_items_changed(self, items):
        if self._current_session:
            path = Path(self._current_session["directory"]) / "action_items.json"
            try:
                atomic_write_json(path, items, indent=2)
                self._export_transcript()
            except OSError:
                self.status_label.setText("Failed to save action items.")
```

- [ ] **Step 7: Smoke-test end to end**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "
import json, tempfile
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
from app.utils import config as config_module

app = QApplication([])
tmp = tempfile.TemporaryDirectory()
tmp_path = Path(tmp.name)
with patch.object(config_module, 'CONFIG_DIR', tmp_path), \
     patch.object(config_module, 'CONFIG_FILE', tmp_path / 'settings.json'):
    from app.main_window import MainWindow
    win = MainWindow()
    rec_dir = tmp_path / 'rec1'
    rec_dir.mkdir()
    session = {'directory': str(rec_dir), 'started_at': '2026-08-13T14:00:00'}
    (rec_dir / 'transcript.json').write_text(json.dumps({
        'segments': [{'start': 0, 'end': 1, 'text': 'hi', 'speaker': 'SPEAKER_00'}],
        'language': 'en', 'duration': 1,
    }))
    win._current_session = session
    win._export_transcript()
    exported = list(Path(win.config.get('transcripts', 'directory')).glob('*.md'))
    assert len(exported) == 1, exported
    print('OK: export produced', exported[0].name)
"
```

Expected: prints `OK: export produced rec1_20260813_1400.md` (or similar) with no traceback.
`MainWindow()`'s constructor touches real audio/device APIs — if it throws in this sandboxed
environment for reasons unrelated to this change (e.g. no audio hardware), that's a pre-existing
environment limitation, not a regression from this task; note it in the task report rather than
trying to work around it in production code.

- [ ] **Step 8: Commit**

```bash
git add app/main_window.py
git commit -m "feat: export LLM-ready transcript markdown on every transcript/notes/summary save"
```

---

### Task 6: Rename suggestion after calendar tagging

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `RecordingHeader.name_changed` handler `_on_recording_renamed` (existing, unchanged).
- Produces: `MainWindow._maybe_suggest_rename(session, event)`, called from
  `_on_calendar_tag_requested` (this task) and reused by Task 7's remap flow.

- [ ] **Step 1: Implement `_maybe_suggest_rename`**

Add this method right after `_on_calendar_tag_requested` (after line 1612, before
`_on_calendar_dismissed`):

```python
    def _maybe_suggest_rename(self, session, event):
        """Offer to rename the recording to the calendar event's subject.
        Never overwrites a name the user already set — a recording counts
        as "already custom-named" the moment metadata["name"] is truthy,
        whether that happened via manual rename or an earlier accepted
        suggestion."""
        if session is None or session.get("name"):
            return
        subject = event.get("subject", "").strip()
        if not subject:
            return
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "Rename Recording?",
            "Rename this recording to match the calendar event?",
            text=subject,
        )
        if ok and new_name.strip():
            self._on_recording_renamed(new_name.strip())
```

- [ ] **Step 2: Call it from `_on_calendar_tag_requested`**

In `_on_calendar_tag_requested` (lines 1590-1612), add the call at the end:

```python
        self._calendar_attendees = event_to_save.get("attendees", [])
        self.transcript_viewer.set_calendar_attendees(self._calendar_attendees)
```

becomes:

```python
        self._calendar_attendees = event_to_save.get("attendees", [])
        self.transcript_viewer.set_calendar_attendees(self._calendar_attendees)
        self._maybe_suggest_rename(self._current_session, event_to_save)
```

- [ ] **Step 3: Smoke-test the skip condition**

The dialog itself can't be driven headlessly in a one-liner (it's modal), but the skip condition
is verifiable directly:

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "
from unittest.mock import patch, MagicMock
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import app.main_window as mw

win = MagicMock(spec=mw.MainWindow)
win._on_recording_renamed = MagicMock()

# already-named session: dialog must never even be constructed
with patch.object(mw, 'QInputDialog') as mock_dialog:
    mw.MainWindow._maybe_suggest_rename(win, {'name': 'Existing Name'}, {'subject': 'Q3 Sync'})
    mock_dialog.getText.assert_not_called()

# blank subject: dialog must never even be constructed
with patch.object(mw, 'QInputDialog') as mock_dialog:
    mw.MainWindow._maybe_suggest_rename(win, {}, {'subject': ''})
    mock_dialog.getText.assert_not_called()

print('OK: rename-suggestion skip conditions hold')
"
```

Expected: prints `OK: rename-suggestion skip conditions hold`. Note: `QInputDialog` is imported
locally inside `_maybe_suggest_rename` (`from PyQt6.QtWidgets import QInputDialog`), so
`patch.object(mw, 'QInputDialog')` won't intercept it as written — if the smoke test's patch
doesn't take effect, move the `QInputDialog` import to `app/main_window.py`'s top-level import
block instead (alongside the other `PyQt6.QtWidgets` imports at line 12-15) so it's patchable at
module scope, and drop the local import from `_maybe_suggest_rename`. Prefer the top-level import
regardless — it matches how every other Qt widget class is already imported in this file.

- [ ] **Step 4: Commit**

```bash
git add app/main_window.py
git commit -m "feat: suggest renaming a recording to its tagged calendar event's subject"
```

---

### Task 7: Calendar remap — "Change" button wired end to end

**Files:**
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `RecordingHeader.change_calendar_requested` (Task 4),
  `CalendarLookupWorker` / `CalendarSuggestionBanner` (existing, unchanged),
  `_maybe_suggest_rename` (Task 6), `_export_transcript` (Task 5).

- [ ] **Step 1: Extract the shared worker-dispatch helper**

In `_maybe_lookup_calendar` (lines 1538-1566), replace the inline worker creation at the end:

```python
        worker = CalendarLookupWorker(started_dt, stopped_dt)
        worker.session = session
        worker.finished.connect(self._on_calendar_lookup_finished)
        self._calendar_lookup_workers.append(worker)
        worker.start()
```

with:

```python
        self._dispatch_calendar_lookup(session, started_dt, stopped_dt)

    def _dispatch_calendar_lookup(self, session, started_dt, stopped_dt):
        worker = CalendarLookupWorker(started_dt, stopped_dt)
        worker.session = session
        worker.finished.connect(self._on_calendar_lookup_finished)
        self._calendar_lookup_workers.append(worker)
        worker.start()
```

- [ ] **Step 2: Show a status message when a manual lookup finds nothing**

In `_on_calendar_lookup_finished` (lines 1568-1588), the existing early return on empty results:

```python
        if not events or session is None:
            return
```

Split the two conditions so an empty-but-valid-session result can report status instead of
silently doing nothing — needed for the manual "Change" trigger in Step 4 below (the automatic
trigger already only fires when nothing is tagged yet, so seeing "no matches" there was never
useful; for a manual click it is):

```python
        if session is None:
            return
        if not events:
            if self._is_current_session(session):
                self.status_label.setText("No other matching calendar events found.")
            return
```

- [ ] **Step 3: Add `_on_change_calendar_requested`**

Add this method right after `_on_calendar_dismissed` (after the method added in Task 6's rename
wiring, or after the existing `_on_calendar_dismissed` if Task 6 hasn't touched this area):

```python
    def _on_change_calendar_requested(self):
        session = self._current_session
        if session is None:
            return
        if not self.config.get("calendar", "enabled"):
            return
        started, stopped = session.get("started_at"), session.get("stopped_at")
        if not started or not stopped:
            return
        try:
            started_dt = datetime.fromisoformat(started)
            stopped_dt = datetime.fromisoformat(stopped)
        except ValueError:
            return
        self.status_label.setText("Looking up calendar events...")
        self._calendar_banner_session = session
        self._dispatch_calendar_lookup(session, started_dt, stopped_dt)
```

`datetime` is already imported at module scope (`from datetime import datetime`, line 6) — no
new import needed here, unlike `_maybe_lookup_calendar` which does its own local import.

Deliberately skips the two guards that only make sense for the automatic post-recording
suggestion: `session.get("calendar_prompt_dismissed")` and "already tagged" (the whole point of
this path is running when a tag already exists). Setting `self._calendar_banner_session = session`
before dispatch (rather than only in `_on_calendar_lookup_finished`) matches the existing pattern
and ensures `_on_calendar_tag_requested`'s defense-in-depth check
(`self._calendar_banner_session is not None and not self._is_current_session(...)`) has a
correct value to compare against even if the user switches recordings while the lookup is
in flight.

- [ ] **Step 4: Connect the signal**

Find where `self.recording_header` is constructed and its existing `name_changed` connection
(`self.recording_header.name_changed.connect(self._on_recording_renamed)` — search for this line
in `app/main_window.py`'s `_setup_ui`/`__init__` region). Add immediately after it:

```python
        self.recording_header.change_calendar_requested.connect(self._on_change_calendar_requested)
```

- [ ] **Step 5: Export after a remap tag**

`_on_calendar_tag_requested` already calls `_maybe_suggest_rename` (Task 6) — add the export call
right after it, so a remap's new calendar context is reflected immediately rather than waiting
for the next transcript/notes edit:

```python
        self._calendar_attendees = event_to_save.get("attendees", [])
        self.transcript_viewer.set_calendar_attendees(self._calendar_attendees)
        self._maybe_suggest_rename(self._current_session, event_to_save)
```

becomes:

```python
        self._calendar_attendees = event_to_save.get("attendees", [])
        self.transcript_viewer.set_calendar_attendees(self._calendar_attendees)
        self._maybe_suggest_rename(self._current_session, event_to_save)
        self._export_transcript()
```

- [ ] **Step 6: Run the full test suite**

Run: `.venv/Scripts/python.exe -m unittest discover tests -v 2>&1 | tail -60`

Expected: no new failures relative to the state before this plan started (compare against a
baseline run before Task 1 if any pre-existing failures are unrelated to this work — this
project's test suite has zero QApplication-instantiating tests, so this run should be fast and
should not require a display).

- [ ] **Step 7: Commit**

```bash
git add app/main_window.py
git commit -m "feat: let the user remap a recording to a different calendar event"
```

---

## Manual Verification (not automatable in this environment)

This environment has no Windows desktop GUI interaction tool, so the following need a human with
a running TalkTrack instance and (for the calendar parts) a real Outlook desktop client:

1. Tag a recording to a calendar event, confirm the rename dialog appears pre-filled with the
   subject, edit it, confirm the recording's name updates in the header and `metadata.json`.
2. Tag a second recording, accept the rename suggestion as-is (no edit), confirm same result.
3. Manually rename a recording first, then tag it to a calendar event — confirm no rename dialog
   appears (custom name preserved).
4. After transcribing a tagged recording, confirm a `.md` file appears in the configured
   transcripts folder with frontmatter, summary/action items (if AI is configured), notes, and
   the full transcript. Edit a segment, confirm the export file updates in place (same filename).
5. Change the transcript folder in Settings > Output, confirm new exports land in the new
   location.
6. On an already-tagged recording, click "Change" in the header, confirm the suggestion banner
   reappears with other overlapping events (requires a calendar with 2+ overlapping events to
   observe non-trivial results) or the "No other matching calendar events found." status text.
7. Confirm the attendee dropdown (fixed separately, commit `8282d20`) now opens its full option
   list on click/focus, not just inline autocomplete.
