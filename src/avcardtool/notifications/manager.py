"""
NotificationManager — fans events out to enabled backends.

Guarantees to callers in the processing pipeline:

  * notify() never raises
  * per-backend delivery is retried with bounded backoff
  * event-type filtering and per-event rate limits come from config
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from avcardtool.notifications.backends import BACKENDS
from avcardtool.notifications.base import (
    NotificationBackend,
    NotificationEvent,
    NotificationResult,
)

logger = logging.getLogger(__name__)

# Delays before each retry; total attempts = 1 + len(_RETRY_DELAYS)
_RETRY_DELAYS = (2.0, 4.0)

# event_type -> minimum seconds between successful notifications.
# garmin_auth_expired would otherwise repeat on every daily check until
# the user re-authenticates.
RATE_LIMITED_EVENTS = {"garmin_auth_expired": 86400.0}


class NotificationManager:
    def __init__(self, notifications_config, data_dir: Path):
        self.config = notifications_config
        self.state_dir = Path(data_dir) / "notifications"
        self.state_path = self.state_dir / "state.json"
        self.backends = self._build_backends()

    @classmethod
    def from_config(cls, config) -> "NotificationManager":
        """Build a manager from the application Config object."""
        return cls(config.notifications, Path(config.system.data_dir))

    def _build_backends(self) -> List[NotificationBackend]:
        backends = []
        for name, backend_cfg in (self.config.backends or {}).items():
            if not isinstance(backend_cfg, dict) or not backend_cfg.get("enabled"):
                continue
            backend_cls = BACKENDS.get(name)
            if backend_cls is None:
                logger.warning(f"Unknown notification backend '{name}' — skipping")
                continue
            try:
                backends.append(backend_cls(backend_cfg))
            except Exception as e:
                logger.warning(f"Could not initialize notification backend '{name}': {e}")
        return backends

    @property
    def active(self) -> bool:
        """True when notifications are enabled and at least one backend is configured."""
        return bool(self.config.enabled and self.backends)

    def event_enabled(self, event_type: str) -> bool:
        # Unknown event types default to enabled so newly added events
        # work without a config migration.
        return bool((self.config.events or {}).get(event_type, True))

    def notify(self, event: NotificationEvent, force: bool = False) -> List[NotificationResult]:
        """
        Deliver an event through every enabled backend.

        Never raises — a notification failure must not break the pipeline
        that called it. force=True bypasses the enabled/event-type/rate-limit
        checks (used by `avcardtool notify-test`).
        """
        try:
            return self._notify(event, force)
        except Exception:
            logger.exception("Notification dispatch failed")
            return []

    def _notify(self, event: NotificationEvent, force: bool) -> List[NotificationResult]:
        if not force:
            if not self.active:
                return []
            if not self.event_enabled(event.event_type):
                logger.debug(f"Notification for {event.event_type} disabled in config")
                return []
            if self._rate_limited(event.event_type):
                logger.info(f"Skipping {event.event_type} notification (rate limited)")
                return []

        if not event.timestamp:
            event.timestamp = datetime.now().isoformat(timespec="seconds")

        results = [self._send_with_retry(backend, event) for backend in self.backends]

        if any(r.success for r in results):
            self._record_sent(event.event_type)

        for r in results:
            if r.success:
                logger.info(f"Notification sent via {r.backend}: {event.event_type}")
            else:
                logger.warning(
                    f"Notification via {r.backend} failed for {event.event_type}: {r.message}"
                )
        return results

    def _send_with_retry(
        self, backend: NotificationBackend, event: NotificationEvent
    ) -> NotificationResult:
        result = NotificationResult(backend=backend.name, success=False, message="not attempted")
        for delay in (0.0,) + _RETRY_DELAYS:
            if delay:
                time.sleep(delay)
            try:
                result = backend.send(event)
            except Exception as e:
                # Backends shouldn't raise, but never trust that.
                result = NotificationResult(backend=backend.name, success=False, message=str(e))
            if result.success:
                return result
        return result

    # -- rate-limit state ------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text())
        except Exception:
            return {}

    def _rate_limited(self, event_type: str) -> bool:
        window = RATE_LIMITED_EVENTS.get(event_type)
        if not window:
            return False
        last = self._load_state().get("last_sent", {}).get(event_type)
        return bool(last) and (time.time() - float(last)) < window

    def _record_sent(self, event_type: str) -> None:
        # Only rate-limited events need persistence; a failed send is
        # deliberately not recorded so the next run retries.
        if event_type not in RATE_LIMITED_EVENTS:
            return
        try:
            state = self._load_state()
            state.setdefault("last_sent", {})[event_type] = time.time()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning(f"Could not persist notification state: {e}")
