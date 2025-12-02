#!/bin/bash
#
# G3X Flight Data Processor - Installation Script
# Run this on your Raspberry Pi to set up automatic SD card processing
#

set -e

echo "========================================"
echo "G3X Flight Data Processor Installation"
echo "========================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./install.sh)"
    exit 1
fi

# Install dependencies
echo ""
echo "Installing Python dependencies..."
apt-get update
apt-get install -y python3 python3-pip
pip3 install requests

# Create directories
echo ""
echo "Creating directories..."
mkdir -p /etc/g3x_processor
mkdir -p /var/lib/g3x_processor
mkdir -p /var/log

# Install the main script
echo ""
echo "Installing main processor script..."
cp g3x_processor.py /usr/local/bin/g3x_processor.py
chmod +x /usr/local/bin/g3x_processor.py

# Install config file (only if it doesn't exist)
if [ ! -f /etc/g3x_processor/config.json ]; then
    echo "Installing default configuration..."
    cp config.json /etc/g3x_processor/config.json
    echo ""
    echo "IMPORTANT: Edit /etc/g3x_processor/config.json to configure your settings!"
else
    echo "Configuration file already exists, not overwriting."
fi

# Install udev rule
echo ""
echo "Installing udev rule..."
cp 99-g3x-sdcard.rules /etc/udev/rules.d/99-g3x-sdcard.rules

# Install systemd service
echo ""
echo "Installing systemd service..."
cp g3x-processor@.service /lib/systemd/system/g3x-processor@.service

# Reload udev and systemd
echo ""
echo "Reloading udev and systemd..."
udevadm control --reload-rules
systemctl daemon-reload

# Set permissions
chown -R root:root /etc/g3x_processor
chmod 600 /etc/g3x_processor/config.json  # Protect API keys

echo ""
echo "========================================"
echo "Installation Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Edit the configuration file:"
echo "   sudo nano /etc/g3x_processor/config.json"
echo ""
echo "2. Configure your settings:"
echo "   - Set your aircraft tail number"
echo "   - Configure Hobbs trigger (rpm, oil_pressure, or flight_time)"
echo "   - Configure Tach mode (variable or fixed)"
echo "   - Add your upload service credentials"
echo ""
echo "3. Test with an SD card:"
echo "   - Insert a G3X SD card"
echo "   - Check the log: journalctl -u g3x-processor@* -f"
echo ""
echo "4. Or test manually:"
echo "   sudo g3x_processor.py /path/to/mounted/sdcard"
echo ""
echo "For CloudAhoy API access, contact team@cloudahoy.com"
echo "For Savvy Aviation, files are staged in /var/lib/g3x_processor/savvy_staging/"
echo ""
