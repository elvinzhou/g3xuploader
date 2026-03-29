"""
Core functionality shared across aviation tools modules.
"""

from avcardtool.core.config import (
    Config,
    EngineTimeConfig,
    AirframeTimeConfig,
    OOOIConfig,
    FlightDetectionConfig,
    FlightDataConfig,
    NavdataConfig,
    SystemConfig,
)
from avcardtool.core.utils import (
    setup_logging,
    hash_file,
    is_sd_card,
    get_mount_point,
    resolve_device_mount_point,
    mount_device,
    unmount_device,
    format_duration,
    parse_duration,
)
from avcardtool.core.processed_files import ProcessedFilesDatabase

__all__ = [
    "Config",
    "EngineTimeConfig",
    "AirframeTimeConfig",
    "OOOIConfig",
    "FlightDetectionConfig",
    "FlightDataConfig",
    "NavdataConfig",
    "SystemConfig",
    "setup_logging",
    "hash_file",
    "is_sd_card",
    "get_mount_point",
    "resolve_device_mount_point",
    "mount_device",
    "unmount_device",
    "format_duration",
    "parse_duration",
    "ProcessedFilesDatabase",
]
