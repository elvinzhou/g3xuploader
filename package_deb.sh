#!/bin/bash
#
# package_deb.sh - Builds a .deb package for AVCardTool
#
# The package bundles the Python wheel and installs it into a virtual
# environment during postinst. No C compilation or cross-compilation required.
#
# Prerequisites: poetry must be installed and `poetry build` must have been run.
# Usage: bash package_deb.sh
#

set -e

ARCH="arm64"
PKG_NAME="avcardtool"

# Derive version from the package itself (single source of truth)
VERSION=$(python3 -c "
import re, pathlib
text = pathlib.Path('src/avcardtool/__init__.py').read_text()
m = re.search(r'__version__\s*=\s*[\"\']([\d.]+)', text)
print(m.group(1))
")

WHEEL_FILE=$(ls dist/${PKG_NAME}-${VERSION}-*.whl 2>/dev/null | head -1)
if [ -z "$WHEEL_FILE" ]; then
    echo "Error: No wheel found in dist/ for version ${VERSION}."
    echo "Run 'poetry build' first."
    exit 1
fi

PKG_DIR="dist/${PKG_NAME}_${VERSION}_${ARCH}"

echo "Building ${PKG_NAME} v${VERSION} (${ARCH})..."
echo "Using wheel: ${WHEEL_FILE}"

# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------
rm -rf "$PKG_DIR"
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/opt/avcardtool/wheels"
mkdir -p "${PKG_DIR}/usr/local/bin"
mkdir -p "${PKG_DIR}/etc/udev/rules.d"
mkdir -p "${PKG_DIR}/lib/systemd/system"

# ---------------------------------------------------------------------------
# Bundle the wheel (installed at postinst time — no internet needed)
# ---------------------------------------------------------------------------
cp "$WHEEL_FILE" "${PKG_DIR}/opt/avcardtool/wheels/"

# ---------------------------------------------------------------------------
# System files
# ---------------------------------------------------------------------------
cp systemd/99-avcardtool-sdcard.rules "${PKG_DIR}/etc/udev/rules.d/"
# Service file is customised by postinst — copy the template
cp systemd/avcardtool-processor@.service "${PKG_DIR}/lib/systemd/system/"

# ---------------------------------------------------------------------------
# control
# ---------------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.9), python3-venv, python3-pip, udev, util-linux, curl
Maintainer: AVCardTool Contributors <noreply@github.com>
Description: Aviation SD card tool - flight data processing and navdata management
 Unified tool for G3X flight logs and Garmin navigation databases.
 Automatically processes flight data when an SD card is inserted and
 uploads to tracking services such as CloudAhoy, FlySto, and SavvyAviation.
EOF

# ---------------------------------------------------------------------------
# postinst
# ---------------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
set -e

VENV_DIR="/opt/avcardtool/venv"
SYMLINK="/usr/local/bin/avcardtool"
WHEEL=$(ls /opt/avcardtool/wheels/*.whl | head -1)

# Detect the real user (first non-root user with a login shell, fallback to pi)
REAL_USER=$(getent passwd | awk -F: '$3 >= 1000 && $7 !~ /nologin|false/ {print $1; exit}')
REAL_USER="${REAL_USER:-pi}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

CONFIG_DIR="$REAL_HOME/.config/avcardtool"
DATA_DIR="$REAL_HOME/.local/share/avcardtool"

echo "Setting up for user: $REAL_USER"

# Create venv and install from bundled wheel
python3 -m venv "$VENV_DIR"
chown -R "$REAL_USER":"$REAL_USER" /opt/avcardtool
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip -q
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install "$WHEEL" -q

# Symlink
ln -sf "$VENV_DIR/bin/avcardtool" "$SYMLINK"

# User directories and default config
sudo -u "$REAL_USER" mkdir -p "$CONFIG_DIR" "$DATA_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
    sudo -u "$REAL_USER" "$SYMLINK" config generate "$CONFIG_DIR/config.json"
fi

# udev
udevadm control --reload-rules || true
udevadm trigger || true

# systemd service — substitute user placeholders
sed -i \
    -e "s|AVCARDTOOL_USER|${REAL_USER}|g" \
    -e "s|AVCARDTOOL_DATA_DIR|${DATA_DIR}|g" \
    -e "s|AVCARDTOOL_CONFIG_DIR|${CONFIG_DIR}|g" \
    /lib/systemd/system/avcardtool-processor@.service

systemctl daemon-reload || true

echo ""
echo "AVCardTool installed. Edit $CONFIG_DIR/config.json to configure."
POSTINST
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

# ---------------------------------------------------------------------------
# prerm
# ---------------------------------------------------------------------------
cat > "${PKG_DIR}/DEBIAN/prerm" <<'PRERM'
#!/bin/bash
set -e
systemctl stop 'avcardtool-processor@*.service' 2>/dev/null || true
systemctl daemon-reload || true
rm -f /usr/local/bin/avcardtool
rm -rf /opt/avcardtool
PRERM
chmod 755 "${PKG_DIR}/DEBIAN/prerm"

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
dpkg-deb --build "${PKG_DIR}"
echo ""
echo "Package ready: dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
