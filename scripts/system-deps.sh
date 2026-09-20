#!/bin/sh
# Sourced by install.sh. Install only missing prerequisites using the native
# package manager. No passwords are stored; sudo prompts on the user's terminal.

install_system_dependencies() {
    napback_missing=""
    command -v python3 >/dev/null 2>&1 || napback_missing="$napback_missing python3"
    command -v rsync >/dev/null 2>&1 || napback_missing="$napback_missing rsync"
    command -v ssh >/dev/null 2>&1 || napback_missing="$napback_missing ssh"
    command -v systemctl >/dev/null 2>&1 || napback_missing="$napback_missing systemctl"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || napback_missing="$napback_missing venv"
        if [ "$napback_with_tray" = yes ]; then
            python3 -c '
import ctypes
for library in (
    "libEGL.so.1", "libGL.so.1", "libfontconfig.so.1", "libglib-2.0.so.0",
    "libdbus-1.so.3", "libX11-xcb.so.1", "libSM.so.6", "libICE.so.6",
    "libxkbcommon-x11.so.0", "libxcb-cursor.so.0", "libxcb-icccm.so.4",
    "libxcb-image.so.0", "libxcb-keysyms.so.1", "libxcb-render-util.so.0",
    "libxcb-randr.so.0", "libxcb-shape.so.0", "libxcb-sync.so.1",
):
    ctypes.CDLL(library)
' >/dev/null 2>&1 || napback_missing="$napback_missing qt-runtime"
        fi
    else
        napback_missing="$napback_missing venv"
        [ "$napback_with_tray" = no ] || napback_missing="$napback_missing qt-runtime"
    fi
    [ -n "$napback_missing" ] || return 0
    printf 'Missing prerequisites:%s\n' "$napback_missing"
    if command -v pacman >/dev/null 2>&1; then
        napback_manager=pacman
    elif command -v apt-get >/dev/null 2>&1; then
        napback_manager=apt
    elif command -v dnf >/dev/null 2>&1; then
        napback_manager=dnf
    else
        printf 'No supported package manager (pacman, apt-get, dnf). Install prerequisites and rerun.\n' >&2
        return 1
    fi
    napback_packages=""
    for napback_requirement in $napback_missing; do
        case "$napback_manager:$napback_requirement" in
            pacman:python3|pacman:venv) napback_package=python ;;
            apt:python3) napback_package=python3 ;;
            apt:venv) napback_package=python3-venv ;;
            dnf:python3) napback_package=python3 ;;
            dnf:venv) napback_package=python3-pip ;;
            *:rsync) napback_package=rsync ;;
            pacman:ssh) napback_package=openssh ;;
            apt:ssh) napback_package=openssh-client ;;
            dnf:ssh) napback_package=openssh-clients ;;
            *:systemctl) napback_package=systemd ;;
            # The distribution Qt GUI package brings the complete platform
            # dependency set; PyQt's bundled Qt alone does not provide these.
            pacman:qt-runtime) napback_package='qt6-base' ;;
            apt:qt-runtime) napback_package='libqt6gui6 libxcb-cursor0' ;;
            dnf:qt-runtime) napback_package='qt6-qtbase-gui xcb-util-cursor' ;;
        esac
        for napback_package_name in $napback_package; do
            case " $napback_packages " in
                *" $napback_package_name "*) ;;
                *) napback_packages="$napback_packages $napback_package_name" ;;
            esac
        done
    done
    if [ "$(id -u)" -eq 0 ]; then
        napback_sudo=""
    elif command -v sudo >/dev/null 2>&1; then
        napback_sudo=sudo
    else
        printf 'sudo is missing. Ask an administrator to install:%s\n' "$napback_packages" >&2
        return 1
    fi
    printf 'Installing with %s:%s\n' "$napback_manager" "$napback_packages"
    case "$napback_manager" in
        pacman) $napback_sudo pacman -S --needed --noconfirm $napback_packages ;;
        apt) $napback_sudo apt-get update && $napback_sudo apt-get install -y $napback_packages ;;
        dnf) $napback_sudo dnf install -y $napback_packages ;;
    esac
}
