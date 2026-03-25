# AVCardTool

**Unified flight data processing and navigation database management for general aviation**

AVCardTool combines two essential capabilities for general aviation into one streamlined system:

1. **Flight Data Processing** - Automatically process flight logs, calculate Hobbs/Tach times, detect OOOI events, and upload to tracking services.
2. **Navigation Database Management** - Download and install aviation databases (NavData, Terrain, Obstacles, Charts) to SD cards.

Built for reliability on Raspberry Pi, AVCardTool is distributed as a standalone binary to eliminate "dependency mess" and ensure a solid "Go-like" deployment experience.

## Features

- **Standalone Binary**: No Python environment required on the target machine.
- **Poetry-Backed**: Strictly locked dependencies for reproducible builds.
- **Robust Garmin SSO**: Direct integration with flyGarmin (Aviation flow) with MFA support.
- **Automatic SD Card Detection**: Triggered by udev, processed by systemd.
- **Professional Packaging**: Distributed as a `.deb` package for easy system integration.

## Installation (Raspberry Pi)

The recommended way to install AVCardTool on Raspberry Pi OS (64-bit) is via the `.deb` package.

### One-Line Install

Copy and paste this command into your terminal:

```bash
curl -sL $(curl -s https://api.github.com/repos/yourusername/g3xuploader/releases/latest | grep "browser_download_url.*arm64.deb" | cut -d '"' -f 4) -o avcardtool.deb && sudo apt install ./avcardtool.deb && rm avcardtool.deb
```

This command will:
1. Download the latest `.deb` package from GitHub.
2. Install the `avcardtool` binary to `/usr/local/bin/`.
3. Set up the `udev` rules for SD card detection.
4. Install the `systemd` service for background processing.
5. Create the configuration directory at `/etc/avcardtool/`.

## One-Time Setup

After installation, you need to authenticate with flyGarmin to enable database downloads:

```bash
# Interactive login with MFA support
avcardtool navdata login --email your@email.com
```

Then, generate and edit your configuration:

```bash
# Generate default config if it doesn't exist
sudo avcardtool config generate /etc/avcardtool/config.json

# Edit your config to enable uploaders (CloudAhoy, FlySto, etc.)
sudo nano /etc/avcardtool/config.json
```

## Architecture

AVCardTool is designed with a modular, manufacturer-agnostic architecture. It keeps Garmin-specific logic separate to allow for easy upstream contributions to projects like `jdmtool`.

For deep technical details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Usage

### Command-Line Interface

```bash
# Flight data commands
avcardtool flight analyze LOG_FILE        # Analyze single log
avcardtool flight list-processors         # List supported formats

# Navigation database commands
avcardtool navdata list-databases         # List available databases
avcardtool navdata download all           # Download subscribed databases
avcardtool navdata install DB.taw         # Install to SD card

# Automatic processing (used by systemd)
avcardtool auto-process [DEVICE]          # Process both flight data and navdata
```

### Automatic Processing

When an SD card is inserted:
1. **Udev** detects the card.
2. **Systemd** starts `avcardtool-processor@.service`.
3. **Flight logs** are parsed, analyzed (Hobbs/Tach/OOOI), and uploaded.
4. **Navdata updates** are automatically checked and installed.

View logs:
```bash
journalctl -u avcardtool-processor@* -f
```

## Development

AVCardTool uses **Poetry** for dependency management and **Nuitka** for standalone compilation.

### Setup

```bash
git clone https://github.com/yourusername/avcardtool.git
cd avcardtool
poetry install
```

### Run Tests

```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)/src
poetry run pytest
```

### Build Standalone Binary

```bash
poetry run python -m nuitka --onefile --standalone --include-package=avcardtool src/avcardtool/cli.py -o avcardtool
```

## License

MIT License. See [LICENSE](LICENSE) for details.

## Disclaimer

Not affiliated with Garmin. Always verify flight data and databases through official methods before flight. Use at your own risk.
