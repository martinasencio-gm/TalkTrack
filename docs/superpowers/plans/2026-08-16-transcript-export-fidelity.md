# Transcript Export Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the exported Markdown transcript a superset of the source `transcript.json`, and stop writing exports for transcripts that contain no segments.

**Architecture:** Three independent changes to `app/utils/transcript_export.py`, a Qt-free pure builder. Two small private helpers (`_as_number`, `_format_confidence`, `_segment_timestamp`) absorb the field-degradation rules so the rendering loop stays flat. One new public predicate (`has_exportable_content`) holds the skip policy, called from `export_transcript` — never from `build_export_markdown`, which stays a pure builder with no policy in it.

**Tech Stack:** Python 3, `unittest` (the repo's test style in this module), pytest as the runner.

**Spec:** `docs/superpowers/specs/2026-08-16-transcript-export-fidelity-design.md`
**Issue:** #66

## Global Constraints

- Every commit references `#66`. **NEVER** add `Co-Authored-By` lines. Never `git commit --amend`.
- Commit directly to `master`. No feature branches.
- Run the suite with `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`. **Never** use `uv run` — the `.venv` has no pytest and `uv run` triggers a sync that can corrupt package metadata.
- `app/utils/transcript_export.py` must stay Qt-free. Do not import PyQt6 or anything that transitively imports it (notably `app/utils/transcriber.py`).
- The range separator is an en-dash, `U+2013` (`\u2013`). `atomic_write_text` defaults to UTF-8, so this is safe. Do not substitute a hyphen.
- `export_transcript` is best-effort: it logs and swallows, never raises into the caller. Do not narrow or widen its existing `except (OSError, TypeError, AttributeError, KeyError)`.
- `export_transcript` must never delete a file.
- Baseline before starting: **626 passed, 1 skipped**.

---

### Task 1: Segment lines carry end time and confidence

**Files:**
- Modify: `app/utils/transcript_export.py` (add helpers after `_format_time` at line 22; rewrite the transcript loop at lines 133-140)
- Test: `tests/test_transcript_export.py`

**Interfaces:**
- Consumes: `_format_time(seconds)` — existing, returns `HH:MM:SS`.
- Produces: `_as_number(value) -> float | int | None`, `_format_confidence(value) -> str | None`, `_segment_timestamp(seg) -> str`. All private to this module; Tasks 2 and 3 do not use them.

- [ ] **Step 1: Update the three existing assertions that the new format breaks**

Three current assertions expect the single-timestamp form on fixtures that
carry an `end`. They are correct today and wrong after this task. Change
them first so the next step's run shows only the *new* tests failing.

In `tests/test_transcript_export.py`, `test_transcript_section_uses_speaker_names_and_timestamps` (lines 183-184):

```python
        self.assertIn("**[00:00:03–00:00:08] Jane Doe:** Let's get started.", md)
        self.assertIn("**[00:00:12–00:00:15] SPEAKER_01:** Sounds good.", md)
```

In `test_export_transcript_happy_path` (line 263):

```python
            self.assertIn("**[00:00:03–00:00:08] Alice:** Test segment one.", content)
```

- [ ] **Step 2: Write the failing tests**

Append this class to `tests/test_transcript_export.py`, immediately after
`TestBuildExportMarkdown` and before `TestExportTranscript`:

```python
class TestSegmentRendering(unittest.TestCase):
    """Segment lines must carry end time and confidence so the export stands
    on its own once the audio is deleted — while degrading cleanly for
    transcripts produced before those fields were exported."""

    def _metadata(self):
        return {
            "directory": "C:/recordings/rec_20260813_140000",
            "started_at": "2026-08-13T14:00:00",
            "duration": 1834,
        }

    def _render(self, segment):
        transcript = {"segments": [segment], "duration": 1834}
        return build_export_markdown(
            self._metadata(), transcript, {"SPEAKER_00": "Alice"},
            None, "", None, None,
        )

    def test_full_form_with_end_speaker_and_confidence(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": 0.7590766239383613,
        })
        self.assertIn("**[00:00:01–00:00:09] Alice (0.76):** Hello there.", md)

    def test_confidence_omitted_when_absent(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_no_colon_when_there_is_no_speaker(self):
        """The colon reads as '<speaker> said:' — without a name it is noise."""
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "confidence": 0.76,
        })
        self.assertIn("**[00:00:01–00:00:09] (0.76)** Hello there.", md)

    def test_timestamp_only_when_neither_speaker_nor_confidence(self):
        md = self._render({"start": 1.62, "end": 9.02, "text": "Hello there."})
        self.assertIn("**[00:00:01–00:00:09]** Hello there.", md)

    def test_missing_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 1.62, "text": "Hello there.", "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01] Alice:** Hello there.", md)

    def test_none_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 1.62, "end": None, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01] Alice:** Hello there.", md)

    def test_non_numeric_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 1.62, "end": "9.02", "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:01] Alice:** Hello there.", md)

    def test_non_advancing_end_falls_back_to_single_timestamp(self):
        """A backwards or zero-length range would be worse than no range."""
        md = self._render({
            "start": 9.02, "end": 1.62, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:09] Alice:** Hello there.", md)
        self.assertNotIn("–", md)

    def test_equal_end_falls_back_to_single_timestamp(self):
        md = self._render({
            "start": 9.02, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:09] Alice:** Hello there.", md)

    def test_none_confidence_is_omitted(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": None,
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_non_numeric_confidence_is_omitted(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": "high",
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_boolean_confidence_is_omitted(self):
        """bool is an int subclass in Python — a stray True must not render
        as '(1.00)'."""
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": True,
        })
        self.assertIn("**[00:00:01–00:00:09] Alice:** Hello there.", md)

    def test_confidence_rendered_to_two_decimals(self):
        md = self._render({
            "start": 1.62, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00", "confidence": 0.7590766239383613,
        })
        self.assertIn("(0.76)", md)
        self.assertNotIn("0.7590766", md)

    def test_missing_start_is_treated_as_zero(self):
        md = self._render({"end": 9.02, "text": "Hello there.", "speaker": "SPEAKER_00"})
        self.assertIn("**[00:00:00–00:00:09] Alice:** Hello there.", md)

    def test_none_start_does_not_raise(self):
        """A None start previously reached int(None) inside _format_time.
        The new comparison against end must not make that worse."""
        md = self._render({
            "start": None, "end": 9.02, "text": "Hello there.",
            "speaker": "SPEAKER_00",
        })
        self.assertIn("**[00:00:00–00:00:09] Alice:** Hello there.", md)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_transcript_export.py -q`

Expected: FAIL. The 15 `TestSegmentRendering` cases fail on the missing range/confidence, and the 3 assertions edited in Step 1 fail for the same reason. Every other test in the file passes.

- [ ] **Step 4: Add the helpers**

In `app/utils/transcript_export.py`, insert after `_format_time` (which ends at line 22):

```python
def _as_number(value):
    """The value if it is a real number, else None.

    bool is excluded explicitly: it is an int subclass in Python, so a stray
    True would otherwise sail through as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _format_confidence(value):
    """Two-decimal confidence string, or None when there is nothing usable
    to render. Measured confidences here cluster in 0.65-0.79, so two
    decimals is the resolution that actually distinguishes segments."""
    number = _as_number(value)
    return None if number is None else f"{number:.2f}"


def _segment_timestamp(seg):
    """HH:MM:SS, or an HH:MM:SS–HH:MM:SS range when the segment carries a
    usable end.

    A missing, non-numeric, or non-advancing end falls back to the single
    timestamp: a backwards or zero-length range in the corpus would be worse
    than no range at all. Transcripts produced before end was exported hit
    this path and render exactly as they used to.
    """
    start = _as_number(seg.get("start")) or 0
    end = _as_number(seg.get("end"))
    if end is None or end <= start:
        return _format_time(start)
    return f"{_format_time(start)}\u2013{_format_time(end)}"
```

- [ ] **Step 5: Rewrite the transcript loop**

Replace lines 135-140 of `app/utils/transcript_export.py` — the `for seg in ...` body — with:

```python
    for seg in transcript_data.get("segments", []):
        speaker_id = seg.get("speaker", "")
        display = speaker_names.get(speaker_id, speaker_id) if speaker_id else ""
        parts = [f"[{_segment_timestamp(seg)}]"]
        if display:
            parts.append(display)
        confidence = _format_confidence(seg.get("confidence"))
        if confidence:
            parts.append(f"({confidence})")
        # The colon reads as "<speaker> said:" — it only earns its place
        # when a speaker is actually named.
        suffix = ":**" if display else "**"
        lines.append(f"**{' '.join(parts)}{suffix} {seg.get('text', '').strip()}")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_transcript_export.py -q`

Expected: PASS, all tests in the file.

- [ ] **Step 7: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`

Expected: 641 passed, 1 skipped (626 + 15 new).

- [ ] **Step 8: Commit**

```bash
git add app/utils/transcript_export.py tests/test_transcript_export.py
git commit -m "feat: export segment end times and confidence in transcript markdown

The exported .md dropped per-segment end and confidence, so it was not a
superset of transcript.json. That is invisible while both files exist, but
the corpus is meant to outlive the audio — at which point those fields are
gone for good.

Each field degrades on its own: a missing, non-numeric, or non-advancing
end falls back to the single-timestamp form rather than emitting a
backwards range, and confidence is omitted unless it is a real number.

Refs #66"
```

---

### Task 2: Frontmatter carries transcription provenance

**Files:**
- Modify: `app/utils/transcript_export.py` (frontmatter block in `build_export_markdown`, between the `duration_seconds` and `source_directory` lines — 83-84 before Task 1, unchanged by it)
- Test: `tests/test_transcript_export.py`

**Interfaces:**
- Consumes: `_yaml_str(value) -> str` — existing, double-quotes and escapes a string for a YAML scalar.
- Produces: nothing new. Task 3 does not depend on this task.

- [ ] **Step 1: Write the failing tests**

Append to the `TestBuildExportMarkdown` class in `tests/test_transcript_export.py`:

```python
    def test_frontmatter_includes_transcription_provenance(self):
        """model_size is what tells a later reader how much to trust a
        transcript — it has to survive into the corpus."""
        transcript = dict(self._transcript())
        transcript["model_size"] = "small"
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertIn('language: "en"', md)
        self.assertIn('model_size: "small"', md)

    def test_provenance_lines_sit_between_duration_and_source_directory(self):
        transcript = dict(self._transcript())
        transcript["model_size"] = "small"
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertLess(md.index("duration_seconds:"), md.index("language:"))
        self.assertLess(md.index("language:"), md.index("model_size:"))
        self.assertLess(md.index("model_size:"), md.index("source_directory:"))

    def test_provenance_lines_omitted_when_absent(self):
        transcript = {"segments": [], "duration": 1834}
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertNotIn("language:", md)
        self.assertNotIn("model_size:", md)

    def test_empty_provenance_values_are_omitted(self):
        transcript = {"segments": [], "duration": 1834, "language": "", "model_size": None}
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertNotIn("language:", md)
        self.assertNotIn("model_size:", md)

    def test_transcribe_seconds_is_not_exported(self):
        """Perf telemetry about the machine that ran the transcription. It
        says nothing about the content, so it stays out of the corpus."""
        transcript = dict(self._transcript())
        transcript["transcribe_seconds"] = 130.43
        md = build_export_markdown(
            self._metadata(), transcript, {}, None, "", None, None
        )
        self.assertNotIn("transcribe_seconds", md)
        self.assertNotIn("130.43", md)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_transcript_export.py -q -k "provenance or transcribe_seconds"`

(The `-k` expression must be quoted — an unquoted `or` is parsed by the shell, not pytest.)

Expected: 2 failed, 3 passed. `test_frontmatter_includes_transcription_provenance` fails on the missing `language:` line, and `test_provenance_lines_sit_between_duration_and_source_directory` fails with `ValueError` from `md.index("language:")`. The other three assert absences that already hold today — they pass immediately and exist to guard the behavior going forward.

- [ ] **Step 3: Add the frontmatter lines**

In `build_export_markdown`, between `lines.append(f"duration_seconds: {int(duration)}")` and `lines.append(f"source_directory: {_yaml_str(directory_name)}")`, insert:

```python
    # Provenance, not content: model_size is what tells a later reader how
    # much to trust this transcript once the audio it came from is gone.
    # transcribe_seconds is deliberately not exported — it describes the
    # machine that ran the transcription, not the transcript.
    language = transcript_data.get("language")
    if language:
        lines.append(f"language: {_yaml_str(str(language))}")
    model_size = transcript_data.get("model_size")
    if model_size:
        lines.append(f"model_size: {_yaml_str(str(model_size))}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_transcript_export.py -q`

Expected: PASS, all tests in the file.

- [ ] **Step 5: Commit**

```bash
git add app/utils/transcript_export.py tests/test_transcript_export.py
git commit -m "feat: export language and model_size in transcript frontmatter

Which model produced a transcript is what tells a later reader how much to
trust it. That mattered little while transcript.json sat next to the export;
it matters once the export is the copy that survives.

transcribe_seconds stays out — it describes the machine that ran the
transcription, not the transcript.

Refs #66"
```

---

### Task 3: Empty transcripts are not exported

**Files:**
- Modify: `app/utils/transcript_export.py` (new public predicate; early return in `export_transcript`)
- Test: `tests/test_transcript_export.py`

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `has_exportable_content(transcript_data) -> bool`. Public — importable by `app/main_window.py` later if a caller ever wants to check before doing export prep work. No caller does today; do not add one.

- [ ] **Step 1: Write the failing tests**

Add `has_exportable_content` to the import block at the top of
`tests/test_transcript_export.py`:

```python
from app.utils.transcript_export import (
    sanitize_filename_component,
    export_path_for,
    build_export_markdown,
    export_transcript,
    has_exportable_content,
)
```

Then append this class at the end of the file, before the
`if __name__ == "__main__":` block:

```python
class TestHasExportableContent(unittest.TestCase):
    def test_false_for_no_segments(self):
        self.assertFalse(has_exportable_content({"segments": []}))

    def test_false_for_missing_segments_key(self):
        self.assertFalse(has_exportable_content({"language": "en"}))

    def test_false_for_none(self):
        self.assertFalse(has_exportable_content(None))

    def test_true_for_one_segment(self):
        self.assertTrue(has_exportable_content({"segments": [{"text": "hi"}]}))


class TestExportSkipsEmptyTranscripts(unittest.TestCase):
    """A recording that transcribed to nothing produced an export of
    frontmatter plus an empty '# Transcript' heading — files that only
    dilute the corpus."""

    def _metadata(self):
        return {
            "directory": "C:/recordings/rec_20260813_140000",
            "started_at": "2026-08-13T14:00:00",
            "duration": 600,
        }

    def test_empty_transcript_writes_no_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            export_transcript(
                self._metadata(), {"segments": [], "language": "en"},
                {}, None, "", None, None, tmpdir,
            )
            self.assertEqual(list(Path(tmpdir).glob("*.md")), [])

    def test_skip_leaves_an_existing_export_untouched(self):
        """Skipping suppresses a write; it must never delete. Removing stale
        exports is the user's call, and the delete-recording flow already
        offers it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = self._metadata()
            transcript = {
                "segments": [{"start": 3.0, "end": 8.0, "text": "Real content."}],
                "language": "en",
            }
            export_transcript(
                metadata, transcript, {}, None, "", None, None, tmpdir
            )
            written = list(Path(tmpdir).glob("*.md"))
            self.assertEqual(len(written), 1)
            before = written[0].read_bytes()

            export_transcript(
                metadata, {"segments": []}, {}, None, "", None, None, tmpdir
            )

            self.assertEqual(list(Path(tmpdir).glob("*.md")), written)
            self.assertEqual(written[0].read_bytes(), before)

    def test_non_empty_transcript_still_exports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = {
                "segments": [{"start": 3.0, "end": 8.0, "text": "Real content."}],
                "language": "en",
            }
            export_transcript(
                self._metadata(), transcript, {}, None, "", None, None, tmpdir
            )
            self.assertEqual(len(list(Path(tmpdir).glob("*.md"))), 1)

    def test_malformed_transcript_data_does_not_raise(self):
        """A list where a dict was expected reaches .get() — the existing
        best-effort handler must absorb it, as it does every other
        malformed input."""
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                export_transcript(
                    self._metadata(), [], {}, None, "", None, None, tmpdir
                )
            except AttributeError:
                self.fail("export_transcript() raised on malformed transcript_data")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_transcript_export.py -q`

Expected: collection FAILS with `ImportError: cannot import name 'has_exportable_content'`.

- [ ] **Step 3: Add the predicate**

In `app/utils/transcript_export.py`, insert immediately before
`def export_transcript(`:

```python
def has_exportable_content(transcript_data):
    """Whether this transcript is worth writing to the corpus at all.

    No segments means nothing was heard — the export would be frontmatter
    and an empty '# Transcript' heading. build_export_markdown deliberately
    does NOT consult this: it is a pure builder, and whether a document is
    worth writing is policy that belongs at the write site.
    """
    return bool((transcript_data or {}).get("segments"))
```

- [ ] **Step 4: Add the early return**

In `export_transcript`, insert as the first statement inside the `try:`,
above `os.makedirs(transcripts_dir, exist_ok=True)`:

```python
        if not has_exportable_content(transcript_data):
            logger.info(
                "Skipping transcript export for %s — no segments",
                metadata.get("directory"),
            )
            return
```

It goes inside the `try` on purpose: a malformed `transcript_data` reaches
`.get()` here, and the existing handler is what absorbs it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_transcript_export.py -q`

Expected: PASS, all tests in the file.

- [ ] **Step 6: Run the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`

Expected: 655 passed, 1 skipped (626 + 15 from Task 1 + 5 from Task 2 + 9 from Task 3).

- [ ] **Step 7: Commit**

```bash
git add app/utils/transcript_export.py tests/test_transcript_export.py
git commit -m "feat: skip exporting transcripts with no segments

A recording that transcribed to nothing still produced an export:
frontmatter and an empty '# Transcript' heading. Two such files sit in the
current corpus at 179 and 281 bytes, diluting a set meant for analysis.

The check keys on segment count, not word count — the junk is characterized
by having no segments at all, and the smallest legitimate transcript in the
corpus is 145 words, so any word threshold would be a guess.

Skipping suppresses the write and nothing else; a previously written export
is left alone. export_transcript has never deleted a file.

Refs #66"
```

---

## Verification

After Task 3, confirm the change on the real corpus rather than only in
tests. Re-exporting is idempotent (the filename derives from the stable
session directory name and `started_at`), so this overwrites in place:

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -c "import json,pathlib; from app.utils.transcript_export import build_export_markdown; d=pathlib.Path(json.load(open(pathlib.Path.home()/'OneDrive - EPAM'/'Documents'/'TalkTrack'/'settings.json'))['output']['directory']); p=sorted(d.glob('*/transcript.json'))[-1]; print(build_export_markdown({'directory':str(p.parent),'started_at':''}, json.load(open(p,encoding='utf-8')), {}, None, '', None, None)[:1200])"
```

Expected: frontmatter carrying `language` and `model_size`, and segment
lines of the form `**[00:00:01–00:00:09] SPEAKER_01 (0.76):** ...`.
