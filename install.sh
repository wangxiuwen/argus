#!/bin/sh
# Argus installer — run from inside the unpacked release directory.
set -e

APP_DIR="$HOME/Applications"
BIN_DIR="$HOME/.local/bin"
SHARE_DIR="$HOME/.local/share/argus"

mkdir -p "$APP_DIR" "$BIN_DIR" "$SHARE_DIR"

rm -rf "$APP_DIR/Argus.app"
cp -R Argus.app "$APP_DIR/"
# release archives arrive quarantined; the app is ad-hoc signed, so clear the flag
xattr -dr com.apple.quarantine "$APP_DIR/Argus.app" 2>/dev/null || true

install -m 755 bin/argus "$BIN_DIR/argus"
install -m 644 share/* "$SHARE_DIR/"

echo "Installed:"
echo "  $APP_DIR/Argus.app"
echo "  $BIN_DIR/argus"
echo
if ! command -v mlx_vlm.server >/dev/null 2>&1 && [ ! -x "$BIN_DIR/mlx_vlm.server" ]; then
  echo "Next: install the inference server"
  echo "  uv tool install -p 3.12 mlx-vlm     # or: pip install mlx-vlm"
  echo
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add $BIN_DIR to your PATH to use the argus command."; echo ;;
esac
echo "Then: open $APP_DIR/Argus.app   (or run: argus start)"
