"""Poster (cover image) cache for Movies and Series.

Posters come from the URLs the IPTV provider already supplies. They are
downloaded in a background thread, shrunk to a small JPEG and cached on disk
so the menus stay fast and the SD card doesn't fill up (size-capped).
"""
import io
import os
import time
import hashlib
import threading
import queue
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
POSTER_DIR = os.path.join(CACHE_DIR, "posters")

POSTER_W, POSTER_H = 240, 360
TIMEOUT = 6
MAX_BYTES = 6 * 1024 * 1024         # refuse huge downloads
MAX_FILES = 5000
MAX_TOTAL = 300 * 1024 * 1024
RETRY_AFTER = 1800                  # seconds before retrying a failed URL
UA = "Mozilla/5.0 (X11; Linux aarch64) EasyIPTVBox"

version = 0                         # bumped whenever a new poster lands
_q = queue.Queue()
_pending = set()
_failed = {}                        # url -> time of failure
_lock = threading.Lock()
_started = False


def _ok_url(url):
    return isinstance(url, str) and url.lower().startswith(("http://", "https://"))


def poster_path(url):
    return os.path.join(POSTER_DIR,
                        hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")


def has_poster(url):
    try:
        return os.path.getsize(poster_path(url)) > 0
    except OSError:
        return False


def request(urls):
    """Queue posters for download (no-op for cached/failed/queued ones)."""
    global _started
    for u in urls:
        if not _ok_url(u) or has_poster(u):
            continue
        with _lock:
            if u in _pending:
                continue
            ft = _failed.get(u)
            if ft and time.time() - ft < RETRY_AFTER:
                continue
            _pending.add(u)
        _q.put(u)
    if not _started:
        _started = True
        threading.Thread(target=_worker, daemon=True).start()


def _fetch(url):
    from PIL import Image
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("too big")
    im = Image.open(io.BytesIO(data))
    im.draft("RGB", (POSTER_W * 2, POSTER_H * 2))
    im = im.convert("RGB")
    im.thumbnail((POSTER_W, POSTER_H))
    os.makedirs(POSTER_DIR, exist_ok=True)
    p = poster_path(url)
    tmp = p + ".tmp"
    im.save(tmp, "JPEG", quality=82)
    os.replace(tmp, p)


_sem = threading.BoundedSemaphore(4)


def get_now(url):
    """Path of the poster, downloading it now if needed (None on failure).
    Used by the web app's image endpoint."""
    global version
    if not _ok_url(url):
        return None
    if has_poster(url):
        return poster_path(url)
    ft = _failed.get(url)
    if ft and time.time() - ft < RETRY_AFTER:
        return None
    with _sem:
        if has_poster(url):
            return poster_path(url)
        try:
            _fetch(url)
            version += 1
            return poster_path(url)
        except Exception:
            _failed[url] = time.time()
            return None


def _worker():
    global version
    n = 0
    while True:
        u = _q.get()
        try:
            _fetch(u)
            version += 1
        except Exception:
            _failed[u] = time.time()
        finally:
            with _lock:
                _pending.discard(u)
        n += 1
        if n % 100 == 0:
            _prune()


def _prune():
    """Delete the oldest posters when the cache grows too big."""
    try:
        files = []
        for f in os.listdir(POSTER_DIR):
            p = os.path.join(POSTER_DIR, f)
            st = os.stat(p)
            files.append((st.st_mtime, st.st_size, p))
        total = sum(s for _m, s, _p in files)
        files.sort()
        while files and (len(files) > MAX_FILES or total > MAX_TOTAL):
            _m, s, p = files.pop(0)
            os.remove(p)
            total -= s
    except OSError:
        pass
