"""
Garmin G3X Touch flight data processor.

Parses Garmin G3X Touch CSV log files and extracts flight data.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
import logging

from avcardtool.flight_data.base import (
    FlightDataProcessor,
    FlightData,
    FlightMetadata,
    DataPoint,
)
from avcardtool.core.utils import hash_file

logger = logging.getLogger(__name__)


class GarminG3XProcessor(FlightDataProcessor):
    """
    Processor for Garmin G3X Touch CSV log files.

    Format:
        Line 1: #airframe_info,aircraft_ident="N12345",airframe_hours="110.4",...
        Line 2: Full column headers
        Line 3: Short column headers
        Line 4+: Data at 1Hz
    """

    # Default column name substrings for mapping
    COLUMN_MAP = {
        'date': 'Date',
        'time': 'Time',
        'latitude': 'Latitude',
        'longitude': 'Longitude',
        'ground_speed': 'Ground Speed',
        'track': 'Ground Track',
        'baro_alt': 'Baro Altitude',
        'vertical_speed': 'Vertical Speed',
        'rpm': 'RPM',
        'oil_press': 'Oil Press',
        'oil_temp': 'Oil Temp',
        'cht1': 'CHT1',
        'egt1': 'EGT1',
        'pressure_alt': 'Pressure Altitude',
        'gps_alt': 'GPS Altitude',
        'ias': 'Indicated Airspeed',
        'tas': 'True Airspeed',
        'pitch': 'Pitch',
        'roll': 'Roll',
    }

    def __init__(self):
        super().__init__()
        self.column_indices = {}

    def get_name(self) -> str:
        """Get the name of this processor."""
        return "Garmin G3X Touch"

    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions."""
        return ['.csv']

    def detect_log_format(self, file_path: Path) -> bool:
        """
        Detect if this is a G3X CSV log file.
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline()
                return first_line.startswith('#airframe_info')
        except Exception as e:
            logger.debug(f"Error detecting G3X format in {file_path}: {e}")
            return False

    def parse_log(self, file_path: Path) -> FlightData:
        """
        Parse a G3X log file and extract complete flight data.
        """
        if not self.detect_log_format(file_path):
            raise ValueError(f"File {file_path} is not a valid G3X log file")

        # Extract metadata
        metadata = self.extract_metadata(file_path)

        # Map columns and parse data points
        data_points = self._parse_data_points(file_path)

        # Calculate file hash
        file_hash = hash_file(file_path)

        return FlightData(
            metadata=metadata,
            data_points=data_points,
            file_path=file_path,
            file_hash=file_hash
        )

    def extract_metadata(self, file_path: Path) -> FlightMetadata:
        """
        Extract metadata from G3X log file header and filename.
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline()

            if not first_line.startswith('#airframe_info'):
                raise ValueError("Invalid G3X log file: missing #airframe_info header")

            metadata_dict = self._parse_metadata_line(first_line)

            date = None
            departure_airport = None
            
            # Try to extract date and departure from filename
            # Format: log_YYYYMMDD_HHMMSS_ID_DEPT.csv
            filename = file_path.name
            if filename.startswith('log_'):
                parts = filename.replace('.csv', '').split('_')
                if len(parts) >= 2:
                    date_str = parts[1]
                    if len(date_str) == 8 and date_str.isdigit():
                        date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                
                # Some files have DEPT at the end
                if len(parts) >= 4:
                    departure_airport = parts[-1]

            # If date extraction from filename failed, we'll try to get it from the first data line later
            # For now, return what we have
            return FlightMetadata(
                aircraft_ident=metadata_dict.get('aircraft_ident'),
                date=date,
                departure_airport=departure_airport,
                airframe_hours_start=self._safe_float(metadata_dict.get('airframe_hours')),
                engine_hours_start=self._safe_float(metadata_dict.get('engine_hours')),
                manufacturer="Garmin",
                model="G3X Touch",
                serial_number=metadata_dict.get('unit_serial_number') or metadata_dict.get('system_id'),
                additional=metadata_dict
            )

        except Exception as e:
            logger.error(f"Error extracting metadata from {file_path}: {e}")
            # Return minimal metadata if parsing fails
            return FlightMetadata(
                aircraft_ident="UNKNOWN",
                manufacturer="Garmin",
                model="G3X Touch"
            )

    def _parse_metadata_line(self, line: str) -> dict:
        metadata = {}
        parts = line.strip().split(',')
        for part in parts[1:]:
            if '=' in part:
                key, value = part.split('=', 1)
                metadata[key] = value.strip('"')
        return metadata

    def _map_columns(self, header_line: str):
        """Dynamically map column names to indices."""
        cols = [c.strip() for c in header_line.split(',')]
        self.column_indices = {}
        
        for internal_name, substring in self.COLUMN_MAP.items():
            for i, col in enumerate(cols):
                if substring in col:
                    self.column_indices[internal_name] = i
                    break
        
        # Log missing columns
        missing = [name for name in self.COLUMN_MAP if name not in self.column_indices]
        if missing:
            logger.warning(f"Missing columns in CSV: {missing}")

    def _parse_data_points(self, file_path: Path) -> List[DataPoint]:
        data_points = []
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            # Skip metadata line (Line 1)
            f.readline()
            
            # Line 2: Headers
            header_line = f.readline()
            if not header_line:
                return data_points
            self._map_columns(header_line)
            
            # Line 3: Short headers (skip)
            f.readline()

            # Data starts at line 4
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data_point = self._parse_data_line(line)
                    if data_point:
                        data_points.append(data_point)
                except Exception as e:
                    logger.debug(f"Skipping malformed CSV line: {e}")
                    continue

        return data_points

    def _parse_data_line(self, line: str) -> Optional[DataPoint]:
        cols = [c.strip() for c in line.split(',')]
        
        def get_val(name):
            idx = self.column_indices.get(name)
            return cols[idx] if idx is not None and idx < len(cols) else ""

        date_str = get_val('date')
        time_str = get_val('time')
        
        # Handle cases where date/time might have extra quotes or be empty
        if not date_str or not time_str:
            return None
            
        timestamp = self._parse_datetime(date_str, time_str)
        if not timestamp:
            return None

        return DataPoint(
            timestamp=timestamp,
            latitude=self._safe_float(get_val('latitude')),
            longitude=self._safe_float(get_val('longitude')),
            altitude_ft=self._safe_float(get_val('baro_alt')),
            ground_speed_kts=self._safe_float(get_val('ground_speed')),
            track=self._safe_float(get_val('track')),
            vertical_speed_fpm=self._safe_float(get_val('vertical_speed')),
            rpm=self._safe_float(get_val('rpm')),
            oil_pressure=self._safe_float(get_val('oil_press')),
            oil_temperature=self._safe_float(get_val('oil_temp')),
            cylinder_head_temp=self._safe_float(get_val('cht1')),
            exhaust_gas_temp=self._safe_float(get_val('egt1')),
            additional={
                'pressure_altitude': self._safe_float(get_val('pressure_alt')),
                'gps_altitude': self._safe_float(get_val('gps_alt')),
                'ias': self._safe_float(get_val('ias')),
                'tas': self._safe_float(get_val('tas')),
                'pitch': self._safe_float(get_val('pitch')),
                'roll': self._safe_float(get_val('roll')),
            }
        )

    def _parse_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        # G3X format: YYYY-MM-DD HH:MM:SS
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Try other common formats if needed
            try:
                return datetime.fromisoformat(f"{date_str}T{time_str}")
            except ValueError:
                return None

    def _safe_float(self, value: str) -> Optional[float]:
        if not value or value.strip() == "": return None
        try: return float(value)
        except (ValueError, TypeError): return None
