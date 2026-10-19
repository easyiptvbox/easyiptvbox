"""Automatic EPG (XMLTV TV guide) for the user's live channels.

Strategy:
  * The provider's channels use their own tvg_id / names which do NOT match
    any public XMLTV guide's channel ids.
  * We download the big epgshare01 "ALL_SOURCES" XMLTV (a few hundred MB
    gzipped), stream-parse it, and:
      1. read every <channel> to learn which channel ids exist and their
         display names,
      2. fuzzy-match the user's live channels (from the selected categories)
         to the best guide channel id,
      3. stream the <programme> entries and keep only those for the matched
         channels, writing a small filtered guide to cache/epg_auto.xml.
  * This runs in the background and refreshes every ~24h.

Everything is streaming (iterparse over a gzip stream) so memory stays low
even though the source file is ~1.8 GB uncompressed.
"""
import os
import re
import gzip
import json
import time
import urllib.request
import xml.etree.ElementTree as ET

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("IPTV_CACHE", os.path.join(APP_DIR, "cache"))

ALL_SOURCES_URL = ("https://epgshare01.online/epgshare01/"
                   "epg_ripper_ALL_SOURCES1.xml.gz")

GUIDE_PATH = os.path.join(CACHE_DIR, "epg_auto.xml")
MAP_PATH = os.path.join(CACHE_DIR, "epg_map.json")


# --------------------------------------------------------------------------
#  Name normalisation + fuzzy matching
# --------------------------------------------------------------------------
_QUALITY = re.compile(
    r"\b(uhd|4k|8k|fhd|hd|sd|raw|vip|hevc|h265|h264|ultra|"
    r"1080p?|720p?|2160p?|multi[- ]?audio|backup|stable|"
    r"exclusive|ppv)\b", re.I)
_SUPERSCRIPT = str.maketrans(
    "\u1d34\u1d30\u1d3f\u1d2c\u1d42\u1d35\u1d3e\u1d38\u1d3a\u1d1c\u1d3c"
    "\u1d31\u1d52\u1d56\u1d50\u1d43\u1d47\u1d9c\u1d48\u1d49\u1da0\u1d4d"
    "\u02b0\u2071\u02b2\u1d4f\u02e1\u207f\u1d52\u1d56\u1d5b\u02b3\u02e2"
    "\u1d57\u1d58\u1d5b\u02b7\u02e3\u02b8\u1dbb",
    "HDRAWIBLNUOEOPMABCDEFGHIJKLNOPVRSTUVWXYZ")


def _norm(name):
    """Reduce a channel name to a comparable token string.

    Removes country/quality tags, punctuation, provider prefixes, etc.
    """
    s = name or ""
    # translate common superscript letters to normal
    try:
        s = s.translate(_SUPERSCRIPT)
    except Exception:
        pass
    s = s.lower()
    # drop a leading provider prefix like "no:", "uk:", "gold:"
    s = re.sub(r"^[a-z0-9]{1,6}\s*:\s*", "", s)
    # drop bracketed / parenthesised bits e.g. (NO,NO) [backup]
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", s)
    # a guide id like "NRK1.Norway.(NO,NO).no" -> spaces
    s = s.replace(".", " ")
    # remove quality/format words
    s = _QUALITY.sub(" ", s)
    # keep alnum + spaces
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens(name):
    n = _norm(name)
    return set(n.split()) if n else set()


def _score(a_tokens, a_norm, b_tokens, b_norm):
    """Similarity 0..1 between two normalised channel names."""
    if not a_tokens or not b_tokens:
        return 0.0
    inter = a_tokens & b_tokens
    if not inter:
        return 0.0
    # Jaccard on tokens, boosted if one is a substring of the other
    jac = len(inter) / len(a_tokens | b_tokens)
    if a_norm == b_norm:
        return 1.0
    if a_norm in b_norm or b_norm in a_norm:
        jac = max(jac, 0.9)
    return jac


# Map ISO country codes (from provider tvg_id / EPG id) to the country
# tokens they may appear as in an epgshare channel id.
_COUNTRY_TOKENS = {
    "no": {"norway", "no"},
    "uk": {"uk", "gb", "england", "britain", "united", "kingdom"},
    "gb": {"uk", "gb", "england", "britain", "united", "kingdom"},
    "se": {"sweden", "se"},
    "dk": {"denmark", "dk"},
    "de": {"germany", "de"},
    "fr": {"france", "fr"},
}


def _country_of_epg_id(cid):
    """The trailing country code of an epgshare id, e.g. '...no' -> 'no'."""
    if not cid:
        return ""
    tail = cid.rsplit(".", 1)[-1].lower()
    return tail if len(tail) == 2 and tail.isalpha() else ""



# --------------------------------------------------------------------------
#  Download + streaming parse
# --------------------------------------------------------------------------
def _open_gz_stream(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "IPTVPlayer/1.0"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return gzip.GzipFile(fileobj=resp)


def _read_channels(url):
    """Stream the guide and return {channel_id: [display_names]}."""
    channels = {}
    stream = _open_gz_stream(url)
    try:
        cur_id = None
        names = []
        for event, elem in ET.iterparse(stream, events=("start", "end")):
            tag = elem.tag
            if event == "start" and tag == "channel":
                cur_id = elem.get("id")
                names = []
            elif event == "end":
                if tag == "display-name" and cur_id is not None:
                    if elem.text:
                        names.append(elem.text)
                elif tag == "channel":
                    if cur_id:
                        # also use the id itself as a name candidate
                        channels[cur_id] = names + [cur_id]
                    cur_id = None
                    names = []
                    elem.clear()
                elif tag == "programme":
                    # channels appear before programmes; stop early
                    elem.clear()
                    break
    finally:
        try:
            stream.close()
        except Exception:
            pass
    return channels


def _write_filtered_guide(url, wanted_ids, out_path):
    """Stream the guide again and write only <channel>/<programme> whose id is
    in wanted_ids to a small XMLTV file."""
    tmp = out_path + ".tmp"
    stream = _open_gz_stream(url)
    n_prog = 0
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tv>\n')
            for event, elem in ET.iterparse(stream, events=("end",)):
                tag = elem.tag
                if tag == "channel":
                    if elem.get("id") in wanted_ids:
                        f.write(ET.tostring(elem, encoding="unicode"))
                        f.write("\n")
                    elem.clear()
                elif tag == "programme":
                    if elem.get("channel") in wanted_ids:
                        f.write(ET.tostring(elem, encoding="unicode"))
                        f.write("\n")
                        n_prog += 1
                    elem.clear()
            f.write("</tv>\n")
        os.replace(tmp, out_path)
    finally:
        try:
            stream.close()
        except Exception:
            pass
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return n_prog


# --------------------------------------------------------------------------
#  Public: build the mapping + filtered guide
# --------------------------------------------------------------------------
def build_auto_epg(live_channels, url=ALL_SOURCES_URL, min_score=0.5):
    """live_channels: iterable of (tvg_id, name).

    Returns (mapping, n_programmes) where mapping is:
        { key -> epg_channel_id }
    key is the provider tvg_id (if set) and/or the normalised name.
    Writes the filtered guide to GUIDE_PATH and the mapping to MAP_PATH.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    # 1) learn all guide channels
    epg_channels = _read_channels(url)   # {id: [names...]}
    # precompute normalised tokens for each guide channel
    epg_norm = []
    for cid, names in epg_channels.items():
        best = None
        for nm in names:
            nn = _norm(nm)
            toks = set(nn.split()) if nn else set()
            if toks and (best is None or len(toks) > len(best[1])):
                best = (nn, toks)
        if best:
            country = _country_of_epg_id(cid)
            epg_norm.append((cid, best[0], best[1], country))

    def _prov_country(tvg_id):
        if tvg_id and "." in tvg_id:
            tail = tvg_id.rsplit(".", 1)[-1].lower()
            if len(tail) == 2 and tail.isalpha():
                return tail
        return ""

    # 2) match each provider channel to the best guide channel
    mapping = {}
    wanted = set()
    # de-duplicate provider channels by (tvg_id, normalised name)
    seen = set()
    for tvg_id, name in live_channels:
        pn = _norm(name)
        if not pn:
            continue
        key = (tvg_id or "", pn)
        if key in seen:
            continue
        seen.add(key)
        ptoks = set(pn.split())
        pcc = _prov_country(tvg_id)
        want_tokens = _COUNTRY_TOKENS.get(pcc, set())
        best_id = None
        best_sc = 0.0
        for cid, en, etoks, ecc in epg_norm:
            sc = _score(ptoks, pn, etoks, en)
            if sc <= 0:
                continue
            # Prefer channels from the same country as the provider channel.
            if pcc:
                if ecc == pcc or (etoks & want_tokens):
                    sc += 0.25            # strong boost for matching country
                elif ecc and ecc != pcc:
                    sc -= 0.15            # penalise a clearly different country
            if sc > best_sc:
                best_sc = sc
                best_id = cid
        if best_id and best_sc >= min_score:
            wanted.add(best_id)
            if tvg_id:
                mapping[tvg_id] = best_id
            mapping["name:" + pn] = best_id

    # 3) write a small guide with only the matched channels
    n_prog = 0
    if wanted:
        n_prog = _write_filtered_guide(url, wanted, GUIDE_PATH)

    try:
        with open(MAP_PATH, "w", encoding="utf-8") as f:
            json.dump({"mapping": mapping, "built": time.time(),
                       "channels": len(wanted), "programmes": n_prog}, f)
    except Exception:
        pass
    return mapping, n_prog


def load_map():
    """Return the saved {key -> epg_channel_id} mapping (or {})."""
    try:
        with open(MAP_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("mapping", {})
    except Exception:
        return {}


def map_age_hours():
    try:
        return (time.time() - os.path.getmtime(MAP_PATH)) / 3600.0
    except Exception:
        return 1e9


def normalise_name(name):
    return _norm(name)
