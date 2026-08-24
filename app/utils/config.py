import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path

from app.utils.app_paths import APP_DATA_DIR
from app.utils.atomic_io import atomic_write_json
from app.utils.config_migration import (
    apply_close_to_tray_migration,
    apply_meeting_detection_migration,
)


DEFAULT_CONFIG = {
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "mic_device": None,
        "loopback_device": None,
        "last_mic": "",
        "last_mic2": "",
        # Saved by name, like the mics: device indices shift between
        # sessions as hardware comes and goes.
        "last_loopback": "",
        "capture_mode": "legacy",
        "selected_apps": [],
        "hidden_devices": [],
        "mic_count": 1,
        "mic_mute_on_start": False,
        "mic_gain": 1.0,
    },
    "output": {
        "directory": str(Path(__file__).parent.parent.parent / "recordings"),
        "format": "wav",
        "filename_template": "recording_{timestamp}",
    },
    "transcripts": {
        # One-shot flag for the session-folder import of exports stranded in
        # the old separate transcripts/ folder; set once at startup so later
        # launches do no filesystem work. See app/main_window.py.
        "session_import_done": False,
    },
    "transcription": {
        "engine": "faster_whisper",
        "model_size": "base",
        "language": None,
        "device": "cpu",
        "min_duration": 10,
    },
    "diarization": {
        "enabled": True,
        "engine": "sherpa_onnx",
        "hf_token": "",
        "min_speakers": None,
        "max_speakers": None,
    },
    "ai": {
        "provider": "none",
        "api_key": "",
        "model": "",
        "local_model_path": "",
        "embed_model": "all-MiniLM-L6-v2",
        "auto_summarize": True,
        "provider_settings": {},
    },
    "general": {
        "user_name": "",
        "min_recording_length": 5,
        "auto_record": False,
        "auto_record_threshold": 5,
        "auto_transcribe": True,
        # Recordings the app doesn't transcribe itself (auto-transcribe off,
        # or under min_duration) get tagged for the scheduled batch run
        # instead of quietly piling up untranscribed.
        "batch_auto_queue": True,
        "silence_auto_stop": True,
        "silence_duration": 120,
        # The minimize button always minimizes to the taskbar; the tray is
        # reached from the close button's "minimize instead" choice.
        "close_to_tray": False,
        "launch_in_compact_mode": False,
        "show_tray_hint": True,
        "start_menu_offer_done": False,
        "prompt_tags_after_recording": True,
        "auto_tag_by_name": True,
        "replace_you_with_name": False,
    },
    "meeting_detection": {
        "mode": "suggest",          # "off" | "suggest" | "auto"
        "threshold_seconds": 5,     # sustained activity before acting on a start
        "detect_end": True,         # suggest stop/pause when the meeting ends
        "end_grace_seconds": 60,    # absence before a meeting counts as ended
        "end_action": "stop",       # auto mode only: "stop" | "pause"
        "use_mic_capture": True,    # strongest signal; opt-out for privacy/perf
        "use_calendar": True,       # reuses the existing Outlook integration
        "use_window_title": True,   # captures meeting / contact names from window titles
        "apps": ["ms-teams", "Teams", "Zoom", "Webex"],
    },
    "ui": {
        "theme": "dark",
        "speakers_collapsed": False,
        "right_panel_collapsed": False,
        "audio_sources_collapsed": False,
        "recordings_collapsed": False,
        "activity_widget_position": None,
        "compact_strip_visible": False,
        "compact_strip_position": None,
        "strip_variant": "full",
        # Where a double-click on the capture bar lands, as an entry point
        # into the fixed full -> compact_bar -> pill -> full chain.
        "double_click_target": "compact_bar",  # "compact_bar" | "pill"
    },
    "calendar": {
        "enabled": False,
    },
}

CONFIG_DIR = APP_DATA_DIR
CONFIG_FILE = CONFIG_DIR / "settings.json"


class Config:
    def __init__(self):
        self._data = {}
        self._batch_depth = 0
        self._batch_dirty = False
        self.load()

    def load(self):
        self._data = None
        saved = None
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                if not isinstance(saved, dict):
                    raise ValueError("settings root is not an object")
                self._data = self._deep_merge(DEFAULT_CONFIG, saved)
            except (json.JSONDecodeError, ValueError, OSError):
                saved = None
                self._backup_corrupt_file()
        if self._data is None:
            self._data = copy.deepcopy(DEFAULT_CONFIG)
        self._data = apply_meeting_detection_migration(saved, self._data)
        self._data = apply_close_to_tray_migration(saved, self._data)
        try:
            os.makedirs(self._data["output"]["directory"], exist_ok=True)
        except OSError:
            self._data["output"]["directory"] = DEFAULT_CONFIG["output"]["directory"]
            os.makedirs(self._data["output"]["directory"], exist_ok=True)

    def save(self):
        """Persist settings, unless a batch() is collecting writes.

        Goes through atomic_io rather than a bare replace: settings.json
        lives under Documents, which is routinely redirected to OneDrive,
        whose sync client holds brief locks that made os.replace fail with
        WinError 5 and take the settings dialog down with it.
        """
        if self._batch_depth:
            self._batch_dirty = True
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(CONFIG_FILE, self._data, indent=2)

    @contextmanager
    def batch(self):
        """Collapse the enclosed set() calls into a single disk write.

        The settings dialog assigns ~40 keys per OK click; one write each
        meant 40 chances to collide with a sync lock, all to produce the
        same file. Writes on exit even when the body raises, so values
        already applied in memory aren't silently lost, and nests safely.
        """
        self._batch_depth += 1
        try:
            yield self
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._batch_dirty:
                self._batch_dirty = False
                self.save()

    def _backup_corrupt_file(self):
        try:
            backup = CONFIG_FILE.parent / (CONFIG_FILE.name + ".bak")
            os.replace(CONFIG_FILE, backup)
        except OSError:
            pass

    def get(self, *keys):
        value = self._data
        for key in keys:
            value = value[key]
        return value

    def set(self, *keys_and_value):
        keys = keys_and_value[:-1]
        value = keys_and_value[-1]
        d = self._data
        for key in keys[:-1]:
            d = d[key]
        d[keys[-1]] = value
        self.save()

    @property
    def data(self):
        return self._data

    def _deep_merge(self, base, override):
        # Deep-copy so missing sections don't alias (and later mutate)
        # the module-global DEFAULT_CONFIG through Config.set().
        result = copy.deepcopy(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
