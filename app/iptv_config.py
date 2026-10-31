"""Configuration loading and provider (Xtream Codes) / EPG handling."""
import os
import sys
import time
import gzip
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

try:
    import yaml
except ImportError:
    print("PyYAML is required (sudo apt install python3-yaml)", file=sys.stderr)
    raise

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("IPTV_CONFIG", os.path.join(APP_DIR, "config.yaml"))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))

TEXT_SIZES = {"small": 0.75, "normal": 1.0, "large": 1.3, "extra_large": 1.6}


class Channel:
    __slots__ = ("name", "url", "logo", "tvg_id")

    def __init__(self, name, url, logo="", tvg_id=""):
        self.name = name
        self.url = url
        self.logo = logo
        self.tvg_id = tvg_id


class Playlist:
    def __init__(self, name, is_movies=False, is_series=False, alias=""):
        self.name = name
        self.alias = alias    # short display name (falls back to name)
        self.channels = []
        self.is_movies = is_movies
        self.is_series = is_series
        self.series = []   # for a Series category: [{series_id, name, logo}]

    @property
    def display_name(self):
        return self.alias.strip() if self.alias and self.alias.strip() else self.name


class Config:
    def __init__(self, data):
        self._data = data or {}
        self.playlists = []
        self.epg_sources = list((self._data.get("epg") or {}).get("sources") or [])
        self.epg_refresh_hours = float((self._data.get("epg") or {}).get("refresh_hours") or 12)
        # The "Refresh every (hours)" setting also controls how often the
        # provider caches (channels / movies / series) are refreshed.
        self.refresh_hours = self.epg_refresh_hours
        s = self._data.get("settings") or {}
        self.text_scale = TEXT_SIZES.get(str(s.get("text_size", "large")), 1.3)
        self.theme = "light" if str(s.get("theme", "dark")) == "light" else "dark"
        self.autoplay_first = bool(s.get("autoplay_first_channel", False))
        self.show_clock = bool(s.get("show_clock", True))
        # Live-TV audio sync offset, in seconds (config stores milliseconds).
        # Negative = audio earlier (when sound lags the video), positive = later.
        try:
            self.audio_delay = float(s.get("audio_delay_ms", 0) or 0) / 1000.0
        except (TypeError, ValueError):
            self.audio_delay = 0.0

    def load_sources(self):
        """Build the list of playlists from manual config + provider categories."""
        self.playlists = []
        prov = self._data.get("provider") or {}
        # Provider channel snapshot, grouped by category id and name, loaded
        # once from the on-disk cache (fast). Refreshed daily by the app.
        prov_by_cat_id = {}
        prov_by_cat_name = {}
        if prov.get("enabled"):
            try:
                for c in cached_provider_channels(prov):
                    prov_by_cat_id.setdefault(str(c.get("category_id", "")), []).append(c)
                    prov_by_cat_name.setdefault(c.get("group", ""), []).append(c)
            except Exception as e:
                print("Provider cache load failed: %s" % e, file=sys.stderr)

        for p in (self._data.get("playlists") or []):
            pl = Playlist(str(p.get("name", "Playlist")),
                          is_movies=bool(p.get("is_movies")),
                          is_series=bool(p.get("is_series")),
                          alias=str(p.get("alias", "")))
            # A Series category carries a list of series references.
            if pl.is_series:
                for se in (p.get("series") or []):
                    if se.get("series_id"):
                        pl.series.append({
                            "series_id": str(se.get("series_id")),
                            "name": str(se.get("name", "Series")),
                            "logo": str(se.get("logo", "")),
                        })
            # A playlist can reference a whole provider category...
            cat_id = p.get("provider_category")
            cat_name = p.get("provider_category_name")
            if cat_id is not None or cat_name is not None:
                src = prov_by_cat_id.get(str(cat_id)) if cat_id is not None else None
                if not src and cat_name:
                    src = prov_by_cat_name.get(cat_name)
                for c in (src or []):
                    pl.channels.append(Channel(
                        str(c.get("name", "Channel")),
                        str(c.get("url", "")),
                        str(c.get("logo", "")),
                        str(c.get("tvg_id", "")),
                    ))
            # ...and/or list individual channels manually.
            for c in (p.get("channels") or []):
                if not c.get("url"):
                    continue
                pl.channels.append(Channel(
                    str(c.get("name", "Channel")),
                    str(c["url"]),
                    str(c.get("logo", "")),
                    str(c.get("tvg_id", "")),
                ))
            # Keep the playlist even if empty when it's a provider category
            # (so the user still sees the category and a "no channels" note),
            # but hide truly-empty manual lists.
            if (pl.channels or cat_id is not None or cat_name is not None
                    or p.get("is_movies") or p.get("is_series")):
                self.playlists.append(pl)

        # Merge live categories that share the same display label (e.g. two
        # "Norway" categories) into a single one, dropping duplicate channels
        # (matched by URL). Movies/Series are never merged.
        merged = []
        by_label = {}
        seen_urls = {}
        for pl in self.playlists:
            if pl.is_movies or getattr(pl, "is_series", False):
                merged.append(pl)
                continue
            key = pl.display_name.strip().casefold()
            first = by_label.get(key)
            if first is None:
                by_label[key] = pl
                seen_urls[key] = {c.url for c in pl.channels}
                merged.append(pl)
            else:
                urls = seen_urls[key]
                for c in pl.channels:
                    if c.url not in urls:
                        urls.add(c.url)
                        first.channels.append(c)
        self.playlists = merged

        # Movies and Series always exist on the start page (even if empty),
        # so create them if the config didn't define them.
        if not any(getattr(pl, "is_movies", False) for pl in self.playlists):
            self.playlists.append(Playlist("Movies", is_movies=True))
        if not any(getattr(pl, "is_series", False) for pl in self.playlists):
            self.playlists.append(Playlist("Series", is_series=True))

        return self.playlists


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cfg = Config(data)
    cfg.load_sources()
    return cfg


# --------------------------------------------------------------------------
#  Xtream Codes provider support (url + username + password)
# --------------------------------------------------------------------------
def _http_json(url, timeout=20, retries=4):
    """GET JSON with retries and backoff.

    Providers commonly rate-limit big/repeated requests with an HTTP 513
    (or drop the connection). We retry with growing delays and, if it still
    fails, raise a clear, user-friendly error.
    """
    import urllib.error
    last = None
    for attempt in range(retries):
        resp = None
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "IPTVPlayer/1.0",
                              "Connection": "close"})
            resp = urllib.request.urlopen(req, timeout=timeout)
            data = resp.read()
            return json.loads(data.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = e
            try:
                e.read()      # drain so the socket can close cleanly
            except Exception:
                pass
            if e.code == 513:
                # rate-limited: wait a bit before retrying (keep total short so
                # the web request doesn't hang too long)
                time.sleep(6 * (attempt + 1))
            else:
                time.sleep(3 * (attempt + 1))
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
    # Turn the raw error into something the UI can show sensibly.
    import urllib.error as ue
    if isinstance(last, ue.HTTPError) and last.code == 513:
        raise RuntimeError(
            "The provider is temporarily busy (rate limit). "
            "Please wait a minute and press refresh again.")
    raise last


def _parse_provider(prov):
    """Work out base URL + username + password.

    Supports several ways users enter provider details:
      1. Separate fields: url = http://host:port, username, password.
      2. A full Xtream URL pasted into the url field, e.g.
         http://host:port/get.php?username=U&password=P&type=m3u_plus
         (credentials are taken from the query string).
      3. A player_api.php URL with username/password params.
    Returns (base_url, username, password).
    """
    raw_url = str(prov.get("url", "")).strip()
    user = str(prov.get("username", "")).strip()
    pw = str(prov.get("password", "")).strip()

    # Treat leftover placeholder / example values as "not set".
    PLACEHOLDERS = {"", "your_username", "your_password", "username", "password",
                    "user", "pass", "changeme"}
    if user.lower() in PLACEHOLDERS:
        user = ""
    if pw.lower() in PLACEHOLDERS:
        pw = ""

    parsed = urllib.parse.urlparse(raw_url)
    # Pull credentials from the query string if present (and not already set).
    if parsed.query:
        qs = urllib.parse.parse_qs(parsed.query)
        if not user and qs.get("username"):
            user = qs["username"][0]
        if not pw and qs.get("password"):
            pw = qs["password"][0]

    # Base = scheme://host[:port] with any /get.php, /player_api.php, path stripped.
    if parsed.scheme and parsed.netloc:
        base = "%s://%s" % (parsed.scheme, parsed.netloc)
    else:
        base = raw_url.rstrip("/")
    base = base.rstrip("/")
    return base, user, pw


def load_xtream(prov):
    base, username, password = _parse_provider(prov)
    if not base or not username or not password:
        raise ValueError("provider needs a URL, username and password "
                         "(or a URL containing username/password parameters)")
    user = urllib.parse.quote(username)
    pw = urllib.parse.quote(password)
    only = set(prov.get("only_categories") or [])
    api = "%s/player_api.php?username=%s&password=%s" % (base, user, pw)

    cats = _http_json(api + "&action=get_live_categories")
    cat_names = {str(c["category_id"]): c.get("category_name", "Live")
                 for c in cats}
    streams = _http_json(api + "&action=get_live_streams")

    by_cat = {}
    for st in streams:
        cid = str(st.get("category_id"))
        cname = cat_names.get(cid, "Live")
        if only and cname not in only:
            continue
        ext = st.get("container_extension") or "ts"
        surl = "%s/live/%s/%s/%s.%s" % (
            base, username, password, st["stream_id"], ext)
        ch = Channel(
            str(st.get("name", "Channel")),
            surl,
            str(st.get("stream_icon", "")),
            str(st.get("epg_channel_id", "")),
        )
        by_cat.setdefault(cname, []).append(ch)

    out = []
    for cname, chans in by_cat.items():
        pl = Playlist(cname)
        pl.channels = chans
        out.append(pl)
    return out


def fetch_provider_channels(prov, force=False, max_age_hours=12):
    """Return a flat, searchable list of ALL provider live channels.

    Each item: {"name", "url", "logo", "tvg_id", "group"}.
    Cached to disk because a big provider can return tens of thousands of
    channels and take a few seconds to download.
    """
    base, username, password = _parse_provider(prov)
    if not base or not username or not password:
        raise ValueError("provider needs a URL + username + password")

    cache = _cache_path("provider_channels.json")
    if (not force and os.path.exists(cache) and
            (time.time() - os.path.getmtime(cache)) < max_age_hours * 3600):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    user = urllib.parse.quote(username)
    pw = urllib.parse.quote(password)
    api = "%s/player_api.php?username=%s&password=%s" % (base, user, pw)
    cats = _http_json(api + "&action=get_live_categories", timeout=40)
    cat_names = {str(c["category_id"]): c.get("category_name", "Live")
                 for c in cats}
    streams = _http_json(api + "&action=get_live_streams", timeout=90)

    items = []
    for st in streams:
        ext = st.get("container_extension") or "ts"
        surl = "%s/live/%s/%s/%s.%s" % (
            base, username, password, st["stream_id"], ext)
        items.append({
            "name": str(st.get("name", "Channel")),
            "url": surl,
            "logo": str(st.get("stream_icon", "")),
            "tvg_id": str(st.get("epg_channel_id", "")),
            "category_id": str(st.get("category_id", "")),
            "group": cat_names.get(str(st.get("category_id")), "Live"),
        })
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(items, f)
    except Exception:
        pass
    return items


def cached_provider_channels(prov):
    """Return the provider channel snapshot from the on-disk cache only.

    Does NOT hit the network (so app startup stays fast). Returns [] if there
    is no cache yet. Use fetch_provider_channels(force=True) to (re)build it.
    """
    cache = _cache_path("provider_channels.json")
    if os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    # No cache yet: try a (network) fetch so the first run still works.
    try:
        return fetch_provider_channels(prov)
    except Exception:
        return []


def _read_cache_file(name):
    """Read a cached JSON list from disk. Returns None if missing/unreadable."""
    cache = _cache_path(name)
    if os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def cached_provider_movies(prov):
    """Movies from cache only. Returns [] if there is no cache yet."""
    data = _read_cache_file("provider_movies.json")
    return data if data is not None else []


def cached_provider_series(prov):
    """Series from cache only. Returns [] if there is no cache yet."""
    data = _read_cache_file("provider_series.json")
    return data if data is not None else []


def provider_categories(prov, force=False, cache_only=False):
    """Return [{id, name, count}] for the provider's live categories.

    cache_only=True never touches the network (used by the web search UI).
    """
    if cache_only:
        chans = cached_provider_channels(prov)
    else:
        chans = fetch_provider_channels(prov, force=force)
    by = {}
    for c in chans:
        key = (c.get("category_id", ""), c.get("group", ""))
        by[key] = by.get(key, 0) + 1
    cats = [{"id": cid, "name": name, "count": n}
            for (cid, name), n in by.items()]
    cats.sort(key=lambda x: x["name"].lower())
    return cats


def fetch_provider_movies(prov, force=False, max_age_hours=24):
    """Return a flat, searchable list of ALL provider VOD movies.

    Each item: {"name", "url", "logo", "group", "rating", "year"}.
    Cached to disk (the VOD catalogue can be very large).
    """
    base, username, password = _parse_provider(prov)
    if not base or not username or not password:
        raise ValueError("provider needs a URL + username + password")

    cache = _cache_path("provider_movies.json")
    if (not force and os.path.exists(cache) and
            (time.time() - os.path.getmtime(cache)) < max_age_hours * 3600):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    user = urllib.parse.quote(username)
    pw = urllib.parse.quote(password)
    api = "%s/player_api.php?username=%s&password=%s" % (base, user, pw)
    cats = _http_json(api + "&action=get_vod_categories", timeout=40)
    cat_names = {str(c["category_id"]): c.get("category_name", "Movies")
                 for c in cats}
    streams = _http_json(api + "&action=get_vod_streams", timeout=120)

    items = []
    for st in streams:
        ext = st.get("container_extension") or "mp4"
        surl = "%s/movie/%s/%s/%s.%s" % (
            base, username, password, st["stream_id"], ext)
        items.append({
            "name": str(st.get("name", "Movie")),
            "url": surl,
            "logo": str(st.get("stream_icon", "")),
            "category_id": str(st.get("category_id", "")),
            "group": cat_names.get(str(st.get("category_id")), "Movies"),
            "rating": str(st.get("rating", "")),
        })
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(items, f)
    except Exception:
        pass
    return items


def fetch_provider_series(prov, force=False, max_age_hours=24):
    """Return a flat, searchable list of ALL provider TV series.

    Each item: {"series_id", "name", "logo", "category_id", "group", "rating"}.
    Cached to disk.
    """
    base, username, password = _parse_provider(prov)
    if not base or not username or not password:
        raise ValueError("provider needs a URL + username + password")

    cache = _cache_path("provider_series.json")
    if (not force and os.path.exists(cache) and
            (time.time() - os.path.getmtime(cache)) < max_age_hours * 3600):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    user = urllib.parse.quote(username)
    pw = urllib.parse.quote(password)
    api = "%s/player_api.php?username=%s&password=%s" % (base, user, pw)
    cats = _http_json(api + "&action=get_series_categories", timeout=40)
    cat_names = {str(c["category_id"]): c.get("category_name", "Series")
                 for c in cats}

    # Some providers return nothing for a bare get_series and require a
    # category_id; fetch per-category if the global call is empty.
    all_series = _http_json(api + "&action=get_series", timeout=120)
    if not all_series and cats:
        all_series = []
        for c in cats:
            try:
                part = _http_json(
                    api + "&action=get_series&category_id=%s" % c["category_id"],
                    timeout=60)
                all_series.extend(part or [])
            except Exception:
                continue

    items = []
    for st in all_series:
        items.append({
            "series_id": str(st.get("series_id", "")),
            "name": str(st.get("name", "Series")),
            "logo": str(st.get("cover", "") or st.get("stream_icon", "")),
            "category_id": str(st.get("category_id", "")),
            "group": cat_names.get(str(st.get("category_id")), "Series"),
            "rating": str(st.get("rating", "")),
        })
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(items, f)
    except Exception:
        pass
    return items


def fetch_series_episodes(prov, series_id, force=False, max_age_hours=24):
    """Return the seasons/episodes for one series.

    Returns {"seasons": [{"season": N, "name": str,
                          "episodes": [{"title","url","episode_num"}]}]}.
    Cached per series on disk.
    """
    base, username, password = _parse_provider(prov)
    if not base or not username or not password:
        raise ValueError("provider needs a URL + username + password")

    safe_id = "".join(ch for ch in str(series_id) if ch.isalnum())
    cache = _cache_path("series_%s.json" % safe_id)
    if (not force and os.path.exists(cache) and
            (time.time() - os.path.getmtime(cache)) < max_age_hours * 3600):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    user = urllib.parse.quote(username)
    pw = urllib.parse.quote(password)
    api = "%s/player_api.php?username=%s&password=%s" % (base, user, pw)
    info = _http_json(
        api + "&action=get_series_info&series_id=%s" % urllib.parse.quote(
            str(series_id)), timeout=20, retries=2)

    episodes_by_season = info.get("episodes") or {}
    season_meta = {str(s.get("season_number")): s
                   for s in (info.get("seasons") or [])}

    seasons = []
    for skey in sorted(episodes_by_season.keys(),
                       key=lambda x: int(x) if str(x).isdigit() else 0):
        eps = []
        for ep in episodes_by_season[skey]:
            ext = ep.get("container_extension") or "mp4"
            eid = ep.get("id")
            url = "%s/series/%s/%s/%s.%s" % (base, username, password, eid, ext)
            num = ep.get("episode_num")
            title = ep.get("title") or ("Episode %s" % num)
            eps.append({
                "title": str(title),
                "url": url,
                "episode_num": num,
            })
        eps.sort(key=lambda e: int(e["episode_num"])
                 if str(e["episode_num"]).isdigit() else 0)
        meta = season_meta.get(str(skey), {})
        sname = meta.get("name") or ("Season %s" % skey)
        seasons.append({"season": skey, "name": str(sname), "episodes": eps})

    result = {"seasons": seasons}
    try:
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except Exception:
        pass
    return result



# --------------------------------------------------------------------------
#  EPG (XMLTV) support
# --------------------------------------------------------------------------
def _cache_path(name):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, name)


class EPG:
    """Loads XMLTV guides and answers 'now / next' for a channel.

    Two ways to resolve a channel to guide programmes:
      1. Directly by its tvg_id (if the guide uses the same ids).
      2. Via the automatic fuzzy mapping (cache/epg_map.json) built by
         iptv_epg, which maps provider tvg_id / channel name to the guide's
         channel id.
    """
    def __init__(self, sources, refresh_hours=24):
        self.sources = sources or []
        self.refresh_hours = refresh_hours
        # channel_id -> list of (start_epoch, stop_epoch, title)
        self.programmes = {}
        # provider tvg_id / "name:<norm>" -> guide channel_id
        self.mapping = {}

    def refresh(self, force=False):
        merged = {}
        # 1) any manually-configured XMLTV sources
        for i, src in enumerate(self.sources):
            try:
                raw = self._fetch(src, i, force)
                self._parse_into(raw, merged)
            except Exception as e:
                print("EPG source failed (%s): %s" % (src, e), file=sys.stderr)
        # 2) the automatic filtered guide built by iptv_epg (if present)
        try:
            import iptv_epg
            self.mapping = iptv_epg.load_map()
            if os.path.exists(iptv_epg.GUIDE_PATH):
                with open(iptv_epg.GUIDE_PATH, "rb") as f:
                    self._parse_into(f.read(), merged)
        except Exception as e:
            print("Auto-EPG load failed: %s" % e, file=sys.stderr)
        if merged:
            self.programmes = merged

    def _fetch(self, url, idx, force):
        cache = _cache_path("epg_%d.xml" % idx)
        fresh = (os.path.exists(cache) and not force and
                 (time.time() - os.path.getmtime(cache)) < self.refresh_hours * 3600)
        if fresh:
            with open(cache, "rb") as f:
                return f.read()
        req = urllib.request.Request(url, headers={"User-Agent": "IPTVPlayer/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        with open(cache, "wb") as f:
            f.write(data)
        return data

    @staticmethod
    def _parse_time(s):
        # XMLTV: 20260824180000 +0000
        s = s.strip()
        base = s[:14]
        tz = s[15:] if len(s) > 15 else "+0000"
        try:
            st = time.strptime(base, "%Y%m%d%H%M%S")
            epoch = calendar_timegm(st)
            sign = 1 if tz[0] != "-" else -1
            off = int(tz[1:3]) * 3600 + int(tz[3:5]) * 60
            return epoch - sign * off
        except Exception:
            return 0

    def _parse_into(self, raw, merged):
        root = ET.fromstring(raw)
        for prog in root.iter("programme"):
            cid = prog.get("channel", "")
            if not cid:
                continue
            start = self._parse_time(prog.get("start", ""))
            stop = self._parse_time(prog.get("stop", ""))
            title_el = prog.find("title")
            title = title_el.text if title_el is not None and title_el.text else ""
            merged.setdefault(cid, []).append((start, stop, title))
        for cid in merged:
            merged[cid].sort()

    def _resolve(self, tvg_id, name=""):
        """Return the guide channel_id for a channel, using direct id, then
        the fuzzy mapping (by tvg_id, then by normalised name)."""
        if tvg_id and tvg_id in self.programmes:
            return tvg_id
        if tvg_id and tvg_id in self.mapping:
            return self.mapping[tvg_id]
        if name:
            try:
                import iptv_epg
                key = "name:" + iptv_epg.normalise_name(name)
                if key in self.mapping:
                    return self.mapping[key]
            except Exception:
                pass
        return tvg_id or None

    def now_next(self, tvg_id, name=""):
        cid = self._resolve(tvg_id, name)
        if not cid or cid not in self.programmes:
            return (None, None)
        now = time.time()
        progs = self.programmes[cid]
        cur = nxt = None
        for i, (start, stop, title) in enumerate(progs):
            if start <= now < stop:
                cur = (start, stop, title)
                if i + 1 < len(progs):
                    nxt = progs[i + 1]
                break
            if start > now:
                nxt = (start, stop, title)
                break
        return (cur, nxt)


def calendar_timegm(st):
    import calendar
    return calendar.timegm(st)
