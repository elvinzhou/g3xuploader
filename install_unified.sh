#!/bin/bash
#
# Unified Installation Script for Aviation Tools
#
# This script installs the aviation-tools package which combines:
#   1. Flight data processing (formerly g3x_processor)
#   2. Navigation database management (formerly g3x_db_updater)
#
# Usage: sudo ./install_unified.sh
#

set -e

echo "======================================================================"
echo "Aviation Tools - Unified Installation"
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
apt-get install -y python3 python3-pip python3-venv udev

# ============================================================================
# Step 2: Create virtual environment and install package
# ============================================================================

echo "[2/7] Setting up Python environment..."

# Remove old installations if they exist
rm -rf /opt/aviation_tools

# Create directory and virtual environment
mkdir -p /opt/aviation_tools
python3 -m venv /opt/aviation_tools/venv

# Install the package
echo "Installing aviation-tools package..."
/opt/aviation_tools/venv/bin/pip install --upgrade pip
/opt/aviation_tools/venv/bin/pip install -e .

# Create symlink for easy access
ln -sf /opt/aviation_tools/venv/bin/aviation-tools /usr/local/bin/aviation-tools

echo "✓ Package installed"

# ============================================================================
# Step 3: Create configuration directory
# ============================================================================

echo "[3/7] Creating configuration directories..."

# Create config directory
mkdir -p /etc/aviation_tools

# Generate default config if it doesn't exist
if [ ! -f /etc/aviation_tools/config.json ]; then
    echo "Generating default configuration..."
    /usr/local/bin/aviation-tools config generate /etc/aviation_tools/config.json
    chmod 644 /etc/aviation_tools/config.json
    echo "✓ Default configuration created: /etc/aviation_tools/config.json"
    echo "  Please edit this file to configure your settings"
else
    echo "✓ Configuration file already exists: /etc/aviation_tools/config.json"
fi

# Migrate legacy config if it exists
if [ -f /etc/g3x_processor/config.json ] && [ ! -f /etc/aviation_tools/config.json.migrated ]; then
    echo "Found legacy configuration, migrating..."
    /usr/local/bin/aviation-tools config migrate \
        /etc/g3x_processor/config.json \
        /etc/aviation_tools/config.json.migrated
    echo "✓ Legacy config migrated to: /etc/aviation_tools/config.json.migrated"
    echo "  Review and rename to config.json if you want to use it"
fi

# ============================================================================
# Step 4: Create data directories
# ============================================================================

echo "[4/7] Creating data directories..."

mkdir -p /var/lib/aviation_tools
mkdir -p /var/lib/aviation_tools/processed_files
mkdir -p /var/lib/aviation_tools/staging
mkdir -p /var/lib/aviation_tools/downloads

# Set permissions
chown -R root:root /var/lib/aviation_tools
chmod -R 755 /var/lib/aviation_tools

echo "✓ Data directories created"

# ============================================================================
# Step 5: Create log directory
# ============================================================================

echo "[5/7] Setting up logging..."

# Create log directory
mkdir -p /var/log/aviation_tools

# Create log file
touch /var/log/aviation_tools/aviation_tools.log
chmod 644 /var/log/aviation_tools/aviation_tools.log

echo "✓ Log directory created"

# ============================================================================
# Step 6: Install udev rules
# ============================================================================

echo "[6/7] Installing udev rules..."

# Create updated udev rule
cat > /etc/udev/rules.d/99-aviation-sdcard.rules << 'EOF'
# Aviation Tools - Automatic SD Card Processing
#
# Triggers when an SD card is inserted
# Starts aviation-processor@.service for the device

ACTION=="add", KERNEL=="sd[a-z][0-9]", SUBSYSTEM=="block", \
    ENV{ID_FS_TYPE}=="vfat", \
    TAG+="systemd", ENV{SYSTEMD_WANTS}="aviation-processor@%k.service"
EOF

chmod 644 /etc/udev/rules.d/99-aviation-sdcard.rules

# Remove old udev rules if they exist
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

# Create updated systemd service
cat > /lib/systemd/system/aviation-processor@.service << 'EOF'
[Unit]
Description=Aviation Tools Processor for %i
Documentation=https://github.com/yourusername/aviation-tools
BindsTo=dev-%i.device
After=dev-%i.device

[Service]
Type=oneshot
ExecStart=/usr/local/bin/aviation-tools auto-process /dev/%i
StandardOutput=journal
StandardError=journal
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

chmod 644 /lib/systemd/system/aviation-processor@.service

# Remove old systemd services if they exist
systemctl disable g3x-processor@.service 2>/dev/null || true
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
echo "Configuration file: /etc/aviation_tools/config.json"
echo "Command-line tool:  aviation-tools"
echo "Log file:          /var/log/aviation_tools/aviation_tools.log"
echo ""
echo "Next steps:"
echo "  1. Edit /etc/aviation_tools/config.json with your settings"
echo "  2. Configure upload service credentials"
echo "  3. Configure Garmin account for navdata downloads (if needed)"
echo "  4. Insert an SD card to test automatic processing"
echo ""
echo "Test the installation:"
echo "  aviation-tools --help"
echo "  aviation-tools config show"
echo "  aviation-tools flight list-processors"
echo ""
echo "View logs:"
echo "  journalctl -u aviation-processor@* -f"
echo "  tail -f /var/log/aviation_tools/aviation_tools.log"
echo ""
echo "======================================================================"
