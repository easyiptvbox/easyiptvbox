"""Small built-in web server: virtual remote + configuration editor.

Runs inside the player process (stdlib only, no extra dependencies).
Remote-button presses are pushed onto the SAME action queue that the IR
remote uses, so the web remote and a real IR remote behave identically.

Only reachable from the local network (bind on all interfaces, no auth by
design - the user asked for no login since only local devices connect).
"""
import os
import json
import time
import threading
import http.server
import socketserver

import iptv_input as ir

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("IPTV_CONFIG", os.path.join(APP_DIR, "config.yaml"))

# Map web button names -> action constants understood by the app
BUTTON_ACTIONS = {
    "power": ir.POWER,
    "up": ir.UP,
    "down": ir.DOWN,
    "left": ir.LEFT,
    "right": ir.RIGHT,
    "ok": ir.OK,
    "select": ir.OK,
    "back": ir.BACK,
    "home": ir.HOME,
    "menu": ir.MENU,
    "vol_up": ir.VOL_UP,
    "vol_down": ir.VOL_DOWN,
    "mute": "mute",
}


class ControlServer:
    def __init__(self, action_queue, port=8080):
        self.q = action_queue
        self.port = port
        self.httpd = None
        # a snapshot the UI can show (set by the app)
        self.status = {"screen": "", "playlist": "", "channel": ""}

    def start(self):
        handler = self._make_handler()
        # allow_reuse_address so a quick restart can rebind immediately
        # instead of failing with "Address already in use".
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        last_err = None
        for _ in range(20):
            try:
                self.httpd = socketserver.ThreadingTCPServer(("", self.port), handler)
                break
            except OSError as e:
                last_err = e
                time.sleep(0.5)
        if self.httpd is None:
            raise last_err
        self.httpd.daemon_threads = True
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()

    def set_status(self, screen, playlist="", channel="", selected="",
                   extra=None):
        st = {"screen": screen, "playlist": playlist,
              "channel": channel, "selected": selected}
        if extra:
            st.update(extra)
        self.status = st

    def _make_handler(server):
        outer = server

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass  # keep the journal quiet

            def _send(self, code, body, ctype="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                elif isinstance(body, str):
                    body = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            # ---- GET ----
            def do_HEAD(self):
                path = self.path.split("?")[0]
                if path.startswith("/stream/movie/"):
                    import iptv_stream
                    iptv_stream.log("HEAD %s ua=%s" % (
                        path, (self.headers.get("User-Agent") or "")[:80]))
                    m = iptv_stream.find_movie(path.rsplit("/", 1)[-1].split(".")[0])
                    if m:
                        iptv_stream.serve(self, m, head_only=True,
                                          download="dl=1" in self.path)
                        return
                self.send_response(404)
                self.end_headers()

            def do_GET(self):
                path = self.path.split("?")[0]
                if path.startswith("/stream/movie/"):
                    import iptv_stream
                    iptv_stream.log("GET %s range=%s ua=%s" % (
                        path, self.headers.get("Range"),
                        (self.headers.get("User-Agent") or "")[:80]))
                    m = iptv_stream.find_movie(path.rsplit("/", 1)[-1].split(".")[0])
                    if m:
                        iptv_stream.serve(self, m,
                                          download="dl=1" in self.path)
                    else:
                        self._send(404, {"error": "not an added movie"})
                    return
                if path.startswith("/player/"):
                    import iptv_stream
                    m = iptv_stream.find_movie(path.rsplit("/", 1)[-1])
                    if not m:
                        self._send(404, {"error": "not an added movie"})
                        return
                    import html as _h
                    page = (PLAYER_PAGE
                            .replace("__NAME__", _h.escape(m["name"]))
                            .replace("__ID__", m["id"])
                            .replace("__EXT__", m["ext"])
                            .replace("__SIZE__", iptv_stream.size_text(m["id"]))
                            .replace("__SAFARI__", "1" if m["ext"] in ("mp4", "m4v", "mov") else "0"))
                    self._send(200, page, "text/html; charset=utf-8")
                    return
                if path == "/api/stream/movies":
                    import iptv_stream
                    self._send(200, {"movies": [
                        {"id": m["id"], "name": m["name"], "ext": m["ext"]}
                        for m in iptv_stream.added_movies()],
                        "busy": iptv_stream.busy()})
                    return
                if path in ("/", "/index.html"):
                    self._send(200, PAGE, "text/html; charset=utf-8")
                elif path == "/api/status":
                    self._send(200, outer.status)
                elif path == "/api/config":
                    try:
                        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                            self._send(200, {"text": f.read()})
                    except Exception as e:
                        self._send(500, {"error": str(e)})
                elif path == "/api/config-data":
                    try:
                        import yaml
                        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f) or {}
                        self._send(200, data)
                    except Exception as e:
                        self._send(500, {"error": str(e)})
                elif path == "/api/provider/search":
                    self._handle_provider_search()
                elif path == "/api/provider/categories":
                    self._handle_provider_categories()
                elif path == "/api/provider/movies":
                    self._handle_provider_movies()
                elif path == "/api/provider/series":
                    self._handle_provider_series()
                elif path == "/api/remotes":
                    self._handle_list_remotes()
                elif path == "/api/saved-searches":
                    import iptv_watch
                    self._send(200, {"searches": [
                        {"kind": x["kind"], "query": x["query"],
                         "created": x.get("created", 0)}
                        for x in iptv_watch.load()]})
                elif path == "/api/wifi/networks":
                    self._handle_wifi_networks()
                else:
                    self._send(404, {"error": "not found"})

            # ---- POST ----
            def do_POST(self):
                path = self.path.split("?")[0]
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b""
                if path == "/api/press":
                    try:
                        data = json.loads(raw or b"{}")
                        btn = str(data.get("button", "")).lower()
                    except Exception:
                        btn = ""
                    action = BUTTON_ACTIONS.get(btn)
                    if action:
                        outer.q.put(action)
                        self._send(200, {"ok": True, "action": action})
                    else:
                        self._send(400, {"error": "unknown button"})
                elif path == "/api/config":
                    self._handle_save_config(raw)
                elif path == "/api/config-data":
                    self._handle_save_config_data(raw)
                elif path == "/api/remotes/learn":
                    outer.q.put("__learn_remote__")
                    self._send(200, {"ok": True})
                elif path == "/api/remotes/delete":
                    self._handle_delete_remote(raw)
                elif path == "/api/remotes/rename":
                    self._handle_rename_remote(raw)
                elif path == "/api/remotes/reset":
                    self._handle_reset_remotes()
                elif path in ("/api/saved-searches/add",
                              "/api/saved-searches/delete"):
                    import iptv_watch
                    try:
                        d = json.loads(raw or b"{}")
                        if path.endswith("/add"):
                            iptv_watch.add(d.get("kind"), d.get("query"))
                        else:
                            iptv_watch.remove(d.get("kind"), d.get("query", ""))
                        self._send(200, {"ok": True})
                    except Exception as e:
                        self._send(400, {"error": str(e)})
                elif path == "/api/wifi/delete":
                    self._handle_wifi_delete(raw)
                elif path == "/api/wifi/retest":
                    outer.q.put("__net_retest__")
                    self._send(200, {"ok": True})
                elif path == "/api/wifi-setup":
                    outer.q.put("__wifi_setup__")
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "not found"})

            # ---- saved Wi-Fi networks + speed test results ----
            def _handle_wifi_networks(self):
                import iptv_net
                res = iptv_net.load_results()
                net = (outer.status or {}).get("network", {})
                self._send(200, {
                    "networks": iptv_net.ranked_wifi(),
                    "eth0": res.get("eth0"),
                    "eth0_connected": "eth0" in iptv_net.connected_interfaces(),
                    "active": iptv_net.default_interface(),
                    "tested": res.get("tested", 0),
                    "testing": bool(net.get("testing")),
                    "progress": net.get("progress", ""),
                    "error": net.get("last_error", ""),
                })

            def _handle_wifi_delete(self, raw):
                import iptv_net, subprocess
                try:
                    name = str(json.loads(raw or b"{}").get("name", ""))
                except Exception:
                    name = ""
                if name not in {n for n, _ in iptv_net.saved_wifi_profiles()}:
                    self._send(404, {"error": "not found"})
                    return
                r = subprocess.run(["sudo", "nmcli", "connection", "delete",
                                    "id", name], capture_output=True,
                                   text=True, timeout=20)
                res = iptv_net.load_results()
                res.get("wifi", {}).pop(name, None)
                iptv_net.save_results(res)
                self._send(200 if r.returncode == 0 else 500,
                           {"ok": r.returncode == 0})

            # ---- remotes management ----
            # Built-in remote(s) live in the kernel keymap (ir-keytable);
            # learned remotes live in remotes.json and are decoded by the app.
            def _handle_list_remotes(self):
                import iptv_net, iptv_input
                out = [{"name": b.get("name", "Remote"), "kind": "builtin",
                        "buttons": len(b.get("scancodes", {}))}
                       for b in iptv_net.read_keymap()]
                out += [{"name": r.get("name", "Remote"), "kind": "learned",
                         "buttons": len(r.get("codes", {}))}
                        for r in iptv_input.load_learned()]
                self._send(200, {"remotes": out})

            def _handle_delete_remote(self, raw):
                import iptv_net, iptv_input
                try:
                    name = str(json.loads(raw or b"{}").get("name", ""))
                except Exception:
                    name = ""
                learned = iptv_input.load_learned()
                kept = [r for r in learned if r.get("name") != name]
                if len(kept) != len(learned):
                    iptv_input.save_learned(kept)
                    outer.q.put("__reload_remotes__")
                    self._send(200, {"ok": True})
                    return
                blocks = iptv_net.read_keymap()
                kb = [b for b in blocks if b.get("name") != name]
                if len(kb) == len(blocks):
                    self._send(404, {"error": "not found"})
                    return
                ok = iptv_net.write_keymap(kb) and iptv_net.load_keymap_live()
                self._send(200 if ok else 500, {"ok": ok})

            def _handle_rename_remote(self, raw):
                import iptv_net, iptv_input
                try:
                    d = json.loads(raw or b"{}")
                    old = str(d.get("name", ""))
                    new = str(d.get("new_name", "")).strip()
                except Exception:
                    old, new = "", ""
                if not new:
                    self._send(400, {"error": "empty name"})
                    return
                learned = iptv_input.load_learned()
                if any(r.get("name") == old for r in learned):
                    for r in learned:
                        if r.get("name") == old:
                            r["name"] = new
                    iptv_input.save_learned(learned)
                    self._send(200, {"ok": True})
                    return
                blocks = iptv_net.read_keymap()
                if not any(b.get("name") == old for b in blocks):
                    self._send(404, {"error": "not found"})
                    return
                for b in blocks:
                    if b.get("name") == old:
                        b["name"] = new
                ok = iptv_net.write_keymap(blocks) and iptv_net.load_keymap_live()
                self._send(200 if ok else 500, {"ok": ok})

            def _handle_reset_remotes(self):
                """Restore the known default Argon remote keymap."""
                import iptv_net
                default = [{
                    "name": "Argon Remote", "protocol": "nec", "variant": "nec",
                    "scancodes": {
                        "0x00ca": "KEY_UP", "0x00d2": "KEY_DOWN",
                        "0x0099": "KEY_LEFT", "0x00c1": "KEY_RIGHT",
                        "0x00ce": "KEY_OK", "0x0090": "KEY_BACK",
                        "0x00cb": "KEY_HOME", "0x009d": "KEY_MENU",
                        "0x0080": "KEY_VOLUMEUP", "0x0081": "KEY_VOLUMEDOWN",
                        "0x009c": "KEY_POWER"}}]
                ok = iptv_net.write_keymap(default) and iptv_net.load_keymap_live()
                try:
                    import iptv_input
                    iptv_input.save_learned([])
                    outer.q.put("__reload_remotes__")
                except Exception:
                    ok = False
                self._send(200 if ok else 500, {"ok": ok})

            def _read_provider(self):
                import yaml
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return data.get("provider") or {}

            def _handle_provider_categories(self):
                import urllib.parse as up
                from iptv_config import provider_categories
                qs = up.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (qs.get("q", [""])[0] or "").strip().lower()
                prov = self._read_provider()
                if not prov.get("enabled"):
                    self._send(400, {"error": "Enter your provider URL/username/password and "
                                     "press Save & Apply first."})
                    return
                # Use the on-disk cache only (never the live URL).
                cats = provider_categories(prov, cache_only=True)
                if not cats:
                    self._send(200, {"count": 0, "categories": [],
                                     "hint": "No cached data yet. Press the "
                                             "refresh button to load it."})
                    return
                if query:
                    cats = [c for c in cats if query in c["name"].lower()]
                self._send(200, {"count": len(cats), "categories": cats})

            def _handle_provider_movies(self):
                import urllib.parse as up
                from iptv_config import cached_provider_movies
                qs = up.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (qs.get("q", [""])[0] or "").strip().lower()
                limit = int(qs.get("limit", ["200"])[0] or 200)
                prov = self._read_provider()
                if not prov.get("enabled"):
                    self._send(400, {"error": "Enter your provider URL/username/password and "
                                     "press Save & Apply first."})
                    return
                if not query:
                    self._send(200, {"total": 0, "count": 0, "movies": [],
                                     "hint": "Type a movie name to search."})
                    return
                # Use the on-disk cache only (never the live URL).
                movies = cached_provider_movies(prov)
                if not movies:
                    self._send(200, {"total": 0, "count": 0, "movies": [],
                                     "hint": "No movies loaded yet. Press the "
                                             "refresh button to download them."})
                    return
                results = []
                for m in movies:
                    if all(w in m["name"].lower() for w in query.split()):
                        results.append(m)
                        if len(results) >= limit:
                            break
                self._send(200, {"total": len(movies), "count": len(results),
                                 "movies": results})

            def _handle_provider_series(self):
                import urllib.parse as up
                from iptv_config import cached_provider_series
                qs = up.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (qs.get("q", [""])[0] or "").strip().lower()
                limit = int(qs.get("limit", ["200"])[0] or 200)
                prov = self._read_provider()
                if not prov.get("enabled"):
                    self._send(400, {"error": "Enter your provider URL/username/password and "
                                     "press Save & Apply first."})
                    return
                if not query:
                    self._send(200, {"total": 0, "count": 0, "series": [],
                                     "hint": "Type a series name to search."})
                    return
                # Use the on-disk cache only (never the live URL).
                series = cached_provider_series(prov)
                if not series:
                    self._send(200, {"total": 0, "count": 0, "series": [],
                                     "hint": "No series loaded yet. Press the "
                                             "refresh button to download them."})
                    return
                results = []
                for sitem in series:
                    if all(w in sitem["name"].lower() for w in query.split()):
                        results.append(sitem)
                        if len(results) >= limit:
                            break
                self._send(200, {"total": len(series), "count": len(results),
                                 "series": results})

            def _handle_provider_search(self):
                import urllib.parse as up
                from iptv_config import cached_provider_channels
                qs = up.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (qs.get("q", [""])[0] or "").strip().lower()
                group = (qs.get("group", [""])[0] or "").strip().lower()
                limit = int(qs.get("limit", ["300"])[0] or 300)
                prov = self._read_provider()
                if not prov.get("enabled"):
                    self._send(400, {"error": "Enter your provider URL/username/password and "
                                     "press Save & Apply first."})
                    return
                # Use the on-disk cache only (never the live URL).
                chans = cached_provider_channels(prov)
                results = []
                for c in chans:
                    if query and query not in c["name"].lower():
                        continue
                    if group and group not in c["group"].lower():
                        continue
                    results.append(c)
                    if len(results) >= limit:
                        break
                # also return the list of groups (categories) for a dropdown
                groups = sorted({c["group"] for c in chans})
                self._send(200, {"total": len(chans), "count": len(results),
                                 "channels": results, "groups": groups})

            def _handle_save_config(self, raw):
                try:
                    data = json.loads(raw or b"{}")
                    text = data.get("text", "")
                except Exception:
                    self._send(400, {"error": "bad request"})
                    return
                # validate YAML before saving
                try:
                    import yaml
                    yaml.safe_load(text)
                except Exception as e:
                    self._send(400, {"error": "YAML error: %s" % e})
                    return
                # write + backup
                if not self._write_config(text):
                    return
                # ask the app to reload by injecting a special action
                outer.q.put("__reload_config__")
                self._send(200, {"ok": True})

            def _write_config(self, text):
                """Backup + write config.yaml. Sends error + returns False on fail."""
                try:
                    if os.path.exists(CONFIG_PATH):
                        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                            old = f.read()
                        with open(CONFIG_PATH + ".bak", "w", encoding="utf-8") as f:
                            f.write(old)
                    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                        f.write(text)
                    return True
                except Exception as e:
                    self._send(500, {"error": "save failed: %s" % e})
                    return False

            def _handle_save_config_data(self, raw):
                """Receive structured config as JSON (from the form), write YAML."""
                try:
                    data = json.loads(raw or b"{}")
                except Exception:
                    self._send(400, {"error": "bad request"})
                    return
                try:
                    text = build_config_yaml(data)
                except Exception as e:
                    self._send(400, {"error": "invalid data: %s" % e})
                    return
                # sanity: make sure it parses back
                try:
                    import yaml
                    yaml.safe_load(text)
                except Exception as e:
                    self._send(400, {"error": "YAML error: %s" % e})
                    return
                if not self._write_config(text):
                    return
                outer.q.put("__reload_config__")
                self._send(200, {"ok": True})

        return Handler


def build_config_yaml(data):
    """Turn the form's structured data into a clean, commented config.yaml."""
    import yaml

    def s(v):
        return "" if v is None else str(v)

    prov = data.get("provider") or {}
    epg = data.get("epg") or {}
    settings = data.get("settings") or {}
    playlists = data.get("playlists") or []

    doc = {
        "provider": {
            "enabled": bool(prov.get("enabled", False)),
            "url": s(prov.get("url", "")),
            "username": s(prov.get("username", "")),
            "password": s(prov.get("password", "")),
            "only_categories": [s(c) for c in (prov.get("only_categories") or []) if s(c).strip()],
        },
        "epg": {
            "sources": [s(u) for u in (epg.get("sources") or []) if s(u).strip()],
            "refresh_hours": _num(epg.get("refresh_hours"), 12),
        },
        "playlists": [],
        "settings": {
            "text_size": s(settings.get("text_size", "large")) or "large",
            "audio_delay_ms": _num(settings.get("audio_delay_ms", 0), 0),
        },
    }
    for p in playlists:
        name = s(p.get("name", "")).strip()
        if not name:
            continue
        entry = {"name": name}
        # Optional short display name (alias) for the TV start page.
        if s(p.get("alias", "")).strip():
            entry["alias"] = s(p["alias"]).strip()
        # A fixed "Movies" category keeps its flag (and shows even when empty).
        is_movies = bool(p.get("is_movies"))
        if is_movies:
            entry["is_movies"] = True
        # A fixed "Series" category holds a list of series references.
        is_series = bool(p.get("is_series"))
        if is_series:
            entry["is_series"] = True
        # A playlist can reference a whole provider category.
        if s(p.get("provider_category", "")).strip():
            entry["provider_category"] = s(p["provider_category"]).strip()
        if s(p.get("provider_category_name", "")).strip():
            entry["provider_category_name"] = s(p["provider_category_name"]).strip()
        chans = []
        for c in (p.get("channels") or []):
            url = s(c.get("url", "")).strip()
            cname = s(c.get("name", "")).strip()
            if not url or not cname:
                continue
            ch = {"name": cname, "url": url}
            if s(c.get("logo", "")).strip():
                ch["logo"] = s(c["logo"]).strip()
            if s(c.get("tvg_id", "")).strip():
                ch["tvg_id"] = s(c["tvg_id"]).strip()
            chans.append(ch)
        entry["channels"] = chans
        # Series list (for the Series category).
        series = []
        for se in (p.get("series") or []):
            sid = s(se.get("series_id", "")).strip()
            sname = s(se.get("name", "")).strip()
            if not sid or not sname:
                continue
            item = {"series_id": sid, "name": sname}
            if s(se.get("logo", "")).strip():
                item["logo"] = s(se["logo"]).strip()
            series.append(item)
        if is_series:
            entry["series"] = series
        # Keep category-only / Movies / Series playlists even when empty.
        if (chans or is_movies or is_series or "provider_category" in entry
                or "provider_category_name" in entry):
            doc["playlists"].append(entry)

    header = (
        "# IPTV Player configuration\n"
        "# This file is managed by the Settings page in the web app.\n"
        "# You can still edit it by hand; keep it valid YAML.\n\n"
    )
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False)
    return header + body


def _num(v, default):
    try:
        n = float(v)
        return int(n) if n == int(n) else n
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
#  The web page (single file, no external assets)
# --------------------------------------------------------------------------
PLAYER_PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__</title>
<style>
 body{margin:0;background:#000;color:#e1e4eb;font-family:system-ui,-apple-system,sans-serif}
 video{width:100%;max-height:80vh;background:#000;display:block}
 .bar{padding:1rem;display:flex;flex-direction:column;gap:.7rem}
 h1{font-size:1.1rem;margin:0}
 a.btn{display:block;text-align:center;padding:.9rem;border-radius:10px;
   background:#ff8800;color:#000;font-weight:600;text-decoration:none;font-size:1.05rem}
 a.back{color:#8a90a0;text-align:center;text-decoration:none}
 #st{color:#8a90a0;font-size:.9rem}
 .note{color:#8a90a0;font-size:.85rem;line-height:1.4;background:#1e2130;
   border-radius:10px;padding:.7rem .8rem}
</style></head><body>
<video id="v" controls playsinline preload="auto"></video>
<div class="bar">
 <h1>__NAME__</h1>
 <div id="st"></div>
 <a class="btn" id="vlc" href="#">Open in VLC</a>
 <a class="btn" id="dl" href="#" style="background:#3a3f50;color:#e1e4eb">Download to this device</a>
 <div class="note" id="dlnote">The file is large (__SIZE__). While it downloads, the
  TV can't play anything (the provider allows one connection). Keep this page
  open and the phone awake until the download finishes; on iPhone it is saved
  in the <b>Files</b> app &rsaquo; <b>Downloads</b>.</div>
 <div class="note"><b>First time using VLC?</b> On the iPhone, open
  <b>Settings &rsaquo; Privacy &amp; Security &rsaquo; Local Network</b> and switch
  <b>VLC</b> on. Without it, VLC opens but shows an error and plays nothing.
  (If VLC isn't listed, open the VLC app once and allow the question it asks.)</div>
 <a class="back" href="/">&larr; Back to the remote</a>
</div>
<script>
const src=location.origin+'/stream/movie/__ID__.__EXT__';
document.getElementById('vlc').href='vlc://'+src;
const dl=document.getElementById('dl');
dl.href=src+'?dl=1';
dl.setAttribute('download','');
dl.addEventListener('click',()=>{
  try{ v.pause(); v.removeAttribute('src'); v.load(); }catch(e){}
  st.textContent='Download started - see the download icon in Safari (top right).';
});
// stop Safari's player first: the provider allows only one connection
document.getElementById('vlc').addEventListener('click',()=>{
  try{ v.pause(); v.removeAttribute('src'); v.load(); }catch(e){}
});
const v=document.getElementById('v'), st=document.getElementById('st');
if('__SAFARI__'==='1'){
  st.textContent='Connecting... (the TV stops while you watch here)';
  v.src=src;
  v.addEventListener('playing',()=>st.textContent='');
  v.addEventListener('waiting',()=>st.textContent='Loading...');
  v.addEventListener('error',()=>st.textContent='Could not play here - try "Open in VLC".');
}else{
  v.style.display='none';
  st.textContent='This movie format plays in VLC only. Tap "Open in VLC".';
}
</script></body></html>"""

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>TV Remote</title>
<style>
  :root { --bg:#12141c; --panel:#1e2130; --accent:#007acc; --accent2:#5ac878;
          --text:#e1e4eb; --dim:#8a90a0; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); }
  header { display:flex; gap:.5rem; padding:.6rem 1rem; background:var(--panel);
           position:sticky; top:0; z-index:10; align-items:center; }
  header h1 { font-size:1.1rem; margin:0; flex:1; }
  .tab { background:none; border:1px solid var(--accent); color:var(--text);
         padding:.5rem .9rem; border-radius:10px; font-size:1rem; cursor:pointer; }
  .tab.active { background:var(--accent); }
  main { padding:1rem; max-width:520px; margin:0 auto; }
  section { display:none; } section.active { display:block; }

  /* Remote */
  .remote { display:flex; flex-direction:column; align-items:center; gap:1rem;
            margin-top:.5rem; }
  .power { background:#c0392b; }
  .dpad { display:grid; grid-template-columns:repeat(3,90px);
          grid-template-rows:repeat(3,90px); gap:.4rem; }
  .btn { border:none; border-radius:16px; background:var(--panel); color:var(--text);
         font-size:1.05rem; font-weight:600; cursor:pointer; user-select:none;
         display:flex; align-items:center; justify-content:center; transition:.05s;
         box-shadow:0 2px 0 #0006; }
  .btn:active { transform:translateY(2px); box-shadow:none; background:var(--accent); }
  .dpad .ok { background:var(--accent); border-radius:50%; font-size:1.2rem; }
  .row { display:flex; gap:.6rem; justify-content:center; width:100%; }
  .row .btn { flex:1; height:64px; max-width:150px; }
  .vol { display:flex; gap:.6rem; align-items:center; }
  .vol .btn { width:90px; height:60px; font-size:1.4rem; }
  .status { color:var(--dim); text-align:center; margin-top:.5rem; min-height:1.2em; }
  .hint { color:var(--dim); font-size:.85rem; text-align:center; margin-top:1rem; }

  /* Config form */
  .card { background:var(--panel); border-radius:12px; padding:1rem;
          margin-bottom:1rem; }
  .card h2 { margin:0 0 .8rem; font-size:1.05rem; }
  label { display:block; font-size:.85rem; color:var(--dim); margin:.6rem 0 .2rem; }
  input[type=text], input[type=url], input[type=password], input[type=number],
  select {
    width:100%; background:#0d0f16; color:var(--text); border:1px solid #2a2e40;
    border-radius:8px; padding:.55rem .6rem; font-size:1rem; }
  .checkline { display:flex; align-items:center; gap:.5rem; margin:.6rem 0; }
  .checkline input { width:1.2rem; height:1.2rem; }
  .checkline label { margin:0; color:var(--text); font-size:1rem; }
  .chan, .pl { border:1px solid #2a2e40; border-radius:10px; padding:.7rem;
               margin:.6rem 0; }
  .chan { background:#161925; }
  .plhead { display:flex; gap:.5rem; align-items:center; }
  .plhead input { flex:1; font-weight:700; }
  .minirow { display:flex; gap:.5rem; }
  .minirow > * { flex:1; }
  .addbtn { background:none; border:1px dashed var(--accent); color:var(--text);
            padding:.55rem; border-radius:8px; width:100%; cursor:pointer;
            font-size:.95rem; margin-top:.4rem; }
  .del { background:#3d1212; border:none; color:#e88; border-radius:8px;
         padding:.4rem .7rem; cursor:pointer; font-size:.85rem; }
  .cfgbar { display:flex; gap:.6rem; margin:1rem 0; position:sticky; bottom:0;
            background:var(--bg); padding:.6rem 0; }
  .save { background:var(--accent2); color:#06210f; border:none; padding:.8rem 1.2rem;
          border-radius:10px; font-size:1.05rem; font-weight:700; cursor:pointer; flex:1; }
  .ghost { background:none; border:1px solid var(--accent); color:var(--text);
           border-radius:10px; padding:.8rem 1rem; cursor:pointer; }
  .msg { margin-bottom:.8rem; padding:.6rem .8rem; border-radius:8px; display:none; }
  .msg.ok { background:#123d22; color:#8fe3a8; display:block; }
  .msg.err { background:#3d1212; color:#e88; display:block; white-space:pre-wrap; }
  details.raw summary { color:var(--dim); cursor:pointer; margin-top:1rem; }
  .sr { display:flex; align-items:center; gap:.5rem; padding:.5rem;
        border:1px solid #2a2e40; border-radius:8px; margin:.35rem 0;
        background:#161925; }
  .sr .nm { flex:1; font-size:.95rem; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
  .sr .grp { color:var(--dim); font-size:.72rem; }
  .sr .add { background:var(--accent2); color:#06210f; border:none;
             border-radius:8px; padding:.4rem .8rem; cursor:pointer;
             font-weight:700; flex:0 0 auto; }
  .sr .add.done { background:#2a4d33; color:#8fe3a8; }
  textarea { width:100%; height:40vh; background:#0d0f16; color:#d6f5c6;
             border:1px solid #2a2e40; border-radius:10px; padding:.8rem;
             font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.85rem;
             margin-top:.5rem; }
</style>
</head>
<body>
<header>
  <h1>TV Remote</h1>
  <button class="tab active" data-tab="remote">Remote</button>
  <button class="tab" data-tab="config">Settings</button>
</header>
<main>
  <section id="remote" class="active">
    <div class="remote">
      <button class="btn power" style="width:150px;height:56px" data-b="power">Power</button>
      <div class="dpad">
        <div></div>
        <button class="btn" data-b="up">&#9650;</button>
        <div></div>
        <button class="btn" data-b="left">&#9664;</button>
        <button class="btn ok" data-b="ok">OK</button>
        <button class="btn" data-b="right">&#9654;</button>
        <div></div>
        <button class="btn" data-b="down">&#9660;</button>
        <div></div>
      </div>
      <div class="row">
        <button class="btn" data-b="back">Back</button>
        <button class="btn" data-b="menu">Menu</button>
        <button class="btn" data-b="home">Home</button>
      </div>
      <div class="vol">
        <button class="btn" data-b="vol_down">&#8211;</button>
        <span style="color:var(--dim)">Volume</span>
        <button class="btn" data-b="vol_up">+</button>
        <button class="btn" data-b="mute" style="width:80px;font-size:1rem">Mute</button>
      </div>
      <div class="status" id="status"></div>
    </div>
    <p class="hint">Tip: you can also use the arrow keys, Enter, Backspace and H on a keyboard.</p>
  </section>

  <section id="config">
    <div class="msg" id="msg"></div>

    <div class="card" id="movieCard">
      <h2>Movies</h2>
      <div class="minirow">
        <input type="text" id="movieSearch" placeholder="Search a movie (e.g. Toy Story)">
        <button class="ghost" id="movieBtn" style="flex:0 0 auto">Search</button>
      </div>
      <div id="movieInfo" style="color:var(--dim);font-size:.85rem;margin:.5rem 0"></div>
      <div id="movieResults"></div>
      <div id="movieWatches"></div>
      <div style="color:var(--dim);font-size:.8rem;margin:.9rem 0 .3rem">Added movies</div>
      <div id="movieAdded"></div>
    </div>

    <div class="card" id="seriesCard">
      <h2>TV series</h2>
      <div class="minirow">
        <input type="text" id="seriesSearch" placeholder="Search a series (e.g. Friends)">
        <button class="ghost" id="seriesBtn" style="flex:0 0 auto">Search</button>
      </div>
      <div id="seriesInfo" style="color:var(--dim);font-size:.85rem;margin:.5rem 0"></div>
      <div id="seriesResults"></div>
      <div id="seriesWatches"></div>
      <div style="color:var(--dim);font-size:.8rem;margin:.9rem 0 .3rem">Added series</div>
      <div id="seriesAdded"></div>
    </div>

    <div class="card" id="catCard">
      <h2>Live categories</h2>
      <div class="minirow">
        <input type="text" id="catSearch" placeholder="Search categories (e.g. VGTV, Sport)">
        <button class="ghost" id="catBtn" style="flex:0 0 auto">Search</button>
      </div>
      <div id="catInfo" style="color:var(--dim);font-size:.85rem;margin:.5rem 0"></div>
      <div id="catResults"></div>
      <div style="color:var(--dim);font-size:.8rem;margin:.9rem 0 .3rem">Added categories</div>
      <div id="catAdded"></div>
    </div>

    <div class="card">
      <h2>TV guide (EPG)</h2>
      <p style="color:var(--dim);font-size:.85rem;margin:.2rem 0 .6rem">
        The TV guide is automatic: your live channels are matched to a public
        guide and refreshed every 24 hours. You normally don't need to do
        anything here. You can add extra XMLTV guide URLs below if you want.</p>
      <label>Extra guide addresses (XMLTV URLs, one per line, optional)</label>
      <textarea id="epgSources" style="height:6rem" spellcheck="false"
                placeholder="http://example.com/guide.xml"></textarea>
      <label>Provider lists refresh every (hours)</label>
      <input type="number" id="epgRefresh" min="1" max="720" value="96">
      <p style="color:var(--dim);font-size:.8rem;margin:.3rem 0 0">
        How often the provider lists (channels, movies, series) are refreshed
        in the background while idle. Searching always uses the saved copy, so
        it stays instant. (The TV guide always refreshes every 24 hours.)</p>
    </div>

    <div class="card">
      <h2>Appearance</h2>
      <label>Text size on the TV</label>
      <select id="textSize">
        <option value="small">Small</option>
        <option value="normal">Normal</option>
        <option value="large">Large</option>
        <option value="extra_large">Extra large</option>
      </select>
    </div>

    <div class="card">
      <h2>Playback</h2>
      <label>Audio sync for live TV (milliseconds)</label>
      <input type="number" id="audioDelay" step="10" min="-2000" max="2000"
             placeholder="0">
      <div style="color:var(--dim);font-size:.82rem;margin:.4rem 0 0">
        Shifts the sound relative to the picture on live channels.<br>
        &bull; <b>0</b> = no change (use the stream as-is).<br>
        &bull; A <b>negative</b> number (e.g. <b>-100</b>) makes the sound play
        <b>earlier</b> &mdash; use this when the audio lags <i>behind</i> the video.<br>
        &bull; A <b>positive</b> number (e.g. <b>100</b>) makes the sound play
        <b>later</b> &mdash; use this when the audio comes <i>before</i> the video.<br>
        Takes effect the next time a channel is opened.
      </div>
    </div>

    <div class="card">
      <h2>Remotes</h2>
      <div style="color:var(--dim);font-size:.85rem;margin:0 0 .6rem">
        Learned IR remotes. All listed remotes work at the same time.
        Adding one opens a setup wizard <b>on the TV</b> &mdash; follow the
        on-screen prompts and press each button on the new remote.
      </div>
      <div id="remotesList"></div>
      <div class="minirow" style="margin-top:.6rem">
        <button class="ghost" id="addRemoteBtn">Add a remote (on TV)</button>
        <button class="ghost" id="resetRemotesBtn">Reset to default</button>
      </div>
    </div>

    <div class="card">
      <h2>Wi-Fi</h2>
      <div style="color:var(--dim);font-size:.85rem;margin:0 0 .6rem">
        Saved Wi-Fi networks, best first (by the last speed test). At start-up
        the TV box connects to the first one on the list that is in range.
      </div>
      <div id="wifiNets"></div>
      <div id="wifiInfo" style="color:var(--dim);font-size:.82rem;margin:.5rem 0"></div>
      <div class="minirow">
        <button class="ghost" id="wifiRetestBtn">Re-test connections</button>
        <button class="ghost" id="wifiSetupBtn">Add / change Wi-Fi (on TV)</button>
      </div>
    </div>

    <div class="card">
      <h2>IPTV provider</h2>
      <input type="hidden" id="provEnabled">
      <div id="provFields">
        <label>Provider address or full URL</label>
        <input type="text" id="provUrl"
               placeholder="http://example.com:8080  (or the full get.php?... link)">
        <label>Username (leave blank if it is already in the URL above)</label>
        <input type="text" id="provUser" autocomplete="off">
        <label>Password (leave blank if it is already in the URL above)</label>
        <input type="password" id="provPass" autocomplete="off">
      </div>
    </div>

    <div class="cfgbar">
      <button class="save" id="save">Save &amp; Apply</button>
      <button class="ghost" id="reload">Cancel</button>
    </div>

  </section>
</main>

<script>
function press(b){
  fetch('/api/press',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({button:b})}).catch(()=>{});
}
document.querySelectorAll('[data-b]').forEach(el=>{
  el.addEventListener('click',()=>press(el.dataset.b));
});
// tabs
document.querySelectorAll('.tab[data-tab]').forEach(t=>{
  t.addEventListener('click',()=>{
    document.querySelectorAll('.tab[data-tab]').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.querySelectorAll('section').forEach(s=>s.classList.remove('active'));
    document.getElementById(t.dataset.tab).classList.add('active');
    if(t.dataset.tab==='config') loadForm();
  });
});
// keyboard shortcuts
const KEYMAP={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',
  Enter:'ok',' ':'ok',Backspace:'back',Escape:'back',h:'home',H:'home',
  m:'menu',M:'menu','+':'vol_up','-':'vol_down'};
document.addEventListener('keydown',e=>{
  if(document.getElementById('config').classList.contains('active')) return;
  const b=KEYMAP[e.key]; if(b){e.preventDefault();press(b);}
});
// status poll
async function poll(){
  try{const r=await fetch('/api/status');const s=await r.json();
    let t=''; if(s.screen==='playing'&&s.channel) t='Now watching: '+s.channel;
    else if(s.screen==='channels') t='List: '+s.playlist;
    else if(s.screen==='playlists') t='Home menu';
    else if(s.screen==='power') t='Power menu (Standby / Shut down)';
    else if(s.screen==='standby') t='Standby - press any button to turn on';
    else if(s.screen==='resume') t='Resume? '+s.channel;
    else if(s.screen==='series') t='Series: '+s.playlist;
    else if(s.screen==='seasons'||s.screen==='episodes') t='Series';
    document.getElementById('status').textContent=t;
  }catch(e){}
}
setInterval(poll,1500); poll();

// ---- config form ----
const $ = id => document.getElementById(id);
let PL = [];   // working copy of playlists (Movies, Series, Live categories)

function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

function showMsg(ok,text){
  const m=$('msg'); m.className='msg '+(ok?'ok':'err'); m.textContent=text;
  m.scrollIntoView({behavior:'smooth',block:'nearest'});
}

// -- find or create the fixed Movies / Series playlists --
function moviesPl(){
  let m=PL.find(p=>p.is_movies);
  if(!m){ m={name:'Movies',is_movies:true,channels:[]}; PL.push(m); }
  m.channels=m.channels||[];
  return m;
}
function seriesPl(){
  let m=PL.find(p=>p.is_series);
  if(!m){ m={name:'Series',is_series:true,series:[]}; PL.push(m); }
  m.series=m.series||[];
  return m;
}
function catPls(){
  return PL.filter(p=>p.provider_category!==undefined
                     || p.provider_category_name!==undefined);
}

// -- render the three "Added" lists --
function renderMoviesAdded(){
  const box=$('movieAdded'); box.innerHTML='';
  const ch=(PL.find(p=>p.is_movies)||{}).channels||[];
  if(!ch.length){box.innerHTML='<div style="color:var(--dim);font-size:.85rem">None yet.</div>';return;}
  ch.forEach((c,i)=>{
    const row=document.createElement('div'); row.className='sr';
    const t=document.createElement('div'); t.className='nm'; t.textContent=c.name;
    const play=document.createElement('a'); play.className='add'; play.textContent='\u25b6 Play';
    play.style.cssText='text-decoration:none;margin-right:.4rem';
    const sm=(STREAMABLE||[]).find(m=>m.name===c.name);
    if(sm){ play.href='/player/'+sm.id; }
    else { play.textContent='Save first'; play.style.opacity='.5'; }
    const rm=document.createElement('button'); rm.className='del'; rm.textContent='Remove';
    rm.onclick=()=>{ch.splice(i,1);renderMoviesAdded();refreshSearchButtons();};
    row.appendChild(t); row.appendChild(play); row.appendChild(rm); box.appendChild(row);
  });
}
let STREAMABLE=[];
async function loadStreamable(){
  try{const r=await fetch('/api/stream/movies');STREAMABLE=(await r.json()).movies||[];}
  catch(e){STREAMABLE=[];}
  renderMoviesAdded();
}
function renderSeriesAdded(){
  const box=$('seriesAdded'); box.innerHTML='';
  const ss=(PL.find(p=>p.is_series)||{}).series||[];
  if(!ss.length){box.innerHTML='<div style="color:var(--dim);font-size:.85rem">None yet.</div>';return;}
  ss.forEach((s,i)=>{
    const row=document.createElement('div'); row.className='sr';
    const t=document.createElement('div'); t.className='nm'; t.textContent=s.name;
    const rm=document.createElement('button'); rm.className='del'; rm.textContent='Remove';
    rm.onclick=()=>{ss.splice(i,1);renderSeriesAdded();refreshSearchButtons();};
    row.appendChild(t); row.appendChild(rm); box.appendChild(row);
  });
}
function renderCatsAdded(){
  const box=$('catAdded'); box.innerHTML='';
  const cats=catPls();
  if(!cats.length){box.innerHTML='<div style="color:var(--dim);font-size:.85rem">None yet.</div>';return;}
  cats.forEach(pl=>{
    const wrap=document.createElement('div'); wrap.className='chan';
    const row=document.createElement('div'); row.className='sr';
    row.style.marginBottom='.4rem';
    const t=document.createElement('div'); t.className='nm'; t.textContent=pl.name;
    const rm=document.createElement('button'); rm.className='del'; rm.textContent='Remove';
    rm.onclick=()=>{const k=PL.indexOf(pl);if(k>=0)PL.splice(k,1);renderCatsAdded();refreshSearchButtons();};
    row.appendChild(t); row.appendChild(rm); wrap.appendChild(row);
    const al=document.createElement('input'); al.type='text';
    al.placeholder='Short name to show on TV (optional, e.g. VGTV)';
    al.value=pl.alias||'';
    al.oninput=e=>{pl.alias=e.target.value;};
    wrap.appendChild(al);
    box.appendChild(wrap);
  });
}
function renderAdded(){renderMoviesAdded();renderSeriesAdded();renderCatsAdded();}

// re-mark search result buttons as Added/Add after a change
function refreshSearchButtons(){
  document.querySelectorAll('#movieResults .add').forEach(b=>{
    const added=movieAdded(b.dataset.url);
    b.textContent=added?'Added':'Add'; b.classList.toggle('done',added); b.disabled=added;
  });
  document.querySelectorAll('#seriesResults .add').forEach(b=>{
    const added=seriesAddedId(b.dataset.id);
    b.textContent=added?'Added':'Add'; b.classList.toggle('done',added); b.disabled=added;
  });
  document.querySelectorAll('#catResults .add').forEach(b=>{
    const added=catAdded(b.dataset.id,b.dataset.name);
    b.textContent=added?'Added':'Add'; b.classList.toggle('done',added); b.disabled=added;
  });
}

async function loadForm(){
  $('msg').className='msg';
  const r=await fetch('/api/config-data'); const d=await r.json();
  const prov=d.provider||{}, epg=d.epg||{}, st=d.settings||{};
  $('provEnabled').value = prov.enabled? '1':'';
  $('provUrl').value=prov.url||''; $('provUser').value=prov.username||'';
  $('provPass').value=prov.password||'';
  $('epgSources').value=(epg.sources||[]).join('\n');
  $('epgRefresh').value=epg.refresh_hours||96;
  $('textSize').value=st.text_size||'large';
  $('audioDelay').value=(st.audio_delay_ms!==undefined&&st.audio_delay_ms!==null)?st.audio_delay_ms:0;
  PL=JSON.parse(JSON.stringify(d.playlists||[]));
  PL.forEach(p=>{p.channels=p.channels||[];});
  // ensure Movies & Series always exist
  moviesPl(); seriesPl();
  renderAdded();
  loadStreamable();
  // also fill the raw editor
}

function collectForm(){
  return {
    provider:{
      enabled:true,
      url:$('provUrl').value, username:$('provUser').value,
      password:$('provPass').value,
      only_categories:[],
    },
    epg:{
      sources:$('epgSources').value.split('\n').map(s=>s.trim()).filter(Boolean),
      refresh_hours:parseFloat($('epgRefresh').value)||96,
    },
    settings:{
      text_size:$('textSize').value,
      audio_delay_ms:parseInt($('audioDelay').value,10)||0,
    },
    playlists:PL,
  };
}

// ---- Movies ----
function movieAdded(url){
  const m=PL.find(p=>p.is_movies);
  return !!(m && (m.channels||[]).some(c=>c.url===url));
}
function cleanMovieName(n){
  return n.replace(/^[0-9A-Z\-\| ]{0,10}-\s*/,'').trim() || n;
}
function addMovie(mv,btn){
  if(movieAdded(mv.url)) return;
  moviesPl().channels.push({name:cleanMovieName(mv.name),url:mv.url,logo:mv.logo||''});
  btn.textContent='Added'; btn.classList.add('done'); btn.disabled=true;
  renderMoviesAdded();
}
async function searchMovies(){
  const q=$('movieSearch').value.trim();
  const info=$('movieInfo'); const box=$('movieResults');
  box.innerHTML='';
  if(!q){info.textContent='Type a movie name to search.';return;}
  info.textContent='Searching movies...';
  try{
    const r=await fetch('/api/provider/movies?limit=200&q='+encodeURIComponent(q));
    const d=await r.json();
    if(!r.ok){info.textContent=d.error||'Search failed';return;}
    if(d.hint){info.textContent=d.hint;return;}
    info.textContent=d.count+' shown'+(d.count<d.total?(' (type more to narrow)'):'');
    d.movies.forEach(mv=>{
      const row=document.createElement('div'); row.className='sr';
      const nm=document.createElement('div'); nm.className='nm';
      nm.innerHTML='<div>'+esc(mv.name)+'</div>'+
        (mv.rating?('<div class="grp">Rating '+esc(mv.rating)+'</div>'):'');
      const add=document.createElement('button'); add.className='add'; add.dataset.url=mv.url;
      if(movieAdded(mv.url)){add.textContent='Added';add.classList.add('done');add.disabled=true;}
      else add.textContent='Add';
      add.onclick=()=>addMovie(mv,add);
      row.appendChild(nm); row.appendChild(add); box.appendChild(row);
    });
    if(d.count===0) info.textContent='No movies match "'+q+'".';
    watchButton('movies',q,box);
  }catch(e){info.textContent='Search failed: '+e;}
}
$('movieBtn').onclick=searchMovies;
$('movieSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchMovies();}});

// ---- TV series ----
function seriesAddedId(id){
  const m=PL.find(p=>p.is_series);
  return !!(m && (m.series||[]).some(s=>String(s.series_id)===String(id)));
}
function addSeries(se,btn){
  if(seriesAddedId(se.series_id)) return;
  seriesPl().series.push({series_id:se.series_id,name:se.name,logo:se.logo||''});
  btn.textContent='Added'; btn.classList.add('done'); btn.disabled=true;
  renderSeriesAdded();
}
async function searchSeries(){
  const q=$('seriesSearch').value.trim();
  const info=$('seriesInfo'); const box=$('seriesResults');
  box.innerHTML='';
  if(!q){info.textContent='Type a series name to search.';return;}
  info.textContent='Searching series...';
  try{
    const r=await fetch('/api/provider/series?limit=200&q='+encodeURIComponent(q));
    const d=await r.json();
    if(!r.ok){info.textContent=d.error||'Search failed';return;}
    if(d.hint){info.textContent=d.hint;return;}
    info.textContent=d.count+' shown'+(d.count<d.total?(' (type more to narrow)'):'');
    d.series.forEach(se=>{
      const row=document.createElement('div'); row.className='sr';
      const nm=document.createElement('div'); nm.className='nm';
      nm.innerHTML='<div>'+esc(se.name)+'</div>'+
        (se.group?('<div class="grp">'+esc(se.group)+'</div>'):'');
      const add=document.createElement('button'); add.className='add'; add.dataset.id=se.series_id;
      if(seriesAddedId(se.series_id)){add.textContent='Added';add.classList.add('done');add.disabled=true;}
      else add.textContent='Add';
      add.onclick=()=>addSeries(se,add);
      row.appendChild(nm); row.appendChild(add); box.appendChild(row);
    });
    if(d.count===0) info.textContent='No series match "'+q+'".';
    watchButton('series',q,box);
  }catch(e){info.textContent='Search failed: '+e;}
}
$('seriesBtn').onclick=searchSeries;
$('seriesSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchSeries();}});

// ---- Live categories ----
function catAdded(id,name){
  return PL.some(p => (id && String(p.provider_category)===String(id))
                   || (name && p.provider_category_name===name));
}
function addCategory(cat,btn){
  if(catAdded(cat.id,cat.name)) return;
  PL.push({name:cat.name, provider_category:cat.id,
           provider_category_name:cat.name, channels:[]});
  btn.textContent='Added'; btn.classList.add('done'); btn.disabled=true;
  renderCatsAdded();
}
async function searchCategories(){
  const q=$('catSearch').value.trim();
  const info=$('catInfo'); const box=$('catResults');
  box.innerHTML=''; info.textContent='Searching categories...';
  try{
    const r=await fetch('/api/provider/categories?q='+encodeURIComponent(q));
    const d=await r.json();
    if(!r.ok){info.textContent=d.error||'Failed';return;}
    if(d.hint){info.textContent=d.hint;return;}
    info.textContent=d.count+' categor'+(d.count===1?'y':'ies')+
                     (q?'':' (type above to filter)');
    d.categories.forEach(cat=>{
      const row=document.createElement('div'); row.className='sr';
      const nm=document.createElement('div'); nm.className='nm';
      nm.innerHTML='<div>'+esc(cat.name)+'</div>'+
                   '<div class="grp">'+cat.count+' channels</div>';
      const add=document.createElement('button'); add.className='add';
      add.dataset.id=cat.id; add.dataset.name=cat.name;
      if(catAdded(cat.id,cat.name)){add.textContent='Added';add.classList.add('done');add.disabled=true;}
      else add.textContent='Add';
      add.onclick=()=>addCategory(cat,add);
      row.appendChild(nm); row.appendChild(add); box.appendChild(row);
    });
    if(d.count===0) info.textContent='No categories match "'+q+'".';
  }catch(e){info.textContent='Failed: '+e;}
}
$('catBtn').onclick=searchCategories;
$('catSearch').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchCategories();}});

// ---- Save / Cancel ----
$('reload').onclick=loadForm;   // "Cancel" = discard changes, reload saved config
$('save').onclick=async()=>{
  const r=await fetch('/api/config-data',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(collectForm())});
  const j=await r.json();
  if(r.ok){ showMsg(true,'Saved. The TV is updating...'); setTimeout(loadStreamable,1500); }
  else showMsg(false,j.error||'Save failed');
};

// ---- remotes management ----
async function loadRemotes(){
  try{
    const r=await fetch('/api/remotes'); const j=await r.json();
    const box=$('remotesList'); box.innerHTML='';
    (j.remotes||[]).forEach(rm=>{
      const row=document.createElement('div');
      row.className='minirow';
      row.style.cssText='align-items:center;margin:.3rem 0';
      const label=document.createElement('div');
      label.style.cssText='flex:1';
      label.textContent=rm.name+'  ('+rm.buttons+' buttons'+(rm.kind==='builtin'?', built-in':'')+')';
      const ren=document.createElement('button'); ren.className='ghost';
      ren.textContent='Rename'; ren.style.flex='0 0 auto';
      ren.onclick=async()=>{
        const nn=prompt('New name for "'+rm.name+'":',rm.name);
        if(!nn)return;
        await fetch('/api/remotes/rename',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name:rm.name,new_name:nn})});
        loadRemotes();
      };
      const del=document.createElement('button'); del.className='ghost';
      del.textContent='Delete'; del.style.flex='0 0 auto';
      del.onclick=async()=>{
        if(!confirm('Delete remote "'+rm.name+'"?'))return;
        await fetch('/api/remotes/delete',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name:rm.name})});
        loadRemotes();
      };
      row.appendChild(label); row.appendChild(ren); row.appendChild(del);
      box.appendChild(row);
    });
    if(!(j.remotes||[]).length) box.innerHTML=
      '<div style="color:var(--dim)">No remotes learned yet.</div>';
  }catch(e){}
}
$('addRemoteBtn').onclick=async()=>{
  await fetch('/api/remotes/learn',{method:'POST'});
  showMsg(true,'Look at the TV and follow the remote setup wizard.');
};
$('resetRemotesBtn').onclick=async()=>{
  if(!confirm('Reset to the default remote? This removes learned remotes.'))return;
  await fetch('/api/remotes/reset',{method:'POST'});
  showMsg(true,'Default remote restored.'); loadRemotes();
};
$('wifiSetupBtn').onclick=async()=>{
  await fetch('/api/wifi-setup',{method:'POST'});
  showMsg(true,'Look at the TV to finish Wi-Fi setup.');
};
loadRemotes();

// ---- saved searches ("Add future results automatically") ----
async function loadWatches(){
  try{
    const r=await fetch('/api/saved-searches'); const j=await r.json();
    ['movies','series'].forEach(kind=>{
      const box=$(kind==='movies'?'movieWatches':'seriesWatches'); box.innerHTML='';
      const list=(j.searches||[]).filter(s=>s.kind===kind);
      if(!list.length) return;
      const h=document.createElement('div');
      h.style.cssText='color:var(--dim);font-size:.8rem;margin:.9rem 0 .3rem';
      h.textContent='Waiting for new results (added automatically when the lists are renewed)';
      box.appendChild(h);
      list.forEach(s=>{
        const row=document.createElement('div'); row.className='sr';
        const nm=document.createElement('div'); nm.className='nm';
        nm.textContent='"'+s.query+'"';
        const del=document.createElement('button'); del.className='add'; del.textContent='Delete';
        del.onclick=async()=>{
          await fetch('/api/saved-searches/delete',{method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,query:s.query})});
          loadWatches();
        };
        row.appendChild(nm); row.appendChild(del); box.appendChild(row);
      });
    });
    return j.searches||[];
  }catch(e){return [];}
}
async function watchButton(kind,q,box){
  const list=await loadWatches();
  const b=document.createElement('button'); b.className='ghost';
  b.style.cssText='width:100%;margin:.4rem 0';
  const on=list.some(s=>s.kind===kind&&s.query.toLowerCase()===q.toLowerCase());
  b.textContent=on?'Saved - new results will be added automatically':'Add future results automatically';
  b.disabled=on;
  b.onclick=async()=>{
    const r=await fetch('/api/saved-searches/add',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,query:q})});
    if(r.ok){b.textContent='Saved - new results will be added automatically';b.disabled=true;loadWatches();}
  };
  box.insertBefore(b,box.firstChild);
}
loadWatches();


// ---- saved wifi list + speed test ----
let wifiPoll=null;
async function loadWifi(){
  try{
    const r=await fetch('/api/wifi/networks'); const j=await r.json();
    const box=$('wifiNets'); box.innerHTML='';
    const fmt=v=>(v===null||v===undefined)?'not tested':(v>0?v+' Mbit/s':'failed');
    const row=(title,status,speed,used,delName)=>{
      const d=document.createElement('div');
      d.className='minirow';
      d.style.cssText='align-items:center;margin:.25rem 0;padding:.35rem .5rem;border-radius:8px;'
        +(used?'background:rgba(90,200,120,.18);border:1px solid var(--accent2)':'');
      const a=document.createElement('div'); a.style.flex='1';
      a.textContent=title; if(used){a.style.color='var(--accent2)';a.style.fontWeight='600';}
      const b=document.createElement('div'); b.style.cssText='flex:0 0 auto;color:var(--dim)';
      b.textContent=status;
      const c=document.createElement('div'); c.style.cssText='flex:0 0 6.5rem;text-align:right';
      c.textContent=speed;
      d.appendChild(a); d.appendChild(b); d.appendChild(c);
      if(delName){
        const x=document.createElement('button'); x.className='ghost'; x.textContent='Delete';
        x.style.flex='0 0 auto';
        x.onclick=async()=>{
          if(!confirm('Delete the saved Wi-Fi "'+delName+'"? You will need the password to join it again.'))return;
          await fetch('/api/wifi/delete',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({name:delName})});
          loadWifi();
        };
        d.appendChild(x);
      }
      box.appendChild(d);
    };
    if(j.eth0_connected||(j.eth0!==null&&j.eth0!==undefined))
      row('Ethernet'+(j.active==='eth0'?'  (playing via this)':''),
          j.eth0_connected?'plugged in':'unplugged', fmt(j.eth0), j.active==='eth0', null);
    (j.networks||[]).forEach((n,i)=>{
      const used=n.current&&j.active==='wlan0';
      row((i+1)+'. '+n.name+(used?'  (playing via this)':(n.current?'  (connected)':'')),
          n.reachable?'in range':'not in range', fmt(n.mbit), used, n.name);
    });
    if(!(j.networks||[]).length){const e=document.createElement('div');
      e.style.color='var(--dim)';e.textContent='No saved Wi-Fi networks.';box.appendChild(e);}
    let info=j.tested?('Last test: '+new Date(j.tested*1000).toLocaleString()+'.'):'Never tested.';
    info+=' Green = the connection used for playing.';
    if(j.testing) info='Testing... '+(j.progress||'')+' (the box may briefly drop Wi-Fi)';
    else if(j.error) info+=' '+j.error;
    $('wifiInfo').textContent=info;
    $('wifiRetestBtn').disabled=!!j.testing;
    clearTimeout(wifiPoll);
    if(j.testing) wifiPoll=setTimeout(loadWifi,3000);
  }catch(e){ clearTimeout(wifiPoll); wifiPoll=setTimeout(loadWifi,5000); }
}
$('wifiRetestBtn').onclick=async()=>{
  await fetch('/api/wifi/retest',{method:'POST'});
  $('wifiInfo').textContent='Starting test...';
  setTimeout(loadWifi,2000);
};
loadWifi();

</script>
</body>
</html>"""
