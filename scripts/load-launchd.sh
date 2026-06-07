#!/usr/bin/env bash
# Install + load (or unload / status) the Signal Monitor launchd job.
#
# Usage:
#   ./scripts/load-launchd.sh          # install + load (Saturday 7am local)
#   ./scripts/load-launchd.sh unload   # unload + remove from LaunchAgents
#   ./scripts/load-launchd.sh status   # show launchctl list entry + next fire
#
# macOS 15 gotcha (see AEH-OS feedback_launchd_fda_required memory):
#   /bin/bash needs Full Disk Access to read scripts in ~/Documents/.
#   If the job fires silently with no output, grant FDA to /bin/bash via
#   System Settings → Privacy & Security → Full Disk Access.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd)"

LABEL="com.aehirota.signal-monitor"
PLIST_SRC="$PROJECT_DIR/launchd/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

ensure_executable() {
    chmod +x "$PROJECT_DIR/scripts/run-with-env.sh"
}

install_plist() {
    ensure_executable
    # Substitute __PROJECT_DIR__ → absolute path. sed -i needs a backup arg on macOS.
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
    echo "[load-launchd] installed plist at $PLIST_DST"
}

cmd_install() {
    install_plist
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    echo "[load-launchd] loaded $LABEL"
    launchctl list | grep -F "$LABEL" || true
}

cmd_unload() {
    if [[ -f "$PLIST_DST" ]]; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        echo "[load-launchd] unloaded $LABEL"
    fi
    rm -f "$PLIST_DST"
    echo "[load-launchd] removed $PLIST_DST"
}

cmd_status() {
    echo "─ launchctl list entry ─"
    launchctl list | grep -F "$LABEL" || echo "  (not loaded)"
    echo
    echo "─ plist on disk ─"
    if [[ -f "$PLIST_DST" ]]; then
        ls -la "$PLIST_DST"
    else
        echo "  (not installed at $PLIST_DST)"
    fi
    echo
    echo "─ recent stderr (tail) ─"
    if [[ -f "$PROJECT_DIR/.tmp/launchd-stderr.log" ]]; then
        tail -n 20 "$PROJECT_DIR/.tmp/launchd-stderr.log"
    else
        echo "  (no log yet)"
    fi
}

case "${1:-install}" in
    install|load)
        cmd_install
        ;;
    unload|remove)
        cmd_unload
        ;;
    status)
        cmd_status
        ;;
    *)
        echo "usage: $0 {install|unload|status}" >&2
        exit 2
        ;;
esac
