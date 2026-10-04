#!/usr/bin/env bash
# Install quick-code for the current user: ~/.local/bin, app menu, autostart.
# Usage: ./install.sh [--uninstall]
set -euo pipefail

repo="$(cd "$(dirname "$0")" && pwd)"
bin="$HOME/.local/bin/quick-code"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications/quick-code.desktop"
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/quick-code.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    pkill -f "(bin/quick-code|quick_code\.py)( |$)" 2>/dev/null || true
    rm -f "$bin" "$apps" "$autostart"
    echo "Uninstalled. Config kept at ${XDG_CONFIG_HOME:-$HOME/.config}/quick-code/"
    exit 0
fi

mkdir -p "$(dirname "$bin")" "$(dirname "$apps")" "$(dirname "$autostart")"
ln -sf "$repo/quick_code.py" "$bin"
install -m 644 "$repo/quick-code.desktop" "$apps"
install -m 644 "$repo/quick-code.desktop" "$autostart"
echo "Installed. Start now with: quick-code &"
