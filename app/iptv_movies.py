"""Persistent movie state: resume positions + preferred audio/subtitle langs.

Stored as JSON in the cache directory so it survives restarts/reboots.
"""
import os
import json
import threading

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
STATE_PATH = os.path.join(CACHE_DIR, "movie_state.json")

_lock = threading.Lock()


def _load():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


class MovieState:
    """Resume positions keyed by movie URL, plus global language prefs."""

    def __init__(self):
        with _lock:
            self._data = _load()
        self._data.setdefault("positions", {})   # url -> {pos, duration}
        self._data.setdefault("prefs", {})        # {audio_lang, sub_lang, subs_on}

    # -- resume positions ------------------------------------------------
    def get_position(self, url):
        rec = self._data["positions"].get(url)
        if not rec:
            return None
        return rec  # {"pos": seconds, "duration": seconds}

    def save_position(self, url, pos, duration=0):
        # If we're within the last 90s (or before 15s), treat as "finished"
        # / "not really started" and forget it, so we don't nag to resume.
        if duration and pos >= duration - 90:
            self._data["positions"].pop(url, None)
        elif pos is not None and pos > 15:
            self._data["positions"][url] = {
                "pos": float(pos), "duration": float(duration or 0)}
        with _lock:
            _save(self._data)

    def clear_position(self, url):
        self._data["positions"].pop(url, None)
        with _lock:
            _save(self._data)

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
        return bool(self._data["prefs"].get("subs_on", False))

    def set_audio_lang(self, lang):
        """A chosen audio language moves to the top of the history (max 8)."""
        if not lang:
            return
        p = self._data["prefs"]
        lst = [l for l in self.audio_langs if l != lang]
        p["audio_langs"] = ([lang] + lst)[:8]
        p["audio_lang"] = lang
        with _lock:
            _save(self._data)

    def set_sub_pref(self, lang, on):
        """Turn subtitles on/off; a chosen language moves to the top of the
        history (max 8 kept)."""
        p = self._data["prefs"]
        if on and lang:
            lst = [l for l in self.sub_langs if l != lang]
            p["sub_langs"] = ([lang] + lst)[:8]
            p["sub_lang"] = lang
        p["subs_on"] = bool(on)
        with _lock:
            _save(self._data)
