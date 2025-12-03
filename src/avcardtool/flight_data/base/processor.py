"""
Abstract base class for flight data processors.

This module defines the interface that all manufacturer-specific flight data
processors must implement, enabling support for multiple avionics manufacturers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any


@dataclass
class FlightMetadata:
    """Metadata extracted from flight log header or file."""
    aircraft_ident: Optional[str] = None
    date: Optional[str] = None
    departure_airport: Optional[str] = None
    arrival_airport: Optional[str] = None
    airframe_hours_start: Optional[float] = None
    engine_hours_start: Optional[float] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    additional: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DataPoint:
    """Single data point from flight log."""
    timestamp: datetime
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_ft: Optional[float] = None
    ground_speed_kts: Optional[float] = None
    track: Optional[float] = None
    vertical_speed_fpm: Optional[float] = None
    rpm: Optional[float] = None
    manifold_pressure: Optional[float] = None
    oil_pressure: Optional[float] = None
    oil_temperature: Optional[float] = None
    cylinder_head_temp: Optional[float] = None
    exhaust_gas_temp: Optional[float] = None
    fuel_flow: Optional[float] = None
    fuel_quantity: Optional[float] = None
    additional: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FlightData:
    """Complete flight data parsed from log file."""
    metadata: FlightMetadata
    data_points: List[DataPoint]
    file_path: Path
    file_hash: str

    @property
    def duration_seconds(self) -> float:
        """Calculate flight duration in seconds."""
        if len(self.data_points) < 2:
            return 0.0
        return (self.data_points[-1].timestamp - self.data_points[0].timestamp).total_seconds()

    @property
    def max_altitude_ft(self) -> Optional[float]:
        """Get maximum altitude reached."""
        altitudes = [dp.altitude_ft for dp in self.data_points if dp.altitude_ft is not None]
        return max(altitudes) if altitudes else None

    @property
    def max_ground_speed_kts(self) -> Optional[float]:
        """Get maximum ground speed."""
        speeds = [dp.ground_speed_kts for dp in self.data_points if dp.ground_speed_kts is not None]
        return max(speeds) if speeds else None


class FlightDataProcessor(ABC):
    """
    Abstract base class for flight data processors.

    Each manufacturer's avionics system has its own log file format.
    Implement this class to add support for a new manufacturer.

    Example:
        class GarminG3XProcessor(FlightDataProcessor):
            def detect_log_format(self, file_path):
                # Check if file is G3X CSV format
                with open(file_path) as f:
                    first_line = f.readline()
                    return first_line.startswith('#airframe_info')

            def parse_log(self, file_path):
                # Parse G3X CSV format
                ...
    """

    @abstractmethod
    def detect_log_format(self, file_path: Path) -> bool:
        """
        Detect if this processor can handle the given log file.

        Args:
            file_path: Path to log file

        Returns:
            True if this processor can handle the file format
        """
        pass

    @abstractmethod
    def parse_log(self, file_path: Path) -> FlightData:
        """
        Parse a log file and extract flight data.

        Args:
            file_path: Path to log file

        Returns:
            FlightData object with parsed data

        Raises:
            ValueError: If file format is invalid or cannot be parsed
        """
        pass

    @abstractmethod
    def extract_metadata(self, file_path: Path) -> FlightMetadata:
        """
        Extract metadata from log file without parsing all data points.

        This is useful for quick analysis without loading the entire file.

        Args:
            file_path: Path to log file

        Returns:
            FlightMetadata object

        Raises:
            ValueError: If file format is invalid
        """
        pass

    def get_name(self) -> str:
        """
        Get human-readable name for this processor.

        Returns:
            Processor name (e.g., "Garmin G3X Touch")
        """
        return self.__class__.__name__.replace("Processor", "")

    def get_supported_extensions(self) -> List[str]:
        """
        Get list of file extensions this processor supports.

        Returns:
            List of extensions (e.g., ['.csv', '.log'])
        """
        return []

    def validate_data(self, flight_data: FlightData) -> bool:
        """
        Validate parsed flight data.

        Args:
            flight_data: Parsed flight data

        Returns:
            True if data is valid

        Raises:
            ValueError: If data is invalid, with description of problem
        """
        if not flight_data.data_points:
            raise ValueError("No data points in flight data")

        if not flight_data.metadata:
            raise ValueError("No metadata in flight data")

        return True
