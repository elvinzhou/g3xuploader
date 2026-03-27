"""
Unified configuration management for aviation tools.

Handles loading, saving, and validating configuration for both flight data
processing and navigation database management.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class EngineTimeConfig:
    """
    Engine Time (Tach Time) Configuration

    mode: "variable" or "fixed"
    - variable: Time accrues at (RPM / reference_rpm) rate
    - fixed: Time accrues at 1:1 with clock when RPM > minimum_recording_rpm
    """
    mode: str = "variable"
    minimum_recording_rpm: int = 500
    reference_rpm: int = 2700


@dataclass
class AirframeTimeConfig:
    """
    Total Airframe Time (Hobbs) Configuration

    trigger: "rpm", "oil_pressure", or "flight_time"
    """
    trigger: str = "oil_pressure"
    rpm_threshold: int = 500
    oil_pressure_threshold: float = 5.0
    airborne_speed_threshold: float = 50.0


@dataclass
class OOOIConfig:
    """Out/Off/On/In Detection Configuration"""
    engine_start_rpm: int = 500
    engine_start_oil_psi: float = 10.0
    takeoff_speed_kts: float = 50.0
    landing_speed_kts: float = 50.0
    engine_stop_rpm: int = 300  # RPM threshold for engine shutdown detection


@dataclass
class FlightDetectionConfig:
    """Configuration for determining if a log contains an actual flight"""
    minimum_flight_time_minutes: float = 5.0
    minimum_ground_speed_kts: float = 50.0
    minimum_altitude_change_ft: float = 200.0
    minimum_data_points: int = 300


@dataclass
class UploaderConfig:
    """Individual uploader configuration"""
    enabled: bool = False
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FlightDataConfig:
    """Flight data processing configuration"""
    enabled: bool = True
    engine_time: EngineTimeConfig = field(default_factory=EngineTimeConfig)
    airframe_time: AirframeTimeConfig = field(default_factory=AirframeTimeConfig)
    oooi: OOOIConfig = field(default_factory=OOOIConfig)
    flight_detection: FlightDetectionConfig = field(default_factory=FlightDetectionConfig)
    uploaders: Dict[str, UploaderConfig] = field(default_factory=dict)


@dataclass
class NavdataConfig:
    """Navigation database configuration"""
    enabled: bool = True
    auto_download: bool = False
    garmin: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemConfig:
    """System-level configuration"""
    data_dir: str = str(Path.home() / ".local" / "share" / "avcardtool")
    log_file: str = str(Path.home() / ".local" / "share" / "avcardtool" / "avcardtool.log")
    log_level: str = "INFO"
    debug: bool = False


class Config:
    """
    Unified configuration manager for aviation tools.

    Handles loading, saving, validation, and migration of configuration files.
    """

    DEFAULT_CONFIG_PATHS = [
        Path("/etc/avcardtool/config.json"),
        Path.home() / ".config" / "avcardtool" / "config.json",
        Path.home() / ".avcardtool" / "config.json",
    ]

    LEGACY_PATHS = [
        Path("/etc/aviation_tools/config.json"),
        Path("/etc/g3x_processor/config.json"),
    ]

    @staticmethod
    def get_base_dir() -> Path:
        """Get the base directory of the application, handling standalone binary mode."""
        import sys
        if getattr(sys, 'frozen', False):
            # Running as a bundled binary (Nuitka/PyInstaller)
            return Path(sys.executable).parent
        return Path(__file__).parent.parent.parent

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration.

        Args:
            config_path: Optional path to config file. If not provided, searches default locations.
        """
        self.config_path = config_path or self._find_config()
        self.flight_data = FlightDataConfig()
        self.navdata = NavdataConfig()
        self.system = SystemConfig()

        if self.config_path and self.config_path.exists():
            self.load()
        else:
            logger.info(f"No config file found, using defaults")

    def _find_config(self) -> Optional[Path]:
        """Search for config file in default locations."""
        # Check default paths
        for path in self.DEFAULT_CONFIG_PATHS:
            if path.exists():
                logger.info(f"Found config at {path}")
                return path

        # Check legacy paths
        for path in self.LEGACY_PATHS:
            if path.exists():
                logger.info(f"Found legacy config at {path}, will migrate")
                return path

        return None

    def load(self, path: Optional[Path] = None) -> None:
        """
        Load configuration from file.

        Args:
            path: Optional path to load from. Uses self.config_path if not provided.
        """
        load_path = path or self.config_path
        if not load_path or not load_path.exists():
            raise FileNotFoundError(f"Config file not found: {load_path}")

        logger.info(f"Loading config from {load_path}")

        with open(load_path, 'r') as f:
            data = json.load(f)

        # Check if this is a legacy config and migrate if needed
        if self._is_legacy_config(data):
            logger.info("Detected legacy config format, migrating...")
            data = self._migrate_legacy_config(data)

        # Load flight data config
        if "flight_data" in data:
            fd = data["flight_data"]
            if "engine_time" in fd:
                self.flight_data.engine_time = EngineTimeConfig(**fd["engine_time"])
            if "airframe_time" in fd:
                self.flight_data.airframe_time = AirframeTimeConfig(**fd["airframe_time"])
            if "oooi" in fd:
                self.flight_data.oooi = OOOIConfig(**fd["oooi"])
            if "flight_detection" in fd:
                self.flight_data.flight_detection = FlightDetectionConfig(**fd["flight_detection"])
            if "uploaders" in fd:
                self.flight_data.uploaders = {
                    name: UploaderConfig(enabled=cfg.get("enabled", False), config=cfg)
                    for name, cfg in fd["uploaders"].items()
                }
            if "enabled" in fd:
                self.flight_data.enabled = fd["enabled"]

        # Load navdata config
        if "navdata" in data:
            nd = data["navdata"]
            self.navdata.enabled = nd.get("enabled", True)
            self.navdata.auto_download = nd.get("auto_download", False)
            self.navdata.garmin = nd.get("garmin", {})

        # Load system config
        if "system" in data:
            sys = data["system"]
            self.system.data_dir = sys.get("data_dir", self.system.data_dir)
            self.system.log_file = sys.get("log_file", self.system.log_file)
            self.system.log_level = sys.get("log_level", self.system.log_level)
            self.system.debug = sys.get("debug", self.system.debug)

    def save(self, path: Optional[Path] = None) -> None:
        """
        Save configuration to file.

        Args:
            path: Optional path to save to. Uses self.config_path if not provided.
        """
        save_path = path or self.config_path
        if not save_path:
            save_path = self.DEFAULT_CONFIG_PATHS[0]

        # Ensure directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)

        data = self.to_dict()

        logger.info(f"Saving config to {save_path}")
        with open(save_path, 'w') as f:
            json.dump(data, f, indent=2)

        self.config_path = save_path

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "flight_data": {
                "enabled": self.flight_data.enabled,
                "engine_time": asdict(self.flight_data.engine_time),
                "airframe_time": asdict(self.flight_data.airframe_time),
                "oooi": asdict(self.flight_data.oooi),
                "flight_detection": asdict(self.flight_data.flight_detection),
                "uploaders": {
                    name: cfg.config
                    for name, cfg in self.flight_data.uploaders.items()
                }
            },
            "navdata": {
                "enabled": self.navdata.enabled,
                "auto_download": self.navdata.auto_download,
                "garmin": self.navdata.garmin
            },
            "system": asdict(self.system)
        }

    def _is_legacy_config(self, data: Dict[str, Any]) -> bool:
        """Check if this is a legacy config format."""
        # Legacy configs have top-level keys like engine_time, airframe_time
        legacy_keys = ["engine_time", "airframe_time", "savvy_aviation", "cloudahoy", "flysto"]
        return any(key in data for key in legacy_keys)

    def _migrate_legacy_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Migrate legacy config format to new format."""
        new_config = {
            "flight_data": {
                "enabled": True,
                "uploaders": {}
            },
            "navdata": {
                "enabled": True,
                "auto_download": False,
                "garmin": {}
            },
            "system": {
                "data_dir": str(Path.home() / ".local" / "share" / "avcardtool"),
                "log_file": str(Path.home() / ".local" / "share" / "avcardtool" / "avcardtool.log"),
                "log_level": "INFO"
            }
        }

        # Migrate flight data configs
        if "engine_time" in data:
            new_config["flight_data"]["engine_time"] = data["engine_time"]
        if "airframe_time" in data:
            new_config["flight_data"]["airframe_time"] = data["airframe_time"]
        if "oooi" in data:
            new_config["flight_data"]["oooi"] = data["oooi"]
        if "flight_detection" in data:
            new_config["flight_data"]["flight_detection"] = data["flight_detection"]

        # Migrate uploaders
        for uploader in ["savvy_aviation", "cloudahoy", "flysto", "maintenance_tracker"]:
            if uploader in data:
                new_config["flight_data"]["uploaders"][uploader] = data[uploader]

        # Migrate database updater config if present
        if "database_updater" in data:
            new_config["navdata"] = data["database_updater"]

        return new_config

    def validate(self) -> bool:
        """
        Validate configuration.

        Returns:
            True if configuration is valid, raises ValueError otherwise.
        """
        # Validate engine time config
        if self.flight_data.engine_time.mode not in ["variable", "fixed"]:
            raise ValueError(f"Invalid engine_time mode: {self.flight_data.engine_time.mode}")

        # Validate airframe time config
        if self.flight_data.airframe_time.trigger not in ["rpm", "oil_pressure", "flight_time"]:
            raise ValueError(f"Invalid airframe_time trigger: {self.flight_data.airframe_time.trigger}")

        # Validate system paths
        data_dir = Path(self.system.data_dir)
        if not data_dir.parent.exists():
            raise ValueError(f"Parent directory does not exist: {data_dir.parent}")

        return True

    @classmethod
    def generate_default(cls, path: Path) -> "Config":
        """
        Generate and save a default configuration file.

        Args:
            path: Path where to save the config

        Returns:
            Config instance with defaults
        """
        config = cls()
        
        # Enable debug by default for initial setup/debugging
        config.system.debug = True

        # Set some example uploader configs
        config.flight_data.uploaders = {
            "mock": UploaderConfig(
                enabled=True,
                config={"description": "Logs payloads for debugging"}
            ),
            "cloudahoy": UploaderConfig(
                enabled=False,
                config={"api_token": "your-oauth-token"}
            ),
            "flysto": UploaderConfig(
                enabled=False,
                config={
                    "client_id": "your-client-id",
                    "client_secret": "your-client-secret",
                    "redirect_uri": "http://localhost:8080/callback"
                }
            ),
            "savvy_aviation": UploaderConfig(
                enabled=False,
                config={
                    "email": "your-email@example.com",
                    "password": "your-password"
                }
            ),
            "maintenance_tracker": UploaderConfig(
                enabled=False,
                config={
                    "url": "https://your-tracker.com/api/flights",
                    "api_key": "your-api-key"
                }
            )
        }

        config.navdata.garmin = {
            "email": "your-flygarmin-email@example.com",
            "databases": ["navdata", "terrain", "obstacles"]
        }

        config.save(path)
        return config
