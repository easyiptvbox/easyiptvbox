"""Stream added movies to other devices (phone/computer) through the Pi.

The device requests /stream/movie/<id>; the Pi fetches the movie from the IPTV
provider and relays it, honouring HTTP Range requests so players can seek.
The provider login never leaves the Pi, and only movies in the Movies category
can be streamed.

To make playback start instantly, the first PREFIX_BYTES of every added movie
are cached on the SD card while the box is idle; requests that start inside
that prefix are answered from the cache first.

The provider allows ONE connection at a time, so while a device is streaming
the TV must not play (see busy()).
"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request

from iptv_config import CACHE_DIR, CONFIG_PATH
import iptv_livepics as _lp

PREFIX_BYTES = 20 * 1024 * 1024
STREAM_DIR = os.path.join(CACHE_DIR, "stream")
UA = "IPTVPlayer/1.0"
CHUNK = 256 * 1024
RETRY_SECONDS = 90          # provider may still count the TV's connection

_lock = threading.Lock()
_active = 0                 # open relay connections
_last_activity = 0.0
_notify = None              # callable() - tell the TV a device took over


LOG_PATH = os.path.join(CACHE_DIR, "stream.log")


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def set_notify(fn):
    global _notify
    _notify = fn


def busy(grace=6):
    """True while a device is streaming (or was, within `grace` seconds)."""
    return (_active > 0 or (time.time() - _last_activity) < grace
            or _hls.get("alive", False))


def movie_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _ext(url):
    tail = url.rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1].lower() if "." in tail else ""


def _item(kind, name, url):
    return {"id": movie_id(url), "kind": kind, "name": str(name),
            "url": url, "ext": _ext(url)}


_reg = {"key": None, "items": {}, "catalog": None, "posters": {}}
_reg_lock = threading.Lock()
_extra = {}                 # episodes registered when listed


def _registry():
    """Everything that may be streamed: the categories/movies/series added
    under Settings. Rebuilt when the config or provider cache changes."""
    def mt(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0
    key = (mt(CONFIG_PATH),
           mt(os.path.join(CACHE_DIR, "provider_channels.json")))
    with _reg_lock:
        if _reg["key"] == key and _reg["catalog"] is not None:
            return _reg["items"], _reg["catalog"]
        from iptv_config import load_config
        cfg = load_config()
        items, live, movies, series = {}, [], [], []
        posters = {}

        def poster(url):
            """Short id for a provider poster URL ('' if none)."""
            if not url or not str(url).lower().startswith(("http://", "https://")):
                return ""
            pid = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:12]
            posters[pid] = str(url)
            return pid

        for p in cfg.playlists:
            if p.is_movies:
                for c in p.channels:
                    if c.url:
                        it = _item("movie", c.name, c.url)
                        items[it["id"]] = it
                        movies.append({"id": it["id"], "name": it["name"],
                                       "ext": it["ext"],
                                       "poster": poster(c.logo)})
            elif getattr(p, "is_series", False):
                for se in p.series:
                    series.append({"sid": str(se["series_id"]),
                                   "name": se["name"],
                                   "poster": poster(se.get("logo", ""))})
            else:
                chans = []
                for c in p.channels:
                    if c.url:
                        it = _item("live", c.name, c.url)
                        items[it["id"]] = it
                        chans.append({"id": it["id"], "name": it["name"],
                                      "ci": _lp.color_index(it["name"])})
                live.append({"name": p.display_name,
                             "key": "k" + hashlib.sha1(p.display_name.strip()
                                      .encode("utf-8")).hexdigest()[:12],
                             "ci": _lp.color_index(p.display_name),
                             "channels": chans})
        cat = {"live": live, "movies": movies, "series": series,
               "palette": _lp.PALETTE}
        _reg.update(key=key, items=items, catalog=cat, posters=posters)
        return items, cat


def live_category_urls(key):
    """Stream URLs of the channels in the live category with this key."""
    items, cat = _registry()
    for c in cat["live"]:
        if c.get("key") == key:
            return [items[ch["id"]]["url"] for ch in c["channels"]
                    if ch["id"] in items]
    return []


def poster_url(pid):
    """Provider poster URL for a catalog poster id (only known ids)."""
    _registry()
    return _reg["posters"].get(pid)


def catalog():
    return _registry()[1]


def series_episodes(sid):
    """Seasons/episodes of an added series (ids registered for streaming)."""
    if sid not in {s["sid"] for s in catalog()["series"]}:
        return None
    from iptv_config import fetch_series_episodes, load_config
    prov = load_config()._data.get("provider") or {}
    data = fetch_series_episodes(prov, sid)
    out = []
    for season in data.get("seasons", []):
        eps = []
        for e in season.get("episodes", []):
            it = _item("episode", e["title"], e["url"])
            _extra[it["id"]] = it
            eps.append({"id": it["id"], "title": it["name"], "ext": it["ext"]})
        out.append({"name": season.get("name", ""), "episodes": eps})
    return {"seasons": out}


def resolve(item_id, kind=None):
    """The streamable item with this id, or None."""
    it = _registry()[0].get(item_id) or _extra.get(item_id)
    if it is None and kind in (None, "episode"):
        # after a restart: look in the cached episode lists of added series
        sids = {s["sid"] for s in catalog()["series"]}
        for sid in sids:
            safe = "".join(ch for ch in sid if ch.isalnum())
            try:
                with open(os.path.join(CACHE_DIR, "series_%s.json" % safe),
                          "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            for season in data.get("seasons", []):
                for e in season.get("episodes", []):
                    if movie_id(e["url"]) == item_id:
                        it = _item("episode", e["title"], e["url"])
                        _extra[item_id] = it
                        break
    if it and kind and it["kind"] != kind:
        return None
    return it


def added_movies():
    items = _registry()[0]
    return [it for it in items.values() if it["kind"] == "movie"]


def find_movie(mid):
    return resolve(mid, "movie")


def content_type(ext):
    return {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime",
            "mkv": "video/x-matroska", "avi": "video/x-msvideo",
            "ts": "video/mp2t"}.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
#  Prefix cache
# ---------------------------------------------------------------------------
def _paths(mid):
    return (os.path.join(STREAM_DIR, mid + ".bin"),
            os.path.join(STREAM_DIR, mid + ".json"))


def cache_info(mid):
    """{"size": total_file_size, "have": cached_bytes} or None."""
    binp, metap = _paths(mid)
    try:
        with open(metap, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["have"] = os.path.getsize(binp)
        return meta if meta.get("size") else None
    except Exception:
        return None


def size_text(mid):
    info = cache_info(mid)
    if not info:
        return "several GB"
    gb = info["size"] / 1e9
    return ("%.1f GB" % gb) if gb >= 1 else ("%d MB" % (info["size"] / 1e6))


def _parse_total(content_range):
    # "bytes 0-1023/123456"
    try:
        return int(content_range.rsplit("/", 1)[1])
    except Exception:
        return 0


def cache_prefix(movie, keep_going):
    """Download the first PREFIX_BYTES of `movie`. Stops early (and discards
    the partial file) if keep_going() turns False. Returns True on success."""
    os.makedirs(STREAM_DIR, exist_ok=True)
    binp, metap = _paths(movie["id"])
    tmp = binp + ".part"
    req = urllib.request.Request(movie["url"], headers={
        "User-Agent": UA, "Range": "bytes=0-%d" % (PREFIX_BYTES - 1)})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            total = _parse_total(r.headers.get("Content-Range", "")) or \
                int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                while got < PREFIX_BYTES:
                    if not keep_going():
                        raise InterruptedError()
                    buf = r.read(min(CHUNK, PREFIX_BYTES - got))
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
        if not total or got == 0:
            raise ValueError("no data")
        os.replace(tmp, binp)
        with open(metap, "w", encoding="utf-8") as f:
            json.dump({"size": total, "url": movie["url"]}, f)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def prune_cache(keep_ids):
    try:
        for name in os.listdir(STREAM_DIR):
            mid = name.split(".", 1)[0]
            if mid not in keep_ids:
                os.remove(os.path.join(STREAM_DIR, name))
    except OSError:
        pass


def start_cacher(is_idle):
    """Background thread: cache the start of every added movie, one at a
    time, only while is_idle() is True and no device is streaming."""
    def ok():
        return is_idle() and not busy()

    def loop():
        time.sleep(60)
        while True:
            try:
                movies = added_movies()
                prune_cache({m["id"] for m in movies})
                for m in movies:
                    info = cache_info(m["id"])
                    if info and info["have"] >= min(PREFIX_BYTES, info["size"]):
                        continue
                    if not ok():
                        break
                    if cache_prefix(m, ok):
                        print("stream cache: cached start of %s" % m["name"],
                              flush=True)
                    time.sleep(15)   # be gentle with the single connection
            except Exception as e:
                print("stream cacher error: %s" % e, flush=True)
            time.sleep(120)
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------------------
#  Relay
# ---------------------------------------------------------------------------
def _parse_range(header, size):
    """Return (start, end) inclusive for a single 'bytes=a-b' range."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    a, _, b = spec.partition("-")
    try:
        if a == "":                       # suffix: last N bytes
            n = int(b)
            return (max(0, size - n), size - 1) if size else None
        start = int(a)
        end = int(b) if b else (size - 1 if size else None)
        if size and end is not None:
            end = min(end, size - 1)
        return (start, end)
    except ValueError:
        return None


def _open_upstream(url, start, end=None, deadline=None, status_cb=None):
    """Open the provider stream at byte `start`, retrying while the provider
    refuses (it allows a single connection and may still count the TV's)."""
    headers = {"User-Agent": UA}
    if start or end is not None:
        headers["Range"] = "bytes=%d-%s" % (start, "" if end is None else end)
    deadline = deadline or (time.time() + RETRY_SECONDS)
    last = None
    while True:
        req = urllib.request.Request(url, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (401, 403, 429, 458, 460, 461, 503, 509):
                raise
        except (urllib.error.URLError, OSError) as e:
            last = e
        if time.time() > deadline:
            raise last or TimeoutError()
        if status_cb:
            status_cb("waiting")
        time.sleep(3)


def download_name(movie):
    """A clean file name like 'To Catch a Killer (2023).mp4'."""
    import re
    name = re.sub(r'[\\/:*?"<>|]+', " ", movie["name"])
    name = re.sub(r"\s+", " ", name).strip() or "movie"
    return "%s.%s" % (name, movie["ext"] or "mp4")


def _disposition(fname):
    import urllib.parse
    ascii_name = fname.encode("ascii", "replace").decode().replace("?", "_")
    return 'attachment; filename="%s"; filename*=UTF-8\'\'%s' % (
        ascii_name.replace('"', ""), urllib.parse.quote(fname))


def serve(handler, movie, head_only=False, download=False):
    """Answer an HTTP GET/HEAD for `movie` on BaseHTTPRequestHandler
    `handler`, with Range support and the cached prefix."""
    global _active, _last_activity
    mid = movie["id"]
    ctype = content_type(movie["ext"])
    info = cache_info(mid)
    size = info["size"] if info else 0
    have = info["have"] if info else 0
    rng = _parse_range(handler.headers.get("Range"), size)

    with _lock:
        first = _active == 0 and (time.time() - _last_activity) > 6
        _active += 1
        _last_activity = time.time()
    hls_stop()
    if first and _notify:
        try:
            _notify()            # stop the TV, show "Playing on another device"
        except Exception:
            pass
    try:
        # ---- fast path: we know the size, answer headers ourselves ------
        if size:
            start, end = rng if rng else (0, size - 1)
            if end is None:
                end = size - 1
            if start >= size:
                handler.send_response(416)
                handler.send_header("Content-Range", "bytes */%d" % size)
                handler.end_headers()
                return
            length = end - start + 1
            handler.send_response(206 if rng else 200)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", str(length))
            if download:
                handler.send_header("Content-Disposition",
                                    _disposition(download_name(movie)))
            if rng:
                handler.send_header("Content-Range",
                                    "bytes %d-%d/%d" % (start, end, size))
            handler.end_headers()
            if head_only:
                return
            pos = start
            # 1) from the cached prefix
            if pos < have:
                binp, _ = _paths(mid)
                with open(binp, "rb") as f:
                    f.seek(pos)
                    while pos <= end and pos < have:
                        buf = f.read(min(CHUNK, have - pos, end - pos + 1))
                        if not buf:
                            break
                        handler.wfile.write(buf)
                        pos += len(buf)
                        _last_activity = time.time()
            # 2) the rest from the provider
            if pos <= end:
                with _open_upstream(movie["url"], pos, end) as r:
                    while pos <= end:
                        buf = r.read(min(CHUNK, end - pos + 1))
                        if not buf:
                            break
                        handler.wfile.write(buf)
                        pos += len(buf)
                        _last_activity = time.time()
            return

        # ---- no cache info: pass the provider's answer through -----------
        start = rng[0] if rng else 0
        end = rng[1] if rng else None
        with _open_upstream(movie["url"], start, end) as r:
            handler.send_response(206 if rng else 200)
            handler.send_header("Content-Type", ctype)
            handler.send_header("Accept-Ranges", "bytes")
            if download:
                handler.send_header("Content-Disposition",
                                    _disposition(download_name(movie)))
            for h in ("Content-Length", "Content-Range"):
                if r.headers.get(h):
                    handler.send_header(h, r.headers[h])
            handler.end_headers()
            if head_only:
                return
            while True:
                buf = r.read(CHUNK)
                if not buf:
                    break
                handler.wfile.write(buf)
                _last_activity = time.time()
    except (BrokenPipeError, ConnectionResetError):
        log("  client closed")
    except Exception as e:
        log("  error: %r" % (e,))
        try:
            handler.send_error(502, "Provider unavailable: %s" % e)
        except Exception:
            pass
    finally:
        with _lock:
            _active -= 1
            _last_activity = time.time()


# ---------------------------------------------------------------------------
#  Live TV in the browser: the provider's MPEG-TS stream is repackaged by
#  ffmpeg into HLS (video copied, audio converted to AAC) in /tmp, which
#  Safari plays natively. One session at a time; it stops ~30s after the
#  player stops asking for segments.
# ---------------------------------------------------------------------------
import re as _re
import shutil as _shutil
import subprocess as _sp

HLS_ROOT = "/tmp/iptv-hls"
HLS_IDLE = 30
_hls = {"id": None, "gen": 0, "alive": False, "last": 0.0, "state": "",
        "proc": None, "dir": None}
_hls_lock = threading.Lock()
_HLS_FILE = _re.compile(r"^(index\.m3u8|init\.mp4|seg\d+\.m4s)$")


def hls_stop():
    with _hls_lock:
        if not _hls["alive"]:
            return
        _hls["gen"] += 1
        proc = _hls["proc"]
        _hls.update(alive=False, proc=None, state="stopped")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def hls_state():
    return {"id": _hls["id"], "alive": _hls["alive"], "state": _hls["state"]}


def _hls_start(item):
    with _hls_lock:
        if _hls["alive"] and _hls["id"] == item["id"]:
            _hls["last"] = time.time()
            return
    hls_stop()
    d = os.path.join(HLS_ROOT, item["id"])
    _shutil.rmtree(HLS_ROOT, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    with _hls_lock:
        _hls["gen"] += 1
        gen = _hls["gen"]
        _hls.update(id=item["id"], alive=True, last=time.time(),
                    state="starting", dir=d, proc=None)
    log("live start %s" % item["name"])
    if _notify:
        try:
            _notify()
        except Exception:
            pass
    threading.Thread(target=_hls_run, args=(item, d, gen),
                     daemon=True).start()


def _hls_cmd(url, d):
    return ["ffmpeg", "-nostdin", "-loglevel", "error",
            "-user_agent", UA, "-rw_timeout", "15000000",
            "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", url,
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-ac", "2", "-b:a", "160k",
            "-f", "hls", "-hls_time", "4", "-hls_list_size", "8",
            "-hls_flags", "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", os.path.join(d, "seg%05d.m4s"),
            os.path.join(d, "index.m3u8")]


def _hls_run(item, d, gen):
    global _last_activity
    index = os.path.join(d, "index.m3u8")
    deadline = time.time() + RETRY_SECONDS
    try:
        while _hls["gen"] == gen:
            if time.time() - _hls["last"] > HLS_IDLE:
                break
            errf = open(os.path.join(d, "ffmpeg.log"), "ab")
            proc = _sp.Popen(_hls_cmd(item["url"], d), stdout=_sp.DEVNULL,
                             stderr=errf)
            with _hls_lock:
                if _hls["gen"] != gen:
                    proc.kill()
                    break
                _hls["proc"] = proc
            while proc.poll() is None:
                if _hls["gen"] != gen or \
                        time.time() - _hls["last"] > HLS_IDLE:
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except Exception:
                        proc.kill()
                    break
                if os.path.exists(index):
                    _hls["state"] = "playing"
                _last_activity = time.time()
                time.sleep(1)
            errf.close()
            if _hls["gen"] != gen or time.time() - _hls["last"] > HLS_IDLE:
                break
            # ffmpeg ended by itself: provider refused (still counting the
            # TV's connection) or the stream dropped -> retry for a while
            if _hls["state"] == "playing":
                deadline = time.time() + RETRY_SECONDS
            if time.time() > deadline:
                _hls["state"] = "failed"
                log("live failed %s" % item["name"])
                return
            _hls["state"] = "waiting"
            time.sleep(3)
    finally:
        with _hls_lock:
            if _hls["gen"] == gen:
                if _hls["state"] != "failed":
                    _hls["state"] = "stopped"
                _hls.update(alive=False, proc=None)
                _last_activity = time.time()
        log("live stop %s" % item["name"])


def hls_serve(handler, item_id, fname):
    """Serve /hls/<id>/<fname>. Requesting the playlist starts the session."""
    if not _HLS_FILE.match(fname):
        handler.send_error(404)
        return
    item = resolve(item_id, "live")
    if not item:
        handler.send_error(404)
        return
    if fname == "index.m3u8":
        _hls_start(item)
    with _hls_lock:
        if _hls["id"] == item_id:
            _hls["last"] = time.time()
    d = os.path.join(HLS_ROOT, item_id)
    path = os.path.join(d, fname)
    # wait for ffmpeg to produce the file (provider may make us wait)
    end = time.time() + (100 if fname == "index.m3u8" else 15)
    while not os.path.exists(path) and time.time() < end:
        if _hls["id"] != item_id or _hls["state"] == "failed":
            break
        time.sleep(0.5)
        with _hls_lock:
            if _hls["id"] == item_id:
                _hls["last"] = time.time()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        handler.send_error(503, "Live stream not available right now")
        return
    ctype = ("application/vnd.apple.mpegurl" if fname.endswith(".m3u8")
             else "video/mp4")
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass
