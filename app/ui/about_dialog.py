"""About TalkTrack dialog."""

import webbrowser
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
)

GITHUB_URL = "https://github.com/martinasencio-gm/TalkTrack"
BMAC_URL = "https://buymeacoffee.com/obscureaintsecure"
VERSION = "1.0.0"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About TalkTrack")
        # Sized to the button row, which is the widest item: the two
        # link buttons need 496px together, plus the layout's 11px
        # margins. At 420 they were squeezed ~60px each. Re-check this
        # number if either button's label changes.
        self.setFixedSize(540, 320)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("TalkTrack")
        title.setStyleSheet(
            "font-size: 26px; font-weight: 600; color: #e9e9ed; "
            "font-family: 'Inter', 'Segoe UI', sans-serif;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        version = QLabel(f"Version {VERSION}")
        version.setStyleSheet("font-size: 13px; color: #9397ab;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        desc = QLabel(
            "AI-powered call recording, transcription, and speaker\n"
            "diarization for Windows. Free and offline."
        )
        desc.setStyleSheet("font-size: 12px; color: #cfd3e5;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        credits = QLabel(
            "Built with Faster Whisper, pyannote.audio, Catppuccin,\n"
            "and Phosphor Icons (MIT License)."
        )
        credits.setStyleSheet("font-size: 10.5px; color: #75798c;")
        credits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits.setWordWrap(True)
        layout.addWidget(credits)

        layout.addSpacing(8)

        gh_btn = QPushButton("GitHub Repository")
        gh_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(145,132,217,0.18); color: #9184d9; border: 1px solid #9184d9;"
            "  padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(145,132,217,0.28);"
            "}"
        )
        gh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gh_btn.clicked.connect(lambda: webbrowser.open(GITHUB_URL))

        bmac_btn = QPushButton("Buy Me a Coffee")
        bmac_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #f9e2af; color: #12141f; font-weight: bold;"
            "  padding: 8px 16px; border-radius: 6px; font-size: 13px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #f2d68a;"
            "}"
        )
        bmac_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bmac_btn.clicked.connect(lambda: webbrowser.open(BMAC_URL))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(gh_btn)
        btn_row.addWidget(bmac_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)
