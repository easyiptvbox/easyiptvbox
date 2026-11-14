"""Persistent movie state: resume positions + preferred audio/subtitle langs.

Stored as JSON in the cache directory so it survives restarts/reboots.
"""
import os
import json
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
import iptv_profiles


def _state_path():
    return os.path.join(iptv_profiles.state_dir(), "movie_state.json")

_lock = threading.Lock()


def _load():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _state_path())
    except Exception:
        pass


class MovieState:
    """Resume positions keyed by movie URL, plus global language prefs."""

    def __init__(self):
        self._path = None
        self._mt = 0
        self._sync_t = 0.0
        self._sync(force=True)

    def _sync(self, force=False):
        """(Re)load the saved state when the file changed: the web app and
        VLC downloads save positions too, and the TV must see them."""
        now = time.time()
        if not force and now - self._sync_t < 1.0:
            return
        self._sync_t = now
        p = _state_path()
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0
        if p == self._path and mt == self._mt:
            return
        with _lock:
            self._data = _load()
        self._path, self._mt = p, mt
        self.__dict__.pop("_wset", None)
        self._data.setdefault("positions", {})   # url -> {pos, duration}
        self._data.setdefault("watched", [])     # urls watched > 75%
        self._data.setdefault("prefs", {})        # {audio_lang, sub_lang, subs_on}

    def _persist(self):
        with _lock:
            _save(self._data)
        try:
            self._mt = os.path.getmtime(_state_path())
        except OSError:
            pass

    # -- resume positions ------------------------------------------------
    def get_position(self, url):
        self._sync()
        rec = self._data["positions"].get(url)
        if not rec:
            return None
        return rec  # {"pos": seconds, "duration": seconds}

    WATCHED_RATIO = 0.75

    def is_watched(self, url):
        self._sync()
        return url in self._watched_set()

    def _watched_set(self):
        w = self.__dict__.get("_wset")
        if w is None:
            w = self.__dict__["_wset"] = set(self._data.get("watched", []))
        return w

    def mark_watched(self, url):
        self._sync(force=True)
        if url and url not in self._watched_set():
            self._watched_set().add(url)
            self._data["watched"] = sorted(self._watched_set())
            self._persist()

    def save_position(self, url, pos, duration=0):
        self._sync(force=True)
        if duration and pos and pos >= duration * self.WATCHED_RATIO:
            self.mark_watched(url)
        # If we're within the last 90s (or before 15s), treat as "finished"
        # / "not really started" and forget it, so we don't nag to resume.
        if duration and pos >= duration - 90:
            self._data["positions"].pop(url, None)
        elif pos is not None and pos > 15:
            self._data["positions"][url] = {
                "pos": float(pos), "duration": float(duration or 0)}
        self._persist()

    def clear_position(self, url):
        self._sync(force=True)
        self._data["positions"].pop(url, None)
        self._persist()

    # -- language preferences -------------------------------------------
    @property
    def audio_langs(self):
        """Audio languages chosen before, most recent first."""
        p = self._data["prefs"]
        lst = [l for l in (p.get("audio_langs") or []) if l]
        old = p.get("audio_lang")
        if old and old not in lst:
            lst.append(old)        # migrate the old single preference
        return lst

    @property
    def audio_lang(self):
        """Priority list for mpv's --alang (e.g. "nor,eng"), or None."""
        lst = self.audio_langs
        return ",".join(lst) if lst else None

    @property
    def sub_langs(self):
        """Subtitle languages chosen before, most recent first."""
        p = self._data["prefs"]
        lst = [l for l in (p.get("sub_langs") or []) if l]
        old = p.get("sub_lang")
        if old and old not in lst:
            lst.append(old)        # migrate the old single preference
        return lst

    @property
    def sub_lang(self):
        """Priority list for mpv's --slang (e.g. "nor,eng"), or None."""
        lst = self.sub_langs
        return ",".join(lst) if lst else None

    @property
    def subs_on(self):
        """Subtitles are used whenever a preferred language is saved. The old
        saved on/off flag is ignored: choosing "Off" now only affects the
        video being watched."""
        return bool(self.sub_langs)

    def set_audio_lang(self, lang):
        """A chosen audio language moves to the top of the history (max 8)."""
        if not lang:
            return
        self._sync(force=True)
        p = self._data["prefs"]
        lst = [l for l in self.audio_langs if l != lang]
        p["audio_langs"] = ([lang] + lst)[:8]
        p["audio_lang"] = lang
        self._persist()

    def set_sub_pref(self, lang, on):
        """A chosen subtitle language moves to the top of the history (max 8
        kept). Choosing Off (on=False) changes nothing that is saved."""
        if not (on and lang):
            return
        self._sync(force=True)
        p = self._data["prefs"]
        lst = [l for l in self.sub_langs if l != lang]
        p["sub_langs"] = ([lang] + lst)[:8]
        p["sub_lang"] = lang
        p["subs_on"] = True
        self._persist()
