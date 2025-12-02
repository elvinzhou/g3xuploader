"""
G3X Database Updater

A tool for automatically downloading Garmin aviation databases and
installing them on SD cards for G3X Touch flight displays.

This package provides:
- Garmin flyGarmin portal authentication
- TAW/AWP file parsing and extraction
- Automatic SD card detection and writing
- Integration with the G3X Flight Data Processor
"""

__version__ = "0.1.0"
__author__ = "G3X Database Updater Contributors"

from .garmin_auth import (
    GarminAuth,
    FlyGarminAPI,
    GarminTokens,
    GarminDevice,
    DatabaseInfo,
    GarminAuthError,
    GarminAPIError,
)

from .taw_parser import (
    TAWParser,
    TAWExtractor,
    TAWFile,
    TAWHeader,
    TAWRegion,
    TAWParseError,
    TAW_REGION_PATHS,
    G3X_DATABASE_STRUCTURE,
)

from .sdcard_writer import (
    SDCardDetector,
    SDCardInfo,
    G3XDatabaseWriter,
    AutoDatabaseUpdater,
    DatabaseVersion,
    WriteResult,
    SDCardError,
    SDCardNotFoundError,
    SDCardFormatError,
    SDCardWriteError,
)

__all__ = [
    # Version info
    '__version__',
    '__author__',
    
    # Authentication
    'GarminAuth',
    'FlyGarminAPI',
    'GarminTokens',
    'GarminDevice',
    'DatabaseInfo',
    'GarminAuthError',
    'GarminAPIError',
    
    # TAW parsing
    'TAWParser',
    'TAWExtractor',
    'TAWFile',
    'TAWHeader',
    'TAWRegion',
    'TAWParseError',
    'TAW_REGION_PATHS',
    'G3X_DATABASE_STRUCTURE',
    
    # SD card operations
    'SDCardDetector',
    'SDCardInfo',
    'G3XDatabaseWriter',
    'AutoDatabaseUpdater',
    'DatabaseVersion',
    'WriteResult',
    'SDCardError',
    'SDCardNotFoundError',
    'SDCardFormatError',
    'SDCardWriteError',
]
