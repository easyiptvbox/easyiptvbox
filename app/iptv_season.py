"""Seasonal themes: the Christmas theme exists from 1 December to 6 January.

On the first check of each season every profile is switched to it once (the
theme that was in use is remembered). A profile that is changed to something
else afterwards is left alone until the next 1 December. When the season is
over, profiles still on Christmas go back to what they had before.
"""
import datetime
import json
import os

import iptv_profiles

CHRISTMAS_END = (1, 6)            # last day: 6 January


def season_year(today=None):
    """The year the current Christmas season started in, or None."""
    d = today or datetime.date.today()
    if d.month == 12:
        return d.year
    if d.month == 1 and d.day <= CHRISTMAS_END[1]:
        return d.year - 1
    return None


def christmas_available(today=None):
    return season_year(today) is not None


def _state_path():
    return os.path.join(iptv_profiles.BOX_CACHE, "season_state.json")


def _load():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, _state_path())
    except Exception:
        pass


def _theme_of(pid):
    try:
        st = (iptv_profiles.read_config_data(pid).get("settings") or {})
        return str(st.get("theme", "dark"))
    except Exception:
        return "dark"


def _set_theme(pid, theme):
    import yaml
    with iptv_profiles.CONFIG_LOCK:
        data = iptv_profiles.read_config_data(pid)
        data = dict(data)
        st = dict(data.get("settings") or {})
        st["theme"] = theme
        data["settings"] = st
        iptv_profiles.write_config_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), pid)


def check(today=None):
    """Apply the seasonal rules. Returns True when a profile's theme changed."""
    year = season_year(today)
    st = _load()
    pids = list(dict.fromkeys([iptv_profiles.DEFAULT] + iptv_profiles.ids()))
    changed = False
    if year is not None:
        if st.get("season") == year:
            return False                      # already done this season
        prev = {}
        for pid in pids:
            t = _theme_of(pid)
            if t != "christmas":
                prev[pid] = t
                _set_theme(pid, "christmas")
                changed = True
        _save({"season": year, "prev": prev})
    else:
        prev = st.get("prev") or {}
        for pid in pids:
            if _theme_of(pid) == "christmas":
                back = prev.get(pid) or prev.get(iptv_profiles.DEFAULT) or "dark"
                _set_theme(pid, back if back != "christmas" else "dark")
                changed = True
    return changed
