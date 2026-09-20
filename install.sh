#!/bin/sh
set -eu
for program in python3 rsync ssh systemctl; do
    command -v "$program" >/dev/null 2>&1 || { printf 'Missing dependency: %s\n' "$program" >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/napback"
bin_dir="$HOME/.local/bin"
stamp=$(date -u +%Y%m%dT%H%M%S)
mkdir -p "$install_dir" "$bin_dir"
if [ -e "$install_dir/venv" ]; then
    mv "$install_dir/venv" "$install_dir/venv.bak-$stamp"
    printf 'Backup: %s\n' "$install_dir/venv.bak-$stamp"
fi
python3 -m venv "$install_dir/venv"
"$install_dir/venv/bin/python" -m pip install "$project_dir"
if [ -e "$bin_dir/napback" ] || [ -L "$bin_dir/napback" ]; then
    mv "$bin_dir/napback" "$bin_dir/napback.bak-$stamp"
    printf 'Backup: %s\n' "$bin_dir/napback.bak-$stamp"
fi
ln -s "$install_dir/venv/bin/napback" "$bin_dir/napback"
printf 'Installed: %s\nRun: napback setup\n' "$bin_dir/napback"
