"""
Notifications system — delivery layer for events produced by the
automatic processing flows. See NOTIFICATIONS_DESIGN.md.
"""

from avcardtool.notifications.backends import BACKENDS
from avcardtool.notifications.base import (
    NotificationBackend,
    NotificationEvent,
    NotificationResult,
    Severity,
)
from avcardtool.notifications.manager import NotificationManager

__all__ = [
    "BACKENDS",
    "NotificationBackend",
    "NotificationEvent",
    "NotificationResult",
    "NotificationManager",
    "Severity",
]
