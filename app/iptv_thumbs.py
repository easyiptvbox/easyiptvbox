"""Grabs a thumbnail frame from each live channel using ffmpeg.

Thumbnails are captured in a background thread (so the menu stays responsive)
and cached to disk keyed by a hash of the stream URL, so a category only needs
to be scanned once.
"""
import os
import time
import hashlib
import threading
import subprocess

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
THUMB_DIR = os.path.join(CACHE_DIR, "thumbs")

THUMB_WIDTH = 320
GRAB_TIMEOUT = 4           # seconds max per channel (skip slow/dead ones fast)
MAX_AGE = 3600             # re-grab thumbnails older than one hour
BLACK_YAVG = 20            # frames darker than this average luma are "black"
import re
_YAVG_RE = re.compile(rb"YAVG=([0-9.]+)")


_procs = set()                 # running ffmpeg grabs (killed by ThumbScanner.stop)
_procs_lock = threading.Lock()


def _kill_all():
    with _procs_lock:
        procs = list(_procs)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass


def _key(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def thumb_path(url):
    return os.path.join(THUMB_DIR, _key(url) + ".jpg")


def has_thumb(url):
    """True if a thumbnail image exists on disk (regardless of age)."""
    p = thumb_path(url)
    try:
        return os.path.exists(p) and os.path.getsize(p) > 0
    except OSError:
        return False


def thumb_fresh(url, max_age=MAX_AGE):
    """True if a thumbnail exists AND is younger than max_age seconds."""
    p = thumb_path(url)
    try:
        if not (os.path.exists(p) and os.path.getsize(p) > 0):
            return False
        return (time.time() - os.path.getmtime(p)) < max_age
    except OSError:
        return False


def _grab_one(url):
    """Capture a single frame from a stream into the thumb cache.

    Returns True only if we got a real (non-black) frame. A channel that
    returns nothing, errors, or a black/near-black frame produces NO cached
    file (any previous one is removed), so it won't show in the picture grid.
    It will still be tried again on the next scan.
    """
    os.makedirs(THUMB_DIR, exist_ok=True)
    out = thumb_path(url)
    tmp = out + ".tmp.jpg"
    cmd = [
        "ffmpeg", "-y",
        "-nostdin",
        "-loglevel", "error",
        "-user_agent", "IPTVPlayer/1.0",
        "-rw_timeout", "3000000",     # 3s I/O timeout (microseconds)
        "-ss", "1",                    # skip 1s (avoid black lead-in)
        "-i", url,
        "-frames:v", "1",
        # scale + measure average brightness (YAVG) in one pass; the
        # metadata is written to stdout (file=-) so -loglevel error is fine.
        "-vf", ("scale=%d:-1,signalstats,"
                "metadata=print:key=lavfi.signalstats.YAVG:file=-" % THUMB_WIDTH),
        "-q:v", "5",
        "-update", "1",
        tmp,
    ]
    ok = False
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        with _procs_lock:
            _procs.add(p)
        try:
            try:
                out_b, _e = p.communicate(timeout=GRAB_TIMEOUT)
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()
                raise
        finally:
            with _procs_lock:
                _procs.discard(p)
        r = subprocess.CompletedProcess(cmd, p.returncode, out_b, b"")
        got_file = (r.returncode == 0 and os.path.exists(tmp)
                    and os.path.getsize(tmp) > 0)
        if got_file and not _is_black(r.stdout):
            os.replace(tmp, out)
            ok = True
    except Exception:
        ok = False
    # Clean up: remove temp and, if this channel is now invalid, drop any
    # stale cached thumbnail so it disappears from the grid.
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    if not ok:
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
    return ok


def _is_black(stderr_bytes):
    """Return True if the captured frame's average brightness is near black."""
    try:
        vals = [float(m) for m in _YAVG_RE.findall(stderr_bytes or b"")]
    except Exception:
        return False
    if not vals:
        return False   # couldn't measure -> assume it's fine
    return max(vals) < BLACK_YAVG


class ThumbScanner:
    """Background scanner for a list of channel URLs."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._gen = 0
        self.total = 0
        self.done = 0
        self.active = False

    def is_scanning(self):
        return self.active

    def start(self, urls, force=False):
        """Scan the given URLs. Skip ones with a fresh (< 1 hour old)
        thumbnail unless force=True."""
        # Make sure any previous scan is fully stopped before starting a new
        # one, otherwise the old thread could keep running in the background.
        self.stop()
        todo = [u for u in urls if u and (force or not thumb_fresh(u))]
        self.total = len(todo)
        self.done = 0
        self._stop.clear()
        if not todo:
            return
        self.active = True
        self._gen += 1
        gen = self._gen

        def worker():
            for u in todo:
                if self._stop.is_set() or gen != self._gen:
                    break
                _grab_one(u)
                # re-check after the (slow) grab: a stop()/new scan may have
                # happened while we were fetching, so don't touch shared state.
                if gen != self._gen:
                    break
                self.done += 1
            # only clear active if we're still the current generation
            if gen == self._gen:
                self.active = False

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop(self):
        # Signal the running worker to quit and bump the generation so any
        # in-flight thread becomes a no-op (it won't touch progress/active or
        # interfere with the next scan). We deliberately do NOT join here: a
        # grab can take a few seconds and blocking the UI thread would freeze
        # the interface. The daemon thread finishes its current grab and exits.
        self._stop.set()
        self.active = False
        self._gen += 1
        self._thread = None
        _kill_all()      # free the provider's single connection right away
