"""
Card inspection and firmware installs.

Two package kinds, both generic over the product registry:

  gcd_folder  copy image/Garmin/* into <card>/Garmin/ (the display asks the
              pilot to confirm the update when powered up with the card).
  copy2card   a GTN-style loader card: the whole image tree is the card root,
              replacing everything on it (Copy2Card's EraseDestination).

The automatic path is deliberately narrow: a loader card is only ever
refreshed if it already is one, and a database/log card is never touched.
"""

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from avcardtool.firmware.catalog import compare_versions
from avcardtool.firmware.packages import (
    ImageFile,
    PreparedPackage,
    check_member_name,
    invalidate_package,
)
from avcardtool.firmware.registry import KIND_COPY2CARD, KIND_GCD_FOLDER, FirmwareError, Product

logger = logging.getLogger(__name__)

LOADER_MARKER = "ldr_part_nmbr.txt"

# Files/dirs that mean "this is a navigation database or flight-log card".
DATABASE_CARD_SIGNS = (
    "feat_unlk.dat",
    ".gadm.meta",
    "ldr_sys/avtn_db.bin",
    "data_log",
    ".navdata_cycles.json",
    ".g3x_db_metadata.json",
    "avionics.txt",
)

# Entries a freshly formatted card may carry; they do not make it "non-empty".
_JUNK_ENTRIES = {"system volume information", "$recycle.bin", ".trash-1000", ".fseventsd",
                 ".spotlight-v100", "found.000", "lost+found"}

_COPY_CHUNK = 1024 * 1024
_FILE_OVERHEAD = 32 * 1024     # slack per file for FAT cluster rounding
_SPACE_MARGIN = 4 * 1024 * 1024


# ---------------------------------------------------------------------------
# Reading cards
# ---------------------------------------------------------------------------

@dataclass
class LogInfo:
    product: str
    software_version: str
    log_name: str


def read_log_info(card: Path) -> Optional[LogInfo]:
    """Product and software version from the first line of the card's newest flight log."""
    log_dir = card / "data_log"
    try:
        logs = sorted(p.name for p in log_dir.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    except OSError:
        return None
    if not logs:
        return None
    newest = logs[-1]
    try:
        with open(log_dir / newest, "r", errors="replace") as f:
            first = f.readline(8192)
    except OSError:
        return None
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', first))
    if "product" not in attrs or "software_version" not in attrs:
        return None
    return LogInfo(attrs["product"], attrs["software_version"], newest)


def read_loader_marker(card: Path) -> Optional[str]:
    try:
        return (card / LOADER_MARKER).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def database_card_signs(card: Path) -> List[str]:
    return [s for s in DATABASE_CARD_SIGNS if (card / s).exists()]


def _card_entries(card: Path) -> List[Path]:
    try:
        return [p for p in card.iterdir() if p.name.lower() not in _JUNK_ENTRIES]
    except OSError:
        return []


def _tree_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for n in files:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                pass
    return total


def _gpn(text: str) -> Optional[tuple]:
    """Trailing number of a loader card's 'GPN 006-B3191-17' line, as a tuple."""
    m = re.search(r"GPN\s+\S*?-(\d+)\s*$", text, re.M)
    return (int(m.group(1)),) if m else None


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

@dataclass
class CardPlan:
    product_key: str
    version: str
    card: Path
    action: str                       # install | current | skip | cleanup
    reason: str
    installed_version: Optional[str] = None
    copy: List[ImageFile] = field(default_factory=list)
    erase: bool = False               # delete everything at the card root first
    remove: List[str] = field(default_factory=list)   # card-relative files to delete

    @property
    def writes(self) -> bool:
        return self.action in ("install", "cleanup")


def _differs(card: Path, f: ImageFile) -> bool:
    p = card / f.path
    try:
        return not p.is_file() or p.stat().st_size != f.size
    except OSError:
        return True


def plan_gcd_folder(product: Product, pkg: PreparedPackage, card: Path, *, manual: bool,
                    remove_after_install: bool, cached: List[PreparedPackage], **_ignored) -> CardPlan:
    base = dict(product_key=product.key, version=pkg.version, card=card)

    if manual:
        if (card / LOADER_MARKER).exists():
            raise FirmwareError(f"{card} is a loader card ({LOADER_MARKER}); refusing to add a Garmin/ folder to it")
        info = read_log_info(card)
        installed = info.software_version if info else None
        return CardPlan(**base, action="install", installed_version=installed,
                        reason="manual install", copy=list(pkg.files))

    info = read_log_info(card)
    if info is None:
        return CardPlan(**base, action="skip", reason="no flight logs on the card (installed version unknown)")
    if not (product.log_product_prefix and info.product.startswith(product.log_product_prefix)):
        return CardPlan(**base, action="skip", reason=f"flight logs are from '{info.product}', not {product.name}",
                        installed_version=info.software_version)
    try:
        newer = compare_versions(info.software_version, pkg.version) < 0
    except FirmwareError:
        return CardPlan(**base, action="skip", reason=f"unreadable installed version {info.software_version!r}")

    if newer:
        todo = [f for f in pkg.files if _differs(card, f)]
        if not todo:
            return CardPlan(**base, action="current", installed_version=info.software_version,
                            reason=f"already on card (display runs {info.software_version}, package is {pkg.version})")
        return CardPlan(**base, action="install", installed_version=info.software_version,
                        reason=f"display last logged {info.software_version}, {pkg.version} is available",
                        copy=todo)

    stale = []
    if remove_after_install:
        ours = {(Path(f.path).name, f.size) for c in cached for f in c.files}
        garmin_dir = card / "Garmin"
        try:
            for p in sorted(garmin_dir.iterdir()):
                if p.is_file() and (p.name, p.stat().st_size) in ours:
                    stale.append(f"Garmin/{p.name}")
        except OSError:
            pass
    if stale:
        return CardPlan(**base, action="cleanup", installed_version=info.software_version, remove=stale,
                        reason=f"display runs {info.software_version}; removing the update files copied earlier")
    return CardPlan(**base, action="current", installed_version=info.software_version,
                    reason=f"display runs {info.software_version}")


def plan_copy2card(product: Product, pkg: PreparedPackage, card: Path, *, manual: bool,
                   erase_ok: bool = False, **_ignored) -> CardPlan:
    base = dict(product_key=product.key, version=pkg.version, card=card)
    marker = read_loader_marker(card)
    is_loader = marker is not None and product.name in marker
    signs = database_card_signs(card)
    image_marker = pkg.file(LOADER_MARKER)
    image_marker_bytes = (pkg.image_dir / LOADER_MARKER).read_bytes() if image_marker else b""

    if manual:
        if signs and not erase_ok:
            raise FirmwareError(
                f"{card} looks like a database/log card ({', '.join(signs)}); a {product.name} loader card "
                f"must be a separate, dedicated card. Use --erase to wipe this card anyway."
            )
        if not is_loader and _card_entries(card) and not erase_ok:
            raise FirmwareError(
                f"{card} is not empty and is not a {product.name} loader card; use --erase to wipe it."
            )
    else:
        if not is_loader:
            return CardPlan(**base, action="skip", reason=f"not a {product.name} loader card")
        if signs:
            return CardPlan(**base, action="skip",
                            reason=f"has database/log files ({', '.join(signs)}); never touched automatically")

    installed = None
    if marker:
        installed = " ".join(marker.split())
    up_to_date = (
        is_loader
        and marker.encode("utf-8", errors="replace") == image_marker_bytes
        and not any(_differs(card, f) for f in pkg.files)
    )
    if up_to_date:
        return CardPlan(**base, action="current", installed_version=installed, reason="loader card already current")

    if is_loader and image_marker:
        card_gpn, image_gpn = _gpn(marker), _gpn(image_marker_bytes.decode("utf-8", errors="replace"))
        if card_gpn and image_gpn and card_gpn > image_gpn:
            return CardPlan(**base, action="skip", installed_version=installed,
                            reason="loader card has newer software than the cached package; not downgrading")

    return CardPlan(**base, action="install", installed_version=installed, copy=list(pkg.files),
                    erase=pkg.erase_destination,
                    reason="loader card differs from the package" if is_loader else "creating loader card")


PLANNERS: Dict[str, Callable[..., CardPlan]] = {
    KIND_GCD_FOLDER: plan_gcd_folder,
    KIND_COPY2CARD: plan_copy2card,
}


def plan_for_card(product: Product, pkg: PreparedPackage, card: Path, *, manual: bool = False,
                  erase_ok: bool = False, remove_after_install: bool = True,
                  cached: Optional[List[PreparedPackage]] = None) -> CardPlan:
    planner = PLANNERS.get(product.kind)
    if planner is None:
        raise FirmwareError(f"No card installer for package kind {product.kind}")
    return planner(product, pkg, card, manual=manual, erase_ok=erase_ok,
                   remove_after_install=remove_after_install, cached=cached or [pkg])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def describe_plan(plan: CardPlan) -> List[str]:
    lines = [f"{plan.action}: {plan.reason}"]
    if plan.action == "install":
        total = sum(f.size for f in plan.copy)
        lines.append(f"would copy {len(plan.copy)} file(s), {total / 1e6:,.1f} MB"
                     + (" after erasing everything on the card" if plan.erase else ""))
    if plan.remove:
        lines.append("would delete: " + ", ".join(plan.remove))
    return lines


def _write_order(files: List[ImageFile]) -> List[ImageFile]:
    # The loader marker goes last so a half-written card never looks like a valid loader card.
    return sorted(files, key=lambda f: (f.path.lower() == LOADER_MARKER, f.path))


def _copy_verified(src: Path, dest: Path, f: ImageFile) -> None:
    tmp = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            for chunk in iter(lambda: fin.read(_COPY_CHUNK), b""):
                h.update(chunk)
                fout.write(chunk)
            fout.flush()
            os.fsync(fout.fileno())
        if h.hexdigest() != f.sha256:
            raise FirmwareError(f"Cached file {f.path} does not match its recorded checksum (cache corrupt)")
        os.replace(tmp, dest)
        # Read back from the card (dropping the page cache first where possible).
        rb = hashlib.sha256()
        with open(dest, "rb") as fin:
            try:
                os.posix_fadvise(fin.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            except (AttributeError, OSError):
                pass
            for chunk in iter(lambda: fin.read(_COPY_CHUNK), b""):
                rb.update(chunk)
        if dest.stat().st_size != f.size or rb.hexdigest() != f.sha256:
            raise FirmwareError(f"Verification failed after writing {dest} (size or sha256 mismatch)")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _erase_card_root(card: Path) -> None:
    for entry in card.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _check_space(plan: CardPlan, card: Path) -> None:
    needed = sum(f.size + _FILE_OVERHEAD for f in plan.copy) + _SPACE_MARGIN
    free = shutil.disk_usage(card).free
    if plan.erase:
        free += _tree_size(card)
    else:
        for f in plan.copy:
            p = card / f.path
            if p.is_file():
                free += p.stat().st_size
    if free < needed:
        raise FirmwareError(
            f"Not enough free space on {card}: need about {needed / 1e6:,.0f} MB, have {free / 1e6:,.0f} MB"
        )


def apply_plan(plan: CardPlan, pkg: PreparedPackage, *, dry_run: bool = False) -> int:
    """
    Carry out an install or cleanup plan. Returns the number of files written or removed.

    Raises FirmwareError on any failure. In dry-run mode nothing is written.
    """
    card = plan.card
    if not plan.writes:
        return 0
    if dry_run:
        for line in describe_plan(plan):
            logger.info(f"  DRY RUN: {line}")
        return 0

    try:
        if plan.action == "cleanup":
            n = 0
            for rel in plan.remove:
                check_member_name(rel)
                (card / rel).unlink(missing_ok=True)
                n += 1
            os.sync()
            return n

        _check_space(plan, card)
        if plan.erase:
            logger.info(f"  Erasing {card} (package EraseDestination=true)")
            _erase_card_root(card)
        for f in _write_order(plan.copy):
            check_member_name(f.path)
            logger.info(f"  Writing {f.path} ({f.size:,} bytes)")
            _copy_verified(pkg.image_dir / f.path, card / f.path, f)
        os.sync()
        return len(plan.copy)
    except FirmwareError as e:
        if "cache corrupt" in str(e):
            invalidate_package(pkg)
        raise
    except OSError as e:
        raise FirmwareError(f"Writing to {card} failed: {e}")
