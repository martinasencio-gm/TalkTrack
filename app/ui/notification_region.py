import logging
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon
from dataclasses import dataclass
from typing import Callable, Optional

from app.utils.icons import colored_pixmap

logger = logging.getLogger(__name__)

_NOTIF_ICON_SIZE = 14

# Priorities as integers (lower is higher priority)
PRIORITY_BLOCKING_ERROR = 0
PRIORITY_RECORDING_INTEGRITY = 1
PRIORITY_MEETING_DETECTED = 2
PRIORITY_JOB_FINISHED = 3
PRIORITY_SUGGESTION = 4
PRIORITY_CONFIRMATION = 5

@dataclass
class Notification:
    priority: int
    text: str
    action_text: Optional[str] = None
    action_callback: Optional[Callable] = None
    secondary_action_text: Optional[str] = None
    secondary_action_callback: Optional[Callable] = None
    ttl: int = 0  # 0 means infinite

class NotificationRegion(QWidget):
    """
    A single 34 px row directly under the capture bar, showing one message at a time.
    Others queue based on fixed priority.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.queue = []
        self.current_notification = None
        self._setup_ui()
        
        self.ttl_timer = QTimer(self)
        self.ttl_timer.timeout.connect(self._on_ttl_tick)
        self.current_ttl = 0
        
        self.hide()

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #232532;
                border-bottom: 1px solid #3f424d;
            }
        """)
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(17, 0, 17, 0)
        self.main_layout.setSpacing(11)
        
        self.icon_label = QLabel()
        self.icon_label.setPixmap(colored_pixmap("info", "#9184d9", _NOTIF_ICON_SIZE))
        
        self.text_label = QLabel("")
        self.text_label.setStyleSheet("color: #e9e9ed; font-size: 12.5px;")
        
        self.main_layout.addWidget(self.icon_label)
        self.main_layout.addWidget(self.text_label, stretch=1)
        
        self.primary_btn = QPushButton("")
        self.primary_btn.setObjectName("primaryAction")
        self.primary_btn.clicked.connect(self._on_primary_clicked)
        self.primary_btn.hide()
        
        self.secondary_btn = QPushButton("")
        self.secondary_btn.clicked.connect(self._on_secondary_clicked)
        self.secondary_btn.hide()
        
        self.dismiss_btn = QPushButton()
        self.dismiss_btn.setIcon(QIcon(colored_pixmap("x", "#9397ab", 12)))
        self.dismiss_btn.setFixedSize(24, 24)
        self.dismiss_btn.setStyleSheet("border: none;")
        self.dismiss_btn.clicked.connect(self.dismiss_current)
        
        self.main_layout.addWidget(self.secondary_btn)
        self.main_layout.addWidget(self.primary_btn)
        self.main_layout.addWidget(self.dismiss_btn)

    def enqueue(self, priority: int, text: str, action_text=None, action_callback=None,
                secondary_action_text=None, secondary_action_callback=None, ttl: int = 0):
        
        notif = Notification(
            priority=priority,
            text=text,
            action_text=action_text,
            action_callback=action_callback,
            secondary_action_text=secondary_action_text,
            secondary_action_callback=secondary_action_callback,
            ttl=ttl
        )
        
        self.queue.append(notif)
        # Sort by priority (0 is highest)
        self.queue.sort(key=lambda n: n.priority)
        
        self._evaluate_queue()

    def _evaluate_queue(self):
        if not self.queue:
            if not self.current_notification:
                self.hide()
            return
            
        next_notif = self.queue[0]
        
        # If there is a current notification, check if the new one has higher priority
        if self.current_notification:
            if next_notif.priority < self.current_notification.priority:
                # Displace the current one (put it back in queue)
                self.queue.append(self.current_notification)
                self.queue.sort(key=lambda n: n.priority)
                self._show_notification(self.queue.pop(0))
        else:
            self._show_notification(self.queue.pop(0))

    def _show_notification(self, notif: Notification):
        self.current_notification = notif
        self.text_label.setText(notif.text)
        
        # Priority icon/color mapping
        if notif.priority == PRIORITY_BLOCKING_ERROR:
            self.icon_label.setPixmap(colored_pixmap("warning-octagon", "#f38ba8", _NOTIF_ICON_SIZE))
        elif notif.priority == PRIORITY_RECORDING_INTEGRITY:
            self.icon_label.setPixmap(colored_pixmap("warning", "#f9e2af", _NOTIF_ICON_SIZE))
        else:
            self.icon_label.setPixmap(colored_pixmap("info", "#9184d9", _NOTIF_ICON_SIZE))
        
        if notif.action_text:
            self.primary_btn.setText(notif.action_text)
            self.primary_btn.show()
        else:
            self.primary_btn.hide()
            
        if notif.secondary_action_text:
            self.secondary_btn.setText(notif.secondary_action_text)
            self.secondary_btn.show()
        else:
            self.secondary_btn.hide()
            
        if notif.ttl > 0:
            self.current_ttl = notif.ttl
            self.ttl_timer.start(1000)
        else:
            self.ttl_timer.stop()
            
        self.show()

    def dismiss_current(self):
        self.current_notification = None
        self.ttl_timer.stop()
        self.hide()
        self._evaluate_queue()
        
    def _on_primary_clicked(self):
        if self.current_notification and self.current_notification.action_callback:
            self.current_notification.action_callback()
        self.dismiss_current()

    def _on_secondary_clicked(self):
        if self.current_notification and self.current_notification.secondary_action_callback:
            self.current_notification.secondary_action_callback()
        self.dismiss_current()

    def _on_ttl_tick(self):
        self.current_ttl -= 1
        if self.current_ttl <= 0:
            self.dismiss_current()
