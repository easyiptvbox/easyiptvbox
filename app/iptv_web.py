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
import iptv_profiles

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_STATUS = "/var/lib/iptv-update/status"
UPDATE_SRC = "/opt/easyiptvbox"


def _update_status():
    """Current version + result of the last update run."""
    import subprocess
    st = {"commit": "", "date": "", "last": {}}
    try:
        r = subprocess.run(["git", "-c", "safe.directory=*", "-C", UPDATE_SRC, "log", "-1",
                            "--format=%h|%cs"], capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0 and "|" in r.stdout:
            st["commit"], st["date"] = r.stdout.strip().split("|", 1)
    except Exception:
        pass
    try:
        with open(UPDATE_STATUS) as f:
            for line in f:
                k, _, v = line.rstrip("\n").partition("=")
                st["last"][k] = v
    except OSError:
        pass
    st["auto"] = not os.path.exists("/var/lib/iptv-update/auto-off")
    try:
        r = subprocess.run(["systemctl", "is-active", "iptv-update.service"],
                           capture_output=True, text=True, timeout=5)
        st["running"] = r.stdout.strip() in ("active", "activating")
    except Exception:
        st["running"] = False
    return st


def _current_theme():
    """The box-wide theme (shared by the web app and the TV)."""
    try:
        import yaml
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            st = (yaml.safe_load(f) or {}).get("settings") or {}
        return "light" if str(st.get("theme", "dark")) == "light" else "dark"
    except Exception:
        return "dark"


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
        self.pending_tv = None       # {"kind","url","name","sid"} for "Play on TV"
        self.pending_scan = []       # stream URLs to scan for pictures

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
            def _handle_profiles_post(self, path, raw):
                P = iptv_profiles
                import iptv_remote
                try:
                    if iptv_remote.from_vpn(self.client_address[0]):
                        self._send(403, {"error": "Change profiles from home"})
                        return
                except Exception:
                    pass
                action = path.split("/")[3] if path.count("/") >= 3 else ""
                tok_hdr = self.headers.get("X-Profile-Token", "")
                try:
                    if action == "avatar":      # POST /api/profiles/avatar/<id>
                        pid = path.rsplit("/", 1)[-1]
                        if not P.exists(pid) or not P.token_ok(pid, tok_hdr):
                            self._send(403, {"error": "pin", "profile": pid})
                            return
                        P.save_avatar(pid, raw)
                        self._send(200, {"ok": True})
                        return
                    try:
                        d = json.loads(raw or b"{}")
                    except Exception:
                        d = {}
                    pid = str(d.get("id", ""))
                    tok = str(d.get("token", ""))
                    if action == "unlock":
                        if not P.exists(pid):
                            self._send(404, {"error": "No such profile"})
                            return
                        t = P.unlock(pid, str(d.get("pin", "")))
                        if t is None:
                            self._send(403, {"error": "Wrong PIN"})
                        else:
                            self._send(200, {"ok": True, "token": t})
                        return
                    if action == "create":
                        src = d.get("copy") or None
                        if src and (not P.exists(src) or
                                    not P.token_ok(src, str(d.get("copy_token", "")))):
                            self._send(403, {"error": "Open the profile you "
                                             "want to copy first (PIN)"})
                            return
                        new = P.create(d.get("name", ""), d.get("pin", ""), src)
                        self._send(200, {"ok": True, "id": new})
                        return
                    if action in ("rename", "pin", "delete", "avatar-delete"):
                        if not P.exists(pid):
                            self._send(404, {"error": "No such profile"})
                            return
                        if not P.token_ok(pid, tok):
                            self._send(403, {"error": "pin", "profile": pid})
                            return
                        if action == "rename":
                            P.rename(pid, d.get("name", ""))
                        elif action == "pin":
                            P.set_pin(pid, d.get("pin", ""))
                        elif action == "delete":
                            P.delete(pid)
                        else:
                            P.delete_avatar(pid)
                        self._send(200, {"ok": True})
                        return
                    self._send(404, {"error": "not found"})
                except PermissionError as e:
                    self._send(429, {"error": str(e)})
                except ValueError as e:
                    self._send(400, {"error": str(e)})

            def _handle_livepic_post(self, path, raw):
                import iptv_livepics, iptv_remote
                try:
                    if iptv_remote.from_vpn(self.client_address[0]):
                        self._send(403, {"error": "Change pictures from home"})
                        return
                except Exception:
                    pass
                parts = path.split("/")          # ['', 'api', 'livepic', key, action]
                key = parts[3] if len(parts) > 3 else ""
                action = parts[4] if len(parts) > 4 else ""
                if not iptv_livepics.KEY_RE.match(key):
                    self._send(400, {"error": "bad key"})
                    return
                try:
                    if action == "upload":
                        iptv_livepics.save(key, raw)
                    elif action == "delete":
                        iptv_livepics.delete(key)
                    else:
                        self._send(404, {"error": "not found"})
                        return
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                    return
                self._send(200, {"ok": True})

            def _handle_remote(self, path, raw):
                import iptv_remote
                try:
                    d = json.loads(raw or b"{}")
                except Exception:
                    d = {}
                try:
                    # managing remote access is only allowed from home
                    if iptv_remote.from_vpn(self.client_address[0]):
                        self._send(403, {"error": "Change remote access "
                                         "settings from home"})
                        return
                    if path == "/api/remote/enable":
                        iptv_remote.enable(bool(d.get("on")))
                    elif path == "/api/remote/duckdns":
                        iptv_remote.set_duckdns(str(d.get("domain", "")),
                                                str(d.get("token", "")))
                    elif path == "/api/remote/add":
                        conf = iptv_remote.add_device(str(d.get("name", "")))
                        self._send(200, {"ok": True, "conf": conf,
                                         "qr": iptv_remote.qr_svg(conf)})
                        outer.last_vpn_conf = (str(d.get("name", "")), conf,
                                               time.time())
                        outer.q.put("__remote_qr__")
                        return
                    elif path == "/api/remote/remove":
                        iptv_remote.remove_device(str(d.get("name", "")))
                    else:
                        self._send(404, {"error": "not found"})
                        return
                    self._send(200, {"ok": True})
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                except Exception as e:
                    self._send(500, {"error": str(e)})

            def _stream_item(self, path):
                """/stream/<kind>/<id>.<ext> -> item or None."""
                import iptv_stream
                parts = path.split("/")
                if len(parts) != 4 or parts[2] not in ("movie", "episode", "live"):
                    return None
                return iptv_stream.resolve(parts[3].split(".")[0], parts[2])

            def do_HEAD(self):
                path = self.path.split("?")[0]
                if path.startswith("/stream/"):
                    import iptv_stream
                    iptv_stream.log("HEAD %s ua=%s" % (
                        path, (self.headers.get("User-Agent") or "")[:80]))
                    m = self._stream_item(path)
                    if m and m["kind"] != "live":
                        iptv_stream.serve(self, m, head_only=True,
                                          download="dl=1" in self.path)
                        return
                self.send_response(404)
                self.end_headers()

            PROTECTED = ("/api/config", "/api/play/", "/api/provider/",
                         "/api/saved-searches", "/api/livepic/")

            def _enter(self, method):
                """Pick this request's profile and enforce its PIN."""
                import urllib.parse as up
                path, _, qs = self.path.partition("?")
                q = up.parse_qs(qs)
                pid = (self.headers.get("X-Profile") or
                       (q.get("p") or [""])[0] or iptv_profiles.DEFAULT)
                if not iptv_profiles.exists(pid):
                    pid = iptv_profiles.DEFAULT
                iptv_profiles.set_request(pid)
                img = (method == "GET" and (
                    path.startswith("/api/poster/") or
                    path.startswith("/api/thumb/") or
                    (path.startswith("/api/livepic/") and
                     path != "/api/livepic/list")))
                if (not img and path.startswith(self.PROTECTED) and
                        not iptv_profiles.token_ok(
                            pid, self.headers.get("X-Profile-Token", ""))):
                    self._send(403, {"error": "pin", "profile": pid})
                    return False
                return True

            def do_GET(self):
                try:
                    if self._enter("GET"):
                        self._do_GET()
                finally:
                    iptv_profiles.clear_request()

            def do_POST(self):
                try:
                    if self._enter("POST"):
                        self._do_POST()
                finally:
                    iptv_profiles.clear_request()

            def _do_GET(self):
                path = self.path.split("?")[0]
                if path == "/help":
                    import iptv_help
                    self._send(200, iptv_help.HELP_PAGE, "text/html; charset=utf-8")
                    return
                if path == "/api/remote/status":
                    import iptv_remote
                    st = iptv_remote.status()
                    st["via_vpn"] = iptv_remote.from_vpn(self.client_address[0])
                    self._send(200, st)
                    return
                if path == "/api/update/status":
                    self._send(200, _update_status())
                    return
                if path == "/api/remote/test":
                    import iptv_remote
                    self._send(200, iptv_remote.port_test())
                    return
                if path.startswith("/stream/"):
                    import iptv_stream
                    iptv_stream.log("GET %s range=%s ua=%s" % (
                        path, self.headers.get("Range"),
                        (self.headers.get("User-Agent") or "")[:80]))
                    m = self._stream_item(path)
                    if m:
                        iptv_stream.serve(self, m,
                                          download="dl=1" in self.path
                                          and m["kind"] != "live")
                    else:
                        self._send(404, {"error": "not in your lists"})
                    return
                if path.startswith("/hls/"):
                    import iptv_stream
                    parts = path.split("/")
                    if len(parts) == 4:
                        iptv_stream.hls_serve(self, parts[2], parts[3])
                    else:
                        self._send(404, {"error": "not found"})
                    return
                if path == "/api/profiles":
                    self._send(200, {"profiles": iptv_profiles.list_profiles(),
                                     "active": iptv_profiles.ACTIVE})
                    return
                if path.startswith("/api/profiles/avatar/"):
                    pid = path.rsplit("/", 1)[-1].split(".")[0]
                    fp = (iptv_profiles.avatar_path(pid)
                          if iptv_profiles.exists(pid) and
                          iptv_profiles.has_avatar(pid) else None)
                    if not fp:
                        self._send(404, {"error": "no picture"})
                        return
                    with open(fp, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/api/livepic/list":
                    import iptv_livepics
                    self._send(200, {"keys": iptv_livepics.keys()})
                    return
                if path.startswith("/api/livepic/"):
                    import iptv_livepics
                    fp = iptv_livepics.find(path.rsplit("/", 1)[-1].split(".")[0])
                    if not fp:
                        self._send(404, {"error": "no picture"})
                        return
                    with open(fp, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path.startswith("/api/poster/"):
                    import iptv_stream, iptv_posters
                    pid = path.rsplit("/", 1)[-1].split(".")[0]
                    url = iptv_stream.poster_url(pid)
                    fp = iptv_posters.get_now(url) if url else None
                    if not fp:
                        self._send(404, {"error": "no poster"})
                        return
                    with open(fp, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/api/play/catalog":
                    import iptv_stream
                    try:
                        self._send(200, iptv_stream.catalog())
                    except Exception as e:
                        self._send(500, {"error": str(e)})
                    return
                if path == "/api/play/series":
                    import iptv_stream, urllib.parse as up
                    qs = up.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    try:
                        d = iptv_stream.series_episodes(qs.get("sid", [""])[0])
                        if d is None:
                            self._send(404, {"error": "not an added series"})
                        else:
                            self._send(200, d)
                    except Exception as e:
                        self._send(502, {"error": "Could not load episodes: %s" % e})
                    return
                if path.startswith("/api/thumb/"):
                    import iptv_stream, iptv_thumbs
                    it = iptv_stream.resolve(path.rsplit("/", 1)[-1].split(".")[0],
                                             "live")
                    fp = (iptv_thumbs.thumb_path(it["url"])
                          if it and iptv_thumbs.has_thumb(it["url"]) else None)
                    if not fp:
                        self._send(404, {"error": "no picture"})
                        return
                    with open(fp, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/api/play/state":
                    import iptv_stream
                    self._send(200, {"busy": iptv_stream.busy(),
                                     "live": iptv_stream.hls_state()})
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
                    self._send(200, PAGE.replace("__THEME__", _current_theme()),
                               "text/html; charset=utf-8")
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
                        self._send(200, iptv_profiles.read_config_data())
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
            def _do_POST(self):
                path = self.path.split("?")[0]
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length > 8 * 1024 * 1024:
                    self.close_connection = True
                    self._send(413, {"error": "File too large"})
                    return
                raw = self.rfile.read(length) if length else b""
                if path.startswith("/api/profiles/"):
                    self._handle_profiles_post(path, raw)
                    return
                if path.startswith("/api/livepic/"):
                    self._handle_livepic_post(path, raw)
                    return
                if path.startswith("/api/remote/"):
                    self._handle_remote(path, raw)
                    return
                if path == "/api/update/auto":
                    import subprocess
                    try:
                        on = bool(json.loads(raw or b"{}").get("on"))
                    except Exception:
                        on = True
                    # marker keeps the choice across updates (apply.sh)
                    subprocess.run(["sudo", "-n", "sh", "-c",
                        "mkdir -p /var/lib/iptv-update && " +
                        ("rm -f /var/lib/iptv-update/auto-off && "
                         "systemctl enable --now iptv-update.timer" if on else
                         "touch /var/lib/iptv-update/auto-off && "
                         "systemctl disable --now iptv-update.timer")],
                        capture_output=True, timeout=20)
                    self._send(200, {"ok": True, "auto": on})
                    return
                if path == "/api/play/tv":
                    import iptv_stream
                    try:
                        d = json.loads(raw or b"{}")
                    except Exception:
                        d = {}
                    kind = str(d.get("kind", ""))
                    it = iptv_stream.resolve(str(d.get("id", "")), kind)
                    if not it:
                        self._send(404, {"error": "Not found"})
                        return
                    # the page's own preview must not block it
                    if not iptv_stream.release_for_tv():
                        self._send(409, {"error": "Another device is still "
                                         "streaming from the box. Stop it "
                                         "first."})
                        return
                    outer.pending_tv = {"kind": kind, "url": it["url"],
                                        "name": it["name"],
                                        "sid": str(d.get("sid", "")),
                                        "profile": iptv_profiles.current()}
                    outer.q.put("__play_tv__")
                    self._send(200, {"ok": True})
                    return
                if path == "/api/play/scan":
                    import iptv_stream
                    try:
                        key = str(json.loads(raw or b"{}").get("key", ""))
                    except Exception:
                        key = ""
                    urls = iptv_stream.live_category_urls(key)
                    if urls:
                        outer.pending_scan = urls
                        outer.q.put("__scan_web__")
                    self._send(200, {"ok": True, "channels": len(urls)})
                    return
                if path == "/api/update/run":
                    import subprocess
                    subprocess.Popen(["sudo", "-n", "systemctl", "start",
                                      "--no-block", "iptv-update.service"])
                    self._send(200, {"ok": True})
                    return
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
                                     "wait a moment for it to save, then try again."})
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
                                     "wait a moment for it to save, then try again."})
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
                                     "wait a moment for it to save, then try again."})
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
                                     "wait a moment for it to save, then try again."})
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
                """Backup + write the active request profile's config. Sends
                an error + returns False on failure."""
                path = iptv_profiles.config_path()
                try:
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            old = f.read()
                        with open(path + ".bak", "w", encoding="utf-8") as f:
                            f.write(old)
                        # dated history: keep the last 10 versions
                        import time as _t, glob as _g
                        if old.strip():
                            hist = path + ".%s.bak" % _t.strftime(
                                "%Y%m%d-%H%M%S")
                            with open(hist, "w", encoding="utf-8") as f:
                                f.write(old)
                            for oldf in sorted(_g.glob(path + ".2*.bak"))[:-10]:
                                os.remove(oldf)
                    iptv_profiles.write_config_text(text)
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
                # safety: never wipe all categories by accident
                try:
                    import yaml as _y
                    with open(iptv_profiles.config_path(), "r",
                              encoding="utf-8") as f:
                        cur = _y.safe_load(f) or {}
                    if (cur.get("playlists") and
                            not (_y.safe_load(text) or {}).get("playlists")):
                        self._send(409, {"error": "Refused: this would remove "
                                         "all categories. Reload the page."})
                        return
                except Exception:
                    pass
                if not self._write_config(text):
                    return
                if iptv_profiles.current() in (iptv_profiles.ACTIVE,
                                               iptv_profiles.DEFAULT):
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
            "theme": "light" if s(settings.get("theme", "dark")) == "light" else "dark",
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
  st.textContent='Download started - see the download icon.';
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
<script>document.documentElement.dataset.theme="__THEME__";</script>
<title>TV Remote</title>
<style>
  :root { --bg:#12141c; --panel:#1e2130; --accent:#007acc; --accent2:#5ac878;
          --text:#e1e4eb; --dim:#8a90a0; --line:#2a2e40; --panel2:#161925;
          --field:#0d0f16; --tilebg:#0c0e14; --btn2:#3a3f50; --code:#d6f5c6;
          --errbg:#3d1212; --errtx:#e88; --okbg:#123d22; --oktx:#8fe3a8; --okbg2:#2a4d33; }
  :root[data-theme="light"] { --bg:#f3f5f9; --panel:#ffffff; --accent:#0069b4; --accent2:#2e9e57;
          --text:#1c2230; --dim:#5d6578; --line:#cfd5e1; --panel2:#eef1f6;
          --field:#ffffff; --tilebg:#e3e7ef; --btn2:#d5dae5; --code:#14532d;
          --errbg:#fde4e4; --errtx:#a12020; --okbg:#dcf4e4; --oktx:#17652f; --okbg2:#cfeedb; }
  :root[data-theme="light"] .tab.active { color:#fff; }
  :root[data-theme="light"] .pimg .ptxt { text-shadow:0 1px 2px #0008; }
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
  .dpad .ok { background:var(--accent); border-radius:50%; font-size:1.2rem; color:#fff; }
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
  .card h2 a.hl { color:inherit; text-decoration:none; }
  .card h2 .hi { display:inline-block; width:1.2em; height:1.2em; line-height:1.2em;
                 text-align:center; border-radius:50%; background:var(--btn2);
                 color:var(--text); font-size:.75em; vertical-align:middle; }
  .pbtn { display:block; text-align:center; padding:.85rem; border-radius:10px;
          background:#ff8800; color:#000; font-weight:600; text-decoration:none;
          margin:.6rem 0 0; }
  .pbtn.alt { background:var(--btn2); color:var(--text); }
  .pnote { color:var(--dim); font-size:.82rem; line-height:1.4; margin:.6rem 0 0; }
  label { display:block; font-size:.85rem; color:var(--dim); margin:.6rem 0 .2rem; }
  input[type=text], input[type=url], input[type=password], input[type=number],
  select {
    width:100%; background:var(--field); color:var(--text); border:1px solid var(--line);
    border-radius:8px; padding:.55rem .6rem; font-size:1rem; }
  .checkline { display:flex; align-items:center; gap:.5rem; margin:.6rem 0; }
  .checkline input { width:1.2rem; height:1.2rem; }
  .checkline label { margin:0; color:var(--text); font-size:1rem; }
  .chan, .pl { border:1px solid var(--line); border-radius:10px; padding:.7rem;
               margin:.6rem 0; }
  .chan { background:var(--panel2); }
  .plhead { display:flex; gap:.5rem; align-items:center; }
  .plhead input { flex:1; font-weight:700; }
  .minirow { display:flex; gap:.5rem; }
  .minirow > * { flex:1; }
  .addbtn { background:none; border:1px dashed var(--accent); color:var(--text);
            padding:.55rem; border-radius:8px; width:100%; cursor:pointer;
            font-size:.95rem; margin-top:.4rem; }
  .del { background:var(--errbg); border:none; color:var(--errtx); border-radius:8px;
         padding:.4rem .7rem; cursor:pointer; font-size:.85rem; }
  .cfgbar { display:flex; gap:.6rem; margin:1rem 0; position:sticky; bottom:0;
            background:var(--bg); padding:.6rem 0; }
  .save { background:var(--accent2); color:#06210f; border:none; padding:.8rem 1.2rem;
          border-radius:10px; font-size:1.05rem; font-weight:700; cursor:pointer; flex:1; }
  .pgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(104px,1fr)); gap:.6rem; margin-top:.5rem; }
  .ptile { background:var(--panel,#1e2130); border:2px solid transparent; border-radius:10px; padding:0;
           color:var(--text); text-align:left; cursor:pointer; overflow:hidden; font-size:.75rem; }
  .pgrid.wide { grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); }
  .pgrid.wide .pimg { aspect-ratio:16/9; padding:0; }
  .pimg .ptxt { color:#fff; font-size:.8rem; padding:.3rem; }
  .ptile.sel { border-color:var(--accent); }
  .ptile .pimg { width:100%; aspect-ratio:2/3; background:var(--tilebg); display:flex; align-items:center;
                 justify-content:center; padding:.3rem; text-align:center; font-size:.72rem; color:var(--dim);
                 overflow:hidden; }
  .ptile img { width:100%; height:100%; object-fit:cover; display:block; }
  .ptile .pname { padding:.3rem .4rem; line-height:1.2; max-height:2.5em; overflow:hidden; }
  .ghost { background:none; border:1px solid var(--accent); color:var(--text);
           border-radius:10px; padding:.8rem 1rem; cursor:pointer; }
  .msg { margin-bottom:.8rem; padding:.6rem .8rem; border-radius:8px; display:none; }
  .msg.ok { background:var(--okbg); color:var(--oktx); display:block; }
  .msg.err { background:var(--errbg); color:var(--errtx); display:block; white-space:pre-wrap; }
  details.raw summary { color:var(--dim); cursor:pointer; margin-top:1rem; }
  .sr { display:flex; align-items:center; gap:.5rem; padding:.5rem;
        border:1px solid var(--line); border-radius:8px; margin:.35rem 0;
        background:var(--panel2); }
  .sr .nm { flex:1; font-size:.95rem; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
  .sr .grp { color:var(--dim); font-size:.72rem; }
  .sr .add { background:var(--accent2); color:#06210f; border:none;
             border-radius:8px; padding:.4rem .8rem; cursor:pointer;
             font-weight:700; flex:0 0 auto; }
  .sr .add.done { background:var(--okbg2); color:var(--oktx); }
  textarea { width:100%; height:40vh; background:var(--field); color:var(--code);
             border:1px solid var(--line); border-radius:10px; padding:.8rem;
             font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.85rem;
             margin-top:.5rem; }
</style>
</head>
<body>
<header>
  <h1>TV Remote <span id="profBadge" style="display:none;font-size:.7rem;font-weight:600;padding:.1rem .5rem;border-radius:10px;background:var(--accent);color:#fff;vertical-align:middle"></span></h1>
  <button class="tab active" data-tab="remote">Remote</button>
  <button class="tab" data-tab="play">Play</button>
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

  <section id="play">
    <div class="card" id="pcard" style="display:none">
      <h2 id="ptitle"></h2>
      <video id="pv" controls playsinline preload="metadata"
             style="width:100%;max-height:60vh;background:#000;border-radius:10px"></video>
      <div id="pst" class="pnote"></div>
      <a class="pbtn" id="ptv" href="#" style="background:var(--accent);color:#fff">Play on TV</a>
      <a class="pbtn" id="pvlc" href="#">Open in VLC</a>
      <a class="pbtn alt" id="pdl" href="#" download>Download to this device</a>
      <div class="pnote" id="pdlnote">Movies are large (often several GB). While a
        download runs the TV can't play anything (the provider allows one
        connection). Keep this page open and the phone awake until it finishes;
        on iPhone it is saved in the <b>Files</b> app &rsaquo; <b>Downloads</b>.</div>
      <div class="pnote"><b>First time using VLC?</b> On the iPhone, open
        <b>Settings &rsaquo; Privacy &amp; Security &rsaquo; Local Network</b> and switch
        <b>VLC</b> on. Without it, VLC opens but shows an error and plays nothing.
        (If VLC isn't listed, open the VLC app once and allow the question it asks.)
        <br><b>On a computer</b>, "Open in VLC" downloads a small <b>.m3u</b> file &ndash;
        open it with VLC (choose VLC the first time).</div>
      <div class="pnote">Playing here stops the TV, and the TV shows
        "Busy" until you stop.</div>
    </div>
    <div class="card"><h2><a class="hl" href="/help#play" target="_blank" rel="noopener">Movies <span class="hi">?</span></a></h2><div id="pmovies"></div></div>
    <div class="card"><h2><a class="hl" href="/help#play" target="_blank" rel="noopener">Live TV <span class="hi">?</span></a></h2><div id="plive">Loading&hellip;</div></div>
    <div class="card"><h2><a class="hl" href="/help#play" target="_blank" rel="noopener">Series <span class="hi">?</span></a></h2><div id="pseries"></div><div id="peps"></div></div>
  </section>

  <section id="config">
    <p style="margin:0 0 .6rem;color:var(--dim);font-size:.85rem">Tap a section title for help, or
      <a href="/help" target="_blank" rel="noopener" style="color:var(--accent)">read all instructions</a>.</p>
    <div class="msg" id="msg"></div>

    <div class="card" id="profCard">
      <h2>Profile</h2>
      <label>Profile used in this browser</label>
      <select id="profSel"></select>
      <div id="profPinRow" style="display:none;margin-top:.5rem" class="minirow">
        <input type="password" id="profPin" inputmode="numeric" maxlength="6" placeholder="PIN" autocomplete="off">
        <button class="ghost" id="profUnlock" style="flex:0 0 auto">Unlock</button>
      </div>
      <div id="profNote" class="pnote"></div>
    </div>

    <div class="card" id="movieCard">
      <h2><a class="hl" href="/help#movies" target="_blank" rel="noopener">Movies <span class="hi">?</span></a></h2>
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
      <h2><a class="hl" href="/help#series" target="_blank" rel="noopener">TV series <span class="hi">?</span></a></h2>
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
      <h2><a class="hl" href="/help#live" target="_blank" rel="noopener">Live categories <span class="hi">?</span></a></h2>
      <div class="minirow">
        <input type="text" id="catSearch" placeholder="Search categories (e.g. VGTV, Sport)">
        <button class="ghost" id="catBtn" style="flex:0 0 auto">Search</button>
      </div>
      <div id="catInfo" style="color:var(--dim);font-size:.85rem;margin:.5rem 0"></div>
      <div id="catResults"></div>
      <div style="color:var(--dim);font-size:.8rem;margin:.9rem 0 .3rem">Added categories</div>
      <div id="catAdded"></div>
    </div>


    <div class="card" id="lpCard">
      <h2>Live thumbnails</h2>
      <div class="pnote">Choose a live category and upload a picture for its tile on the TV.
        Channel pictures are made automatically by the picture scan. Without a picture a colored square is shown.</div>
      <label>Category</label>
      <select id="lpCat"></select>
      <div style="display:flex;gap:.8rem;align-items:center;margin-top:.7rem">
        <div id="lpPrev" style="width:160px;height:90px;border-radius:8px;background:var(--tilebg);
             display:flex;align-items:center;justify-content:center;color:var(--dim);
             font-size:.75rem;overflow:hidden;text-align:center">no picture</div>
        <div style="flex:1">
          <input type="file" id="lpFile" accept="image/*">
        </div>
      </div>
      <div style="display:flex;gap:.5rem;margin-top:.6rem">
        <button class="ghost" id="lpUp" style="flex:1">Upload</button>
        <button class="ghost" id="lpDel" style="flex:1">Remove picture</button>
      </div>
      <div id="lpMsg" class="pnote" style="min-height:1.2em"></div>
    </div>

    <div class="card">
      <h2><a class="hl" href="/help#epg" target="_blank" rel="noopener">TV guide (EPG) <span class="hi">?</span></a></h2>
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
      <h2><a class="hl" href="/help#appearance" target="_blank" rel="noopener">Appearance <span class="hi">?</span></a></h2>
      <label>Theme (web app and TV)</label>
      <select id="themeSel">
        <option value="dark">Dark</option>
        <option value="light">Light</option>
      </select>
      <label>Text size on the TV</label>
      <select id="textSize">
        <option value="small">Small</option>
        <option value="normal">Normal</option>
        <option value="large">Large</option>
        <option value="extra_large">Extra large</option>
      </select>
    </div>

    <div class="card">
      <h2><a class="hl" href="/help#playback" target="_blank" rel="noopener">Playback <span class="hi">?</span></a></h2>
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
      <h2><a class="hl" href="/help#remotes" target="_blank" rel="noopener">Remotes <span class="hi">?</span></a></h2>
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
      <h2><a class="hl" href="/help#wifi" target="_blank" rel="noopener">Wi-Fi <span class="hi">?</span></a></h2>
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

    <div class="card" id="profMgr">
      <h2>Manage profiles</h2>
      <div class="pnote">Each profile has its own Movies, Live and Series categories, theme, text size and
        watch history. The Default profile always exists and the TV starts on it.</div>
      <div id="profList"></div>
      <label style="margin-top:.8rem">Add a profile</label>
      <input type="text" id="profNewName" placeholder="Name" maxlength="24" autocomplete="off">
      <input type="password" id="profNewPin" inputmode="numeric" maxlength="6" placeholder="PIN (optional, 4-6 digits)" autocomplete="off" style="margin-top:.4rem">
      <select id="profNewCopy" style="margin-top:.4rem"></select>
      <button class="ghost" id="profCreate" style="width:100%;margin-top:.5rem">Create profile</button>
      <div id="profMsg" class="pnote" style="min-height:1.2em"></div>
    </div>

    <div class="card" id="updCard">
      <h2>Software updates</h2>
      <div id="updStatus" class="pnote"></div>
      <button class="ghost" id="updBtn" style="width:100%;margin-top:.5rem">Update now</button>
      <label style="display:flex;gap:.5rem;align-items:center;margin-top:.7rem;cursor:pointer">
        <input type="checkbox" id="updAuto" style="width:auto;margin:0">
        <span>Check for updates by itself every Monday night</span>
      </label>
      <div class="pnote"><a href="https://github.com/easyiptvbox/easyiptvbox/blob/main/WHATS_NEW.md" target="_blank" rel="noopener">What&rsquo;s new</a> &ndash; see the latest changes on GitHub before you update.</div>
    </div>

    <div class="card" id="raCard">
      <h2><a class="hl" href="/help#vpn" target="_blank" rel="noopener">Remote access (VPN) <span class="hi">?</span></a></h2>
      <div class="pnote" style="margin-top:0">Watch and control the box when you are
        away from home, through an encrypted WireGuard VPN. Only this box is
        reachable over the VPN &ndash; not the rest of your home network.</div>
      <div id="raStatus" class="pnote"></div>
      <div class="minirow" style="margin-top:.6rem">
        <button class="ghost" id="raToggle">&hellip;</button>
        <button class="ghost" id="raTest">Check setup</button>
      </div>

      <label>DuckDNS domain</label>
      <input type="text" id="raDomain" placeholder="easyiptvbox1.duckdns.org" autocomplete="off">
      <label>DuckDNS token (stored only on the box; leave empty to keep the saved one)</label>
      <input type="password" id="raToken" autocomplete="off">
      <button class="ghost" id="raDuckSave" style="width:100%;margin-top:.5rem">Save DuckDNS</button>

      <label>Devices</label>
      <div id="raDevices"></div>
      <div class="minirow" style="margin-top:.5rem">
        <input type="text" id="raNewName" placeholder="Device name, e.g. Stig iPhone">
        <button class="ghost" id="raAdd" style="flex:0 0 auto">Add device</button>
      </div>
      <div id="raQr" style="display:none;margin-top:.8rem;text-align:center">
        <div id="raQrImg" style="background:#fff;padding:10px;border-radius:10px;
             display:inline-block;max-width:280px;width:100%"></div>
        <div class="pnote">Scan this in the <b>WireGuard</b> app:
          <b>+ &rsaquo; Create from QR code</b>. It is shown only once &ndash; add the
          device again if you lose it. The same code is also shown on the TV.</div>
        <a class="pbtn alt" id="raConfDl" href="#" download="easyiptvbox.conf">Download the
          config file instead</a>
      </div>

      <details style="margin-top:1rem"><summary>How to set it up</summary>
        <ol class="pnote" style="padding-left:1.2rem">
          <li><b>Router &ndash; reserve the address:</b> in the router's DHCP settings, reserve
            <b><span class="raLan">192.168.0.168</span></b> for the box (MAC
            <b><span class="raMac"></span></b>), so it never changes and no other device gets it.</li>
          <li><b>Router:</b> forward <b>UDP port <span class="raPort">51821</span></b> to
            <b><span class="raLan">192.168.0.168</span></b>, port <span class="raPort">51821</span>
            (Sagemcom HomeBox: Access Control &rsaquo; Port Forwarding &rsaquo; Add Rules
            Manually; Protocol UDP, External host empty).</li>
          <li><b>DuckDNS:</b> sign in at
            <a href="https://www.duckdns.org" target="_blank" rel="noopener">duckdns.org</a>
            (this creates your account), copy your <b>token</b>, add a domain, then enter both
            above &ndash; so the phone finds your home even when your home address changes.</li>
          <li><b>iPhone:</b> install <b>WireGuard</b> from the App Store, then <b>Add
            device</b> here and scan the QR code.</li>
          <li><b>Away from home:</b> switch the VPN on in WireGuard and open
            <a id="raBookmark" href="http://10.66.0.1:8080">http://10.66.0.1:8080</a>
            &ndash; add it to your Home Screen (Share &rsaquo; Add to Home Screen).
            WireGuard uses the DuckDNS name to find home; the browser always uses
            10.66.0.1.</li>
        </ol>
      </details>
    </div>

    <div class="card">
      <h2><a class="hl" href="/help#provider" target="_blank" rel="noopener">IPTV provider <span class="hi">?</span></a></h2>
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

    <div class="pnote" style="text-align:center">Changes are saved automatically.</div>
    <div id="toast" style="position:fixed;left:50%;bottom:1rem;transform:translateX(-50%);
         padding:.5rem 1rem;border-radius:20px;font-size:.85rem;display:none;z-index:50"></div>

  </section>
</main>

<script>
// ---- profiles: every API call carries this browser's profile (and its PIN token) ----
const PROF={id:'default',tok:{}};
try{ const s=JSON.parse(sessionStorage.getItem('prof')||'{}'); if(s.id) PROF.id=s.id; PROF.tok=s.tok||{}; }catch(e){}
function profSave(){ try{ sessionStorage.setItem('prof',JSON.stringify({id:PROF.id,tok:PROF.tok})); }catch(e){} }
function pq(u){ return u+(u.indexOf('?')>=0?'&':'?')+'p='+PROF.id; }
const _fetch=window.fetch.bind(window);
window.fetch=async(u,o)=>{
  if(typeof u==='string' && u.indexOf('/api/')===0){
    o=Object.assign({},o||{});
    o.headers=Object.assign({'X-Profile':PROF.id,'X-Profile-Token':PROF.tok[PROF.id]||''},o.headers||{});
  }
  const r=await _fetch(u,o);
  if(r.status===403 && PROF.id!=='default' && typeof u==='string' && u.indexOf('/api/profiles')!==0){
    r.clone().json().then(j=>{ if(j&&j.error==='pin'){ PROF.id='default'; profSave(); location.reload(); } }).catch(()=>{});
  }
  return r;
};
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
    if(t.dataset.tab==='config'){ loadProfiles().then(loadForm); loadRemote(); loadUpdate(); loadLivePics(); }
    if(t.dataset.tab==='play') loadPlay();
    else stopPlayer();
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
    const rm=document.createElement('button'); rm.className='del'; rm.textContent='Remove';
    rm.onclick=()=>{ch.splice(i,1);renderMoviesAdded();refreshSearchButtons();};
    row.appendChild(t); row.appendChild(rm); box.appendChild(row);
  });
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
  FORM_LOADED=false;
  const r=await fetch('/api/config-data'); if(!r.ok) return; const d=await r.json();
  const prov=d.provider||{}, epg=d.epg||{}, st=d.settings||{};
  $('provEnabled').value = prov.enabled? '1':'';
  $('provUrl').value=prov.url||''; $('provUser').value=prov.username||'';
  $('provPass').value=prov.password||'';
  $('epgSources').value=(epg.sources||[]).join('\n');
  $('epgRefresh').value=epg.refresh_hours||96;
  $('textSize').value=st.text_size||'large';
  $('themeSel').value=st.theme||'dark';
  document.documentElement.dataset.theme=st.theme||'dark';
  $('audioDelay').value=(st.audio_delay_ms!==undefined&&st.audio_delay_ms!==null)?st.audio_delay_ms:0;
  PL=JSON.parse(JSON.stringify(d.playlists||[]));
  PL.forEach(p=>{p.channels=p.channels||[];});
  // ensure Movies & Series always exist
  moviesPl(); seriesPl();
  renderAdded();
  SAVED_SNAP=snapNow(); AS_LAST=SAVED_SNAP; FORM_LOADED=true;
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
      theme:$('themeSel').value,
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

// ---- Autosave: every change in Settings is saved and applied by itself ----
let FORM_LOADED=false, SAVED_SNAP=null, AS_STABLE=0, AS_LAST='', AS_BUSY=false;
function toast(ok,text){
  const t=$('toast'); t.textContent=text; t.style.display='block';
  t.style.background=ok?'var(--okbg)':'var(--errbg)'; t.style.color=ok?'var(--oktx)':'var(--errtx)';
  clearTimeout(t._h); t._h=setTimeout(()=>t.style.display='none',ok?1800:5000);
}
function snapNow(){ try{ return JSON.stringify(collectForm()); }catch(e){ return null; } }
async function autoSave(){
  if(AS_BUSY||!FORM_LOADED) return;
  const snap=snapNow(); if(snap===null||snap===SAVED_SNAP) return;
  AS_BUSY=true;
  try{
    const r=await fetch('/api/config-data',{method:'POST',
      headers:{'Content-Type':'application/json'},body:snap});
    const j=await r.json().catch(()=>({}));
    if(r.ok){ SAVED_SNAP=snap; toast(true,'Saved'); }
    else toast(false,j.error||'Save failed');
  }catch(e){ toast(false,'Could not reach the box'); }
  AS_BUSY=false;
}
setInterval(()=>{
  if(!FORM_LOADED||SAVED_SNAP===null) return;
  if($('config').offsetParent===null) return;             // Settings tab not shown
  const a=document.activeElement;
  if(a && (a.tagName==='TEXTAREA' || (a.tagName==='INPUT' && !['checkbox','file','button'].includes(a.type)))) {
    return;                                               // still typing: wait for blur
  }
  const snap=snapNow();
  if(snap===null) return;
  if(snap!==AS_LAST){ AS_LAST=snap; AS_STABLE=0; return; }
  if(snap!==SAVED_SNAP && ++AS_STABLE>=1) autoSave();
},1000);
document.addEventListener('visibilitychange',()=>{ if(document.hidden && FORM_LOADED) autoSave(); });

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

// ---- Theme (shared with the TV; saved with the other settings) ----
$('themeSel').addEventListener('change',()=>{ document.documentElement.dataset.theme=$('themeSel').value; });

// ---- Profiles ----
let PROFS=[], PROF_PENDING=null;
const SHARED_IDS=['provEnabled','provUrl','provUser','provPass','epgSources','epgRefresh','audioDelay'];
function profMsg(ok,t){ const m=$('profMsg'); m.textContent=t; m.style.color=ok?'var(--oktx)':'var(--errtx)'; }
function profAvatar(p,px){
  const d=document.createElement('div');
  d.style.cssText='width:'+px+'px;height:'+px+'px;border-radius:50%;overflow:hidden;flex:0 0 auto;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;background:#'+((p.id==='default')?'5a6a8a':(parseInt(p.id.slice(0,6),16)%0xffffff).toString(16).padStart(6,'0'));
  if(p.has_pic){ const i=document.createElement('img'); i.src='/api/profiles/avatar/'+p.id+'.jpg?t='+Date.now(); i.style.cssText='width:100%;height:100%;object-fit:cover';
    d.appendChild(i); }
  else d.textContent=p.name.slice(0,1).toUpperCase();
  return d;
}
function applySharedLock(){
  const lock=(PROF.id!=='default');
  SHARED_IDS.forEach(id=>{ const e=$(id); if(e) e.disabled=lock; });
  const cur=PROFS.find(p=>p.id===PROF.id);
  $('profNote').textContent=lock
    ? 'Editing the profile "'+(cur?cur.name:'')+'": categories, theme and text size. Provider, TV guide and audio sync are shared by the whole box and can only be changed in the Default profile.'
    : 'Editing the Default profile. Provider, TV guide and audio sync are shared by all profiles.';
  const b=$('profBadge');
  if(lock&&cur){ b.textContent=cur.name; b.style.display=''; } else b.style.display='none';
}
async function loadProfiles(){
  let d; try{ d=await (await fetch('/api/profiles')).json(); }catch(e){ return; }
  PROFS=d.profiles||[];
  if(!PROFS.find(p=>p.id===PROF.id)){ PROF.id='default'; profSave(); }
  const sel=$('profSel'); sel.innerHTML='';
  PROFS.forEach(p=>{ const o=document.createElement('option'); o.value=p.id; o.textContent=p.name+(p.has_pin?'  \uD83D\uDD12':''); sel.appendChild(o); });
  sel.value=PROF.id;
  const cp=$('profNewCopy'); cp.innerHTML='';
  const o0=document.createElement('option'); o0.value=''; o0.textContent='Start empty'; cp.appendChild(o0);
  PROFS.forEach(p=>{ const o=document.createElement('option'); o.value=p.id; o.textContent='Copy of '+p.name; cp.appendChild(o); });
  const list=$('profList'); list.innerHTML='';
  PROFS.forEach(p=>{
    const row=document.createElement('div'); row.style.cssText='display:flex;align-items:center;gap:.6rem;margin:.5rem 0;flex-wrap:wrap';
    row.appendChild(profAvatar(p,44));
    const nm=document.createElement('div'); nm.style.cssText='flex:1;min-width:6rem'; nm.textContent=p.name+(p.has_pin?'  \uD83D\uDD12':'')+(p.default?'  (always shown)':''); row.appendChild(nm);
    const mk=(t,fn)=>{ const b=document.createElement('button'); b.className='ghost'; b.type='button'; b.textContent=t; b.style.cssText='flex:0 0 auto;padding:.35rem .6rem;font-size:.8rem'; b.onclick=fn; row.appendChild(b); };
    mk('Picture',()=>profPicture(p));
    if(p.has_pic) mk('Remove picture',()=>profCall('avatar-delete',{id:p.id}));
    if(!p.default){ mk('Rename',()=>{ const n=prompt('New name',p.name); if(n) profCall('rename',{id:p.id,name:n}); });
      mk(p.has_pin?'Change PIN':'Set PIN',()=>{ const n=prompt('PIN, 4 to 6 digits'+(p.has_pin?' (leave empty to remove the PIN)':'')); if(n!==null) profCall('pin',{id:p.id,pin:n}); });
      mk('Delete',()=>{ if(confirm('Delete the profile "'+p.name+'" with its categories and watch history?')) profCall('delete',{id:p.id}); }); }
    list.appendChild(row);
  });
  applySharedLock();
}
async function profCall(action,body){
  body=Object.assign({token:PROF.tok[body.id]||''},body);
  const r=await fetch('/api/profiles/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(r.ok){ profMsg(true,'Done.'); if(action==='delete'&&PROF.id===body.id){ PROF.id='default'; profSave(); await afterProfileChange(); } else await loadProfiles(); return j; }
  profMsg(false, j.error==='pin' ? 'Select that profile at the top of Settings and enter its PIN first.' : (j.error||'Failed.'));
}
function profPicture(p){
  const f=document.createElement('input'); f.type='file'; f.accept='image/*';
  f.onchange=async()=>{
    const file=f.files[0]; if(!file) return;
    if(file.size>6*1024*1024){ profMsg(false,'The picture must be smaller than 6 MB.'); return; }
    const r=await fetch('/api/profiles/avatar/'+p.id,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Profile-Token':PROF.tok[p.id]||''},body:file});
    const j=await r.json().catch(()=>({}));
    if(r.ok){ profMsg(true,'Picture saved.'); await loadProfiles(); }
    else profMsg(false, j.error==='pin' ? 'Select that profile at the top of Settings and enter its PIN first.' : (j.error||'Upload failed.'));
  };
  f.click();
}
async function afterProfileChange(){
  await loadProfiles();
  await loadForm();
  loadLivePics(); loadPlay(); loadWatches();
}
async function profSelect(id){
  const p=PROFS.find(x=>x.id===id); if(!p) return;
  if(typeof autoSave==='function'){ await autoSave(); while(AS_BUSY) await new Promise(r=>setTimeout(r,50)); }
  if(p.has_pin && !PROF.tok[id]){ PROF_PENDING=id; $('profPinRow').style.display=''; $('profPin').value=''; $('profPin').focus(); return; }
  $('profPinRow').style.display='none'; PROF_PENDING=null;
  PROF.id=id; profSave(); await afterProfileChange();
}
$('profSel').onchange=()=>profSelect($('profSel').value);
$('profUnlock').onclick=async()=>{
  const id=PROF_PENDING; if(!id) return;
  const r=await fetch('/api/profiles/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,pin:$('profPin').value})});
  const j=await r.json().catch(()=>({}));
  if(r.ok){ PROF.tok[id]=j.token; $('profPinRow').style.display='none'; PROF_PENDING=null; PROF.id=id; profSave(); await afterProfileChange(); }
  else{ $('profNote').textContent=j.error||'Wrong PIN'; $('profNote').style.color='var(--errtx)'; setTimeout(()=>{ $('profNote').style.color=''; applySharedLock(); },3000); $('profPin').value=''; if(r.status===429||true) $('profSel').value=PROF.id; }
};
$('profPin').addEventListener('keydown',e=>{ if(e.key==='Enter') $('profUnlock').click(); });
$('profCreate').onclick=async()=>{
  const src=$('profNewCopy').value;
  const r=await fetch('/api/profiles/create',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:$('profNewName').value,pin:$('profNewPin').value,copy:src,copy_token:PROF.tok[src]||''})});
  const j=await r.json().catch(()=>({}));
  if(r.ok){ $('profNewName').value=''; $('profNewPin').value=''; profMsg(true,'Profile created.'); await loadProfiles(); }
  else profMsg(false, j.error||'Could not create the profile.');
};

// ---- Live thumbnails ----
let LP={live:[],have:new Set()};
function lpKey(){
  const cat=LP.live[$('lpCat').value]; return cat?cat.key:'';
}
async function lpRefreshHave(){
  try{ const d=await (await fetch('/api/livepic/list')).json(); LP.have=new Set(d.keys||[]); }catch(e){}
}
function lpPreview(){
  const k=lpKey(), box=$('lpPrev'); box.textContent='no picture';
  $('lpDel').disabled=!LP.have.has(k);
  if(k && LP.have.has(k)){ const i=document.createElement('img'); i.style.cssText='width:100%;height:100%;object-fit:cover';
    i.src=pq('/api/livepic/'+k+'.jpg?t='+Date.now()); box.textContent=''; box.appendChild(i); }
}
async function loadLivePics(){
  let d; try{ d=await (await fetch('/api/play/catalog')).json(); }catch(e){ return; }
  LP.live=d.live||[]; await lpRefreshHave();
  const sel=$('lpCat'); const keep=sel.value; sel.innerHTML='';
  LP.live.forEach((c,i)=>{ const o=document.createElement('option'); o.value=i;
    o.textContent=c.name+(LP.have.has(c.key)?'  \u2713':''); sel.appendChild(o); });
  if(keep) sel.value=keep;
  lpPreview();
}
$('lpCat').onchange=lpPreview;
function lpMsg(ok,t){ const m=$('lpMsg'); m.textContent=t; m.style.color=ok?'var(--oktx)':'var(--errtx)'; }
$('lpUp').onclick=async()=>{
  const k=lpKey(), f=$('lpFile').files[0];
  if(!k){ lpMsg(false,'Choose a category first.'); return; }
  if(!f){ lpMsg(false,'Choose a picture file first.'); return; }
  if(f.size>6*1024*1024){ lpMsg(false,'The picture must be smaller than 6 MB.'); return; }
  const r=await fetch('/api/livepic/'+k+'/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:f});
  const j=await r.json().catch(()=>({}));
  if(r.ok){ lpMsg(true,'Picture saved.'); $('lpFile').value=''; await loadLivePics(); }
  else lpMsg(false,j.error||'Upload failed.');
};
$('lpDel').onclick=async()=>{
  const k=lpKey(); if(!k) return;
  const r=await fetch('/api/livepic/'+k+'/delete',{method:'POST'});
  if(r.ok){ lpMsg(true,'Picture removed.'); await loadLivePics(); }
};

// ---- Software updates ----
async function loadUpdate(){
  let st;
  try{ st=await (await fetch('/api/update/status')).json(); }catch(e){ return; }
  const l=st.last||{};
  let t='Version: '+(st.commit?st.commit+' ('+st.date+')':'unknown (not installed from GitHub)');
  if(st.running) t+='\nUpdating\u2026';
  else if(l.time) t+='\nLast check: '+l.time.replace('T',' ').slice(0,16)+' \u2013 '+(l.message||l.result);
  $('updStatus').style.whiteSpace='pre-line';
  $('updStatus').textContent=t;
  $('updBtn').disabled=!!st.running;
  $('updAuto').checked=!!st.auto;
}
$('updAuto').onchange=async()=>{
  const on=$('updAuto').checked;
  await fetch('/api/update/auto',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on:on})});
  showMsg(true,on?'Automatic weekly updates are on.':'Automatic weekly updates are off.');
  loadUpdate();
};
$('updBtn').onclick=async()=>{
  await fetch('/api/update/run',{method:'POST'});
  showMsg(true,'Checking for updates\u2026 the player may restart.');
  setTimeout(loadUpdate,3000);
};

// ---- Remote access ----
async function loadRemote(){
  let st;
  try{ st=await (await fetch('/api/remote/status')).json(); }catch(e){ return; }
  if(!st.installed){ $('raStatus').textContent='Remote access is not installed on this box.'; return; }
  const ago=t=>{ if(!t) return 'never'; const s=Math.round(Date.now()/1000-t);
    return s<90?s+' s ago':s<5400?Math.round(s/60)+' min ago':s<172800?Math.round(s/3600)+' h ago':Math.round(s/86400)+' days ago'; };
  const dd=st.duckdns||{};
  let t=(st.enabled?(st.running?'VPN is ON':'VPN is enabled but not running'):'VPN is OFF')
    +' (UDP '+st.port+').';
  t+=dd.domain?(' DuckDNS: '+dd.domain+(dd.last_ok?(' \u2192 '+dd.last_ip+', updated '+ago(dd.last_ok)):'')
    +(dd.last_error?(' \u2013 '+dd.last_error):'')+'.'):' DuckDNS not set.';
  if(st.via_vpn) t+=' (You are connected over the VPN \u2013 change these settings from home.)';
  $('raStatus').textContent=t;
  $('raToggle').textContent=st.enabled?'Turn remote access off':'Turn remote access on';
  $('raToggle').onclick=async()=>{ await postJ('/api/remote/enable',{on:!st.enabled}); loadRemote(); };
  if(!$('raDomain').value) $('raDomain').value=dd.domain||'';
  document.querySelectorAll('.raPort').forEach(e=>e.textContent=st.port);
  document.querySelectorAll('.raLan').forEach(e=>e.textContent=st.lan_ip||location.hostname);
  document.querySelectorAll('.raMac').forEach(e=>e.textContent=st.lan_mac||'');
  const box=$('raDevices'); box.innerHTML='';
  (st.peers||[]).forEach(p=>{
    const row=document.createElement('div'); row.className='sr';
    const nm=document.createElement('div'); nm.className='nm';
    nm.textContent=p.name+'  \u00b7  last connected: '+ago(p.last_handshake);
    const del=document.createElement('button'); del.className='del'; del.textContent='Remove';
    del.onclick=async()=>{ if(!confirm('Remove "'+p.name+'"? It will no longer be able to connect.'))return;
      await postJ('/api/remote/remove',{name:p.name}); loadRemote(); };
    row.appendChild(nm); row.appendChild(del); box.appendChild(row);
  });
  if(!(st.peers||[]).length) box.innerHTML='<div class="pnote" style="margin:0">No devices yet.</div>';
}
async function postJ(url,body){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  let j={}; try{ j=await r.json(); }catch(e){}
  if(!r.ok){ alert(j.error||'Failed'); throw new Error(j.error); }
  return j;
}
$('raDuckSave').onclick=async()=>{
  try{ await postJ('/api/remote/duckdns',{domain:$('raDomain').value.trim(),token:$('raToken').value.trim()});
    $('raToken').value=''; showMsg(true,'DuckDNS saved.'); }catch(e){}
  loadRemote();
};
$('raAdd').onclick=async()=>{
  const name=$('raNewName').value.trim();
  if(!name){ alert('Enter a device name first'); return; }
  try{
    const j=await postJ('/api/remote/add',{name});
    $('raQrImg').innerHTML=j.qr;
    const svg=$('raQrImg').querySelector('svg'); if(svg){svg.style.width='100%';svg.style.height='auto';}
    $('raConfDl').href='data:text/plain;charset=utf-8,'+encodeURIComponent(j.conf);
    $('raConfDl').download=name.replace(/[^A-Za-z0-9_-]+/g,'_')+'.conf';
    $('raQr').style.display=''; $('raNewName').value='';
  }catch(e){}
  loadRemote();
};
$('raTest').onclick=async()=>{
  $('raStatus').textContent='Checking\u2026';
  const r=await (await fetch('/api/remote/test')).json();
  let t=(r.listening?'\u2713 VPN service is running.':'\u2717 VPN service is not running \u2013 turn remote access on.');
  t+=' Public address: '+(r.public_ip||'unknown')+'.';
  if(r.dns_ok===true) t+=' \u2713 DuckDNS points to it.';
  else if(r.dns_ok===false) t+=' \u2717 DuckDNS points to '+(r.dns_ip||'nothing')+' \u2013 check the token, or wait a few minutes.';
  else t+=' DuckDNS not set.';
  t+=' The router port forward can only be confirmed by connecting from outside (phone on mobile data).';
  $('raStatus').textContent=t;
};

// ---- Play tab ----
let PSELS=[];
function stopPlayer(){
  const v=$('pv'); if(!v) return;
  try{ v.pause(); v.removeAttribute('src'); v.load(); }catch(e){}
}
function pSelect(label,options,onpick){
  const wrap=document.createElement('div');
  if(label){const l=document.createElement('label');l.textContent=label;wrap.appendChild(l);}
  const sel=document.createElement('select');
  const o0=document.createElement('option'); o0.value=''; o0.textContent='\u2014 choose \u2014';
  sel.appendChild(o0);
  options.forEach(o=>{
    if(o.group){
      const g=document.createElement('optgroup'); g.label=o.group;
      o.items.forEach(it=>{const x=document.createElement('option');x.value=it.value;x.textContent=it.text;g.appendChild(x);});
      sel.appendChild(g);
    }else{
      const x=document.createElement('option'); x.value=o.value; x.textContent=o.text; sel.appendChild(x);
    }
  });
  sel.onchange=()=>{ PSELS.forEach(s=>{ if(s!==sel && !s.dataset.keep) s.value=''; }); if(sel.value) onpick(sel.value); };
  PSELS.push(sel);
  wrap.appendChild(sel);
  return wrap;
}
function posterGrid(items,onpick,wide){
  const wrap=document.createElement('div');
  let box=null;
  if(items.length>8){
    box=document.createElement('input'); box.type='text'; box.placeholder='Filter by name';
    box.style.marginBottom='.2rem'; wrap.appendChild(box);
  }
  const grid=document.createElement('div'); grid.className='pgrid'+(wide?' wide':''); wrap.appendChild(grid);
  const failed=[];
  // re-try tiles whose picture did not exist yet (the TV makes them in the background)
  wrap.retryFailed=()=>{
    failed.splice(0).forEach(f=>{
      if(!document.body.contains(f.im)) return;
      const i=new Image(); i.alt='';
      i.onload=()=>{ f.im.textContent=''; f.im.style.background=''; f.im.appendChild(i);
        i.style.cssText='width:100%;height:100%;object-fit:cover;display:block'; };
      i.onerror=()=>{ failed.push(f); };
      i.src=f.it.src+(f.it.src.includes('?')?'&':'?')+'t='+Date.now();
    });
  };
  const tiles=items.map(it=>{
    const b=document.createElement('button'); b.className='ptile'; b.type='button';
    const im=document.createElement('div'); im.className='pimg';
    const fallback=()=>{ im.textContent=''; if(it.color) im.style.background='rgb('+it.color.join(',')+')';
      const sp=document.createElement('span'); sp.className='ptxt'; sp.textContent=it.name; if(!it.color) sp.style.color='var(--dim)'; im.appendChild(sp); };
    if(it.src){
      const i=document.createElement('img'); i.loading='lazy'; i.alt=''; i.src=it.src;
      i.onerror=()=>{ i.remove(); fallback(); if(it.retry) failed.push({im,it,fallback}); };
      im.appendChild(i);
    }else{ fallback(); if(it.retry) failed.push({im,it,fallback}); }
    const nm=document.createElement('div'); nm.className='pname'; nm.textContent=it.name;
    b.appendChild(im); b.appendChild(nm);
    b.onclick=()=>{ tiles.forEach(t=>t.el.classList.remove('sel')); b.classList.add('sel'); onpick(it); };
    grid.appendChild(b);
    return {el:b,name:it.name.toLowerCase()};
  });
  if(box) box.oninput=()=>{ const q=box.value.trim().toLowerCase();
    tiles.forEach(t=>t.el.style.display=(!q||t.name.includes(q))?'':'none'); };
  return wrap;
}
let LIVETIMER=null, CURSID='';
async function loadPlay(){
  let d;
  try{ const r=await fetch('/api/play/catalog'); d=await r.json(); }
  catch(e){ $('plive').textContent='Could not load the lists.'; return; }
  PSELS=[];
  const live=$('plive');
  let have=new Set();
  try{ have=new Set((await (await fetch('/api/livepic/list')).json()).keys||[]); }catch(e){}
  const pal=d.palette||[[60,60,60]];
  const col=ci=>pal[ci%pal.length];
  const showCats=()=>{
    live.innerHTML='';
    if(!(d.live||[]).length){ live.textContent='No live categories added under Settings.'; return; }
    live.appendChild(posterGrid((d.live||[]).map(c=>({key:c.key,name:c.name,color:col(c.ci),
      src:have.has(c.key)?pq('/api/livepic/'+c.key+'.jpg'):'',c:c})),it=>showChans(it.c),true));
  };
  const showChans=cat=>{
    live.innerHTML='';
    const b=document.createElement('button'); b.className='ghost'; b.type='button';
    b.textContent='\u2190 '+cat.name+' ('+cat.channels.length+')'; b.style.cssText='width:100%;margin-bottom:.3rem';
    b.onclick=showCats; live.appendChild(b);
    const g=posterGrid(cat.channels.map(c=>({key:c.id,name:c.name,color:col(c.ci),retry:true,
      src:have.has('c'+c.id)?pq('/api/livepic/c'+c.id+'.jpg'):pq('/api/thumb/'+c.id+'.jpg'),c:c})),
      it=>playItem('live',it.c.id,it.c.name,'ts'),true);
    live.appendChild(g);
    // ask the box to make pictures for this category (only while the TV is idle)
    fetch('/api/play/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:cat.key})}).catch(()=>{});
    if(LIVETIMER) clearInterval(LIVETIMER);
    LIVETIMER=setInterval(()=>{ if(!document.body.contains(g)){ clearInterval(LIVETIMER); LIVETIMER=null; return; } g.retryFailed(); },12000);
  };
  showCats();
  const mv=$('pmovies'); mv.innerHTML='';
  if((d.movies||[]).length)
    mv.appendChild(posterGrid(d.movies.map(m=>({key:m.id,name:m.name,src:m.poster?pq('/api/poster/'+m.poster+'.jpg'):'',m:m})),
      it=>playItem('movie',it.m.id,it.m.name,it.m.ext)));
  else mv.textContent='No movies added under Settings.';
  const se=$('pseries'); se.innerHTML=''; $('peps').innerHTML='';
  if((d.series||[]).length){
    se.appendChild(posterGrid(d.series.map(s=>({key:s.sid,name:s.name,src:s.poster?pq('/api/poster/'+s.poster+'.jpg'):'',s:s})),
      it=>{ CURSID=it.s.sid; loadEpisodes(it.s.sid); }));
  } else se.textContent='No series added under Settings.';
}
async function loadEpisodes(sid){
  const box=$('peps'); box.textContent='Loading episodes\u2026';
  PSELS=PSELS.filter(s=>document.body.contains(s) && !box.contains(s));
  try{
    const r=await fetch('/api/play/series?sid='+encodeURIComponent(sid)); const d=await r.json();
    if(!r.ok){ box.textContent=d.error||'Could not load episodes.'; return; }
    box.innerHTML='';
    const all={};
    const groups=(d.seasons||[]).map(s=>({group:s.name,items:s.episodes.map(e=>{all[e.id]=e;return {value:e.id,text:e.title};})}));
    box.appendChild(pSelect('Episode',groups,id=>playItem('episode',id,all[id].title,all[id].ext)));
  }catch(e){ box.textContent='Could not load episodes.'; }
}
let PCUR=null;
function playItem(kind,id,name,ext){
  stopPlayer();
  const v=$('pv'), st=$('pst');
  const src=location.origin+'/stream/'+kind+'/'+id+'.'+(ext||'ts');
  $('pcard').style.display=''; $('ptitle').textContent=name;
  const tq='';
  const vsrc=src+(tq?'?'+tq:'');
  const ios=/iPhone|iPad|iPod/.test(navigator.userAgent) ||
            (navigator.platform==='MacIntel' && navigator.maxTouchPoints>1);
  if(ios){
    // VLC for iOS understands vlc:// links
    $('pvlc').href='vlc://'+vsrc;
    $('pvlc').removeAttribute('download');
  }else{
    // desktop VLC doesn't: hand over a small playlist file instead
    $('pvlc').href='data:audio/x-mpegurl;charset=utf-8,'+
      encodeURIComponent('#EXTM3U\n#EXTINF:-1,'+name+'\n'+vsrc+'\n');
    $('pvlc').setAttribute('download',(name||'stream').replace(/[^A-Za-z0-9 _.-]+/g,'_').trim()+'.m3u');
  }
  PCUR={kind:kind,id:id,sid:CURSID};
  const isLive=(kind==='live');
  $('pdl').style.display=isLive?'none':''; $('pdlnote').style.display=isLive?'none':'';
  $('pdl').href=src+'?dl=1'+(tq?'&'+tq:'');
  v.style.display='';
  if(isLive){
    if(v.canPlayType('application/vnd.apple.mpegurl')){
      v.src='/hls/'+id+'/index.m3u8';
      st.textContent='Press play. Live TV can take 10\u201320 seconds to start.';
    }else{
      v.style.display='none';
      st.textContent='Live TV plays in Safari (iPhone/iPad/Mac) or in VLC.';
    }
  }else if(['mp4','m4v','mov'].includes((ext||'').toLowerCase())){
    v.src=src; st.textContent='';
  }else{
    v.style.display='none';
    st.textContent='This format plays in VLC only (or download it).';
  }
  $('pcard').scrollIntoView({behavior:'smooth'});
}
$('ptv').onclick=async(e)=>{
  e.preventDefault(); if(!PCUR) return;
  stopPlayer();
  const st=$('pst');
  try{
    const r=await fetch('/api/play/tv',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(PCUR)});
    const j=await r.json().catch(()=>({}));
    st.textContent=r.ok?'Started on the TV.':(j.error||'Could not start on the TV.');
  }catch(err){ st.textContent='Could not reach the box.'; }
};
(function(){
  const v=$('pv'), st=$('pst');
  v.addEventListener('playing',()=>st.textContent='');
  v.addEventListener('waiting',()=>st.textContent='Loading\u2026');
  v.addEventListener('error',()=>{ if(v.getAttribute('src')) st.textContent='Could not play here \u2013 try "Open in VLC".'; });
  $('pvlc').addEventListener('click',stopPlayer);
  $('pdl').addEventListener('click',()=>{ stopPlayer(); st.textContent='Download started - see the download icon.'; });
})();

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
if(PROF.id!=='default'){      // restored profile of this browser session
  loadProfiles();
  fetch('/api/config-data').then(r=>r.ok?r.json():null).then(d=>{ if(d) document.documentElement.dataset.theme=(d.settings||{}).theme||'dark'; }).catch(()=>{});
}

</script>
</body>
</html>"""
