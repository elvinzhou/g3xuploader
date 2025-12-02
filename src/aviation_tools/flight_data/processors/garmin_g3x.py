"""
Garmin G3X Touch flight data processor.

Parses Garmin G3X Touch CSV log files and extracts flight data.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import logging

from aviation_tools.flight_data.base import (
    FlightDataProcessor,
    FlightData,
    FlightMetadata,
    DataPoint,
)
from aviation_tools.core.utils import hash_file

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

    # Column indices based on G3X CSV format (0-indexed)
    # Format: Garmin G3X Touch / GDU 460
    COL_DATE = 0
    COL_TIME = 1
    COL_UTC_TIME = 2
    COL_UTC_OFFSET = 3
    COL_LATITUDE = 4
    COL_LONGITUDE = 5
    COL_GPS_ALTITUDE = 6
    COL_GPS_FIX = 7
    COL_GROUND_SPEED = 8
    COL_TRACK = 9
    COL_HDG = 13
    COL_PRESSURE_ALT = 16
    COL_BARO_ALT = 17
    COL_VERTICAL_SPEED = 18
    COL_IAS = 19
    COL_TAS = 20
    COL_PITCH = 21
    COL_ROLL = 22
    COL_RPM = 53
    COL_OIL_PRESS = 54
    COL_OIL_TEMP = 55
    COL_CHT1 = 63
    COL_CHT2 = 64
    COL_EGT1 = 69
    COL_EGT2 = 70

    def detect_log_format(self, file_path: Path) -> bool:
        """
        Detect if this is a G3X CSV log file.

        Args:
            file_path: Path to log file

        Returns:
            True if file appears to be G3X format
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline()
                # G3X files start with #airframe_info
                return first_line.startswith('#airframe_info')
        except Exception as e:
            logger.debug(f"Error detecting G3X format in {file_path}: {e}")
            return False

    def parse_log(self, file_path: Path) -> FlightData:
        """
        Parse a G3X log file and extract complete flight data.

        Args:
            file_path: Path to G3X CSV log file

        Returns:
            FlightData object with metadata and data points

        Raises:
            ValueError: If file format is invalid
        """
        if not self.detect_log_format(file_path):
            raise ValueError(f"File {file_path} is not a valid G3X log file")

        # Extract metadata
        metadata = self.extract_metadata(file_path)

        # Parse data points
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
        Extract metadata from G3X log file header.

        Args:
            file_path: Path to G3X CSV log file

        Returns:
            FlightMetadata object

        Raises:
            ValueError: If file format is invalid
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline()

            if not first_line.startswith('#airframe_info'):
                raise ValueError("Invalid G3X log file: missing #airframe_info header")

            # Parse metadata from first line
            metadata_dict = self._parse_metadata_line(first_line)

            # Extract date from filename if available
            # Format: log_YYYYMMDD_HHMMSS_ICAO.csv
            date = None
            departure_airport = None
            arrival_airport = None

            filename = file_path.name
            if filename.startswith('log_'):
                parts = filename.replace('.csv', '').split('_')
                if len(parts) >= 4:
                    date_str = parts[1]
                    if len(date_str) == 8:
                        date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    # Airport code might be in filename
                    if len(parts) == 4:
                        departure_airport = parts[3]

            return FlightMetadata(
                aircraft_ident=metadata_dict.get('aircraft_ident'),
                date=date,
                departure_airport=departure_airport,
                arrival_airport=arrival_airport,
                airframe_hours_start=self._safe_float(metadata_dict.get('airframe_hours')),
                engine_hours_start=self._safe_float(metadata_dict.get('engine_hours')),
                manufacturer="Garmin",
                model="G3X Touch",
                serial_number=metadata_dict.get('unit_serial_number'),
                additional=metadata_dict
            )

        except Exception as e:
            raise ValueError(f"Error extracting metadata from {file_path}: {e}")

    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions."""
        return ['.csv']

    def _parse_metadata_line(self, line: str) -> dict:
        """
        Parse the #airframe_info metadata line.

        Args:
            line: First line of G3X log file

        Returns:
            Dictionary of metadata key-value pairs
        """
        metadata = {}
        parts = line.strip().split(',')

        for part in parts[1:]:  # Skip #airframe_info
            if '=' in part:
                key, value = part.split('=', 1)
                # Remove quotes from value
                value = value.strip('"')
                metadata[key] = value

        return metadata

    def _parse_data_points(self, file_path: Path) -> List[DataPoint]:
        """
        Parse all data points from G3X log file.

        Args:
            file_path: Path to G3X CSV log file

        Returns:
            List of DataPoint objects
        """
        data_points = []

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Skip metadata and header lines (lines 0-2)
        data_start = 3

        if len(lines) < data_start + 1:
            logger.warning(f"No data points found in {file_path}")
            return data_points

        for line_num, line in enumerate(lines[data_start:], start=data_start):
            line = line.strip()
            if not line:
                continue

            try:
                data_point = self._parse_data_line(line)
                if data_point:
                    data_points.append(data_point)
            except Exception as e:
                logger.debug(f"Error parsing line {line_num} in {file_path}: {e}")
                continue

        logger.info(f"Parsed {len(data_points)} data points from {file_path}")
        return data_points

    def _parse_data_line(self, line: str) -> Optional[DataPoint]:
        """
        Parse a single data line from G3X CSV.

        Args:
            line: CSV data line

        Returns:
            DataPoint object or None if line is invalid
        """
        cols = line.split(',')

        # Need at least enough columns for basic data
        if len(cols) < 20:
            return None

        # Parse date and time
        date_str = cols[self.COL_DATE] if self.COL_DATE < len(cols) else ""
        time_str = cols[self.COL_TIME] if self.COL_TIME < len(cols) else ""
        timestamp = self._parse_datetime(date_str, time_str)

        if not timestamp:
            return None

        return DataPoint(
            timestamp=timestamp,
            latitude=self._safe_float(self._get_col(cols, self.COL_LATITUDE)),
            longitude=self._safe_float(self._get_col(cols, self.COL_LONGITUDE)),
            altitude_ft=self._safe_float(self._get_col(cols, self.COL_BARO_ALT)),
            ground_speed_kts=self._safe_float(self._get_col(cols, self.COL_GROUND_SPEED)),
            track=self._safe_float(self._get_col(cols, self.COL_TRACK)),
            vertical_speed_fpm=self._safe_float(self._get_col(cols, self.COL_VERTICAL_SPEED)),
            rpm=self._safe_float(self._get_col(cols, self.COL_RPM)),
            oil_pressure=self._safe_float(self._get_col(cols, self.COL_OIL_PRESS)),
            oil_temperature=self._safe_float(self._get_col(cols, self.COL_OIL_TEMP)),
            cylinder_head_temp=self._safe_float(self._get_col(cols, self.COL_CHT1)),
            exhaust_gas_temp=self._safe_float(self._get_col(cols, self.COL_EGT1)),
            fuel_flow=None,  # Not present in all G3X formats
            fuel_quantity=None,  # Not present in all G3X formats
            additional={
                'pressure_altitude': self._safe_float(self._get_col(cols, self.COL_PRESSURE_ALT)),
                'gps_altitude': self._safe_float(self._get_col(cols, self.COL_GPS_ALTITUDE)),
                'ias': self._safe_float(self._get_col(cols, self.COL_IAS)),
                'tas': self._safe_float(self._get_col(cols, self.COL_TAS)),
                'pitch': self._safe_float(self._get_col(cols, self.COL_PITCH)),
                'roll': self._safe_float(self._get_col(cols, self.COL_ROLL)),
            }
        )

    def _get_col(self, cols: List[str], index: int) -> str:
        """Safely get column value."""
        return cols[index] if index < len(cols) else ""

    def _parse_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Parse date and time strings into datetime object."""
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _safe_float(self, value: str, default: Optional[float] = None) -> Optional[float]:
        """Safely convert string to float."""
        if not value or value.strip() == "":
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
