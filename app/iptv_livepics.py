"""User-uploaded pictures for Live categories / channels, plus the generated
colored fallback tiles used when nothing was uploaded.

Pictures are stored resized (16:9 JPEG) in cache/livepics/<key>.jpg.
A key is "k" + hash(category name) or "c" + hash(stream url) (12 hex chars).
"""
import io
import os
import re
import hashlib
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
PIC_DIR = os.path.join(CACHE_DIR, "livepics")

PIC_W, PIC_H = 480, 270
MAX_UPLOAD = 6 * 1024 * 1024
MAX_FILES = 3000
KEY_RE = re.compile(r"^[kc][0-9a-f]{12}$")
_lock = threading.Lock()

# Soft, distinguishable tile colors (dark enough for white text).
PALETTE = [
    (176, 58, 46), (211, 84, 0), (183, 149, 11), (35, 155, 86),
    (26, 138, 142), (36, 113, 163), (113, 75, 160), (160, 64, 130),
    (84, 110, 122), (121, 85, 72), (60, 120, 60), (150, 90, 40),
]


def _h(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def cat_key(name):
    return "k" + _h(str(name).strip())[:12]


def chan_key(url):
    # same id the web catalog uses for a stream (sha1(url)[:12])
    return "c" + _h(str(url))[:12]


def color_for(name):
    return PALETTE[int(_h(str(name).casefold())[:6], 16) % len(PALETTE)]


def path_for(key):
    if not KEY_RE.match(key or ""):
        return None
    return os.path.join(PIC_DIR, key + ".jpg")


def find(key):
    """Path of the uploaded picture for this key, or None."""
    p = path_for(key)
    try:
        if p and os.path.getsize(p) > 0:
            return p
    except OSError:
        pass
    return None


def keys():
    try:
        return sorted(f[:-4] for f in os.listdir(PIC_DIR)
                      if f.endswith(".jpg") and KEY_RE.match(f[:-4]))
    except OSError:
        return []


def save(key, data):
    """Validate, resize and store an uploaded image. Raises ValueError."""
    p = path_for(key)
    if not p:
        raise ValueError("bad key")
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Picture must be smaller than 6 MB")
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (PIC_W * 2, PIC_H * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise ValueError("That file is not a usable picture (use JPG or PNG)")
    im = ImageOps.fit(im, (PIC_W, PIC_H), Image.LANCZOS)
    with _lock:
        os.makedirs(PIC_DIR, exist_ok=True)
        if not os.path.exists(p) and len(keys()) >= MAX_FILES:
            raise ValueError("Too many pictures; remove some first")
        tmp = p + ".tmp"
        im.save(tmp, "JPEG", quality=85)
        os.replace(tmp, p)


def delete(key):
    p = path_for(key)
    if p:
        try:
            os.remove(p)
        except OSError:
            pass


def dir_stamp():
    """Changes whenever a picture is added, replaced or removed."""
    try:
        return os.stat(PIC_DIR).st_mtime_ns
    except OSError:
        return 0
