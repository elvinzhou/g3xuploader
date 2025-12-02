#!/bin/bash
#
# G3X Database Updater - Installation Script
# Run this on your Raspberry Pi or Linux system
#

set -e

echo "========================================"
echo "G3X Database Updater Installation"
echo "========================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./install.sh)"
    exit 1
fi

# Detect if we're on a Raspberry Pi
IS_RPI=false
if [ -f /proc/device-tree/model ]; then
    if grep -q "Raspberry Pi" /proc/device-tree/model; then
        IS_RPI=true
        echo "Detected: Raspberry Pi"
    fi
fi

# Install system dependencies
echo ""
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3 python3-pip python3-venv

# Create virtual environment (optional but recommended)
INSTALL_DIR="/opt/g3x-db-updater"
VENV_DIR="$INSTALL_DIR/venv"

echo ""
echo "Creating installation directory..."
mkdir -p "$INSTALL_DIR"
mkdir -p /var/lib/g3x_db_updater/pending
mkdir -p /var/lib/g3x_db_updater/downloads
mkdir -p /var/log

# Install Python package
echo ""
echo "Installing Python package..."

# Check if we're in the source directory
if [ -f "pyproject.toml" ]; then
    # Install from source
    pip3 install . --break-system-packages
else
    # Install from PyPI (when published)
    pip3 install g3x-database-updater --break-system-packages
fi

# Create symlink for CLI
if [ ! -L /usr/local/bin/g3x-db-updater ]; then
    # Find the installed script
    SCRIPT_PATH=$(which g3x-db-updater 2>/dev/null || echo "")
    if [ -n "$SCRIPT_PATH" ]; then
        ln -sf "$SCRIPT_PATH" /usr/local/bin/g3x-db-updater
    fi
fi

# Install systemd files
echo ""
echo "Installing systemd files..."
if [ -d "systemd" ]; then
    cp systemd/99-g3x-db-sdcard.rules /etc/udev/rules.d/
    cp systemd/g3x-db-updater@.service /lib/systemd/system/
fi

# Reload udev and systemd
echo ""
echo "Reloading udev and systemd..."
udevadm control --reload-rules
systemctl daemon-reload

# Set permissions
chown -R root:root /var/lib/g3x_db_updater
chmod 755 /var/lib/g3x_db_updater
chmod 755 /var/lib/g3x_db_updater/pending
chmod 755 /var/lib/g3x_db_updater/downloads

echo ""
echo "========================================"
echo "Installation Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Login to your Garmin account:"
echo "   sudo g3x-db-updater login"
echo ""
echo "2. Download your databases:"
echo "   sudo g3x-db-updater list-databases"
echo "   sudo g3x-db-updater download all"
echo ""
echo "3. Insert an SD card to write databases:"
echo "   - The system will auto-detect and prompt for writing"
echo "   - Or manually: sudo g3x-db-updater write /path/to/database.taw"
echo ""
echo "4. For automatic updates, download databases to the pending folder:"
echo "   sudo g3x-db-updater download -o /var/lib/g3x_db_updater/pending all"
echo ""
echo "Logs can be viewed with:"
echo "   journalctl -u g3x-db-updater@* -f"
echo ""

# Optional: Create a cron job for periodic database downloads
read -p "Set up weekly automatic database download? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    CRON_CMD="0 2 * * 0 /usr/local/bin/g3x-db-updater download -o /var/lib/g3x_db_updater/pending all"
    (crontab -l 2>/dev/null | grep -v "g3x-db-updater"; echo "$CRON_CMD") | crontab -
    echo "Weekly download scheduled for Sunday 2:00 AM"
fi

echo ""
echo "Installation complete!"
