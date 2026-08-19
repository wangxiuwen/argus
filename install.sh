#!/bin/sh
# Fermi installer — run from inside the unpacked release directory.
set -e

APP_DIR="$HOME/Applications"
BIN_DIR="$HOME/.local/bin"
SHARE_DIR="$HOME/.local/share/argus"

mkdir -p "$APP_DIR" "$BIN_DIR" "$SHARE_DIR"

rm -rf "$APP_DIR/Fermi.app"
cp -R Fermi.app "$APP_DIR/"
# release archives arrive quarantined; the app is ad-hoc signed, so clear the flag
xattr -dr com.apple.quarantine "$APP_DIR/Fermi.app" 2>/dev/null || true

install -m 755 bin/argus "$BIN_DIR/argus"
install -m 755 bin/argus "$BIN_DIR/mira"
install -m 755 bin/argus "$BIN_DIR/fermi"
install -m 644 share/chat.py share/ui.py share/ui.html share/settings.html share/FermiIcon.png \
  share/launch.py share/bridge.py share/prune.py share/video.py share/music.py \
  share/image.py share/jobs.py share/iteration.py "$SHARE_DIR/"
mkdir -p "$SHARE_DIR/video-pipelines"
install -m 644 share/video-pipelines/*.vpipeline "$SHARE_DIR/video-pipelines/"

echo "Installed:"
echo "  $APP_DIR/Fermi.app"
echo "  $BIN_DIR/fermi"
echo
if ! command -v mlx_vlm.server >/dev/null 2>&1 && [ ! -x "$BIN_DIR/mlx_vlm.server" ]; then
  echo "Next: install the inference server"
  echo "  uv tool install -p 3.12 mlx-vlm     # or: pip install mlx-vlm"
  echo
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add $BIN_DIR to your PATH to use the fermi command."; echo ;;
esac
echo "Then: open $APP_DIR/Fermi.app   (or run: fermi start)"
