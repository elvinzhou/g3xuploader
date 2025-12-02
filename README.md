# G3X Flight Data Processor

Automatically process Garmin G3X Touch flight logs, calculate Hobbs/Tach times, detect OOOI (Out/Off/On/In) times, and upload to flight tracking services.

## Features

- **Automatic SD Card Detection**: Processes G3X SD cards automatically when inserted
- **Flight Detection**: Intelligently distinguishes actual flights from power-on cycles
- **Hobbs/Tach Calculation**: Mirrors G3X configuration options
- **OOOI Times**: Automatically detects engine start/stop and takeoff/landing
- **Multiple Upload Services**: Savvy Aviation, CloudAhoy, and custom maintenance trackers
- **Deduplication**: Tracks processed files to avoid re-uploading

## Installation

### On Raspberry Pi

```bash
# Clone or copy the files to your Pi
cd g3x_flight_processor

# Run the installer
sudo ./install.sh

# Edit your configuration
sudo nano /etc/g3x_processor/config.json
```

### Manual Installation

```bash
# Install dependencies
sudo apt-get install python3 python3-pip
pip3 install requests

# Copy files
sudo cp g3x_processor.py /usr/local/bin/
sudo chmod +x /usr/local/bin/g3x_processor.py
sudo mkdir -p /etc/g3x_processor
sudo cp config.json /etc/g3x_processor/

# For automatic SD card detection:
sudo cp 99-g3x-sdcard.rules /etc/udev/rules.d/
sudo cp g3x-processor@.service /lib/systemd/system/
sudo udevadm control --reload-rules
sudo systemctl daemon-reload
```

## Configuration

Edit `/etc/g3x_processor/config.json`:

### Engine Time (Tach)

```json
"engine_time": {
    "mode": "variable",           // "variable" or "fixed"
    "minimum_recording_rpm": 500, // RPM threshold to start recording
    "reference_rpm": 2700         // For variable mode: RPM at which tach = 1:1
}
```

**Variable Mode**: Tach accrues at `(current_rpm / reference_rpm)` rate
- At 2700 RPM: 1 hour flight = 1.0 tach hour
- At 2400 RPM: 1 hour flight = 0.89 tach hour

**Fixed Mode**: Tach accrues at 1:1 when RPM > minimum_recording_rpm

### Airframe Time (Hobbs)

```json
"airframe_time": {
    "trigger": "oil_pressure",      // "rpm", "oil_pressure", or "flight_time"
    "rpm_threshold": 500,           // If trigger is "rpm"
    "oil_pressure_threshold": 5.0,  // If trigger is "oil_pressure" (PSI)
    "airborne_speed_threshold": 50.0 // If trigger is "flight_time" (knots)
}
```

### Flight Detection

Logs without actual flight are automatically filtered out:

```json
"flight_detection": {
    "minimum_flight_time_minutes": 5.0,   // Must be airborne this long
    "minimum_ground_speed_kts": 50.0,     // Must reach this speed
    "minimum_altitude_change_ft": 200.0,  // Must climb this much
    "minimum_data_points": 300            // At least 5 minutes of data
}
```

### Upload Services

#### CloudAhoy

CloudAhoy has an API but requires approval. Contact team@cloudahoy.com for access.

```json
"cloudahoy": {
    "enabled": true,
    "api_token": "your-oauth-token"
}
```

#### FlySto

FlySto uses OAuth2 authentication. You need to register your application with FlySto first.

1. Contact FlySto (support@flysto.net) to register your application
2. They will provide you with a `client_id` and `client_secret`
3. Configure your settings:

```json
"flysto": {
    "enabled": true,
    "client_id": "your-client-id",
    "client_secret": "your-client-secret",
    "redirect_uri": "http://localhost:8080/callback"
}
```

4. Complete the OAuth authorization (one-time setup):

```bash
# Get the authorization URL
g3x_processor.py --flysto-auth-url

# Open the URL in your browser, login to FlySto, grant permission
# You'll be redirected to: http://localhost:8080/callback?code=XXXXXX
# Copy the code value

# Exchange the code for tokens
g3x_processor.py --flysto-auth XXXXXX
```

After this setup, uploads will work automatically. Tokens are stored in
`/var/lib/g3x_processor/flysto_tokens.json` and automatically refreshed.

#### Savvy Aviation

Savvy Aviation doesn't have a public API. Files are staged to 
`/var/lib/g3x_processor/savvy_staging/` for manual upload.

```json
"savvy_aviation": {
    "enabled": true,
    "email": "your-email@example.com",
    "password": "your-password"
}
```

#### Custom Maintenance Tracker

POST JSON payload to your API:

```json
"maintenance_tracker": {
    "enabled": true,
    "url": "https://your-tracker.com/api/flights",
    "api_key": "your-api-key"
}
```

Payload format:
```json
{
    "aircraft_ident": "N12345",
    "date": "2024-01-15",
    "hobbs": {"start": 110.4, "end": 112.6, "increment": 2.16},
    "tach": {"start": 70.3, "end": 72.1, "increment": 1.83},
    "oooi": {
        "out": "2024-01-15T14:58:23",
        "off": "2024-01-15T15:02:57",
        "on": "2024-01-15T17:04:35",
        "in": "2024-01-15T17:07:54",
        "block_time_minutes": 129.5,
        "flight_time_minutes": 121.6
    }
}
```

## Usage

### Automatic Processing

Just insert the G3X SD card into the Raspberry Pi's USB card reader. The system will:

1. Detect the card insertion
2. Mount it read-only
3. Scan for G3X log files
4. Analyze each file
5. Skip non-flight logs (power-on only)
6. Calculate Hobbs/Tach for actual flights
7. Upload to configured services
8. Unmount the card

Check the log:
```bash
journalctl -u g3x-processor@* -f
```

### Manual Processing

```bash
# Process an SD card manually
sudo g3x_processor.py /media/g3x_sdcard

# Analyze a single file
g3x_processor.py --analyze /path/to/log_20241115_143000_KHHR.csv

# JSON output
g3x_processor.py --analyze /path/to/log.csv --json

# Generate sample config
g3x_processor.py --generate-config --config my_config.json
```

## File Structure

```
/etc/g3x_processor/
└── config.json              # Configuration file

/var/lib/g3x_processor/
├── processed_files.json     # Database of processed files
└── savvy_staging/           # Files staged for Savvy Aviation upload

/var/log/
└── g3x_processor.log        # Application log
```

## Troubleshooting

### SD card not detected

Check udev rules are loaded:
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Test manually:
```bash
sudo mount /dev/sda1 /mnt/test
sudo g3x_processor.py /mnt/test
```

### Permission errors

Ensure the config file is readable:
```bash
sudo chmod 644 /etc/g3x_processor/config.json
```

### View logs

```bash
# Live log
journalctl -u g3x-processor@* -f

# All processor logs
cat /var/log/g3x_processor.log
```

## CSV Format

The processor reads G3X CSV files with this format:

- Line 1: `#airframe_info,aircraft_ident="N12345",airframe_hours="110.4",engine_hours="70.3",...`
- Line 2: Full column headers
- Line 3: Short column headers
- Line 4+: Data at 1Hz

Key columns used:
- Column 1: Date (YYYY-MM-DD)
- Column 2: Time (HH:MM:SS)
- Column 10: GPS Ground Speed (kt)
- Column 20: Baro Altitude (ft)
- Column 86: RPM
- Column 87: Oil Press (PSI)

## License

MIT License - feel free to modify and distribute.
