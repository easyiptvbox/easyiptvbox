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
- The box needs a fixed LAN address (set on its Ethernet connection).
