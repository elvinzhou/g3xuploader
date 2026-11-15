"""Per-product record of the latest version seen and which versions were announced."""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from avcardtool.firmware.packages import firmware_dir

logger = logging.getLogger(__name__)


class FirmwareState:
    """<data_dir>/firmware/state.json, written atomically."""

    def __init__(self, data_dir):
        self.path = firmware_dir(data_dir) / "state.json"
        self.data: Dict[str, Any] = {}
        try:
            loaded = json.loads(self.path.read_text())
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            pass

    def _product(self, key: str) -> Dict[str, Any]:
        entry = self.data.setdefault(key, {})
        if not isinstance(entry.get("notified"), list):
            entry["notified"] = []
        return entry

    def latest_seen(self, key: str) -> Optional[str]:
        return (self.data.get(key) or {}).get("latest_version")

    def record_seen(self, key: str, version: str, now: Optional[datetime] = None) -> None:
        entry = self._product(key)
        if entry.get("latest_version") != version:
            entry["latest_version"] = version
            entry["seen_at"] = (now or datetime.now()).isoformat(timespec="seconds")

    def is_notified(self, key: str, version: str) -> bool:
        return version in self._product(key)["notified"]

    def mark_notified(self, key: str, version: str) -> None:
        notified = self._product(key)["notified"]
        if version not in notified:
            notified.append(version)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            os.replace(tmp, self.path)
        except OSError as e:
            logger.warning(f"Could not save firmware state: {e}")
