"""
Core types for the notifications system.

Events are produced by the automatic processing flows (auto-process and
navdata auto-update) and delivered by NotificationManager through the
configured backends. See NOTIFICATIONS_DESIGN.md for the full design.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class Severity(str, Enum):
    INFO = "info"        # flights processed, navdata installed
    WARNING = "warning"  # partial failure (some uploads failed, file errors)
    ERROR = "error"      # auth expired, install failed, nothing delivered


@dataclass
class NotificationEvent:
    event_type: str          # "flights_processed" | "navdata_updated" | ...
    severity: Severity
    title: str               # short; becomes the email subject
    body: str                # multi-line human-readable text
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""      # ISO-8601 local time; filled by the manager if empty


@dataclass
class NotificationResult:
    backend: str
    success: bool
    message: str = ""


class NotificationBackend(ABC):
    """
    Base class for notification delivery backends.

    Backends must be constructible from their config dict alone and must
    report delivery failures via NotificationResult rather than raising —
    a notification failure can never break the processing pipeline.
    """

    name = "base"

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def send(self, event: NotificationEvent) -> NotificationResult:
        """Deliver a single event."""

    def validate_config(self) -> List[str]:
        """Return human-readable configuration problems (empty list = OK)."""
        return []
