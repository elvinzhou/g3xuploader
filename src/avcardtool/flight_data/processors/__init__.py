"""
Flight data processors for various manufacturers.
"""

from avcardtool.flight_data.processors.garmin_g3x import GarminG3XProcessor

# Registry of all available processors
PROCESSORS = [
    GarminG3XProcessor,
]

__all__ = [
    "GarminG3XProcessor",
    "PROCESSORS",
]
