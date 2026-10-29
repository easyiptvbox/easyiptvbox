"""Remote access (VPN) support for the web app.

* Thin wrappers around the root helper /usr/local/sbin/iptv-vpn.
"""
import json
import os
import subprocess
import time

from iptv_config import CACHE_DIR

HELPER = "/usr/local/sbin/iptv-vpn"
VPN_PREFIX = "10.66.0."
SERVER_IP = "10.66.0.1"


def from_vpn(client_ip):
    return str(client_ip).startswith(VPN_PREFIX)


# ---------------------------------------------------------------------------
#  VPN helper wrappers
# ---------------------------------------------------------------------------
def _helper(*args, inp=None, timeout=60):
    r = subprocess.run(["sudo", "-n", HELPER] + list(args), input=inp,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "failed").strip().splitlines()[-1]
        raise RuntimeError(msg)
    return r.stdout


def status():
    try:
        st = json.loads(_helper("status", timeout=15))
    except Exception as e:
        return {"installed": os.path.exists(HELPER), "error": str(e)}
    st["installed"] = True
    st["lan_ip"] = _lan_ip()
    try:
        st["lan_mac"] = open("/sys/class/net/eth0/address").read().strip()
    except OSError:
        st["lan_mac"] = ""
    return st


def _lan_ip():
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "eth0"],
                           capture_output=True, text=True, timeout=4)
        return r.stdout.split()[3].split("/")[0]
    except Exception:
        return "192.168.0.168"


def enable(on):
    _helper("enable" if on else "disable")


def set_duckdns(domain, token):
    _helper("set-duckdns", domain, inp=token or "")


def add_device(name):
    """Returns the WireGuard client config text."""
    return _helper("add", name)


def remove_device(name):
    _helper("remove", name)


def qr_svg(text):
    import io
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def port_test():
    """Ask an outside service whether UDP port is open. WireGuard ignores
    unauthenticated packets, so UDP can't really be probed from outside;
    we check the pieces we can: DuckDNS resolves to our public IP and the
    service is listening."""
    import socket
    import urllib.request
    st = status()
    res = {"listening": bool(st.get("running")), "dns_ok": None,
           "public_ip": "", "dns_ip": ""}
    try:
        res["public_ip"] = urllib.request.urlopen(
            "https://api.ipify.org", timeout=8).read().decode().strip()
    except Exception:
        pass
    dom = (st.get("duckdns") or {}).get("domain")
    if dom:
        try:
            res["dns_ip"] = socket.gethostbyname(dom)
            res["dns_ok"] = (res["dns_ip"] == res["public_ip"])
        except Exception:
            res["dns_ok"] = False
    return res
