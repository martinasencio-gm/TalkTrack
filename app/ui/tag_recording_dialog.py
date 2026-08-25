"""Modal dialog for tagging a single recording.

Shows the tags already on this recording (removable chips), a same-name
suggestion shortcut sourced from tag_manager.find_tags_for_recording_name
(recurring meetings tend to get the same tags every time), and a
filterable grid of every known tag sorted by how often it's used. Every
tap writes through tag_manager immediately — there is no separate save
step, matching the "Saved as you tap" footer hint.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFrame, QScrollArea, QWidget, QLayout, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint, QSize
from PyQt6.QtGui import QCursor, QColor

from app.utils import tag_manager
from app.utils.icons import colored_pixmap


class _FlowLayout(QLayout):
    """Left-to-right, top-to-bottom wrapping layout (Qt has no built-in one).

    Used for both the assigned-tag chips and the all-tags grid, whose pills
    are all different widths — a fixed-column grid would leave ragged gaps.
    """

    def __init__(self, parent=None, margin=0, hspacing=8, vspacing=8):
        super().__init__(parent)
        self._hspacing = hspacing
        self._vspacing = vspacing
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = effective.x(), effective.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._hspacing
            if next_x - self._hspacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._vspacing
                next_x = x + hint.width() + self._hspacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class _AssignedChip(QFrame):
    """Removable chip for a tag already on this recording."""

    remove_clicked = pyqtSignal(str)

    def __init__(self, name, color, parent=None):
        super().__init__(parent)
        self.setObjectName("assignedTagChip")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(6)

        label = QLabel(name)
        label.setStyleSheet("color: #e9e9ed; font-size: 12px; font-weight: 500;")
        layout.addWidget(label)

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(16, 16)
        remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        remove_btn.setStyleSheet(
            "QPushButton { color: #9397ab; background: transparent; border: none; "
            "font-size: 13px; font-weight: bold; padding: 0; } "
            "QPushButton:hover { color: #f38ba8; }"
        )
        remove_btn.clicked.connect(lambda: self.remove_clicked.emit(name))
        layout.addWidget(remove_btn)

        qc = QColor(color)
        self.setStyleSheet(
            f"QFrame#assignedTagChip {{ background-color: rgba({qc.red()}, {qc.green()}, {qc.blue()}, 0.30); "
            f"border: 1px solid {color}; border-radius: 14px; }}"
        )


class _AllTagPill(QFrame):
    """Toggleable pill in the ALL TAGS grid: checkmark + name + usage count."""

    clicked = pyqtSignal(str)

    def __init__(self, name, count, selected, parent=None):
        super().__init__(parent)
        self.tag_name = name
        self._selected = selected
        self.setObjectName("allTagPill")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        self._check_label = QLabel("✓")
        self._check_label.setStyleSheet("color: #9184d9; font-size: 11px; font-weight: bold;")
        self._check_label.setVisible(selected)
        layout.addWidget(self._check_label)

        self._name_label = QLabel(name)
        layout.addWidget(self._name_label)

        layout.addSpacing(10)

        self._count_label = QLabel(str(count))
        self._count_label.setStyleSheet("color: #75798c; font-size: 11px;")
        layout.addWidget(self._count_label)

        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_selected(not self._selected)
            self.clicked.emit(self.tag_name)
        super().mousePressEvent(event)

    def set_selected(self, selected):
        self._selected = selected
        self._check_label.setVisible(selected)
        self._apply_style()

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet(
                "QFrame#allTagPill { background-color: rgba(145,132,217,0.18); "
                "border: 1px solid #9184d9; border-radius: 8px; }"
            )
            self._name_label.setStyleSheet("color: #9184d9; font-size: 12.5px; font-weight: 600;")
        else:
            self.setStyleSheet(
                "QFrame#allTagPill { background-color: transparent; "
                "border: 1px solid rgba(233,233,237,0.16); border-radius: 8px; }"
                "QFrame#allTagPill:hover { background-color: rgba(233,233,237,0.07); }"
            )
            self._name_label.setStyleSheet("color: #e9e9ed; font-size: 12.5px; font-weight: 500;")


def _format_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class TagRecordingDialog(QDialog):
    """"Tag this recording" modal: assigned chips, a same-name suggestion,
    and a filterable most-used-first grid of every tag."""

    tags_changed = pyqtSignal(list)  # full assigned-tag list after any change

    def __init__(self, metadata, recordings_dir, tags_file=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.metadata = metadata
        self.recordings_dir = recordings_dir
        self.tags_file = tags_file
        self._session_dir = metadata.get("directory")
        self._assigned = list(tag_manager.get_recording_tags(metadata))
        self._suggested_tags = []

        self.setWindowTitle("Tag this recording")
        self.setMinimumSize(420, 480)
        self.resize(460, 580)
        self._setup_ui()
        self._refresh_all()

    def showEvent(self, event):
        super().showEvent(event)
        from app.utils.screen_utils import center_on_active_screen
        center_on_active_screen(self, self.parent())

    def _setup_ui(self):
        self.setStyleSheet("QDialog { background-color: #161826; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(colored_pixmap("bookmark-simple", "#9184d9", 18))
        header.addWidget(icon)
        title = QLabel("Tag this recording")
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #e9e9ed;")
        header.addWidget(title)
        header.addStretch(1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet(
            "QPushButton { color: #75798c; background: transparent; border: none; font-size: 13px; } "
            "QPushButton:hover { color: #e9e9ed; }"
        )
        close_btn.clicked.connect(self.accept)
        header.addWidget(close_btn)
        outer.addLayout(header)

        name = self.metadata.get("name", "")
        duration = _format_duration(self.metadata.get("duration", 0))
        subtitle = QLabel(f"{name} · {duration}" if name else duration)
        subtitle.setStyleSheet("color: #75798c; font-size: 12.5px;")
        outer.addWidget(subtitle)

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter or type a new tag")
        self.filter_input.textChanged.connect(lambda _t: self._refresh_all_tags())
        self.filter_input.returnPressed.connect(self._on_filter_enter)
        outer.addWidget(self.filter_input)

        self._on_this_header = QLabel()
        self._on_this_header.setObjectName("sectionHeader")
        outer.addWidget(self._on_this_header)

        self._assigned_container = QWidget()
        self._assigned_flow = _FlowLayout(self._assigned_container, hspacing=8, vspacing=8)
        outer.addWidget(self._assigned_container)

        self._suggestion_frame = QFrame()
        self._suggestion_frame.setObjectName("tagSuggestionBox")
        self._suggestion_frame.setStyleSheet(
            "QFrame#tagSuggestionBox { border: 1px dashed rgba(145,132,217,0.45); "
            "border-radius: 8px; background-color: rgba(145,132,217,0.06); }"
        )
        sug_layout = QHBoxLayout(self._suggestion_frame)
        sug_layout.setContentsMargins(10, 8, 10, 8)
        sug_layout.setSpacing(8)
        sug_icon = QLabel()
        sug_icon.setPixmap(colored_pixmap("sparkle", "#9184d9", 14))
        sug_layout.addWidget(sug_icon, 0, Qt.AlignmentFlag.AlignTop)
        self._suggestion_label = QLabel()
        self._suggestion_label.setWordWrap(True)
        self._suggestion_label.setStyleSheet("color: #cfd3e5; font-size: 12px;")
        sug_layout.addWidget(self._suggestion_label, 1)
        self._suggestion_btn = QPushButton("Use both")
        self._suggestion_btn.setObjectName("primaryAction")
        self._suggestion_btn.clicked.connect(self._apply_suggestion)
        sug_layout.addWidget(self._suggestion_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._suggestion_frame.setVisible(False)
        outer.addWidget(self._suggestion_frame)

        all_header = QHBoxLayout()
        all_label = QLabel("ALL TAGS")
        all_label.setObjectName("sectionHeader")
        all_header.addWidget(all_label)
        all_header.addStretch(1)
        hint = QLabel("most used first")
        hint.setStyleSheet("color: #75798c; font-size: 11px;")
        all_header.addWidget(hint)
        outer.addLayout(all_header)

        self._all_scroll = QScrollArea()
        self._all_scroll.setWidgetResizable(True)
        self._all_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._all_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._all_container = QWidget()
        self._all_flow = _FlowLayout(self._all_container, hspacing=8, vspacing=8)
        self._all_scroll.setWidget(self._all_container)
        outer.addWidget(self._all_scroll, 1)

        footer = QHBoxLayout()
        save_hint = QLabel("Saved as you tap · Esc to close")
        save_hint.setStyleSheet("color: #75798c; font-size: 11px;")
        footer.addWidget(save_hint)
        footer.addStretch(1)
        done_btn = QPushButton("Done")
        done_btn.setObjectName("primaryAction")
        done_btn.clicked.connect(self.accept)
        footer.addWidget(done_btn)
        outer.addLayout(footer)

    # --- data refresh -----------------------------------------------------

    def _refresh_all(self):
        self._refresh_assigned()
        self._refresh_suggestion()
        self._refresh_all_tags()

    def _refresh_assigned(self):
        self._on_this_header.setText(f"ON THIS RECORDING · {len(self._assigned)}")
        while self._assigned_flow.count():
            item = self._assigned_flow.takeAt(0)
            widget = item.widget()
            if widget:
                # setParent(None) detaches (and hides) immediately; deleteLater()
                # alone defers removal to the next event-loop pass, so the old
                # widget stays painted at its stale position and ghosts behind
                # the freshly laid-out replacement until that pass runs.
                widget.setParent(None)
                widget.deleteLater()
        for name in self._assigned:
            color = tag_manager.get_tag_color(name, tags_file=self.tags_file)
            chip = _AssignedChip(name, color)
            chip.remove_clicked.connect(self._on_remove_tag)
            self._assigned_flow.addWidget(chip)

    def _refresh_suggestion(self):
        name = self.metadata.get("name", "")
        suggested = tag_manager.find_tags_for_recording_name(
            name, self.recordings_dir, exclude_dir=self._session_dir
        )
        missing = [t for t in suggested if t not in self._assigned]
        if not suggested or not missing:
            self._suggestion_frame.setVisible(False)
            self._suggested_tags = []
            return

        self._suggested_tags = suggested
        if len(suggested) == 1:
            joined = suggested[0]
            self._suggestion_btn.setText("Use it")
        elif len(suggested) == 2:
            joined = " and ".join(suggested)
            self._suggestion_btn.setText("Use both")
        else:
            joined = ", ".join(suggested[:-1]) + f", and {suggested[-1]}"
            self._suggestion_btn.setText("Use all")
        self._suggestion_label.setText(f"Last “{name}” was tagged {joined}.")
        self._suggestion_frame.setVisible(True)

    def _refresh_all_tags(self):
        query = self.filter_input.text().strip().lower()
        while self._all_flow.count():
            item = self._all_flow.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        all_tags = tag_manager.load_all_tags(tags_file=self.tags_file)
        counts = tag_manager.get_tag_counts(self.recordings_dir) if self.recordings_dir else {}

        known = {t["name"].lower() for t in all_tags}
        for name in self._assigned:
            if name.lower() not in known:
                all_tags.append({"name": name, "color": tag_manager.get_tag_color(name, tags_file=self.tags_file)})
                known.add(name.lower())

        all_tags.sort(key=lambda t: counts.get(t["name"], 0), reverse=True)

        for t in all_tags:
            name = t["name"]
            if query and query not in name.lower():
                continue
            pill = _AllTagPill(name, counts.get(name, 0), name in self._assigned)
            pill.clicked.connect(self._on_toggle_tag)
            self._all_flow.addWidget(pill)

    # --- actions ------------------------------------------------------------

    def _on_toggle_tag(self, name):
        if not self._session_dir:
            return
        if name in self._assigned:
            self._assigned = tag_manager.remove_tag_from_recording(self._session_dir, name)
        else:
            self._assigned = tag_manager.add_tag_to_recording(
                self._session_dir, name, tags_file=self.tags_file
            )
        self._refresh_all()
        self.tags_changed.emit(self._assigned)

    def _on_remove_tag(self, name):
        if not self._session_dir:
            return
        self._assigned = tag_manager.remove_tag_from_recording(self._session_dir, name)
        self._refresh_all()
        self.tags_changed.emit(self._assigned)

    def _on_filter_enter(self):
        text = self.filter_input.text().strip()
        if not text or not self._session_dir:
            return
        existing = {t["name"].lower() for t in tag_manager.load_all_tags(tags_file=self.tags_file)}
        if text.lower() not in existing:
            tag_manager.create_tag(text, tags_file=self.tags_file)
        self._assigned = tag_manager.add_tag_to_recording(
            self._session_dir, text, tags_file=self.tags_file
        )
        self.filter_input.clear()
        self._refresh_all()
        self.tags_changed.emit(self._assigned)

    def _apply_suggestion(self):
        if not self._session_dir:
            return
        for name in self._suggested_tags:
            if name not in self._assigned:
                self._assigned = tag_manager.add_tag_to_recording(
                    self._session_dir, name, tags_file=self.tags_file
                )
        self._suggestion_frame.setVisible(False)
        self._refresh_assigned()
        self._refresh_all_tags()
        self.tags_changed.emit(self._assigned)
