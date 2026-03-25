# AVCardTool - Architecture Design

## Overview

This project merges two separate tools into one modular system:
1. **Flight Data Processor** - Processes flight logs and uploads to tracking services.
2. **Navigation Database Updater** - Downloads and installs aviation databases.

## Design Goals

1. **Modular Architecture**: Separate concerns to allow independent development.
2. **Manufacturer-Agnostic Flight Data**: Support multiple avionics manufacturers.
3. **Upstream Contribution**: Keep Garmin navdata code separate for upstreaming.
4. **Professional Deployment**: High-reliability standalone binaries for Raspberry Pi.
5. **Unified CLI**: Single command-line interface with subcommands.

## High-Reliability Deployment Strategy

To address the common "dependency mess" and runtime instability of Python in headless environments (like a Raspberry Pi), AVCardTool employs a professional build strategy:

### 1. Dependency Locking (Poetry)
The project uses **Poetry** to manage dependencies. This ensures that every build uses the exact same versions of all transitive dependencies, preventing breakage from upstream package updates.

### 2. Standalone Binary Compilation (Nuitka)
Instead of distributing source code, AVCardTool is compiled into a standalone machine-code binary using **Nuitka**.
- **Self-Contained**: The binary includes the Python interpreter and all libraries.
- **High Performance**: Nuitka translates Python into optimized C++ before compilation.
- **Zero-Dependency Target**: No `python3` or `pip` installation is required on the target Raspberry Pi.

### 3. Automated CI/CD (GitHub Actions)
A GitHub Actions workflow (`release.yml`) automates the build process:
- Uses **QEMU** to emulate ARM64 (aarch64) environments.
- Compiles the binary on every release tag.
- Packages the binary, `udev` rules, and `systemd` services into a **.deb** package.

## Project Structure

```
avcardtool/
├── src/
│   └── avcardtool/
│       ├── core/           # Configuration with standalone path discovery
│       ├── flight_data/    # Dynamic CSV parsing and analysis
│       └── navdata/        # Garmin SSO (aviation flow) and TAW parsing
├── pyproject.toml          # Poetry-backed dependencies
├── .github/workflows/      # Automated ARM64 release builds
├── package_deb.sh          # Professional packaging script
└── systemd/                # OS-level integration (udev/systemctl)
```

## Core Module Features

### Dynamic Column Mapping
The flight data module avoids brittle hardcoded CSV indices. It reads the Garmin header row and maps internal data fields to actual CSV column names dynamically, ensuring resilience against avionics firmware updates.

### Interactive MFA Login
Authentication with flyGarmin supports interactive Multi-Factor Authentication (MFA). The CLI handles the MFA callback, allowing users to securely log in once on the Pi and store persistent session tokens for the background service.

### Standalone Path Discovery
The `Config` module uses intelligent path resolution to locate `/etc/avcardtool/config.json` whether running from source or from within a bundled Nuitka binary.

## Data Flow

### SD Card Insertion
1. **Udev** detects a `vfat` block device and triggers a systemd service.
2. **Systemd** executes `avcardtool auto-process /dev/sdx`.
3. **Flight Data Module**:
   - Deduplicates files via a local database (`processed_files.json`).
   - Analyzes logs for Hobbs/Tach/OOOI events.
   - Uploads to enabled services (CloudAhoy, FlySto, etc.).
4. **Navdata Module**:
   - Checks for new database versions on Garmin's servers.
   - Downloads and extracts `.taw` files directly to the SD card.
5. **Systemd** cleans up and the card is ready for use.

## Testing Strategy

- **Unit Tests**: Full coverage for analysis logic (Hobbs, Tach, OOOI).
- **Integration Tests**: Verifies interaction between modules and CLI.
- **Emulated Hardware Tests**: Mocks SD card insertions and Garmin API responses.
- **CI Verification**: GitHub Actions runs the full test suite on every push.

## Benefits of This Architecture

1. **Stability**: Standalone binaries cannot be broken by system-level Python updates.
2. **Reproducibility**: Poetry ensures bit-for-bit identical builds.
3. **Professionalism**: Distribution via `.deb` packages follows Linux standards.
4. **Maintenance**: Modular design allows adding new avionics (Dynon/Aspen) by implementing a single interface.
