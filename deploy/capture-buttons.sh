#!/usr/bin/env bash
# Capture the scancode of any remote button so you can add it to the keymap.
# Run:  ./capture-buttons.sh   then press buttons on the Argon remote.
# Press Ctrl+C to stop. Add new lines to /etc/rc_keymaps/argon.toml like:
#     0xNN = "KEY_1"
# then:  sudo systemctl restart iptv-ir
echo "Point the Argon remote at the Pi and press buttons..."
echo "(Ctrl+C to stop)"
sudo ir-keytable -s rc0 -t
