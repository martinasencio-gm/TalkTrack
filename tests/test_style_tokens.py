"""Guard against new hardcoded colours/sizes creeping back into token-migrated files.

Not a logic test — a grep-style regression guard. `app/ui/tokens.py` exists so a
hex colour or px font-size is named once instead of being re-typed inline (see
".claude/rules/ui-patterns.md" > Design tokens). Once a file has been migrated
onto tokens (its call sites reference `tokens.X` instead of literal `#rrggbb` /
`NNpx` strings), a new raw literal in that file is exactly the drift this guard
exists to catch — it means a token wasn't reached for.

This does not require every UI file to be migrated; it only holds the line on
the ones already converted, listed in `_MIGRATED_FILES` below. Add a file to
that list once it's been migrated onto tokens.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "app" / "ui"

# Files already migrated onto app/ui/tokens.py (Phase 6 of the 2026-08-29
# UI-declutter plan). A raw hex colour or bare NNpx font-size anywhere in one
# of these is a regression — it should be a tokens.* reference instead.
_MIGRATED_FILES = [
    "activity_indicator.py",
    "batch_process_info_dialog.py",
    "batch_run_dialog.py",
    "compact_strip.py",
    "recording_controls.py",
    "tag_recording_dialog.py",
]

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
_FONT_SIZE_RE = re.compile(r"font-size:\s*\d+px\b")


class TestStyleTokens(unittest.TestCase):
    def test_migrated_files_have_no_raw_hex_colours(self):
        for name in _MIGRATED_FILES:
            path = UI_DIR / name
            text = path.read_text(encoding="utf-8")
            matches = _HEX_RE.findall(text)
            self.assertEqual(
                matches, [],
                f"{name} has raw hex colour literal(s) {matches} — "
                f"add the value to app/ui/tokens.py and reference it instead.",
            )

    def test_migrated_files_have_no_raw_px_font_sizes(self):
        for name in _MIGRATED_FILES:
            path = UI_DIR / name
            text = path.read_text(encoding="utf-8")
            matches = _FONT_SIZE_RE.findall(text)
            self.assertEqual(
                matches, [],
                f"{name} has raw font-size literal(s) {matches} — "
                f"use one of tokens.TYPE_XS/SM/MD/BASE/LG instead.",
            )

    def test_tokens_module_exists_and_is_importable(self):
        from app.ui import tokens  # noqa: F401 — import is the assertion

    def test_migrated_files_import_tokens(self):
        for name in _MIGRATED_FILES:
            path = UI_DIR / name
            text = path.read_text(encoding="utf-8")
            self.assertIn(
                "from app.ui import tokens", text,
                f"{name} is listed as migrated but doesn't import tokens.",
            )


if __name__ == "__main__":
    unittest.main()
