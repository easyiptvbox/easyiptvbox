"""Remote access (VPN) support for the web app.

* PIN gate: requests arriving over the VPN (10.66.0.0/24) must present a valid
  session cookie, obtained by entering the PIN. Requests from the home network
  are never asked for a PIN.
* Thin wrappers around the root helper /usr/local/sbin/iptv-vpn.
"""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time

from iptv_config import CACHE_DIR

HELPER = "/usr/local/sbin/iptv-vpn"
VPN_PREFIX = "10.66.0."
SERVER_IP = "10.66.0.1"
AUTH_PATH = os.path.join(CACHE_DIR, "remote_auth.json")
COOKIE = "eib_session"
SESSION_DAYS = 90
MAX_FAILS = 5
LOCK_SECONDS = 300

_fails = {}          # client ip -> (count, first_fail_time)


def from_vpn(client_ip):
    return str(client_ip).startswith(VPN_PREFIX)


# ---------------------------------------------------------------------------
#  PIN + sessions
# ---------------------------------------------------------------------------
def _load():
    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
              "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, AUTH_PATH)


def _hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt),
                               200000).hex()


def has_pin():
    return bool(_load().get("pin_hash"))


def set_pin(pin):
    pin = str(pin or "").strip()
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        raise ValueError("The PIN must be 4-8 digits")
    salt = secrets.token_hex(16)
    _save({"salt": salt, "pin_hash": _hash(pin, salt),
           "secret": secrets.token_hex(32)})   # new secret logs everyone out


def _sign(secret, expires):
    return hmac.new(bytes.fromhex(secret), str(expires).encode(),
                    "sha256").hexdigest()


def login(pin, client_ip):
    """Return a cookie value on success, or raise ValueError."""
    n, t0 = _fails.get(client_ip, (0, 0))
    if n >= MAX_FAILS and time.time() - t0 < LOCK_SECONDS:
        raise ValueError("Too many wrong PINs - wait 5 minutes")
    d = _load()
    if not d.get("pin_hash"):
        raise ValueError("No PIN has been set on the box yet")
    if not hmac.compare_digest(_hash(str(pin).strip(), d["salt"]),
                               d["pin_hash"]):
        if time.time() - t0 > LOCK_SECONDS:
            n, t0 = 0, time.time()
        _fails[client_ip] = (n + 1, t0)
        raise ValueError("Wrong PIN")
    _fails.pop(client_ip, None)
    expires = int(time.time()) + SESSION_DAYS * 86400
    return "%d.%s" % (expires, _sign(d["secret"], expires))


def session_ok(cookie_header):
    d = _load()
    if not d.get("secret"):
        return False
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and "." in v:
            exp, sig = v.split(".", 1)
            try:
                if int(exp) < time.time():
                    return False
            except ValueError:
                return False
            return hmac.compare_digest(sig, _sign(d["secret"], exp))
    return False


LOGIN_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EasyIPTVBox</title><style>
body{margin:0;background:#12141c;color:#e1e4eb;font-family:system-ui,-apple-system,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh}
form{background:#1e2130;padding:1.6rem;border-radius:14px;width:min(90vw,340px)}
h1{margin:0 0 1rem;font-size:1.3rem}
input{width:100%;box-sizing:border-box;font-size:1.6rem;padding:.6rem;border-radius:10px;
border:1px solid #3a3f50;background:#12141c;color:#e1e4eb;text-align:center;letter-spacing:.3em}
button{width:100%;margin-top:1rem;padding:.8rem;font-size:1.05rem;border:none;border-radius:10px;
background:#007acc;color:#fff;font-weight:600}
#e{color:#e88;min-height:1.2em;margin-top:.7rem;font-size:.9rem}
p{color:#8a90a0;font-size:.85rem}
</style></head><body><form id="f">
<h1>EasyIPTVBox</h1><p>You are connected from outside your home. Enter the PIN.</p>
<input id="p" type="password" inputmode="numeric" autocomplete="current-password" autofocus>
<button>Unlock</button><div id="e"></div></form>
<script>
document.getElementById('f').onsubmit=async ev=>{ev.preventDefault();
 const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({pin:document.getElementById('p').value})});
 if(r.ok) location.replace(location.pathname==='/login'?'/':location.href);
 else document.getElementById('e').textContent=(await r.json()).error||'Wrong PIN';};
</script></body></html>"""


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
    st["has_pin"] = has_pin()
    st["lan_ip"] = _lan_ip()
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
