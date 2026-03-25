#!/bin/bash
# package_deb.sh - Creates a .deb package for AVCardTool
set -e

VERSION="1.0.0"
ARCH="arm64"
PKG_NAME="avcardtool"
PKG_DIR="dist/${PKG_NAME}_${VERSION}_${ARCH}"

echo "Creating package structure in ${PKG_DIR}..."

# Create directory structure
mkdir -p "${PKG_DIR}/DEBIAN"
mkdir -p "${PKG_DIR}/usr/local/bin"
mkdir -p "${PKG_DIR}/etc/avcardtool"
mkdir -p "${PKG_DIR}/etc/udev/rules.d"
mkdir -p "${PKG_DIR}/lib/systemd/system"

# Copy binary
cp avcardtool-compiled "${PKG_DIR}/usr/local/bin/avcardtool"

# Copy system files
cp systemd/99-avcardtool-sdcard.rules "${PKG_DIR}/etc/udev/rules.d/"
cp systemd/avcardtool-processor@.service "${PKG_DIR}/lib/systemd/system/"

# Create control file
cat > "${PKG_DIR}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: AVCardTool Contributors
Description: Aviation SD card tool - Flight data processing and navdata management
 Unified tool for G3X flight logs and Garmin navigation databases.
EOF

# Create post-install script
cat > "${PKG_DIR}/DEBIAN/postinst" <<EOF
#!/bin/bash
set -e
echo "Reloading udev rules..."
udevadm control --reload-rules || true
echo "Reloading systemd daemon..."
systemctl daemon-reload || true
echo "Installation complete. Edit /etc/avcardtool/config.json to begin."
EOF
chmod 755 "${PKG_DIR}/DEBIAN/postinst"

# Build the package
dpkg-deb --build "${PKG_DIR}"
echo "Package created: dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
