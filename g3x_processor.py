#!/usr/bin/env python3
"""
G3X Flight Data Processor

Automatically processes G3X SD card data, calculates Hobbs/Tach times,
detects OOOI times, and uploads to Savvy Aviation, CloudAhoy, and
maintenance trackers.

This script is designed to run on a Raspberry Pi and be triggered
automatically when a G3X SD card is inserted.
"""

import csv
import json
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import requests
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/g3x_processor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Data Classes
# =============================================================================

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
    engine_stop_rpm: int = 100


@dataclass
class FlightDetectionConfig:
    """Configuration for determining if a log contains an actual flight"""
    minimum_flight_time_minutes: float = 5.0  # Must be airborne for at least this long
    minimum_ground_speed_kts: float = 50.0    # Must exceed this speed
    minimum_altitude_change_ft: float = 200.0  # Must climb at least this much
    minimum_data_points: int = 300            # At least 5 minutes of data


@dataclass
class UploadConfig:
    """Upload destination configuration"""
    savvy_aviation: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "email": "",
        "password": ""
    })
    cloudahoy: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "api_token": ""
    })
    flysto: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "http://localhost:8080/callback"
    })
    maintenance_tracker: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "url": "",
        "api_key": ""
    })


@dataclass
class G3XConfig:
    """Complete configuration"""
    aircraft_ident: str = ""
    engine_time: EngineTimeConfig = field(default_factory=EngineTimeConfig)
    airframe_time: AirframeTimeConfig = field(default_factory=AirframeTimeConfig)
    oooi: OOOIConfig = field(default_factory=OOOIConfig)
    flight_detection: FlightDetectionConfig = field(default_factory=FlightDetectionConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    processed_files_db: str = "/var/lib/g3x_processor/processed_files.json"
    
    @classmethod
    def from_json(cls, path: str) -> 'G3XConfig':
        with open(path, 'r') as f:
            data = json.load(f)
        
        return cls(
            aircraft_ident=data.get('aircraft_ident', ''),
            engine_time=EngineTimeConfig(**data.get('engine_time', {})),
            airframe_time=AirframeTimeConfig(**data.get('airframe_time', {})),
            oooi=OOOIConfig(**data.get('oooi', {})),
            flight_detection=FlightDetectionConfig(**data.get('flight_detection', {})),
            upload=UploadConfig(
                savvy_aviation=data.get('upload', {}).get('savvy_aviation', {}),
                cloudahoy=data.get('upload', {}).get('cloudahoy', {}),
                flysto=data.get('upload', {}).get('flysto', {}),
                maintenance_tracker=data.get('upload', {}).get('maintenance_tracker', {})
            ),
            processed_files_db=data.get('processed_files_db', '/var/lib/g3x_processor/processed_files.json')
        )
    
    def to_json(self, path: str):
        data = {
            'aircraft_ident': self.aircraft_ident,
            'engine_time': asdict(self.engine_time),
            'airframe_time': asdict(self.airframe_time),
            'oooi': asdict(self.oooi),
            'flight_detection': asdict(self.flight_detection),
            'upload': {
                'savvy_aviation': self.upload.savvy_aviation,
                'cloudahoy': self.upload.cloudahoy,
                'flysto': self.upload.flysto,
                'maintenance_tracker': self.upload.maintenance_tracker
            },
            'processed_files_db': self.processed_files_db
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)


# =============================================================================
# Flight Analysis Data Classes
# =============================================================================

@dataclass
class FlightTimes:
    """Calculated flight times"""
    starting_hobbs: float
    starting_tach: float
    hobbs_increment: float
    tach_increment: float
    
    @property
    def ending_hobbs(self) -> float:
        return self.starting_hobbs + self.hobbs_increment
    
    @property
    def ending_tach(self) -> float:
        return self.starting_tach + self.tach_increment


@dataclass
class OOOITimes:
    """Out/Off/On/In times"""
    out_time: Optional[datetime] = None
    off_time: Optional[datetime] = None
    on_time: Optional[datetime] = None
    in_time: Optional[datetime] = None
    
    @property
    def block_time_minutes(self) -> Optional[float]:
        if self.out_time and self.in_time:
            return (self.in_time - self.out_time).total_seconds() / 60
        return None
    
    @property
    def flight_time_minutes(self) -> Optional[float]:
        if self.off_time and self.on_time:
            return (self.on_time - self.off_time).total_seconds() / 60
        return None


@dataclass
class FlightAnalysis:
    """Complete flight analysis results"""
    filename: str
    filepath: str
    aircraft_ident: str
    date: str
    is_flight: bool
    rejection_reason: Optional[str]
    times: Optional[FlightTimes]
    oooi: Optional[OOOITimes]
    data_points: int
    max_altitude: float
    max_ground_speed: float
    file_hash: str


# =============================================================================
# G3X Log Parser
# =============================================================================

class G3XLogParser:
    """Parser for G3X SD card CSV log files"""
    
    # Column indices based on G3X CSV format (0-indexed)
    COL_DATE = 0
    COL_TIME = 1
    COL_UTC_TIME = 2
    COL_LATITUDE = 4
    COL_LONGITUDE = 5
    COL_GPS_ALTITUDE = 6
    COL_GROUND_SPEED = 9
    COL_PRESSURE_ALT = 18
    COL_BARO_ALT = 19
    COL_RPM = 85
    COL_OIL_PRESS = 86
    COL_OIL_TEMP = 87
    
    def __init__(self, config: G3XConfig):
        self.config = config
    
    def parse_metadata(self, first_line: str) -> dict:
        """Parse the #airframe_info metadata line"""
        metadata = {}
        parts = first_line.strip().split(',')
        for part in parts[1:]:
            if '=' in part:
                key, value = part.split('=', 1)
                value = value.strip('"')
                metadata[key] = value
        return metadata
    
    def parse_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Parse date and time strings into datetime object"""
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    
    def safe_float(self, value: str, default: float = 0.0) -> float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    def safe_int(self, value: str, default: int = 0) -> int:
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default
    
    def compute_file_hash(self, filepath: str) -> str:
        """Compute SHA256 hash of file for deduplication"""
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def is_actual_flight(self, max_speed: float, max_alt: float, min_alt: float,
                         airborne_seconds: int, data_points: int) -> Tuple[bool, Optional[str]]:
        """
        Determine if this log represents an actual flight vs just a power-on cycle.
        Returns (is_flight, rejection_reason)
        """
        cfg = self.config.flight_detection
        
        if data_points < cfg.minimum_data_points:
            return False, f"Too few data points ({data_points} < {cfg.minimum_data_points})"
        
        airborne_minutes = airborne_seconds / 60
        if airborne_minutes < cfg.minimum_flight_time_minutes:
            return False, f"Airborne time too short ({airborne_minutes:.1f} < {cfg.minimum_flight_time_minutes} min)"
        
        if max_speed < cfg.minimum_ground_speed_kts:
            return False, f"Max ground speed too low ({max_speed:.1f} < {cfg.minimum_ground_speed_kts} kts)"
        
        altitude_change = max_alt - min_alt
        if altitude_change < cfg.minimum_altitude_change_ft:
            return False, f"Altitude change too small ({altitude_change:.0f} < {cfg.minimum_altitude_change_ft} ft)"
        
        return True, None
    
    def analyze(self, filepath: str) -> FlightAnalysis:
        """Analyze a G3X log file and return flight analysis"""
        
        file_hash = self.compute_file_hash(filepath)
        
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        if len(lines) < 4:
            return FlightAnalysis(
                filename=Path(filepath).name,
                filepath=filepath,
                aircraft_ident="Unknown",
                date="Unknown",
                is_flight=False,
                rejection_reason="File too short",
                times=None,
                oooi=None,
                data_points=0,
                max_altitude=0,
                max_ground_speed=0,
                file_hash=file_hash
            )
        
        # Parse metadata
        metadata = self.parse_metadata(lines[0])
        aircraft_ident = metadata.get('aircraft_ident', self.config.aircraft_ident or 'Unknown')
        starting_hobbs = float(metadata.get('airframe_hours', 0))
        starting_tach = float(metadata.get('engine_hours', 0))
        
        # Skip header rows
        data_start = 3
        
        # Initialize counters
        hobbs_seconds = 0.0
        tach_seconds = 0.0
        data_points = 0
        airborne_seconds = 0
        
        # Track altitude and speed
        max_ground_speed = 0.0
        max_altitude = -99999.0
        min_altitude = 99999.0
        
        # OOOI detection state
        oooi = OOOITimes()
        was_above_takeoff_speed = False
        engine_was_running = False
        prev_rpm = 0
        
        flight_date = "Unknown"
        
        # Process data rows
        for line in lines[data_start:]:
            if not line.strip():
                continue
            
            cols = line.strip().split(',')
            if len(cols) < self.COL_OIL_TEMP + 1:
                continue
            
            data_points += 1
            
            # Extract values
            date_str = cols[self.COL_DATE]
            time_str = cols[self.COL_TIME]
            ground_speed = self.safe_float(cols[self.COL_GROUND_SPEED])
            altitude = self.safe_float(cols[self.COL_BARO_ALT])
            rpm = self.safe_int(cols[self.COL_RPM])
            oil_press = self.safe_float(cols[self.COL_OIL_PRESS])
            
            if flight_date == "Unknown" and date_str:
                flight_date = date_str
            
            timestamp = self.parse_datetime(date_str, time_str)
            
            # Track min/max values
            if ground_speed > max_ground_speed:
                max_ground_speed = ground_speed
            if altitude > max_altitude:
                max_altitude = altitude
            if altitude < min_altitude and altitude > -1000:  # Filter out invalid altitudes
                min_altitude = altitude
            
            # Track airborne time (for flight detection)
            if ground_speed > self.config.flight_detection.minimum_ground_speed_kts:
                airborne_seconds += 1
            
            # === HOBBS (Airframe Time) Calculation ===
            hobbs_recording = False
            trigger = self.config.airframe_time.trigger
            
            if trigger == "rpm":
                hobbs_recording = rpm > self.config.airframe_time.rpm_threshold
            elif trigger == "oil_pressure":
                hobbs_recording = oil_press > self.config.airframe_time.oil_pressure_threshold
            elif trigger == "flight_time":
                hobbs_recording = ground_speed > self.config.airframe_time.airborne_speed_threshold
            
            if hobbs_recording:
                hobbs_seconds += 1.0
            
            # === TACH (Engine Time) Calculation ===
            if rpm >= self.config.engine_time.minimum_recording_rpm:
                if self.config.engine_time.mode == "fixed":
                    tach_seconds += 1.0
                else:  # variable
                    rate = rpm / self.config.engine_time.reference_rpm
                    tach_seconds += rate
            
            # === OOOI Detection ===
            if timestamp:
                engine_running = (rpm > self.config.oooi.engine_start_rpm and 
                                oil_press > self.config.oooi.engine_start_oil_psi)
                
                # OUT: Engine start
                if engine_running and not engine_was_running and oooi.out_time is None:
                    oooi.out_time = timestamp
                
                # OFF: Takeoff
                if ground_speed > self.config.oooi.takeoff_speed_kts:
                    if not was_above_takeoff_speed and oooi.off_time is None:
                        oooi.off_time = timestamp
                    was_above_takeoff_speed = True
                
                # ON: Landing
                if was_above_takeoff_speed and ground_speed < self.config.oooi.landing_speed_kts:
                    if ground_speed > 0:
                        oooi.on_time = timestamp
                        was_above_takeoff_speed = False
                
                # IN: Engine stop
                if rpm <= 10 and prev_rpm > self.config.oooi.engine_stop_rpm:
                    oooi.in_time = timestamp
                
                prev_rpm = rpm
                engine_was_running = engine_running
        
        # Check if this is an actual flight
        is_flight, rejection_reason = self.is_actual_flight(
            max_ground_speed, max_altitude, min_altitude, airborne_seconds, data_points
        )
        
        if not is_flight:
            return FlightAnalysis(
                filename=Path(filepath).name,
                filepath=filepath,
                aircraft_ident=aircraft_ident,
                date=flight_date,
                is_flight=False,
                rejection_reason=rejection_reason,
                times=None,
                oooi=None,
                data_points=data_points,
                max_altitude=max_altitude,
                max_ground_speed=max_ground_speed,
                file_hash=file_hash
            )
        
        # Convert seconds to hours
        hobbs_increment = hobbs_seconds / 3600.0
        tach_increment = tach_seconds / 3600.0
        
        return FlightAnalysis(
            filename=Path(filepath).name,
            filepath=filepath,
            aircraft_ident=aircraft_ident,
            date=flight_date,
            is_flight=True,
            rejection_reason=None,
            times=FlightTimes(
                starting_hobbs=starting_hobbs,
                starting_tach=starting_tach,
                hobbs_increment=hobbs_increment,
                tach_increment=tach_increment
            ),
            oooi=oooi,
            data_points=data_points,
            max_altitude=max_altitude,
            max_ground_speed=max_ground_speed,
            file_hash=file_hash
        )


# =============================================================================
# Upload Services
# =============================================================================

class SavvyAviationUploader:
    """Upload flight data to Savvy Aviation"""
    
    # Savvy Aviation uses web form upload, not a public API
    # This is a placeholder for the actual implementation
    UPLOAD_URL = "https://www.savvyaviation.com/upload/"
    
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get('enabled', False)
        self.email = config.get('email', '')
        self.password = config.get('password', '')
    
    def upload(self, filepath: str, analysis: FlightAnalysis) -> Tuple[bool, str]:
        """
        Upload a flight CSV to Savvy Aviation.
        
        Note: Savvy Aviation doesn't have a public API. This would need to either:
        1. Use browser automation (selenium)
        2. Reverse-engineer their form submission
        3. Use their official upload process
        
        For now, this copies to a staging folder for manual upload.
        """
        if not self.enabled:
            return False, "Savvy Aviation upload not enabled"
        
        # Stage the file for manual upload
        staging_dir = Path("/var/lib/g3x_processor/savvy_staging")
        staging_dir.mkdir(parents=True, exist_ok=True)
        
        dest_path = staging_dir / Path(filepath).name
        shutil.copy2(filepath, dest_path)
        
        logger.info(f"Staged {filepath} for Savvy Aviation upload at {dest_path}")
        return True, f"Staged for upload: {dest_path}"


class CloudAhoyUploader:
    """Upload flight data to CloudAhoy"""
    
    # CloudAhoy API endpoint
    API_URL = "https://www.cloudahoy.com/integration/v1/flights"
    
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get('enabled', False)
        self.api_token = config.get('api_token', '')
    
    def upload(self, filepath: str, analysis: FlightAnalysis) -> Tuple[bool, str]:
        """Upload a flight CSV to CloudAhoy"""
        if not self.enabled:
            return False, "CloudAhoy upload not enabled"
        
        if not self.api_token:
            return False, "CloudAhoy API token not configured"
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_token}"
            }
            
            metadata = {
                "importerVersion": "g3x_processor_v1.0",
                "tail": analysis.aircraft_ident
            }
            
            with open(filepath, 'rb') as f:
                files = {
                    'IMPORT': (Path(filepath).name, f, 'text/csv'),
                }
                data = {
                    'METADATA': json.dumps(metadata)
                }
                
                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=60
                )
            
            if response.status_code == 200:
                result = response.json()
                flight_key = result.get('key', '')
                debrief_url = f"https://www.cloudahoy.com/debrief/?key={flight_key}"
                logger.info(f"Successfully uploaded to CloudAhoy: {debrief_url}")
                return True, debrief_url
            else:
                error_msg = f"CloudAhoy upload failed: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return False, error_msg
                
        except Exception as e:
            error_msg = f"CloudAhoy upload error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg


class MaintenanceTrackerUploader:
    """Upload flight data to a custom maintenance tracker"""
    
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get('enabled', False)
        self.url = config.get('url', '')
        self.api_key = config.get('api_key', '')
    
    def upload(self, filepath: str, analysis: FlightAnalysis) -> Tuple[bool, str]:
        """Upload flight summary to maintenance tracker"""
        if not self.enabled:
            return False, "Maintenance tracker upload not enabled"
        
        if not self.url or not self.api_key:
            return False, "Maintenance tracker URL or API key not configured"
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "aircraft_ident": analysis.aircraft_ident,
                "date": analysis.date,
                "filename": analysis.filename,
                "hobbs": {
                    "start": analysis.times.starting_hobbs,
                    "end": analysis.times.ending_hobbs,
                    "increment": round(analysis.times.hobbs_increment, 2)
                },
                "tach": {
                    "start": analysis.times.starting_tach,
                    "end": analysis.times.ending_tach,
                    "increment": round(analysis.times.tach_increment, 2)
                },
                "oooi": {
                    "out": analysis.oooi.out_time.isoformat() if analysis.oooi.out_time else None,
                    "off": analysis.oooi.off_time.isoformat() if analysis.oooi.off_time else None,
                    "on": analysis.oooi.on_time.isoformat() if analysis.oooi.on_time else None,
                    "in": analysis.oooi.in_time.isoformat() if analysis.oooi.in_time else None,
                    "block_time_minutes": round(analysis.oooi.block_time_minutes, 1) if analysis.oooi.block_time_minutes else None,
                    "flight_time_minutes": round(analysis.oooi.flight_time_minutes, 1) if analysis.oooi.flight_time_minutes else None
                }
            }
            
            response = requests.post(
                self.url,
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"Successfully uploaded to maintenance tracker")
                return True, "Upload successful"
            else:
                error_msg = f"Maintenance tracker upload failed: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return False, error_msg
                
        except Exception as e:
            error_msg = f"Maintenance tracker upload error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg


class FlyStoUploader:
    """
    Upload flight data to FlySto (flysto.net)
    
    FlySto uses OAuth2 authentication. To use this uploader:
    1. Contact FlySto to register your application and get client_id and client_secret
    2. Complete the OAuth flow once to get refresh_token
    3. The uploader will automatically refresh access tokens as needed
    
    OAuth Flow (one-time setup):
    1. Open browser: https://www.flysto.net/oauth/authorize?response_type=code&client_id=<app-id>&redirect_uri=<redirect-uri>
    2. User grants permission, redirected to: <redirect-uri>?code=<AUTHORIZATION_CODE>
    3. Exchange code for tokens (see get_initial_tokens method)
    4. Store refresh_token in config
    """
    
    OAUTH_TOKEN_URL = "https://www.flysto.net/oauth/token"
    UPLOAD_URL = "https://www.flysto.net/public-api/log-upload"
    TOKEN_FILE = "/var/lib/g3x_processor/flysto_tokens.json"
    
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get('enabled', False)
        self.client_id = config.get('client_id', '')
        self.client_secret = config.get('client_secret', '')
        self.redirect_uri = config.get('redirect_uri', 'http://localhost:8080/callback')
        
        # Tokens - can be provided in config or loaded from token file
        self.access_token = config.get('access_token', '')
        self.refresh_token = config.get('refresh_token', '')
        self.token_expires_at = 0
        
        # Try to load tokens from file if not in config
        if not self.access_token or not self.refresh_token:
            self._load_tokens()
    
    def _load_tokens(self):
        """Load tokens from persistent storage"""
        try:
            if Path(self.TOKEN_FILE).exists():
                with open(self.TOKEN_FILE, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token', '')
                    self.refresh_token = data.get('refresh_token', '')
                    self.token_expires_at = data.get('expires_at', 0)
        except Exception as e:
            logger.warning(f"Could not load FlySto tokens: {e}")
    
    def _save_tokens(self):
        """Save tokens to persistent storage"""
        try:
            token_dir = Path(self.TOKEN_FILE).parent
            token_dir.mkdir(parents=True, exist_ok=True)
            
            with open(self.TOKEN_FILE, 'w') as f:
                json.dump({
                    'access_token': self.access_token,
                    'refresh_token': self.refresh_token,
                    'expires_at': self.token_expires_at
                }, f)
            
            # Secure the token file
            os.chmod(self.TOKEN_FILE, 0o600)
        except Exception as e:
            logger.warning(f"Could not save FlySto tokens: {e}")
    
    def _refresh_access_token(self) -> bool:
        """Refresh the access token using the refresh token"""
        if not self.refresh_token:
            logger.error("No FlySto refresh token available")
            return False
        
        try:
            response = requests.post(
                self.OAUTH_TOKEN_URL,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data={
                    'grant_type': 'refresh_token',
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'refresh_token': self.refresh_token
                },
                timeout=30
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token', '')
                # Update refresh token if a new one is provided
                if 'refresh_token' in token_data:
                    self.refresh_token = token_data['refresh_token']
                # Calculate expiration time (subtract 60 seconds for safety margin)
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = time.time() + expires_in - 60
                
                self._save_tokens()
                logger.info("Successfully refreshed FlySto access token")
                return True
            else:
                logger.error(f"Failed to refresh FlySto token: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error refreshing FlySto token: {e}")
            return False
    
    def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token, refreshing if necessary"""
        if not self.access_token or time.time() >= self.token_expires_at:
            return self._refresh_access_token()
        return True
    
    def exchange_code_for_tokens(self, authorization_code: str) -> Tuple[bool, str]:
        """
        Exchange an authorization code for access and refresh tokens.
        This is used during initial setup.
        
        Usage:
            1. Open browser to get authorization code
            2. Call this method with the code
            3. Tokens are saved automatically
        """
        try:
            response = requests.post(
                self.OAUTH_TOKEN_URL,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data={
                    'grant_type': 'authorization_code',
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': authorization_code,
                    'redirect_uri': self.redirect_uri
                },
                timeout=30
            )
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token', '')
                self.refresh_token = token_data.get('refresh_token', '')
                expires_in = token_data.get('expires_in', 3600)
                self.token_expires_at = time.time() + expires_in - 60
                
                self._save_tokens()
                logger.info("Successfully obtained FlySto tokens")
                return True, "Tokens obtained and saved successfully"
            else:
                error_msg = f"Failed to exchange code: {response.status_code} - {response.text}"
                logger.error(error_msg)
                return False, error_msg
                
        except Exception as e:
            error_msg = f"Error exchanging code for tokens: {e}"
            logger.error(error_msg)
            return False, error_msg
    
    def get_authorization_url(self) -> str:
        """Get the URL to start the OAuth authorization flow"""
        return (
            f"https://www.flysto.net/oauth/authorize?"
            f"response_type=code&"
            f"client_id={self.client_id}&"
            f"redirect_uri={self.redirect_uri}"
        )
    
    def upload(self, filepath: str, analysis: FlightAnalysis) -> Tuple[bool, str]:
        """
        Upload a flight CSV to FlySto.
        
        The file must be uploaded as a ZIP (one log file per ZIP).
        """
        if not self.enabled:
            return False, "FlySto upload not enabled"
        
        if not self.client_id or not self.client_secret:
            return False, "FlySto client_id and client_secret not configured"
        
        if not self.refresh_token:
            # Provide setup instructions
            auth_url = self.get_authorization_url()
            return False, (
                f"FlySto not authorized. Complete OAuth setup:\n"
                f"1. Open: {auth_url}\n"
                f"2. Grant permission\n"
                f"3. Copy the 'code' parameter from redirect URL\n"
                f"4. Run: g3x_processor.py --flysto-auth <code>"
            )
        
        # Ensure we have a valid access token
        if not self._ensure_valid_token():
            return False, "Failed to obtain valid FlySto access token"
        
        try:
            # Create a ZIP file containing the CSV
            import zipfile
            import tempfile
            
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp_zip:
                zip_path = tmp_zip.name
            
            try:
                # Create ZIP with the CSV file
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(filepath, Path(filepath).name)
                
                # Upload the ZIP file
                with open(zip_path, 'rb') as f:
                    zip_content = f.read()
                
                response = requests.post(
                    self.UPLOAD_URL,
                    headers={
                        'Authorization': f'Bearer {self.access_token}',
                        'Content-Type': 'application/zip'
                    },
                    data=zip_content,
                    timeout=120
                )
                
                if response.status_code in [200, 201]:
                    logger.info(f"Successfully uploaded to FlySto: {analysis.filename}")
                    return True, "Upload successful"
                elif response.status_code == 401:
                    # Token might have expired, try refreshing
                    if self._refresh_access_token():
                        # Retry upload with new token
                        response = requests.post(
                            self.UPLOAD_URL,
                            headers={
                                'Authorization': f'Bearer {self.access_token}',
                                'Content-Type': 'application/zip'
                            },
                            data=zip_content,
                            timeout=120
                        )
                        if response.status_code in [200, 201]:
                            logger.info(f"Successfully uploaded to FlySto (after token refresh): {analysis.filename}")
                            return True, "Upload successful"
                    
                    error_msg = f"FlySto authentication failed: {response.status_code}"
                    logger.error(error_msg)
                    return False, error_msg
                else:
                    error_msg = f"FlySto upload failed: {response.status_code} - {response.text}"
                    logger.error(error_msg)
                    return False, error_msg
                    
            finally:
                # Clean up temp ZIP file
                try:
                    os.unlink(zip_path)
                except:
                    pass
                    
        except Exception as e:
            error_msg = f"FlySto upload error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg


# =============================================================================
# Processed Files Database
# =============================================================================

class ProcessedFilesDB:
    """Track which files have been processed to avoid duplicates"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """Ensure the database file and directory exist"""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        if not Path(self.db_path).exists():
            self._save({})
    
    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    def _save(self, data: Dict[str, Any]):
        with open(self.db_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def is_processed(self, file_hash: str) -> bool:
        """Check if a file has already been processed"""
        data = self._load()
        return file_hash in data.get('processed', {})
    
    def mark_processed(self, file_hash: str, analysis: FlightAnalysis, 
                       upload_results: Dict[str, Tuple[bool, str]]):
        """Mark a file as processed"""
        data = self._load()
        
        if 'processed' not in data:
            data['processed'] = {}
        
        data['processed'][file_hash] = {
            'filename': analysis.filename,
            'date': analysis.date,
            'aircraft': analysis.aircraft_ident,
            'is_flight': analysis.is_flight,
            'processed_at': datetime.now().isoformat(),
            'uploads': {k: {'success': v[0], 'message': v[1]} for k, v in upload_results.items()}
        }
        
        self._save(data)


# =============================================================================
# Main Processor
# =============================================================================

class G3XFlightProcessor:
    """Main processor that orchestrates everything"""
    
    def __init__(self, config_path: str):
        self.config = G3XConfig.from_json(config_path)
        self.parser = G3XLogParser(self.config)
        self.processed_db = ProcessedFilesDB(self.config.processed_files_db)
        
        # Initialize uploaders
        self.uploaders = {
            'savvy_aviation': SavvyAviationUploader(self.config.upload.savvy_aviation),
            'cloudahoy': CloudAhoyUploader(self.config.upload.cloudahoy),
            'flysto': FlyStoUploader(self.config.upload.flysto),
            'maintenance_tracker': MaintenanceTrackerUploader(self.config.upload.maintenance_tracker)
        }
    
    def find_log_files(self, mount_point: str) -> List[str]:
        """Find all G3X log CSV files on the SD card"""
        log_files = []
        
        # G3X stores logs in data_log folder
        data_log_dir = Path(mount_point) / "data_log"
        
        if not data_log_dir.exists():
            # Try root of SD card
            data_log_dir = Path(mount_point)
        
        # Find all CSV files that look like G3X logs
        for csv_file in data_log_dir.glob("*.csv"):
            # Check if it's a G3X log file by looking at first line
            try:
                with open(csv_file, 'r') as f:
                    first_line = f.readline()
                    if first_line.startswith('#airframe_info'):
                        log_files.append(str(csv_file))
            except Exception as e:
                logger.warning(f"Could not read {csv_file}: {e}")
        
        # Also check for log_*.csv pattern
        for csv_file in data_log_dir.glob("log_*.csv"):
            if str(csv_file) not in log_files:
                try:
                    with open(csv_file, 'r') as f:
                        first_line = f.readline()
                        if first_line.startswith('#airframe_info'):
                            log_files.append(str(csv_file))
                except Exception as e:
                    logger.warning(f"Could not read {csv_file}: {e}")
        
        return sorted(log_files)
    
    def process_file(self, filepath: str) -> Optional[FlightAnalysis]:
        """Process a single log file"""
        logger.info(f"Processing {filepath}")
        
        # Analyze the file
        analysis = self.parser.analyze(filepath)
        
        # Check if already processed
        if self.processed_db.is_processed(analysis.file_hash):
            logger.info(f"File {filepath} already processed, skipping")
            return None
        
        # Log the analysis result
        if analysis.is_flight:
            logger.info(f"Flight detected: {analysis.filename}")
            logger.info(f"  Aircraft: {analysis.aircraft_ident}")
            logger.info(f"  Date: {analysis.date}")
            logger.info(f"  Hobbs: {analysis.times.starting_hobbs:.1f} -> {analysis.times.ending_hobbs:.1f} (+{analysis.times.hobbs_increment:.2f})")
            logger.info(f"  Tach: {analysis.times.starting_tach:.1f} -> {analysis.times.ending_tach:.1f} (+{analysis.times.tach_increment:.2f})")
            if analysis.oooi.out_time:
                logger.info(f"  OUT: {analysis.oooi.out_time.strftime('%H:%M:%S')}")
            if analysis.oooi.off_time:
                logger.info(f"  OFF: {analysis.oooi.off_time.strftime('%H:%M:%S')}")
            if analysis.oooi.on_time:
                logger.info(f"  ON: {analysis.oooi.on_time.strftime('%H:%M:%S')}")
            if analysis.oooi.in_time:
                logger.info(f"  IN: {analysis.oooi.in_time.strftime('%H:%M:%S')}")
        else:
            logger.info(f"Not a flight: {analysis.filename} - {analysis.rejection_reason}")
        
        return analysis
    
    def upload_flight(self, analysis: FlightAnalysis) -> Dict[str, Tuple[bool, str]]:
        """Upload a flight to all configured services"""
        results = {}
        
        for name, uploader in self.uploaders.items():
            try:
                success, message = uploader.upload(analysis.filepath, analysis)
                results[name] = (success, message)
            except Exception as e:
                logger.error(f"Upload to {name} failed: {e}")
                results[name] = (False, str(e))
        
        return results
    
    def process_sd_card(self, mount_point: str) -> Dict[str, Any]:
        """Process all new files on an SD card"""
        logger.info(f"Processing SD card at {mount_point}")
        
        results = {
            'mount_point': mount_point,
            'files_found': 0,
            'flights_detected': 0,
            'non_flights': 0,
            'already_processed': 0,
            'upload_results': [],
            'errors': []
        }
        
        try:
            log_files = self.find_log_files(mount_point)
            results['files_found'] = len(log_files)
            logger.info(f"Found {len(log_files)} G3X log files")
            
            for filepath in log_files:
                try:
                    analysis = self.process_file(filepath)
                    
                    if analysis is None:
                        results['already_processed'] += 1
                        continue
                    
                    if analysis.is_flight:
                        results['flights_detected'] += 1
                        
                        # Upload to services
                        upload_results = self.upload_flight(analysis)
                        
                        # Mark as processed
                        self.processed_db.mark_processed(
                            analysis.file_hash, analysis, upload_results
                        )
                        
                        results['upload_results'].append({
                            'filename': analysis.filename,
                            'uploads': upload_results
                        })
                    else:
                        results['non_flights'] += 1
                        # Still mark as processed so we don't re-analyze
                        self.processed_db.mark_processed(
                            analysis.file_hash, analysis, {}
                        )
                        
                except Exception as e:
                    error_msg = f"Error processing {filepath}: {e}"
                    logger.error(error_msg)
                    results['errors'].append(error_msg)
            
        except Exception as e:
            error_msg = f"Error scanning SD card: {e}"
            logger.error(error_msg)
            results['errors'].append(error_msg)
        
        logger.info(f"Processing complete: {results['flights_detected']} flights, "
                   f"{results['non_flights']} non-flights, "
                   f"{results['already_processed']} already processed")
        
        return results


# =============================================================================
# CLI Entry Point
# =============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='G3X Flight Data Processor')
    parser.add_argument('mount_point', nargs='?', help='SD card mount point')
    parser.add_argument('--config', '-c', default='/etc/g3x_processor/config.json',
                       help='Path to config file')
    parser.add_argument('--generate-config', action='store_true',
                       help='Generate a sample config file')
    parser.add_argument('--analyze', '-a', help='Analyze a single file')
    parser.add_argument('--json', '-j', action='store_true',
                       help='Output results as JSON')
    parser.add_argument('--flysto-auth', metavar='CODE',
                       help='Exchange FlySto authorization code for tokens')
    parser.add_argument('--flysto-auth-url', action='store_true',
                       help='Print FlySto OAuth authorization URL')
    
    args = parser.parse_args()
    
    # Handle FlySto OAuth setup
    if args.flysto_auth_url or args.flysto_auth:
        if not Path(args.config).exists():
            print(f"Config file not found: {args.config}")
            print("Create a config file first with --generate-config")
            sys.exit(1)
        
        config = G3XConfig.from_json(args.config)
        flysto = FlyStoUploader(config.upload.flysto)
        
        if args.flysto_auth_url:
            print("FlySto OAuth Authorization")
            print("=" * 50)
            print()
            print("1. Open this URL in your browser:")
            print()
            print(f"   {flysto.get_authorization_url()}")
            print()
            print("2. Login to FlySto and grant permission")
            print()
            print("3. You'll be redirected to a URL like:")
            print(f"   {flysto.redirect_uri}?code=XXXXXX")
            print()
            print("4. Copy the 'code' value and run:")
            print(f"   g3x_processor.py --flysto-auth XXXXXX")
            return
        
        if args.flysto_auth:
            print("Exchanging authorization code for tokens...")
            success, message = flysto.exchange_code_for_tokens(args.flysto_auth)
            if success:
                print("✓ Success! FlySto tokens saved.")
                print("  You can now upload flights to FlySto.")
            else:
                print(f"✗ Failed: {message}")
                sys.exit(1)
            return
    
    args = parser.parse_args()
    
    if args.generate_config:
        config = G3XConfig(
            aircraft_ident="N12345",
            engine_time=EngineTimeConfig(),
            airframe_time=AirframeTimeConfig(),
            oooi=OOOIConfig(),
            flight_detection=FlightDetectionConfig(),
            upload=UploadConfig(
                savvy_aviation={"enabled": False, "email": "", "password": ""},
                cloudahoy={"enabled": False, "api_token": "your-api-token"},
                flysto={
                    "enabled": False,
                    "client_id": "your-flysto-client-id",
                    "client_secret": "your-flysto-client-secret",
                    "redirect_uri": "http://localhost:8080/callback"
                },
                maintenance_tracker={"enabled": False, "url": "https://your-tracker.com/api/flights", "api_key": "your-api-key"}
            )
        )
        config_path = args.config if args.config != '/etc/g3x_processor/config.json' else 'g3x_config.json'
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        config.to_json(config_path)
        print(f"Generated sample config: {config_path}")
        return
    
    if args.analyze:
        # Analyze a single file
        if not Path(args.config).exists():
            # Use defaults
            config = G3XConfig()
        else:
            config = G3XConfig.from_json(args.config)
        
        parser_obj = G3XLogParser(config)
        analysis = parser_obj.analyze(args.analyze)
        
        if args.json:
            result = {
                'filename': analysis.filename,
                'is_flight': analysis.is_flight,
                'rejection_reason': analysis.rejection_reason,
                'aircraft': analysis.aircraft_ident,
                'date': analysis.date,
                'data_points': analysis.data_points,
                'max_ground_speed': analysis.max_ground_speed,
                'max_altitude': analysis.max_altitude
            }
            if analysis.is_flight and analysis.times:
                result['hobbs'] = {
                    'starting': analysis.times.starting_hobbs,
                    'increment': round(analysis.times.hobbs_increment, 2),
                    'ending': round(analysis.times.ending_hobbs, 1)
                }
                result['tach'] = {
                    'starting': analysis.times.starting_tach,
                    'increment': round(analysis.times.tach_increment, 2),
                    'ending': round(analysis.times.ending_tach, 1)
                }
                result['oooi'] = {
                    'out': analysis.oooi.out_time.isoformat() if analysis.oooi.out_time else None,
                    'off': analysis.oooi.off_time.isoformat() if analysis.oooi.off_time else None,
                    'on': analysis.oooi.on_time.isoformat() if analysis.oooi.on_time else None,
                    'in': analysis.oooi.in_time.isoformat() if analysis.oooi.in_time else None
                }
            print(json.dumps(result, indent=2))
        else:
            print(f"File: {analysis.filename}")
            print(f"Is Flight: {analysis.is_flight}")
            if not analysis.is_flight:
                print(f"Reason: {analysis.rejection_reason}")
            else:
                print(f"Aircraft: {analysis.aircraft_ident}")
                print(f"Date: {analysis.date}")
                print(f"Hobbs: {analysis.times.starting_hobbs:.1f} -> {analysis.times.ending_hobbs:.1f} (+{analysis.times.hobbs_increment:.2f})")
                print(f"Tach: {analysis.times.starting_tach:.1f} -> {analysis.times.ending_tach:.1f} (+{analysis.times.tach_increment:.2f})")
        return
    
    if not args.mount_point:
        parser.print_help()
        sys.exit(1)
    
    # Process SD card
    processor = G3XFlightProcessor(args.config)
    results = processor.process_sd_card(args.mount_point)
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\nProcessing Results:")
        print(f"  Files found: {results['files_found']}")
        print(f"  Flights detected: {results['flights_detected']}")
        print(f"  Non-flights skipped: {results['non_flights']}")
        print(f"  Already processed: {results['already_processed']}")
        if results['errors']:
            print(f"  Errors: {len(results['errors'])}")


if __name__ == '__main__':
    main()
