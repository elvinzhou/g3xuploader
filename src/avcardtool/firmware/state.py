"""Per-product record of the latest version seen and which versions were announced."""

import hmac
import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
        # Tokens of the cycles that were pending when this copy was read. save()
        # uses them to notice a confirm/cancel made meanwhile by another process
        # (the link server or the CLI during a long nightly run).
        self._loaded_tokens = self._pending_tokens(self.data)

    @staticmethod
    def _pending_tokens(data: Dict[str, Any]) -> Dict[Tuple[str, str], Any]:
        out: Dict[Tuple[str, str], Any] = {}
        cards = data.get("cards")
        for serial, by_product in (cards.items() if isinstance(cards, dict) else ()):
            for key, entry in (by_product.items() if isinstance(by_product, dict) else ()):
                if isinstance(entry, dict) and isinstance(entry.get("pending"), dict):
                    out[(serial, key)] = entry["pending"].get("token")
        return out

    def _adopt_external_resolutions(self) -> None:
        """Keep a confirm/cancel another process wrote since this copy was read."""
        if not self._loaded_tokens:
            return
        try:
            disk = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(disk, dict):
            return
        disk_tokens = self._pending_tokens(disk)
        for (serial, key), token in self._loaded_tokens.items():
            if disk_tokens.get((serial, key)) == token:
                continue
            disk_entry = ((disk.get("cards") or {}).get(serial) or {}).get(key)
            if isinstance(disk_entry, dict):
                self.data.setdefault("cards", {}).setdefault(serial, {})[key] = disk_entry

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

    # -- per-card firmware cycle (keyed by the card's volume serial: private) --

    def _card(self, serial: str, key: str, create: bool = True) -> Optional[Dict[str, Any]]:
        cards = self.data.get("cards")
        if not isinstance(cards, dict):
            if not create:
                return None
            cards = self.data["cards"] = {}
        by_product = cards.get(serial)
        if not isinstance(by_product, dict):
            if not create:
                return None
            by_product = cards[serial] = {}
        entry = by_product.get(key)
        if not isinstance(entry, dict):
            if not create:
                return None
            entry = by_product[key] = {}
        return entry

    def installed_version(self, serial: str, key: str) -> Optional[str]:
        return (self._card(serial, key, create=False) or {}).get("installed_version")

    def has_baseline(self, key: str) -> bool:
        return any(isinstance(p, dict) and (p.get(key) or {}).get("installed_version")
                   for p in (self.data.get("cards") or {}).values())

    def set_installed(self, serial: str, key: str, version: str) -> None:
        self._card(serial, key)["installed_version"] = version

    def pending(self, serial: str, key: str) -> Optional[Dict[str, Any]]:
        p = (self._card(serial, key, create=False) or {}).get("pending")
        return p if isinstance(p, dict) else None

    def start_pending(self, serial: str, key: str, version: str, backup_dir: Optional[str],
                      now: Optional[datetime] = None) -> str:
        token = secrets.token_urlsafe(32)
        self._card(serial, key)["pending"] = {
            "version": version,
            "written_at": (now or datetime.now()).isoformat(timespec="seconds"),
            "token": token,
            "backup_dir": backup_dir,
            "notified_at": None,
            "last_reminded": None,
        }
        return token

    def navdata_blocked(self, serial: str) -> Optional[str]:
        """Human reason while any product's firmware on this card awaits confirmation."""
        by_product = (self.data.get("cards") or {}).get(serial)
        if isinstance(by_product, dict):
            for key, entry in by_product.items():
                if isinstance(entry, dict) and isinstance(entry.get("pending"), dict):
                    return f"{key} software {entry['pending'].get('version', '?')} awaits confirmation"
        return None

    def pending_items(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        out = []
        for serial, by_product in (self.data.get("cards") or {}).items():
            for key, entry in (by_product or {}).items():
                if isinstance(entry, dict) and isinstance(entry.get("pending"), dict):
                    out.append((serial, key, entry["pending"]))
        return out

    def restore_items(self, serial: Optional[str] = None) -> List[Tuple[str, str, Dict[str, Any]]]:
        out = []
        for s, by_product in (self.data.get("cards") or {}).items():
            if serial is not None and s != serial:
                continue
            for key, entry in (by_product or {}).items():
                if isinstance(entry, dict) and entry.get("restore"):
                    out.append((s, key, entry))
        return out

    def find_by_token(self, token: str) -> Optional[Tuple[str, str]]:
        """Constant-time lookup over every pending token."""
        found = None
        for serial, key, pend in self.pending_items():
            if hmac.compare_digest(str(pend.get("token", "")).encode(), token.encode()):
                found = (serial, key)
        return found

    def confirm(self, serial: str, key: str) -> Optional[str]:
        """Firmware confirmed installed: baseline moves up, loader files must be restored. Returns version."""
        entry = self._card(serial, key, create=False)
        if not entry or not isinstance(entry.get("pending"), dict):
            return None
        version = entry["pending"].get("version")
        entry["installed_version"] = version
        entry["restore"] = {"backup_dir": entry["pending"].get("backup_dir")}
        entry.pop("pending", None)
        return version

    def cancel(self, serial: str, key: str) -> Optional[str]:
        """Not installing: restore the card; remember the version as declined for this card."""
        entry = self._card(serial, key, create=False)
        if not entry or not isinstance(entry.get("pending"), dict):
            return None
        version = entry["pending"].get("version")
        entry["restore"] = {"backup_dir": entry["pending"].get("backup_dir")}
        declined = entry.setdefault("declined", [])
        if version not in declined:
            declined.append(version)
        entry.pop("pending", None)
        return version

    def is_declined(self, serial: str, key: str, version: str) -> bool:
        return version in ((self._card(serial, key, create=False) or {}).get("declined") or [])

    def clear_restore(self, serial: str, key: str) -> None:
        entry = self._card(serial, key, create=False)
        if entry:
            entry.pop("restore", None)

    def mark_confirm_sent(self, serial: str, key: str, now: Optional[datetime] = None) -> None:
        pend = self.pending(serial, key)
        if pend is not None:
            stamp = (now or datetime.now()).isoformat(timespec="seconds")
            pend["notified_at"] = pend.get("notified_at") or stamp
            pend["last_reminded"] = stamp

    def confirm_due(self, serial: str, key: str, now: Optional[datetime] = None,
                    interval_days: int = 7) -> Optional[str]:
        """'first' if never delivered, 'reminder' if the last one is old enough, else None."""
        pend = self.pending(serial, key)
        if pend is None:
            return None
        if not pend.get("notified_at"):
            return "first"
        try:
            last = datetime.fromisoformat(pend.get("last_reminded") or pend["notified_at"])
        except (TypeError, ValueError):
            return "reminder"
        return "reminder" if ((now or datetime.now()) - last).days >= interval_days else None

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.with_name("state.lock"), "w") as lock:
                try:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX)
                except (ImportError, OSError):
                    pass
                self._adopt_external_resolutions()
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(self.data, indent=2))
                os.replace(tmp, self.path)
                self._loaded_tokens = self._pending_tokens(self.data)
        except OSError as e:
            logger.warning(f"Could not save firmware state: {e}")
