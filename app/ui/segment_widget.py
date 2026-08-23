"""Individual transcript segment row widget."""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton, QMenu, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QPointF
from PyQt6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPolygonF, QPen, QFont

from app.utils.icons import icon_path


class AvatarWidget(QWidget):
    def __init__(self, initial, color, parent=None):
        super().__init__(parent)
        self.initial = initial[:1].upper() if initial else "?"
        self.color = color
        self.setFixedSize(24, 24)

    def set_initial(self, initial):
        self.initial = initial[:1].upper() if initial else "?"
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        pen = QPen(QColor(self.color))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawEllipse(1, 1, 22, 22)
        
        painter.setPen(QColor(self.color))
        font = QFont("Inter", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.initial)
        painter.end()


def _format_time(seconds):
    """Format seconds as HH:MM:SS."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _display_speaker(speaker_id, speaker_names):
    """Return display name for a speaker, falling back to ID."""
    if not speaker_id:
        return ""
    if speaker_names and speaker_id in speaker_names and speaker_names[speaker_id]:
        return speaker_names[speaker_id]
    return speaker_id


class EditHistory:
    """Undo/redo stack for segment text edits."""
    def __init__(self, initial_text: str, max_depth: int = 20):
        self._stack = [initial_text]
        self._pos = 0
        self._max_depth = max_depth

    def current(self) -> str: return self._stack[self._pos]
    def original(self) -> str: return self._stack[0]
    def is_modified(self) -> bool: return self._pos > 0
    def can_undo(self) -> bool: return self._pos > 0
    def can_redo(self) -> bool: return self._pos < len(self._stack) - 1

    def push(self, text: str):
        self._stack = self._stack[:self._pos + 1]
        self._stack.append(text)
        self._pos += 1
        if len(self._stack) > self._max_depth + 1:
            trim = len(self._stack) - self._max_depth - 1
            self._stack = self._stack[trim:]
            self._pos -= trim

    def undo(self) -> str:
        if self.can_undo(): self._pos -= 1
        return self.current()

    def redo(self) -> str:
        if self.can_redo(): self._pos += 1
        return self.current()


class SegmentWidget(QWidget):
    """A single transcript segment row.
    New stacked layout:
    [Avatar]  [Name] [Timestamp]         [Play]
              [Text                         ]
    """
    play_requested = pyqtSignal(int)
    stop_requested = pyqtSignal()
    text_edited = pyqtSignal(int, str)
    text_reverted = pyqtSignal(int)
    speaker_clicked = pyqtSignal(str)

    def __init__(self, index, segment, speaker_color="#cdd6f4",
                 speaker_name="", has_audio=True, parent=None):
        super().__init__(parent)
        self._index = index
        self._segment = segment
        self._speaker_color = speaker_color
        self._speaker_name = speaker_name
        self._has_audio = bool(has_audio)
        self._editing = False
        self._playing = False
        self._history = EditHistory(segment.text)
        self._setup_ui()

    def _setup_ui(self):
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(16, 13, 16, 13)
        self.main_layout.setSpacing(12)
        
        # Avatar
        display_name = self._speaker_name or self._segment.speaker or "?"
        self.avatar = AvatarWidget(display_name, self._speaker_color)
        self.main_layout.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        
        # Right column
        self.right_col = QVBoxLayout()
        self.right_col.setSpacing(4)
        
        # Line 1: Name + Timestamp + Play
        self.meta_row = QHBoxLayout()
        
        self.speaker_label = QLabel(display_name)
        self.speaker_label.setStyleSheet(f"color: {self._speaker_color}; font-size: 12.5px; font-weight: 600;")
        self.speaker_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speaker_label.mousePressEvent = self._on_speaker_clicked
        
        start_ts = _format_time(self._segment.start)
        self.timestamp_label = QLabel(start_ts)
        self.timestamp_label.setStyleSheet("color: #595d6c; font-family: Consolas; font-size: 10.5px;")
        
        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(20, 20)
        self.play_btn.setStyleSheet("border: none; background: transparent;")
        # Set icon later when needed, placeholder icon for now:
        self.play_btn.setIcon(QIcon(str(icon_path("play"))))
        self.play_btn.clicked.connect(self._on_play_clicked)
        self.play_btn.setVisible(self._has_audio)
        
        self.edit_affordance = QPushButton()
        self.edit_affordance.setIcon(QIcon(str(icon_path("pencil-simple"))))
        self.edit_affordance.setFixedSize(20, 20)
        self.edit_affordance.setStyleSheet("border: none; background: transparent;")
        self.edit_affordance.clicked.connect(self._start_edit)
        self.edit_affordance.hide()
        
        self.meta_row.addWidget(self.speaker_label)
        self.meta_row.addWidget(self.timestamp_label)
        self.meta_row.addStretch()
        self.meta_row.addWidget(self.edit_affordance)
        self.meta_row.addWidget(self.play_btn)
        
        self.right_col.addLayout(self.meta_row)
        
        # Line 2: Text
        self.text_label = QLabel(self._segment.text)
        self.text_label.setStyleSheet("color: #e4e7f5; font-size: 14px; line-height: 1.6;")
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.text_label.mouseDoubleClickEvent = self._on_text_double_clicked
        self.text_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_label.customContextMenuRequested.connect(self._show_context_menu)
        self.right_col.addWidget(self.text_label)
        
        self.text_edit = QLineEdit()
        self.text_edit.hide()
        self.text_edit.returnPressed.connect(self._finish_edit)
        self.right_col.addWidget(self.text_edit)
        
        self.main_layout.addLayout(self.right_col, stretch=1)

    def enterEvent(self, event):
        self.edit_affordance.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.edit_affordance.hide()
        super().leaveEvent(event)

    def update_speaker(self, speaker_names):
        display = _display_speaker(self._segment.speaker, speaker_names)
        if display:
            self.speaker_label.setText(display)
            self.avatar.set_initial(display)
        else:
            self.speaker_label.setText("")
            self.avatar.set_initial("?")

    def set_has_audio(self, has_audio: bool):
        self._has_audio = bool(has_audio)
        if not self._has_audio and self._playing:
            self.set_playing(False)
        self.play_btn.setVisible(self._has_audio)

    def set_playing(self, playing):
        self._playing = playing
        if playing:
            self.play_btn.setIcon(QIcon(str(icon_path("stop"))))
        else:
            self.play_btn.setIcon(QIcon(str(icon_path("play"))))

    def _on_play_clicked(self):
        if self._playing:
            self.stop_requested.emit()
        else:
            self.play_requested.emit(self._index)

    def _on_speaker_clicked(self, event):
        if self._segment.speaker:
            self.speaker_clicked.emit(self._segment.speaker)

    def _on_text_double_clicked(self, event):
        self._start_edit()

    def _start_edit(self):
        if self._editing: return
        self._editing = True
        self.text_edit.setText(self.text_label.text())
        self.text_label.hide()
        self.text_edit.show()
        self.text_edit.setFocus()
        self.text_edit.selectAll()

    def _finish_edit(self):
        if not self._editing: return
        self._editing = False
        new_text = self.text_edit.text().strip()
        if new_text and new_text != self._history.current():
            self._history.push(new_text)
            self.text_label.setText(new_text)
            self.text_edited.emit(self._index, new_text)
        self.text_edit.hide()
        self.text_label.show()

    def cancel_edit(self):
        if not self._editing: return
        self._editing = False
        self.text_edit.hide()
        self.text_label.show()

    def undo(self):
        if self._history.can_undo():
            text = self._history.undo()
            self.text_label.setText(text)
            self.text_edited.emit(self._index, text)

    def redo(self):
        if self._history.can_redo():
            text = self._history.redo()
            self.text_label.setText(text)
            self.text_edited.emit(self._index, text)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        edit_action = QAction("Edit Text", self)
        edit_action.triggered.connect(self._start_edit)
        menu.addAction(edit_action)

        if self._history.can_undo():
            undo_action = QAction("Undo", self)
            undo_action.triggered.connect(self.undo)
            menu.addAction(undo_action)

        if self._history.can_redo():
            redo_action = QAction("Redo", self)
            redo_action.triggered.connect(self.redo)
            menu.addAction(redo_action)

        if self._history.is_modified():
            revert_action = QAction("Revert to Original", self)
            revert_action.triggered.connect(self._revert_to_original)
            menu.addAction(revert_action)

        menu.exec(self.text_label.mapToGlobal(pos))

    def _revert_to_original(self):
        original = self._history.original()
        self._history = EditHistory(original)
        self.text_label.setText(original)
        self.text_reverted.emit(self._index)

    def highlight_match(self, start, end):
        self.setStyleSheet("background-color: rgba(145,132,217,0.12);")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self.setStyleSheet(""))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._editing:
            self.cancel_edit()
        else:
            super().keyPressEvent(event)
