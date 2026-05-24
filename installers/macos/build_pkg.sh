#!/bin/bash
# Build macOS .pkg installer for CortexHarness context menu integration

set -e

# Default values
OUTPUT_DIR="dist"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--output DIR]"
            echo "Build macOS .pkg installer for CortexHarness"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "Building macOS .pkg installer"
echo "Output directory: $OUTPUT_DIR"

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Check for pkgbuild
if ! command -v pkgbuild &> /dev/null; then
    echo "[error] pkgbuild not found. This script requires macOS."
    echo "[info] pkgbuild is available on macOS by default."
    exit 1
fi

# Create package structure
PKG_ROOT="$(mktemp -d)"
echo "[temp] Package root: $PKG_ROOT"

# Copy workflows
WORKFLOWS_SRC="$PROJECT_ROOT/installers/macos/workflows"
WORKFLOWS_DST="$PKG_ROOT/Library/Services"

if [ -d "$WORKFLOWS_SRC" ]; then
    mkdir -p "$WORKFLOWS_DST"
    cp -r "$WORKFLOWS_SRC"/*.workflow "$WORKFLOWS_DST/" 2>/dev/null || echo "[warning] No workflows found"
    echo "[copy] Installed workflows"
else
    echo "[warning] Workflows directory not found: $WORKFLOWS_SRC"
fi

# Create post-install script
POST_INSTALL="$PKG_ROOT/post-install.sh"
cat > "$POST_INSTALL" << 'EOF'
#!/bin/bash
# Post-install script for CortexHarness context menu

echo "Refreshing system services..."
/System/Library/CoreServices/pbs -flush

echo "CortexHarness context menu installation complete!"
echo "You may need to log out and log back in for changes to take effect."
EOF

chmod +x "$POST_INSTALL"

# Build package
PKG_OUTPUT="$OUTPUT_DIR/cortex-harness-macos.pkg"
echo "[building] Creating package: $PKG_OUTPUT"

pkgbuild \
    --root "$PKG_ROOT" \
    --output "$PKG_OUTPUT" \
    --install-location "/" \
    --scripts "$PKG_ROOT" \
    --identifier "com.cortex.harness.contextmenu" \
    --version "1.0.0" \
    --ownership recommend

# Clean up
rm -rf "$PKG_ROOT"

echo "[success] macOS installer created: $PKG_OUTPUT"
echo "[info] Install with: sudo installer -pkg $PKG_OUTPUT -target /"