#!/bin/bash
#
# Installation Script for AVCardTool
#
# Installs the Python package into a per-user virtual environment and
# configures the system-level components (udev rules, systemd service)
# that still require root.
#
# Usage: sudo ./install.sh
#        sudo bash -c "$(curl -sSL https://raw.githubusercontent.com/elvinzhou/g3xuploader/main/install.sh)"
#

set -e

INSTALL_VERSION="1.3.0"
VENV_DIR="/opt/avcardtool/venv"
SYMLINK="/usr/local/bin/avcardtool"

echo "======================================================================"
echo "AVCardTool v${INSTALL_VERSION} - Installation"
echo "======================================================================"
echo ""

# ---------------------------------------------------------------------------
# Require root (needed for udev, systemd, and /opt)
# ---------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)"
    exit 1
fi

# Detect the real (non-root) user who invoked sudo
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
    REAL_USER="$SUDO_USER"
else
    REAL_USER="$USER"
fi
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

echo "Installing for user: $REAL_USER (home: $REAL_HOME)"
echo ""

# User-space paths (owned by REAL_USER)
CONFIG_DIR="$REAL_HOME/.config/avcardtool"
DATA_DIR="$REAL_HOME/.local/share/avcardtool"
CONFIG_FILE="$CONFIG_DIR/config.json"

# ---------------------------------------------------------------------------
# Detect existing installation and confirm upgrade
# ---------------------------------------------------------------------------
if [ -x "$SYMLINK" ]; then
    CURRENT_VERSION=$(sudo -u "$REAL_USER" "$SYMLINK" --version 2>/dev/null | awk '{print $NF}' || true)
    if [ -n "$CURRENT_VERSION" ] && [ "$CURRENT_VERSION" != "$INSTALL_VERSION" ]; then
        echo "Existing installation found: v${CURRENT_VERSION}"
        echo "This will upgrade to:        v${INSTALL_VERSION}"
        echo ""
        read -r -p "Continue? [Y/n] " confirm < /dev/tty
        case "$confirm" in
            [nN][oO]|[nN]) echo "Aborted."; exit 0 ;;
        esac
        echo ""
    elif [ "$CURRENT_VERSION" = "$INSTALL_VERSION" ]; then
        echo "v${INSTALL_VERSION} is already installed."
        read -r -p "Reinstall? [Y/n] " confirm < /dev/tty
        case "$confirm" in
            [nN][oO]|[nN]) echo "Nothing to do."; exit 0 ;;
        esac
        echo ""
    fi
fi

# ---------------------------------------------------------------------------
# Step 1: System dependencies
# ---------------------------------------------------------------------------
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv udev util-linux
echo "Done"

# ---------------------------------------------------------------------------
# Step 2: Python virtual environment (owned by REAL_USER)
# ---------------------------------------------------------------------------
echo "[2/6] Setting up Python environment..."

# Remove old installation cleanly
rm -rf "$VENV_DIR"
rm -rf /opt/avcardtool  # recreate below
mkdir -p /opt/avcardtool
chown "$REAL_USER":"$REAL_USER" /opt/avcardtool

# Create venv as the real user
sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"

# Install the package
if [ -f "pyproject.toml" ] && [ -d "src/avcardtool" ]; then
    echo "Local source found — installing from source..."
    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install -e . -q
else
    echo "Installing avcardtool==${INSTALL_VERSION} from PyPI..."
    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install "avcardtool==${INSTALL_VERSION}" -q
fi

# Symlink into system PATH (still root-owned, pointing into the user venv)
rm -f "$SYMLINK"
rm -f /usr/local/bin/aviation-tools  # remove legacy
ln -sf "$VENV_DIR/bin/avcardtool" "$SYMLINK"

echo "Done ($(sudo -u "$REAL_USER" "$SYMLINK" --version 2>/dev/null || echo 'unknown version'))"

# ---------------------------------------------------------------------------
# Step 3: User-space config and data directories
# ---------------------------------------------------------------------------
echo "[3/6] Creating user directories..."

sudo -u "$REAL_USER" mkdir -p "$CONFIG_DIR"
sudo -u "$REAL_USER" mkdir -p "$DATA_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Generating default configuration..."
    sudo -u "$REAL_USER" "$SYMLINK" config generate "$CONFIG_FILE"
    echo "Default config created: $CONFIG_FILE"
else
    echo "Config already exists: $CONFIG_FILE"
fi

# Migrate legacy configs if present
for LEGACY in /etc/aviation_tools/config.json /etc/g3x_processor/config.json; do
    if [ -f "$LEGACY" ] && [ ! -f "${CONFIG_FILE}.legacy" ]; then
        cp "$LEGACY" "${CONFIG_FILE}.legacy"
        chown "$REAL_USER":"$REAL_USER" "${CONFIG_FILE}.legacy"
        echo "Legacy config copied to: ${CONFIG_FILE}.legacy (review and rename if wanted)"
    fi
done

echo "Done"

# ---------------------------------------------------------------------------
# Step 4: udev rules  (requires root — stays system-level)
# ---------------------------------------------------------------------------
echo "[4/6] Installing udev rules..."

UDEV_RULE_PATH="/etc/udev/rules.d/99-avcardtool-sdcard.rules"

if [ -f "systemd/99-avcardtool-sdcard.rules" ]; then
    cp systemd/99-avcardtool-sdcard.rules "$UDEV_RULE_PATH"
else
    echo "Downloading udev rules from GitHub..."
    curl -sSL \
        "https://raw.githubusercontent.com/elvinzhou/g3xuploader/v${INSTALL_VERSION}/systemd/99-avcardtool-sdcard.rules" \
        -o "$UDEV_RULE_PATH"
fi

chmod 644 "$UDEV_RULE_PATH"

# Remove legacy rules
rm -f /etc/udev/rules.d/99-aviation-sdcard.rules
rm -f /etc/udev/rules.d/99-g3x-sdcard.rules
rm -f /etc/udev/rules.d/99-g3x-db-sdcard.rules

udevadm control --reload-rules
udevadm trigger

echo "Done"

# ---------------------------------------------------------------------------
# Step 5: systemd service  (requires root — stays system-level)
# ---------------------------------------------------------------------------
echo "[5/6] Installing systemd service..."

SERVICE_SRC="systemd/avcardtool-processor@.service"
SERVICE_DEST="/lib/systemd/system/avcardtool-processor@.service"

TMP_SERVICE=""
if [ ! -f "$SERVICE_SRC" ]; then
    echo "Downloading service file from GitHub..."
    TMP_SERVICE=$(mktemp)
    curl -sSL \
        "https://raw.githubusercontent.com/elvinzhou/g3xuploader/v${INSTALL_VERSION}/systemd/avcardtool-processor@.service" \
        -o "$TMP_SERVICE"
    SERVICE_SRC="$TMP_SERVICE"
fi

# Substitute placeholders with real user paths
sed \
    -e "s|AVCARDTOOL_USER|${REAL_USER}|g" \
    -e "s|AVCARDTOOL_DATA_DIR|${DATA_DIR}|g" \
    -e "s|AVCARDTOOL_CONFIG_DIR|${CONFIG_DIR}|g" \
    "$SERVICE_SRC" > "$SERVICE_DEST"

[ -n "$TMP_SERVICE" ] && rm -f "$TMP_SERVICE"

chmod 644 "$SERVICE_DEST"

# Remove legacy services
systemctl disable aviation-processor@.service 2>/dev/null || true
systemctl disable g3x-processor@.service 2>/dev/null || true
rm -f /lib/systemd/system/aviation-processor@.service
rm -f /lib/systemd/system/g3x-processor@.service
rm -f /lib/systemd/system/g3x-db-updater@.service

systemctl daemon-reload

echo "Done"

# ---------------------------------------------------------------------------
# Step 6: Clean up legacy system paths (no longer used)
# ---------------------------------------------------------------------------
echo "[6/6] Cleaning up legacy system paths..."

for OLD_PATH in /var/lib/avcardtool /etc/avcardtool; do
    if [ -d "$OLD_PATH" ]; then
        echo "  Removing $OLD_PATH (data now lives in $DATA_DIR)"
        rm -rf "$OLD_PATH"
    fi
done

echo "Done"

# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "Installation Complete!"
echo "======================================================================"
echo ""
echo "Config file:   $CONFIG_FILE"
echo "Data/logs:     $DATA_DIR"
echo "Command:       avcardtool"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_FILE with your settings"
echo "  2. Configure upload service credentials"
echo "  3. Insert an SD card to test automatic processing"
echo ""
echo "Useful commands:"
echo "  avcardtool --help"
echo "  avcardtool config show"
echo "  avcardtool self-update"
echo "  journalctl -u avcardtool-processor@* -f"
echo ""
echo "======================================================================"
