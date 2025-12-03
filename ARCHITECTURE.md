# AVCardTool - Architecture Design

## Overview

This project merges two separate tools into one modular system:
1. **Flight Data Processor** - Processes flight logs and uploads to tracking services
2. **Navigation Database Updater** - Downloads and installs aviation databases

## Design Goals

1. **Modular Architecture**: Separate concerns to allow independent development
2. **Manufacturer-Agnostic Flight Data**: Support multiple avionics manufacturers
3. **Upstream Contribution**: Keep Garmin navdata code separate for upstreaming
4. **Single Installation**: One install script for the complete system
5. **Unified CLI**: Single command-line interface with subcommands

## Project Structure

```
avcardtool/
├── src/
│   └── avcardtool/
│       ├── __init__.py
│       ├── cli.py                          # Main CLI entry point
│       │
│       ├── core/                            # Shared core functionality
│       │   ├── __init__.py
│       │   ├── config.py                   # Unified configuration
│       │   └── utils.py                    # Common utilities
│       │
│       ├── flight_data/                     # Flight data processing module
│       │   ├── __init__.py
│       │   │
│       │   ├── base/                       # Abstract base classes
│       │   │   ├── __init__.py
│       │   │   ├── processor.py           # Base flight data processor
│       │   │   └── uploader.py            # Base uploader interface
│       │   │
│       │   ├── processors/                 # Manufacturer-specific processors
│       │   │   ├── __init__.py
│       │   │   └── garmin_g3x.py         # Garmin G3X Touch processor
│       │   │
│       │   ├── analyzers/                  # Flight data analyzers
│       │   │   ├── __init__.py
│       │   │   ├── hobbs.py              # Airframe time calculation
│       │   │   ├── tach.py               # Engine time calculation
│       │   │   ├── oooi.py               # Out/Off/On/In detection
│       │   │   └── flight_detector.py    # Flight vs power-on detection
│       │   │
│       │   └── uploaders/                  # Upload service integrations
│       │       ├── __init__.py
│       │       ├── base.py
│       │       ├── cloudahoy.py
│       │       ├── flysto.py
│       │       ├── savvy_aviation.py
│       │       └── maintenance_tracker.py
│       │
│       └── navdata/                         # Navigation database module
│           ├── __init__.py
│           ├── sdcard.py                   # SD card detection/mounting
│           ├── installer.py                # Database installer
│           │
│           └── garmin/                     # Garmin-specific (for upstream)
│               ├── __init__.py
│               ├── auth.py                # Garmin SSO authentication
│               ├── taw_parser.py          # TAW/AWP file parser
│               └── downloader.py          # Database downloader
│
├── pyproject.toml                           # Python package configuration
├── install.sh                               # Unified installation script
├── README.md                                # Main documentation
│
├── systemd/                                 # System integration
│   ├── 99-avcardtool-sdcard.rules            # Unified udev rules
│   └── avcardtool-processor@.service         # Unified systemd service
│
└── tests/                                   # Test suite
    ├── test_flight_data/
    ├── test_navdata/
    └── test_integration/
```

## Module Responsibilities

### Core Module (`avcardtool.core`)

**Purpose**: Shared functionality used across all modules

- **config.py**: Unified configuration management
  - Load/save configuration files
  - Environment variable support
  - Configuration validation
  - Migration from old config formats

- **utils.py**: Common utilities
  - File hashing for deduplication
  - Logging setup
  - Path management
  - SD card detection helpers

### Flight Data Module (`avcardtool.flight_data`)

**Purpose**: Process flight logs from various manufacturers

#### Base Classes (`base/`)

Abstract interfaces that define the contract for processors and uploaders:

- **FlightDataProcessor**: Abstract base for all flight data processors
  ```python
  class FlightDataProcessor(ABC):
      @abstractmethod
      def detect_log_format(self, file_path: Path) -> bool

      @abstractmethod
      def parse_log(self, file_path: Path) -> FlightData

      @abstractmethod
      def extract_metadata(self, file_path: Path) -> dict
  ```

- **FlightDataUploader**: Abstract base for upload services
  ```python
  class FlightDataUploader(ABC):
      @abstractmethod
      def authenticate(self) -> bool

      @abstractmethod
      def upload_flight(self, flight_data: FlightData) -> bool
  ```

#### Processors (`processors/`)

Manufacturer-specific log file processors:

- **garmin_g3x.py**: Garmin G3X Touch CSV processor
  - Parse G3X CSV format
  - Extract airframe info from header
  - Handle 1Hz data format
  - Future: Add other Garmin formats (G1000, etc.)

- **Future processors**:
  - Dynon SkyView
  - Aspen Evolution
  - Avidyne Entegra

#### Analyzers (`analyzers/`)

Manufacturer-agnostic flight data analysis:

- **hobbs.py**: Airframe time (Hobbs) calculation
  - Multiple trigger modes (RPM, oil pressure, flight time)
  - Configurable thresholds

- **tach.py**: Engine time (Tach) calculation
  - Variable mode (proportional to RPM)
  - Fixed mode (above threshold)

- **oooi.py**: Out/Off/On/In time detection
  - Engine start/stop detection
  - Takeoff/landing detection
  - Block time and flight time calculation

- **flight_detector.py**: Distinguish flights from power-on cycles
  - Minimum flight time
  - Minimum ground speed
  - Minimum altitude change

#### Uploaders (`uploaders/`)

Service-specific upload implementations:

- **cloudahoy.py**: CloudAhoy API integration
- **flysto.py**: FlySto OAuth2 integration
- **savvy_aviation.py**: Savvy Aviation staging (no API)
- **maintenance_tracker.py**: Generic webhook uploader

### Navigation Database Module (`avcardtool.navdata`)

**Purpose**: Download and install aviation databases

#### SD Card Management (`sdcard.py`)

- Detect SD cards
- Validate filesystem (FAT32)
- Mount/unmount operations
- Track installed versions

#### Database Installer (`installer.py`)

- Extract database files
- Write to correct SD card structure
- Verify installations
- Create metadata files

#### Garmin Module (`garmin/`)

**Purpose**: Garmin-specific functionality (kept separate for upstream contributions)

This module is based on [jdmtool](https://github.com/dimaryaz/jdmtool) and can be contributed back upstream.

- **auth.py**: Garmin SSO authentication
  - Login to flyGarmin portal
  - Token management and refresh
  - Device registration

- **taw_parser.py**: TAW/AWP file format parser
  - Parse Garmin's proprietary format
  - Extract regions (navdata, terrain, obstacles, charts)
  - Handle multiple database types

- **downloader.py**: Database download from Garmin
  - List available databases
  - Download by subscription
  - Handle device-specific downloads

## CLI Design

Using Click for a unified command-line interface:

```bash
# Main command groups
avcardtool [OPTIONS] COMMAND [ARGS]...

# Flight data commands
avcardtool flight process [SD_CARD_PATH]
avcardtool flight analyze LOG_FILE
avcardtool flight list-processors
avcardtool flight upload LOG_FILE --service cloudahoy

# Navigation database commands
avcardtool navdata login
avcardtool navdata list-databases
avcardtool navdata download [--all | --indices 0,1,2]
avcardtool navdata install TAW_FILE [SD_CARD_PATH]
avcardtool navdata auto-update

# Combined automatic operation
avcardtool auto-process [SD_CARD_PATH]  # Does both flight data + navdata

# Configuration
avcardtool config show
avcardtool config edit
avcardtool config validate
```

## Configuration

Unified configuration file: `/etc/avcardtool/config.json`

```json
{
  "flight_data": {
    "enabled": true,
    "engine_time": {
      "mode": "variable",
      "minimum_recording_rpm": 500,
      "reference_rpm": 2700
    },
    "airframe_time": {
      "trigger": "oil_pressure",
      "oil_pressure_threshold": 5.0
    },
    "uploaders": {
      "cloudahoy": {
        "enabled": true,
        "api_token": "..."
      }
    }
  },
  "navdata": {
    "enabled": true,
    "auto_download": true,
    "garmin": {
      "email": "...",
      "databases": ["navdata", "terrain", "obstacles"]
    }
  },
  "system": {
    "data_dir": "/var/lib/avcardtool",
    "log_file": "/var/log/avcardtool.log"
  }
}
```

## Systemd Integration

Single unified service that handles both functions:

**Udev Rule** (`99-avcardtool-sdcard.rules`):
```
ACTION=="add", KERNEL=="sd[a-z][0-9]", SUBSYSTEM=="block", \
    ENV{ID_FS_TYPE}=="vfat", \
    TAG+="systemd", ENV{SYSTEMD_WANTS}="avcardtool-processor@%k.service"
```

**Systemd Service** (`avcardtool-processor@.service`):
```ini
[Unit]
Description=AVCardTool Processor for %i
BindsTo=dev-%i.device
After=dev-%i.device

[Service]
Type=oneshot
ExecStart=/usr/local/bin/avcardtool auto-process /dev/%i
```

## Data Flow

### SD Card Insertion

1. **Udev** detects SD card insertion
2. **Systemd** starts `avcardtool-processor@` service
3. **CLI** invokes `auto-process` command
4. **Flight Data Module**:
   - Detects and parses flight logs
   - Analyzes flights (Hobbs/Tach/OOOI)
   - Uploads to configured services
5. **Navdata Module**:
   - Checks for new database updates
   - Downloads if needed
   - Installs to SD card
6. **Systemd** completes and unmounts

## Extension Points

### Adding a New Flight Data Processor

1. Create new processor in `processors/`:
   ```python
   from avcardtool.flight_data.base import FlightDataProcessor

   class DynonSkyViewProcessor(FlightDataProcessor):
       def detect_log_format(self, file_path):
           # Check if this is a Dynon log
           pass

       def parse_log(self, file_path):
           # Parse Dynon-specific format
           pass
   ```

2. Register in `processors/__init__.py`:
   ```python
   PROCESSORS = [
       GarminG3XProcessor,
       DynonSkyViewProcessor,
   ]
   ```

### Adding a New Upload Service

1. Create new uploader in `uploaders/`:
   ```python
   from avcardtool.flight_data.base import FlightDataUploader

   class MyServiceUploader(FlightDataUploader):
       def authenticate(self):
           pass

       def upload_flight(self, flight_data):
           pass
   ```

2. Register in configuration schema

### Adding Support for Another Manufacturer's Databases

1. Create new module: `avcardtool.navdata.dynon/`
2. Implement similar interfaces to `garmin/`
3. Keep separate for potential upstream contribution

## Testing Strategy

- **Unit tests**: Test individual components in isolation
- **Integration tests**: Test module interactions
- **End-to-end tests**: Test full workflows with sample data
- **Mock external services**: Don't require actual Garmin/CloudAhoy accounts

## Migration Path

For existing users:

1. **Configuration migration**: Auto-convert old config files
2. **Data preservation**: Migrate processed file databases
3. **Service names**: New systemd service names (with deprecation notices)
4. **CLI compatibility**: Provide aliases for old command names

## Benefits of This Architecture

1. **Separation of Concerns**: Each module has a clear, focused purpose
2. **Testability**: Modules can be tested independently
3. **Extensibility**: Easy to add new manufacturers/services
4. **Upstream Contributions**: Garmin navdata module can be contributed back to jdmtool
5. **Maintainability**: Clear structure makes code easier to understand and modify
6. **Single Installation**: One install script, one service, one CLI
7. **Backward Compatibility**: Migration path for existing users
