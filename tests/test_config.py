# tests/test_config.py
import copy
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils import config as config_module
from app.utils.config import Config, DEFAULT_CONFIG


class ConfigTestCase(unittest.TestCase):
    """Base: point CONFIG_DIR/CONFIG_FILE at a temp dir for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self._patchers = [
            patch.object(config_module, "CONFIG_DIR", tmp_path),
            patch.object(config_module, "CONFIG_FILE", tmp_path / "settings.json"),
        ]
        for p in self._patchers:
            p.start()
        self.config_file = tmp_path / "settings.json"
        self.output_dir = tmp_path / "recordings"

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()

    def _write_settings(self, data):
        self.config_file.write_text(json.dumps(data), encoding="utf-8")


class TestCorruptionHandling(ConfigTestCase):
    def test_corrupt_json_falls_back_to_defaults(self):
        self.config_file.write_text('{"audio": {tru', encoding="utf-8")
        cfg = Config()
        self.assertEqual(cfg.get("audio", "sample_rate"),
                         DEFAULT_CONFIG["audio"]["sample_rate"])

    def test_corrupt_json_is_backed_up(self):
        self.config_file.write_text("not json at all", encoding="utf-8")
        Config()
        backups = list(self.config_file.parent.glob("settings.json.bak*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "not json at all")

    def test_empty_file_falls_back_to_defaults(self):
        self.config_file.write_text("", encoding="utf-8")
        cfg = Config()
        self.assertEqual(cfg.get("general", "min_recording_length"),
                         DEFAULT_CONFIG["general"]["min_recording_length"])


class TestAtomicSave(ConfigTestCase):
    def test_save_load_round_trip(self):
        cfg = Config()
        cfg.set("output", "directory", str(self.output_dir))
        cfg.set("transcription", "model_size", "small")
        cfg2 = Config()
        self.assertEqual(cfg2.get("transcription", "model_size"), "small")

    def test_save_leaves_no_temp_file(self):
        cfg = Config()
        cfg.set("output", "directory", str(self.output_dir))
        cfg.save()
        leftovers = [p for p in self.config_file.parent.iterdir()
                     if p.name.startswith("settings") and p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_save_rides_through_a_transient_permission_error(self):
        """settings.json lives in Documents, which is commonly redirected to
        OneDrive; its sync client holds brief locks that made os.replace fail
        with WinError 5 and crash the settings dialog."""
        cfg = Config()
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, "Access is denied")
            real_replace(src, dst)

        with patch("os.replace", side_effect=flaky_replace), patch("time.sleep"):
            cfg.set("general", "silence_duration", 99)

        saved = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["general"]["silence_duration"], 99)

    def test_failed_save_leaves_no_temp_file(self):
        cfg = Config()
        with patch("os.replace", side_effect=PermissionError(5, "Access is denied")), \
             patch("time.sleep"):
            with self.assertRaises(PermissionError):
                cfg.set("general", "silence_duration", 99)

        leftovers = [p for p in self.config_file.parent.iterdir()
                     if p.name.startswith("settings") and p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])


class TestBatchedSave(ConfigTestCase):
    """The settings dialog sets ~40 keys per OK click. One disk write each
    multiplied the chance of colliding with a OneDrive lock by 40 and made
    saving visibly slow; batch() collapses them into a single write."""

    def test_batch_writes_the_file_once(self):
        cfg = Config()
        cfg.save()  # ensure the file exists before counting writes

        with patch("os.replace", wraps=os.replace) as replace:
            with cfg.batch():
                cfg.set("general", "silence_duration", 42)
                cfg.set("general", "min_recording_length", 7)
                cfg.set("audio", "sample_rate", 48000)
            self.assertEqual(replace.call_count, 1)

        saved = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["general"]["silence_duration"], 42)
        self.assertEqual(saved["general"]["min_recording_length"], 7)
        self.assertEqual(saved["audio"]["sample_rate"], 48000)

    def test_batch_saves_even_when_the_body_raises(self):
        """A failure partway through the settings dialog must not silently
        drop the values already applied to the in-memory config."""
        cfg = Config()
        with self.assertRaises(ValueError):
            with cfg.batch():
                cfg.set("general", "silence_duration", 55)
                raise ValueError("boom")

        saved = json.loads(self.config_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["general"]["silence_duration"], 55)

    def test_nested_batches_still_write_once(self):
        cfg = Config()
        cfg.save()
        with patch("os.replace", wraps=os.replace) as replace:
            with cfg.batch():
                with cfg.batch():
                    cfg.set("general", "silence_duration", 11)
                cfg.set("general", "min_recording_length", 3)
            self.assertEqual(replace.call_count, 1)


class TestLoopbackPersistence(ConfigTestCase):
    """The system-audio device used to be re-derived on every launch, so a
    correct choice silently reverted to whatever the (often wrong) default
    lookup returned. It is now saved by name like the mics."""

    def test_last_loopback_defaults_empty(self):
        self.assertEqual(Config().get("audio", "last_loopback"), "")

    def test_last_loopback_round_trips(self):
        cfg = Config()
        cfg.set("audio", "last_loopback", "Speakers (Realtek(R) Audio) (WASAPI Loopback)")
        self.assertEqual(Config().get("audio", "last_loopback"),
                         "Speakers (Realtek(R) Audio) (WASAPI Loopback)")


class TestDefaultConfigIsolation(ConfigTestCase):
    def test_set_does_not_mutate_default_config(self):
        # Saved file is missing the "ui" section entirely.
        self._write_settings({"output": {"directory": str(self.output_dir)}})
        pristine = copy.deepcopy(DEFAULT_CONFIG)
        cfg = Config()
        cfg.set("ui", "theme", "light")
        self.assertEqual(DEFAULT_CONFIG, pristine)

    def test_partial_config_filled_with_defaults(self):
        self._write_settings({
            "output": {"directory": str(self.output_dir)},
            "transcription": {"model_size": "large-v3"},
        })
        cfg = Config()
        self.assertEqual(cfg.get("transcription", "model_size"), "large-v3")
        # Unspecified keys in the same section keep defaults.
        self.assertEqual(cfg.get("transcription", "device"),
                         DEFAULT_CONFIG["transcription"]["device"])
        # Missing sections fully present.
        self.assertEqual(cfg.get("ui", "theme"), DEFAULT_CONFIG["ui"]["theme"])


class TestOutputDirFallback(ConfigTestCase):
    def test_invalid_output_dir_does_not_crash_load(self):
        self._write_settings({"output": {"directory": "Z:\\no\\such\\drive\\path"}})
        try:
            Config()
        except OSError:
            self.fail("Config() raised OSError for an invalid output directory")


class TestCalendarDefaults(ConfigTestCase):
    def test_calendar_enabled_defaults_false(self):
        cfg = Config()
        self.assertFalse(cfg.get("calendar", "enabled"))

    def test_calendar_enabled_round_trips(self):
        cfg = Config()
        cfg.set("calendar", "enabled", True)
        cfg2 = Config()
        self.assertTrue(cfg2.get("calendar", "enabled"))


class TestTranscriptsSessionImportFlag(ConfigTestCase):
    """transcripts.directory and its migration are gone (#74) — transcript.md
    now lives in the recording's own folder. The only surviving key is the
    one-shot flag for importing exports stranded in the old folder."""

    def test_session_import_done_defaults_false(self):
        cfg = Config()
        self.assertFalse(cfg.get("transcripts", "session_import_done"))

    def test_session_import_done_round_trips(self):
        cfg = Config()
        cfg.set("transcripts", "session_import_done", True)
        cfg2 = Config()
        self.assertTrue(cfg2.get("transcripts", "session_import_done"))


class TestLocalModelNameDefault(ConfigTestCase):
    def test_ai_block_has_local_model_name_default(self):
        self.assertEqual(DEFAULT_CONFIG["ai"]["local_model_name"], "")


if __name__ == "__main__":
    unittest.main()
