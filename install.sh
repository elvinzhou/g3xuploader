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

INSTALL_VERSION="1.6.0"
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

# Snapshot first-run state before anything modifies the data directory.
# processed_files.json is created by auto-process on first SD card use;
# if it doesn't exist yet this is a genuine first run and we should trigger
# udev at the end (after setup is complete) so any card already in the reader
# gets processed with the correct config and historical-marking in place.
DB_FILE="$DATA_DIR/processed_files.json"
if [ -f "$DB_FILE" ]; then
    IS_FIRST_RUN="no"
else
    IS_FIRST_RUN="yes"
fi

# ---------------------------------------------------------------------------
# Stop any running avcardtool services before touching anything.
# Without this, an SD card already in the reader can trigger auto-process
# via the existing udev rule while the setup wizard is running, creating
# processed_files.json before first-run historical marking can take effect.
# ---------------------------------------------------------------------------
systemctl stop 'avcardtool-processor@*' 2>/dev/null || true
systemctl stop 'avcardtool-navdata@*' 2>/dev/null || true

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
apt-get install -y python3 python3-pip python3-venv udev util-linux fatattr
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
    echo "Installing avcardtool v${INSTALL_VERSION} from GitHub..."
    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install \
        "git+https://github.com/elvinzhou/g3xuploader.git@v${INSTALL_VERSION}" -q
fi

# Symlink into system PATH (still root-owned, pointing into the user venv)
rm -f "$SYMLINK"
rm -f /usr/local/bin/aviation-tools  # remove legacy
ln -sf "$VENV_DIR/bin/avcardtool" "$SYMLINK"

echo "Done ($(sudo -u "$REAL_USER" "$SYMLINK" --version 2>/dev/null || echo 'unknown version'))"

# ---------------------------------------------------------------------------
# Step 3: Clean up legacy system paths before config generation
# ---------------------------------------------------------------------------
echo "[3/6] Cleaning up legacy system paths..."

for OLD_PATH in /var/lib/avcardtool /etc/avcardtool; do
    if [ -d "$OLD_PATH" ]; then
        echo "  Removing $OLD_PATH"
        rm -rf "$OLD_PATH"
    fi
done

echo "Done"

# ---------------------------------------------------------------------------
# Step 4: User-space config and data directories
# ---------------------------------------------------------------------------
echo "[4/6] Creating user directories..."

sudo -u "$REAL_USER" mkdir -p "$CONFIG_DIR"
sudo -u "$REAL_USER" mkdir -p "$DATA_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Launching setup wizard..."
    echo ""
    sudo -u "$REAL_USER" "$SYMLINK" setup --config-path "$CONFIG_FILE" < /dev/tty
else
    echo "Config already exists: $CONFIG_FILE (skipping setup wizard)"
    # Patch stale /var/log path from old installs
    if grep -q '"/var/log/' "$CONFIG_FILE" 2>/dev/null; then
        NEW_LOG_PATH="${DATA_DIR}/avcardtool.log"
        sed -i "s|\"log_file\": \"/var/log/[^\"]*\"|\"log_file\": \"${NEW_LOG_PATH}\"|" "$CONFIG_FILE"
        echo "  Patched stale log_file path -> $NEW_LOG_PATH"
    fi
    # Patch stale /var/lib/avcardtool data_dir from old installs
    if grep -q '"/var/lib/avcardtool"' "$CONFIG_FILE" 2>/dev/null; then
        sed -i "s|\"data_dir\": \"/var/lib/avcardtool\"|\"data_dir\": \"${DATA_DIR}\"|" "$CONFIG_FILE"
        echo "  Patched stale data_dir path -> $DATA_DIR"
    fi
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
# Read wizard choices from the saved config
# ---------------------------------------------------------------------------
_cfg_bool() {
    local KEY="$1"
    local DEFAULT="$2"
    python3 -c "
import json, sys
try:
    d = json.load(open('${CONFIG_FILE}'))
    v = d.get('system', {}).get('${KEY}')
    print('yes' if v else 'no')
except Exception:
    print('${DEFAULT}')
" 2>/dev/null || echo "$DEFAULT"
}

ENABLE_FLIGHT_PROC=$(_cfg_bool "auto_process_flights" "no")
ENABLE_NAVDATA=$(_cfg_bool "auto_update_navdata" "no")

echo "Features enabled by setup:"
echo "  Flight log processing:      $ENABLE_FLIGHT_PROC"
echo "  Navdata auto-update:        $ENABLE_NAVDATA"
echo ""

# ---------------------------------------------------------------------------
# Step 5: udev rules + polkit  (only if at least one feature is active)
# ---------------------------------------------------------------------------
if [ "$ENABLE_FLIGHT_PROC" = "yes" ] || [ "$ENABLE_NAVDATA" = "yes" ]; then
    echo "[5/6] Installing udev rules and polkit policy..."

    UDEV_RULE_PATH="/etc/udev/rules.d/99-avcardtool-sdcard.rules"

    if [ -f "systemd/99-avcardtool-sdcard.rules" ]; then
        cp systemd/99-avcardtool-sdcard.rules "$UDEV_RULE_PATH"
    else
        echo "  Downloading udev rules from GitHub..."
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
    # Do NOT trigger -- re-triggering udev events for already-connected block
    # devices would immediately fire auto-process on any SD card already in the
    # reader, racing with first-run historical marking. New insertions will
    # pick up the updated rules automatically.

    # polkit rule: allow service user to mount/unmount via udisks2 without a TTY
    POLKIT_RULE_PATH="/etc/polkit-1/rules.d/99-avcardtool.rules"
    if [ -f "systemd/99-avcardtool.rules" ]; then
        sed "s|AVCARDTOOL_USER|${REAL_USER}|g" \
            systemd/99-avcardtool.rules > "$POLKIT_RULE_PATH"
    else
        echo "  Downloading polkit rules from GitHub..."
        curl -sSL \
            "https://raw.githubusercontent.com/elvinzhou/g3xuploader/v${INSTALL_VERSION}/systemd/99-avcardtool.rules" \
            | sed "s|AVCARDTOOL_USER|${REAL_USER}|g" > "$POLKIT_RULE_PATH"
    fi
    chmod 644 "$POLKIT_RULE_PATH"

    echo "Done"
else
    echo "[5/6] Skipping udev rules (no automated features enabled)"
fi

# ---------------------------------------------------------------------------
# Step 6: systemd services  (only install services for enabled features)
# ---------------------------------------------------------------------------
echo "[6/6] Installing systemd services..."

install_service() {
    local NAME="$1"
    local SRC="systemd/${NAME}"
    local DEST="/lib/systemd/system/${NAME}"
    local TMP=""

    if [ ! -f "$SRC" ]; then
        echo "  Downloading ${NAME} from GitHub..."
        TMP=$(mktemp)
        curl -sSL \
            "https://raw.githubusercontent.com/elvinzhou/g3xuploader/v${INSTALL_VERSION}/systemd/${NAME}" \
            -o "$TMP"
        SRC="$TMP"
    fi

    sed \
        -e "s|AVCARDTOOL_USER|${REAL_USER}|g" \
        -e "s|AVCARDTOOL_DATA_DIR|${DATA_DIR}|g" \
        -e "s|AVCARDTOOL_CONFIG_DIR|${CONFIG_DIR}|g" \
        "$SRC" > "$DEST"

    [ -n "$TMP" ] && rm -f "$TMP"
    chmod 644 "$DEST"
    echo "  Installed ${DEST}"
}

INSTALLED_SERVICES=0

if [ "$ENABLE_FLIGHT_PROC" = "yes" ]; then
    install_service "avcardtool-processor@.service"
    INSTALLED_SERVICES=$((INSTALLED_SERVICES + 1))
else
    rm -f /lib/systemd/system/avcardtool-processor@.service
fi

if [ "$ENABLE_NAVDATA" = "yes" ]; then
    install_service "avcardtool-navdata@.service"
    INSTALLED_SERVICES=$((INSTALLED_SERVICES + 1))
else
    rm -f /lib/systemd/system/avcardtool-navdata@.service
fi

# Remove legacy services
systemctl disable aviation-processor@.service 2>/dev/null || true
systemctl disable g3x-processor@.service 2>/dev/null || true
rm -f /lib/systemd/system/aviation-processor@.service
rm -f /lib/systemd/system/g3x-processor@.service
rm -f /lib/systemd/system/g3x-db-updater@.service

systemctl daemon-reload

if [ "$INSTALLED_SERVICES" -eq 0 ]; then
    echo "  No services installed (no automated features enabled)"
fi
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
echo "  1. Insert your SD card — processing starts automatically"
echo "  2. Monitor progress:"
echo "     journalctl -u 'avcardtool-*@*' -f"
echo ""
echo "To re-run setup or change credentials:"
echo "  avcardtool setup"
echo ""
echo "Useful commands:"
echo "  avcardtool --help"
echo "  avcardtool config show"
echo "  avcardtool self-update"
echo ""
echo "======================================================================"

# ---------------------------------------------------------------------------
# If this was a first run and automated features are enabled, trigger udev
# now that setup is complete. Any SD card already in the reader will be
# processed with the correct config and historical-marking logic in place.
# ---------------------------------------------------------------------------
if [ "$IS_FIRST_RUN" = "yes" ] && { [ "$ENABLE_FLIGHT_PROC" = "yes" ] || [ "$ENABLE_NAVDATA" = "yes" ]; }; then
    echo ""
    echo "First-run detected — triggering udev for any SD card already inserted..."
    udevadm trigger --subsystem-match=block
fi
