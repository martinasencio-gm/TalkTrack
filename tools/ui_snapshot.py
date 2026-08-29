"""Offscreen screenshot harness for TalkTrack's UI surfaces.

Renders each panel (and the whole window) with fixture data and saves a PNG,
so a de-cluttering change can be compared before/after without launching the
real app or clicking anything.

    python tools/ui_snapshot.py                    # -> docs/ui-snapshots/current/
    python tools/ui_snapshot.py --out docs/ui-snapshots/baseline
    python tools/ui_snapshot.py --only transcript_viewer

Runs under QT_QPA_PLATFORM=offscreen, the same platform the test suite uses
(tests/conftest.py), so it needs no display and is safe in CI.

Config is redirected at a throwaway directory before anything constructs a
Config, for the same reason tests/conftest.py does it: building MainWindow can
trigger a save, and that would otherwise overwrite the developer's real
~/.talktrack/settings.json -- including the API keys and HF token in it.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = REPO_ROOT / "docs" / "ui-snapshots" / "current"

# Widths mirror MainWindow._setup_ui's splitter sizes ([262, 776, 322]) so a
# panel snapshot shows the same wrapping and elision the real column does.
LIBRARY_W, TRANSCRIPT_W, INSPECTOR_W = 262, 776, 322
WINDOW_W = LIBRARY_W + TRANSCRIPT_W + INSPECTOR_W


def _isolate_config(tmp_root):
    """Point Config's on-disk file at a throwaway dir.

    Must run before any Config is constructed.
    """
    from app.utils import config as config_module

    config_dir = tmp_root / ".talktrack"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(
        json.dumps({
            # The same already-past-first-run state tests/conftest.py
            # pre-seeds: an empty token opens a blocking setup wizard and an
            # unset offer flag opens a blocking QMessageBox, neither of which
            # can be dismissed under the offscreen platform.
            "general": {"start_menu_offer_done": True},
            "diarization": {"hf_token": "snapshot-placeholder-token"},
        }),
        encoding="utf-8",
    )
    config_module.CONFIG_DIR = config_dir
    config_module.CONFIG_FILE = config_dir / "settings.json"
    return config_dir


# Module-level so the QApplication outlives _make_app's frame. A local-only
# reference is dropped when the function returns, Python collects the
# QApplication, and every widget built afterwards dies on Qt's fail-fast
# (exit 0xC0000409) with no Python traceback to explain it.
_APP = None


def _make_app():
    """QApplication carrying the real stylesheet and fonts, so snapshots show
    what the user actually sees rather than Qt's default theme."""
    global _APP
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFontDatabase

    _APP = QApplication.instance() or QApplication(sys.argv)
    app = _APP

    style_path = REPO_ROOT / "resources" / "style.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))

    font_path = REPO_ROOT / "resources" / "fonts" / "InterVariable.ttf"
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))

    return app


def _fixture_recordings(root):
    """Build a recordings dir covering every row state the list can show:
    audio-only, transcribed, transcribed + summarized, and batch-queued."""
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    specs = [
        ("Weekly Engineering Sync", 3 * 60 + 42, 0, True, True, False, ["team"]),
        ("One-to-one with Priya", 27 * 60, 1, True, False, False, ["1-1"]),
        ("Customer call - Northwind Trading", 61 * 60 + 5, 2, True, True, True, ["customer"]),
        ("Untitled recording", 51, 3, False, False, False, []),
    ]

    for name, duration, days_ago, transcript, summary, queued, tags in specs:
        started = now - timedelta(days=days_ago, hours=2)
        directory = root / started.strftime("%Y%m%d_%H%M%S")
        directory.mkdir(parents=True, exist_ok=True)

        audio = directory / "combined_audio.wav"
        audio.write_bytes(b"RIFF")  # presence is all the row checks

        metadata = {
            "directory": str(directory),
            "name": name,
            "started_at": started.isoformat(),
            "duration": duration,
            "audio_files": {"combined": str(audio)},
            "tags": tags,
        }
        if queued:
            metadata["batch_pending"] = True
        (directory / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )

        if transcript:
            (directory / "transcript.json").write_text(
                json.dumps({"segments": []}), encoding="utf-8"
            )
        if summary:
            (directory / "summary.md").write_text(
                "- Agreed the rollout date\n\n## Action Items\n_None._\n",
                encoding="utf-8",
            )


def _fixture_transcript():
    from app.transcription.transcriber import TranscriptResult, TranscriptSegment

    lines = [
        (0.0, 4.2, "You",
         "Thanks for making the time - I wanted to walk through the migration plan."),
        (4.2, 11.8, "Remote",
         "Sounds good. My main worry is the cutover window on the Friday."),
        (11.8, 18.5, "You",
         "That's fair. We can stage it so the read path moves first and the "
         "writes follow on Monday."),
        (18.5, 26.0, "Remote",
         "If we do that, who owns the rollback call if the read path misbehaves?"),
        (26.0, 31.4, "You",
         "I'll own it. I'll write the runbook up before Thursday so there's "
         "no ambiguity."),
    ]
    segments = [
        TranscriptSegment(start=start, end=end, text=text, speaker=speaker)
        for start, end, speaker, text in lines
    ]
    return TranscriptResult(segments=segments, duration=31.4)


def _newest_metadata(recordings_dir):
    newest = sorted(recordings_dir.iterdir(), reverse=True)[0]
    return json.loads((newest / "metadata.json").read_text(encoding="utf-8"))


def _save(widget, path, width, height=None):
    """Render a widget offscreen and write it to disk.

    show() before grab(): an unshown widget has no laid-out geometry, so the
    grab comes back at the default size with its children unpositioned.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication

    widget.resize(width, height or max(widget.sizeHint().height(), 80))
    widget.show()
    QApplication.processEvents()
    # Panels retire old children with deleteLater(), and processEvents() does
    # not flush DeferredDelete -- without this the widget is gone from the
    # layout but still painted at its stale geometry, so e.g. the transcript's
    # "Nothing selected" placeholder renders on top of the segments.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = widget.grab().save(str(path))
    widget.hide()
    return saved


# --- surfaces --------------------------------------------------------------
# Each builder returns (widget, width, height). Registered by name so --only
# can pick one without paying to construct the rest.

def _surface_transcript_viewer(ctx):
    from app.ui.transcript_viewer import TranscriptViewer

    viewer = TranscriptViewer(config=ctx["config"])
    viewer.set_audio_path(str(ctx["recordings_dir"] / "combined_audio.wav"))
    viewer.set_diarization_available(True)
    viewer.set_summarize_available(True)
    viewer.display_transcript(_fixture_transcript())
    return viewer, TRANSCRIPT_W, 640


def _surface_recording_header(ctx):
    from app.ui.recording_header import RecordingHeader

    header = RecordingHeader()
    header.set_recording(
        ctx["metadata"],
        speaker_count=2,
        calendar_event={
            "subject": "Weekly Engineering Sync",
            "organizer": "Priya Raman",
            "attendees": ["Priya Raman", "Sam Okafor"],
        },
        model_size="small",
        transcribe_seconds=42.0,
    )
    return header, TRANSCRIPT_W, 140


def _surface_recordings_list(ctx):
    from app.ui.recordings_list import RecordingsList

    listing = RecordingsList(str(ctx["recordings_dir"]))
    listing.refresh()
    return listing, LIBRARY_W, 640


def _surface_capture_bar_idle(ctx):
    from app.ui.recording_controls import RecordingControls

    controls = RecordingControls()
    controls.set_capturing("Shure MV7", "Microsoft Teams")
    controls.set_source_summary("Mic + Teams", "Levels look healthy")
    return controls, WINDOW_W, None


def _surface_capture_bar_recording(ctx):
    from app.recording.recorder import RecordingState
    from app.ui.recording_controls import RecordingControls

    controls = RecordingControls()
    controls.set_capturing("Shure MV7", "Microsoft Teams")
    controls.set_source_summary("Mic + Teams", "Levels look healthy")
    controls.set_state(RecordingState.RECORDING)
    controls.update_time(3821)
    return controls, WINDOW_W, None


def _surface_capture_bar_transcribing(ctx):
    from app.ui.recording_controls import RecordingControls

    controls = RecordingControls()
    controls.set_transcribing(
        True, percent=62, name="Weekly Engineering Sync", elapsed_seconds=95
    )
    return controls, WINDOW_W, None


def _surface_inspector(ctx):
    from app.ui.chat_panel import ChatPanel
    from app.ui.inspector import InspectorWidget
    from app.ui.notes_panel import NotesPanel
    from app.ui.speaker_name_panel import SpeakerNamePanel
    from app.ui.summary_panel import SummaryPanel

    inspector = InspectorWidget()
    inspector.add_notes_panel(NotesPanel())
    inspector.add_speakers_panel(SpeakerNamePanel(config=ctx["config"]))
    inspector.add_summary_panel(SummaryPanel())
    inspector.add_chat_panel(ChatPanel())
    return inspector, INSPECTOR_W, 640


def _surface_main_window(ctx):
    from app.main_window import MainWindow
    from app.utils.com_session_worker import ComSessionPoller

    # MainWindow starts a ComSessionPoller, whose worker is a separate OS
    # process doing pycaw/comtypes COM polling. Under the snapshot harness it
    # dies on COM teardown and respawns on a backoff forever, so the run never
    # finishes. Neutering start() leaves _queue as None, which is exactly the
    # state get_snapshot() already handles -- it returns the empty default
    # snapshot, so the window builds against "no audio apps detected".
    original_start = ComSessionPoller.start
    ComSessionPoller.start = lambda self: None
    try:
        window = MainWindow()
    finally:
        ComSessionPoller.start = original_start
    window.recordings_list.recordings_dir = ctx["recordings_dir"]
    window.recordings_list.refresh()
    window.transcript_viewer.set_diarization_available(True)
    window.transcript_viewer.set_summarize_available(True)
    window.transcript_viewer.display_transcript(_fixture_transcript())
    window.recording_header.set_recording(ctx["metadata"], speaker_count=2)
    return window, 1400, 900


SURFACES = {
    "capture_bar_idle": _surface_capture_bar_idle,
    "capture_bar_recording": _surface_capture_bar_recording,
    "capture_bar_transcribing": _surface_capture_bar_transcribing,
    "recordings_list": _surface_recordings_list,
    "recording_header": _surface_recording_header,
    "transcript_viewer": _surface_transcript_viewer,
    "inspector": _surface_inspector,
    "main_window": _surface_main_window,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help="output directory (default: docs/ui-snapshots/current)",
    )
    parser.add_argument(
        "--only", action="append", choices=sorted(SURFACES),
        help="snapshot only this surface (repeatable)",
    )
    parser.add_argument(
        "--list", action="store_true", help="list surface names and exit",
    )
    args = parser.parse_args()

    if args.list:
        for name in sorted(SURFACES):
            print(name)
        return 0

    # Resolved so the progress line's relative_to(REPO_ROOT) works for a
    # relative --out as well as an absolute one.
    out_dir = Path(args.out).resolve()
    tmp_root = Path(tempfile.mkdtemp(prefix="talktrack-snap-"))
    try:
        _isolate_config(tmp_root)
        _make_app()

        from app.utils.config import Config

        recordings_dir = tmp_root / "recordings"
        _fixture_recordings(recordings_dir)

        ctx = {
            "config": Config(),
            "recordings_dir": recordings_dir,
            "metadata": _newest_metadata(recordings_dir),
        }

        failures = []
        for name in args.only or list(SURFACES):
            try:
                widget, width, height = SURFACES[name](ctx)
                path = out_dir / f"{name}.png"
                if _save(widget, path, width, height):
                    try:
                        shown = path.relative_to(REPO_ROOT)
                    except ValueError:  # --out pointed outside the repo
                        shown = path
                    print(f"  {shown}")
                else:
                    failures.append((name, "grab().save() returned False"))
            except Exception as exc:  # one bad surface must not lose the rest
                failures.append((name, f"{type(exc).__name__}: {exc}"))

        for name, why in failures:
            print(f"  FAILED {name}: {why}", file=sys.stderr)
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
