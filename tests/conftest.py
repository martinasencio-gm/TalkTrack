import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.utils import config as config_module


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    """Point Config's on-disk file at a throwaway dir for every test.

    Without this, any test that constructs a real Config/MainWindow and
    triggers a save (directly, or via closeEvent) writes to the developer's
    actual ~/.talktrack/settings.json — including secrets already stored
    there (API keys, HF tokens) and settings like close_to_tray that
    tests should never be able to flip on a real machine.

    MainWindow.__init__ schedules two delayed one-time-setup checks via
    QTimer.singleShot: _check_startup_status at 500ms (which opens a
    blocking DiarizationSetupWizard.exec() when diarization.hf_token is
    empty) and _maybe_offer_start_menu_shortcut at 1500ms (which opens a
    blocking QMessageBox.question when general.start_menu_offer_done is
    still False). Neither timer fires within the test that constructs the
    window, but once enough real wall-clock time elapses later in the
    suite, some other test's app.processEvents() call delivers it — and a
    Qt modal has no way to be dismissed under the offscreen platform, so
    the run hangs. A real developer's config never hits either path (the
    token is already set and the offer is already recorded done), so it
    only surfaces once tests get a fresh, isolated config — pre-seed both
    to match that already-past-first-run state.
    """
    config_dir = tmp_path / ".talktrack"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(
        json.dumps({
            "general": {"start_menu_offer_done": True},
            "diarization": {"hf_token": "test-placeholder-token"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_dir / "settings.json")
