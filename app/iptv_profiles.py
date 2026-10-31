"""User profiles.

The Default profile lives where the box always kept its data (config.yaml and
cache/), so nothing has to be migrated. Extra profiles live in
profiles/<id>/ :

    config.yaml      theme, text size and the live / Movies / Series categories
    state/           watch progress, last section, saved searches, live pictures
    avatar.jpg       optional picture shown next to the name

Provider login, TV guide, audio sync, remotes, Wi-Fi etc. are shared by the
whole box (they stay in the Default config.yaml).

A PIN is a soft lock (4-6 digits, salted hash). The web app has no login, so
it only keeps people out of a profile, it does not protect secrets.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("IPTV_PROFILES", os.path.join(APP_DIR, "profiles"))
BOX_CONFIG = os.environ.get("IPTV_CONFIG", os.path.join(APP_DIR, "config.yaml"))
BOX_CACHE = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
INDEX = os.path.join(ROOT, "index.json")

DEFAULT = "default"
ID_RE = re.compile(r"^[0-9a-f]{8}$")
PIN_RE = re.compile(r"^[0-9]{4,6}$")
PROFILE_SETTINGS = ("theme", "text_size")     # settings kept per profile
AVATAR_PX = 160
MAX_PROFILES = 12

ACTIVE = DEFAULT              # the profile the TV is showing
_local = threading.local()    # profile of the current web request
_lock = threading.RLock()
_tokens = {}                  # unlock token -> profile id
_fails = {}                   # profile id -> (count, locked until)
_cache = {"mtime": None, "data": None}


# ---------------------------------------------------------------- current
def current():
    """Profile for this thread: the web request's, else the TV's."""
    return getattr(_local, "pid", None) or ACTIVE


def set_request(pid):
    _local.pid = pid


def clear_request():
    _local.pid = None


# ------------------------------------------------------------------ paths
def _dir(pid):
    return os.path.join(ROOT, pid)


def config_path(pid=None):
    pid = pid or current()
    return BOX_CONFIG if pid == DEFAULT else os.path.join(_dir(pid), "config.yaml")


def state_dir(pid=None):
    pid = pid or current()
    return BOX_CACHE if pid == DEFAULT else os.path.join(_dir(pid), "state")


def pic_dir(pid=None):
    return os.path.join(state_dir(pid), "livepics")


def avatar_path(pid):
    return os.path.join(_dir(pid), "avatar.jpg")


def has_avatar(pid):
    try:
        return os.path.getsize(avatar_path(pid)) > 0
    except OSError:
        return False


# ------------------------------------------------------------------ index
def _read_index():
    try:
        with open(INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            return data
    except Exception:
        pass
    return {"profiles": []}


def _write_index(data):
    os.makedirs(ROOT, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, INDEX)
    _cache["mtime"] = None


def _all():
    """Index with the Default profile always first (never stored)."""
    try:
        mt = os.path.getmtime(INDEX)
    except OSError:
        mt = 0
    if _cache["mtime"] == mt and _cache["data"] is not None:
        return _cache["data"]
    extra = [p for p in _read_index()["profiles"]
             if isinstance(p, dict) and ID_RE.match(str(p.get("id", "")))]
    data = [{"id": DEFAULT, "name": "Default"}] + extra
    _cache.update(mtime=mt, data=data)
    return data


def ids():
    return [p["id"] for p in _all()]


def exists(pid):
    return pid in ids()


def name_of(pid):
    for p in _all():
        if p["id"] == pid:
            return p["name"]
    return "Default"


def has_pin(pid):
    if pid == DEFAULT:
        return False
    for p in _all():
        if p["id"] == pid:
            return bool(p.get("pin"))
    return False


def list_profiles():
    """Public view (no hashes)."""
    return [{"id": p["id"], "name": p["name"], "has_pin": has_pin(p["id"]),
             "has_pic": has_avatar(p["id"]), "default": p["id"] == DEFAULT}
            for p in _all()]


def extra_count():
    return len(_all()) - 1


# -------------------------------------------------------------------- pins
def _hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt),
                               60000).hex()


def set_pin(pid, pin):
    """Set (or with '' clear) a profile's PIN."""
    if pid == DEFAULT:
        raise ValueError("The Default profile cannot have a PIN")
    pin = str(pin or "").strip()
    if pin and not PIN_RE.match(pin):
        raise ValueError("The PIN must be 4 to 6 digits")
    with _lock:
        data = _read_index()
        for p in data["profiles"]:
            if p.get("id") == pid:
                if pin:
                    salt = secrets.token_hex(8)
                    p["salt"], p["pin"] = salt, _hash(pin, salt)
                else:
                    p.pop("salt", None)
                    p.pop("pin", None)
                _write_index(data)
                _tokens_drop(pid)
                return
    raise ValueError("No such profile")


def check_pin(pid, pin):
    """True if the PIN is right. Raises PermissionError while locked out."""
    with _lock:
        cnt, until = _fails.get(pid, (0, 0))
        if until > time.time():
            raise PermissionError("Too many wrong tries. Wait %d seconds." %
                                  int(until - time.time() + 1))
        for p in _all():
            if p["id"] == pid:
                if not p.get("pin"):
                    return True
                ok = hmac.compare_digest(
                    _hash(str(pin or ""), p.get("salt", "00")), p["pin"])
                if ok:
                    _fails.pop(pid, None)
                else:
                    cnt += 1
                    _fails[pid] = ((0, time.time() + 30) if cnt >= 5
                                   else (cnt, 0))
                return ok
    return False


def unlock(pid, pin):
    """PIN check that returns a token for the web app (or None)."""
    if not check_pin(pid, pin):
        return None
    tok = secrets.token_hex(16)
    with _lock:
        _tokens[tok] = pid
    return tok


def token_ok(pid, token):
    if pid == DEFAULT or not has_pin(pid):
        return True
    return bool(token) and _tokens.get(token) == pid


def _tokens_drop(pid):
    with _lock:
        for t in [t for t, p in _tokens.items() if p == pid]:
            _tokens.pop(t, None)


# ------------------------------------------------------------ config files
def _yaml():
    import yaml
    return yaml


def read_config_data(pid=None):
    """The config as the web app / TV sees it: box-wide parts from the
    Default config, plus this profile's settings and categories."""
    y = _yaml()
    pid = pid or current()
    with open(BOX_CONFIG, "r", encoding="utf-8") as f:
        data = y.safe_load(f) or {}
    if pid == DEFAULT:
        return data
    try:
        with open(config_path(pid), "r", encoding="utf-8") as f:
            mine = y.safe_load(f) or {}
    except Exception:
        mine = {}
    data = dict(data)
    st = dict(data.get("settings") or {})
    for k in PROFILE_SETTINGS:
        if k in (mine.get("settings") or {}):
            st[k] = mine["settings"][k]
    data["settings"] = st
    data["playlists"] = mine.get("playlists") or []
    return data


def profile_text(full_text):
    """Reduce a full config text to what a profile stores."""
    y = _yaml()
    d = y.safe_load(full_text) or {}
    st = {k: v for k, v in (d.get("settings") or {}).items()
          if k in PROFILE_SETTINGS}
    out = {"settings": st, "playlists": d.get("playlists") or []}
    return ("# Profile configuration (managed by the web app)\n" +
            y.safe_dump(out, allow_unicode=True, sort_keys=False))


def write_config_text(text, pid=None):
    """Atomically store a config text for a profile (text is a full config
    for Default, anything for the others: it is reduced first)."""
    pid = pid or current()
    path = config_path(pid)
    if pid != DEFAULT:
        text = profile_text(text)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ----------------------------------------------------------- management
def _clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name or len(name) > 24:
        raise ValueError("Give the profile a name of 1 to 24 characters")
    return name


def _name_free(name, ignore=None):
    low = name.casefold()
    for p in _all():
        if p["id"] != ignore and p["name"].casefold() == low:
            raise ValueError("There is already a profile with that name")


def create(name, pin="", copy_from=None):
    """New profile: empty, or a copy of another (settings, categories and
    live pictures; watch history starts clean). Returns its id."""
    name = _clean_name(name)
    pin = str(pin or "").strip()
    if pin and not PIN_RE.match(pin):
        raise ValueError("The PIN must be 4 to 6 digits")
    with _lock:
        if extra_count() >= MAX_PROFILES:
            raise ValueError("At most %d extra profiles" % MAX_PROFILES)
        _name_free(name)
        if copy_from is not None and copy_from != "" and not exists(copy_from):
            raise ValueError("The profile to copy does not exist")
        pid = secrets.token_hex(4)
        d = _dir(pid)
        os.makedirs(os.path.join(d, "state"), exist_ok=True)
        try:
            src = read_config_data(copy_from or DEFAULT)
            if copy_from:
                text = {"settings": {k: v for k, v in
                                     (src.get("settings") or {}).items()
                                     if k in PROFILE_SETTINGS},
                        "playlists": src.get("playlists") or []}
            else:       # empty: no categories, same look as the Default
                text = {"settings": {k: v for k, v in
                                     (src.get("settings") or {}).items()
                                     if k in PROFILE_SETTINGS},
                        "playlists": []}
            with open(os.path.join(d, "config.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("# Profile configuration (managed by the web app)\n" +
                        _yaml().safe_dump(text, allow_unicode=True,
                                          sort_keys=False))
            if copy_from:
                sp = pic_dir(copy_from)
                if os.path.isdir(sp):
                    shutil.copytree(sp, pic_dir(pid))
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        data = _read_index()
        data["profiles"].append({"id": pid, "name": name})
        _write_index(data)
    if pin:
        set_pin(pid, pin)
    return pid


def rename(pid, name):
    if pid == DEFAULT:
        raise ValueError("The Default profile cannot be renamed")
    name = _clean_name(name)
    with _lock:
        _name_free(name, ignore=pid)
        data = _read_index()
        for p in data["profiles"]:
            if p.get("id") == pid:
                p["name"] = name
                _write_index(data)
                return
    raise ValueError("No such profile")


def delete(pid):
    if pid == DEFAULT:
        raise ValueError("The Default profile cannot be deleted")
    global ACTIVE
    with _lock:
        data = _read_index()
        data["profiles"] = [p for p in data["profiles"] if p.get("id") != pid]
        _write_index(data)
        shutil.rmtree(_dir(pid), ignore_errors=True)
        _tokens_drop(pid)
        if ACTIVE == pid:
            ACTIVE = DEFAULT


def save_avatar(pid, data):
    """Store an uploaded picture (square crop, small JPEG)."""
    if not exists(pid):
        raise ValueError("No such profile")
    if not data or len(data) > 6 * 1024 * 1024:
        raise ValueError("Picture must be smaller than 6 MB")
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (AVATAR_PX * 2, AVATAR_PX * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        raise ValueError("That file is not a usable picture (use JPG or PNG)")
    im = ImageOps.fit(im, (AVATAR_PX, AVATAR_PX), Image.LANCZOS)
    os.makedirs(_dir(pid), exist_ok=True)
    tmp = avatar_path(pid) + ".tmp"
    im.save(tmp, "JPEG", quality=88)
    os.replace(tmp, avatar_path(pid))


def delete_avatar(pid):
    try:
        os.remove(avatar_path(pid))
    except OSError:
        pass


def initial_color(name):
    h = int(hashlib.sha1(name.casefold().encode()).hexdigest()[:6], 16)
    pal = [(176, 58, 46), (211, 84, 0), (183, 149, 11), (35, 155, 86),
           (26, 138, 142), (36, 113, 163), (113, 75, 160), (160, 64, 130)]
    return pal[h % len(pal)]
