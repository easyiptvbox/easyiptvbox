"""Saved searches: "Add future results automatically".

A saved search remembers a movie/series search and which results existed when
it was saved. Each time the provider lists are renewed, the search is run
again on the fresh list; any NEW results are added to the Movies / Series
category and the saved search is then deleted (it has done its job).
"""
import json
import os
import time

import iptv_profiles
from iptv_config import (CACHE_DIR, CONFIG_PATH, cached_provider_movies,
                         cached_provider_series)



def _searches_path():
    return os.path.join(iptv_profiles.state_dir(), "saved_searches.json")


def _key(kind, item):
    return str(item.get("url") if kind == "movies" else item.get("series_id"))


def matches(kind, query):
    q = (query or "").strip().lower()
    if not q:
        return []
    items = cached_provider_movies({}) if kind == "movies" \
        else cached_provider_series({})
    words = q.split()
    return [it for it in items
            if all(w in str(it.get("name", "")).lower() for w in words)]


def load():
    try:
        with open(_searches_path(), "r", encoding="utf-8") as f:
            return [s for s in json.load(f) if isinstance(s, dict)]
    except Exception:
        return []


def _save(searches):
    os.makedirs(os.path.dirname(_searches_path()), exist_ok=True)
    tmp = _searches_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(searches, f, indent=1)
    os.replace(tmp, _searches_path())


def add(kind, query):
    """Save a search. Everything it matches right now counts as 'known', so
    only results that appear later are added automatically."""
    if kind not in ("movies", "series"):
        raise ValueError("kind must be movies or series")
    query = (query or "").strip()
    if not query:
        raise ValueError("empty search")
    searches = [s for s in load()
                if not (s["kind"] == kind and s["query"].lower() == query.lower())]
    searches.append({
        "kind": kind, "query": query, "created": int(time.time()),
        "known": sorted({_key(kind, it) for it in matches(kind, query)}),
    })
    _save(searches)
    return searches[-1]


def remove(kind, query):
    searches = load()
    kept = [s for s in searches
            if not (s["kind"] == kind and s["query"].lower() == query.lower())]
    _save(kept)
    return len(kept) != len(searches)


def _clean_movie_name(n):
    import re
    return re.sub(r"^[0-9A-Z\-\| ]{0,10}-\s*", "", n).strip() or n


def check():
    """Run every saved search on the current (cached) lists. New results are
    added to the config and the search is deleted. Returns the number of
    items added (0 = config untouched)."""
    searches = load()
    if not searches:
        return 0
    import yaml
    try:
        data = iptv_profiles.read_config_data()
    except Exception:
        return 0
    playlists = data.setdefault("playlists", [])

    def category(flag, name):
        for p in playlists:
            if p.get(flag):
                return p
        p = {"name": name, flag: True}
        playlists.append(p)
        return p

    added, remaining = 0, []
    for s in searches:
        kind = s.get("kind")
        found = matches(kind, s.get("query"))
        if not found:
            remaining.append(s)       # list empty / not loaded yet: keep
            continue
        known = set(s.get("known") or [])
        new = [it for it in found if _key(kind, it) not in known]
        if not new:
            remaining.append(s)
            continue
        if kind == "movies":
            cat = category("is_movies", "Movies")
            chans = cat.setdefault("channels", [])
            have = {c.get("url") for c in chans}
            for it in new:
                if it["url"] not in have:
                    chans.append({"name": _clean_movie_name(it["name"]),
                                  "url": it["url"], "logo": it.get("logo", "")})
                    added += 1
        else:
            cat = category("is_series", "Series")
            lst = cat.setdefault("series", [])
            have = {str(x.get("series_id")) for x in lst}
            for it in new:
                if str(it["series_id"]) not in have:
                    lst.append({"series_id": str(it["series_id"]),
                                "name": it["name"], "logo": it.get("logo", "")})
                    added += 1
        print("saved search '%s' (%s): added %d new result(s), search removed"
              % (s.get("query"), kind, len(new)), flush=True)
        # search done: not kept in `remaining`
    if added:
        from iptv_web import build_config_yaml
        text = build_config_yaml(data)
        iptv_profiles.write_config_text(text)
    if len(remaining) != len(searches):
        _save(remaining)
    return added
