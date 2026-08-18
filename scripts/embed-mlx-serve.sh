#!/bin/sh
set -eu

archive=$1
bundle=$2
target="$bundle/Contents/Helpers/mlx-serve"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

tar -xzf "$archive" -C "$work"
source_dir="$work/mlx-serve-macos-arm64"
[ -x "$source_dir/mlx-serve" ] || { echo "mlx-serve binary missing from archive" >&2; exit 1; }
rm -rf "$target"
mkdir -p "$(dirname "$target")"
cp -R "$source_dir" "$target"
chmod 755 "$target/mlx-serve"
# GitHub's macOS runner can be older than the binary's deployment target.
# Validate the artifact without launching it; release verification on a
# supported Mac exercises the executable itself.
file "$target/mlx-serve" | grep -Eq 'Mach-O .*arm64'
