"""About TalkTrack dialog."""

import webbrowser
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
)

BMAC_URL = "https://buymeacoffee.com/obscureaintsecure"
VERSION = "1.0.0"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About TalkTrack")
        self.setFixedSize(400, 280)
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
        version.setStyleSheet("font-size: 13px; color: #a6adc8;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        desc = QLabel(
            "AI-powered call recording, transcription, and speaker\n"
            "diarization for Windows. Free and offline."
        )
        desc.setStyleSheet("font-size: 12px; color: #bac2de;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        credits = QLabel(
            "Built with Faster Whisper, pyannote.audio, Catppuccin,\n"
            "and Phosphor Icons (MIT License)."
        )
        credits.setStyleSheet("font-size: 10.5px; color: #7f849c;")
        credits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits.setWordWrap(True)
        layout.addWidget(credits)

        layout.addSpacing(8)

        bmac_btn = QPushButton("Buy Me a Coffee")
        bmac_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #f9e2af; color: #1e1e2e; font-weight: bold;"
            "  padding: 10px 20px; border-radius: 6px; font-size: 14px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #f2d68a;"
            "}"
        )
        bmac_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bmac_btn.clicked.connect(lambda: webbrowser.open(BMAC_URL))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
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
