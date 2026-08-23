import ctypes
import json
import logging
import os
import stat
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta

import shutil

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMenu, QMessageBox, QFileDialog, QSizePolicy,
    QButtonGroup
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QAction, QFontMetrics, QColor, QIcon

from app.ui.search_bar import SearchBar
from app.utils import batch_queue, tag_manager
from app.utils.icons import colored_pixmap
from app.ui.delete_scope_dialog import (
    DeleteScopeDialog, DELETE_RECORDINGS, DELETE_TRANSCRIPTIONS, DELETE_BOTH
)

logger = logging.getLogger(__name__)

# Fixed filenames the app itself writes for transcript-derived artifacts.
# Unlike the whole session directory removed by DELETE_BOTH, these names are
# never user- or import-controlled, so a static list is safe.
TRANSCRIPTION_FILENAMES = [
    "transcript.json", "transcript.md", "transcript.txt", "summary.md",
    "action_items.json", "speaker_names.json",
]

# Audio filename patterns the app writes into a session directory. Globbed
# rather than read from metadata["audio_files"] alone so a "recordings only"
# delete also catches dual-mic raw temps or a track metadata lost track of.
_AUDIO_GLOB_PATTERNS = ("*_audio.wav", "*_audio.mp3", "*_raw.wav")

# style.qss's "QListWidget::item { padding: 10px; border-bottom: 1px solid
# ...; }" is drawn around a setItemWidget row too, but Qt shrinks the
# embedded widget to fit inside that padding+border rather than growing the
# item to fit the widget. A row's sizeHint() must add this back or its two
# text lines get squeezed into less height than they need and overlap.
_LIST_ITEM_VERTICAL_CHROME = 21  # 10px top + 10px bottom padding + 1px border-bottom


def partition_by_queue_state(metadatas):
    """Split recordings into (not yet queued, already queued).

    Kept out of the widget so the menu's labels and enablement can be
    tested without a QListWidget.
    """
    queued, unqueued = [], []
    for metadata in metadatas or []:
        if not metadata or "directory" not in metadata:
            continue
        (queued if batch_queue.is_queued(metadata) else unqueued).append(metadata)
    return unqueued, queued


def format_relative_date(dt, now=None):
    """"Today, 09:02" / "Yesterday, 14:30" / "19 Aug, 15:20".

    Short and glanceable in a 262px-wide column, unlike the fixed-width
    absolute timestamp it replaces — which is what forced the elision
    workarounds on the date label in _build_row_widget.
    """
    now = now or datetime.now()
    if dt.date() == now.date():
        return f"Today, {dt.strftime('%H:%M')}"
    if dt.date() == (now.date() - timedelta(days=1)):
        return f"Yesterday, {dt.strftime('%H:%M')}"
    return f"{dt.day} {dt.strftime('%b')}, {dt.strftime('%H:%M')}"


def has_transcript(metadata):
    """Checked live against disk, not trusted from metadata — a delete can
    remove the transcript without updating metadata, and both the row badge
    and the Untranscribed filter chip need to reflect that immediately."""
    return (Path(metadata.get("directory", "")) / "transcript.json").exists()


class _RecordingRow(QWidget):
    """Row widget for the recordings list.

    Elides its text labels to their actually-assigned width on every resize.
    Two things this protects against, both of which previously showed up as
    truncated text:

    1. An unbounded-length recording name inflates the row's sizeHint, and
       QListWidget then widens EVERY row to match the widest one — forcing a
       horizontal scrollbar whose viewport edge clips the row's right side.
    2. A label sized to its own pixel-exact text width has zero slack, so any
       font-metric difference between where sizeHint is computed and where the
       text is actually rendered chops the final glyph ("51s" rendering "51c").

    Elision is the graceful failure mode: when space really is short the text
    ends in an ellipsis, which reads as intentional, instead of a half-drawn
    character that reads as a bug.
    """

    def __init__(self):
        super().__init__()
        self._elidable = []  # list of (label, full_text)

    def sizeHint(self):
        # Report 0 width so QListWidget doesn't widen its column beyond the
        # visible viewport width when populating items on initial load.
        hint = super().sizeHint()
        return QSize(0, hint.height())

    def register_elidable(self, label, full_text):
        label.setToolTip(full_text)
        self._elidable.append((label, full_text))
        self._reelide(label, full_text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for label, full_text in self._elidable:
            self._reelide(label, full_text)

    @staticmethod
    def _reelide(label, full_text):
        metrics = QFontMetrics(label.font())
        label.setText(metrics.elidedText(full_text, Qt.TextElideMode.ElideRight, label.width()))


class _SearchWorker(QThread):
    """Runs recording search off the GUI thread.

    Semantic search loads an embedding model and embeds the whole corpus —
    seconds to minutes on first use. Inline, that froze the window.
    """

    results_ready = pyqtSignal(list)

    def __init__(self, recordings_dir, query, is_semantic):
        super().__init__()
        self._recordings_dir = recordings_dir
        self._query = query
        self._is_semantic = is_semantic

    def run(self):
        from app.ai.search_index import load_all_transcripts, text_search
        transcripts = load_all_transcripts(self._recordings_dir)

        results = None
        if self._is_semantic:
            try:
                from app.ai.search_index import semantic_search
                from app.ai.provider_factory import create_provider
                from app.utils.config import Config
                config = Config()
                ai_config = config.data.get("ai", {})
                provider = create_provider(ai_config)
                if provider is not None:
                    results = semantic_search(self._query, transcripts, provider,
                                              recordings_dir=self._recordings_dir)
            except Exception:
                logger.exception("Semantic search failed — falling back to text")
        if results is None:
            results = text_search(self._query, transcripts)

        self.results_ready.emit(results)


_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _is_cloud_placeholder(path):
    """True if `path` is a OneDrive Files On-Demand placeholder not yet
    hydrated to local disk (FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS). Opening
    such a file blocks on a synchronous cloud fetch with no timeout - a
    crash-orphaned recording under a OneDrive-synced output directory can
    leave one behind, and reading it here runs on the UI thread during
    MainWindow construction (observed: an 8-minute startup hang on one
    un-hydrated file)."""
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    if attrs == _INVALID_FILE_ATTRIBUTES:
        return False
    return bool(attrs & _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)


def salvage_orphaned_recordings(recordings_dir, min_age_seconds=600):
    """Give crash-orphaned recording dirs minimal metadata.

    The recorder creates the session dir before capture and writes
    metadata.json only on successful stop, so a crash mid-recording leaves
    WAVs in a directory the list skips — invisible forever. Synthesize
    metadata so the audio shows up as a "Recovered" recording instead of
    deleting user audio. Recent dirs are skipped: they may belong to a
    recording in progress. Returns the list of salvaged directories.
    """
    recordings_dir = Path(recordings_dir)
    if not recordings_dir.exists():
        return []

    salvaged = []
    now = time.time()
    for entry in recordings_dir.iterdir():
        if not entry.is_dir() or (entry / "metadata.json").exists():
            continue

        audio_files = {}
        for key, fname in (("mic", "mic_audio.wav"),
                           ("system", "system_audio.wav"),
                           ("combined", "combined_audio.wav")):
            path = entry / fname
            if path.exists():
                audio_files[key] = str(path)
        if not audio_files:
            continue

        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if now - mtime < min_age_seconds:
            continue

        duration = 0.0
        for _key in ("combined", "system", "mic"):
            candidate = audio_files.get(_key)
            if not candidate or _is_cloud_placeholder(candidate):
                continue
            try:
                import soundfile as sf
                duration = float(sf.info(candidate).duration)
            except Exception:
                logger.exception("Could not read duration for %s", candidate)
            break
        else:
            logger.warning(
                "Skipping duration read for %s — audio not hydrated locally "
                "(OneDrive cloud-only placeholder)", entry,
            )

        created = datetime.fromtimestamp(mtime)
        metadata = {
            "id": entry.name.replace("recording_", ""),
            "directory": str(entry),
            "name": f"Recovered {created.strftime('%Y-%m-%d %H:%M')}",
            "started_at": created.isoformat(),
            "stopped_at": created.isoformat(),
            "duration": duration,
            "audio_files": audio_files,
            "recovered": True,
        }
        try:
            with open(entry / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            salvaged.append(str(entry))
            logger.info("Salvaged orphaned recording: %s", entry)
        except OSError:
            logger.exception("Failed to salvage %s", entry)
    return salvaged


def _rmtree_robust(directory, retries=4, initial_delay=0.1):
    """shutil.rmtree with Windows-friendly retry + read-only handling.

    Windows holds transient locks from Explorer/indexer/Defender on just-
    touched audio files. A plain rmtree races with those locks and can
    leave the folder intact. This retries with exponential backoff and
    chmods read-only files writable before removing.
    """
    def onerror(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    # A nonexistent path raises FileNotFoundError, which looks identical to a
    # transient lock to the except-and-retry loop below — without this check
    # a batch delete where one folder was already removed externally burns
    # four retries (up to 1.5s of UI-thread sleeping) and then pops a modal,
    # for a directory that was already gone.
    if not Path(directory).exists():
        return

    last_exc = None
    delay = initial_delay
    for attempt in range(retries):
        try:
            shutil.rmtree(directory, onerror=onerror)
            if not Path(directory).exists():
                return
            last_exc = OSError(f"rmtree returned but path still exists: {directory}")
        except Exception as e:
            last_exc = e
        logger.warning(
            "rmtree attempt %d failed for %s: %s", attempt + 1, directory, last_exc
        )
        time.sleep(delay)
        delay *= 2
    raise last_exc


def _remove_file_robust(path):
    """os.remove with a chmod-and-retry fallback for Windows read-only locks."""
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def _delete_transcription_files(directory):
    """Remove this session's transcript-derived artifacts, including
    transcript.md (see app/utils/transcript_export.py — it now writes
    alongside transcript.json in the session directory, not to a separate
    folder).

    metadata.json needs no update: transcribed status is derived live from
    disk, both for the "Transcribed" pill and for _selected_transcribed
    (drives the Transcribe/Export context-menu actions) — both check
    transcript.json directly.
    """
    for filename in TRANSCRIPTION_FILENAMES:
        path = Path(directory) / filename
        if path.exists():
            _remove_file_robust(path)


def _delete_audio_files(directory):
    """Remove only this session's audio files, keeping every
    transcript-derived artifact (transcript.json/.md, summary, action items,
    speaker names, notes, chat history, calendar tag, embeddings) intact —
    the session survives as a transcript-only entry."""
    directory = Path(directory)
    for pattern in _AUDIO_GLOB_PATTERNS:
        for path in directory.glob(pattern):
            _remove_file_robust(path)


def _has_any_audio(directory):
    directory = Path(directory)
    return any(next(directory.glob(pattern), None) is not None
               for pattern in _AUDIO_GLOB_PATTERNS)


def _has_any_transcript(directory):
    directory = Path(directory)
    return (directory / "transcript.json").exists() or (directory / "transcript.md").exists()


class RecordingsList(QWidget):
    """Browse and manage past recordings."""

    recording_selected = pyqtSignal(dict)  # metadata dict
    about_to_delete = pyqtSignal(str)      # directory path — emitted BEFORE rmtree
                                           # so main_window can release caches and
                                           # stop playback on the target files
    recording_deleted = pyqtSignal(str)    # directory path of deleted recording
    recording_files_changed = pyqtSignal(str)  # directory path — partial delete
                                           # (recordings-only or transcriptions-
                                           # only); the session survives but its
                                           # displayed content is now stale
    search_result_selected = pyqtSignal(str, float)  # recording_id, timestamp
    import_requested = pyqtSignal(str)  # chosen audio file path
    transcribe_selected_requested = pyqtSignal(list)  # list[dict] metadata, untranscribed only
    export_selected_requested = pyqtSignal(list)      # list[dict] metadata, transcribed only
    run_batch_requested = pyqtSignal()
    manage_tags_requested = pyqtSignal()
    recording_tags_changed = pyqtSignal(str, list)    # directory, tags list

    def __init__(self, recordings_dir, parent=None):
        super().__init__(parent)
        self.recordings_dir = Path(recordings_dir)
        self._recordings = []
        self._search_worker = None
        self._pending_search = None
        self._showing_search_results = False
        self._filter_query = ""
        self._active_chip_filter = "all"
        self._transcribing = set()
        self._batch_running = False
        try:
            from app.batch.process_monitor import find_running_batch_processes
            self._batch_running = bool(find_running_batch_processes())
        except Exception:
            pass
        try:
            salvage_orphaned_recordings(self.recordings_dir)
        except Exception:
            logger.exception("Orphan salvage failed")
        self._setup_ui()
        self.refresh()

    def active_search_worker(self):
        """Return the in-flight search worker, if any (for shutdown handling)."""
        return self._search_worker

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Search bar
        self.search_bar = SearchBar()
        self.search_bar.search_requested.connect(self._on_search)
        self.search_bar.filter_changed.connect(self._on_filter_changed)
        self.search_bar.cleared.connect(self._on_search_cleared)
        layout.addWidget(self.search_bar)

        import_row = QHBoxLayout()
        self.batch_btn = QPushButton("Run Batch")
        self.batch_btn.setObjectName("batchListBtn")
        self.batch_btn.setToolTip("Open the batch transcription launcher for queued recordings")
        self.batch_btn.clicked.connect(self.run_batch_requested.emit)
        self.batch_btn.setVisible(False)
        import_row.addWidget(self.batch_btn)
        import_row.addStretch()
        self.import_btn = QPushButton("Import...")
        self.import_btn.setToolTip(
            "Import an existing audio file (wav/mp3/m4a) as a new recording, "
            "running it through transcription and diarization."
        )
        self.import_btn.clicked.connect(self._on_import_clicked)
        import_row.addWidget(self.import_btn)
        layout.addLayout(import_row)

        # Filter chips: All / Untranscribed / Tagged — single-select, counts
        # kept live by _update_filter_chip_counts() on every refresh().
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self.chip_all = QPushButton("All")
        self.chip_untranscribed = QPushButton("Untranscribed")
        self.chip_tagged = QPushButton("Tagged")
        self._filter_chip_group = QButtonGroup(self)
        self._filter_chip_group.setExclusive(True)
        for btn, filter_name in (
            (self.chip_all, "all"),
            (self.chip_untranscribed, "untranscribed"),
            (self.chip_tagged, "tagged"),
        ):
            btn.setCheckable(True)
            btn.setObjectName("filterChip")
            self._filter_chip_group.addButton(btn)
            btn.toggled.connect(
                lambda checked, name=filter_name: self._on_chip_toggled(name, checked)
            )
            chip_row.addWidget(btn)
        self.chip_all.setObjectName("filterChipAccent")
        self.chip_all.setChecked(True)
        chip_row.addStretch()
        layout.addLayout(chip_row)

        # List
        self.list_widget = QListWidget()
        self.list_widget.setMinimumHeight(100)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget, 1)

        self._empty_label = QLabel("")
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(
            "color: #75798c; font-size: 11.5px; padding: 8px 6px;"
        )
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

    def _set_empty_message(self, text):
        self._empty_label.setText(text)
        self._empty_label.setVisible(bool(text))

    def set_batch_running(self, running: bool):
        """Update whether a batch transcription process is currently active."""
        self._batch_running = running
        self._update_batch_btn_visibility()

    def most_recent_recording(self):
        """Metadata dict for the newest recording, or None if there are
        none. self._recordings is always the full unfiltered list sorted
        newest-first by refresh() — a live search only flips
        _showing_search_results, it never reassigns self._recordings."""
        return self._recordings[0] if self._recordings else None

    def _update_batch_btn_visibility(self):
        if not hasattr(self, "batch_btn"):
            return
        queued_count = sum(
            1 for m in self._recordings
            if batch_queue.is_queued(m) and not batch_queue.exhausted(m)
        )
        self.batch_btn.setText(f"Run Batch ({queued_count})")
        self.batch_btn.setVisible(queued_count > 0 and not self._batch_running)

    def set_transcribing(self, directories):
        """Mark these session directories as having work in flight.

        Rebuilds only when the set actually changes: the caller polls this
        from the same place it updates the activity widget, which fires far
        more often than the status changes, and refresh() drops the user's
        selection.
        """
        directories = set(directories)
        if directories == self._transcribing:
            return
        self._transcribing = directories
        if not self._showing_search_results:
            selected = self.list_widget.currentRow()
            self.refresh()
            if 0 <= selected < self.list_widget.count():
                self.list_widget.setCurrentRow(selected)

    def refresh(self):
        self._showing_search_results = False
        self._set_empty_message("")
        self.list_widget.clear()
        self._recordings = []

        if not self.recordings_dir.exists():
            return

        for entry in sorted(self.recordings_dir.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            meta_path = entry / "metadata.json"
            if not meta_path.exists():
                continue

            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            if not metadata.get("directory"):
                continue

            self._recordings.append(metadata)

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, metadata)
            self.list_widget.addItem(item)
            row_widget = self._build_row_widget(metadata)
            hint = row_widget.sizeHint()
            item.setSizeHint(QSize(hint.width(), hint.height() + _LIST_ITEM_VERTICAL_CHROME))
            self.list_widget.setItemWidget(item, row_widget)

        self._update_batch_btn_visibility()
        self._update_filter_chip_counts()
        self._apply_filters()

    def _build_row_widget(self, metadata):
        """Build a two-line recording row: bold name + duration on top,
        muted date + colored status pill(s) on the bottom.

        Everything is left-aligned except duration/pills, and the only
        fields allowed to shrink are the name and the date, which elide.
        Nothing is right-aligned against the row's edge sized to a
        pixel-exact text fit — that arrangement is what produced the
        clipped "51s"/"Transcribed" text this layout replaces.
        """
        widget = _RecordingRow()
        outer = QVBoxLayout(widget)
        outer.setContentsMargins(8, 3, 11, 3)
        outer.setSpacing(3)

        name = metadata.get("name", "")
        started = metadata.get("started_at", "")
        try:
            dt = datetime.fromisoformat(started)
            date_str = format_relative_date(dt)
        except (ValueError, TypeError):
            date_str = started

        # --- Line 1: name (bold, elidable) ... duration (muted, fixed) ---
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        # Ignored horizontal policy keeps an unbounded-length name from
        # contributing its full text width to this row's sizeHint — see
        # _RecordingRow's docstring for why that matters.
        #
        # Font sizes/colors live in each label's own objectName-scoped QSS
        # rule, never in setFont()/inline setStyleSheet: the global
        # "QWidget { font-size: 10pt }" rule in style.qss overrides
        # setFont() once a widget is polished, but a QSS selector is more
        # specific and wins. Widths are then left to Qt's own sizeHint,
        # which measures with the correctly-polished font — hand-measuring
        # with QFontMetrics here reads the wrong font and produces wrong
        # widths.
        name_label = QLabel()
        name_label.setObjectName("recordingRowName")
        name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top_row.addWidget(name_label, 1)
        widget.register_elidable(name_label, name or date_str)

        # Duration is short and bounded ("1h 23m 45s" at worst), so it keeps
        # its natural width and never elides — it is information the list
        # exists to show, and it was the field visibly losing its final glyph
        # ("51s" drawing as "51c") back when it was right-aligned at the edge.
        dur_label = QLabel(self._format_duration(metadata.get("duration", 0)))
        dur_label.setObjectName("recordingRowDur")
        top_row.addWidget(dur_label, 0)

        outer.addLayout(top_row)

        # --- Line 2: date (muted, elidable) ... status pill(s) ---
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(6)

        # The date is the only thing here allowed to shrink: it is the
        # longest field and the least precise, so it absorbs all the elision
        # pressure and keeps it off the pills beside it.
        if name:
            date_label = QLabel()
            date_label.setObjectName("recordingRowDate")
            date_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            meta_row.addWidget(date_label, 1)
            widget.register_elidable(date_label, date_str)
        else:
            meta_row.addStretch(1)

        # Checked live against disk rather than trusting metadata — a delete
        # can remove audio or transcript without the other, and the row needs
        # to reflect that on the very next refresh() with no cached state.
        audio_files = metadata.get("audio_files", {}) or {}
        has_audio = any(p and Path(p).exists() for p in audio_files.values())
        row_has_transcript = has_transcript(metadata)

        # Neither pill ever shrinks — all shrink pressure lands on the
        # elidable date label beside them, which is the only Ignored-policy
        # item on this line.
        if has_audio:
            self._add_status_pill(
                meta_row, "music-note", "#9397ab", "audio",
                "Audio recording available", "recordingBadgeAudio",
            )

        # In-progress wins over Transcribed: a re-transcribe of an already
        # transcribed recording would otherwise look like nothing was
        # happening, which is exactly the ambiguity this pill removes.
        if metadata.get("directory") in self._transcribing:
            self._add_status_pill(
                meta_row, "waveform", "#f9e2af", "transcribing",
                "Transcription in progress...", "recordingBadgeWorking",
            )
        elif row_has_transcript:
            self._add_status_pill(
                meta_row, "file-text", "#a6e3a1", "transcribed",
                "Transcript available", "recordingBadgeTranscribed",
            )

        # Peach rather than the in-progress yellow: waiting for a scheduled
        # run is a different state from being worked on right now, and the
        # two pills can appear on the same row after a re-queue.
        if batch_queue.is_queued(metadata):
            self._add_status_pill(
                meta_row, "hourglass", "#fab387", "queued",
                "Queued for batch transcription", "recordingBadgeQueued",
            )

        # Assigned tags badges
        tags = tag_manager.get_recording_tags(metadata)
        if tags:
            for t_name in tags[:2]:
                t_color = tag_manager.get_tag_color(t_name)
                qc = QColor(t_color)
                tag_badge = QLabel(t_name)
                tag_badge.setObjectName("recordingRowTag")
                tag_badge.setToolTip(f"Tag: {t_name}")
                tag_badge.setStyleSheet(
                    f"color: {t_color}; "
                    f"background-color: rgba({qc.red()}, {qc.green()}, {qc.blue()}, 0.18); "
                    f"border: 1px solid rgba({qc.red()}, {qc.green()}, {qc.blue()}, 0.45); "
                    f"border-radius: 3px; padding: 0px 4px; font-size: 11px; font-weight: 500;"
                )
                meta_row.addWidget(tag_badge, 0)
            if len(tags) > 2:
                more_tag_badge = QLabel(f"+{len(tags) - 2}")
                more_tag_badge.setToolTip(", ".join(tags[2:]))
                more_tag_badge.setStyleSheet("color: #75798c; font-size: 11px; padding: 0px 2px;")
                meta_row.addWidget(more_tag_badge, 0)

        outer.addLayout(meta_row)
        return widget

    @staticmethod
    def _add_status_pill(row_layout, icon_name, color, caption, tooltip, object_name):
        """A small icon + colored caption for a row's status line (audio /
        transcribed / transcribing / queued).

        The objectName lives on the icon label, not the container, so the
        badge-presence tests in test_recordings_list_badges.py keep finding
        it exactly as before.
        """
        container = QWidget()
        h = QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        icon_label = QLabel()
        icon_label.setPixmap(colored_pixmap(icon_name, color, 12))
        icon_label.setObjectName(object_name)
        icon_label.setToolTip(tooltip)
        h.addWidget(icon_label)

        text_label = QLabel(caption)
        text_label.setToolTip(tooltip)
        text_label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        h.addWidget(text_label)

        row_layout.addWidget(container, 0)

    def _on_item_double_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return
        if "recording_id" in data and "directory" not in data:
            # This is a search result
            self.search_result_selected.emit(data["recording_id"], data.get("start", 0.0))
        else:
            self.recording_selected.emit(data)

    def _show_context_menu(self, position):
        item = self.list_widget.itemAt(position)
        if not item:
            return

        selected_items = self.list_widget.selectedItems()
        metadata = item.data(Qt.ItemDataRole.UserRole)
        if not metadata:
            return

        menu = QMenu(self)

        if len(selected_items) > 1:
            # Multi-select context menu
            open_recordings = QAction("Open Recordings Folder", self)
            open_recordings.triggered.connect(
                lambda: self._open_folder(str(self.recordings_dir))
            )
            menu.addAction(open_recordings)

            menu.addSeparator()

            untranscribed = self._selected_untranscribed(selected_items)
            transcribe_action = QAction(f"Transcribe {len(untranscribed)} Recordings", self)
            transcribe_action.setEnabled(len(untranscribed) > 0)
            transcribe_action.triggered.connect(
                lambda: self.transcribe_selected_requested.emit(untranscribed)
            )
            menu.addAction(transcribe_action)

            transcribed = self._selected_transcribed(selected_items)
            export_action = QAction(f"Export {len(transcribed)} Transcripts", self)
            export_action.setEnabled(len(transcribed) > 0)
            export_action.triggered.connect(
                lambda: self.export_selected_requested.emit(transcribed)
            )
            menu.addAction(export_action)

            self._add_batch_queue_actions(
                menu, [i.data(Qt.ItemDataRole.UserRole) for i in selected_items])

            self._add_tag_actions(
                menu, [i.data(Qt.ItemDataRole.UserRole) for i in selected_items])

            menu.addSeparator()

            count = len(selected_items)
            delete_action = QAction(f"Delete {count} Recordings", self)
            delete_action.triggered.connect(
                lambda: self._delete_selected_recordings(selected_items)
            )
            menu.addAction(delete_action)
        else:
            # Single item context menu
            open_recordings = QAction("Open Recordings Folder", self)
            open_recordings.triggered.connect(
                lambda: self._open_folder(metadata["directory"])
            )
            menu.addAction(open_recordings)

            menu.addSeparator()

            view_action = QAction("View / Transcribe", self)
            view_action.triggered.connect(lambda: self.recording_selected.emit(metadata))
            menu.addAction(view_action)

            play_action = QAction("Play Audio", self)
            play_action.triggered.connect(lambda: self._play_audio(metadata))
            menu.addAction(play_action)

            self._add_batch_queue_actions(menu, [metadata])
            self._add_tag_actions(menu, [metadata])

            menu.addSeparator()

            delete_action = QAction("Delete Recording", self)
            delete_action.triggered.connect(lambda: self._delete_recording(metadata))
            menu.addAction(delete_action)

        menu.exec(self.list_widget.mapToGlobal(position))

    def _add_tag_actions(self, menu, metadatas):
        """Add tag assignment action(s).

        A single recording opens the "Tag this recording" dialog — it names
        the recording in its header, so it only makes sense for one at a
        time. A multi-selection keeps the checkable Tags submenu, which
        applies/removes a tag across every selected recording at once.
        """
        valid_metas = [m for m in metadatas if m and m.get("directory")]
        if not valid_metas:
            return

        if len(valid_metas) == 1:
            tag_action = QAction(
                QIcon(colored_pixmap("bookmark-simple", "#9397ab", 14)), "Tag...", self
            )
            tag_action.triggered.connect(lambda: self._open_tag_dialog(valid_metas[0]))
            menu.addAction(tag_action)
            return

        tags_menu = menu.addMenu(QIcon(colored_pixmap("bookmark-simple", "#9397ab", 14)), "Tags")
        all_tags = tag_manager.load_all_tags()

        for tag in all_tags:
            name = tag["name"]
            all_have = all(name in tag_manager.get_recording_tags(m) for m in valid_metas)
            action = QAction(name, self)
            action.setCheckable(True)
            action.setChecked(all_have)
            action.triggered.connect(
                lambda checked, n=name, have_all=all_have: self._toggle_tag_on_recordings(valid_metas, n, not have_all)
            )
            tags_menu.addAction(action)

        tags_menu.addSeparator()

        new_tag_action = QAction("+ New Tag...", self)
        new_tag_action.triggered.connect(lambda: self._prompt_new_tag_for_recordings(valid_metas))
        tags_menu.addAction(new_tag_action)

        manage_tags_action = QAction("Manage Tags...", self)
        manage_tags_action.triggered.connect(self.manage_tags_requested.emit)
        tags_menu.addAction(manage_tags_action)

    def _open_tag_dialog(self, metadata):
        from app.ui.tag_recording_dialog import TagRecordingDialog

        directory = metadata.get("directory")
        dialog = TagRecordingDialog(metadata, self.recordings_dir, parent=self)
        dialog.tags_changed.connect(
            lambda tags, d=directory: self.recording_tags_changed.emit(d, tags)
        )
        dialog.exec()
        self.refresh()

    def _toggle_tag_on_recordings(self, metadatas, tag_name, should_assign):
        for m in metadatas:
            d = m.get("directory")
            if not d:
                continue
            if should_assign:
                updated = tag_manager.add_tag_to_recording(d, tag_name)
            else:
                updated = tag_manager.remove_tag_from_recording(d, tag_name)
            m["tags"] = updated
            self.recording_tags_changed.emit(d, updated)
        self.refresh()

    def _prompt_new_tag_for_recordings(self, metadatas):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Tag", "Enter tag name:")
        if ok and name.strip():
            tag_name = name.strip()
            self._toggle_tag_on_recordings(metadatas, tag_name, True)

    def _add_batch_queue_actions(self, menu, metadatas):
        """Queue / unqueue entries for the scheduled batch transcription run.

        Both directions are always offered when they apply: a mixed
        selection can be pushed either way in one click rather than making
        the user work out which recordings are in which state.
        """
        unqueued, queued = partition_by_queue_state(metadatas)
        # A recording actively being transcribed will already have a
        # transcript by the time any batch run reaches it (and the tag gets
        # cleared automatically the moment that transcription finishes —
        # see MainWindow._display_final_transcript), so offering to queue it
        # now would only ever be a no-op at best.
        unqueued = [m for m in unqueued if m.get("directory") not in self._transcribing]
        if not unqueued and not queued:
            return

        menu.addSeparator()

        if unqueued:
            label = ("Queue for Batch Transcription" if len(unqueued) == 1
                     else f"Queue {len(unqueued)} for Batch Transcription")
            action = QAction(label, self)
            action.setToolTip(
                "Transcribe these on the next scheduled batch run instead of now"
            )
            action.triggered.connect(lambda: self._set_queued(unqueued, True))
            menu.addAction(action)

        if queued:
            label = ("Remove from Batch Queue" if len(queued) == 1
                     else f"Remove {len(queued)} from Batch Queue")
            action = QAction(label, self)
            action.triggered.connect(lambda: self._set_queued(queued, False))
            menu.addAction(action)

            run_batch_action = QAction("Process Batch Queue Now...", self)
            run_batch_action.triggered.connect(self.run_batch_requested.emit)
            menu.addAction(run_batch_action)

    def _set_queued(self, metadatas, queued):
        """Write the tag for each recording, then redraw the pills."""
        if queued:
            transcribed = [
                m for m in metadatas
                if m and m.get("directory") and (Path(m["directory"]) / "transcript.json").exists()
            ]
            if transcribed:
                if len(transcribed) == 1:
                    name = transcribed[0].get("name") or Path(transcribed[0]["directory"]).name
                    msg = (
                        f"The recording '{name}' already has a transcription.\n\n"
                        "Queueing it for batch transcription will re-transcribe it and "
                        "overwrite the existing transcript.\n\n"
                        "Do you want to continue?"
                    )
                else:
                    msg = (
                        f"{len(transcribed)} of the selected recordings already have a transcription.\n\n"
                        "Queueing them for batch transcription will re-transcribe them and "
                        "overwrite existing transcripts.\n\n"
                        "Do you want to continue?"
                    )
                reply = QMessageBox.question(
                    self,
                    "Overwrite Existing Transcription?",
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        failures = [m for m in metadatas
                    if not batch_queue.set_queued(m["directory"], queued)]
        self.refresh()
        if failures:
            QMessageBox.warning(
                self, "Batch Queue",
                f"Could not update {len(failures)} recording(s) — their "
                "metadata.json is missing or unreadable.",
            )

    def _is_safe_recording_path(self, target_path) -> bool:
        """Security check: ensure path is within self.recordings_dir."""
        try:
            target = Path(target_path).resolve()
            root = self.recordings_dir.resolve()
            return target.is_relative_to(root) and target != root
        except Exception:
            return False

    def _open_folder(self, directory):
        try:
            target = Path(directory).resolve()
            root = self.recordings_dir.resolve()
            if target == root or target.is_relative_to(root):
                if target.exists():
                    os.startfile(directory)
                return
        except Exception:
            pass
        logger.warning("Rejected request to open folder outside recordings directory: %s", directory)

    def _selected_untranscribed(self, items):
        result = []
        for item in items:
            metadata = item.data(Qt.ItemDataRole.UserRole)
            if not metadata or "directory" not in metadata:
                continue
            if not (Path(metadata["directory"]) / "transcript.json").exists():
                result.append(metadata)
        return result

    def _selected_transcribed(self, items):
        """Selected recordings that have transcript.json on disk. Drives
        Export/Transcribe, which need the source the export is built from —
        same check the row badge uses (see _build_row_widget)."""
        result = []
        for item in items:
            metadata = item.data(Qt.ItemDataRole.UserRole)
            if not metadata or "directory" not in metadata:
                continue
            if (Path(metadata["directory"]) / "transcript.json").exists():
                result.append(metadata)
        return result

    def _delete_recording(self, metadata):
        dialog = DeleteScopeDialog(count=1, parent=self)
        if not dialog.exec():
            return

        self._perform_delete(metadata, dialog.selected_scope())
        self.refresh()

    def _delete_selected_recordings(self, items):
        """Delete multiple selected recordings, all with the same scope."""
        recordings = []
        for item in items:
            meta = item.data(Qt.ItemDataRole.UserRole)
            if meta and "directory" in meta:
                recordings.append(meta)

        if not recordings:
            return

        dialog = DeleteScopeDialog(count=len(recordings), parent=self)
        if not dialog.exec():
            return
        scope = dialog.selected_scope()

        for meta in recordings:
            self._perform_delete(meta, scope)

        self.refresh()

    def _perform_delete(self, metadata, scope):
        """Delete a single recording's files per scope.

        Notifies listeners BEFORE touching audio so playback stops and any
        file handle is released before removal — DELETE_RECORDINGS and
        DELETE_BOTH are the only scopes that ever remove audio.

        "Recordings only" and "transcriptions only" are no longer symmetric
        with a folder rmtree: each removes just its own file set, and the
        session survives as a partial entry (transcript-only or, in
        principle, audio-only) unless removal leaves NEITHER a recording NOR
        a transcript behind — at that point there's nothing left the entry
        is for, so the folder itself is removed instead.
        """
        directory = metadata.get("directory", "")
        if not directory:
            return

        if not self._is_safe_recording_path(directory):
            logger.warning("Rejected delete operation for path outside recordings directory: %s", directory)
            QMessageBox.warning(self, "Security Error", "Cannot delete directory outside the recordings folder.")
            return

        if scope in (DELETE_RECORDINGS, DELETE_BOTH):
            self.about_to_delete.emit(directory)

        try:
            if scope == DELETE_BOTH:
                _rmtree_robust(directory)
                self.recording_deleted.emit(directory)
            elif scope == DELETE_RECORDINGS:
                _delete_audio_files(directory)
                if _has_any_transcript(directory):
                    self.recording_files_changed.emit(directory)
                else:
                    _rmtree_robust(directory)
                    self.recording_deleted.emit(directory)
            elif scope == DELETE_TRANSCRIPTIONS:
                _delete_transcription_files(directory)
                if _has_any_audio(directory):
                    self.recording_files_changed.emit(directory)
                else:
                    _rmtree_robust(directory)
                    self.recording_deleted.emit(directory)
        except Exception as e:
            logger.exception("Failed to delete recording files (%s): %s", scope, directory)
            QMessageBox.warning(self, "Error", f"Failed to delete: {e}")

    def _play_audio(self, metadata):
        audio_files = metadata.get("audio_files", {})
        audio_path = audio_files.get("combined") or audio_files.get("system") or audio_files.get("mic")
        if audio_path:
            try:
                target = Path(audio_path).resolve()
                root = self.recordings_dir.resolve()
                if target.is_relative_to(root) and target.is_file():
                    os.startfile(audio_path)
                    return
            except Exception as e:
                logger.warning("Could not play audio for %s: %s", audio_path, e)

    def _on_filter_changed(self, text):
        """Live, local filter of the visible recordings by name/date.

        Runs on every keystroke against rows already on screen — no worker
        thread, no transcript required — unlike _on_search below, which
        searches transcript *content* and only fires on Enter. If a
        transcript-search result set is currently showing, rebuild the
        normal recordings view first so there's something to filter.
        """
        if self._showing_search_results:
            self.refresh()

        self._filter_query = text.strip().lower()
        self._apply_filters()

    def _on_search_cleared(self):
        self._filter_query = ""
        self.refresh()

    def _on_chip_toggled(self, filter_name, checked):
        if not checked:
            return
        self._active_chip_filter = filter_name
        if self._showing_search_results:
            # Search-result rows carry a different UserRole shape (a hit
            # dict, not full metadata) — refresh() drops back to the normal
            # browsing view first, same guard _on_filter_changed uses.
            self.refresh()
        else:
            self._apply_filters()

    def _passes_chip_filter(self, metadata):
        if self._active_chip_filter == "untranscribed":
            return not has_transcript(metadata)
        if self._active_chip_filter == "tagged":
            return bool(tag_manager.get_recording_tags(metadata))
        return True

    def _update_filter_chip_counts(self):
        untranscribed = sum(1 for m in self._recordings if not has_transcript(m))
        tagged = sum(1 for m in self._recordings if tag_manager.get_recording_tags(m))
        self.chip_all.setText(f"All {len(self._recordings)}")
        self.chip_untranscribed.setText(f"Untranscribed {untranscribed}")
        self.chip_tagged.setText(f"Tagged {tagged}")

    def _apply_filters(self):
        """Combine the live text filter and the selected chip into one
        visibility pass over rows already built by refresh() — mirrors the
        pre-existing text-only filter's hide/show approach rather than
        rebuilding, so both filters can be active together.
        """
        if not hasattr(self, "list_widget"):
            # chip_all.setChecked(True) in _setup_ui() fires this via the
            # toggled signal before list_widget is constructed.
            return

        visible_count = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            metadata = item.data(Qt.ItemDataRole.UserRole) or {}
            tags_str = " ".join(tag_manager.get_recording_tags(metadata))
            haystack = f"{metadata.get('name', '')} {metadata.get('started_at', '')} {tags_str}".lower()
            match = self._filter_query in haystack and self._passes_chip_filter(metadata)
            item.setHidden(not match)
            visible_count += match

        if visible_count:
            self._set_empty_message("")
        elif self._filter_query:
            self._set_empty_message(f'No recordings match "{self._filter_query}"')
        elif self._active_chip_filter == "untranscribed":
            self._set_empty_message("No untranscribed recordings")
        elif self._active_chip_filter == "tagged":
            self._set_empty_message("No tagged recordings")
        else:
            self._set_empty_message("")

    def _on_search(self, query, is_semantic):
        # Only the latest query matters; typing while a search runs replaces
        # the pending one, and the runner picks it up when the worker frees.
        self._pending_search = (query, is_semantic)
        self._maybe_start_search()

    def _maybe_start_search(self):
        if self._search_worker is not None and self._search_worker.isRunning():
            return
        if self._pending_search is None:
            return
        query, is_semantic = self._pending_search
        self._pending_search = None
        self._search_worker = _SearchWorker(self.recordings_dir, query, is_semantic)
        self._search_worker.results_ready.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_finished(self, results):
        self._show_search_results(results)
        self._maybe_start_search()

    def _show_search_results(self, results):
        self._showing_search_results = True
        self.list_widget.clear()
        for result in results[:50]:
            rec_id = result["recording_id"]
            speaker = result.get("speaker", "")
            text = result["text"]
            display = f"{rec_id}\n"
            if speaker:
                display += f"  [{speaker}] "
            display += text[:80]
            if len(text) > 80:
                display += "..."
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.list_widget.addItem(item)

        self._set_empty_message("No transcripts match that search." if not results else "")

    def _format_duration(self, seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        elif m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    def transcribe_selected(self):
        """Emit transcribe_selected_requested for the current list selection's
        untranscribed recordings. Menu-bar entry point mirroring the
        multi-select context menu action."""
        selected_items = self.list_widget.selectedItems()
        untranscribed = self._selected_untranscribed(selected_items)
        if untranscribed:
            self.transcribe_selected_requested.emit(untranscribed)

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Recording", "",
            "Audio Files (*.wav *.mp3 *.m4a)"
        )
        if path:
            self.import_requested.emit(path)
