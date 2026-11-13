"""Movie fact sheet: description, cast, rating ... for one movie.

Data comes from the provider (Xtream `get_vod_info`, which usually carries the
TMDB plot / cast / director / genre / rating). When the provider has no
description, a short summary is looked up on Wikipedia (no key needed).
Results are cached on disk, so each movie is fetched only once.
"""
import hashlib
import json
import os
import re
import time
import threading
import urllib.parse
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))
_DIR = os.path.join(CACHE_DIR, "movieinfo")
_lock = threading.Lock()
RETRY_AFTER = 6 * 3600          # a failed lookup is tried again after this


def _path(url):
    return os.path.join(_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
                        + ".json")


def vod_id(url):
    m = re.search(r"/movie/[^/]+/[^/]+/(\d+)", url or "")
    return m.group(1) if m else ""


def clean_title(name):
    """('Movie Name (2023)' -> ('Movie Name', '2023'))"""
    name = re.sub(r"^\s*[A-Z]{2,3}\s*[-|:]\s*", "", str(name or ""))
    m = re.search(r"\((\d{4})\)\s*$", name)
    year = m.group(1) if m else ""
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
    return name, year


def cached(url):
    try:
        with open(_path(url), "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("plot") or d.get("cast") or d.get("rating"):
            return d
        if time.time() - d.get("t", 0) < RETRY_AFTER:
            return d                 # known to have nothing: don't ask again
    except Exception:
        pass
    return None


def _save(url, d):
    try:
        os.makedirs(_DIR, exist_ok=True)
        tmp = _path(url) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _path(url))
    except Exception:
        pass


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "EasyIPTVBox/1.0 (movie info)", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _num(v):
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _from_provider(prov, url):
    from iptv_config import _parse_provider, _http_json
    vid = vod_id(url)
    base, user, pw = _parse_provider(prov or {})
    if not (vid and base and user and pw):
        return {}
    d = _http_json("%s/player_api.php?username=%s&password=%s&action="
                   "get_vod_info&vod_id=%s" % (
                       base, urllib.parse.quote(user), urllib.parse.quote(pw),
                       vid), timeout=15, retries=2)
    info = (d or {}).get("info") or {}
    runtime = int(_num(info.get("runtime") or info.get("episode_run_time")
                       or 0)) or int(_num(info.get("duration_secs")) // 60)
    year = str(info.get("releasedate") or info.get("release_date") or "")[:4]
    return {
        "plot": str(info.get("plot") or info.get("description") or "").strip(),
        "cast": str(info.get("cast") or info.get("actors") or "").strip(),
        "director": str(info.get("director") or "").strip(),
        "genre": str(info.get("genre") or "").strip(),
        "year": year if year.isdigit() else "",
        "runtime": runtime,
        "rating": round(_num(info.get("rating")), 1),
        "rating_src": "TMDB" if info.get("tmdb_id") else "",
        "cover": str(info.get("cover_big") or info.get("movie_image") or ""),
    }


def _from_wikipedia(title, year):
    """Short summary of the film's Wikipedia article ('' if none found)."""
    for q in ("%s %s film" % (title, year) if year else "%s film" % title,
              title):
        s = _get_json("https://en.wikipedia.org/w/api.php?action=query&list="
                      "search&format=json&srlimit=3&srsearch=" +
                      urllib.parse.quote(q))
        for hit in (s.get("query") or {}).get("search") or []:
            t = hit.get("title", "")
            if title.lower().split(":")[0][:12] not in t.lower():
                continue
            e = _get_json("https://en.wikipedia.org/w/api.php?action=query&"
                          "prop=extracts&exintro=1&explaintext=1&redirects=1"
                          "&format=json&titles=" + urllib.parse.quote(t))
            for pg in ((e.get("query") or {}).get("pages") or {}).values():
                txt = (pg.get("extract") or "").strip()
                if len(txt) > 80:
                    return txt[:900]
    return ""


def fetch(url, name, prov, force=False):
    """The fact sheet for a movie ({} fields may be empty). Cached."""
    if not force:
        c = cached(url)
        if c is not None:
            return c
    title, year = clean_title(name)
    out = {"title": title or str(name), "year": year, "plot": "", "cast": "",
           "director": "", "genre": "", "runtime": 0, "rating": 0.0,
           "rating_src": "", "source": "", "t": time.time()}
    try:
        p = _from_provider(prov, url)
        for k, v in p.items():
            if v:
                out[k] = v
        if out["plot"] or out["cast"]:
            out["source"] = "provider"
    except Exception as e:
        print("movie info (provider) failed: %s" % e, flush=True)
    if not out["plot"] and title:
        try:
            w = _from_wikipedia(title, out["year"] or year)
            if w:
                out["plot"] = w
                out["source"] = (out["source"] + " + " if out["source"]
                                 else "") + "Wikipedia"
        except Exception as e:
            print("movie info (wikipedia) failed: %s" % e, flush=True)
    with _lock:
        _save(url, out)
    return out
