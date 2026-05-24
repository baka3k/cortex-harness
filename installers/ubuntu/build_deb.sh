#!/bin/bash
# Build Ubuntu .deb package for CortexHarness context menu integration

set -e

# Default values
OUTPUT_DIR="dist"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VERSION="1.0.0"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--output DIR] [--version VERSION]"
            echo "Build Ubuntu .deb package for CortexHarness"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "Building Ubuntu .deb package"
echo "Version: $VERSION"
echo "Output directory: $OUTPUT_DIR"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Check for dpkg-deb
if ! command -v dpkg-deb &> /dev/null; then
    echo "[error] dpkg-deb not found. Please install dpkg-dev: sudo apt-get install dpkg-dev"
    exit 1
fi

# Create package structure
PKG_ROOT="$(mktemp -d)"
PKG_DIR="$PKG_ROOT/cortex-harness-contextmenu_$VERSION"

echo "[temp] Package root: $PKG_DIR"

# Create directory structure
mkdir -p "$PKG_DIR/DEBIAN"
mkdir -p "$PKG_DIR/usr/share/nautilus-scripts/CortexHarness"
mkdir -p "$PKG_DIR/usr/share/doc/cortex-harness-contextmenu"

# Copy scripts
SCRIPTS_SRC="$PROJECT_ROOT/installers/ubuntu/scripts"
if [ -d "$SCRIPTS_SRC" ]; then
    cp "$SCRIPTS_SRC"/*.sh "$PKG_DIR/usr/share/nautilus-scripts/CortexHarness/" 2>/dev/null || echo "[warning] No scripts found"
    chmod +x "$PKG_DIR/usr/share/nautilus-scripts/CortexHarness/"*.sh
    echo "[copy] Installed Nautilus scripts"
else
    echo "[warning] Scripts directory not found: $SCRIPTS_SRC"
fi

# Create control file
cat > "$PKG_DIR/DEBIAN/control" << EOF
Package: cortex-harness-contextmenu
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: nautilus | gnome-terminal
Maintainer: CortexHarness Project <info@cortex-harness.org>
Description: CortexHarness file manager context menu integration
 This package adds CortexHarness commands to the Nautilus context menu,
 allowing users to right-click any folder and execute CortexHarness
 operations like code sync and document processing.
Homepage: https://github.com/your-org/cortex-harness
EOF

# Create post-install script
cat > "$PKG_DIR/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e

echo "CortexHarness context menu installation complete!"
echo "Restart Nautilus to see the new context menu entries."
echo "Run: nautilus -q"

# Attempt to restart Nautilus if it's running
if pgrep -x nautilus > /dev/null; then
    echo "Restarting Nautilus..."
    nautilus -q &
    sleep 2
    nautilus &
fi

exit 0
EOF

chmod +x "$PKG_DIR/DEBIAN/postinst"

# Create pre-remove script
cat > "$PKG_DIR/DEBIAN/prerm" << 'EOF'
#!/bin/bash
set -e

echo "Removing CortexHarness context menu integration..."

# Stop Nautilus if running to avoid file conflicts
if pgrep -x nautilus > /dev/null; then
    nautilus -q &
fi

exit 0
EOF

chmod +x "$PKG_DIR/DEBIAN/prerm"

# Create documentation
cat > "$PKG_DIR/usr/share/doc/cortex-harness-contextmenu/README" << 'EOF'
CortexHarness Context Menu Integration
========================================

This package adds CortexHarness commands to the Nautilus right-click menu.

AFTER INSTALLATION:
1. Restart Nautilus: nautilus -q
2. Right-click any folder
3. Select "Scripts" -> "CortexHarness"
4. Choose the desired command

AVAILABLE COMMANDS:
- Sync Code: Incrementally sync code changes
- Sync Documents: Process documents to vector database
- Run Harness: Execute AI agent task orchestration

TROUBLESHOOTING:
- If menu doesn't appear, restart Nautilus: nautilus -q
- Ensure scripts are executable: chmod +x ~/.local/share/nautilus/scripts/CortexHarness/*.sh
- Check that gnome-terminal is installed

UNINSTALLATION:
sudo apt-get remove cortex-harness-contextmenu
EOF

# Calculate installed size
INSTALLED_SIZE=$(du -sk "$PKG_DIR" | cut -f1)
echo "Installed-Size: $INSTALLED_SIZE" >> "$PKG_DIR/DEBIAN/control"

# Build the package
PKG_OUTPUT="$OUTPUT_DIR/cortex-harness-contextmenu_$VERSION_all.deb"
echo "[building] Creating package: $PKG_OUTPUT"

dpkg-deb --build "$PKG_DIR" "$PKG_OUTPUT"

# Clean up
rm -rf "$PKG_ROOT"

echo "[success] Ubuntu package created: $PKG_OUTPUT"
echo "[info] Install with: sudo dpkg -i $PKG_OUTPUT"
echo "[info] Or: sudo apt-get install ./$PKG_OUTPUT"