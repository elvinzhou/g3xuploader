# AVCardTool

**Unified flight data processing and navigation database management for general aviation**

AVCardTool combines two essential capabilities for general aviation into one streamlined system:

1. **Flight Data Processing** - Automatically process flight logs, calculate Hobbs/Tach times, detect OOOI events, and upload to tracking services.
2. **Navigation Database Management** - Download and install aviation databases (NavData, Terrain, Obstacles, Charts) to SD cards.

Built for reliability on Raspberry Pi and other Linux systems, AVCardTool is designed to run as a background service triggered by SD card insertions.

## Features

- **Automated Workflow**: Insert an SD card, and it handles everything from log uploads to database updates.
- **Poetry-Backed**: Strictly locked dependencies for reproducible environments.
- **Robust Garmin SSO**: Direct integration with flyGarmin (Aviation flow) with MFA support.
- **Automatic SD Card Detection**: Triggered by udev, processed by systemd.
- **Manufacturer-Agnostic**: Modular design to support various avionics (Garmin G3X, etc.).

## Installation

The recommended way to install AVCardTool is using the provided installation script, which sets up a dedicated virtual environment, system integration, and then automatically launches the setup wizard.

### One-Line Install

```bash
curl -sSL https://raw.githubusercontent.com/elvinzhou/g3xuploader/main/install.sh | sudo bash
```

The installer will:
1. Install system dependencies (`python3-venv`, `udev`, `fatattr`).
2. Create a virtual environment in `/opt/avcardtool/venv`.
3. Install `avcardtool` and its dependencies.
4. **Launch the interactive setup wizard** (new installs only).
5. Set up `udev` rules for automatic SD card detection.
6. Install the `systemd` services for background processing.

Re-running the installer on an existing installation upgrades the package and system files but skips the wizard to avoid overwriting your existing configuration.

## Setup Wizard

The setup wizard runs automatically at the end of a fresh install. You can also run it any time with:

```bash
avcardtool setup
```

The wizard covers four sections. Each major feature is opt-in — only its follow-up questions are shown if you enable it.

### System Settings

| Prompt | Default | Description |
|--------|---------|-------------|
| Data directory | `~/.local/share/avcardtool` | Where logs, tokens, and debug payloads are stored |
| Enable debug mode | No | Saves the exact payload sent to each service into `<data_dir>/debug/` before every upload, even if credentials aren't configured yet |

### Flight Log Processing

> **Enable automatic flight log processing?** *(default: Yes)*

If enabled, avcardtool will parse G3X flight logs, calculate Hobbs and Tach times, detect OOOI events, and upload to your chosen services whenever an SD card is inserted.

**Log processing settings** (only shown if enabled):

| Prompt | Options | Default | Description |
|--------|---------|---------|-------------|
| Hobbs time trigger | `oil_pressure`, `rpm`, `flight_time` | `oil_pressure` | What starts the Hobbs clock — oil pressure is most common for certified aircraft |
| Tach time mode | `variable`, `fixed` | `variable` | `variable` accrues time at the RPM/redline ratio; `fixed` runs 1:1 with the clock whenever the engine is running |
| Engine redline RPM | integer | `2700` | Only shown for `variable` mode — used as the denominator for tach ratio |

**Upload services** (only shown if flight processing is enabled):

You are asked whether to enable each service. Only enabled services will receive uploads when your SD card is inserted.

- **CloudAhoy** — API token (Bearer token from your CloudAhoy account)
- **FlySto** — OAuth2 Client ID and Client Secret (from FlySto developer settings), redirect URI (default: `http://localhost:8080/callback`). The wizard prints the authorization URL and offers to complete the OAuth code exchange inline. You can skip this and authorize later with `avcardtool flight flysto-auth <code>`.
- **Savvy Aviation** — No credentials required. Files are staged locally at `<data_dir>/savvy_staging/` for manual upload via their website.
- **Carryd** — API key (format: `eal_...`, obtained from the Carryd dashboard under **Settings → API Keys**) and an optional comma-separated list of engine logbook UUIDs (found under **Logbook → Settings**). Omit logbook UUIDs to update total airframe time only.

**First-run behavior** (only shown if flight processing is enabled):

AVCardTool tracks every processed file by hash. New users typically have months of existing flight history on their SD card. Since services like FlySto do not deduplicate, uploading everything at once would flood your account with old flights.

> **Mark all existing files as already processed on first SD card use?** *(Recommended: Yes)*

- **Yes** — on the very first SD card insertion, all files already on the card are marked as historical and skipped. Only flights recorded *after* setup will be uploaded.
- **No** — all files on the card are processed and uploaded on first insertion.

This only affects the first insertion. If a processed-files database already exists (e.g. on a re-install), this setting has no effect.

### Navigation Database Auto-Update

> **Enable automatic navigation database updates?** *(default: Yes)*

If enabled, avcardtool will check for and install updated Garmin NavData, terrain, obstacle, and chart databases whenever an SD card is inserted.

If you say Yes, the wizard prompts for your flyGarmin credentials and logs in immediately:

| Prompt | Description |
|--------|-------------|
| flyGarmin email | Your Garmin SSO email |
| flyGarmin password | Used only to obtain a session token — the password itself is not stored |
| MFA code | If your account has multi-factor authentication enabled |

On success, a session token is saved to `<data_dir>/` and used for all future database downloads. You can re-authenticate at any time with `avcardtool navdata login`.

### Saving Configuration

The wizard confirms where to write the config file (default: `~/.config/avcardtool/config.json` for non-root, `/etc/avcardtool/config.json` when running as root) and warns before overwriting an existing file.

The installer then reads the saved config and installs only the systemd services and udev rules needed for the features you enabled — if you enabled neither automated feature, no system-level rules are installed.

## Architecture

AVCardTool is designed with a modular, manufacturer-agnostic architecture. It keeps Garmin-specific logic separate to allow for easy upstream contributions to projects like `jdmtool`.

For deep technical details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Usage

### Command-Line Interface

```bash
# Post-install setup / re-configure
avcardtool setup                          # Interactive setup wizard

# Flight data commands
avcardtool flight analyze LOG_FILE        # Analyze single log
avcardtool flight upload LOG_FILE         # Upload a single log to enabled services
avcardtool flight flysto-auth <code>      # Complete FlySto OAuth after setup
avcardtool flight list-processors         # List supported formats
avcardtool flight list-uploaders          # Show configured upload services

# Navigation database commands
avcardtool navdata login                  # Authenticate with flyGarmin (MFA supported)
avcardtool navdata list-databases         # List available databases
avcardtool navdata download all           # Download subscribed databases
avcardtool navdata install DB.taw         # Install to SD card

# Configuration
avcardtool config show                    # Print current configuration
avcardtool config validate                # Validate configuration file

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
git clone https://github.com/elvinzhou/g3xuploader.git
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
