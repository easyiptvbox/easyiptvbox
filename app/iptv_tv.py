#!/usr/bin/env python3
"""IPTV Player - simple, remote-controlled TV for everyone.

Screens:
  * PLAYLISTS  - pick a big list (News, Sports, ...)
  * CHANNELS   - pick a channel inside the chosen list
  * PLAYING    - video is on; a small overlay can show now/next

Remote:
  Up/Down  move selection      OK    choose / play
  Left/Right change column      Back  go back one screen
  Home     back to first screen Menu  show channel list while watching
  Vol +/-  volume               Power quit / restart handled by system
"""
import os
import sys
import time
import threading

os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")  # audio goes through mpv
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

from iptv_config import load_config, EPG
from iptv_player import Player
import iptv_input as ir
from iptv_web import ControlServer
from iptv_movies import MovieState
import iptv_thumbs
import iptv_posters
import iptv_livepics
import iptv_profiles
import iptv_net
import iptv_stream

MENU_SCREENS = ("playlists", "channels", "series")   # screens with the left menu
SECTIONS = ("movies", "live", "series")
SECTION_LABELS = {"live": "Live", "movies": "Movies", "series": "Series"}
def _ui_state_path():
    return os.path.join(iptv_profiles.state_dir(), "ui_state.json")


WEB_PORT = int(os.environ.get("IPTV_WEB_PORT", "8080"))


def _local_ip():
    """Best-effort primary LAN IPv4 address of this machine."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def web_app_url():
    return "http://%s:%d" % (_local_ip(), WEB_PORT)


def _fmt_time(seconds):
    seconds = int(max(0, seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)

# ---- colours (dark, high contrast, easy on the eyes) --------------------
BG = (18, 20, 28)
PANEL = (30, 33, 45)
SELECT = (0, 122, 204)
SELECT_TEXT = (255, 255, 255)
TEXT = (225, 228, 235)
DIM = (140, 145, 160)
ACCENT = (90, 200, 120)
MENU_BG = (20, 22, 31)
TILE_BG = (10, 12, 18)
POPUP_BG = (28, 31, 43, 250)

THEMES = {
    "dark": dict(BG=(18, 20, 28), PANEL=(30, 33, 45), SELECT=(0, 122, 204),
                 SELECT_TEXT=(255, 255, 255), TEXT=(225, 228, 235),
                 DIM=(140, 145, 160), ACCENT=(90, 200, 120),
                 MENU_BG=(20, 22, 31), TILE_BG=(10, 12, 18),
                 POPUP_BG=(28, 31, 43, 250)),
    "light": dict(BG=(243, 245, 249), PANEL=(224, 229, 238), SELECT=(0, 105, 180),
                  SELECT_TEXT=(255, 255, 255), TEXT=(28, 34, 48),
                  DIM=(95, 104, 120), ACCENT=(30, 140, 75),
                  MENU_BG=(230, 234, 242), TILE_BG=(205, 211, 224),
                  POPUP_BG=(255, 255, 255, 250)),
}


def set_theme(name):
    """Switch the TV colours (same setting as the web app's theme)."""
    globals().update(THEMES.get(name, THEMES["dark"]))

SPLASH_DIR = "/usr/share/plymouth/themes/easyiptvbox"
SPLASH_BOOT = os.path.join(SPLASH_DIR, "splash.png")
SPLASH_SHUTDOWN = os.path.join(SPLASH_DIR, "shutdown.png")
FONT_NAME = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SCREEN_PLAYLISTS = "playlists"
SCREEN_CHANNELS = "channels"
SCREEN_PLAYING = "playing"
SCREEN_POWER = "power"       # power prompt (Shutdown / Standby)
SCREEN_STANDBY = "standby"   # black screen; any key wakes
SCREEN_RESUME = "resume"     # "Continue / Start over" prompt for a movie
SCREEN_SERIES = "series"     # list of series in the Series category
SCREEN_SEASONS = "seasons"   # seasons of a chosen series
SCREEN_EPISODES = "episodes" # episodes of a chosen season
SCREEN_LOADING = "loading"   # "Loading..." while fetching episodes
SCREEN_HELP = "help"         # instructions page
SCREEN_REMOTE = "remote_access"  # VPN QR + instructions
SCREEN_LEARN = "learn"       # IR remote learning wizard
SCREEN_WIFI_LIST = "wifi_list"      # pick a Wi-Fi network
SCREEN_WIFI_KEYBOARD = "wifi_kbd"   # on-screen keyboard for the password
SCREEN_WIFI_CONNECT = "wifi_connect"  # connecting / result
SCREEN_PIN = "pin"           # numeric PIN pad to open a protected profile


class App:
    def __init__(self):
        self.cfg = load_config()
        set_theme(self.cfg.theme)
        self.player = Player()
        self.remote = ir.RemoteInput()
        self.epg = EPG(self.cfg.epg_sources, self.cfg.epg_refresh_hours)
        # web remote + config editor shares the remote's action queue
        self.web = ControlServer(self.remote.q, port=WEB_PORT)

        self.screen_state = SCREEN_PLAYLISTS
        self.pl_index = 0          # selected playlist
        self._ox = 0               # left offset of the content (menu width)
        self.menu_focus = False    # keyboard focus is in the left menu
        self.menu_prof = None      # highlighted profile row in the menu
        self._loaded_profile = iptv_profiles.ACTIVE
        self._pin_target = None
        self._pin_buf = ""
        self._pin_idx = 4
        self._pin_msg = ""
        self.live_sel = 0          # selected live category tile
        self.section = self._load_ui_state()
        self.ch_index = 0          # selected channel
        self.current_playlist = None
        self.current_channel = None
        self.overlay_until = 0     # show info overlay while playing until this time
        self.message = ""
        self.message_until = 0
        self.movies = MovieState()
        # live-channel thumbnail scanning
        self.thumbs = iptv_thumbs.ThumbScanner()
        self.show_thumbs = False        # thumbnail grid view for current category
        self._thumb_surfaces = {}       # url -> pygame.Surface (loaded lazily)
        self._poster_surfaces = {}      # (url, w, h) -> scaled poster surface
        self.scan_menu = False          # "rescan all / only new" prompt open
        self.sm_index = 0               # selected item in the scan prompt
        # movie overlay menu state (only used while watching a movie)
        self.movie_menu = None     # None | "main" | "audio" | "subs" | "seek"
        self.mm_index = 0          # selected item in the movie menu
        self.seek_target = 0.0     # seconds, while in the seek screen
        self._movie_url = None     # url of the movie currently playing
        # IR-learn + Wi-Fi wizard state (safe defaults for the redraw signature)
        self._learn_idx = 0
        self._learn_msg = ""
        self._learn_map = {}
        self._wifi_index = 0
        self._wifi_scanning = False
        self._wifi_connecting = False
        self._wifi_password = ""
        self._wifi_status = ""
        self._kb_row = 0
        self._kb_col = 0
        self._kb_mode = "lower"
        self._last_saved_pos = 0.0
        # live TV pause/timeshift menu state
        # series navigation state
        self.series_index = 0      # selected series in the Series category
        self.season_index = 0      # selected season
        self.episode_index = 0     # selected episode
        self.current_series = None # {series_id, name, logo}
        self.current_seasons = []  # [{season, name, episodes:[...]}]
        self._series_channel_ctx = None  # remember which "channel" is the episode

        self._display_active = False
        # Start the web remote / config editor FIRST, so it works even when
        # no TV is connected yet.
        try:
            self.web.start()
            print("Web remote available on port %d" % WEB_PORT, flush=True)
        except Exception as e:
            print("Web server failed to start: %s" % e, flush=True)
        pygame.font.init()
        self._init_display()  # becomes active only if a TV is connected

    # -- setup -----------------------------------------------------------
    def _display_connected(self):
        """True if an HDMI output currently has a TV/monitor connected."""
        import glob
        for path in glob.glob("/sys/class/drm/*/status"):
            try:
                with open(path) as f:
                    if f.read().strip() == "connected":
                        return True
            except OSError:
                pass
        return False

    def _init_display(self):
        """Try to bring up the pygame/DRM display. Safe to call repeatedly.

        Returns True if the display is active. If no TV is connected we stay
        headless (the web remote/editor still works) and try again later.
        """
        if self._display_active:
            return True
        if not self._display_connected():
            return False
        # Hand the screen over from the boot splash (Plymouth holds DRM until
        # told to quit; --retain-splash keeps the image up until we draw).
        if not getattr(self, "_plymouth_done", False):
            self._plymouth_done = True
            try:
                import subprocess
                subprocess.run(["sudo", "/usr/bin/plymouth", "quit",
                                "--retain-splash"], timeout=5,
                               capture_output=True)
            except Exception:
                pass
        try:
            pygame.display.init()
            pygame.font.init()
            try:
                self.surface = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            except pygame.error:
                self.surface = pygame.display.set_mode((1280, 720))
            pygame.mouse.set_visible(False)
            self.W, self.H = self.surface.get_size()
            self._display_active = True
            self._load_fonts()
            self._last_sig = None  # force a fresh redraw
            self._show_image(SPLASH_BOOT)
            return True
        except Exception as e:
            print("Display init failed, staying headless: %s" % e, flush=True)
            try:
                pygame.display.quit()
            except Exception:
                pass
            self._display_active = False
            return False

    def _release_display(self):
        """Give up the DRM display so mpv can render fullscreen video.

        SDL's kmsdrm backend stays DRM *master* even after display.quit(),
        which stops mpv from becoming master (audio plays, no video). We
        explicitly DROP the DRM master on any /dev/dri fd we still hold, then
        close it, before launching mpv.
        """
        self._drop_drm_master_and_close()
        try:
            pygame.display.quit()
        except Exception:
            pass
        self._drop_drm_master_and_close()
        self._display_active = False

    def _drop_drm_master_and_close(self):
        """Drop DRM master and close any /dev/dri/card* fds this process holds."""
        import fcntl
        # DRM_IOCTL_DROP_MASTER = _IO('d', 0x1f) = 0x641f
        DRM_IOCTL_DROP_MASTER = 0x641f
        fd_dir = "/proc/self/fd"
        try:
            entries = os.listdir(fd_dir)
        except OSError:
            return
        for name in entries:
            try:
                target = os.readlink(os.path.join(fd_dir, name))
            except OSError:
                continue
            # card* nodes carry the DRM master; leave renderD* alone
            if target.startswith("/dev/dri/card"):
                fd = int(name)
                try:
                    fcntl.ioctl(fd, DRM_IOCTL_DROP_MASTER, 0)
                except OSError:
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _acquire_display(self):
        """Take the DRM display back for the menus (after video)."""
        return self._init_display()

    def _font(self, px, bold=False):
        key = (int(px), bool(bold))
        cache = self.__dict__.setdefault("_font_cache", {})
        f = cache.get(key)
        if f is None:
            f = pygame.font.Font(FONT_BOLD if bold else FONT_NAME, key[0])
            cache[key] = f
            self.__dict__.setdefault("_font_spec", {})[id(f)] = key
        return f

    def _fit(self, font, text, color, max_w, min_scale=0.65):
        """Render `text` so it fits in `max_w` pixels: shrink the font step by
        step (down to `min_scale`), and only if it still doesn't fit shorten
        it with an ellipsis."""
        text = str(text)
        max_w = max(20, int(max_w))
        if font.size(text)[0] <= max_w:
            return font.render(text, True, color)
        px, bold = self._font_spec.get(id(font), (font.get_height(), False))
        f = font
        scale = 0.95
        while scale >= min_scale - 1e-6:
            f = self._font(px * scale, bold)
            if f.size(text)[0] <= max_w:
                return f.render(text, True, color)
            scale -= 0.05
        while text and f.size(text + "\u2026")[0] > max_w:
            text = text[:-1]
        return f.render(text.rstrip() + "\u2026", True, color)

    def _wrap(self, font, text, max_w):
        """Split `text` into lines no wider than `max_w`."""
        lines, cur = [], ""
        lead = text[:len(text) - len(text.lstrip())]
        for word in text.split():
            trial = (cur + " " + word) if cur else lead + word
            if font.size(trial)[0] <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = lead + "  " + word if lead else word
        if cur:
            lines.append(cur)
        return lines or [""]

    def _load_fonts(self):
        s = self.cfg.text_scale
        base = self.H / 24.0
        self.f_title = self._font(base * 1.9 * s, bold=True)
        self.f_item = self._font(base * 1.3 * s, bold=True)
        self.f_count = self._font(base * 1.3 * s * 0.5)   # half the item size
        self.f_small = self._font(base * 0.95 * s)
        self.f_hint = self._font(base * 0.8)
        self.f_clock = self._font(base * 1.0)
        # smaller fonts for the thumbnail grid so 3 rows fit on a TV
        self.f_grid_header = self._font(base * 1.35 * s, bold=True)
        self.f_grid_label = self._font(base * 0.7 * s)

    # -- helpers ---------------------------------------------------------
    def notify(self, text, seconds=2.5):
        self.message = text
        self.message_until = time.time() + seconds

    def _live_channels_for_epg(self):
        """(tvg_id, name) for every channel in the live categories."""
        out = []
        for pl in self.cfg.playlists:
            if pl.is_movies or getattr(pl, "is_series", False):
                continue
            for c in pl.channels:
                out.append((c.tvg_id, c.name))
        return out

    def start_epg_thread(self):
        EPG_HOURS = 24.0

        def build_and_load(force=False):
            # (Re)build the automatic fuzzy mapping + filtered guide if it is
            # missing or older than 24h. Then (re)load the EPG.
            try:
                import iptv_epg
                if force or iptv_epg.map_age_hours() > EPG_HOURS:
                    live = self._live_channels_for_epg()
                    if live:
                        iptv_epg.build_auto_epg(live)
            except Exception as e:
                print("Auto-EPG build failed: %s" % e, flush=True)
            try:
                self.epg.refresh(force=force)
            except Exception as e:
                print("EPG refresh failed: %s" % e, flush=True)

        # initial load in the background (uses existing cache if fresh)
        threading.Thread(target=build_and_load, daemon=True).start()

        # periodic refresh every 24h, when idle (not watching)
        def loop():
            while True:
                time.sleep(EPG_HOURS * 3600)
                while self.screen_state == SCREEN_PLAYING:
                    time.sleep(300)
                build_and_load(force=True)
        threading.Thread(target=loop, daemon=True).start()

    def start_provider_refresh_thread(self):
        """Refresh the provider caches (channels / movies / series) every
        `refresh_hours` (the 'Refresh every (hours)' setting), but only while
        the device is idle (not watching). Everything else reads the cache,
        so this keeps searches and startup fast."""
        prov = (self.cfg._data.get("provider") or {})
        if not prov.get("enabled"):
            return

        def loop():
            from iptv_config import (fetch_provider_channels,
                                     fetch_provider_movies,
                                     fetch_provider_series,
                                     fetch_provider_limits)
            # connection limit: check at startup (if the saved value is old
            # or missing) and on every refresh below; the picture scan uses it
            try:
                fetch_provider_limits(
                    prov, max_age_hours=max(1.0, float(getattr(
                        self.cfg, "refresh_hours", 96))))
            except Exception as e:
                print("Provider limits check failed: %s" % e, flush=True)
            while True:
                hours = max(1.0, float(getattr(self.cfg, "refresh_hours", 96)))
                time.sleep(hours * 3600)
                # wait until the TV is idle (not watching) before refreshing
                while self.screen_state == SCREEN_PLAYING:
                    time.sleep(300)
                prov_now = self.cfg._data.get("provider") or {}
                if not prov_now.get("enabled"):
                    continue
                # Force-refresh each cache. Failures are non-fatal (kept cache).
                for label, fn in (("limits", fetch_provider_limits),
                                   ("channels", fetch_provider_channels),
                                   ("movies", fetch_provider_movies),
                                   ("series", fetch_provider_series)):
                    try:
                        fn(prov_now, force=True)
                    except Exception as e:
                        print("Provider %s refresh failed: %s" % (label, e),
                              flush=True)
                    # small gap so we don't hammer the provider back-to-back
                    time.sleep(10)
                # saved searches: add any new matches from the fresh lists
                try:
                    import iptv_watch
                    for pid in iptv_profiles.ids():
                        iptv_profiles.set_request(pid)
                        try:
                            iptv_watch.check()
                        finally:
                            iptv_profiles.clear_request()
                except Exception as e:
                    print("saved search check failed: %s" % e, flush=True)
                # re-expand categories/series from the fresh caches
                try:
                    self.reload_config()
                except Exception as e:
                    print("reload after refresh failed: %s" % e, flush=True)
        threading.Thread(target=loop, daemon=True).start()

    # -- navigation actions ---------------------------------------------
    def act(self, a):
        # Standby: any button wakes the box back up.
        if self.screen_state == SCREEN_STANDBY:
            self._wake_from_standby()
            return
        # Setup screens capture all buttons for their own navigation.
        if self.screen_state == SCREEN_LEARN:
            # The learn screen reads raw IR directly; only web/keyboard actions
            # reach here. MENU skips the current button, BACK cancels.
            if a == ir.MENU:
                self._learn_skip()
            elif a == ir.BACK:
                self._cancel_learn()
            return
        if self.screen_state == SCREEN_WIFI_LIST:
            self._act_wifi_list(a)
            return
        if self.screen_state == SCREEN_WIFI_KEYBOARD:
            self._act_wifi_keyboard(a)
            return
        if self.screen_state == SCREEN_WIFI_CONNECT:
            self._act_wifi_connect(a)
            return
        # Power button opens the Shutdown / Standby prompt from anywhere.
        if a == ir.POWER:
            if self.screen_state == SCREEN_PLAYING and self.is_movie_playing():
                self._save_movie_position()
            self.open_power_menu()
            return
        if self.screen_state == SCREEN_POWER:
            self._act_power(a)
            return
        if self.screen_state == SCREEN_RESUME:
            self._act_resume(a)
            return

        if a in (ir.VOL_UP,):
            self.player.volume_up(); self.notify("Volume +"); return
        if a in (ir.VOL_DOWN,):
            self.player.volume_down(); self.notify("Volume -"); return
        if a == "mute":
            self.player.toggle_mute(); self.notify("Mute"); return
        if self.screen_state == SCREEN_REMOTE:
            self.screen_state = SCREEN_POWER   # any key: back to the menu
            self._last_sig = None
            return
        if self.screen_state == SCREEN_HELP:
            self.screen_state = SCREEN_POWER   # any key: back to the menu
            self._last_sig = None
            return
        if self.screen_state == SCREEN_PIN:
            self._act_pin(a)
            return
        if self.screen_state in MENU_SCREENS:
            self._normalize_home()
            if self.menu_focus:
                self._act_menu(a)
                return
        if a == ir.HOME:
            if self.screen_state == SCREEN_PLAYING and self.is_movie_playing():
                self._save_movie_position()
            self.go_home(); return

        if self.screen_state == SCREEN_PLAYLISTS:
            self._act_playlists(a)
        elif self.screen_state == SCREEN_CHANNELS:
            self._act_channels(a)
        elif self.screen_state == SCREEN_SERIES:
            self._act_series(a)
        elif self.screen_state == SCREEN_SEASONS:
            self._act_seasons(a)
        elif self.screen_state == SCREEN_EPISODES:
            self._act_episodes(a)
        elif self.screen_state == SCREEN_LOADING:
            self._act_loading(a)
        elif self.screen_state == SCREEN_PLAYING:
            self._act_playing(a)

    def _save_movie_position(self):
        try:
            if self._movie_url:
                self.movies.save_position(
                    self._movie_url, self.player.time_pos(),
                    self.player.duration())
        except Exception:
            pass

    # -- left menu / sections ---------------------------------------------
    def _load_ui_state(self):
        try:
            import json
            with open(_ui_state_path(), "r", encoding="utf-8") as f:
                sec = json.load(f).get("section")
            if sec in SECTIONS:
                return sec
        except Exception:
            pass
        return "live"       # first run: Live is the only section with content

    def _save_ui_state(self):
        try:
            import json
            os.makedirs(os.path.dirname(_ui_state_path()), exist_ok=True)
            tmp = _ui_state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"section": self.section}, f)
            os.replace(tmp, _ui_state_path())
        except Exception:
            pass

    def _live_cats(self):
        return [i for i, p in enumerate(self.cfg.playlists)
                if not getattr(p, "is_movies", False)
                and not getattr(p, "is_series", False)]

    def _section_playlist(self, sec):
        for i, p in enumerate(self.cfg.playlists):
            if sec == "movies" and getattr(p, "is_movies", False):
                return i
            if sec == "series" and getattr(p, "is_series", False):
                return i
        return None

    def _enter_section(self):
        """Show the page of the selected menu section."""
        self.thumbs.stop()
        self.scan_menu = False
        if self.section == "live":
            cats = self._live_cats()
            self.live_sel = max(0, min(self.live_sel, len(cats) - 1))
            if cats:
                self.pl_index = cats[self.live_sel]
            self.screen_state = SCREEN_PLAYLISTS
        else:
            idx = self._section_playlist(self.section)
            if idx is None:
                self.section = "live"
                self.screen_state = SCREEN_PLAYLISTS
            else:
                self.pl_index = idx
                self.open_playlist(idx)
        self._last_sig = None

    def _normalize_home(self):
        """The start screen is the page of the selected section."""
        if self.screen_state == SCREEN_PLAYLISTS and self.section != "live":
            self._enter_section()

    def _menu_profiles(self):
        """Profiles shown above the QR code (only once an extra exists)."""
        if iptv_profiles.extra_count() <= 0:
            return []
        return iptv_profiles.list_profiles()

    def _act_menu(self, a):
        profs = self._menu_profiles()
        if self.menu_prof is not None and self.menu_prof >= len(profs):
            self.menu_prof = None
        ns = len(SECTIONS)
        if a in (ir.UP, ir.DOWN):
            pos = (ns + self.menu_prof) if self.menu_prof is not None \
                else SECTIONS.index(self.section)
            pos = (pos + (1 if a == ir.DOWN else -1)) % (ns + len(profs))
            if pos < ns:
                self.menu_prof = None
                self.section = SECTIONS[pos]
                self._save_ui_state()
                self._enter_section()
            else:
                self.menu_prof = pos - ns       # highlight only; OK switches
        elif a == ir.OK and self.menu_prof is not None:
            self._pick_profile(profs[self.menu_prof])
        elif a in (ir.OK, ir.RIGHT):
            self.menu_prof = None
            self.menu_focus = False
        elif a in (ir.BACK, ir.MENU, ir.HOME):
            self.open_power_menu()

    # -- profiles ------------------------------------------------------------
    def _pick_profile(self, p):
        if p["id"] == iptv_profiles.ACTIVE:
            self.menu_prof = None
            self.notify("Already using %s" % p["name"], 2)
            return
        if p["has_pin"]:
            self._pin_target = p
            self._pin_buf = ""
            self._pin_idx = 4
            self._pin_msg = ""
            self.screen_state = SCREEN_PIN
            return
        self._switch_profile(p["id"])

    def _switch_profile(self, pid):
        if not iptv_profiles.exists(pid):
            return
        iptv_profiles.ACTIVE = pid
        self._apply_profile()
        self.notify("Profile: %s" % iptv_profiles.name_of(pid), 3)

    def _apply_profile(self):
        """Load the active profile's settings, categories and watch state."""
        self.thumbs.stop()
        if self.player.is_running():
            self.player.stop()
            self._acquire_display()
        self._loaded_profile = iptv_profiles.ACTIVE
        try:
            self.cfg = load_config()
        except Exception as e:
            self.notify("Profile error: %s" % e, 5)
            return
        set_theme(self.cfg.theme)
        self._load_fonts()
        self.movies = MovieState()
        self._movie_url = None
        self.movie_menu = None
        self._poster_surfaces = {}
        self._thumb_surfaces = {}
        self.current_playlist = None
        self.current_series = None
        self.current_channel = None
        self.live_sel = self.ch_index = self.series_index = 0
        self.menu_prof = None
        self.menu_focus = True
        self.section = self._load_ui_state()
        self._enter_section()
        self._last_sig = None

    PIN_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "<", "0", "OK"]

    def _act_pin(self, a):
        i = self._pin_idx
        if a == ir.UP and i >= 3:
            self._pin_idx = i - 3
        elif a == ir.DOWN and i <= 8:
            self._pin_idx = i + 3
        elif a == ir.LEFT and i % 3 > 0:
            self._pin_idx = i - 1
        elif a == ir.RIGHT and i % 3 < 2:
            self._pin_idx = i + 1
        elif a == ir.BACK:
            self.screen_state = SCREEN_PLAYLISTS
            self._enter_section()
        elif a == ir.OK:
            k = self.PIN_KEYS[i]
            self._pin_msg = ""
            if k == "<":
                self._pin_buf = self._pin_buf[:-1]
            elif k == "OK":
                self._submit_pin()
            elif len(self._pin_buf) < 6:
                self._pin_buf += k

    def _submit_pin(self):
        p = self._pin_target
        if len(self._pin_buf) < 4:
            self._pin_msg = "The PIN has 4 to 6 digits"
            return
        try:
            ok = iptv_profiles.check_pin(p["id"], self._pin_buf)
        except PermissionError as e:
            self._pin_buf = ""
            self._pin_msg = str(e)
            return
        self._pin_buf = ""
        if not ok:
            self._pin_msg = "Wrong PIN"
            return
        self.screen_state = SCREEN_PLAYLISTS
        self._switch_profile(p["id"])

    def _act_playlists(self, a):
        """Live section: grid of category tiles."""
        cats = self._live_cats()
        if not cats:
            if a in (ir.BACK, ir.LEFT):
                self.menu_focus = True
            return
        r = self._poster_nav(a, self.live_sel, len(cats), cols=self.LIVE_COLS)
        if r is None:
            self.menu_focus = True
        elif r == "ok":
            self.pl_index = cats[self.live_sel]
            self.open_playlist(self.pl_index)
        else:
            self.live_sel = r
            self.pl_index = cats[r]

    def _act_channels(self, a):
        chans = self.current_playlist.channels
        n = len(chans)
        if n == 0:
            if a in (ir.BACK, ir.LEFT):
                if self.current_playlist.is_movies:
                    self.menu_focus = True
                else:
                    self.screen_state = SCREEN_PLAYLISTS
            return
        if self.current_playlist.is_movies:
            r = self._poster_nav(a, self.ch_index, n)
            if r is None:
                self.menu_focus = True
            elif r == "ok":
                self._grid_play = None
                self.play_channel(self.ch_index)
            else:
                self.ch_index = r
            return
        # The scan-choice prompt takes priority when open.
        if self.scan_menu:
            self._act_scan_menu(a)
            return
        if a == ir.MENU:
            # Menu opens a small prompt: rescan all, or only new/empty ones.
            self.scan_menu = True
            self.sm_index = 0
            return
        if self.show_thumbs:
            # picture grid: every channel is shown (uploaded picture, grabbed
            # frame or colored square); the selection is ch_index.
            cols = self._thumb_cols()
            gi = self.ch_index = max(0, min(self.ch_index, n - 1))
            r = self._poster_nav(a, gi, n, cols=cols)
            if r is None:                  # Back / Left on first column
                self.thumbs.stop()
                self.screen_state = SCREEN_PLAYLISTS   # category tiles
            elif r == "ok":
                self._grid_play = None
                self.play_channel(gi)
            else:
                self.ch_index = r
            return
        # plain list view
        if a == ir.UP:
            self.ch_index = (self.ch_index - 1) % n
        elif a == ir.DOWN:
            self.ch_index = (self.ch_index + 1) % n
        elif a in (ir.OK,):
            self._grid_play = None
            self.play_channel(self.ch_index)
        elif a in (ir.BACK, ir.LEFT):
            self.thumbs.stop()
            self.screen_state = SCREEN_PLAYLISTS

    SCAN_MENU = ["Rescan all channels", "Only old / missing pictures"]

    def _act_scan_menu(self, a):
        n = len(self.SCAN_MENU)
        if a == ir.UP:
            self.sm_index = (self.sm_index - 1) % n
        elif a == ir.DOWN:
            self.sm_index = (self.sm_index + 1) % n
        elif a in (ir.BACK, ir.MENU):
            self.scan_menu = False
        elif a == ir.OK:
            force = (self.sm_index == 0)   # "Rescan all" = force
            self.scan_menu = False
            self.show_thumbs = True
            self._start_thumb_scan(force=force)

    def _start_thumb_scan(self, force=False, quiet=False):
        urls = [c.url for c in self.current_playlist.channels]
        # Remove the thumbnails that will be re-grabbed first, so stale
        # pictures don't linger: all of them for "Rescan all", only the old
        # (> 1 hour) ones otherwise - fresh ones are kept and skipped.
        for u in urls:
            if force or iptv_thumbs.needs_scan(u):
                try:
                    os.remove(iptv_thumbs.thumb_path(u))
                except OSError:
                    pass
        self._thumb_surfaces = {}
        self.thumbs.start(urls, force=force)
        if quiet:
            return
        if self.thumbs.total > 0:
            self.notify("Scanning %d channels..." % self.thumbs.total, 3)
        else:
            self.notify("Thumbnails ready", 2)

    LIVE_COLS = 5

    def _resume_scan(self):
        """Back in a live picture grid after watching: continue the scan.
        Fresh pictures are skipped, so it picks up where it was stopped."""
        pl = self.current_playlist
        if (pl and self.show_thumbs and self._is_live_category()
                and pl.channels and not self.thumbs.is_scanning()):
            self._start_thumb_scan(force=False, quiet=True)

    def _thumb_cols(self):
        return self.LIVE_COLS

    # -- series navigation (series -> seasons -> episodes) --------------
    def _act_series(self, a):
        series = self.current_playlist.series
        n = len(series)
        if n == 0:
            if a in (ir.BACK, ir.LEFT):
                self.menu_focus = True
            return
        r = self._poster_nav(a, self.series_index, n)
        if r is None:
            self.menu_focus = True
        elif r == "ok":
            self.open_series(self.series_index)
        else:
            self.series_index = r

    POSTER_COLS = 6

    def _poster_nav(self, a, i, n, cols=None):
        """Move in the poster grid. Returns the new index, "ok", None (leave
        the grid: Back, or Left on the first column) or i (unchanged)."""
        cols = cols or self.POSTER_COLS
        if a == ir.UP:
            return i - cols if i - cols >= 0 else i
        if a == ir.DOWN:
            return min(n - 1, i + cols) if i + cols < n or i // cols < (n - 1) // cols else i
        if a == ir.LEFT:
            return None if i % cols == 0 else i - 1
        if a == ir.RIGHT:
            return min(n - 1, i + 1)
        if a == ir.OK:
            return "ok"
        if a == ir.BACK:
            return None
        return i

    def open_series(self, idx):
        # Don't start a second fetch while one is already running (the provider
        # only allows one connection; piling up requests stalls everything).
        if getattr(self, "_series_fetching", False):
            return
        self.current_series = self.current_playlist.series[idx]
        self._loading_label = self.current_series["name"]
        self._loading_result = None      # ("ok", data) / ("err", msg) / ("cancel", None)
        self.screen_state = SCREEN_LOADING
        sid = self.current_series["series_id"]
        prov = self.cfg._data.get("provider") or {}
        max_age = getattr(self.cfg, "refresh_hours", 96)
        self._series_fetching = True

        def worker():
            try:
                from iptv_config import fetch_series_episodes
                data = fetch_series_episodes(prov, sid, max_age_hours=max_age)
                self._loading_result = ("ok", data)
            except Exception as e:
                self._loading_result = ("err", str(e))
            finally:
                self._series_fetching = False
        threading.Thread(target=worker, daemon=True).start()

    def _finish_open_series(self, result):
        """Called on the main thread once the episode fetch finishes."""
        kind, payload = result
        if kind == "err":
            self.screen_state = SCREEN_SERIES
            self.notify("Could not load series (try again in a minute)", 5)
            return
        self.current_seasons = payload.get("seasons", [])
        if not self.current_seasons:
            self.screen_state = SCREEN_SERIES
            self.notify("No episodes found for this series", 4)
            return
        self.season_index = 0
        # If there's only one season, jump straight to episodes.
        if len(self.current_seasons) == 1:
            self.open_season(0, back=SCREEN_SERIES)
        else:
            self.screen_state = SCREEN_SEASONS

    def _act_loading(self, a):
        # Allow the user to back out of a slow load.
        if a in (ir.BACK, ir.HOME):
            self._loading_result = ("cancel", None)
            self.screen_state = SCREEN_SERIES

    def _act_seasons(self, a):
        n = len(self.current_seasons)
        if a == ir.UP:
            self.season_index = (self.season_index - 1) % n
        elif a == ir.DOWN:
            self.season_index = (self.season_index + 1) % n
        elif a in (ir.BACK, ir.LEFT):
            self.screen_state = SCREEN_SERIES
        elif a == ir.OK:
            self.open_season(self.season_index)

    def open_season(self, idx, back=SCREEN_SEASONS):
        self.season_index = idx
        self._season_back = back
        self.episode_index = 0
        self.screen_state = SCREEN_EPISODES

    def _act_episodes(self, a):
        eps = self.current_seasons[self.season_index]["episodes"]
        n = len(eps)
        if n == 0:
            if a in (ir.BACK, ir.LEFT):
                self.thumbs.stop()
                self.screen_state = SCREEN_PLAYLISTS
            return
        if a == ir.UP:
            self.episode_index = (self.episode_index - 1) % n
        elif a == ir.DOWN:
            self.episode_index = (self.episode_index + 1) % n
        elif a in (ir.BACK, ir.LEFT):
            self.screen_state = getattr(self, "_season_back", SCREEN_SEASONS)
        elif a == ir.OK:
            self.play_episode(self.episode_index)

    def play_episode(self, idx):
        ep = self.current_seasons[self.season_index]["episodes"][idx]
        # Build a lightweight "channel" so playing code can reuse everything.
        from iptv_config import Channel
        label = "%s - %s" % (self.current_series["name"], ep["title"])
        self.current_channel = Channel(label, ep["url"])
        # Episodes behave like movies: resume + track prefs.
        rec = self.movies.get_position(ep["url"])
        if rec and rec.get("pos", 0) > 15:
            self.resume_rec = rec
            self.resume_index = 0
            self._episode_return = SCREEN_EPISODES
            self.screen_state = SCREEN_RESUME
            return
        self._start_movie(start=0)

    def is_movie_playing(self):
        # Movies category OR a series episode both get the rich controls.
        if self.current_playlist and self.current_playlist.is_movies:
            return True
        if self.current_playlist and getattr(self.current_playlist,
                                             "is_series", False):
            return self._movie_url is not None
        return False


    def _act_playing(self, a):
        # Movies and series episodes get a rich pause/menu experience; live
        if self.is_movie_playing():
            self._act_movie(a)
            return
        # Live TV: simple direct controls (no menu).
        #   OK    -> pause / resume
        #   Right -> skip 30s forward
        #   Left  -> skip 30s back
        #   Up/Dn -> change channel   Back/Menu -> channel list
        chans = self.current_playlist.channels
        if a in (ir.BACK, ir.MENU):
            self.player.stop()
            self._acquire_display()
            self.screen_state = SCREEN_CHANNELS
            self._resume_scan()
        elif a in (ir.UP, ir.DOWN):
            step = -1 if a == ir.UP else 1
            order = getattr(self, "_grid_play", None)
            if order and self.ch_index in order:
                # started from the picture grid: only channels with a picture
                pos = (order.index(self.ch_index) + step) % len(order)
                self.ch_index = order[pos]
                self._grid_index = pos
            else:
                self.ch_index = (self.ch_index + step) % len(chans)
            self.play_channel(self.ch_index)
        elif a == ir.OK:
            self.player.toggle_pause()
        elif a == ir.RIGHT:
            self.player.set_property("pause", False)
            self.player.seek_relative(30)
        elif a == ir.LEFT:
            self.player.set_property("pause", False)
            self.player.seek_relative(-30)

    # -- movie playback controls (OSD menu drawn by mpv) ----------------
    def _act_movie(self, a):
        if self.movie_menu is None:
            # Watching normally. OK pauses and opens the menu.
            if a == ir.OK:
                self.player.set_property("pause", True)
                self._open_movie_menu()
            elif a in (ir.BACK, ir.MENU):
                self._exit_movie(to_channels=True)
            elif a == ir.LEFT:
                self.player.seek_relative(-30)
                self.player.osd_message("\u23EA 30s")
            elif a == ir.RIGHT:
                self.player.seek_relative(30)
                self.player.osd_message("\u23E9 30s")
            return

        if self.movie_menu == "main":
            self._act_movie_main(a)
        elif self.movie_menu == "audio":
            self._act_movie_tracklist(a, "audio")
        elif self.movie_menu == "subs":
            self._act_movie_tracklist(a, "subs")
        elif self.movie_menu == "seek":
            self._act_movie_seek(a)

    MOVIE_MAIN = ["Resume", "Audio track", "Subtitles", "Jump to time", "Stop"]

    def _open_movie_menu(self):
        self.movie_menu = "main"
        self.mm_index = 0
        self._draw_movie_menu()

    def _close_movie_menu(self, resume=True):
        self.movie_menu = None
        self.player.osd_clear()
        if resume:
            self.player.set_property("pause", False)

    def _act_movie_main(self, a):
        n = len(self.MOVIE_MAIN)
        if a == ir.UP:
            self.mm_index = (self.mm_index - 1) % n
            self._draw_movie_menu()
        elif a == ir.DOWN:
            self.mm_index = (self.mm_index + 1) % n
            self._draw_movie_menu()
        elif a in (ir.BACK, ir.MENU):
            self._close_movie_menu(resume=True)
        elif a == ir.OK:
            item = self.MOVIE_MAIN[self.mm_index]
            if item == "Resume":
                self._close_movie_menu(resume=True)
            elif item == "Audio track":
                self.movie_menu = "audio"; self.mm_index = 0
                self._draw_tracklist("audio")
            elif item == "Subtitles":
                self.movie_menu = "subs"; self.mm_index = 0
                self._draw_tracklist("subs")
            elif item == "Jump to time":
                self.movie_menu = "seek"
                self.seek_target = self.player.time_pos()
                self._draw_seek()
            elif item == "Stop":
                self._exit_movie(to_channels=True)

    def _audio_options(self):
        return self.player.audio_tracks()

    def _sub_options(self):
        # subtitle list plus an explicit "Off" entry
        return self.player.sub_tracks()

    def _act_movie_tracklist(self, a, kind):
        if kind == "audio":
            opts = self._audio_options()
            labels = [self._track_label(t) for t in opts]
        else:
            opts = self._sub_options()
            labels = ["Off"] + [self._track_label(t) for t in opts]
        n = max(1, len(labels))
        if a == ir.UP:
            self.mm_index = (self.mm_index - 1) % n
            self._draw_tracklist(kind)
        elif a == ir.DOWN:
            self.mm_index = (self.mm_index + 1) % n
            self._draw_tracklist(kind)
        elif a in (ir.BACK,):
            self.movie_menu = "main"; self.mm_index = 0
            self._draw_movie_menu()
        elif a == ir.OK:
            if kind == "audio":
                if opts:
                    t = opts[self.mm_index]
                    self.player.set_audio(t["id"])
                    self.movies.set_audio_lang(t.get("lang") or "")
                    self.player.osd_message("Audio: " + self._track_label(t))
            else:
                if self.mm_index == 0:
                    self.player.set_sub("no")
                    self.movies.set_sub_pref("", False)
                    self.player.osd_message("Subtitles off")
                else:
                    t = opts[self.mm_index - 1]
                    self.player.set_sub(t["id"])
                    self.movies.set_sub_pref(t.get("lang") or "", True)
                    self.player.osd_message("Subtitles: " + self._track_label(t))
            self.movie_menu = "main"; self.mm_index = 0
            self._draw_movie_menu()

    def _act_movie_seek(self, a):
        dur = self.player.duration() or 0
        step = max(30, dur * 0.02)  # ~2% per press, min 30s
        if a == ir.LEFT:
            self.seek_target = max(0, self.seek_target - step)
            self._draw_seek()
        elif a == ir.RIGHT:
            self.seek_target = min(dur, self.seek_target + step)
            self._draw_seek()
        elif a in (ir.BACK,):
            self.movie_menu = "main"; self.mm_index = 3
            self._draw_movie_menu()
        elif a == ir.OK:
            self.player.seek_to(self.seek_target)
            self._close_movie_menu(resume=True)

    def _exit_movie(self, to_channels=True):
        # save resume position before stopping
        try:
            pos = self.player.time_pos()
            dur = self.player.duration()
            if self._movie_url:
                self.movies.save_position(self._movie_url, pos, dur)
        except Exception:
            pass
        self.movie_menu = None
        self._movie_url = None
        self.player.stop()
        self._acquire_display()
        # Series episodes go back to the episode list; movies to the movie
        # list; anything else to the home menu.
        if self.current_playlist and getattr(self.current_playlist,
                                             "is_series", False):
            self.screen_state = SCREEN_EPISODES
        elif to_channels:
            self.screen_state = SCREEN_CHANNELS
        else:
            self.screen_state = SCREEN_PLAYLISTS

    # -- movie OSD drawing (rendered by mpv over the paused frame) -------
    @staticmethod
    def _track_label(t):
        lang = (t.get("lang") or "").upper()
        title = t.get("title") or ""
        parts = [p for p in (lang, title) if p]
        return " - ".join(parts) if parts else ("Track %s" % t.get("id"))

    @staticmethod
    def _ass_escape(s):
        return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")

    def _ass_menu(self, title, items, sel):
        lines = [r"{\an5}{\fs42}{\b1}%s{\b0}" % self._ass_escape(title), r"{\fs34}"]
        # show at most 9 items, scrolled so the selection stays visible
        vis = 9
        start = 0
        if len(items) > vis:
            start = max(0, min(sel - vis // 2, len(items) - vis))
        if start > 0:
            lines.append(r"{\c&HA0A0A0&}" + "\u25b2" + r" %d more{\c&HFFFFFF&}" % start)
        for i, it in list(enumerate(items))[start:start + vis]:
            if i == sel:
                lines.append(r"{\c&H4CC85A&}> %s{\c&HFFFFFF&}"
                             % self._ass_escape(it))
            else:
                lines.append("  " + self._ass_escape(it))
        rest = len(items) - (start + vis)
        if len(items) > vis and rest > 0:
            lines.append(r"{\c&HA0A0A0&}" + "\u25bc" + r" %d more{\c&HFFFFFF&}" % rest)
        return r"\N".join(lines)

    def _draw_movie_menu(self):
        ass = self._ass_menu("Movie", self.MOVIE_MAIN, self.mm_index)
        self.player.osd_show(ass)

    def _draw_tracklist(self, kind):
        if kind == "audio":
            opts = self._audio_options()
            labels = [self._track_label(t) for t in opts]
            title = "Audio track"
            if not labels:
                labels = ["(only one track)"]
            # highlight current
        else:
            opts = self._sub_options()
            labels = ["Off"] + [self._track_label(t) for t in opts]
            title = "Subtitles"
        ass = self._ass_menu(title, labels, self.mm_index)
        self.player.osd_show(ass)

    def _draw_seek(self):
        dur = self.player.duration() or 0
        cur = self.seek_target
        bar_len = 30
        filled = int(bar_len * (cur / dur)) if dur else 0
        bar = "[" + "=" * filled + "-" * (bar_len - filled) + "]"
        title = "Jump to time"
        body = (r"{\an5}{\fs42}{\b1}%s{\b0}\N{\fs34}%s\N%s / %s"
                r"\N{\fs26}Left / Right to move, OK to jump" % (
                    title, bar, _fmt_time(cur), _fmt_time(dur)))
        self.player.osd_show(body)


    def go_home(self):
        self.thumbs.stop()
        self.player.stop()
        self._acquire_display()
        self.screen_state = SCREEN_PLAYLISTS
        self.menu_focus = True

    # -- power menu / standby -------------------------------------------
    def open_power_menu(self):
        self.thumbs.stop()
        self.player.stop()
        self._acquire_display()
        self.power_index = 0  # 0=Standby, 1=Shut down, 2=Cancel
        self._return_screen = (self.screen_state
                               if self.screen_state in (SCREEN_PLAYLISTS,
                                                        SCREEN_CHANNELS,
                                                        SCREEN_SERIES)
                               else SCREEN_PLAYLISTS)
        self.screen_state = SCREEN_POWER

    POWER_OPTIONS = ["Add a remote",
                     "Wi-Fi setup",
                     "Remote access",
                     "Instructions",
                     "Standby (screen off)",
                     "Power off",
                     "Cancel"]

    def _act_power(self, a):
        n = len(self.POWER_OPTIONS)
        if a == ir.UP:
            self.power_index = (self.power_index - 1) % n
        elif a == ir.DOWN:
            self.power_index = (self.power_index + 1) % n
        elif a in (ir.BACK, ir.HOME, ir.MENU):
            self.screen_state = self._return_screen
        elif a == ir.OK:
            choice = self.POWER_OPTIONS[self.power_index]
            if choice == "Add a remote":
                self.open_learn_remote()
            elif choice == "Wi-Fi setup":
                self.open_wifi_setup(reason="menu")
            elif choice == "Instructions":
                self.screen_state = SCREEN_HELP
            elif choice == "Remote access":
                self._open_remote_access()
            elif choice.startswith("Standby"):
                self.enter_standby()
            elif choice == "Power off":
                self.shutdown()
            else:
                self.screen_state = self._return_screen
        self._last_sig = None

    def enter_standby(self):
        self.player.stop()
        self.screen_state = SCREEN_STANDBY
        self._last_sig = None

    def _wake_from_standby(self):
        self._acquire_display()
        self.screen_state = SCREEN_PLAYLISTS
        self._last_sig = None
        self.notify("Welcome back", 2)

    def _show_image(self, path):
        """Draw a full-screen image (splash) right away. Returns True if shown."""
        try:
            if not self._display_active or not os.path.exists(path):
                return False
            img = pygame.image.load(path)
            img = pygame.transform.smoothscale(img, (self.W, self.H))
            self.surface.blit(img, (0, 0))
            pygame.display.flip()
            return True
        except Exception as e:
            print("splash failed: %s" % e, flush=True)
            return False

    def shutdown(self):
        self.player.stop()
        self._acquire_display()
        if not self._show_image(SPLASH_SHUTDOWN):
            self.notify("Shutting down...", 5)
            self._draw()
            if self._display_active:
                pygame.display.flip()
        self._shutting_down = True
        import subprocess
        try:
            subprocess.Popen(["sudo", "poweroff"])
        except Exception as e:
            print("poweroff failed: %s" % e, flush=True)

    # ==================================================================
    #  IR remote learning wizard
    # ==================================================================
    def open_learn_remote(self):
        """Start the learn-remote wizard. Captures raw IR so it works even
        when no remote currently controls the box."""
        self.thumbs.stop()
        self.player.stop()
        self._acquire_display()
        self._learn_return = (self.screen_state
                              if self.screen_state in (SCREEN_PLAYLISTS,)
                              else SCREEN_PLAYLISTS)
        self._learn_idx = 0
        self._learn_map = {}          # action -> (protocol, scancode)
        self._learn_msg = ""
        self._learn_done = False
        self._learner = ir.RemoteLearner(self.remote)
        self.remote.pause()           # don't let presses drive the UI
        if not self._learner.start():
            self.remote.resume()
            self.notify("Could not start IR capture", 4)
            return
        self._learner.flush()
        self.screen_state = SCREEN_LEARN
        self._last_sig = None

    def _learn_poll(self):
        """Called from the main loop while on the learn screen. Grabs the next
        captured press and advances the wizard."""
        if self.screen_state != SCREEN_LEARN or self._learn_done:
            return
        press = self._learner.next_press(timeout=0)
        if not press:
            return
        proto, scan = press
        action, label, keycode = ir.LEARN_BUTTONS_FULL[self._learn_idx]
        # reject a scancode already used for another button
        # Pressing the already-learned OK button skips this step (handy for
        # small remotes that lack some buttons, e.g. the Apple Remote).
        ok_entry = self._learn_map.get("ok")
        if ok_entry and scan == ok_entry[1]:
            self._learn_skip()
            return
        used = {s for (_p, s, _k) in self._learn_map.values()}
        if scan in used:
            self._learn_msg = "Already used - try a different button"
            self._last_sig = None
            return
        self._learn_map[action] = (proto, scan, keycode)
        self._learn_idx += 1
        self._learn_msg = ""
        if self._learn_idx >= len(ir.LEARN_BUTTONS_FULL):
            self._finish_learn()
        else:
            self._learner.flush()
        self._last_sig = None

    def _learn_skip(self):
        """Skip the current button (not all remotes have every key)."""
        if self.screen_state != SCREEN_LEARN:
            return
        self._learn_idx += 1
        self._learn_msg = ""
        if self._learn_idx >= len(ir.LEARN_BUTTONS_FULL):
            self._finish_learn()
        else:
            self._learner.flush()
        self._last_sig = None

    def _finish_learn(self):
        self._learn_done = True
        self._learner.stop()
        codes = {scan: action
                 for action, (_proto, scan, _key) in self._learn_map.items()}
        if not codes:
            self.remote.resume()
            self.notify("No buttons learned", 4)
            self.screen_state = self._learn_return
            self._last_sig = None
            return
        remotes = ir.load_learned()
        names = {r.get("name") for r in remotes}
        n = 1
        while "Remote %d" % n in names:
            n += 1
        remotes.append({"name": "Remote %d" % n, "codes": codes})
        try:
            ir.save_learned(remotes)
            ok = True
        except Exception as e:
            print("save remotes failed: %s" % e, flush=True)
            ok = False
        self.remote.reload_learned()
        self.remote.resume()
        self.notify("Remote saved" if ok else "Could not save remote", 4)
        # After learning, if there is no network, go straight to Wi-Fi setup.
        if not iptv_net.has_network():
            self.open_wifi_setup(reason="no-network")
        else:
            self.screen_state = self._learn_return
        self._last_sig = None

    def _cancel_learn(self):
        try:
            self._learner.stop()
        except Exception:
            pass
        # restore the saved keymap + its protocols (we had enabled "all")
        try:
            iptv_net.load_keymap_live()
        except Exception:
            pass
        self.remote.resume()
        self.screen_state = getattr(self, "_learn_return", SCREEN_PLAYLISTS)
        self._last_sig = None

    # ==================================================================
    #  Wi-Fi setup wizard
    # ==================================================================
    def open_wifi_setup(self, reason=""):
        self.thumbs.stop()
        self.player.stop()
        self._acquire_display()
        self._wifi_return = (self.screen_state
                             if self.screen_state in (SCREEN_PLAYLISTS,)
                             else SCREEN_PLAYLISTS)
        self._wifi_networks = []
        self._wifi_index = 0
        self._wifi_ssid = ""
        self._wifi_secured = True
        self._wifi_scanning = True
        self._wifi_scan_result = None
        self.screen_state = SCREEN_WIFI_LIST
        self._last_sig = None
        self._start_wifi_scan()

    def _start_wifi_scan(self):
        self._wifi_scanning = True
        self._wifi_scan_result = None
        self._last_sig = None

        def worker():
            nets = iptv_net.scan_wifi(rescan=True)
            saved = iptv_net.saved_ssids()
            for npt in nets:
                npt["saved"] = npt["ssid"] in saved
            self._wifi_scan_result = nets
        threading.Thread(target=worker, daemon=True).start()

    def _wifi_poll(self):
        """Pick up a finished background scan/connect from the main loop."""
        if self.screen_state == SCREEN_WIFI_LIST and self._wifi_scanning \
                and self._wifi_scan_result is not None:
            self._wifi_networks = self._wifi_scan_result
            self._wifi_scan_result = None
            self._wifi_scanning = False
            self._wifi_index = 0
            self._last_sig = None
        if self.screen_state == SCREEN_WIFI_CONNECT \
                and getattr(self, "_wifi_connect_result", None) is not None:
            ok, msg = self._wifi_connect_result
            self._wifi_connect_result = None
            self._wifi_connecting = False
            if ok:
                self._wifi_status = "Connected!"
                self.notify("Wi-Fi connected", 3)
                # done - leave setup
                self.screen_state = getattr(self, "_wifi_return",
                                            SCREEN_PLAYLISTS)
            else:
                self._wifi_status = msg or "Could not connect"
            self._last_sig = None

    WIFI_LIST_EXTRA = ["Rescan", "Back"]

    def _act_wifi_list(self, a):
        if self._wifi_scanning:
            if a in (ir.BACK,):
                self.screen_state = getattr(self, "_wifi_return",
                                            SCREEN_PLAYLISTS)
                self._last_sig = None
            return
        total = len(self._wifi_networks) + len(self.WIFI_LIST_EXTRA)
        if a == ir.UP:
            self._wifi_index = (self._wifi_index - 1) % total
            self._last_sig = None
        elif a == ir.DOWN:
            self._wifi_index = (self._wifi_index + 1) % total
            self._last_sig = None
        elif a in (ir.BACK,):
            self.screen_state = getattr(self, "_wifi_return", SCREEN_PLAYLISTS)
            self._last_sig = None
        elif a == ir.OK:
            if self._wifi_index < len(self._wifi_networks):
                net = self._wifi_networks[self._wifi_index]
                self._wifi_ssid = net["ssid"]
                self._wifi_secured = net["secured"]
                if net.get("saved"):
                    self._begin_wifi_connect(use_saved=True)
                elif not net["secured"]:
                    self._begin_wifi_connect(password="")
                else:
                    self._open_wifi_keyboard()
            else:
                extra = self._wifi_index - len(self._wifi_networks)
                if self.WIFI_LIST_EXTRA[extra] == "Rescan":
                    self._start_wifi_scan()
                else:
                    self.screen_state = getattr(self, "_wifi_return",
                                                SCREEN_PLAYLISTS)
                    self._last_sig = None

    # -- on-screen keyboard (QWERTY) ------------------------------------
    _KB_LOWER = [
        list("1234567890"),
        list("qwertyuiop"),
        list("asdfghjkl"),
        list("zxcvbnm"),
        ["SHIFT", "SYM", "SPACE", "DEL", "DONE"],
    ]
    _KB_UPPER = [
        list("1234567890"),
        list("QWERTYUIOP"),
        list("ASDFGHJKL"),
        list("ZXCVBNM"),
        ["SHIFT", "SYM", "SPACE", "DEL", "DONE"],
    ]
    _KB_SYM = [
        list("!@#$%^&*()"),
        list("-_=+[]{}"),
        list(";:'\",.<>"),
        list("/?\\|`~"),
        ["abc", "SPACE", "DEL", "DONE"],
    ]

    def _kb_layout(self):
        if self._kb_mode == "sym":
            return self._KB_SYM
        if self._kb_mode == "upper":
            return self._KB_UPPER
        return self._KB_LOWER

    def _open_wifi_keyboard(self):
        self._wifi_password = ""
        self._kb_mode = "lower"
        self._kb_row = 0
        self._kb_col = 0
        self._kb_show = False
        self.screen_state = SCREEN_WIFI_KEYBOARD
        self._last_sig = None

    def _act_wifi_keyboard(self, a):
        rows = self._kb_layout()
        self._kb_row = min(self._kb_row, len(rows) - 1)
        row = rows[self._kb_row]
        self._kb_col = min(self._kb_col, len(row) - 1)
        if a == ir.UP:
            self._kb_row = (self._kb_row - 1) % len(rows)
            self._kb_col = min(self._kb_col, len(rows[self._kb_row]) - 1)
        elif a == ir.DOWN:
            self._kb_row = (self._kb_row + 1) % len(rows)
            self._kb_col = min(self._kb_col, len(rows[self._kb_row]) - 1)
        elif a == ir.LEFT:
            self._kb_col = (self._kb_col - 1) % len(row)
        elif a == ir.RIGHT:
            self._kb_col = (self._kb_col + 1) % len(row)
        elif a == ir.BACK:
            # Back = delete a character (quick), or leave if empty
            if self._wifi_password:
                self._wifi_password = self._wifi_password[:-1]
            else:
                self.screen_state = SCREEN_WIFI_LIST
        elif a == ir.OK:
            self._kb_press(row[self._kb_col])
        self._last_sig = None

    def _kb_press(self, key):
        if key == "SHIFT":
            self._kb_mode = "lower" if self._kb_mode == "upper" else "upper"
            self._kb_row = 0
            self._kb_col = 0
        elif key in ("SYM",):
            self._kb_mode = "sym"
            self._kb_row = 0
            self._kb_col = 0
        elif key == "abc":
            self._kb_mode = "lower"
            self._kb_row = 0
            self._kb_col = 0
        elif key == "SPACE":
            self._wifi_password += " "
        elif key == "DEL":
            self._wifi_password = self._wifi_password[:-1]
        elif key == "DONE":
            self._begin_wifi_connect(password=self._wifi_password)
        else:
            self._wifi_password += key

    # -- connecting -----------------------------------------------------
    def _begin_wifi_connect(self, password=None, use_saved=False):
        self._wifi_connecting = True
        self._wifi_connect_result = None
        self._wifi_status = "Connecting to %s ..." % self._wifi_ssid
        self.screen_state = SCREEN_WIFI_CONNECT
        self._last_sig = None
        ssid = self._wifi_ssid

        def worker():
            if use_saved:
                ok, msg = iptv_net.connect_saved(ssid)
                if not ok:
                    # saved profile failed - fall through to asking password
                    ok, msg = (False, msg)
            else:
                ok, msg = iptv_net.connect_wifi(ssid, password)
            self._wifi_connect_result = (ok, msg)
        threading.Thread(target=worker, daemon=True).start()

    def _act_wifi_connect(self, a):
        if self._wifi_connecting:
            return  # ignore input while connecting
        # result shown: OK/Back to retry or go back
        if a in (ir.OK, ir.BACK):
            if self._wifi_secured:
                self._open_wifi_keyboard()
            else:
                self.screen_state = SCREEN_WIFI_LIST
                self._start_wifi_scan()
            self._last_sig = None

    def reload_config(self):
        """Re-read config.yaml (after the web editor saved it)."""
        self.player.stop()
        self._acquire_display()
        try:
            self.cfg = load_config()
        except Exception as e:
            self.notify("Config error: %s" % e, 5)
            return
        set_theme(self.cfg.theme)
        self._load_fonts()
        self.epg = EPG(self.cfg.epg_sources, self.cfg.epg_refresh_hours)
        self.pl_index = 0
        self.ch_index = 0
        self.current_playlist = None
        self.current_channel = None
        self.screen_state = SCREEN_PLAYLISTS
        self.menu_focus = False
        self.live_sel = 0
        self._enter_section()
        self.notify("Settings updated", 3)
        # rebuild the automatic EPG mapping for the (possibly changed) channel
        # set, then reload the guide - all in the background.
        def rebuild_epg():
            try:
                import iptv_epg
                live = self._live_channels_for_epg()
                if live:
                    iptv_epg.build_auto_epg(live)
            except Exception as e:
                print("EPG rebuild after reload failed: %s" % e, flush=True)
            try:
                self.epg.refresh()
            except Exception:
                pass
        threading.Thread(target=rebuild_epg, daemon=True).start()

    def open_playlist(self, idx):
        # Stop any thumbnail scan from the previous category so it doesn't keep
        # running in the background (and block the new category's scan).
        self.thumbs.stop()
        self.current_playlist = self.cfg.playlists[idx]
        self._grid_play = None
        self.ch_index = 0
        self.show_thumbs = self._is_live_category()   # live: picture grid
        self.scan_menu = False
        self._thumb_surfaces = {}
        if getattr(self.current_playlist, "is_series", False):
            self.series_index = 0
            self.screen_state = SCREEN_SERIES
        else:
            self.screen_state = SCREEN_CHANNELS
            if self.show_thumbs and self.current_playlist.channels:
                # live category: refresh only old / missing pictures
                self._start_thumb_scan(force=False, quiet=True)

    def _is_live_category(self):
        pl = self.current_playlist
        return bool(pl and not pl.is_movies
                    and not getattr(pl, "is_series", False))

    def _thumbs_ready_ratio(self):
        chans = self.current_playlist.channels
        if not chans:
            return 0.0
        have = sum(1 for c in chans if iptv_thumbs.has_thumb(c.url))
        return have / len(chans)

    def _play_on_tv(self, req):
        """"Play on TV" from the web app: start a channel / movie / episode."""
        if not req:
            return
        if self.screen_state == SCREEN_STANDBY:
            self._wake_from_standby()
        if self.screen_state in (SCREEN_LEARN, SCREEN_WIFI_LIST,
                                 SCREEN_WIFI_KEYBOARD, SCREEN_WIFI_CONNECT,
                                 SCREEN_REMOTE, SCREEN_HELP):
            self.notify("Finish the setup on the TV first", 4)
            return
        if self.screen_state == SCREEN_PLAYING and self.is_movie_playing():
            self._save_movie_position()
        want = req.get("profile") or iptv_profiles.DEFAULT
        if want != iptv_profiles.ACTIVE and iptv_profiles.exists(want):
            self._switch_profile(want)          # the web app already checked the PIN
        self._force_play = True
        try:
            self._play_on_tv_inner(req)
        finally:
            self._force_play = False

    def _play_on_tv_inner(self, req):
        url, kind = req["url"], req["kind"]
        pls = self.cfg.playlists
        self.menu_focus = False
        self.scan_menu = False
        if kind == "episode":
            sid = req.get("sid", "")
            pi = self._section_playlist("series")
            ser = None
            if pi is not None:
                ser = next((x for x in pls[pi].series
                            if str(x["series_id"]) == sid), None)
            if ser is None:
                self.notify("Series not found on the TV", 4)
                return
            try:
                from iptv_config import fetch_series_episodes
                data = fetch_series_episodes(
                    self.cfg._data.get("provider") or {}, sid,
                    max_age_hours=getattr(self.cfg, "refresh_hours", 96))
            except Exception:
                self.notify("Could not load the series", 4)
                return
            seasons = data.get("seasons", [])
            for si, se in enumerate(seasons):
                for ei, ep in enumerate(se["episodes"]):
                    if ep["url"] == url:
                        self.current_playlist = pls[pi]
                        self.current_series = ser
                        self.current_seasons = seasons
                        self.series_index = pls[pi].series.index(ser)
                        self.open_season(si, back=(SCREEN_SEASONS
                                                   if len(seasons) > 1
                                                   else SCREEN_SERIES))
                        self.section = "series"
                        self._save_ui_state()
                        self._play_from_saved(ep["title"], url, ep, ser)
                        return
            self.notify("Episode not found", 4)
            return
        for pi, pl in enumerate(pls):
            if bool(getattr(pl, "is_series", False)):
                continue
            if bool(pl.is_movies) != (kind == "movie"):
                continue
            for ci, c in enumerate(pl.channels):
                if c.url == url:
                    self.pl_index = pi
                    self.current_playlist = pl
                    self.ch_index = ci
                    self._grid_play = None
                    self.show_thumbs = not pl.is_movies
                    self.section = "movies" if pl.is_movies else "live"
                    self._save_ui_state()
                    if pl.is_movies:
                        self.current_channel = c
                        self._episode_return = SCREEN_CHANNELS
                        rec = self.movies.get_position(c.url)
                        pos = int(rec.get("pos", 0)) if rec else 0
                        self._start_movie(start=pos if pos > 15 else 0)
                    else:
                        self.play_channel(ci)
                    return
        self.notify("Not found on the TV", 4)

    def _play_from_saved(self, title, url, ep, ser):
        from iptv_config import Channel
        self.current_channel = Channel("%s - %s" % (ser["name"], title), url)
        self._episode_return = SCREEN_EPISODES
        rec = self.movies.get_position(url)
        pos = int(rec.get("pos", 0)) if rec else 0
        self._start_movie(start=pos if pos > 15 else 0)

    def _stream_busy(self):
        """True (and tells the user) if a phone/computer is streaming."""
        if getattr(self, "_force_play", False):
            return False        # "Play on TV": the web app already took over
        if iptv_stream.busy():
            self.notify("Busy - playing on another device", 4)
            return True
        return False

    def play_channel(self, idx):
        if self._stream_busy():
            return
        self.current_channel = self.current_playlist.channels[idx]
        # Movies: if we have a saved position, ask whether to continue.
        if self.is_movie_playing():
            self._episode_return = SCREEN_CHANNELS
            rec = self.movies.get_position(self.current_channel.url)
            if rec and rec.get("pos", 0) > 15:
                self.resume_rec = rec
                self.resume_index = 0  # 0 = Continue, 1 = Start over
                self.screen_state = SCREEN_RESUME
                return
            self._start_movie(start=0)
            return
        self._idle_tries = 0
        self._idle_since = 0
        self._launch(self.current_channel.url, live=True)
        self.screen_state = SCREEN_PLAYING
        self.overlay_until = 0

    def _launch(self, url, **kwargs):
        """Show a loading screen, release DRM, then start mpv."""
        # Apply the configured live-TV audio-sync offset (seconds) to live
        # streams only. Movies/series keep the stream's own timing.
        if kwargs.get("live") and "audio_delay" not in kwargs:
            ad = getattr(self.cfg, "audio_delay", 0.0)
            if ad:
                kwargs["audio_delay"] = ad
        # Stop any thumbnail scan so it doesn't fight the player for the
        # provider's single allowed connection.
        self.thumbs.stop()
        was_playing = self.player.is_running()
        if not was_playing and self._display_active:
            self.surface.fill(BG)
            msg = self.f_title.render("Loading %s ..." % self.current_channel.name,
                                      True, TEXT)
            self.surface.blit(msg, (self.W // 2 - msg.get_width() // 2,
                                    self.H // 2 - msg.get_height() // 2))
            pygame.display.flip()
            time.sleep(0.3)
            self._release_display()
        self.player.play(url, **kwargs)

    def _start_movie(self, start=0):
        if self._stream_busy():
            if self.screen_state in (SCREEN_RESUME, SCREEN_PLAYING):
                self.screen_state = getattr(self, "_episode_return",
                                            SCREEN_CHANNELS)
            return
        url = self.current_channel.url
        self._movie_url = url
        self.movie_menu = None
        self._launch(
            url,
            start=start,
            alang=self.movies.audio_lang,
            slang=self.movies.sub_lang,
            want_subs=self.movies.subs_on,
        )
        self.screen_state = SCREEN_PLAYING
        self.overlay_until = 0
        self._last_saved_pos = time.time()

    # -- resume prompt --------------------------------------------------
    def _act_resume(self, a):
        if a in (ir.UP, ir.DOWN):
            self.resume_index ^= 1
        elif a in (ir.BACK,):
            self.screen_state = getattr(self, "_episode_return", SCREEN_CHANNELS)
        elif a == ir.OK:
            if self.resume_index == 0:
                self._start_movie(start=int(self.resume_rec.get("pos", 0)))
            else:
                self.movies.clear_position(self.current_channel.url)
                self._start_movie(start=0)

    # -- drawing ---------------------------------------------------------
    def _grid_cols(self):
        n = len(self.cfg.playlists)
        if n <= 3:
            return 1
        if n <= 8:
            return 2
        return 3

    def _draw(self):
        if not getattr(self, "_display_active", True):
            return  # mpv owns the screen while playing
        if self.screen_state == SCREEN_STANDBY:
            self.surface.fill((0, 0, 0))
            hint = self.f_small.render("Press any button to turn on",
                                       True, (60, 64, 74))
            self.surface.blit(hint, (self.W // 2 - hint.get_width() // 2,
                                     int(self.H * 0.9)))
            return
        self.surface.fill(BG)
        self._ox = 0
        if self.screen_state in MENU_SCREENS:
            self._normalize_home()
            self._draw_menu()
        if self.screen_state == SCREEN_PLAYLISTS:
            self._draw_playlists()
        elif self.screen_state == SCREEN_CHANNELS:
            self._draw_channels()
        elif self.screen_state == SCREEN_POWER:
            self._draw_power()
        elif self.screen_state == SCREEN_RESUME:
            self._draw_resume()
        elif self.screen_state == SCREEN_SERIES:
            ser = self.current_playlist.series
            self._draw_poster_grid(
                "", len(ser),
                lambda k: (ser[k]["name"], ser[k].get("logo", ""), False),
                self.series_index, "OK to open  -  Left for menu",
                empty="No series added yet.", focused=not self.menu_focus)
        elif self.screen_state == SCREEN_SEASONS:
            self._draw_list(self.current_series["name"],
                            [s["name"] for s in self.current_seasons],
                            self.season_index,
                            "OK to open  -  Back to series",
                            green=[bool(s["episodes"]) and all(
                                self.movies.is_watched(e["url"])
                                for e in s["episodes"])
                                for s in self.current_seasons])
        elif self.screen_state == SCREEN_EPISODES:
            eps = self.current_seasons[self.season_index]["episodes"]
            title = "%s - %s" % (self.current_series["name"],
                                 self.current_seasons[self.season_index]["name"])
            self._draw_list(title, [self._short_episode(e["title"]) for e in eps],
                            self.episode_index,
                            "OK to play  -  Back to seasons",
                            empty="No episodes.",
                            green=[self.movies.is_watched(e["url"])
                                   for e in eps])
        elif self.screen_state == SCREEN_LOADING:
            self._draw_loading()
        elif self.screen_state == SCREEN_HELP:
            self._draw_help()
        elif self.screen_state == SCREEN_REMOTE:
            self._draw_remote_access()
        elif self.screen_state == SCREEN_LEARN:
            self._draw_learn()
        elif self.screen_state == SCREEN_WIFI_LIST:
            self._draw_wifi_list()
        elif self.screen_state == SCREEN_WIFI_KEYBOARD:
            self._draw_wifi_keyboard()
        elif self.screen_state == SCREEN_PIN:
            self._draw_pin()
        elif self.screen_state == SCREEN_WIFI_CONNECT:
            self._draw_wifi_connect()
        self._draw_message()

    def _draw_loading(self):
        label = getattr(self, "_loading_label", "")
        self._draw_header(label)
        # animated dots
        dots = "." * (1 + int(time.time() * 2) % 3)
        msg = self.f_title.render("Loading episodes" + dots, True, TEXT)
        self.surface.blit(msg, (self.W // 2 - msg.get_width() // 2,
                                int(self.H * 0.45)))
        self._draw_hint("Please wait  -  Back to cancel")

    def _draw_list(self, title, items, sel, hint, empty="Nothing here.",
                   green=None):
        """Generic vertical list (used for series / seasons / episodes)."""
        self._draw_header(title)
        if not items:
            msg = self.f_item.render(empty, True, DIM)
            self.surface.blit(msg, (int(self.W * 0.06), int(self.H * 0.4)))
            self._draw_hint(hint)
            return
        top = int(self.H * 0.18)
        row_h = int(self.H * 0.11)
        visible = max(1, int((self.H * 0.7) // row_h))
        start = max(0, min(sel - visible // 2, max(0, len(items) - visible)))
        left = int(self.W * 0.06)
        w = int(self.W * 0.88)
        for row, i in enumerate(range(start, min(start + visible, len(items)))):
            y = top + row * row_h
            is_sel = (i == sel)
            color = SELECT if is_sel else PANEL
            pygame.draw.rect(self.surface, color, (left, y, w, row_h - 12),
                             border_radius=14)
            tcol = SELECT_TEXT if is_sel else TEXT
            if green and green[i]:
                tcol = (150, 240, 170) if is_sel else ACCENT
            t = self._fit(self.f_item, items[i], tcol, w - 56)
            self.surface.blit(t, (left + 28,
                                  y + (row_h - 12) // 2 - t.get_height() // 2))
        self._draw_hint(hint)

    def _poster_surface(self, src, w, h, cover=False):
        """Scaled picture for a tile, or None. `src` is a provider poster URL
        (cached on disk by iptv_posters) or a local file path."""
        if not src:
            return None
        if src.startswith("/"):
            path = src
        elif iptv_posters.has_poster(src):
            path = iptv_posters.poster_path(src)
        else:
            return None
        try:
            mt = os.path.getmtime(path)
        except OSError:
            return None
        key = (path, w, h, cover)
        hit = self._poster_surfaces.get(key)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            img = pygame.image.load(path).convert()
            iw, ih = img.get_size()
            if cover:
                k = max(w / iw, h / ih)
                sc = pygame.transform.smoothscale(
                    img, (max(w, int(iw * k)), max(h, int(ih * k))))
                surf = pygame.Surface((w, h))
                surf.blit(sc, ((w - sc.get_width()) // 2,
                               (h - sc.get_height()) // 2))
            else:
                k = min(w / iw, h / ih)
                surf = pygame.transform.smoothscale(
                    img, (max(1, int(iw * k)), max(1, int(ih * k))))
        except Exception:
            return None
        if len(self._poster_surfaces) > 200:
            self._poster_surfaces.clear()
        self._poster_surfaces[key] = (mt, surf)
        return surf

    def _text_tile(self, rect, name, bg, fg):
        """A tile without a picture: filled box with the wrapped title."""
        pygame.draw.rect(self.surface, bg, rect, border_radius=8)
        x, y, w, h = rect
        lines, cur = [], ""
        for wd in name.split():
            t = (cur + " " + wd).strip()
            if self.f_grid_label.size(t)[0] <= w - 16 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        lh = self.f_grid_label.get_height()
        shown = max(1, min(len(lines), 6, (h - 8) // lh))
        yy = y + max(4, (h - lh * shown) // 2)
        for ln in lines[:shown]:
            t = self._fit(self.f_grid_label, ln, fg, w - 12)
            self.surface.blit(t, (x + (w - t.get_width()) // 2, yy))
            yy += lh

    def _draw_poster_grid(self, title, n, get, sel, hint, empty="Nothing here.",
                          cols=6, rows=2, aspect=2 / 3, colored=False,
                          cover=False, focused=True, subtitle=""):
        """Grid of tiles. get(i) -> (name, picture, watched); picture is a
        provider URL, a local file path or ''. Colored/dark text tile when
        there is no picture."""
        head = title.strip()
        if head:
            self._draw_header(head, font=self.f_grid_header)
        if subtitle:     # status text, bottom left (e.g. picture scan progress)
            st = self._fit(self.f_hint, subtitle, DIM, (self.W - self._ox) * 0.6)
            self.surface.blit(st, (self._ox + int(self.W * 0.03),
                                   self.H - st.get_height() - 20))
        left0 = self._ox
        avail_w = self.W - left0
        if n == 0:
            msg = self.f_item.render(empty, True, DIM)
            self.surface.blit(msg, (left0 + int(self.W * 0.03),
                                    int(self.H * 0.4)))
            self._draw_hint(hint)
            return
        top = int(self.H * (0.16 if head else 0.06))
        gap = int(self.W * 0.014)
        vgap = int(self.H * 0.025)
        area_w = int(avail_w * 0.94)
        area_h = int(self.H * 0.80) - top
        label_h = int(self.H * 0.045)
        cell_h = (area_h - vgap * (rows - 1)) // rows
        img_h = cell_h - label_h
        img_w = int(img_h * aspect)
        if img_w * cols + gap * (cols - 1) > area_w:
            img_w = (area_w - gap * (cols - 1)) // cols
            img_h = int(img_w / aspect)
        total_w = img_w * cols + gap * (cols - 1)
        left = left0 + (avail_w - total_w) // 2
        per_page = cols * rows
        sel = max(0, min(sel, n - 1))
        start = (sel // per_page) * per_page
        page = [get(k) for k in range(start, min(n, start + per_page))]
        # download this page and the next one in the background
        iptv_posters.request([get(k)[1] for k in
                              range(start, min(n, start + per_page * 2))
                              if get(k)[1].startswith("http")])
        for rel, (name, logo, watched) in enumerate(page):
            pos = start + rel
            x = left + (rel % cols) * (img_w + gap)
            y = top + (rel // cols) * (cell_h + vgap)
            is_sel = focused and (pos == sel)
            pygame.draw.rect(self.surface, SELECT if is_sel else PANEL,
                             (x - 4, y - 4, img_w + 8, img_h + label_h + 8),
                             border_radius=10)
            tile = pygame.Rect(x, y, img_w, img_h)
            surf = self._poster_surface(logo, img_w, img_h, cover)
            if surf is not None:
                self.surface.blit(surf, (x + (img_w - surf.get_width()) // 2,
                                         y + (img_h - surf.get_height()) // 2))
            elif colored:
                self._text_tile(tile, name, iptv_livepics.color_for(name),
                                SELECT_TEXT)
            else:
                self._text_tile(tile, name, TILE_BG, DIM)
            if watched:
                pygame.draw.rect(self.surface, ACCENT, tile, width=4,
                                 border_radius=8)
            col = SELECT_TEXT if is_sel else (ACCENT if watched else TEXT)
            t = self._fit(self.f_grid_label, name, col, img_w - 4)
            self.surface.blit(t, (x + 2, y + img_h + 4))
        pages = (n + per_page - 1) // per_page
        if pages > 1:
            cur = start // per_page + 1
            t = self.f_hint.render("%d/%d" % (cur, pages), True, DIM)
            m = int(self.W * 0.03)
            ty = self.H - t.get_height() - 20
            tx = self.W - m - t.get_width()
            self.surface.blit(t, (tx, ty))
            ah = max(8, t.get_height() // 2)
            cy = ty + t.get_height() // 2
            if cur < pages:      # arrow to the right: more pages ahead
                x0 = tx + t.get_width() + 8
                pts = [(x0, cy - ah // 2), (x0, cy + ah // 2), (x0 + ah, cy)]
                pygame.draw.polygon(self.surface, DIM, pts)
            if cur > 1:          # arrow to the left: pages behind
                x0 = tx - 8
                pts = [(x0, cy - ah // 2), (x0, cy + ah // 2), (x0 - ah, cy)]
                pygame.draw.polygon(self.surface, DIM, pts)

    def _draw_resume(self):
        self._draw_header(self.current_channel.name)
        pos = int(self.resume_rec.get("pos", 0))
        sub = self.f_small.render("You stopped at %s" % _fmt_time(pos),
                                  True, DIM)
        self.surface.blit(sub, (int(self.W * 0.06), int(self.H * 0.22)))
        options = ["Continue watching", "Start from the beginning"]
        top = int(self.H * 0.36)
        row_h = int(self.H * 0.14)
        left = int(self.W * 0.10)
        w = int(self.W * 0.80)
        for i, label in enumerate(options):
            y = top + i * row_h
            sel = (i == self.resume_index)
            color = SELECT if sel else PANEL
            pygame.draw.rect(self.surface, color, (left, y, w, row_h - 16),
                             border_radius=14)
            tcol = SELECT_TEXT if sel else TEXT
            t = self.f_item.render(label, True, tcol)
            self.surface.blit(t, (left + 30,
                                  y + (row_h - 16) // 2 - t.get_height() // 2))
        self._draw_hint("Up / Down to choose  -  OK to confirm  -  Back to cancel")

    def _draw_power(self):
        self._draw_header("Menu")
        top = int(self.H * 0.20)
        row_h = int(min(self.H * 0.13, self.H * 0.68 / len(self.POWER_OPTIONS)))
        left = int(self.W * 0.10)
        w = int(self.W * 0.80)
        for i, label in enumerate(self.POWER_OPTIONS):
            y = top + i * row_h
            sel = (i == self.power_index)
            color = SELECT if sel else PANEL
            pygame.draw.rect(self.surface, color, (left, y, w, row_h - 14),
                             border_radius=14)
            tcol = SELECT_TEXT if sel else TEXT
            t = self._fit(self.f_item, label, tcol, w - 60)
            self.surface.blit(t, (left + 30,
                                  y + (row_h - 14) // 2 - t.get_height() // 2))
        self._draw_hint("Up / Down to choose  -  OK to confirm  -  Back to cancel")

    HELP_LINES = [
        "Arrows: move around      OK: open / play",
        "BACK: go back one step      HOME: back to the start page",
        "MENU, BACK or HOME on the start page: open this menu",
        "",
        "While watching live TV:",
        "  OK pause / resume      LEFT / RIGHT skip 30 s      UP / DOWN change channel",
        "While watching a movie or episode:",
        "  OK opens the menu (audio, subtitles, jump to time, stop)",
        "In a channel list: MENU refreshes the channel pictures",
        "",
        "POWER on the remote switches the box off and on.",
        "Settings: scan the QR code on the start page with a phone.",
        "New place / no Wi-Fi: Menu > Wi-Fi setup.  New remote: Menu > Add a remote.",
    ]

    def _draw_help(self):
        self._draw_header("Instructions")
        max_w = self.W * 0.88
        avail = self.H * 0.70
        px, bold = self._font_spec.get(id(self.f_small),
                                       (self.f_small.get_height(), False))
        scale = 1.0
        while True:
            f = self._font(px * scale, bold)
            lines = []
            for line in self.HELP_LINES:
                lines += self._wrap(f, line, max_w) if line else [""]
            step = int(f.get_linesize() * 1.15)
            if step * len(lines) <= avail or scale <= 0.55:
                break
            scale -= 0.05
        y = int(self.H * 0.17)
        for line in lines:
            if line:
                self.surface.blit(f.render(line, True, TEXT),
                                  (int(self.W * 0.06), y))
            y += step
        self._draw_hint("Press any button to go back")

    def _short_episode(self, t):
        """Drop the series name the provider repeats in every episode title
        ("NF - Cold Case ... - S01E01 - ..." -> "S01E01 - ...")."""
        name = (self.current_series or {}).get("name", "")
        t = str(t)
        if name and t.lower().startswith(name.lower()):
            rest = t[len(name):].lstrip(" -:|")
            if rest:
                return rest
        return t

    def _open_remote_access(self):
        self.thumbs.stop()
        self._acquire_display()
        self._return_screen = getattr(self, "_return_screen", SCREEN_PLAYLISTS)
        try:
            import iptv_remote
            self._ra_status = iptv_remote.status()
        except Exception:
            self._ra_status = {}
        self.screen_state = SCREEN_REMOTE
        self._last_sig = None

    def _matrix_surface(self, text, target_px):
        import qrcode
        q = qrcode.QRCode(border=2, box_size=1,
                          error_correction=qrcode.constants.ERROR_CORRECT_L)
        q.add_data(text)
        q.make(fit=True)
        m = q.get_matrix()
        n = len(m)
        sc = max(1, target_px // n)
        surf = pygame.Surface((n * sc, n * sc))
        surf.fill((255, 255, 255))
        for r, row in enumerate(m):
            for c, v in enumerate(row):
                if v:
                    pygame.draw.rect(surf, (0, 0, 0), (c * sc, r * sc, sc, sc))
        return surf

    def _draw_remote_access(self):
        self._draw_header("Remote access")
        st = getattr(self, "_ra_status", {}) or {}
        last = getattr(self.web, "last_vpn_conf", None)
        fresh = last and time.time() - last[2] < 1800
        left = int(self.W * 0.06)
        if fresh:
            qr = self._matrix_surface(last[1], int(self.H * 0.62))
            x = self.W - qr.get_width() - int(self.W * 0.05)
            pygame.draw.rect(self.surface, (255, 255, 255),
                             (x - 8, int(self.H * 0.17) - 8,
                              qr.get_width() + 16, qr.get_height() + 16),
                             border_radius=8)
            self.surface.blit(qr, (x, int(self.H * 0.17)))
            text_w = x - left - 40
        else:
            text_w = self.W * 0.88
        dd = st.get("duckdns") or {}
        lines = []
        if fresh:
            lines += ["New device: %s" % last[0],
                      "Scan the code with the WireGuard app:",
                      "  + > Create from QR code", ""]
        else:
            lines += ["To add a phone: open the web app (scan the QR code",
                      "on the start page) > Settings > Remote access >",
                      "Add device. Its code is then shown here.", ""]
        lines += ["Status: %s" % ("ON" if st.get("running") else "OFF"),
                  "Home address: %s" % (dd.get("domain") or "not set"),
                  "Router: forward UDP %s to %s" % (st.get("port", 51821),
                                                    st.get("lan_ip", "")),
                  "",
                  "Away from home: switch WireGuard on and open",
                  "  http://10.66.0.1:8080"]
        y = int(self.H * 0.17)
        step = int(self.f_small.get_linesize() * 1.25)
        for line in lines:
            if line:
                t = self._fit(self.f_small, line, TEXT, text_w)
                self.surface.blit(t, (left, y))
            y += step
        self._draw_hint("Press any button to go back")

    def _draw_header(self, title, font=None):
        t = self._fit(font or self.f_title, title, TEXT,
                      (self.W - self._ox) * 0.78)
        self.surface.blit(t, (self._ox + int(self.W * 0.03 if self._ox
                                             else self.W * 0.06),
                              int(self.H * 0.05)))

    def _draw_clock(self):
        if not self.cfg.show_clock:
            return
        s = time.strftime("%H:%M")
        t = self.f_clock.render(s, True, DIM)
        self.surface.blit(t, (self.W - t.get_width() - int(self.W * 0.04),
                              int(self.H * 0.05)))

    def _draw_hint(self, text):
        t = self._fit(self.f_hint, text, DIM, (self.W - self._ox) * 0.9)
        self.surface.blit(t, (self._ox + int(self.W * 0.03 if self._ox
                                             else self.W * 0.06),
                              self.H - t.get_height() - 20))

    def _blit_center(self, surf, cy):
        self.surface.blit(surf, (self.W // 2 - surf.get_width() // 2, cy))

    # -- learn remote wizard drawing ------------------------------------
    def _draw_learn(self):
        self._draw_header("Set up a remote")
        total = len(ir.LEARN_BUTTONS_FULL)
        idx = min(self._learn_idx, total - 1)
        action, label, keycode = ir.LEARN_BUTTONS_FULL[idx]
        # progress
        prog = self.f_small.render("Button %d of %d" % (idx + 1, total),
                                   True, DIM)
        self._blit_center(prog, int(self.H * 0.26))
        # instruction
        instr = self.f_item.render("Point the remote at the box and press:",
                                   True, TEXT)
        self._blit_center(instr, int(self.H * 0.37))
        big = self.f_title.render(label, True, ACCENT)
        self._blit_center(big, int(self.H * 0.46))
        if self._learn_msg:
            m = self.f_small.render(self._learn_msg, True, (230, 170, 80))
            self._blit_center(m, int(self.H * 0.60))
        # learned-so-far ticks
        done = self.f_small.render(
            "Learned: %d    (remote lacks this button? press OK to skip it)"
            % len(self._learn_map), True, DIM)
        self._blit_center(done, int(self.H * 0.70))
        self._draw_hint("Press each button once and wait for the next prompt.")

    # -- wifi list drawing ----------------------------------------------
    def _draw_wifi_list(self):
        self._draw_header("Connect to Wi-Fi")
        if self._wifi_scanning:
            dots = "." * (1 + int(time.time() * 2) % 3)
            msg = self.f_item.render("Searching for networks" + dots, True, TEXT)
            self._blit_center(msg, int(self.H * 0.45))
            self._draw_hint("Back to cancel")
            return
        items = []
        for n in self._wifi_networks:
            lock = "\U0001F512 " if n["secured"] else ""
            star = "  \u2713" if n.get("in_use") else ""
            saved = "  (saved)" if n.get("saved") else ""
            bars = self._signal_bars(n["signal"])
            items.append("%s%s%s%s   %s" % (lock, n["ssid"], saved, star, bars))
        items += list(self.WIFI_LIST_EXTRA)
        self._draw_simple_list(items, self._wifi_index, top=0.18)
        self._draw_hint("Up/Down to choose  -  OK to select  -  Back to exit")

    def _signal_bars(self, signal):
        n = 1 + min(4, max(0, signal // 20))
        return "\u2588" * n + "\u2581" * (5 - n)

    def _draw_simple_list(self, items, sel, top=0.18):
        if not items:
            msg = self.f_item.render("Nothing found.", True, DIM)
            self.surface.blit(msg, (int(self.W * 0.06), int(self.H * 0.4)))
            return
        y0 = int(self.H * top)
        row_h = int(self.H * 0.10)
        left = int(self.W * 0.06)
        w = int(self.W * 0.88)
        visible = max(1, int((self.H * 0.72) // row_h))
        start = max(0, min(sel - visible // 2, max(0, len(items) - visible)))
        for i in range(start, min(start + visible, len(items))):
            y = y0 + (i - start) * row_h
            selected = (i == sel)
            if selected:
                pygame.draw.rect(self.surface, SELECT, (left, y, w, row_h - 8),
                                 border_radius=12)
            col = SELECT_TEXT if selected else TEXT
            t = self._fit(self.f_item, items[i], col, w - 48)
            self.surface.blit(t, (left + 24, y + (row_h - 8) // 2
                                  - t.get_height() // 2))

    # -- wifi keyboard drawing ------------------------------------------
    def _draw_wifi_keyboard(self):
        self._draw_header("Enter Wi-Fi password")
        ssid = self.f_small.render("Network: %s" % self._wifi_ssid, True, DIM)
        self.surface.blit(ssid, (int(self.W * 0.06), int(self.H * 0.17)))
        # password field
        shown = self._wifi_password if self._kb_show \
            else "\u2022" * len(self._wifi_password)
        fld_y = int(self.H * 0.24)
        pygame.draw.rect(self.surface, PANEL,
                         (int(self.W * 0.06), fld_y, int(self.W * 0.88),
                          int(self.H * 0.09)), border_radius=10)
        pw = self.f_item.render(shown + "_", True, TEXT)
        self.surface.blit(pw, (int(self.W * 0.08), fld_y + int(self.H * 0.015)))
        # keyboard grid
        rows = self._kb_layout()
        ky = int(self.H * 0.38)
        kh = int(self.H * 0.11)
        grid_w = int(self.W * 0.88)
        left = int(self.W * 0.06)
        for r, row in enumerate(rows):
            n = len(row)
            cw = grid_w // max(1, n)
            for c, key in enumerate(row):
                x = left + c * cw
                y = ky + r * kh
                selected = (r == self._kb_row and c == self._kb_col)
                col = SELECT if selected else PANEL
                pygame.draw.rect(self.surface, col,
                                 (x + 4, y + 4, cw - 8, kh - 10),
                                 border_radius=8)
                lbl = {"SPACE": "space", "DEL": "\u232b", "DONE": "Done",
                       "SHIFT": "\u21e7", "SYM": "123#", "abc": "abc"}.get(
                           key, key)
                tcol = SELECT_TEXT if selected else TEXT
                t = self.f_item.render(lbl, True, tcol)
                self.surface.blit(t, (x + cw // 2 - t.get_width() // 2,
                                      y + (kh - 10) // 2 - t.get_height() // 2 + 4))
        self._draw_hint("Arrows to move  -  OK to type  -  Back to delete  -  "
                        "pick Done to connect")

    # -- wifi connecting drawing ----------------------------------------
    def _draw_wifi_connect(self):
        self._draw_header("Wi-Fi")
        if self._wifi_connecting:
            dots = "." * (1 + int(time.time() * 2) % 3)
            msg = self.f_item.render(self._wifi_status + dots, True, TEXT)
            self._blit_center(msg, int(self.H * 0.45))
            return
        status = getattr(self, "_wifi_status", "")
        ok = status.lower().startswith("connected")
        col = ACCENT if ok else (230, 120, 110)
        msg = self.f_title.render(status, True, col)
        self._blit_center(msg, int(self.H * 0.42))
        if not ok:
            hint = self.f_small.render("Press OK to try again", True, DIM)
            self._blit_center(hint, int(self.H * 0.56))


    def _start_url_sig(self):
        """Current web-app URL, but only on the start page (where the QR is
        shown) and refreshed at most once every few seconds. Included in the
        redraw signature so a changed IP re-renders the QR promptly."""
        if self.screen_state not in MENU_SCREENS:
            return ""
        now = time.time()
        if now - getattr(self, "_url_sig_checked", 0) > 3:
            self._url_sig_checked = now
            self._url_sig = web_app_url()
        return getattr(self, "_url_sig", "")

    def _qr_surface(self, target_px):
        """Return a cached pygame surface with a QR code for the web app URL."""
        url = web_app_url()
        cache = getattr(self, "_qr_cache", None)
        dark = (self.cfg.theme == "dark")
        if (cache and cache[0] == url and abs(cache[1] - target_px) < 4
                and cache[3] == dark):
            return cache[2]
        bg, fg = (MENU_BG, TEXT) if dark else ((255, 255, 255), (0, 0, 0))
        try:
            import qrcode
            q = qrcode.QRCode(border=2, box_size=1,
                              error_correction=qrcode.constants.ERROR_CORRECT_M)
            q.add_data(url)
            q.make(fit=True)
            matrix = q.get_matrix()
        except Exception:
            return None
        n = len(matrix)
        scale = max(1, target_px // n)
        size = n * scale
        surf = pygame.Surface((size, size))
        surf.fill(bg)
        for r, rowvals in enumerate(matrix):
            for c, val in enumerate(rowvals):
                if val:
                    pygame.draw.rect(surf, fg,
                                     (c * scale, r * scale, scale, scale))
        self._qr_cache = (url, target_px, surf, dark)
        return surf

    def _menu_w(self):
        return int(self.W * 0.17)

    def _draw_menu(self):
        """Thin permanent menu on the left: Live / Movies / Series, QR below."""
        mw = self._menu_w()
        pygame.draw.rect(self.surface, MENU_BG, (0, 0, mw, self.H))
        pygame.draw.line(self.surface, PANEL, (mw - 1, 0), (mw - 1, self.H), 2)
        pad = int(self.W * 0.01)
        row_h = int(self.H * 0.115)
        y = int(self.H * 0.06)
        counts = {"live": "%d categories" % len(self._live_cats())}
        for sec in ("movies", "series"):
            pi = self._section_playlist(sec)
            if pi is None:
                counts[sec] = ""
                continue
            pl = self.cfg.playlists[pi]
            k = len(pl.series) if sec == "series" else len(pl.channels)
            counts[sec] = "%d %s" % (k, sec) if k else "empty"
        for sec in SECTIONS:
            cur = (sec == self.section)
            focus = cur and self.menu_focus
            rect = (pad, y, mw - 2 * pad, row_h - 10)
            if focus:
                pygame.draw.rect(self.surface, SELECT, rect, border_radius=12)
            elif cur:
                pygame.draw.rect(self.surface, PANEL, rect, border_radius=12)
                pygame.draw.rect(self.surface, SELECT,
                                 (pad, y + 8, 6, row_h - 26), border_radius=3)
            tcol = SELECT_TEXT if focus else (TEXT if cur else DIM)
            t = self._fit(self.f_small, SECTION_LABELS[sec], tcol,
                          mw - 2 * pad - 36)
            self.surface.blit(t, (pad + 22, y + 8))
            c = self._fit(self.f_hint, counts[sec],
                          SELECT_TEXT if focus else DIM, mw - 2 * pad - 36)
            self.surface.blit(c, (pad + 22, y + 8 + t.get_height()))
            y += row_h
        # QR code at the bottom of the menu
        qr_px = mw - 4 * pad
        qy = self.H - 24 - qr_px - 6
        self._draw_menu_profiles(pad, mw, y + int(self.H * 0.01), qy - 14)
        surf = self._qr_surface(qr_px)
        if surf is not None:
            qx = (mw - qr_px) // 2
            pygame.draw.rect(self.surface, (MENU_BG if self.cfg.theme == "dark" else (255, 255, 255)),
                             (qx - 6, qy - 6, qr_px + 12, qr_px + 12),
                             border_radius=8)
            self.surface.blit(pygame.transform.scale(surf, (qr_px, qr_px)),
                              (qx, qy))
        self._ox = mw

    def _draw_avatar(self, cx, cy, r, p):
        """Round profile picture (or a colored circle with the initial)."""
        key = ("av", p["id"], r)
        path = iptv_profiles.avatar_path(p["id"])
        try:
            mt = os.path.getmtime(path) if p["has_pic"] else 0
        except OSError:
            mt = 0
        hit = self._poster_surfaces.get(key)
        if not hit or hit[0] != mt:
            d = 2 * r
            surf = pygame.Surface((d, d), pygame.SRCALPHA)
            img = None
            if mt:
                try:
                    img = pygame.transform.smoothscale(
                        pygame.image.load(path).convert(), (d, d))
                except Exception:
                    img = None
            if img is None:
                pygame.draw.circle(surf, iptv_profiles.initial_color(p["name"]),
                                   (r, r), r)
                ini = self.f_small.render(p["name"][:1].upper(), True,
                                          (255, 255, 255))
                surf.blit(ini, (r - ini.get_width() // 2,
                                r - ini.get_height() // 2))
            else:
                mask = pygame.Surface((d, d), pygame.SRCALPHA)
                pygame.draw.circle(mask, (255, 255, 255, 255), (r, r), r)
                img = img.convert_alpha()
                img.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
                surf = img
            hit = (mt, surf)
            self._poster_surfaces[key] = hit
        self.surface.blit(hit[1], (cx - r, cy - r))

    def _draw_menu_profiles(self, pad, mw, top, bottom):
        """Profiles in the menu, just above the QR code (a few rows; the list
        scrolls when there are more)."""
        profs = self._menu_profiles()
        if not profs or bottom - top < 40:
            return
        ph = int(self.H * 0.07)
        vis = max(1, min(len(profs), (bottom - top) // ph))
        act = iptv_profiles.ACTIVE
        sel = self.menu_prof if self.menu_prof is not None else next(
            (i for i, p in enumerate(profs) if p["id"] == act), 0)
        start = max(0, min(sel - vis // 2, len(profs) - vis))
        y = bottom - vis * ph          # stick to the QR code
        for i in range(start, start + vis):
            p = profs[i]
            focus = self.menu_focus and self.menu_prof == i
            rect = (pad, y, mw - 2 * pad, ph - 6)
            if focus:
                pygame.draw.rect(self.surface, SELECT, rect, border_radius=12)
            elif p["id"] == act:
                pygame.draw.rect(self.surface, PANEL, rect, border_radius=12)
                pygame.draw.rect(self.surface, SELECT,
                                 (pad, y + 8, 6, ph - 22), border_radius=3)
            r = (ph - 6) // 2 - 6
            self._draw_avatar(pad + 20 + r, y + (ph - 6) // 2, r, p)
            nm = p["name"] + (" *" if p["has_pin"] else "")
            tcol = SELECT_TEXT if focus else (TEXT if p["id"] == act else DIM)
            s = self._fit(self.f_hint, nm, tcol, mw - 2 * pad - 2 * r - 52)
            self.surface.blit(s, (pad + 30 + 2 * r,
                                  y + (ph - 6) // 2 - s.get_height() // 2))
            y += ph
        # scroll hints
        ax = mw - pad - 14
        if start > 0:
            pygame.draw.polygon(self.surface, DIM, [(ax - 6, bottom - vis * ph + 12),
                                (ax + 6, bottom - vis * ph + 12),
                                (ax, bottom - vis * ph + 4)])
        if start + vis < len(profs):
            pygame.draw.polygon(self.surface, DIM, [(ax - 6, bottom - 12),
                                (ax + 6, bottom - 12), (ax, bottom - 4)])

    def _draw_pin(self):
        p = self._pin_target or {"id": "", "name": "", "has_pic": False}
        cx = self.W // 2
        self._draw_avatar(cx, int(self.H * 0.15), int(self.H * 0.07), p)
        t = self.f_item.render(p["name"], True, TEXT)
        self.surface.blit(t, (cx - t.get_width() // 2, int(self.H * 0.24)))
        # dots
        n = len(self._pin_buf)
        dot_r = int(self.H * 0.012)
        gap = dot_r * 4
        x0 = cx - (6 * gap) // 2 + gap // 2
        for k in range(6):
            pos = (x0 + k * gap, int(self.H * 0.345))
            if k < n:
                pygame.draw.circle(self.surface, TEXT, pos, dot_r)
            else:
                pygame.draw.circle(self.surface, DIM, pos, dot_r, 2)
        # keypad
        bw, bh = int(self.W * 0.075), int(self.H * 0.09)
        g = int(self.H * 0.015)
        left = cx - (3 * bw + 2 * g) // 2
        top = int(self.H * 0.39)
        for k, label in enumerate(self.PIN_KEYS):
            x = left + (k % 3) * (bw + g)
            y = top + (k // 3) * (bh + g)
            sel = (k == self._pin_idx)
            pygame.draw.rect(self.surface, SELECT if sel else PANEL,
                             (x, y, bw, bh), border_radius=12)
            lab = {"<": "<-"}.get(label, label)
            s = self.f_item.render(lab, True, SELECT_TEXT if sel else TEXT)
            self.surface.blit(s, (x + (bw - s.get_width()) // 2,
                                  y + (bh - s.get_height()) // 2))
        if self._pin_msg:
            m = self.f_small.render(self._pin_msg, True, (230, 120, 110))
            self.surface.blit(m, (cx - m.get_width() // 2, int(self.H * 0.88)))
        else:
            self._draw_hint("Arrows and OK to enter the PIN, then OK on OK  -  Back to cancel")

    def _draw_playlists(self):
        """Live section: tiles for the live categories."""
        cats = self._live_cats()
        pls = self.cfg.playlists

        def get(k):
            pl = pls[cats[k]]
            pic = iptv_livepics.find(iptv_livepics.cat_key(pl.display_name))
            return (pl.display_name, pic or "", False)
        self._draw_poster_grid(
            "", len(cats), get, self.live_sel,
            "OK to open  -  Left for menu", cols=self.LIVE_COLS, rows=3,
            aspect=16 / 9, colored=True, cover=True,
            focused=not self.menu_focus,
            empty="No live categories configured (see Settings in the app).")

    def _load_thumb_surface(self, url):
        """Return a cached pygame surface for a channel's thumbnail, or None.

        The surface is keyed by the file's modification time, so when a
        thumbnail is re-scanned (file overwritten) the new image is picked up
        instead of showing the stale cached surface.
        """
        path = iptv_thumbs.thumb_path(url)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._thumb_surfaces.get(url)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            surf = pygame.image.load(path).convert()
        except Exception:
            return None
        self._thumb_surfaces[url] = (mtime, surf)
        return surf

    def _draw_channels(self):
        if self.show_thumbs:
            self._draw_channels_grid()
        else:
            self._draw_channels_list()
        if self.scan_menu:
            self._draw_scan_menu()

    def _draw_scan_menu(self):
        # dim the background
        overlay = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        self.surface.blit(overlay, (0, 0))
        # centred panel
        title = self.f_item.render("Refresh channel pictures", True, TEXT)
        opts = self.SCAN_MENU
        pw = int(self.W * 0.62)
        row_h = int(self.H * 0.12)
        ph = int(self.H * 0.10) + row_h * len(opts) + int(self.H * 0.06)
        px = self.W // 2 - pw // 2
        py = self.H // 2 - ph // 2
        panel = pygame.Surface((pw, ph), pygame.SRCALPHA)
        panel.fill(POPUP_BG)
        self.surface.blit(panel, (px, py))
        self.surface.blit(title, (px + 30, py + 20))
        top = py + int(self.H * 0.10)
        for i, label in enumerate(opts):
            y = top + i * row_h
            sel = (i == self.sm_index)
            color = SELECT if sel else PANEL
            pygame.draw.rect(self.surface, color,
                             (px + 24, y, pw - 48, row_h - 14), border_radius=12)
            tcol = SELECT_TEXT if sel else TEXT
            t = self.f_item.render(label, True, tcol)
            self.surface.blit(t, (px + 48,
                                  y + (row_h - 14) // 2 - t.get_height() // 2))
        hint = self.f_hint.render("Up / Down  -  OK to choose  -  Back to cancel",
                                  True, DIM)
        self.surface.blit(hint, (px + 30, py + ph - hint.get_height() - 16))

    def _draw_channels_list(self):
        chans = self.current_playlist.channels
        if self.current_playlist.is_movies:
            self._draw_poster_grid(
                "", len(chans),
                lambda k: (chans[k].name, chans[k].logo,
                           self.movies.is_watched(chans[k].url)),
                self.ch_index, "OK to watch  -  Left for menu",
                empty="No movies added yet.", focused=not self.menu_focus)
            return
        self._draw_header(self.current_playlist.display_name)
        top = int(self.H * 0.18)
        row_h = int(self.H * 0.11)
        visible = max(1, int((self.H * 0.7) // row_h))
        start = max(0, min(self.ch_index - visible // 2,
                           max(0, len(chans) - visible)))
        left = int(self.W * 0.06)
        w = int(self.W * 0.88)
        for row, i in enumerate(range(start, min(start + visible, len(chans)))):
            y = top + row * row_h
            sel = (i == self.ch_index)
            color = SELECT if sel else PANEL
            pygame.draw.rect(self.surface, color, (left, y, w, row_h - 12),
                             border_radius=14)
            tcol = SELECT_TEXT if sel else TEXT
            name = chans[i].name
            cur, nxt = self.epg.now_next(chans[i].tvg_id, chans[i].name)
            name_w = (w - 56) * (0.55 if cur else 1.0)
            t = self._fit(self.f_item, name, tcol, name_w)
            self.surface.blit(t, (left + 28, y + (row_h - 12) // 2 - t.get_height() // 2))
            # now-playing EPG
            if cur:
                info = "Now: " + cur[2]
                s = self._fit(self.f_small, info, SELECT_TEXT if sel else DIM,
                              (w - 56) * 0.45 - 20)
                self.surface.blit(s, (left + w - s.get_width() - 28,
                                      y + (row_h - 12) // 2 - s.get_height() // 2))
        hint = "Up / Down to move  -  OK to watch  -  Back to lists"
        if self._is_live_category():
            hint += "  -  Menu for pictures"
        self._draw_hint(hint)

    def _draw_channels_grid(self):
        """Live category: picture grid of all channels (uploaded picture,
        grabbed frame, or a colored square)."""
        chans = self.current_playlist.channels
        sub = ""
        if self.thumbs.is_scanning():
            per = self._thumb_cols() * 3
            cur = getattr(self.thumbs, "current", None)
            idx = next((i for i, c in enumerate(chans) if c.url == cur), None)
            if idx is not None:
                sub = "Scanning pictures on page %d/%d" % (
                    idx // per + 1, max(1, (len(chans) + per - 1) // per))

        def get(k):
            c = chans[k]
            pic = iptv_livepics.find(iptv_livepics.chan_key(c.url))
            if not pic and iptv_thumbs.has_thumb(c.url):
                pic = iptv_thumbs.thumb_path(c.url)
            nm = c.name
            # drop a leading provider prefix like "NO: " to save space
            if len(nm) > 4 and nm[:4].upper().startswith(
                    ("NO:", "UK:", "DK:", "SE:", "US:", "DE:")):
                nm = nm.split(":", 1)[1].strip()
            return (nm, pic or "", False)
        self._draw_poster_grid(
            self.current_playlist.display_name, len(chans), get, self.ch_index,
            "OK to watch  -  Menu to refresh pictures  -  Back for categories",
            cols=self._thumb_cols(), rows=3, aspect=16 / 9, colored=True,
            cover=True, focused=not self.menu_focus, subtitle=sub,
            empty="No channels in this category.")

    def _draw_message(self):
        if time.time() > self.message_until or not self.message:
            return
        t = self.f_small.render(self.message, True, SELECT_TEXT)
        pad = 18
        w = t.get_width() + pad * 2
        h = t.get_height() + pad
        x = self.W // 2 - w // 2
        y = int(self.H * 0.08)
        box = pygame.Surface((w, h), pygame.SRCALPHA)
        box.fill((0, 0, 0, 190))
        self.surface.blit(box, (x, y))
        self.surface.blit(t, (x + pad, y + pad // 2))

    # -- main loop -------------------------------------------------------
    # -- best-network selection -----------------------------------------
    def _speed_test_url(self):
        """A provider URL to measure real-world throughput with. Prefer a movie
        file (fixed size, fast start); fall back to a live channel."""
        pls = getattr(self.cfg, "playlists", []) or []
        for want_movies in (True, False):
            for pl in pls:
                if bool(getattr(pl, "is_movies", False)) != want_movies:
                    continue
                if getattr(pl, "is_series", False):
                    continue
                for c in pl.channels:
                    if c.url:
                        return c.url
        return ""

    def start_network_optimizer_thread(self):
        """At start-up, connect to the best reachable saved Wi-Fi (by the last
        stored speed test) and route traffic over the faster of Ethernet /
        Wi-Fi. Speed tests only run when requested from the web app."""
        self.net_info = {"testing": False, "progress": "",
                         "tested": iptv_net.load_results().get("tested", 0)}

        def startup():
            time.sleep(15)
            try:
                self._apply_best_network()
            except Exception as e:
                print("network startup pick failed: %s" % e, flush=True)
        threading.Thread(target=startup, daemon=True).start()

    def _apply_best_network(self):
        res = iptv_net.load_results()
        visible = iptv_net.visible_ssids(rescan=True)
        ranked = iptv_net.ranked_wifi(visible)
        best = next((n for n in ranked if n["reachable"]), None)
        if best:
            names = [n["name"] for n in ranked]
            iptv_net.set_wifi_priority(best["name"], names)
            if iptv_net.current_wifi() != best["name"] and \
                    not self.player.is_running():
                iptv_net.activate_wifi(best["name"])
        devs = iptv_net.connected_interfaces()
        if "eth0" in devs and "wlan0" in devs:
            eth = res.get("eth0") or 0
            wifi = (best or {}).get("mbit") or 0
            # Ethernet is preferred unless a test showed Wi-Fi to be faster
            iptv_net.prefer_interface("wlan0" if wifi > eth > 0 else "eth0",
                                      ["eth0", "wlan0"])

    def start_network_test(self):
        """Measure every reachable saved Wi-Fi (switching between them) and
        Ethernet, store the results and use the best. Runs in the background;
        refused while something is playing."""
        if self.net_info.get("testing"):
            return False, "A test is already running"
        if self.player.is_running():
            return False, "Stop playback first"
        self.net_info["testing"] = True
        self.net_info["last_error"] = ""

        def worker():
            try:
                orig = iptv_net.current_wifi()
                visible = iptv_net.visible_ssids(rescan=True)
                res = iptv_net.load_results()
                wifi = dict(res.get("wifi", {}))
                saved = iptv_net.saved_wifi_profiles()
                for name, ssid in saved:
                    if ssid not in visible and name != orig:
                        continue
                    self.net_info["progress"] = "Testing %s ..." % name
                    if iptv_net.current_wifi() != name and \
                            not iptv_net.activate_wifi(name):
                        wifi[name] = 0.0
                        continue
                    wifi[name] = round(iptv_net.measure_speed("wlan0"), 1)
                # forget results for networks no longer saved
                wifi = {k: v for k, v in wifi.items()
                        if k in {n for n, _ in saved}}
                eth = None
                if "eth0" in iptv_net.connected_interfaces():
                    self.net_info["progress"] = "Testing Ethernet ..."
                    eth = round(iptv_net.measure_speed("eth0"), 1)
                res = {"tested": int(time.time()), "wifi": wifi, "eth0": eth}
                iptv_net.save_results(res)
                self.net_info["tested"] = res["tested"]
                self.net_info["progress"] = "Switching to the best network ..."
                self._apply_best_network()
                if not iptv_net.current_wifi() and orig:
                    iptv_net.activate_wifi(orig)
                print("network test: %s eth=%s" % (wifi, eth), flush=True)
            except Exception as e:
                print("network test failed: %s" % e, flush=True)
            finally:
                self.net_info["progress"] = ""
                self.net_info["testing"] = False
        threading.Thread(target=worker, daemon=True).start()
        return True, "Test started"

    def run(self):
        import signal
        self._stop = False
        def _on_term(signum, frame):
            self._stop = True
        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGINT, _on_term)

        self.remote.start()
        self.start_epg_thread()
        self.start_provider_refresh_thread()
        self.start_network_optimizer_thread()
        # streaming to other devices: stop the TV when a device starts, and
        # cache the start of added movies while idle
        iptv_stream.set_notify(lambda: self.remote.q.put("__remote_stream__"))
        iptv_stream.start_cacher(lambda: (
            self.screen_state != SCREEN_PLAYING
            and not self.player.is_running()
            and not self.thumbs.is_scanning()
            and not getattr(self, "net_info", {}).get("testing")))
        # First-run / relocation helper. Network can take a while to come up
        # after boot, so check in the background for up to ~30s. If there is
        # still no network: with no remote configured, open the remote setup
        # wizard (it continues to Wi-Fi setup when done); otherwise go straight
        # to Wi-Fi setup.
        def _first_run_check():
            for _ in range(15):
                if iptv_net.has_network():
                    return
                time.sleep(2)
            self.remote.q.put("__first_run__")
        threading.Thread(target=_first_run_check, daemon=True).start()
        if not self.cfg.autoplay_first:
            self._enter_section()      # start on the last used section
        if self.cfg.autoplay_first and self.cfg.playlists \
                and self.screen_state == SCREEN_PLAYLISTS:
            self.open_playlist(0)
            if self.current_playlist.channels:
                self.play_channel(0)

        clock = pygame.time.Clock()
        running = True
        display_retry = 0
        while running and not self._stop:
            # If we're headless AND not playing video, keep checking whether a
            # TV got connected. (While playing, mpv owns the DRM display, so we
            # must NOT grab it back or the video output dies.)
            if not self._display_active and self.screen_state != SCREEN_PLAYING:
                now = time.time()
                if now >= display_retry:
                    display_retry = now + 3
                    self._init_display()
            # keyboard fallback (useful for testing over a monitor+keyboard)
            # NOTE: we intentionally do NOT translate pygame KEYDOWN events
            # into actions. All physical input (IR remote and any USB keyboard)
            # is read directly from evdev by RemoteInput. SDL/pygame also sees
            # those same devices as a keyboard, so handling KEYDOWN here would
            # deliver every remote press twice (moving the cursor two steps).
            # We still drain the pygame event queue so the window stays healthy.
            if self._display_active:
                for event in pygame.event.get():
                    pass
            # remote actions
            while True:
                a = self.remote.get(timeout=0)
                if not a:
                    break
                if a == "__reload_config__":
                    # settings autosave: don't interrupt what is being watched
                    if self.screen_state == SCREEN_PLAYING or \
                            self.player.is_running():
                        self._reload_pending = True
                    else:
                        self.reload_config()
                elif a == "__learn_remote__":
                    self.open_learn_remote()
                elif a == "__remote_stream__":
                    if self.player.is_running() or \
                            self.screen_state == SCREEN_PLAYING:
                        if self.is_movie_playing():
                            self._save_movie_position()
                        self.thumbs.stop()
                        self.player.stop()
                        self._movie_url = None
                        self.movie_menu = None
                        self._acquire_display()
                        self.screen_state = (SCREEN_CHANNELS
                                             if self.current_playlist
                                             else SCREEN_PLAYLISTS)
                    else:
                        self.thumbs.stop()
                    self.notify("Playing on another device", 8)
                elif a == "__scan_web__":
                    # the web app's Play tab shows a live category: scan its
                    # pictures, but only while the TV itself is idle
                    urls = list(self.web.pending_scan or [])
                    if (urls and self.screen_state not in (
                            SCREEN_PLAYING, SCREEN_STANDBY)
                            and not self.player.is_running()
                            and not iptv_stream.busy()
                            and not self.thumbs.is_scanning()):
                        self.thumbs.start(urls)
                elif a == "__play_tv__":
                    self._play_on_tv(self.web.pending_tv)
                elif a == "__remote_qr__":
                    # a device was just added in the web app: show its QR
                    if self.screen_state in (SCREEN_PLAYLISTS, SCREEN_CHANNELS,
                                             SCREEN_POWER, SCREEN_REMOTE):
                        self._open_remote_access()
                elif a == "__first_run__":
                    if self.screen_state in (SCREEN_PLAYLISTS, SCREEN_CHANNELS):
                        has_remote = bool(ir.load_learned()) or any(
                            b.get("scancodes") for b in iptv_net.read_keymap())
                        if has_remote:
                            self.open_wifi_setup(reason="boot-no-network")
                        else:
                            self.open_learn_remote()
                elif a == "__net_retest__":
                    ok, msg = self.start_network_test()
                    if not ok:
                        self.net_info["progress"] = ""
                        self.net_info["last_error"] = msg
                elif a == "__reload_remotes__":
                    self.remote.reload_learned()
                elif a == "__wifi_setup__":
                    self.open_wifi_setup(reason="web")
                else:
                    self.act(a)

            # a series episode fetch finished in the background?
            if self.screen_state == SCREEN_LOADING and self._loading_result:
                result = self._loading_result
                self._loading_result = None
                if result[0] != "cancel":
                    self._finish_open_series(result)

            # IR learn wizard: pick up captured presses
            if self.screen_state == SCREEN_LEARN:
                self._learn_poll()
            # Wi-Fi wizard: pick up finished background scan/connect
            if self.screen_state in (SCREEN_WIFI_LIST, SCREEN_WIFI_CONNECT):
                self._wifi_poll()

            # keep the web UI's status in sync
            sel_name = ""
            if (self.screen_state == SCREEN_PLAYLISTS and self.cfg.playlists
                    and 0 <= self.pl_index < len(self.cfg.playlists)):
                sel_name = self.cfg.playlists[self.pl_index].display_name
            if time.time() - getattr(self, "_net_active_t", 0) > 10:
                self._net_active_t = time.time()
                self._net_active = iptv_net.default_interface()
            extra = {"network": dict(getattr(self, "net_info", {}),
                                     active=self._net_active)}
            if self.screen_state == SCREEN_LEARN:
                extra.update({"learn_idx": self._learn_idx,
                         "learn_total": len(ir.LEARN_BUTTONS_FULL),
                         "learn_got": len(self._learn_map)})
            self.web.set_status(
                self.screen_state,
                self.current_playlist.name if self.current_playlist else "",
                self.current_channel.name if self.current_channel else "",
                sel_name, extra)

            # periodically save a movie's position while it plays, so an
            # unexpected power loss still leaves a recent resume point
            # remember progress every ~5s (mpv can't be asked after it exits)
            if (self.screen_state == SCREEN_PLAYING and self._movie_url
                    and time.time() - getattr(self, "_last_prog_t", 0) > 5):
                self._last_prog_t = time.time()
                try:
                    pos, dur = self.player.time_pos(), self.player.duration()
                    if pos and dur:
                        self._last_progress = (self._movie_url, pos, dur)
                        if pos >= dur * 0.75:
                            self.movies.mark_watched(self._movie_url)
                except Exception:
                    pass
            if (self.screen_state == SCREEN_PLAYING and self.is_movie_playing()
                    and self.movie_menu is None
                    and time.time() - self._last_saved_pos > 30):
                self._save_movie_position()
                self._last_saved_pos = time.time()

            if iptv_profiles.ACTIVE != self._loaded_profile and \
                    self.screen_state != SCREEN_PLAYING:
                self._apply_profile()

            if getattr(self, "_reload_pending", False) and \
                    self.screen_state not in (SCREEN_PLAYING, SCREEN_LEARN,
                                              SCREEN_WIFI_LIST,
                                              SCREEN_WIFI_KEYBOARD,
                                              SCREEN_WIFI_CONNECT) and \
                    not self.player.is_running():
                self._reload_pending = False
                self.reload_config()

            # Watchdog: mpv can hang (process alive but IPC frozen) after a
            # thermal stall - then buttons do nothing. Detect it and recover.
            if self.screen_state == SCREEN_PLAYING and self.player.is_running():
                now = time.time()
                if now - getattr(self, "_last_health_check", 0) > 5:
                    self._last_health_check = now
                    if self.player.is_responsive():
                        self._unresponsive_since = 0
                    else:
                        if not getattr(self, "_unresponsive_since", 0):
                            self._unresponsive_since = now
                        elif now - self._unresponsive_since > 10:
                            # Frozen for >10s. Restart the same stream; for a
                            # movie/episode resume from the last saved position.
                            self._unresponsive_since = 0
                            if self.is_movie_playing() and self._movie_url:
                                rec = self.movies.get_position(self._movie_url)
                                pos = int(rec.get("pos", 0)) if rec else 0
                                self.notify("Recovering playback...", 2)
                                self._start_movie(start=pos)
                            else:
                                self.notify("Recovering playback...", 2)
                                self._launch(self.current_channel.url, live=True)
                                self.screen_state = SCREEN_PLAYING

            # Live stream that never started or ended while mpv stays alive
            # (--idle=yes): the screen would stay black. Retry, then give up.
            if (self.screen_state == SCREEN_PLAYING and self.player.is_running()
                    and not self.is_movie_playing() and self.current_channel):
                now = time.time()
                if now - getattr(self, "_last_idle_check", 0) > 2:
                    self._last_idle_check = now
                    if self.player.get_property("idle-active", False) is True:
                        since = getattr(self, "_idle_since", 0) or now
                        self._idle_since = since
                        if now - since > 12:
                            self._idle_since = 0
                            self._idle_tries = getattr(self, "_idle_tries", 0) + 1
                            if self._idle_tries <= 3:
                                self.notify("Stream stopped - retrying...", 3)
                                self._launch(self.current_channel.url, live=True)
                                self.screen_state = SCREEN_PLAYING
                            else:
                                self._idle_tries = 0
                                self.player.stop()
                                self._acquire_display()
                                self.screen_state = SCREEN_CHANNELS
                                self.notify("Stream not available", 5)
                                self._resume_scan()
                    else:
                        self._idle_since = 0
                        if getattr(self, "_idle_ok_since", 0) == 0:
                            self._idle_ok_since = now
                        elif now - self._idle_ok_since > 60:
                            self._idle_tries = 0
            else:
                self._idle_since = 0
                self._idle_ok_since = 0

            # if video died unexpectedly, fall back to menu
            if self.screen_state == SCREEN_PLAYING and not self.player.is_running():
                was_series = bool(self.current_playlist and getattr(
                    self.current_playlist, "is_series", False))
                if self.is_movie_playing():
                    # a movie/episode that ran to the end -> forget resume
                    if self._movie_url:
                        rec = self.movies.get_position(self._movie_url)
                        last = getattr(self, "_last_progress", None)
                        if (last and last[0] == self._movie_url and last[2]
                                and last[1] >= last[2] * 0.75) or \
                                (rec and rec.get("duration") and
                                 rec["pos"] >= rec["duration"] * 0.75):
                            self.movies.mark_watched(self._movie_url)
                        self.movies.clear_position(self._movie_url)
                    self._movie_url = None
                    self.movie_menu = None
                elif self.current_channel:
                    self.notify("Stream ended", 3)
                self._acquire_display()
                self.screen_state = (SCREEN_EPISODES if was_series
                                     else SCREEN_CHANNELS)
                if not was_series:
                    self._resume_scan()

            if self._display_active:
                # Only redraw when something actually changed (saves CPU on a
                # static menu). The clock's minute counts as a change.
                sig = (self.screen_state, self.pl_index, self.ch_index,
                       getattr(self, "power_index", 0),
                       getattr(self, "resume_index", 0),
                       self.series_index, self.season_index, self.episode_index,
                       self.current_series["name"] if self.current_series else "",
                       self.current_playlist.name if self.current_playlist else "",
                       self.show_thumbs, iptv_posters.version,
                       self.section, self.menu_focus, self.live_sel,
                       self.menu_prof, iptv_profiles.ACTIVE,
                       iptv_profiles.extra_count(), iptv_profiles.stamp(), self._pin_idx,
                       len(self._pin_buf), self._pin_msg,
                       iptv_livepics.dir_stamp(),
                       getattr(self, "_grid_index", 0),
                       self.scan_menu, self.sm_index,
                       self.thumbs.done if self.thumbs.is_scanning() else -1,
                       int(time.time() * 2) if self.screen_state == SCREEN_LOADING else 0,
                       time.strftime("%H:%M"),
                       self._start_url_sig(),
                       # learn/wifi wizard state
                       getattr(self, "_learn_idx", 0),
                       getattr(self, "_learn_msg", ""),
                       len(getattr(self, "_learn_map", {})),
                       getattr(self, "_wifi_index", 0),
                       getattr(self, "_wifi_scanning", False),
                       len(getattr(self, "_wifi_password", "")),
                       getattr(self, "_kb_row", 0), getattr(self, "_kb_col", 0),
                       getattr(self, "_kb_mode", ""),
                       getattr(self, "_wifi_status", ""),
                       (getattr(self.web, "last_vpn_conf", None) or ("",))[0],
                       (int(time.time() * 2) if self.screen_state in
                        (SCREEN_WIFI_LIST, SCREEN_WIFI_CONNECT)
                        and (getattr(self, "_wifi_scanning", False)
                             or getattr(self, "_wifi_connecting", False))
                        else 0),
                       self.message if time.time() < self.message_until else "")
                if getattr(self, "_shutting_down", False):
                    sig = getattr(self, "_last_sig", None)   # keep shutdown image
                if sig != getattr(self, "_last_sig", None):
                    self._last_sig = sig
                    self._draw()
                    pygame.display.flip()
                # short sleep to yield the CPU while staying responsive
                time.sleep(0.05)
            else:
                # Headless (no TV): don't burn CPU. The web remote still works;
                # actions are handled above each iteration.
                time.sleep(0.1)

        self.player.stop()
        pygame.quit()

    def _key_to_action(self, key):
        return {
            pygame.K_UP: ir.UP,
            pygame.K_DOWN: ir.DOWN,
            pygame.K_LEFT: ir.LEFT,
            pygame.K_RIGHT: ir.RIGHT,
            pygame.K_RETURN: ir.OK,
            pygame.K_KP_ENTER: ir.OK,
            pygame.K_SPACE: ir.OK,
            pygame.K_BACKSPACE: ir.BACK,
            pygame.K_ESCAPE: ir.BACK,
            pygame.K_h: ir.HOME,
            pygame.K_m: ir.MENU,
            pygame.K_PLUS: ir.VOL_UP,
            pygame.K_EQUALS: ir.VOL_UP,
            pygame.K_MINUS: ir.VOL_DOWN,
            pygame.K_q: "quit",
        }.get(key)


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
