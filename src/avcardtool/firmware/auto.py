"""
The unattended firmware run: check Garmin, keep the cache current, update qualifying cards.

``run_firmware_auto_update`` never raises: firmware problems must not break navdata.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from avcardtool.core.config import FirmwareConfig
from avcardtool.core.utils import _is_mounted_readonly, resolve_device_mount_point
from avcardtool.firmware import cycle
from avcardtool.firmware.cards import (
    CardPlan,
    _erase_card_root,
    apply_plan,
    database_card_signs,
    describe_plan,
    plan_for_card,
    read_loader_marker,
)
from avcardtool.firmware.catalog import FirmwareNetworkError, FirmwareRelease, fetch_release
from avcardtool.firmware.packages import (
    PreparedPackage,
    ensure_package,
    list_cached,
    load_package,
    package_dir,
)
from avcardtool.firmware.registry import PRODUCTS, FirmwareError, Product
from avcardtool.firmware.state import FirmwareState
from avcardtool.navdata.sdcard import SDCardDetector

Issue = Tuple[str, str]  # (severity, message), as in navdata auto-update


class MountTracker:
    """
    Gives read-only access to cards for inspection and read-write access only when
    something will be written, and puts every card back the way it was found:
    cards we mounted, and cards we had to remount read-write, are unmounted at the end.
    """

    def __init__(self, detector: SDCardDetector, log: logging.Logger):
        self.detector = detector
        self.log = log
        self._unmount: Dict[str, str] = {}   # device path -> mount point to unmount at the end

    def inspect(self, device: Path, mount_point: Optional[str]) -> Path:
        if mount_point:
            return Path(mount_point)
        mount = resolve_device_mount_point(device, readonly=True)
        self.log.info(f"Mounted {device} read-only at {mount} to inspect it")
        self._unmount[str(device)] = str(mount)
        return mount

    def writable(self, device: Path, mount: Path) -> Path:
        if not _is_mounted_readonly(device):
            return mount
        new_mount = resolve_device_mount_point(device, readonly=False)
        self._unmount[str(device)] = str(new_mount)
        return new_mount

    def release(self) -> None:
        for mount in self._unmount.values():
            try:
                self.detector.unmount_card(mount)
            except Exception as e:
                self.log.warning(f"Could not unmount {mount}: {e}")
        self._unmount.clear()


def _delivered(manager, results) -> bool:
    """True when a notification needs no retry (sent, or nothing is configured to send it)."""
    if manager is None:
        return True
    if getattr(manager, "dry_run", False):
        return False
    return not results or any(r.success for r in results)


def refresh_product(cfg, product: Product, state: Optional[FirmwareState], notify_manager,
                    log: logging.Logger, issues: List[Issue], download: bool = True,
                    ) -> Tuple[Optional[FirmwareRelease], Optional[PreparedPackage]]:
    """
    Fetch the product's release; download and prepare it if new; announce it once.

    Returns (release or None, newest usable package or None). With download=False
    (dry run) nothing is written: an already cached package is reused.
    """
    from avcardtool.notifications.events import build_firmware_available_event

    data_dir = Path(cfg.system.data_dir)
    fw: FirmwareConfig = cfg.firmware
    release = None
    pkg: Optional[PreparedPackage] = None

    try:
        release = fetch_release(product, fw.product_page_url(product.key))
        log.info(f"{product.name}: Garmin lists software {release.version} ({release.release_date or 'no date'})")
    except FirmwareNetworkError as e:
        log.warning(f"{product.name}: {e}")
        issues.append(("WARNING", f"{product.name}: {e}"))
    except FirmwareError as e:
        log.error(f"{product.name}: {e}")
        issues.append(("ERROR", f"{product.name}: {e}"))

    if release:
        pkg = load_package(package_dir(data_dir, product.key, release.version))
        if pkg is None and download:
            try:
                pkg = ensure_package(release, product, data_dir)
                log.info(f"{product.name}: {release.version} cached at {pkg.root}")
            except FirmwareNetworkError as e:
                log.warning(f"{product.name}: {e}")
                issues.append(("WARNING", f"{product.name}: download failed: {e}"))
            except FirmwareError as e:
                log.error(f"{product.name}: {e}")
                issues.append(("ERROR", f"{product.name}: {e}"))
        elif pkg is None:
            log.info(f"{product.name}: DRY RUN: would download {release.download_url}")

        if download and state is not None:
            state.record_seen(product.key, release.version)
            if pkg and not state.is_notified(product.key, release.version):
                if notify_manager:
                    results = notify_manager.notify(build_firmware_available_event(
                        product_name=product.name, version=release.version,
                        release_date=release.release_date, notes=release.notes,
                        page_url=release.page_url, cache_path=str(pkg.root),
                        bulletin_path=str(pkg.bulletin_path) if pkg.bulletin_path else None,
                        auto_install=fw.auto_install,
                        baseline_hint=(cycle.baseline_hint(product)
                                       if product.cycle_capable and fw.database_card_cycle
                                       and fw.auto_install and not state.has_baseline(product.key)
                                       else None),
                    ))
                else:
                    results = []
                if _delivered(notify_manager, results):
                    state.mark_notified(product.key, release.version)
            state.save()

    if pkg is None:
        cached = list_cached(data_dir, product.key)
        if cached:
            pkg = cached[0]
            log.info(f"{product.name}: using cached software {pkg.version}")
    return release, pkg


def _card_label(card, mount: Path) -> str:
    return f"{mount}" + (f" (volume {card.volume_id})" if card.volume_id else "")


def run_firmware_auto_update(cfg, device: Optional[Path], log: logging.Logger,
                             notify_manager, restored: Optional[Set[str]] = None) -> List[Issue]:
    """
    Check Garmin, refresh the cache, update qualifying cards. Returns (severity, message) issues.

    ``restored``: serials of cards the restore pre-step just handled; no new cycle starts on them.
    """
    issues: List[Issue] = []
    try:
        _run(cfg, device, log, notify_manager, issues, restored or set())
    except Exception as e:  # never let firmware break the caller
        log.exception("Firmware update failed unexpectedly")
        issues.append(("ERROR", f"Unexpected firmware error: {e}"))

    try:
        if issues and notify_manager and any(sev == "ERROR" for sev, _ in issues):
            from avcardtool.notifications.events import build_firmware_update_failed_event
            notify_manager.notify(build_firmware_update_failed_event(issues))
    except Exception:
        log.exception("Could not send the firmware failure notification")
    return issues


def _run(cfg, device: Optional[Path], log: logging.Logger, notify_manager, issues: List[Issue],
         restored: Set[str]) -> None:
    fw: FirmwareConfig = getattr(cfg, "firmware", None) or FirmwareConfig()
    if not fw.enabled:
        return
    data_dir = Path(cfg.system.data_dir)
    dry_run = cfg.system.dry_run
    if dry_run:
        log.info("DRY RUN: firmware plan only — nothing is downloaded, written or announced")

    # 1. Releases and the cache
    state = FirmwareState(data_dir)   # read-only in a dry run: save() is never called
    packages: Dict[str, PreparedPackage] = {}
    for product in PRODUCTS.values():
        if not fw.product_enabled(product.key):
            continue
        _release, pkg = refresh_product(cfg, product, state, notify_manager, log, issues, download=not dry_run)
        if pkg:
            packages[product.key] = pkg

    if not dry_run:
        cycle.send_due_notifications(cfg, state, notify_manager, log)

    if not fw.auto_install:
        log.info("Firmware: auto_install is off — cards are not touched")
        return
    if not packages:
        log.info("Firmware: no cached package to install")
        return

    # 2. Cards
    detector = SDCardDetector()
    all_cards = detector.scan_for_cards()
    cards = [c for c in all_cards if c.device_path == str(device)] if device else all_cards
    if not cards:
        log.info("Firmware: no SD cards found.")
        return

    mounts = MountTracker(detector, log)
    try:
        for card in cards:
            _process_card(cfg, card, packages, mounts, notify_manager, log, issues, dry_run,
                          state, restored)
    finally:
        mounts.release()


def _handle_cycle(cfg, product: Product, pkg: PreparedPackage, decision, card, dev: Path, mount: Path,
                  serial: str, mounts: MountTracker, state: FirmwareState, notify_manager,
                  log: logging.Logger, issues: List[Issue], dry_run: bool) -> None:
    """One-card cycle step for a database card (start, resume or just report)."""
    log.info(f"  {product.name} {pkg.version}: {decision.action} — {decision.reason}")
    if decision.action not in ("start", "resume"):
        return
    if decision.action == "resume" and pkg.version != decision.version:
        pkg = next((c for c in list_cached(cfg.system.data_dir, product.key)
                    if c.version == decision.version), None)
        if pkg is None:
            msg = f"{product.name} {decision.version} is pending on a card but no longer cached"
            log.error(f"  {msg}")
            issues.append(("ERROR", msg))
            return
    if dry_run:
        log.info(f"  DRY RUN {product.name}: would back up the identity files, wipe the card by deleting "
                 f"files, write the {pkg.version} loader and ask for confirmation")
        return
    try:
        write_mount = mounts.writable(dev, mount)
        n = cycle.run_cycle(cfg, product, pkg, write_mount, serial, state, notify_manager, log,
                            resume=(decision.action == "resume"))
        log.info(f"  {product.name}: wrote {n} file(s); waiting for the pilot's confirmation")
    except (FirmwareError, RuntimeError) as e:
        msg = f"{product.name} {pkg.version} on {_card_label(card, mount)}: {e}"
        log.error(f"  {msg}")
        issues.append(("ERROR", msg))


def _process_card(cfg, card, packages: Dict[str, PreparedPackage], mounts: MountTracker,
                  notify_manager, log: logging.Logger, issues: List[Issue], dry_run: bool,
                  state: Optional[FirmwareState] = None, restored: Optional[Set[str]] = None) -> None:
    from avcardtool.notifications.events import build_firmware_card_written_event

    fw: FirmwareConfig = cfg.firmware
    data_dir = Path(cfg.system.data_dir)
    dev = Path(card.device_path)
    try:
        mount = mounts.inspect(dev, card.mount_point)
    except RuntimeError as e:
        log.warning(f"Firmware: could not mount {dev} to inspect it: {e}")
        return

    log.info(f"Firmware: inspecting card {_card_label(card, mount)}")
    plans: List[Tuple[Product, PreparedPackage, CardPlan]] = []
    serial = (card.volume_id or "").strip()
    for key, pkg in packages.items():
        product = PRODUCTS[key]
        if state is not None and product.cycle_capable:
            decision = cycle.decide(product, pkg, mount, serial, state, fw, restored or set())
            if decision.action != "n/a":
                _handle_cycle(cfg, product, pkg, decision, card, dev, mount, serial, mounts, state,
                              notify_manager, log, issues, dry_run)
                continue
        try:
            plan = plan_for_card(product, pkg, mount, remove_after_install=fw.remove_after_install,
                                 cached=list_cached(data_dir, key) or [pkg])
        except FirmwareError as e:
            log.warning(f"  {product.name}: {e}")
            continue
        log.info(f"  {product.name} {pkg.version}: {plan.action} — {plan.reason}")
        if plan.writes:
            plans.append((product, pkg, plan))

    if not plans:
        return
    if dry_run:
        for product, _pkg, plan in plans:
            for line in describe_plan(plan):
                log.info(f"  DRY RUN {product.name}: {line}")
        return

    try:
        write_mount = mounts.writable(dev, mount)
    except RuntimeError as e:
        msg = f"Mount failed for {dev}: {e}"
        log.error(f"  {msg}")
        issues.append(("ERROR", msg))
        return

    for product, pkg, plan in plans:
        plan.card = write_mount
        try:
            n = apply_plan(plan, pkg)
        except FirmwareError as e:
            msg = f"{product.name} {pkg.version} on {_card_label(card, write_mount)}: {e}"
            log.error(f"  {msg}")
            issues.append(("ERROR", msg))
            continue
        if plan.action == "cleanup":
            log.info(f"  {product.name}: removed {n} update file(s) from {write_mount}")
            continue
        log.info(f"  {product.name}: wrote {n} file(s), version {pkg.version}, to {write_mount}")
        if notify_manager:
            notify_manager.notify(build_firmware_card_written_event(
                product_name=product.name, version=pkg.version,
                card=_card_label(card, write_mount), file_count=n,
                next_steps=list(product.next_steps), installed_version=plan.installed_version,
            ))


def run_restore_pass(cfg, device: Optional[Path], log: logging.Logger) -> Tuple[Optional[MountTracker], Set[str]]:
    """
    Pre-step of the navdata run: for every present card whose firmware cycle was confirmed
    or cancelled, remove the loader files and put the identity files back, so navdata
    then reinstalls the databases. Never raises.

    Returns (tracker, serials restored). The tracker is NOT released: the caller unmounts
    after navdata has run, so a card we mounted stays mounted for it.
    """
    restored: Set[str] = set()
    tracker: Optional[MountTracker] = None
    try:
        data_dir = Path(cfg.system.data_dir)
        state = FirmwareState(data_dir)
        if not state.restore_items():
            return None, restored
        dry_run = cfg.system.dry_run
        detector = SDCardDetector()
        cards = detector.scan_for_cards()
        if device:
            cards = [c for c in cards if c.device_path == str(device)]
        tracker = MountTracker(detector, log)
        for card in cards:
            serial = (card.volume_id or "").strip()
            items = state.restore_items(serial) if serial else []
            if not items:
                continue
            dev = Path(card.device_path)
            try:
                mount = tracker.inspect(dev, card.mount_point)
            except RuntimeError as e:
                log.warning(f"Firmware restore: could not mount {dev}: {e}")
                continue
            for _s, key, entry in items:
                try:
                    if _restore_card(cfg, state, tracker, dev, mount, serial, key, entry, log, dry_run):
                        restored.add(serial)
                except Exception as e:
                    log.error(f"Firmware restore failed: {e}")
    except Exception:
        log.exception("Firmware restore pre-step failed")
    return tracker, restored


def _restore_card(cfg, state: FirmwareState, tracker: MountTracker, dev: Path, mount: Path,
                  serial: str, key: str, entry: dict, log: logging.Logger, dry_run: bool) -> bool:
    product = PRODUCTS[key]
    marker = read_loader_marker(mount)
    has_loader = bool(marker and product.name in marker)
    signs = database_card_signs(mount)
    backup = Path((entry.get("restore") or {}).get("backup_dir") or "")
    if signs:
        log.info(f"Firmware restore: card already has database files ({', '.join(signs)}) — not erasing")
        if not dry_run:
            state.clear_restore(serial, key)
            state.save()
        return False
    log.info(f"Firmware restore: {product.name} loader {'found' if has_loader else 'not found'} on the card"
             + (" — would erase it and put the identity files back" if dry_run else ""))
    if dry_run:
        return False
    wm = tracker.writable(dev, mount)
    if has_loader:
        _erase_card_root(wm)
    if backup.is_dir():
        done = cycle.restore_identity(wm, backup)
        log.info(f"  Restored {', '.join(done) or 'nothing (already present)'}; navdata reinstalls the databases next")
        shutil.rmtree(backup.parent, ignore_errors=True)
    else:
        log.warning("  No backup of the identity files (manual flow): nothing written back; "
                    "avionics.txt may be needed for navdata to recognise this card")
    os.sync()
    state.clear_restore(serial, key)
    state.save()
    return True
