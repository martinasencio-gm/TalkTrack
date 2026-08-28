import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal

from app.ui.collapsible_section import CollapsibleSection
from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)

_EMPTY_ICON_COLOR = "#3f424d"  # neutral-800, dim — a quiet, non-alarming state
_EMPTY_ICON_SIZE = 22


class InspectorWidget(QWidget):
    """
    Column C: Inspector
    Replaces the five-tab pane with a scrolling list of collapsible sections.
    """

    connect_provider_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(322)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Scroll area for the sections
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(1) # 1px divider between sections
        
        # Three sections — Notes / Speakers / Summary. "Ask this call…"
        # (chat) is folded into the Summary section rather than a fourth
        # section (see add_chat_panel). Action items are a section inside
        # the AI summary itself, not a separate panel.
        self.notes_section = CollapsibleSection("Notes", icon="note-pencil")
        self.speakers_section = CollapsibleSection("Speakers", icon="users-three")
        self.summary_section = CollapsibleSection("Summary", icon="sparkle")

        self.scroll_layout.addWidget(self.notes_section)
        self.scroll_layout.addWidget(self.speakers_section)
        self.scroll_layout.addWidget(self.summary_section)
        self.scroll_layout.addStretch()
        
        self.scroll_area.setWidget(self.scroll_content)

        # Empty state: shown instead of the sections when nothing is
        # selected (startup, or the loaded recording was deleted/cleared).
        self.empty_widget = QWidget()
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setContentsMargins(17, 22, 17, 22)
        empty_layout.setSpacing(10)

        self.empty_icon = QLabel()
        self.empty_icon.setPixmap(colored_pixmap("note-pencil", _EMPTY_ICON_COLOR, _EMPTY_ICON_SIZE))
        empty_title = QLabel("Notes, speakers and summaries")
        empty_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #cfd3e5;")
        empty_body = QLabel(
            "This panel fills in once a recording is open — and stays "
            "usable while one is running, so taking notes never hides the "
            "transcript."
        )
        empty_body.setWordWrap(True)
        empty_body.setStyleSheet("font-size: 12.5px; color: #75798c;")

        empty_layout.addWidget(self.empty_icon)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_body)
        empty_layout.addStretch()

        # Footer — the product's main claim, otherwise invisible in the UI.
        self.footer = QWidget()
        self.footer.setObjectName("inspectorFooter")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(17, 8, 17, 8)
        footer_layout.setSpacing(6)
        footer_icon = QLabel()
        footer_icon.setPixmap(colored_pixmap("shield-check", "#75798c", 13))
        footer_text = QLabel("Everything stays on this machine")
        footer_text.setStyleSheet("color: #75798c; font-size: 12.5px;")
        footer_layout.addWidget(footer_icon)
        footer_layout.addWidget(footer_text)
        footer_layout.addStretch()

        self.main_layout.addWidget(self.scroll_area)
        self.main_layout.addWidget(self.empty_widget)
        self.main_layout.addWidget(self.footer)

        self.set_empty_state(True)

    def add_notes_panel(self, panel):
        self.notes_section.content_layout().addWidget(panel)
        self.notes_section.set_expanded(True)

    def add_speakers_panel(self, panel):
        self.speakers_section.content_layout().addWidget(panel)
        self.speakers_section.set_expanded(True)

    def add_summary_panel(self, summary_panel):
        self._summary_panel = summary_panel
        self.summary_section.content_layout().addWidget(summary_panel)

        # "No provider configured" state: the summary needs one, and
        # showing a Generate button that silently no-ops on click (see
        # MainWindow._run_summarize's `if provider is None: return`) is
        # worse than not showing one at all.
        self.ai_off_widget = QWidget()
        ai_off_layout = QVBoxLayout(self.ai_off_widget)
        ai_off_layout.setContentsMargins(0, 4, 0, 0)
        ai_off_layout.setSpacing(10)

        ai_off_icon = QLabel()
        ai_off_icon.setPixmap(colored_pixmap("sparkle", _EMPTY_ICON_COLOR, _EMPTY_ICON_SIZE))
        ai_off_layout.addWidget(ai_off_icon)

        ai_off_message = QLabel(
            "These run when a recording stops, if you connect a provider. "
            "No provider is configured — recording, transcription and "
            "speaker labels all work without one."
        )
        ai_off_message.setWordWrap(True)
        ai_off_message.setObjectName("aiOffMessage")
        ai_off_message.setStyleSheet(
            "border: 1px dashed #3f424d; border-radius: 6px; padding: 13px;"
            " font-size: 12.5px; color: #75798c;"
        )
        ai_off_layout.addWidget(ai_off_message)

        connect_btn = QPushButton("Connect a provider")
        connect_btn.setObjectName("primaryAction")
        connect_btn.clicked.connect(self.connect_provider_requested.emit)
        ai_off_layout.addWidget(connect_btn)

        self.summary_section.content_layout().addWidget(self.ai_off_widget)
        self.ai_off_widget.setVisible(False)

        self.summary_section.set_expanded(True)

    def set_ai_configured(self, configured):
        """Toggle the summary section between its real panel and the
        "connect a provider" message."""
        if not hasattr(self, "_summary_panel"):
            return
        self._summary_panel.setVisible(configured)
        self.ai_off_widget.setVisible(not configured)

    def add_chat_panel(self, panel):
        """"Ask this call…" lives inside the Summary section, not as its
        own section — pinned after the summary content."""
        self.summary_section.content_layout().addWidget(panel)

    def set_empty_state(self, is_empty):
        """Swap between the section list and the "nothing selected" message."""
        self.scroll_area.setVisible(not is_empty)
        self.empty_widget.setVisible(is_empty)
