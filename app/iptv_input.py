"""Reads the Argon IR remote (and any keyboard) as simple navigation actions.

We read Linux input devices directly with evdev so it works on the console
with no desktop. The ir-keytable keymap turns remote buttons into standard
key events (KEY_UP, KEY_OK, ...) which we map to actions here.

The Argon remote's real NEC scancodes (from Argon's own decoder) are, for
reference, in /etc/rc_keymaps/argon.toml:
    POWER 0x39  UP 0x53  DOWN 0x4b  LEFT 0x99  RIGHT 0x83
    OK 0x73  HOME 0xd3  MENU 0xb9  BACK 0x09  VOL+ 0x01  VOL- 0x81
Requires an IR receiver wired to GPIO23 (e.g. the Argon ONE case).
"""
import threading
import queue

try:
    from evdev import InputDevice, list_devices, ecodes
    HAVE_EVDEV = True
except Exception:
    HAVE_EVDEV = False

# Actions the rest of the app understands
UP, DOWN, LEFT, RIGHT, OK, BACK, HOME, MENU = (
    "up", "down", "left", "right", "ok", "back", "home", "menu")
VOL_UP, VOL_DOWN, POWER = "vol_up", "vol_down", "power"

# Buttons to learn, in order: (action, friendly label, evdev KEY_* name).
# Used by the learn-remote wizard and to build the keymap.
LEARN_BUTTONS_FULL = [
    ("up",       "UP  (arrow up)",       "KEY_UP"),
    ("down",     "DOWN  (arrow down)",   "KEY_DOWN"),
    ("left",     "LEFT  (arrow left)",   "KEY_LEFT"),
    ("right",    "RIGHT  (arrow right)", "KEY_RIGHT"),
    ("ok",       "OK / SELECT",          "KEY_OK"),
    ("back",     "BACK",                 "KEY_BACK"),
    ("home",     "HOME",                 "KEY_HOME"),
    ("menu",     "MENU",                 "KEY_MENU"),
    ("vol_up",   "VOLUME UP",            "KEY_VOLUMEUP"),
    ("vol_down", "VOLUME DOWN",          "KEY_VOLUMEDOWN"),
    # POWER is deliberately not learned: the Argon ONE case watches for the
    # remote's POWER code itself and shuts the Pi down / turns it on.
]

# Map evdev key codes -> action
KEYMAP = {}
if HAVE_EVDEV:
    KEYMAP = {
        ecodes.KEY_UP: UP,
        ecodes.KEY_DOWN: DOWN,
        ecodes.KEY_LEFT: LEFT,
        ecodes.KEY_RIGHT: RIGHT,
        ecodes.KEY_OK: OK,
        ecodes.KEY_ENTER: OK,
        ecodes.KEY_KPENTER: OK,
        ecodes.KEY_SELECT: OK,
        ecodes.KEY_BACK: BACK,
        ecodes.KEY_ESC: BACK,
        ecodes.KEY_HOME: HOME,
        ecodes.KEY_MENU: MENU,
        ecodes.KEY_CONTEXT_MENU: MENU,
        ecodes.KEY_VOLUMEUP: VOL_UP,
        ecodes.KEY_VOLUMEDOWN: VOL_DOWN,
        ecodes.KEY_POWER: POWER,
        ecodes.KEY_MUTE: "mute",
    }


# ---------------------------------------------------------------------------
#  Learned remotes (decoded in the app from raw IR, any pulse-distance remote)
# ---------------------------------------------------------------------------
import os as _os
import json as _json
import time as _time

REMOTES_PATH = _os.environ.get(
    "IPTV_REMOTES",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "remotes.json"))


def load_learned():
    """Return the learned remotes: [{"name": str, "codes": {code: action}}]."""
    try:
        with open(REMOTES_PATH, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return [r for r in data.get("remotes", []) if isinstance(r, dict)]
    except Exception:
        return []


def save_learned(remotes):
    tmp = REMOTES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump({"remotes": remotes}, f, indent=1)
    _os.replace(tmp, REMOTES_PATH)


def is_repeat_frame(line):
    """True if the line contains an NEC 'key still held' repeat frame
    (~9ms pulse + ~2.25ms space) - those carry no button code."""
    vals = [(t[0], int(t[1:])) for t in line.split()
            if t[:1] in "+-" and t[1:].isdigit()]
    for i in range(len(vals) - 1):
        s0, d0 = vals[i]
        s1, d1 = vals[i + 1]
        if s0 == "+" and d0 > 7000 and s1 == "-" and 1700 < d1 < 2800:
            return True
    return False


def decode_raw_frame(line):
    """Decode one `ir-ctl -r` line ("+pulse -space ..." in microseconds).

    Handles pulse-distance protocols with a 32-bit payload: NEC (9ms leader)
    and Samsung (4.5ms leader), plus most other remotes of this family.
    Returns a stable code string like "nec:0x...." / "pd:0x...." or None
    (repeat frames, noise and unknown encodings are ignored).
    """
    vals = []
    for t in line.split():
        if t[:1] in "+-" and t[1:].isdigit():
            vals.append((t[0], int(t[1:])))
    for i in range(len(vals) - 1):
        s0, d0 = vals[i]
        s1, d1 = vals[i + 1]
        # leader: long pulse followed by a long space
        if s0 == "+" and d0 > 3000 and s1 == "-" and d1 > 3500:
            spaces = [d for (s, d) in vals[i + 2:] if s == "-"]
            bits = []
            for sp in spaces:
                if sp > 3000:          # trailing gap - end of frame
                    break
                bits.append(1 if sp > 1000 else 0)
            if len(bits) < 32:
                return None            # repeat code / truncated frame
            v = 0
            for k, b in enumerate(bits[:32]):
                v |= (b << k)
            proto = "nec" if d0 > 7000 else "pd"
            return "%s:0x%08x" % (proto, v)
    return None


class RemoteInput:
    """Background readers that push actions onto a thread-safe queue.

    Two sources:
      * the kernel-decoded IR device / keyboards (evdev) - the default remote
        whose keymap is loaded by ir-keytable;
      * raw IR read from /dev/lirc0 and decoded here - learned remotes of any
        pulse-distance protocol (e.g. Samsung), which the kernel can't decode.
    """
    def __init__(self):
        self.q = queue.Queue()
        self._stop = threading.Event()
        self._threads = []
        self._paused = False
        self._lock = threading.Lock()
        self._last_action = None
        self._last_action_t = 0.0
        self._learned = {}        # code -> action
        self.raw_listener = None  # callable(code) used by the learn wizard
        self.reload_learned()

    def pause(self):
        """Stop delivering actions (while learning a remote)."""
        self._paused = True

    def resume(self):
        self._paused = False

    def reload_learned(self):
        codes = {}
        for r in load_learned():
            for code, action in (r.get("codes") or {}).items():
                codes[code] = action
        self._learned = codes

    # Holding one of these keys repeats it (after HOLD_DELAY, every
    # HOLD_PERIOD seconds) until the button is released.
    REPEATABLE = {"up", "down", "left", "right", "vol_up", "vol_down"}
    HOLD_DELAY = 0.5
    HOLD_PERIOD = 0.33

    def _press(self, action):
        """A new button press (start of a possible hold)."""
        now = _time.time()
        self._hold_action = action
        self._hold_start = now
        self._hold_seen = now
        self._hold_last = now
        self._emit(action)

    def _held(self, action=None):
        """The button is still held down (repeat signal from the remote)."""
        now = _time.time()
        action = action or getattr(self, "_hold_action", None)
        if not action or action != getattr(self, "_hold_action", None):
            return
        self._hold_seen = now
        if action not in self.REPEATABLE:
            return
        if now - self._hold_start < self.HOLD_DELAY:
            return
        if now - self._hold_last < self.HOLD_PERIOD:
            return
        self._hold_last = now
        with self._lock:
            self._last_action = action
            self._last_action_t = now
        self.q.put(action)

    def _emit(self, action):
        """Queue an action, dropping duplicates within 250ms (one physical
        press can arrive as several frames / via two paths)."""
        with self._lock:
            now = _time.time()
            if action == self._last_action and (now - self._last_action_t) < 0.25:
                self._last_action_t = now
                return
            self._last_action = action
            self._last_action_t = now
        self.q.put(action)

    def start(self):
        t = threading.Thread(target=self._raw_reader, daemon=True)
        t.start()
        self._threads.append(t)
        if not HAVE_EVDEV:
            return
        opened = set()
        for path in list_devices():
            try:
                dev = InputDevice(path)
            except Exception:
                continue
            low = (dev.name or "").lower()
            # Ignore HDMI-CEC virtual keyboards: they advertise the same keys
            # and would deliver phantom presses.
            if "gpio_ir" not in low and "ir_recv" not in low and "ir-recv" not in low:
                if "hdmi" in low or "cec" in low or "vc4" in low:
                    continue
            keys = dev.capabilities().get(ecodes.EV_KEY, [])
            if not any(k in KEYMAP for k in keys):
                continue
            if dev.path in opened:
                continue
            opened.add(dev.path)
            t = threading.Thread(target=self._reader, args=(dev,), daemon=True)
            t.start()
            self._threads.append(t)

    def _reader(self, dev):
        try:
            for event in dev.read_loop():
                if self._stop.is_set():
                    return
                # 1 = key down, 2 = kernel autorepeat while held, 0 = up
                if event.type != ecodes.EV_KEY or event.value not in (1, 2):
                    continue
                if self._paused:
                    continue
                action = KEYMAP.get(event.code)
                if not action:
                    continue
                if event.value == 1:
                    self._press(action)
                else:
                    self._held(action)
        except Exception:
            return

    def _raw_reader(self):
        """Read raw pulses with ir-ctl and decode learned remotes. Restarts
        ir-ctl if it ever exits."""
        import subprocess
        while not self._stop.is_set():
            try:
                proc = subprocess.Popen(
                    ["stdbuf", "-oL", "ir-ctl", "-r", "-d", "/dev/lirc0"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1)
            except Exception:
                _time.sleep(5)
                continue
            try:
                for line in proc.stdout:
                    if self._stop.is_set():
                        break
                    code = decode_raw_frame(line)
                    now = _time.time()
                    if not code:
                        # NEC "still held" frame -> repeat the held button
                        if self.raw_listener is None and not self._paused \
                                and is_repeat_frame(line) \
                                and now - getattr(self, "_hold_seen", 0) < 0.35:
                            self._held()
                        continue
                    listener = self.raw_listener
                    if listener is not None:
                        listener(code)
                        continue
                    if self._paused:
                        continue
                    action = self._learned.get(code)
                    if not action:
                        continue
                    # Some remotes (e.g. Samsung) resend the full code while
                    # held: same code arriving quickly = still held.
                    if action == getattr(self, "_hold_action", None) and \
                            now - getattr(self, "_hold_seen", 0) < 0.35:
                        self._held(action)
                    else:
                        self._press(action)
                    if is_repeat_frame(line):
                        self._held(action)
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            _time.sleep(2)

    def get(self, timeout=None):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
#  Learning a new remote
# ---------------------------------------------------------------------------
class RemoteLearner:
    """Collects decoded raw IR codes while the learn wizard is open.

    Hooks into RemoteInput's raw reader, so it works with any pulse-distance
    remote (NEC, Samsung, ...) even when no remote currently controls the box.
    next_press() returns ("raw", code) tuples.
    """
    def __init__(self, remote):
        self.remote = remote
        self.q = queue.Queue()
        self._last = None
        self._last_t = 0.0

    def _on_code(self, code):
        now = _time.time()
        # a held / single press often arrives as several identical frames
        if code == self._last and (now - self._last_t) < 0.6:
            self._last_t = now
            return
        self._last = code
        self._last_t = now
        self.q.put(("raw", code))

    def start(self):
        self.remote.raw_listener = self._on_code
        return True

    def next_press(self, timeout=None):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def detect_protocol(self):
        return "raw"

    def stop(self):
        if self.remote.raw_listener == self._on_code:
            self.remote.raw_listener = None
