"""Controls an mpv process that renders video directly on the screen (DRM)."""
import os
import json
import socket
import subprocess
import time

IPC_SOCKET = "/tmp/iptv-mpv.sock"


class Player:
    def __init__(self):
        self.proc = None
        self._sock = None
        self.is_live = False

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def is_responsive(self):
        """True if mpv answers IPC. A frozen mpv (process alive but IPC hung,
        or the IPC listener gone) returns False so callers can recover.

        Uses a fresh short-lived connection so a wedged cached socket cannot
        mask the problem, and never blocks for more than ~0.6s."""
        if not self.is_running():
            return False
        s = None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.6)
            s.connect(IPC_SOCKET)
            s.sendall((json.dumps({"command": ["get_property", "mpv-version"],
                                   "request_id": 99}) + "\n").encode())
            deadline = time.time() + 0.6
            buf = b""
            while time.time() < deadline:
                try:
                    chunk = s.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                for line in buf.split(b"\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                    except ValueError:
                        continue
                    if j.get("request_id") == 99 and j.get("error") == "success":
                        return True
            return False
        except OSError:
            return False
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def play(self, url, start=0, alang=None, slang=None, want_subs=True,
             live=False, audio_delay=0.0):
        """Start (or replace) playback of a stream URL fullscreen.

        start:  seconds to seek to on load (resume).
        alang:  preferred audio language code (e.g. "eng", "nor").
        slang:  preferred subtitle language code, or None.
        want_subs: whether subtitles should be shown at all.
        live:   True for live TV -> keep a back-buffer so the user can pause
                and rewind (timeshift).
        audio_delay: seconds to shift audio vs video (negative = audio earlier).
        """
        self.stop()
        self.is_live = bool(live)
        try:
            if os.path.exists(IPC_SOCKET):
                os.remove(IPC_SOCKET)
        except OSError:
            pass
        cmd = [
            "mpv",
            "--fullscreen",
            "--gpu-context=drm",
            # Output 1080p and let mpv pick the refresh rate that matches the
            # video (so 50fps content uses a 50 Hz mode, 25/30fps uses 60 Hz)
            # instead of always 1080p60. This stops the constant frame dropping
            # that made high-framerate channels look laggy.
            "--drm-mode=1920x1080",
            "--hwdec=auto-safe",
            "--interpolation=no",
            # Cheap scalers - the vc4 GPU can't run the fancy ones at 4K/50.
            "--scale=bilinear",
            "--dscale=bilinear",
            "--cscale=bilinear",
            "--tone-mapping=clip",
            "--framedrop=vo",
            # Generous buffering for live streams so brief network hiccups
            # don't underrun the audio (which was cutting out after ~1 min).
            "--cache=yes",
            "--cache-secs=30",
            "--demuxer-max-bytes=150M",
            "--demuxer-readahead-secs=20",
            # Audio output: use ALSA straight to the connected HDMI with a
            # large buffer so the vc4 HDMI PCM doesn't underrun.
            "--ao=alsa",
            "--audio-buffer=0.5",
            "--audio-fallback-to-null=yes",
            "--force-window=yes",
            "--idle=yes",
            "--no-osc",
            "--no-input-default-bindings",
            "--no-terminal",
            "--msg-level=all=error",
            "--input-ipc-server=" + IPC_SOCKET,
            "--network-timeout=30",
            "--user-agent=IPTVPlayer/1.0",
        ]
        if live:
            # Live TV has no on-screen menu, so we can use the classic gpu VO in
            # "dumb mode": the vc4 then downscales even 4K/50fps HDR with almost
            # no frame drops. Plus a rewindable back-buffer for pause/skip.
            cmd += [
                "--vo=gpu",
                "--gpu-dumb-mode=yes",
                # Use audio as the master clock (don't resample audio to the
                # display). This keeps A/V sync tight and avoids the subtle
                # audio artefacts that display-resample introduced, while the
                # dumb-mode gpu VO still plays 4K/50 smoothly.
                "--video-sync=audio",
                "--cache-secs=1800",              # keep up to 30 min buffered
                "--demuxer-max-bytes=400M",       # forward buffer cap
                "--demuxer-max-back-bytes=400M",  # rewindable back-buffer
                "--force-seekable=yes",           # allow seeking within cache
            ]
        else:
            # Movies / series need mpv's on-screen menus (audio/subtitles/etc).
            # The gpu-next (libplacebo) VO crashes on the Pi 5's vc4 after a
            # short while with "MESA: error: Export failed", so use the classic
            # gpu VO instead. We do NOT enable gpu-dumb-mode here so the OSD
            # menus still render. video-sync=display-resample keeps playback
            # smooth and judder-free.
            cmd += ["--vo=gpu", "--video-sync=display-resample"]
        # Preferred languages: mpv picks the best matching track automatically.
        if alang:
            cmd.append("--alang=" + alang)
        if want_subs and slang:
            cmd.append("--slang=" + slang)
            cmd.append("--sid=auto")
        else:
            cmd.append("--sid=no")
        if start and start > 0:
            cmd.append("--start=+%d" % int(start))
        if audio_delay:
            cmd.append("--audio-delay=%.3f" % float(audio_delay))
        cmd.append(url)
        env = dict(os.environ)
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        # wait for the ipc socket to appear
        for _ in range(50):
            if os.path.exists(IPC_SOCKET):
                break
            time.sleep(0.1)

    def _connect(self):
        if self._sock:
            return self._sock
        if not os.path.exists(IPC_SOCKET):
            return None
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(IPC_SOCKET)
            self._sock = s
            return s
        except OSError:
            return None

    def _command(self, *args):
        s = self._connect()
        if not s:
            return None
        try:
            s.sendall((json.dumps({"command": list(args)}) + "\n").encode())
            return True
        except OSError:
            self._sock = None
            return None

    def get_property(self, name, default=None):
        """Query an mpv property and return its value (or default).

        Bounded to ~1s total so a wedged mpv IPC can never stall the caller
        (and thus the UI main loop) for long. On any error/timeout the cached
        socket is dropped so the next call reconnects fresh."""
        s = self._connect()
        if not s:
            return default
        try:
            req = json.dumps({"command": ["get_property", name],
                              "request_id": 42}) + "\n"
            s.sendall(req.encode())
            s.settimeout(0.4)
            deadline = time.time() + 1.0
            buf = b""
            while time.time() < deadline:
                try:
                    chunk = s.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                # keep only whole lines; look for our reply
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        j = json.loads(line)
                    except ValueError:
                        continue
                    if j.get("request_id") == 42 and "error" in j:
                        if j.get("error") == "success":
                            return j.get("data", default)
                        return default
            # timed out without our reply -> assume the socket is unhealthy
            self._sock = None
            return default
        except OSError:
            self._sock = None
            return default

    def set_property(self, name, value):
        self._command("set_property", name, value)

    # -- movie playback controls ----------------------------------------
    def toggle_pause(self):
        self._command("cycle", "pause")

    def is_paused(self):
        return bool(self.get_property("pause", False))

    def time_pos(self):
        v = self.get_property("time-pos", 0)
        return float(v) if v is not None else 0.0

    def duration(self):
        v = self.get_property("duration", 0)
        return float(v) if v is not None else 0.0

    def seek_to(self, seconds):
        self._command("seek", float(seconds), "absolute")

    def seek_relative(self, seconds):
        self._command("seek", float(seconds), "relative")

    def seek_to_live(self):
        """Jump to the live edge (end of the buffer) of a live stream."""
        # Seek to the end of the demuxer cache (the live edge).
        state = self.get_property("demuxer-cache-state", {}) or {}
        end = state.get("cache-end")
        if isinstance(end, (int, float)) and end > 0:
            self._command("seek", float(end) - 1.0, "absolute")
        else:
            # fallback: large forward seek clamps to the end
            self._command("seek", 100000, "relative")

    def audio_tracks(self):
        return self._tracks("audio")

    def sub_tracks(self):
        return self._tracks("sub")

    def _tracks(self, ttype):
        tl = self.get_property("track-list", []) or []
        out = []
        for t in tl:
            if t.get("type") == ttype:
                out.append({
                    "id": t.get("id"),
                    "lang": t.get("lang", ""),
                    "title": t.get("title", ""),
                    "selected": bool(t.get("selected")),
                    "default": bool(t.get("default")),
                })
        return out

    def current_aid(self):
        return self.get_property("aid", None)

    def current_sid(self):
        return self.get_property("sid", None)

    def set_audio(self, aid):
        self._command("set_property", "aid", aid)

    def set_sub(self, sid):
        # sid can be an int id or "no"
        self._command("set_property", "sid", sid)

    # -- on-screen overlay (ASS) drawn over the video by mpv itself -----
    def osd_show(self, ass_text, overlay_id=1):
        self._command("osd-overlay", overlay_id, "ass-events", ass_text)

    def osd_clear(self, overlay_id=1):
        self._command("osd-overlay", overlay_id, "none", "")

    def osd_message(self, text, seconds=2.0):
        self._command("show-text", text, int(seconds * 1000))

    def volume_up(self):
        self._command("add", "volume", 5)

    def volume_down(self):
        self._command("add", "volume", -5)

    def toggle_mute(self):
        self._command("cycle", "mute")

    def stop(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self.is_running():
            try:
                self._command("quit")
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    self.proc.kill()
        self.proc = None
