"""
One-card firmware cycle for loader-card products (GTN Xi) on the avionics' own database card.

  start    back up the card's small identity files, record "pending" (before any erase),
           wipe the card by deleting files (never a reformat: the volume serial must
           survive), write the loader image, ask the pilot to confirm.
  pending  navdata is blocked for that card serial until confirm/cancel.
  restore  after confirm/cancel: remove the loader files, put the identity files back;
           navdata then reinstalls the databases.

Nothing here runs without a recorded baseline (the version the avionics already has):
a card whose avionics is already current must never be wiped.
"""

import logging
import shutil
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from avcardtool.firmware.cards import (
    CardPlan,
    _differs,
    apply_plan,
    is_database_card,
)
from avcardtool.firmware.catalog import is_newer
from avcardtool.firmware.packages import (
    PreparedPackage,
    firmware_dir,
    list_cached,
    load_package,
    package_dir,
    verify_package,
)
from avcardtool.firmware.registry import PRODUCTS, FirmwareError, Product
from avcardtool.firmware.state import FirmwareState

logger = logging.getLogger(__name__)

IDENTITY_FILES = ("avionics.txt", "feat_unlk.dat", ".evidf.dat", ".gadm.meta")
CONFIRM_PATH = "/firmware/confirm/"
BASELINE_HINT = (
    "Automatic installs on the {name} database card start once the installed version is "
    "recorded: avcardtool firmware set-installed {key} <version> (read it from the {name}'s "
    "System Information page), or use 'avcardtool firmware install {key} <device> --erase' "
    "to do it once by hand."
)


def baseline_hint(product: Product) -> str:
    return BASELINE_HINT.format(name=product.name, key=product.key)


# ---------------------------------------------------------------------------
# Identity-file backup
# ---------------------------------------------------------------------------

def backup_dir_for(data_dir, serial: str) -> Path:
    return firmware_dir(data_dir) / "cards" / re.sub(r"[^0-9A-Za-z_-]", "_", serial) / "backup"


def backup_identity(card: Path, dest: Path) -> List[str]:
    """Copy the identity files to ``dest`` (replacing an older backup) and verify them by size."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    saved = []
    for name in IDENTITY_FILES:
        src = card / name
        if src.is_file():
            shutil.copyfile(src, dest / name)
            if (dest / name).stat().st_size != src.stat().st_size:
                raise FirmwareError(f"Backup of {name} is incomplete")
            saved.append(name)
    return saved


def restore_identity(card: Path, src: Path) -> List[str]:
    """Put backed-up identity files back where the card has none; GADM's file attributes included."""
    from avcardtool.navdata.garmin.taw_parser import (
        _set_garmin_data_attrs, _set_garmin_meta_attrs, _set_plain)
    restored = []
    for name in IDENTITY_FILES:
        f = src / name
        if f.is_file() and not (card / name).exists():
            shutil.copyfile(f, card / name)
            if (card / name).stat().st_size != f.stat().st_size:
                raise FirmwareError(f"Restoring {name} failed (size mismatch)")
            {"feat_unlk.dat": _set_garmin_data_attrs, ".gadm.meta": _set_garmin_meta_attrs,
             }.get(name, _set_plain)(card / name)
            restored.append(name)
    return restored


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

@dataclass
class CycleDecision:
    action: str   # n/a | no_baseline | current | declined | skip | start | resume | awaiting
    reason: str = ""
    version: Optional[str] = None


def _image_present(card: Path, pkg: PreparedPackage) -> bool:
    return not any(_differs(card, f) for f in pkg.files)


def decide(product: Product, pkg: PreparedPackage, card: Path, serial: str, state: FirmwareState,
           fw, restored=()) -> CycleDecision:
    if not product.cycle_capable:
        return CycleDecision("n/a")
    pend = state.pending(serial, product.key) if serial else None
    if pend:
        version = pend.get("version")
        if _image_present(card, pkg) if pkg.version == version else False:
            return CycleDecision("awaiting", f"{product.name} {version} written, awaiting confirmation", version)
        return CycleDecision("resume", f"{product.name} {version} write incomplete", version)
    if not fw.database_card_cycle or not is_database_card(product, card):
        return CycleDecision("n/a")
    if not serial:
        return CycleDecision("skip", "database card has no readable volume serial")
    if serial in restored:
        return CycleDecision("skip", "card was restored in this run")
    baseline = state.installed_version(serial, product.key)
    if not baseline:
        return CycleDecision("no_baseline", "no installed version recorded; " + baseline_hint(product))
    if not is_newer(pkg.version, baseline):
        return CycleDecision("current", f"avionics has {baseline}, package is {pkg.version}")
    if state.is_declined(serial, product.key, pkg.version):
        return CycleDecision("declined", f"{pkg.version} was declined for this card")
    return CycleDecision("start", f"avionics has {baseline}, {pkg.version} is available", pkg.version)


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def confirm_link(fw, token: str) -> Optional[str]:
    return fw.confirm_url.rstrip("/") + CONFIRM_PATH + token if fw.confirm_url else None


def send_confirm_needed(cfg, state: FirmwareState, serial: str, key: str, notify_manager,
                        reminder: bool = False, now: Optional[datetime] = None) -> bool:
    """Send (or re-send) the confirm request. Marks it delivered in state; returns delivery."""
    from avcardtool.notifications.events import build_firmware_confirm_needed_event
    pend = state.pending(serial, key)
    if pend is None:
        return False
    product = PRODUCTS[key]
    pkg = load_package(package_dir(cfg.system.data_dir, key, pend["version"]))
    event = build_firmware_confirm_needed_event(
        product.name, pend["version"], confirm_link(cfg.firmware, pend["token"]),
        f"avcardtool firmware confirm {key}", f"avcardtool firmware cancel {key}",
        str(pkg.bulletin_path) if pkg and pkg.bulletin_path else None, reminder=reminder)
    if notify_manager is None:
        delivered = True
    else:
        results = notify_manager.notify(event)
        delivered = (not getattr(notify_manager, "dry_run", False)
                     and (not results or any(r.success for r in results)))
    if delivered:
        state.mark_confirm_sent(serial, key, now)
        state.save()
    return delivered


def send_due_notifications(cfg, state: FirmwareState, notify_manager, log, now: Optional[datetime] = None) -> int:
    """First (retried until delivered) and weekly reminder notifications for pending cycles."""
    sent = 0
    for serial, key, _pend in state.pending_items():
        due = state.confirm_due(serial, key, now)
        if due and send_confirm_needed(cfg, state, serial, key, notify_manager, reminder=(due == "reminder"), now=now):
            sent += 1
            log.info(f"Firmware: sent {'reminder' if due == 'reminder' else 'confirmation request'} for {key}")
    return sent


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def run_cycle(cfg, product: Product, pkg: PreparedPackage, mount: Path, serial: str,
              state: FirmwareState, notify_manager, log, resume: bool = False) -> int:
    """
    Wipe the card and write the loader image. ``mount`` must be writable.

    The package is verified and the identity files backed up before anything is
    erased, and "pending" is saved before the erase, so a crash leaves navdata
    blocked and the cycle resumable. Raises FirmwareError.
    """
    verify_package(pkg)
    baseline = state.installed_version(serial, product.key)
    if not resume:
        bdir = backup_dir_for(cfg.system.data_dir, serial)
        saved = backup_identity(mount, bdir)
        log.info(f"  Backed up {', '.join(saved) or 'no identity files'} before wiping the card")
        state.start_pending(serial, product.key, pkg.version, str(bdir))
        state.save()
    plan = CardPlan(product.key, pkg.version, mount, "install",
                    "firmware cycle on the database card", installed_version=baseline,
                    copy=list(pkg.files), erase=True)
    n = apply_plan(plan, pkg)
    send_confirm_needed(cfg, state, serial, product.key, notify_manager)
    return n
