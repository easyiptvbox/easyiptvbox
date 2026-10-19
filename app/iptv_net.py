"""Networking (Wi-Fi via nmcli) and IR keymap helpers.

All NetworkManager operations go through `sudo nmcli` because the player runs
as the unprivileged `iptv` user (which has passwordless sudo). These helpers are
thin, synchronous wrappers; callers should run the blocking ones (scan/connect)
in a background thread and poll for the result.
"""
import os
import re
import subprocess


# ---------------------------------------------------------------------------
#  Network presence
# ---------------------------------------------------------------------------
def has_network(timeout=4):
    """True if the box has any usable network (Wi-Fi or Ethernet).

    We consider it connected if there is a default route to the internet.
    """
    try:
        r = subprocess.run(["ip", "route", "get", "1.1.1.1"],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and "dev" in r.stdout:
            return True
    except Exception:
        pass
    # fall back to NetworkManager's own view
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "STATE", "general"],
                           capture_output=True, text=True, timeout=timeout)
        state = (r.stdout or "").strip().lower()
        if "connected" in state and "disconnected" not in state:
            return True
    except Exception:
        pass
    return False


def wifi_radio_on():
    try:
        r = subprocess.run(["nmcli", "radio", "wifi"],
                           capture_output=True, text=True, timeout=4)
        return "enabled" in (r.stdout or "").lower()
    except Exception:
        return False


def enable_wifi():
    try:
        subprocess.run(["sudo", "nmcli", "radio", "wifi", "on"],
                       capture_output=True, text=True, timeout=6)
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Wi-Fi scan / connect
# ---------------------------------------------------------------------------
def scan_wifi(rescan=True, timeout=25):
    """Return a list of dicts: {ssid, signal(int), secured(bool), in_use(bool)}.

    Deduplicated by SSID (keeping the strongest), sorted by signal desc.
    Hidden networks (empty SSID) are skipped.
    """
    enable_wifi()
    cmd = ["sudo", "nmcli", "-t", "-e", "no",
           "-f", "IN-USE,SIGNAL,SECURITY,SSID", "device", "wifi", "list"]
    if rescan:
        cmd += ["--rescan", "yes"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return []
    nets = {}
    for line in (r.stdout or "").splitlines():
        # format: IN-USE:SIGNAL:SECURITY:SSID  (SSID may contain ':', so split 3)
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        in_use, signal, security, ssid = parts
        ssid = ssid.strip()
        if not ssid:
            continue
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        secured = bool(security.strip()) and security.strip() != "--"
        cur = nets.get(ssid)
        entry = {
            "ssid": ssid,
            "signal": sig,
            "secured": secured,
            "in_use": (in_use.strip() == "*") or (cur and cur["in_use"]),
        }
        if not cur or sig > cur["signal"]:
            entry["in_use"] = (in_use.strip() == "*") or (cur or {}).get("in_use", False)
            nets[ssid] = entry
        elif in_use.strip() == "*":
            nets[ssid]["in_use"] = True
    out = list(nets.values())
    out.sort(key=lambda n: (-n["signal"], n["ssid"].lower()))
    return out


def saved_ssids():
    """Return the set of SSIDs we already have a saved Wi-Fi profile for."""
    out = set()
    try:
        r = subprocess.run(
            ["sudo", "nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            capture_output=True, text=True, timeout=6)
        for line in (r.stdout or "").splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and "wireless" in parts[-1]:
                out.add(":".join(parts[:-1]))
    except Exception:
        pass
    return out


def connect_saved(ssid, timeout=45):
    """Bring up an existing saved profile by name. Returns (ok, message)."""
    try:
        r = subprocess.run(["sudo", "nmcli", "connection", "up", "id", ssid],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return True, "Connected"
        return False, _nm_error(r.stderr or r.stdout)
    except subprocess.TimeoutExpired:
        return False, "Timed out"
    except Exception as e:
        return False, str(e)


def connect_wifi(ssid, password=None, timeout=45):
    """Connect to a Wi-Fi network. Returns (ok, message).

    If password is None/empty, attempts an open-network connect.
    """
    cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and "successfully" in (r.stdout or "").lower():
            return True, "Connected"
        if r.returncode == 0:
            # some nmcli versions don't print "successfully"; verify by route
            if has_network():
                return True, "Connected"
            return False, _nm_error(r.stderr or r.stdout or "Could not connect")
        return False, _nm_error(r.stderr or r.stdout)
    except subprocess.TimeoutExpired:
        return False, "Timed out - check the password"
    except Exception as e:
        return False, str(e)


def _nm_error(text):
    text = (text or "").strip()
    low = text.lower()
    if "secrets were required" in low or "no secrets" in low or \
            "802-11-wireless-security" in low or "invalid password" in low:
        return "Wrong password"
    if "timeout" in low:
        return "Timed out"
    # take the last meaningful line
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line:
            return line[:80]
    return "Could not connect"


# ---------------------------------------------------------------------------
#  IR keymap (multiple remotes = multiple [[protocols]] blocks)
# ---------------------------------------------------------------------------
KEYMAP_PATH = os.environ.get("IPTV_KEYMAP", "/etc/rc_keymaps/argon.toml")

# scancode(hex str) -> KEY_* : our action buttons in learning order
LEARN_BUTTONS = [
    ("up", "KEY_UP"),
    ("down", "KEY_DOWN"),
    ("left", "KEY_LEFT"),
    ("right", "KEY_RIGHT"),
    ("ok", "KEY_OK"),
    ("back", "KEY_BACK"),
    ("home", "KEY_HOME"),
    ("menu", "KEY_MENU"),
    ("vol_up", "KEY_VOLUMEUP"),
    ("vol_down", "KEY_VOLUMEDOWN"),
    ("power", "KEY_POWER"),
]


def read_keymap(path=KEYMAP_PATH):
    """Parse the keymap TOML into a list of remote blocks:
        [{name, protocol, variant, scancodes:{scancode_str:keycode}}]
    A minimal hand-rolled parser (tomllib may be unavailable on 3.9/3.10 and
    we only need this simple structure)."""
    blocks = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return blocks
    cur = None
    in_scancodes = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[protocols]]":
            cur = {"name": "", "protocol": "nec", "variant": "",
                   "scancodes": {}}
            blocks.append(cur)
            in_scancodes = False
            continue
        if line == "[protocols.scancodes]":
            in_scancodes = True
            continue
        if cur is None:
            continue
        m = re.match(r'^(\S+)\s*=\s*"?([^"]*)"?\s*$', line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if not in_scancodes and key in ("name", "protocol", "variant"):
            cur[key] = val
        elif in_scancodes:
            cur["scancodes"][key.lower()] = val
    return blocks


def write_keymap(blocks, path=KEYMAP_PATH):
    """Write the keymap TOML with one [[protocols]] block per remote, using
    sudo tee so we can write to /etc. Returns True on success."""
    out = ["# IPTV Player IR keymap - managed by the app (learned remotes).\n"]
    for b in blocks:
        out.append("[[protocols]]")
        out.append('name = "%s"' % b.get("name", "Remote"))
        out.append('protocol = "%s"' % b.get("protocol", "nec"))
        if b.get("variant"):
            out.append('variant = "%s"' % b["variant"])
        out.append("[protocols.scancodes]")
        for scan, key in b.get("scancodes", {}).items():
            out.append('%s = "%s"' % (scan, key))
        out.append("")
    text = "\n".join(out)
    try:
        p = subprocess.run(["sudo", "tee", path], input=text,
                           capture_output=True, text=True, timeout=8)
        return p.returncode == 0
    except Exception:
        return False


def protocols_in(blocks):
    """Set of protocol names used across all remote blocks."""
    return sorted({(b.get("protocol") or "nec") for b in blocks})


def load_keymap_live(path=KEYMAP_PATH):
    """Clear + (re)load the keymap into the kernel and enable its protocols.
    Returns True on success."""
    ok = True
    if not any(b.get("scancodes") for b in read_keymap(path)):
        # nothing to load: just clear the kernel table, keep NEC decoding on
        try:
            subprocess.run(["sudo", "ir-keytable", "-c", "-p", "nec"],
                           capture_output=True, text=True, timeout=8)
        except Exception:
            return False
        return True
    try:
        r = subprocess.run(["sudo", "ir-keytable", "-c", "-w", path],
                           capture_output=True, text=True, timeout=8)
        ok = ok and (r.returncode == 0)
    except Exception:
        ok = False
    # enable every protocol used by the loaded remotes
    protos = protocols_in(read_keymap(path)) or ["nec"]
    try:
        cmd = ["sudo", "ir-keytable"]
        for p in protos:
            cmd += ["-p", p]
        subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except Exception:
        ok = False
    return ok


# ---------------------------------------------------------------------------
#  Pick the fastest connection when several are up (e.g. Ethernet + Wi-Fi)
# ---------------------------------------------------------------------------
def connected_interfaces():
    """Return the network devices (ethernet / wifi) that are connected."""
    out = []
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
                           capture_output=True, text=True, timeout=5)
        for line in (r.stdout or "").splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] in ("ethernet", "wifi") \
                    and parts[2] == "connected":
                out.append(parts[0])
    except Exception:
        pass
    return sorted(out)


# Neutral public test files. We deliberately do NOT measure against the IPTV
# provider: it allows a single connection and keeps counting an aborted
# download for minutes, which would lock the user out of watching.
SPEEDTEST_URLS = [
    "https://speed.cloudflare.com/__down?bytes=100000000",
    "https://proof.ovh.net/files/100Mb.dat",
]


def measure_speed(dev, url=None, seconds=6):
    """Download for up to `seconds` over device `dev` and return Mbit/s
    (0.0 on failure). Tries each public test server until one works."""
    urls = [url] if url else SPEEDTEST_URLS
    for u in urls:
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "-o", "/dev/null", "--interface", dev,
                 "--connect-timeout", "5", "--max-time", str(seconds),
                 "-A", "Mozilla/5.0", "-w",
                 "%{http_code} %{size_download} %{time_total}", u],
                capture_output=True, text=True, timeout=seconds + 10)
            code, size, total = (r.stdout or "0 0 0").split()[:3]
            size, total = float(size), float(total)
            if code.startswith("2") and size >= 100000 and total > 0:
                return size * 8 / total / 1e6
        except Exception:
            pass
    return 0.0


def prefer_interface(best, devices):
    """Make `best` the default route by giving it the lowest route metric.
    Applied live with `nmcli device modify` (not saved; redone each boot)."""
    ok = True
    for dev in devices:
        metric = "50" if dev == best else "700"
        try:
            r = subprocess.run(
                ["sudo", "nmcli", "device", "modify", dev,
                 "ipv4.route-metric", metric, "ipv6.route-metric", metric],
                capture_output=True, text=True, timeout=15)
            ok = ok and r.returncode == 0
        except Exception:
            ok = False
    return ok


def default_interface():
    try:
        r = subprocess.run(["ip", "route", "get", "1.1.1.1"],
                           capture_output=True, text=True, timeout=4)
        toks = r.stdout.split()
        if "dev" in toks:
            return toks[toks.index("dev") + 1]
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
#  Saved Wi-Fi networks (NetworkManager keeps every network ever joined)
# ---------------------------------------------------------------------------
def saved_wifi_profiles():
    """Return [(profile_name, ssid)] for every saved Wi-Fi network."""
    out = []
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                           capture_output=True, text=True, timeout=6)
        for line in (r.stdout or "").splitlines():
            name, _, typ = line.rpartition(":")
            if "wireless" not in typ or not name:
                continue
            name = name.replace("\\:", ":")
            s = subprocess.run(["nmcli", "-g", "802-11-wireless.ssid",
                                "connection", "show", name],
                               capture_output=True, text=True, timeout=6)
            out.append((name, (s.stdout or "").strip() or name))
    except Exception:
        pass
    return out


def visible_ssids(rescan=True):
    return {n["ssid"] for n in scan_wifi(rescan=rescan)}


def current_wifi(dev="wlan0"):
    """Name of the active connection on the Wi-Fi device, or ''."""
    try:
        r = subprocess.run(["nmcli", "-g", "GENERAL.CONNECTION", "device",
                            "show", dev], capture_output=True, text=True,
                           timeout=5)
        name = (r.stdout or "").strip()
        return "" if name in ("", "--") else name
    except Exception:
        return ""


def activate_wifi(name, timeout=40):
    """Bring up a saved Wi-Fi profile and wait until it has an address.
    Returns True on success."""
    import time as _t
    ok = False
    for attempt in range(2):
        try:
            r = subprocess.run(["sudo", "nmcli", "--wait", str(timeout),
                                "connection", "up", "id", name],
                               capture_output=True, text=True,
                               timeout=timeout + 10)
            if r.returncode == 0:
                ok = True
                break
        except Exception:
            pass
        _t.sleep(5)   # radio may still be settling after the last switch
    if not ok:
        return False
    for _ in range(15):
        if current_wifi() == name and default_interface() != "" and \
                _iface_has_ipv4("wlan0"):
            return True
        _t.sleep(1)
    return False


def _iface_has_ipv4(dev):
    try:
        r = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", dev],
                           capture_output=True, text=True, timeout=4)
        return "inet " in (r.stdout or "")
    except Exception:
        return False


def set_wifi_priority(best, names):
    """Make the best Wi-Fi the one NetworkManager prefers after a reboot."""
    for n in names:
        try:
            subprocess.run(["sudo", "nmcli", "connection", "modify", n,
                            "connection.autoconnect", "yes",
                            "connection.autoconnect-priority",
                            "10" if n == best else "0"],
                           capture_output=True, text=True, timeout=10)
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  Stored speed-test results
# ---------------------------------------------------------------------------
import json as _json

RESULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cache", "net_results.json")


def load_results():
    """{"tested": epoch, "wifi": {profile: mbit}, "eth0": mbit|None}"""
    try:
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return {"tested": 0, "wifi": {}, "eth0": None}


def save_results(res):
    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    tmp = RESULTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(res, f, indent=1)
    os.replace(tmp, RESULTS_PATH)


def ranked_wifi(visible=None):
    """Saved Wi-Fi profiles, best first: tested ones by last speed, then
    untested. Returns [{name, ssid, reachable, mbit, current}]."""
    res = load_results().get("wifi", {})
    cur = current_wifi()
    if visible is None:
        visible = visible_ssids(rescan=False)
    out = []
    for name, ssid in saved_wifi_profiles():
        out.append({"name": name, "ssid": ssid,
                    "reachable": ssid in visible or name == cur,
                    "mbit": res.get(name), "current": name == cur})
    out.sort(key=lambda n: (n["mbit"] is None, -(n["mbit"] or 0),
                            n["name"].lower()))
    return out
