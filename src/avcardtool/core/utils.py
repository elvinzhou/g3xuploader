"""
Common utilities used across aviation tools modules.
"""

import hashlib
import logging
import sys
from pathlib import Path
from typing import Optional
import subprocess

logger = logging.getLogger(__name__)


def setup_logging(log_file: Optional[str] = None, log_level: str = "INFO") -> None:
    """
    Set up logging configuration for aviation tools.

    Args:
        log_file: Optional path to log file. If None, only logs to console.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatters
    # Detailed format for files, slightly cleaner for console
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler
    if log_file:
        try:
            log_path = Path(log_file).expanduser()
            log_path.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_path)
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)

            logging.info(f"Logging initialized. Level: {log_level}, File: {log_path}")
        except Exception as e:
            logging.debug(f"Could not initialize file logging at {log_file}: {e}. Falling back to console only.")
    else:
        logging.info(f"Logging initialized. Level: {log_level}, Console only.")


def hash_file(file_path: Path, algorithm: str = "sha256") -> str:
    """
    Calculate hash of a file.

    Args:
        file_path: Path to file
        algorithm: Hash algorithm (md5, sha1, sha256, etc.)

    Returns:
        Hexadecimal hash string
    """
    hasher = hashlib.new(algorithm)

    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)

    return hasher.hexdigest()


def is_sd_card(device_path: Path) -> bool:
    """
    Check if a device is likely an SD card.

    Args:
        device_path: Path to device (e.g., /dev/sda1)

    Returns:
        True if device appears to be an SD card
    """
    # Check if it's a removable device
    try:
        # Extract device name (e.g., sda from sda1)
        device_name = device_path.name.rstrip('0123456789')
        removable_path = Path(f"/sys/block/{device_name}/removable")

        if removable_path.exists():
            with open(removable_path, 'r') as f:
                is_removable = f.read().strip() == '1'
                return is_removable
    except Exception:
        pass

    return False


def get_mount_point(device_path: Path) -> Optional[Path]:
    """
    Get the mount point for a device.

    Args:
        device_path: Path to device (e.g., /dev/sda1)

    Returns:
        Mount point path if mounted, None otherwise
    """
    try:
        result = subprocess.run(
            ['findmnt', '-n', '-o', 'TARGET', str(device_path)],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass

    return None


def _is_mounted_readonly(device_path: Path) -> bool:
    """Return True if the device is currently mounted read-only."""
    try:
        result = subprocess.run(
            ['findmnt', '-n', '-o', 'OPTIONS', str(device_path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            opts = result.stdout.strip().split(',')
            return 'ro' in opts
    except Exception:
        pass
    return False


def resolve_device_mount_point(device_path: Path, readonly: bool = True) -> Path:
    """
    Resolve a block device to its mount point, waiting briefly for udisks2 if needed.

    1. Checks if the device is already mounted.
    2. If not, waits 2 seconds (udisks2 may still be mounting) and retries.
    3. If still not mounted, mounts it via udisksctl (no root required).

    When readonly=False and the device is already mounted read-only (e.g. udisks2
    auto-mounted a FAT volume with a dirty bit), the existing mount is unmounted
    and the device is remounted read-write.

    Args:
        device_path: Block device path (e.g., /dev/sda1)
        readonly: Mount read-only if we have to mount it ourselves

    Returns:
        Mount point Path

    Raises:
        RuntimeError: If the device cannot be mounted
    """
    import time as _time

    def _check_and_fix(mount: Path) -> Path:
        if not readonly and _is_mounted_readonly(device_path):
            logger.info(
                f"{device_path} is mounted read-only but write access is needed — "
                f"remounting read-write"
            )
            unmount_device(mount)
            return mount_device(device_path, readonly=False)
        return mount

    mount = get_mount_point(device_path)
    if mount:
        return _check_and_fix(mount)

    _time.sleep(2)
    mount = get_mount_point(device_path)
    if mount:
        return _check_and_fix(mount)

    return mount_device(device_path, readonly=readonly)


def mount_device(device_path: Path, mount_point: Optional[Path] = None, readonly: bool = True) -> Path:
    """
    Mount a device, preferring udisksctl (no root required) over mount.

    Args:
        device_path: Path to device (e.g., /dev/sda1)
        mount_point: Optional explicit mount point (only used with the mount fallback).
        readonly: Mount read-only if True

    Returns:
        Path where device was mounted

    Raises:
        RuntimeError: If mount fails
    """
    # Prefer udisksctl: works as a regular user, lets udisks2 pick the mount point
    udisksctl_cmd = ['udisksctl', 'mount', '--block-device', str(device_path)]
    if readonly:
        udisksctl_cmd.extend(['--options', 'ro'])
    result = subprocess.run(udisksctl_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        # Output: "Mounted /dev/sde1 at /media/user/LABEL."
        import re
        m = re.search(r'at\s+(\S+?)\.?$', result.stdout.strip())
        if m:
            return Path(m.group(1))
        logger.warning(f"udisksctl mount succeeded but could not parse mount point from: {result.stdout.strip()!r}")
    else:
        logger.warning(
            f"udisksctl mount failed for {device_path} (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )

    # Fallback: direct mount (requires root / appropriate permissions)
    if mount_point is None:
        mount_point = Path(f"/media/{device_path.name}")

    mount_point.mkdir(parents=True, exist_ok=True)

    mount_cmd = ['mount']
    if readonly:
        mount_cmd.extend(['-o', 'ro'])
    mount_cmd.extend([str(device_path), str(mount_point)])

    result = subprocess.run(mount_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to mount {device_path}: {result.stderr}")

    return mount_point


def unmount_device(mount_point: Path) -> None:
    """
    Unmount a device, preferring udisksctl (no root required) over umount.

    Args:
        mount_point: Path to mount point

    Raises:
        RuntimeError: If unmount fails
    """
    result = subprocess.run(
        ['udisksctl', 'unmount', '--mount-point', str(mount_point)],
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        return

    # Fallback: direct umount (requires root)
    result = subprocess.run(
        ['umount', str(mount_point)],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to unmount {mount_point}: {result.stderr}")


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds as human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string (e.g., "2h 30m", "45m 30s")
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def parse_duration(duration_str: str) -> float:
    """
    Parse human-readable duration to seconds.

    Args:
        duration_str: Duration string (e.g., "2h 30m", "45m", "1.5h")

    Returns:
        Duration in seconds
    """
    duration_str = duration_str.lower().strip()
    seconds = 0.0

    # Try to parse as decimal hours (e.g., "1.5h")
    if 'h' in duration_str and ' ' not in duration_str:
        try:
            hours = float(duration_str.replace('h', ''))
            return hours * 3600
        except ValueError:
            pass

    # Parse components
    parts = duration_str.split()
    for part in parts:
        if 'h' in part:
            seconds += float(part.replace('h', '')) * 3600
        elif 'm' in part:
            seconds += float(part.replace('m', '')) * 60
        elif 's' in part:
            seconds += float(part.replace('s', ''))

    return seconds
