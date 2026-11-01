# EasyIPTVBox remote access (WireGuard + DuckDNS)

    sudo apt install -y wireguard-tools nftables
    sudo install -m 0755 iptv-vpn /usr/local/sbin/iptv-vpn
    sudo install -m 0644 iptv-duckdns.service iptv-duckdns.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo iptv-vpn setup && sudo iptv-vpn enable

- WireGuard listens on UDP 51821 (forward it in the router to the box).
- VPN network 10.66.0.0/24, box = 10.66.0.1. Clients reach only the box
  (nftables drops anything forwarded from wg0; ip_forward stays off).
- Keys, DuckDNS token and devices are stored in /etc/iptv-vpn (root only).
- Everything else is managed in the web app: Settings > Remote access.

## TCP mode (when WireGuard / UDP is blocked, e.g. Iran)

Settings > Remote access > "TCP mode": VLESS + Reality (sing-box), which looks like
ordinary HTTPS. It is off by default; turning it on downloads sing-box once
(pinned version, SHA-256 checked) and starts `iptv-proxy.service` as the unprivileged
user `iptvproxy`. Default port **TCP 8443** (forward it in the router). Each device has
its own secret; removing a device cuts it off at once. Clients can reach only
`10.66.0.1:8080` (the box's web app, redirected to the loopback port 8081 which counts as
remote access, so settings stay locked). CLI: `iptv-vpn proxy-enable|proxy-disable|proxy-add|proxy-remove|proxy-status`.
Keys and secrets are in /etc/iptv-vpn/state.json and /etc/iptv-proxy/config.json (not world-readable).
- The box needs a fixed LAN address (set on its Ethernet connection).
