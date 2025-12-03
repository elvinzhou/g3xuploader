"""
Base classes for flight data processing.
"""

from avcardtool.flight_data.base.processor import (
    FlightDataProcessor,
    FlightData,
    FlightMetadata,
    DataPoint,
)
from avcardtool.flight_data.base.uploader import (
    FlightDataUploader,
    UploadResult,
    AuthenticationError,
    UploadError,
    DuplicateFlightError,
)

__all__ = [
    "FlightDataProcessor",
    "FlightData",
    "FlightMetadata",
    "DataPoint",
    "FlightDataUploader",
    "UploadResult",
    "AuthenticationError",
    "UploadError",
    "DuplicateFlightError",
]
