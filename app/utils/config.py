import copy
import json
import os
from pathlib import Path

from app.utils.app_paths import APP_DATA_DIR
from app.utils.config_migration import apply_meeting_detection_migration


DEFAULT_CONFIG = {
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "mic_device": None,
        "loopback_device": None,
        "last_mic": "",
        "last_mic2": "",
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
        "directory": str(Path(__file__).parent.parent.parent / "transcripts"),
        # One-shot flag for transcripts_migration.import_legacy_exports; set
        # once at startup so later launches do no filesystem work.
        "legacy_import_done": False,
    },
    "transcription": {
        "model_size": "base",
        "language": None,
        "device": "cpu",
        "min_duration": 10,
    },
    "diarization": {
        "enabled": True,
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
        "min_recording_length": 5,
        "auto_record": False,
        "auto_record_threshold": 5,
        "auto_transcribe": True,
        "silence_auto_stop": True,
        "silence_duration": 120,
        "minimize_to_tray": False,
        "show_tray_hint": True,
        "start_menu_offer_done": False,
    },
    "meeting_detection": {
        "mode": "suggest",          # "off" | "suggest" | "auto"
        "threshold_seconds": 5,     # sustained activity before acting on a start
        "detect_end": True,         # suggest stop/pause when the meeting ends
        "end_grace_seconds": 60,    # absence before a meeting counts as ended
        "end_action": "stop",       # auto mode only: "stop" | "pause"
        "use_mic_capture": True,    # strongest signal; opt-out for privacy/perf
        "use_calendar": True,       # reuses the existing Outlook integration
        "use_window_title": False,  # brittle across app versions and locales
        "apps": ["ms-teams", "Teams", "Zoom", "Webex"],
    },
    "ui": {
        "theme": "dark",
        "speakers_collapsed": False,
        "right_panel_collapsed": False,
        "activity_widget_position": None,
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

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = CONFIG_FILE.parent / (CONFIG_FILE.name + ".tmp")
        with open(tmp_path, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp_path, CONFIG_FILE)

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
