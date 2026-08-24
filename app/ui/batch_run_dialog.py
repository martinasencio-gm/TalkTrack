"""Modal dialog for launching batch transcription on-demand from the app."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSpinBox, QVBoxLayout
)

MODE_IN_APP = "in_app"
MODE_DETACHED = "detached"


class BatchRunDialog(QDialog):
    """Dialog allowing the user to configure and launch a batch run."""

    def __init__(self, queued_count=0, config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Run Batch Transcription")
        self.setMinimumWidth(450)
        self._queued_count = queued_count
        self._config = config

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Summary box
        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            "background-color: rgba(203, 166, 247, 0.08);"
            "border: 1px solid rgba(203, 166, 247, 0.25);"
            "border-radius: 8px; padding: 8px;"
        )
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(4)

        if self._queued_count > 0:
            count_label = QLabel(
                f"<b>{self._queued_count} recording{'s' if self._queued_count != 1 else ''}</b> "
                f"queued for batch processing."
            )
            count_label.setStyleSheet("font-size: 13px; color: #e9e9ed;")
            summary_layout.addWidget(count_label)

            info_label = QLabel("Recordings will be transcribed sequentially in the background.")
            info_label.setStyleSheet("font-size: 11px; color: #9397ab;")
            info_label.setWordWrap(True)
            summary_layout.addWidget(info_label)
        else:
            no_queue_label = QLabel("<b>No recordings are currently queued for batch processing.</b>")
            no_queue_label.setStyleSheet("font-size: 13px; color: #fab387;")
            summary_layout.addWidget(no_queue_label)

            hint_label = QLabel(
                "To queue recordings, right-click any recording in the list and select "
                "<b>Queue for Batch Transcription</b>, or enable automatic queueing in "
                "Settings > General."
            )
            hint_label.setStyleSheet("font-size: 11px; color: #9397ab;")
            hint_label.setWordWrap(True)
            summary_layout.addWidget(hint_label)

        layout.addWidget(summary_frame)

        if self._queued_count > 0:
            # Execution mode group
            mode_group = QGroupBox("Execution Mode")
            mode_layout = QVBoxLayout(mode_group)

            self._in_app_radio = QRadioButton("Process inside app (Recommended)")
            self._in_app_radio.setToolTip(
                "Runs in the background inside TalkTrack with live status updates, "
                "progress indication, and memory model caching."
            )
            self._in_app_radio.setChecked(True)
            mode_layout.addWidget(self._in_app_radio)

            in_app_desc = QLabel("Shows live progress in status bar; pauses if TalkTrack is closed.")
            in_app_desc.setStyleSheet("font-size: 10px; color: #9397ab; margin-left: 20px;")
            mode_layout.addWidget(in_app_desc)

            self._detached_radio = QRadioButton("Run as independent background process")
            self._detached_radio.setToolTip(
                "Spawns an isolated background process (pythonw.exe) that will continue "
                "running even if TalkTrack is closed. Output is written to the batch log."
            )
            mode_layout.addWidget(self._detached_radio)

            detached_desc = QLabel(
                "Continues running even if TalkTrack is closed. "
                "Logs to Documents\\TalkTrack\\batch Log."
            )
            detached_desc.setStyleSheet("font-size: 10px; color: #9397ab; margin-left: 20px;")
            mode_layout.addWidget(detached_desc)

            self._mode_btn_group = QButtonGroup(self)
            self._mode_btn_group.addButton(self._in_app_radio)
            self._mode_btn_group.addButton(self._detached_radio)

            layout.addWidget(mode_group)

            # Diarization options
            diarize_group = QGroupBox("Diarization (Speaker Identification)")
            diarize_layout = QVBoxLayout(diarize_group)

            self._diarize_cb = QCheckBox("Identify individual speakers (pyannote.audio)")
            hf_token = ""
            diarize_config_enabled = False
            if self._config is not None:
                hf_token = self._config.get("diarization", "hf_token") or ""
                diarize_config_enabled = bool(self._config.get("diarization", "enabled"))

            if not hf_token:
                self._diarize_cb.setEnabled(False)
                self._diarize_cb.setChecked(False)
                self._diarize_cb.setToolTip("Requires HuggingFace token in Settings > Diarization")
                no_token_label = QLabel(
                    "<i>Diarization is disabled because no HuggingFace token is configured. "
                    "Per-track speaker labels ('You' / 'Remote') will still be used when available.</i>"
                )
                no_token_label.setStyleSheet("font-size: 10px; color: #fab387;")
                no_token_label.setWordWrap(True)
                diarize_layout.addWidget(self._diarize_cb)
                diarize_layout.addWidget(no_token_label)
            else:
                self._diarize_cb.setChecked(diarize_config_enabled)
                diarize_layout.addWidget(self._diarize_cb)

            layout.addWidget(diarize_group)

            # Limit group
            limit_group = QGroupBox("Worklist Scope")
            limit_layout = QVBoxLayout(limit_group)

            self._all_radio = QRadioButton(f"Process all queued recordings ({self._queued_count})")
            self._all_radio.setChecked(True)
            limit_layout.addWidget(self._all_radio)

            limit_row = QHBoxLayout()
            self._limit_radio = QRadioButton("Process at most:")
            limit_row.addWidget(self._limit_radio)

            self._limit_spinbox = QSpinBox()
            self._limit_spinbox.setRange(1, max(1, self._queued_count))
            self._limit_spinbox.setValue(min(self._queued_count, 5))
            self._limit_spinbox.setEnabled(False)
            limit_row.addWidget(self._limit_spinbox)
            limit_row.addWidget(QLabel("recording(s)"))
            limit_row.addStretch()

            self._limit_radio.toggled.connect(self._limit_spinbox.setEnabled)

            limit_layout.addLayout(limit_row)

            self._scope_btn_group = QButtonGroup(self)
            self._scope_btn_group.addButton(self._all_radio)
            self._scope_btn_group.addButton(self._limit_radio)

            layout.addWidget(limit_group)

        # Dialog Buttons
        bottom_row = QHBoxLayout()
        self.view_logs_btn = QPushButton("View Logs...")
        self.view_logs_btn.setToolTip("Open the batch process logs folder for review")
        self.view_logs_btn.clicked.connect(self._open_logs)
        bottom_row.addWidget(self.view_logs_btn)
        bottom_row.addStretch()

        buttons = QDialogButtonBox()
        if self._queued_count > 0:
            start_btn = buttons.addButton("Start Batch Run", QDialogButtonBox.ButtonRole.AcceptRole)
            start_btn.setDefault(True)
            buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        else:
            buttons.addButton(QDialogButtonBox.StandardButton.Close)

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom_row.addWidget(buttons)
        layout.addLayout(bottom_row)

    def _open_logs(self):
        from app.batch.logging_setup import open_batch_log
        open_batch_log()

    def execution_mode(self):
        """Return MODE_IN_APP or MODE_DETACHED."""
        if hasattr(self, "_detached_radio") and self._detached_radio.isChecked():
            return MODE_DETACHED
        return MODE_IN_APP

    def diarize_enabled(self):
        """Return True/False for diarization override."""
        if hasattr(self, "_diarize_cb") and self._diarize_cb.isEnabled():
            return self._diarize_cb.isChecked()
        return False

    def limit(self):
        """Return maximum recordings limit, or None for all."""
        if hasattr(self, "_limit_radio") and self._limit_radio.isChecked():
            return self._limit_spinbox.value()
        return None
