"""Dialog showing information about active batch transcription processes."""
from typing import List, Union
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QMessageBox, QWidget, QScrollArea
)

from app.batch.logging_setup import open_batch_log
from app.batch.process_monitor import BatchProcessInfo, terminate_batch_process
from app.utils.icons import colored_pixmap
from app.ui import tokens


class BatchProcessInfoDialog(QDialog):
    """Dialog displaying details of running batch process(es) with termination controls."""

    process_terminated = pyqtSignal(int)  # PID

    def __init__(self, process_info: Union[BatchProcessInfo, List[BatchProcessInfo]],
                 in_app_worker=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        if isinstance(process_info, list):
            self.processes = list(process_info)
        else:
            self.processes = [process_info]
        self._in_app_worker = in_app_worker
        self._runtime_labels = {}  # pid -> QLabel
        self._process_cards = {}   # pid -> QWidget

        self.setWindowTitle("Batch Process Status")
        self.setMinimumWidth(480)
        self.setMaximumWidth(600)

        self._setup_ui()

        # Timer to update elapsed time every second while open
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._update_runtime)
        self._tick_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        from app.utils.screen_utils import center_on_active_screen
        center_on_active_screen(self, self.parent())

    @property
    def process_info(self) -> BatchProcessInfo:
        return self.processes[0] if self.processes else None

    def _setup_ui(self):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setSpacing(12)

        # Header card
        self._header_card = QFrame()
        self._header_card.setStyleSheet(
            "background-color: rgba(203, 166, 247, 0.08);"
            "border: 1px solid rgba(203, 166, 247, 0.25);"
            "border-radius: 8px; padding: 10px;"
        )
        header_layout = QVBoxLayout(self._header_card)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(6)

        title_row = QHBoxLayout()
        count = len(self.processes)
        title_text = "<b>Batch Transcription Running</b>" if count <= 1 else f"<b>{count} Batch Jobs Running</b>"
        self._title_label = QLabel(title_text)
        self._title_label.setStyleSheet(f"font-size: {tokens.TYPE_BASE}; color: {tokens.TEXT};")
        title_row.addWidget(self._title_label)
        title_row.addStretch()

        status_pill = QWidget()
        status_pill.setStyleSheet(
            "background-color: rgba(166, 227, 161, 0.15);"
            "border-radius: 6px;"
        )
        status_pill_layout = QHBoxLayout(status_pill)
        status_pill_layout.setContentsMargins(8, 2, 8, 2)
        status_pill_layout.setSpacing(4)
        status_icon = QLabel()
        status_icon.setPixmap(colored_pixmap("check-circle-fill", tokens.GREEN, 11))
        status_text = QLabel("Active")
        status_text.setStyleSheet(f"color: {tokens.GREEN}; font-weight: 600; font-size: {tokens.TYPE_SM};")
        status_pill_layout.addWidget(status_icon)
        status_pill_layout.addWidget(status_text)
        title_row.addWidget(status_pill)
        header_layout.addLayout(title_row)

        desc_text = (
            "A batch transcription job is currently executing in the background."
            if count <= 1 else
            f"{count} batch transcription processes are currently executing in the background."
        )
        self._desc_label = QLabel(desc_text)
        self._desc_label.setStyleSheet(f"font-size: {tokens.TYPE_SM}; color: {tokens.TEXT_MUTED};")
        header_layout.addWidget(self._desc_label)

        self._main_layout.addWidget(self._header_card)

        # Process list / cards container
        if len(self.processes) > 2:
            self._scroll_area = QScrollArea()
            self._scroll_area.setWidgetResizable(True)
            self._scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
            self._cards_container = QWidget()
            self._cards_layout = QVBoxLayout(self._cards_container)
            self._cards_layout.setContentsMargins(0, 0, 0, 0)
            self._cards_layout.setSpacing(10)
            self._scroll_area.setWidget(self._cards_container)
            self._main_layout.addWidget(self._scroll_area, 1)
        else:
            self._scroll_area = None
            self._cards_container = QWidget()
            self._cards_layout = QVBoxLayout(self._cards_container)
            self._cards_layout.setContentsMargins(0, 0, 0, 0)
            self._cards_layout.setSpacing(10)
            self._main_layout.addWidget(self._cards_container, 1)

        for proc in self.processes:
            card = self._build_process_card(proc)
            self._process_cards[proc.pid] = card
            self._cards_layout.addWidget(card)

        # For test compatibility (references first process)
        if self.processes:
            first_proc = self.processes[0]
            if first_proc.pid in self._process_fields:
                f = self._process_fields[first_proc.pid]
                self._pid_label = f.get("pid")
                self._type_label = f.get("type")
                self._started_label = f.get("started")
                self._runtime_label = f.get("runtime")
                self._args_label = f.get("args")

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        if len(self.processes) == 1:
            self.end_btn = QPushButton("End Process")
            self.end_btn.setStyleSheet(
                f"QPushButton {{ background-color: {tokens.DANGER_BG}; color: {tokens.RED}; "
                f"border: 1px solid {tokens.DANGER_BORDER}; border-radius: 6px; padding: 6px 14px; font-weight: bold; }}"
                f"QPushButton:hover {{ background-color: {tokens.DANGER_HOVER}; color: {tokens.ON_DANGER}; }}"
            )
            self.end_btn.setToolTip("Terminate the running batch transcription process")
            self.end_btn.clicked.connect(lambda: self._on_end_clicked_proc(self.processes[0]))
            btn_layout.addWidget(self.end_btn)
        else:
            self.end_all_btn = QPushButton("End All Processes")
            self.end_all_btn.setStyleSheet(
                f"QPushButton {{ background-color: {tokens.DANGER_BG}; color: {tokens.RED}; "
                f"border: 1px solid {tokens.DANGER_BORDER}; border-radius: 6px; padding: 6px 14px; font-weight: bold; }}"
                f"QPushButton:hover {{ background-color: {tokens.DANGER_HOVER}; color: {tokens.ON_DANGER}; }}"
            )
            self.end_all_btn.setToolTip("Terminate all running batch transcription processes")
            self.end_all_btn.clicked.connect(self._on_end_all_clicked)
            btn_layout.addWidget(self.end_all_btn)
            self.end_btn = self.end_all_btn

        self.logs_btn = QPushButton("Open Batch Log")
        self.logs_btn.setToolTip("Open the batch process log file or folder for review")
        self.logs_btn.clicked.connect(self._on_open_log_clicked)
        btn_layout.addWidget(self.logs_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        self._main_layout.addLayout(btn_layout)

    def _build_process_card(self, proc: BatchProcessInfo) -> QWidget:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {tokens.SURFACE_5}; border: 1px solid {tokens.SURFACE_1}; border-radius: 8px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(8)

        if not hasattr(self, "_process_fields"):
            self._process_fields = {}
        self._process_fields[proc.pid] = {}

        # If multiple processes, show a header for each card
        if len(self.processes) > 1:
            card_header = QHBoxLayout()
            card_title = QLabel(f"<b>PID {proc.pid}</b> — {proc.process_type_label}")
            card_title.setStyleSheet(f"font-size: {tokens.TYPE_BASE}; color: {tokens.PEACH};")
            card_header.addWidget(card_title)
            card_header.addStretch()

            end_card_btn = QPushButton("End Process")
            end_card_btn.setStyleSheet(
                f"QPushButton {{ background-color: {tokens.DANGER_PRESSED_BG}; color: {tokens.RED}; "
                f"border: 1px solid {tokens.DANGER_PRESSED_BORDER}; border-radius: 4px; padding: 3px 10px; font-size: {tokens.TYPE_SM}; font-weight: bold; }}"
                f"QPushButton:hover {{ background-color: {tokens.DANGER_PRESSED_BORDER}; color: {tokens.ON_DANGER}; }}"
            )
            end_card_btn.clicked.connect(lambda checked, p=proc: self._on_end_clicked_proc(p))
            card_header.addWidget(end_card_btn)
            card_layout.addLayout(card_header)

        def add_field(label_text: str, value_text: str) -> QLabel:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {tokens.TEXT_MUTED}; font-size: {tokens.TYPE_MD}; font-weight: 500;")
            lbl.setFixedWidth(120)
            val = QLabel(value_text)
            val.setStyleSheet(f"color: {tokens.TEXT}; font-size: {tokens.TYPE_MD}; font-weight: bold;")
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            card_layout.addLayout(row)
            return val

        self._process_fields[proc.pid]["pid"] = add_field("Process ID (PID):", str(proc.pid))
        self._process_fields[proc.pid]["type"] = add_field("Execution Type:", proc.process_type_label)
        self._process_fields[proc.pid]["started"] = add_field("Started At:", proc.formatted_start_time)
        runtime_lbl = add_field("Time Running:", proc.formatted_duration)
        self._process_fields[proc.pid]["runtime"] = runtime_lbl
        self._runtime_labels[proc.pid] = runtime_lbl
        self._process_fields[proc.pid]["args"] = add_field("Arguments:", proc.arguments_summary)

        return card

    def _update_runtime(self):
        for proc in self.processes:
            if proc.pid in self._runtime_labels:
                self._runtime_labels[proc.pid].setText(proc.formatted_duration)

    def _on_open_log_clicked(self):
        open_batch_log()

    def _on_end_clicked(self):
        # Backward-compatible handler for single process
        if self.processes:
            self._on_end_clicked_proc(self.processes[0])

    def _on_end_clicked_proc(self, proc: BatchProcessInfo):
        confirm = QMessageBox.question(
            self,
            "End Batch Process",
            f"Are you sure you want to end batch process (PID {proc.pid})?\n\n"
            "Any recording currently being transcribed will be stopped.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        ok = terminate_batch_process(proc.pid, in_app_worker=self._in_app_worker)
        if ok:
            self.process_terminated.emit(proc.pid)
            if proc in self.processes:
                self.processes.remove(proc)
            if proc.pid in self._process_cards:
                self._process_cards[proc.pid].deleteLater()
                del self._process_cards[proc.pid]

            if not self.processes:
                self._tick_timer.stop()
                QMessageBox.information(
                    self,
                    "Batch Process Ended",
                    f"Batch process (PID {proc.pid}) has been ended.",
                )
                self.accept()
            else:
                count = len(self.processes)
                title_text = "<b>Batch Transcription Running</b>" if count == 1 else f"<b>{count} Batch Jobs Running</b>"
                self._title_label.setText(title_text)
                self._desc_label.setText(f"{count} batch transcription process{'es are' if count > 1 else ' is'} currently executing in the background.")
        else:
            QMessageBox.warning(
                self,
                "Error Ending Process",
                f"Could not terminate process (PID {proc.pid}). It may have already exited.",
            )

    def _on_end_all_clicked(self):
        count = len(self.processes)
        confirm = QMessageBox.question(
            self,
            "End All Batch Processes",
            f"Are you sure you want to end all {count} running batch transcription processes?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        for proc in list(self.processes):
            if terminate_batch_process(proc.pid, in_app_worker=self._in_app_worker):
                self.process_terminated.emit(proc.pid)
        self._tick_timer.stop()
        self.accept()
