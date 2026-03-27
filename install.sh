#!/bin/bash
#
# Installation Script for AVCardTool
#
# This script installs the avcardtool package which combines:
#   1. Flight data processing (formerly g3x_processor)
#   2. Navigation database management (formerly g3x_db_updater)
#
# Usage: sudo ./install.sh
#

set -e

echo "======================================================================"
echo "AVCardTool - Installation"
echo "======================================================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)"
    exit 1
fi

# Detect the user who invoked sudo
if [ -n "$SUDO_USER" ]; then
    REAL_USER="$SUDO_USER"
else
    REAL_USER="$USER"
fi

echo "Installing for user: $REAL_USER"
echo ""

# ============================================================================
# Step 1: Install system dependencies
# ============================================================================

echo "[1/7] Installing system dependencies..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv udev util-linux

# ============================================================================
# Step 2: Create virtual environment and install package
# ============================================================================

echo "[2/7] Setting up Python environment..."

# Remove old installations if they exist
rm -rf /opt/avcardtool
rm -rf /opt/aviation_tools  # Remove legacy installation

# Create directory and virtual environment
mkdir -p /opt/avcardtool
python3 -m venv /opt/avcardtool/venv

# Install the package
echo "Installing avcardtool package..."
/opt/avcardtool/venv/bin/pip install --upgrade pip

# Detect if we're in a git clone or running standalone
if [ -f "pyproject.toml" ]; then
    echo "Local pyproject.toml found, installing from local source..."
    /opt/avcardtool/venv/bin/pip install -e .
else
    echo "No local source found, installing directly from GitHub..."
    /opt/avcardtool/venv/bin/pip install "git+https://github.com/elvinzhou/g3xuploader.git"
fi

# Create symlink for easy access
rm -f /usr/local/bin/aviation-tools  # Remove legacy symlink
ln -sf /opt/avcardtool/venv/bin/avcardtool /usr/local/bin/avcardtool

echo "✓ Package installed"

# ============================================================================
# Step 3: Create configuration directory
# ============================================================================

echo "[3/7] Creating configuration directories..."

# Create config directory
mkdir -p /etc/avcardtool

# Generate default config if it doesn't exist
if [ ! -f /etc/avcardtool/config.json ]; then
    echo "Generating default configuration..."
    /usr/local/bin/avcardtool config generate /etc/avcardtool/config.json
    chmod 644 /etc/avcardtool/config.json
    echo "✓ Default configuration created: /etc/avcardtool/config.json"
    echo "  Please edit this file to configure your settings"
else
    echo "✓ Configuration file already exists: /etc/avcardtool/config.json"
fi

# Migrate legacy config if it exists
if [ -f /etc/aviation_tools/config.json ] && [ ! -f /etc/avcardtool/config.json.migrated ]; then
    echo "Found legacy aviation_tools configuration, migrating..."
    cp /etc/aviation_tools/config.json /etc/avcardtool/config.json.migrated
    echo "✓ Legacy config copied to: /etc/avcardtool/config.json.migrated"
    echo "  Review and rename to config.json if you want to use it"
fi

if [ -f /etc/g3x_processor/config.json ] && [ ! -f /etc/avcardtool/config.json.g3x_legacy ]; then
    echo "Found legacy g3x_processor configuration..."
    cp /etc/g3x_processor/config.json /etc/avcardtool/config.json.g3x_legacy
    echo "✓ Legacy config copied to: /etc/avcardtool/config.json.g3x_legacy"
    echo "  Review and rename to config.json if you want to use it"
fi

# ============================================================================
# Step 4: Create data directories
# ============================================================================

echo "[4/7] Creating data directories..."

mkdir -p /var/lib/avcardtool
mkdir -p /var/lib/avcardtool/processed_files
mkdir -p /var/lib/avcardtool/staging
mkdir -p /var/lib/avcardtool/downloads

# Set permissions
chown -R root:root /var/lib/avcardtool
chmod -R 755 /var/lib/avcardtool

echo "✓ Data directories created"

# ============================================================================
# Step 5: Create log directory
# ============================================================================

echo "[5/7] Setting up logging..."

# Create log directory
mkdir -p /var/log/avcardtool

# Create log file
touch /var/log/avcardtool/avcardtool.log
chmod 644 /var/log/avcardtool/avcardtool.log

echo "✓ Log directory created"

# ============================================================================
# Step 6: Install udev rules
# ============================================================================

echo "[6/7] Installing udev rules..."

# Copy udev rule from systemd directory
cp systemd/99-avcardtool-sdcard.rules /etc/udev/rules.d/99-avcardtool-sdcard.rules
chmod 644 /etc/udev/rules.d/99-avcardtool-sdcard.rules

# Remove old udev rules if they exist
rm -f /etc/udev/rules.d/99-aviation-sdcard.rules
rm -f /etc/udev/rules.d/99-g3x-sdcard.rules
rm -f /etc/udev/rules.d/99-g3x-db-sdcard.rules

# Reload udev rules
udevadm control --reload-rules
udevadm trigger

echo "✓ Udev rules installed"

# ============================================================================
# Step 7: Install systemd service
# ============================================================================

echo "[7/7] Installing systemd service..."

# Copy systemd service from systemd directory
cp systemd/avcardtool-processor@.service /lib/systemd/system/avcardtool-processor@.service
chmod 644 /lib/systemd/system/avcardtool-processor@.service

# Remove old systemd services if they exist
systemctl disable aviation-processor@.service 2>/dev/null || true
systemctl disable g3x-processor@.service 2>/dev/null || true
rm -f /lib/systemd/system/aviation-processor@.service
rm -f /lib/systemd/system/g3x-processor@.service
rm -f /lib/systemd/system/g3x-db-updater@.service

# Reload systemd
systemctl daemon-reload

echo "✓ Systemd service installed"

# ============================================================================
# Installation Complete
# ============================================================================

echo ""
echo "======================================================================"
echo "Installation Complete!"
echo "======================================================================"
echo ""
echo "Configuration file: /etc/avcardtool/config.json"
echo "Command-line tool:  avcardtool"
echo "Log file:          /var/log/avcardtool/avcardtool.log"
echo ""
echo "Next steps:"
echo "  1. Edit /etc/avcardtool/config.json with your settings"
echo "  2. Configure upload service credentials"
echo "  3. Configure Garmin account for navdata downloads (if needed)"
echo "  4. Insert an SD card to test automatic processing"
echo ""
echo "Test the installation:"
echo "  avcardtool --help"
echo "  avcardtool config show"
echo "  avcardtool flight list-processors"
echo ""
echo "View logs:"
echo "  journalctl -u avcardtool-processor@* -f"
echo "  tail -f /var/log/avcardtool/avcardtool.log"
echo ""
echo "======================================================================"
