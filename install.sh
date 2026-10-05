#!/usr/bin/env bash
# Install quick-code for the current user: ~/.local/bin, app menu, autostart at login,
# and start it now.
# Usage: ./install.sh [--uninstall]
#    or: curl -fsSL https://raw.githubusercontent.com/tuzkituan/quick-code/main/install.sh | bash
set -euo pipefail

raw="https://raw.githubusercontent.com/tuzkituan/quick-code/main"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
bin="$HOME/.local/bin/quick-code"
apps="$data/applications/quick-code.desktop"
autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/quick-code.desktop"
downloaded="$data/quick-code"

stop_running() {
    pkill -f "^[^ ]*python[^ ]* [^ ]*(bin/quick-code|quick_code\.py)( |$)" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    stop_running
    rm -f "$bin" "$apps" "$autostart"
    rm -rf "$downloaded"
    echo "Uninstalled. Config kept at ${XDG_CONFIG_HOME:-$HOME/.config}/quick-code/"
    exit 0
fi

# Run from a checkout: link to it. Piped from curl: download into ~/.local/share/quick-code.
script="${BASH_SOURCE[0]:-}"
if [[ -n "$script" && -f "$(dirname "$script")/quick_code.py" ]]; then
    src="$(cd "$(dirname "$script")" && pwd)"
else
    src="$downloaded"
    mkdir -p "$src"
    for f in quick_code.py quick-code.desktop; do
        curl -fsSL "$raw/$f" -o "$src/$f"
    done
    chmod +x "$src/quick_code.py"
fi

mkdir -p "$(dirname "$bin")" "$(dirname "$apps")" "$(dirname "$autostart")"
ln -sf "$src/quick_code.py" "$bin"
# Absolute Exec: ~/.local/bin is often not on PATH yet when the session autostarts apps.
for dest in "$apps" "$autostart"; do
    sed "s|^Exec=.*|Exec=$bin|" "$src/quick-code.desktop" > "$dest"
    chmod 644 "$dest"
done

# Start (or restart, to pick up the new version) in the current graphical session.
if [[ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]]; then
    stop_running
    setsid -f "$bin" > /dev/null 2>&1 < /dev/null
    echo "Installed and started. It will also start automatically at login."
else
    echo "Installed. It will start at your next graphical login (or run: quick-code &)."
fi
