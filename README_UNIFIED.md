# Aviation Tools

**Unified flight data processing and navigation database management for general aviation**

Aviation Tools combines two essential capabilities for general aviation into one streamlined system:

1. **Flight Data Processing** - Automatically process flight logs, calculate Hobbs/Tach times, detect OOOI events, and upload to tracking services
2. **Navigation Database Management** - Download and install aviation databases (NavData, Terrain, Obstacles, Charts) to SD cards

## Features

### Flight Data Processing

- **Multi-Manufacturer Support**: Modular architecture supports multiple avionics manufacturers
  - Garmin G3X Touch (implemented)
  - Dynon, Aspen, Avidyne (coming soon)
- **Automatic SD Card Detection**: Processes data automatically when SD cards are inserted
- **Flight Detection**: Intelligently distinguishes actual flights from power-on cycles
- **Hobbs/Tach Calculation**: Configurable calculation modes mirror avionics settings
- **OOOI Times**: Automatic detection of Out/Off/On/In events
- **Multiple Upload Services**:
  - CloudAhoy
  - FlySto
  - Savvy Aviation staging
  - Custom maintenance trackers
- **Deduplication**: Tracks processed files to avoid re-uploading

### Navigation Database Management

- **Garmin Portal Authentication**: Secure login to flyGarmin for database access
- **TAW/AWP File Support**: Parse and extract Garmin's proprietary database formats
- **Automatic Updates**: Download latest subscribed databases
- **SD Card Installation**: Write files in G3X-compatible directory structure
- **Version Tracking**: Track installed database versions
- **Multiple Database Types**:
  - Navigation data
  - Terrain databases
  - Obstacle databases
  - FliteCharts

## Architecture

Aviation Tools uses a modular architecture that keeps concerns separated:

```
aviation-tools/
├── core/              # Shared configuration and utilities
├── flight_data/       # Flight data processing module
│   ├── base/          # Abstract interfaces
│   ├── processors/    # Manufacturer-specific parsers
│   ├── analyzers/     # Hobbs/Tach/OOOI analysis
│   └── uploaders/     # Upload service integrations
└── navdata/           # Navigation database module
    └── garmin/        # Garmin-specific (for upstream contribution)
```

This design enables:
- Easy addition of new manufacturers
- Independent development of each module
- Upstream contribution of Garmin navdata code
- Clear separation of concerns

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed architecture documentation.

## Installation

### Raspberry Pi (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/aviation-tools.git
cd aviation-tools

# Run the unified installer
sudo ./install_unified.sh
```

The installer will:
1. Install system dependencies
2. Create a Python virtual environment
3. Install the aviation-tools package
4. Set up configuration
5. Install udev rules for automatic SD card detection
6. Install systemd service

### Manual Installation

```bash
# Install dependencies
sudo apt-get install python3 python3-pip python3-venv

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install package
pip install -e .

# Generate default configuration
aviation-tools config generate config.json
```

## Configuration

Configuration is stored in `/etc/aviation_tools/config.json` (or `~/.config/aviation_tools/config.json`).

### Generate Default Configuration

```bash
aviation-tools config generate /etc/aviation_tools/config.json
```

### Configuration Structure

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
        "api_token": "your-token"
      }
    }
  },
  "navdata": {
    "enabled": true,
    "auto_download": true,
    "garmin": {
      "email": "your@email.com",
      "databases": ["navdata", "terrain", "obstacles"]
    }
  }
}
```

### Migrate Legacy Configuration

If you're upgrading from the original g3x_processor:

```bash
aviation-tools config migrate /etc/g3x_processor/config.json /etc/aviation_tools/config.json
```

## Usage

### Command-Line Interface

Aviation Tools provides a unified CLI with subcommands:

```bash
# View help
aviation-tools --help

# Configuration commands
aviation-tools config show                    # Show current config
aviation-tools config validate                # Validate config
aviation-tools config generate config.json    # Generate default config

# Flight data commands
aviation-tools flight process [PATH]          # Process flight data
aviation-tools flight analyze LOG_FILE        # Analyze single log
aviation-tools flight list-processors         # List supported formats

# Navigation database commands
aviation-tools navdata login                  # Login to Garmin
aviation-tools navdata list-databases         # List available databases
aviation-tools navdata download all           # Download databases
aviation-tools navdata install DB.taw         # Install to SD card
aviation-tools navdata auto-update            # Full automatic update

# Automatic processing (used by systemd)
aviation-tools auto-process [DEVICE]          # Process both flight data and navdata
```

### Automatic SD Card Processing

When you insert an SD card:

1. **Udev** detects the insertion
2. **Systemd** starts the aviation-processor service
3. **Flight data** is processed if enabled
4. **Navdata updates** are checked and installed if enabled
5. **Card is unmounted** when complete

View processing logs:

```bash
# Real-time logs
journalctl -u aviation-processor@* -f

# Application logs
tail -f /var/log/aviation_tools/aviation_tools.log
```

### Flight Data Processing

#### Engine Time (Tach)

Configure how engine time accrues:

**Variable Mode** (recommended for aircraft with constant-speed props):
```json
"engine_time": {
    "mode": "variable",
    "reference_rpm": 2700
}
```
- At 2700 RPM: 1 hour flight = 1.0 tach hour
- At 2400 RPM: 1 hour flight = 0.89 tach hour

**Fixed Mode** (for aircraft with fixed-pitch props):
```json
"engine_time": {
    "mode": "fixed",
    "minimum_recording_rpm": 500
}
```
- When RPM > 500: 1 hour flight = 1.0 tach hour

#### Airframe Time (Hobbs)

Configure when Hobbs time accrues:

```json
"airframe_time": {
    "trigger": "oil_pressure",         // "rpm", "oil_pressure", or "flight_time"
    "oil_pressure_threshold": 5.0      // PSI
}
```

#### Flight Detection

Avoid uploading non-flight power-on cycles:

```json
"flight_detection": {
    "minimum_flight_time_minutes": 5.0,
    "minimum_ground_speed_kts": 50.0,
    "minimum_altitude_change_ft": 200.0
}
```

### Navigation Database Management

#### One-Time Setup

```bash
# Login to Garmin
aviation-tools navdata login

# List available databases
aviation-tools navdata list-databases
```

#### Download and Install

```bash
# Download all subscribed databases
aviation-tools navdata download all

# Download specific databases
aviation-tools navdata download 0,1,2

# Install to SD card
aviation-tools navdata install database.taw

# All-in-one automatic update
aviation-tools navdata auto-update
```

## Upload Services

### CloudAhoy

CloudAhoy has an API but requires approval. Contact team@cloudahoy.com.

```json
"cloudahoy": {
    "enabled": true,
    "api_token": "your-oauth-token"
}
```

### FlySto

FlySto uses OAuth2. Contact support@flysto.net to register your application.

```json
"flysto": {
    "enabled": true,
    "client_id": "your-client-id",
    "client_secret": "your-client-secret"
}
```

### Savvy Aviation

Savvy doesn't have a public API. Files are staged for manual upload.

```json
"savvy_aviation": {
    "enabled": true,
    "email": "your@email.com"
}
```

### Custom Maintenance Tracker

POST JSON payload to your own API:

```json
"maintenance_tracker": {
    "enabled": true,
    "url": "https://your-tracker.com/api/flights",
    "api_key": "your-api-key"
}
```

## Extending Aviation Tools

### Adding Support for a New Manufacturer

1. Create a new processor in `aviation_tools/flight_data/processors/`:

```python
from aviation_tools.flight_data.base import FlightDataProcessor, FlightData

class DynonSkyViewProcessor(FlightDataProcessor):
    def detect_log_format(self, file_path):
        # Check if this is a Dynon log file
        ...

    def parse_log(self, file_path):
        # Parse Dynon-specific format
        ...

    def extract_metadata(self, file_path):
        # Extract metadata
        ...
```

2. Register the processor in `processors/__init__.py`

3. Test with sample log files

### Adding a New Upload Service

1. Create a new uploader in `aviation_tools/flight_data/uploaders/`:

```python
from aviation_tools.flight_data.base import FlightDataUploader, UploadResult

class MyServiceUploader(FlightDataUploader):
    def authenticate(self):
        # Authenticate with service
        ...

    def upload_flight(self, flight_data, analysis_results):
        # Upload to service
        ...
```

2. Add configuration schema to `config.py`

3. Test with actual flight data

## Troubleshooting

### SD Card Not Detected

```bash
# Check udev rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Test manually
aviation-tools auto-process /dev/sda1
```

### View Logs

```bash
# System logs
journalctl -u aviation-processor@* -f

# Application logs
tail -f /var/log/aviation_tools/aviation_tools.log
```

### Validate Configuration

```bash
aviation-tools config validate
```

### Permission Errors

```bash
# Ensure config is readable
sudo chmod 644 /etc/aviation_tools/config.json

# Check data directory permissions
ls -la /var/lib/aviation_tools
```

## Development

### Setup Development Environment

```bash
git clone https://github.com/yourusername/aviation-tools.git
cd aviation-tools

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black src/
isort src/
```

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=aviation_tools

# Specific test file
pytest tests/test_config.py
```

## Credits and Acknowledgments

This project combines and enhances two separate tools:

- **Flight Data Processor**: Original G3X flight data processing
- **Navigation Database Updater**: Based on [jdmtool](https://github.com/dimaryaz/jdmtool) by dimaryaz

Special thanks to:
- [jdmtool](https://github.com/dimaryaz/jdmtool) - TAW format research and Garmin authentication
- [garth](https://github.com/matin/garth) - Garmin SSO patterns
- The aviation community for testing and feedback

## Contributing

Contributions are welcome! The modular architecture makes it easy to add support for new manufacturers and services.

### Contributing Upstream

The `aviation_tools/navdata/garmin/` module is kept separate specifically to enable contributions back to [jdmtool](https://github.com/dimaryaz/jdmtool). If you improve the Garmin database handling, please consider contributing upstream.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Disclaimer

This tool is not affiliated with or endorsed by Garmin or any other avionics manufacturer. Always verify flight data and database installations through official methods before flight.

Use at your own risk. The authors are not responsible for any issues arising from use of this software.
