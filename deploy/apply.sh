#!/bin/bash
# Apply the files from the checked-out repo to the running system.
# Idempotent; needs root. Used by bootstrap.sh and update.sh (no apt here).
# Never overwrites the user's config.yaml / remotes.json / cache.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${APP_USER:-iptv}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_DST="$APP_HOME/iptv-player"
FLAG=/run/iptv-app-changed
rm -f "$FLAG"

# --- app code (keep user data) ---
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DST"
changes="$(rsync -rlpt --checksum --itemize-changes --chown="$APP_USER:$APP_USER" \
    --exclude=config.yaml --exclude=config.yaml.bak --exclude=remotes.json \
    --exclude=cache/ --exclude=__pycache__/ --exclude='*.tmp' \
    "$REPO/app/" "$APP_DST/")"
[ -n "$changes" ] && touch "$FLAG"
if [ ! -e "$APP_DST/config.yaml" ]; then
    install -m 0644 -o "$APP_USER" -g "$APP_USER" "$REPO/app/config.yaml" "$APP_DST/config.yaml"
    touch "$FLAG"
fi

units_changed=0
inst() { # inst <mode> <src> <dst>  -> sets units_changed if different
    if ! cmp -s "$2" "$3" 2>/dev/null; then
        install -D -m "$1" "$2" "$3"; units_changed=1
    fi
}

# --- systemd units ---
for u in iptv.service iptv-ir.service iptv-update.service iptv-update.timer; do
    inst 0644 "$REPO/deploy/$u" "/etc/systemd/system/$u"
done
for u in iptv-duckdns.service iptv-duckdns.timer; do
    inst 0644 "$REPO/deploy/vpn/$u" "/etc/systemd/system/$u"
done
inst 0644 "$REPO/deploy/splash/plymouth-quit-retain.conf" \
    /etc/systemd/system/plymouth-quit.service.d/retain.conf
inst 0755 "$REPO/deploy/vpn/iptv-vpn" /usr/local/sbin/iptv-vpn

# --- IR keymap: only seed (the app rewrites it when remotes are learned) ---
[ -e /etc/rc_keymaps/argon.toml ] || install -D -m 0644 "$REPO/deploy/argon.toml" /etc/rc_keymaps/argon.toml

# --- passwordless sudo for the app user (the app needs it for nmcli etc.) ---
if ! grep -rqs "^$APP_USER .*NOPASSWD" /etc/sudoers.d; then
    echo "$APP_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/010_iptv-nopasswd
    chmod 0440 /etc/sudoers.d/010_iptv-nopasswd
fi

# --- splash theme ---
splash_changed=0
T=/usr/share/plymouth/themes/easyiptvbox
install -d "$T"
for f in "$REPO"/deploy/splash/easyiptvbox/*; do
    if ! cmp -s "$f" "$T/$(basename "$f")"; then
        install -m 0644 "$f" "$T/"; splash_changed=1
    fi
done
printf '[Daemon]\nTheme=easyiptvbox\nShowDelay=0\nDeviceTimeout=8\n' > /tmp/plymouthd.conf.$$
if ! cmp -s /tmp/plymouthd.conf.$$ /etc/plymouth/plymouthd.conf 2>/dev/null; then
    install -D -m 0644 /tmp/plymouthd.conf.$$ /etc/plymouth/plymouthd.conf; splash_changed=1
fi
rm -f /tmp/plymouthd.conf.$$
if [ "$(/usr/sbin/plymouth-set-default-theme 2>/dev/null)" != easyiptvbox ]; then
    /usr/sbin/plymouth-set-default-theme easyiptvbox || true; splash_changed=1
fi
[ "$splash_changed" = 1 ] && update-initramfs -u || true

# --- services ---
if [ "$units_changed" = 1 ]; then systemctl daemon-reload; fi
systemctl enable iptv.service iptv-ir.service iptv-duckdns.timer >/dev/null 2>&1 || true
systemctl start iptv-duckdns.timer >/dev/null 2>&1 || true
# weekly auto-update is on unless the user switched it off in the web app
if [ ! -e /var/lib/iptv-update/auto-off ]; then
    systemctl enable iptv-update.timer >/dev/null 2>&1 || true
    systemctl start iptv-update.timer >/dev/null 2>&1 || true
fi
[ "$units_changed" = 1 ] && touch "$FLAG"
exit 0
