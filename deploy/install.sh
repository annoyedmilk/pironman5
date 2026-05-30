#!/usr/bin/env bash
#
# Installer for the Pironman 5 service on Raspberry Pi OS (Bookworm, 64-bit).
# Run from a checkout of this repository: sudo ./deploy/install.sh
#
# It creates a dedicated service user, installs the package into a virtualenv
# under /opt/pironman5, drops the device-tree overlay, udev rules, sudoers and
# systemd unit, then enables the service. Everything is local and auditable;
# nothing is piped from the network into a shell.

set -euo pipefail

PREFIX=/opt/pironman5
CONFIG_DIR=/etc/pironman5
SERVICE_USER=pironman5
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root: sudo ./deploy/install.sh" >&2
    exit 1
fi

echo "==> Enabling SPI and I2C overlays in /boot/firmware/config.txt"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt
for line in "dtparam=spi=on" "dtparam=i2c_arm=on" "dtoverlay=sunfounder-pironman5"; do
    grep -qxF "$line" "$CONFIG_TXT" || echo "$line" >> "$CONFIG_TXT"
done

echo "==> Installing device-tree overlay"
install -m 0644 "$REPO_DIR/deploy/overlays/sunfounder-pironman5.dtbo" \
    /boot/firmware/overlays/ 2>/dev/null || \
install -m 0644 "$REPO_DIR/deploy/overlays/sunfounder-pironman5.dtbo" /boot/overlays/

echo "==> Creating service user '$SERVICE_USER'"
id -u "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
for grp in spi i2c gpio input video; do
    getent group "$grp" >/dev/null && usermod -aG "$grp" "$SERVICE_USER" || true
done

echo "==> Installing system packages"
apt-get update -qq
# swig and liblgpio-dev are needed to build the lgpio extension that the GPIO
# backend depends on; i2c-tools is handy for verifying the OLED bus.
apt-get install -y python3-dev liblgpio-dev swig i2c-tools

echo "==> Ensuring uv is available"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "==> Creating environment at $PREFIX/venv with uv"
mkdir -p "$PREFIX"
"$UV" venv "$PREFIX/venv"
"$UV" pip install --python "$PREFIX/venv/bin/python" "$REPO_DIR[hardware]"

echo "==> Installing config, udev rules, sudoers and service"
mkdir -p "$CONFIG_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"
install -m 0644 "$REPO_DIR/deploy/99-com.rules" /etc/udev/rules.d/99-com.rules
install -m 0440 "$REPO_DIR/deploy/pironman5.sudoers" /etc/sudoers.d/pironman5
install -m 0644 "$REPO_DIR/deploy/pironman5.service" /etc/systemd/system/pironman5.service

udevadm control --reload-rules && udevadm trigger || true
systemctl daemon-reload
systemctl enable --now pironman5

echo
echo "Done. The dashboard is on http://<this-pi>:34001"
echo "A reboot is recommended so the SPI/I2C overlays take effect."
