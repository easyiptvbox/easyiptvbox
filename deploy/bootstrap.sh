#!/bin/bash
# EasyIPTVBox bootstrap for a fresh Raspberry Pi 5 (Raspberry Pi OS Lite, Trixie).
#
#   curl -fsSL https://raw.githubusercontent.com/easyiptvbox/easyiptvbox/main/deploy/bootstrap.sh | sudo bash
#
# Options (env): APP_USER=iptv  IPTV_HOSTNAME=iptv1  WIFI_COUNTRY=NO  REBOOT=1
# Safe to re-run. Provider login / Wi-Fi / VPN are configured later in the web app.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }

APP_USER="${APP_USER:-iptv}"
SRC=/opt/easyiptvbox
URL="${IPTV_REPO_URL:-https://github.com/easyiptvbox/easyiptvbox.git}"
export DEBIAN_FRONTEND=noninteractive

id "$APP_USER" >/dev/null 2>&1 || { echo "user '$APP_USER' does not exist (create it in Raspberry Pi Imager)"; exit 1; }

echo "== packages"
apt-get update
apt-get install -y git rsync mpv python3-pygame python3-evdev python3-yaml \
    python3-requests python3-pil python3-qrcode ir-keytable ffmpeg yt-dlp \
    alsa-utils v4l-utils avahi-daemon network-manager plymouth plymouth-themes \
    plymouth-label wireguard-tools nftables fonts-dejavu

echo "== user groups"
for g in video render input audio tty netdev gpio i2c spi; do
    getent group "$g" >/dev/null && usermod -aG "$g" "$APP_USER"
done

if [ -n "${IPTV_HOSTNAME:-}" ]; then hostnamectl set-hostname "$IPTV_HOSTNAME" || true; fi
if [ -n "${WIFI_COUNTRY:-}" ]; then raspi-config nonint do_wifi_country "$WIFI_COUNTRY" || true; fi

echo "== repository"
if [ -d "$SRC/.git" ]; then
    git -C "$SRC" fetch origin main && git -C "$SRC" checkout -q -B main origin/main
else
    git clone --branch main "$URL" "$SRC"
fi

echo "== boot configuration"
CFG=/boot/firmware/config.txt
CMD=/boot/firmware/cmdline.txt
for line in "dtoverlay=gpio-ir,gpio_pin=23" "disable_splash=1"; do
    grep -qxF "$line" "$CFG" || printf '%s\n' "$line" >> "$CFG"
done
for opt in quiet splash plymouth.ignore-serial-consoles logo.nologo \
           vt.global_cursor_default=0 loglevel=3; do
    grep -qw -- "$opt" "$CMD" || sed -i "1s/\$/ $opt/" "$CMD"
done

echo "== install files and services"
"$SRC/deploy/apply.sh"

echo "== Argon ONE case software (best effort)"
if [ ! -e /etc/argon/argononed.py ]; then
    curl -fsSL --max-time 60 https://download.argon40.com/argonone.sh | bash || echo "Argon install skipped/failed"
fi

echo
echo "Done. Reboot to start the player:  sudo reboot"
echo "Web remote/settings: http://$(hostname).local:8080"
[ "${REBOOT:-0}" = 1 ] && reboot
exit 0
