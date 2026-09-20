#!/bin/sh
set -eu
napback_with_tray=yes
case "${1:-}" in
    --cli-only) napback_with_tray=no ;;
    '') ;;
    *) printf 'Usage: ./install.sh [--cli-only]\n' >&2; exit 2 ;;
esac
if [ "$(id -u)" -eq 0 ]; then
    printf 'Run this installer as your normal user. It uses sudo only for missing system packages.\n' >&2
    exit 1
fi
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$project_dir/scripts/system-deps.sh"
install_system_dependencies
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required; update your distribution Python"'
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/napback"
bin_dir="$HOME/.local/bin"
stamp=$(date -u +%Y%m%dT%H%M%S)
mkdir -p "$install_dir" "$bin_dir"
if [ -e "$install_dir/venv" ]; then
    mv "$install_dir/venv" "$install_dir/venv.bak-$stamp"
    printf 'Backup: %s\n' "$install_dir/venv.bak-$stamp"
fi
# On installation failure, keep the failed environment for diagnosis and restore
# the previous one at its original path (venv scripts contain absolute paths).
napback_install_failed() {
    if [ -e "$install_dir/venv.bak-$stamp" ]; then
        [ ! -e "$install_dir/venv" ] || mv "$install_dir/venv" "$install_dir/venv.failed-$stamp"
        mv "$install_dir/venv.bak-$stamp" "$install_dir/venv"
        printf 'Previous installation restored.\n' >&2
    fi
}
trap 'napback_install_failed' EXIT
python3 -m venv "$install_dir/venv"
napback_requirement="$project_dir"
[ "$napback_with_tray" = no ] || napback_requirement="$project_dir[tray]"
"$install_dir/venv/bin/python" -m pip install "$napback_requirement"
"$install_dir/venv/bin/python" -m napback --version
if [ "$napback_with_tray" = yes ]; then
    QT_QPA_PLATFORM=offscreen "$install_dir/venv/bin/python" -c 'from PyQt6.QtWidgets import QApplication; app = QApplication([])'
fi
trap - EXIT
if [ -e "$bin_dir/napback" ] || [ -L "$bin_dir/napback" ]; then
    mv "$bin_dir/napback" "$bin_dir/napback.bak-$stamp"
    printf 'Backup: %s\n' "$bin_dir/napback.bak-$stamp"
fi
ln -s "$install_dir/venv/bin/napback" "$bin_dir/napback"
# Preserve an existing scheduled configuration, while updating ExecStart to use
# the new configurable check cadence. No new backup sources are selected here.
napback_config="${XDG_CONFIG_HOME:-$HOME/.config}/napback/config.json"
if [ -f "$napback_config" ] && systemctl --user is-enabled --quiet napback.timer 2>/dev/null; then
    "$bin_dir/napback" install-timer
fi
if [ "$napback_with_tray" = yes ]; then
    "$bin_dir/napback" install-tray
fi
printf 'Installed: %s\nRun: napback setup\n' "$bin_dir/napback"
