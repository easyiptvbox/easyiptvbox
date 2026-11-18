"""Technical details of a movie file for the fact sheet: picture quality,
audio languages and subtitle languages.

The provider does not list these, so the file's header is read with ffprobe
(usually 0.2 to 1.5 seconds, a few old AVI files take 2 to 15 seconds). This
uses the provider's single connection for that moment, so callers only probe
when nothing is playing. Successful results are cached on disk; failures are
not (they are tried again next time).
"""
import hashlib
import json
import os
import subprocess
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
_DIR = os.path.join(CACHE_DIR, "movietech")
TIMEOUT = 25
_lock = threading.Lock()
_running = set()                      # urls being probed right now

# canonical (ISO 639-2/T) code and English name; every alias maps to it
_LANGS = [
    ("eng", "English", "en"), ("nor", "Norwegian", "no", "nob", "nno", "nb", "nn"),
    ("swe", "Swedish", "sv"), ("dan", "Danish", "da"),
    ("fin", "Finnish", "fi"), ("isl", "Icelandic", "is", "ice"),
    ("deu", "German", "de", "ger"), ("fra", "French", "fr", "fre"),
    ("spa", "Spanish", "es"), ("ita", "Italian", "it"),
    ("por", "Portuguese", "pt"), ("nld", "Dutch", "nl", "dut"),
    ("pol", "Polish", "pl"), ("rus", "Russian", "ru"),
    ("ukr", "Ukrainian", "uk"), ("ces", "Czech", "cs", "cze"),
    ("slk", "Slovak", "sk", "slo"), ("hun", "Hungarian", "hu"),
    ("ron", "Romanian", "ro", "rum"), ("bul", "Bulgarian", "bg"),
    ("ell", "Greek", "el", "gre"), ("tur", "Turkish", "tr"),
    ("ara", "Arabic", "ar"), ("heb", "Hebrew", "he"),
    ("fas", "Persian", "fa", "per"), ("hin", "Hindi", "hi"),
    ("zho", "Chinese", "zh", "chi"), ("jpn", "Japanese", "ja"),
    ("kor", "Korean", "ko"), ("tha", "Thai", "th"),
    ("vie", "Vietnamese", "vi"), ("ind", "Indonesian", "id"),
    ("hrv", "Croatian", "hr"), ("srp", "Serbian", "sr"),
    ("slv", "Slovenian", "sl"), ("est", "Estonian", "et"),
    ("lav", "Latvian", "lv"), ("lit", "Lithuanian", "lt"),
    ("cat", "Catalan", "ca"), ("sqi", "Albanian", "sq", "alb"),
    ("bos", "Bosnian", "bs"), ("mkd", "Macedonian", "mk", "mac"),
]
_ALIAS = {}
_NAME = {}
for _row in _LANGS:
    _NAME[_row[0]] = _row[1]
    for _a in (_row[0],) + tuple(_row[2:]):
        _ALIAS[_a] = _row[0]


def canon(code):
    c = str(code or "").strip().lower().replace("_", "-").split("-")[0]
    return _ALIAS.get(c, c)


def lang_name(code):
    c = canon(code)
    if not c or c in ("und", "unk", "zxx", "mis"):
        return "Unknown language"
    return _NAME.get(c, c.upper())


def _path(url):
    return os.path.join(_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
                        + ".json")


def cached(url):
    try:
        with open(_path(url), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def quality(w, h):
    """'4K', '1080p', '720p' or 'SD' from the picture size (cinema films have
    black bars, so the width counts as much as the height)."""
    w, h = int(w or 0), int(h or 0)
    if not (w or h):
        return ""
    if w >= 3000 or h >= 2000:
        return "4K"
    if w >= 1800 or h >= 1000:
        return "1080p"
    if w >= 1200 or h >= 700:
        return "720p"
    return "SD"


def _parse(j):
    st = j.get("streams") or []
    vids = [s for s in st if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")]
    aud = [s for s in st if s.get("codec_type") == "audio"]
    sub = [s for s in st if s.get("codec_type") == "subtitle"]
    v = vids[0] if vids else {}
    codec = str(v.get("codec_name") or "")
    codec = {"h264": "H.264", "hevc": "H.265", "mpeg4": "MPEG-4",
             "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG-2"}.get(codec, codec.upper())
    try:
        dur = int(float((j.get("format") or {}).get("duration") or 0))
    except (TypeError, ValueError):
        dur = 0
    return {
        "w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
        "quality": quality(v.get("width"), v.get("height")),
        "vcodec": codec,
        "audio": [{"lang": canon((s.get("tags") or {}).get("language")),
                   "codec": str(s.get("codec_name") or ""),
                   "ch": int(s.get("channels") or 0)} for s in aud],
        "subs": [{"lang": canon((s.get("tags") or {}).get("language"))}
                 for s in sub],
        "duration": dur, "t": time.time(),
    }


def probe(url, force=False):
    """Read the file header (cached). Returns the details, or None when the
    file could not be read (not cached, so it is tried again later)."""
    if not force:
        c = cached(url)
        if c is not None:
            return c
    with _lock:
        if url in _running:
            return None
        _running.add(url)
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", "-user_agent", "Mozilla/5.0",
             "-probesize", "5000000", "-analyzeduration", "5000000", url],
            capture_output=True, text=True, timeout=TIMEOUT)
        j = json.loads(r.stdout or "{}")
        if not (j.get("streams") or []):
            return None
        d = _parse(j)
        if not d["w"] and not d["audio"]:
            return None
        os.makedirs(_DIR, exist_ok=True)
        tmp = _path(url) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _path(url))
        return d
    except Exception as e:
        print("movie probe failed: %s" % e, flush=True)
        return None
    finally:
        with _lock:
            _running.discard(url)


def summary(d, audio_prefs=(), sub_prefs=()):
    """Texts for the fact sheet: {"quality", "audio", "subs"}. Audio and
    subtitles list the tracks that match the viewer's earlier choices (most
    recent choice first); when none match, what the file has is listed."""
    if not d:
        return {}

    def pick(tracks, prefs):
        have = []
        for t in tracks:
            if t["lang"] not in have:
                have.append(t["lang"])
        want = [canon(p) for p in prefs if p]
        match = [c for c in dict.fromkeys(want) if c in have]
        if match:
            return [lang_name(c) for c in match], True
        return [lang_name(c) for c in have], False

    q = " ".join(x for x in (d.get("quality"), d.get("vcodec")) if x)
    out = {"quality": q}
    a, a_ok = pick(d.get("audio") or [], audio_prefs)
    s, s_ok = pick(d.get("subs") or [], sub_prefs)
    out["audio"] = ", ".join(a) if a else ""
    out["audio_match"] = a_ok
    out["subs"] = ", ".join(s) if s else ""
    out["subs_match"] = s_ok
    out["no_subs"] = not (d.get("subs") or [])
    return out
