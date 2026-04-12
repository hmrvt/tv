"""
Toddler TV - A simulated live TV experience for kids
Requires: pip install yt-dlp python-vlc pillow
Also requires VLC media player: https://www.videolan.org/

Project layout:
  toddlertv.py      <- main app (this file)
  config.py         <- channels, off periods, layout constants
  schedule.py       <- off-period logic
  media.py          <- ffmpeg/yt-dlp helpers, ChannelState
  images.py         <- thumbnail & avatar fetching
  robot_canvas.py   <- animated off-screen widget
  channels.json     <- channel definitions (overrides config.py defaults)
  cookies.txt       <- YouTube cookies for yt-dlp
"""

import sys
import time
import threading
import tkinter as tk
from datetime import datetime
from enum import Enum, auto

try:
    import vlc
except ImportError:
    print("ERROR: python-vlc not installed. Run: pip install python-vlc")
    sys.exit(1)

try:
    import yt_dlp  # noqa: F401
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

from config import CHANNELS, N, BEZEL_PAD
from schedule import is_tv_off, current_scene_name
from media import ChannelState, get_video_info, get_playlist_urls
from images import fetch_channel_avatar, get_cached_avatar, set_cached_avatar, is_avatar_fetched
from robot_canvas import RobotCanvas, RobotWorldCanvas
from web_remote import start_web_remote

import random


# ─────────────────────────────────────────────
#  APP STATE
# ─────────────────────────────────────────────

class AppState(Enum):
    BOOTING = auto()       # Initial startup, resolving first channel
    LOADING = auto()       # Switching channel, waiting for VLC
    PLAYING = auto()       # Video is playing normally
    SCHEDULE_OFF = auto()  # Off-period (robots shown)
    POWER_OFF = auto()     # User pressed power button


# ─────────────────────────────────────────────
#  PAUSABLE CLOCK
#
#  Tracks elapsed "play" time. Pauses whenever
#  the TV isn't actively playing video.
# ─────────────────────────────────────────────

class Clock:
    def __init__(self):
        self._accumulated = 0.0
        self._resume_time = None  # None = paused

    def resume(self):
        if self._resume_time is None:
            self._resume_time = time.time()

    def pause(self):
        if self._resume_time is not None:
            self._accumulated += time.time() - self._resume_time
            self._resume_time = None

    def elapsed(self) -> float:
        total = self._accumulated
        if self._resume_time is not None:
            total += time.time() - self._resume_time
        return total

    @property
    def is_running(self) -> bool:
        return self._resume_time is not None


# ─────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────

class ToddlerTV:

    # How many videos to resolve per channel at startup
    INITIAL_RESOLVE = 2
    # Resolve more when this many or fewer videos remain ahead
    LOOKAHEAD = 2
    # Seconds before a streaming URL is considered stale
    URL_TTL = 300

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Toddler TV")
        self.root.configure(bg="#1a0a2e")
        self.root.attributes("-fullscreen", True)

        # VLC
        # --network-caching=10000: 10-second buffer for network streams prevents
        # the end-of-stream stall where YouTube DASH chunks run out and VLC
        # freezes on the last frame while its audio buffer drains.
        # --file-caching=2000: 2-second buffer for local files.
        self.vlc_instance = vlc.Instance(
            "--quiet",
            "--network-caching=10000",
            "--file-caching=2000",
        )
        self.player = self.vlc_instance.media_player_new()

        # State
        self.state = AppState.BOOTING
        self.clock = Clock()  # paused at start
        self.states = [ChannelState() for _ in range(N)]
        self.current_channel = 0
        self._robot_world = None

        # Serialise yt-dlp calls to avoid rate-limiting
        self._resolve_lock = threading.Lock()

        # Build UI, then attach VLC to the video frame
        self._build_ui()
        self._attach_vlc_to_window()

        # Show robots while booting
        self._show_robots("fixing")

        # Start resolving channels in background (staggered)
        for i in range(N):
            self.root.after(
                i * 500,
                lambda idx=i: threading.Thread(
                    target=self._resolve_channel, args=(idx,), daemon=True
                ).start(),
            )

        # Start periodic checks
        self._check_schedule()
        self._poll_vlc()
        self._update_buttons()
        self._update_clock()

        # Start web remote for phone-based schedule control
        start_web_remote()

        # Key bindings
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<F11>", lambda e: self.root.attributes(
            "-fullscreen", not self.root.attributes("-fullscreen")
        ))
        # MK424 mini keyboard: A=power, B/C/D=channels
        for key in ("a", "A"):
            self.root.bind(f"<{key}>", lambda e: self._toggle_power())
        for key, idx in [("b", 0), ("B", 0), ("c", 1), ("C", 1), ("d", 2), ("D", 2)]:
            self.root.bind(f"<{key}>", lambda e, i=idx: self.switch_channel(i))

    # ─────────────────────────────────────────
    #  CLOCK
    # ─────────────────────────────────────────

    def elapsed(self) -> float:
        return self.clock.elapsed()

    # ─────────────────────────────────────────
    #  SCHEDULE
    # ─────────────────────────────────────────

    def _check_schedule(self):
        if self.state == AppState.POWER_OFF:
            pass  # user override, don't touch
        elif is_tv_off():
            if self.state != AppState.SCHEDULE_OFF:
                self._enter_schedule_off()
        else:
            if self.state == AppState.SCHEDULE_OFF:
                self._leave_schedule_off()
        self.root.after(30_000, self._check_schedule)

    def _enter_schedule_off(self):
        print("[schedule] TV turning off")
        self.clock.pause()
        self.player.stop()
        self.state = AppState.SCHEDULE_OFF
        self._show_robots(current_scene_name())
        self._start_mini_robots()
        for btn in self.channel_buttons:
            btn.configure(state="disabled", cursor="")

    def _leave_schedule_off(self):
        print("[schedule] TV turning on")
        self._stop_mini_robots()
        self._hide_robots()
        for btn in self.channel_buttons:
            btn.configure(state="normal", cursor="hand2")
        self._start_channel(self.current_channel)

    # ─────────────────────────────────────────
    #  POWER BUTTON
    # ─────────────────────────────────────────

    def _toggle_power(self):
        if self.state == AppState.POWER_OFF:
            self._power_on()
        else:
            self._power_off()

    def _power_off(self):
        print("[power] OFF")
        self.clock.pause()
        self.player.stop()
        self.state = AppState.POWER_OFF

        # Hide everything
        self._hide_robots()
        self._stop_mini_robots()
        self.video_frame.place_forget()

        # Dim buttons
        for cv in self.channel_buttons:
            cv.delete("all")
            w = cv.winfo_width() or 200
            h = cv.winfo_height() or 100
            cv.create_rectangle(0, 0, w, h, fill=cv._ch_dark, outline="")
            cv.configure(state="disabled", cursor="")

        self.channel_info_label.config(text="")
        self.power_btn.configure(fg="#333344")

    def _power_on(self):
        print("[power] ON")
        self.power_btn.configure(fg="#00ff88")
        for btn in self.channel_buttons:
            btn.configure(state="normal", cursor="hand2")

        # If schedule says off, go to schedule_off
        if is_tv_off():
            self._enter_schedule_off()
            return

        self._start_channel(self.current_channel)

    # ─────────────────────────────────────────
    #  MINI ROBOTS (bottom panel during off)
    # ─────────────────────────────────────────

    def _start_mini_robots(self):
        if self._robot_world:
            return
        world = RobotWorldCanvas(self.bottom_panel, CHANNELS)
        world.place(relx=0, rely=0, relwidth=1, relheight=1)
        world.start()
        self._robot_world = world

    def _stop_mini_robots(self):
        if self._robot_world:
            self._robot_world.stop()
            self._robot_world.place_forget()
            self._robot_world.destroy()
            self._robot_world = None

    # ─────────────────────────────────────────
    #  ROBOT / VIDEO FRAME VISIBILITY
    # ─────────────────────────────────────────

    def _show_robots(self, scene: str):
        self.video_frame.place_forget()
        self.robot_canvas.place(relx=0, rely=0, relwidth=1, relheight=0.76)
        if not self.robot_canvas._animating or self.robot_canvas.scene != scene:
            self.robot_canvas.start(scene)

    def _hide_robots(self):
        self.robot_canvas.stop()
        self.robot_canvas.place_forget()

    def _show_video(self):
        self._hide_robots()
        self.video_frame.place(relx=0, rely=0, relwidth=1, relheight=0.76)

    # ─────────────────────────────────────────
    #  CHANNEL RESOLUTION (background threads)
    # ─────────────────────────────────────────

    def _resolve_channel(self, index: int):
        """Resolve first few videos for a channel. Runs in background thread."""
        state = self.states[index]
        state.resolving = True
        ch = CHANNELS[index]

        # Get playlist URLs
        playlist_url = ch.get("playlist_url", "")
        if playlist_url:
            with self._resolve_lock:
                urls = get_playlist_urls(playlist_url)
            if not urls:
                print(f"[channel {index}] playlist_url empty, using playlist array")
                urls = ch.get("playlist", [])
        else:
            urls = ch.get("playlist", [])

        if not playlist_url:
            urls = urls.copy()
            random.shuffle(urls)

        urls = [u for u in urls if u.strip()]
        initial = urls[:self.INITIAL_RESOLVE]
        state.pending_urls = urls[self.INITIAL_RESOLVE:]

        for i, yt_url in enumerate(initial):
            if self.state == AppState.POWER_OFF:
                while self.state == AppState.POWER_OFF:
                    time.sleep(1)

            if i > 0:
                time.sleep(2)

            with self._resolve_lock:
                info = get_video_info(yt_url)

            if info == "RATE_LIMITED":
                print(f"[channel {index}] Rate-limited, waiting 60s...")
                time.sleep(60)
                with self._resolve_lock:
                    info = get_video_info(yt_url)
                if not info or info == "RATE_LIMITED":
                    state.pending_urls.insert(0, yt_url)
                    continue

            if info is None:
                continue

            self._add_video(state, index, info, yt_url)

        state.resolving = False
        print(f"[CH{index + 1}] {len(state.videos)} resolved, {len(state.pending_urls)} pending")

        # If all channels are done resolving (even if some failed), exit boot
        if self.state == AppState.BOOTING:
            all_done = all(not s.resolving for s in self.states)
            any_ready = any(s.ready for s in self.states)
            if all_done and any_ready:
                self.root.after(0, lambda: self._start_channel(self.current_channel))

    def _add_video(self, state: ChannelState, index: int, info: dict, yt_url: str):
        """Add a resolved video to channel state. Thread-safe for main-thread callbacks."""
        channel_id = info.get("channel_id", "")
        state.videos.append({
            "yt_url": yt_url,
            "stream_url": info["url"],
            "audio_url":  info.get("audio_url"),
            "duration": info["duration"],
            "title": info.get("title", ""),
            "channel": info.get("channel", ""),
            "channel_id": channel_id,
            "thumbnail": info.get("thumbnail", ""),
            "_resolved_at": time.time(),
            "_play_retries": 0,  # Track failed playback attempts
        })

        if channel_id and not is_avatar_fetched(channel_id):
            threading.Thread(
                target=fetch_channel_avatar, args=(channel_id,), daemon=True
            ).start()

        title = info.get("title", yt_url)
        duration = info["duration"]
        print(f"[CH{index + 1}] + \"{title}\" ({duration:.0f}s)")

        if not state.ready:
            state.ready = True

        state.queue_new_video(len(state.videos) - 1)

        # During boot, wait until ALL channels have at least one video
        # so the buttons can show avatars before we start playing.
        if self.state == AppState.BOOTING:
            all_ready = all(s.ready for s in self.states)
            if all_ready:
                self.root.after(0, lambda: self._start_channel(self.current_channel))

    def _resolve_more(self, index: int, count: int = 3):
        """Resolve more pending URLs for a channel. Background thread."""
        state = self.states[index]
        if state.resolving or not state.pending_urls:
            return
        state.resolving = True

        resolved = 0
        while resolved < count and state.pending_urls:
            if self.state == AppState.POWER_OFF:
                while self.state == AppState.POWER_OFF:
                    time.sleep(1)

            if resolved > 0:
                time.sleep(2)

            yt_url = state.pending_urls.pop(0)

            with self._resolve_lock:
                info = get_video_info(yt_url)

            if info == "RATE_LIMITED":
                print(f"[channel {index}] Rate-limited, requeueing")
                state.pending_urls.insert(0, yt_url)
                time.sleep(60)
                continue

            if info is None:
                continue

            self._add_video(state, index, info, yt_url)
            resolved += 1

        state.resolving = False

    def _maybe_resolve_ahead(self, index: int):
        """If running low on upcoming videos, resolve more in background."""
        state = self.states[index]
        if state.resolving or not state.pending_urls or not state.videos:
            return
        videos_ahead = len(state._unplayed)
        if videos_ahead <= self.LOOKAHEAD:
            threading.Thread(
                target=self._resolve_more, args=(index,), daemon=True
            ).start()

    # ─────────────────────────────────────────
    #  CHANNEL SWITCHING
    # ─────────────────────────────────────────

    def switch_channel(self, index: int):
        """Handle a channel button press. Locked during loading/off states."""
        # Only accept input when playing or booting
        if self.state not in (AppState.PLAYING, AppState.BOOTING):
            return
        if index == self.current_channel and self.state == AppState.PLAYING:
            return  # already on this channel
        self._start_channel(index)

    def _start_channel(self, index: int):
        """Begin playing a channel. Shows loading robots until video is ready."""
        self.player.stop()
        self.state = AppState.LOADING
        self.current_channel = index
        self._highlight_active_button(index)

        ch = CHANNELS[index]
        self.channel_info_label.config(
            text=f"  {ch['emoji']}  CH {index + 1}  \u00b7  {ch['name'].upper()}"
        )

        state = self.states[index]
        if not state.ready or not state.videos:
            self._show_robots("fixing")
            self._wait_for_channel_ready(index)
            return

        self._play_video_for_channel(index)

    def _wait_for_channel_ready(self, index: int):
        """Poll until the channel has at least one resolved video."""
        if self.current_channel != index or self.state != AppState.LOADING:
            return
        state = self.states[index]
        if state.ready and state.videos:
            self._play_video_for_channel(index)
        else:
            self.root.after(500, lambda: self._wait_for_channel_ready(index))

    def _play_video_for_channel(self, index: int):
        """Resolve the URL if needed and start VLC playback."""
        if self.current_channel != index or self.state != AppState.LOADING:
            return

        state = self.states[index]
        if not state._initialized:
            state.advance_video(self.elapsed())
        video_idx, offset_secs = state.get_position(self.elapsed())
        video_idx = min(video_idx, len(state.videos) - 1)
        video = state.videos[video_idx]
        seek_ms = int(offset_secs * 1000)

        stream_url = video["stream_url"]
        resolved_at = video.get("_resolved_at", 0)
        is_remote = stream_url.startswith("http") and "googlevideo" in stream_url
        is_stale = (time.time() - resolved_at) > self.URL_TTL
        
        # Track retry attempts for this video
        retry_count = video.get("_play_retries", 0)
        max_retries = 3

        title = video.get("title") or video["yt_url"]
        stale_tag = " [stale]" if is_stale else ""
        retry_tag = f" retry {retry_count}" if retry_count else ""
        print(f"[CH{index + 1}] ▶ \"{title}\" +{offset_secs:.0f}s{stale_tag}{retry_tag}")

        # Skip to next video if this one keeps failing
        if retry_count >= max_retries:
            print(f"[CH{index + 1}] skip \"{title}\" (failed {retry_count}x)")
            video["_failed"] = True
            state._unplayed = [i for i in state._unplayed
                               if not state.videos[i].get("_failed", False)]
            if all(v.get("_failed", False) for v in state.videos):
                for v in state.videos:
                    v["_failed"] = False
                    v["_play_retries"] = 0
                print(f"[channel {index}] All videos failed, resetting")
            state.advance_video(self.elapsed())
            self.root.after(0, lambda i=index: self._play_video_for_channel(i))
            return

        if is_remote and is_stale:
            self._show_robots("fixing")

            def _refresh():
                if self.current_channel != index:
                    return
                with self._resolve_lock:
                    info = get_video_info(video["yt_url"])
                if self.current_channel != index:
                    return
                if info and isinstance(info, dict):
                    video["stream_url"] = info["url"]
                    video["audio_url"]  = info.get("audio_url")
                    video["_resolved_at"] = time.time()
                    video["_play_retries"] = 0  # Reset on successful refresh
                elif info == "RATE_LIMITED":
                    print(f"[channel {index}] Rate-limited, using existing URL")
                else:
                    print(f"[channel {index}] Re-resolve failed, using existing URL")
                self.root.after(0, lambda: self._vlc_play(
                    video["stream_url"], index, seek_ms, video.get("audio_url")
                ))

            threading.Thread(target=_refresh, daemon=True).start()
        else:
            self._vlc_play(stream_url, index, seek_ms, video.get("audio_url"))

    def _vlc_play(self, url: str, channel_index: int, seek_ms: int, audio_url: str | None = None):
        """Hand URL to VLC and wait for it to start playing."""
        if self.current_channel != channel_index or self.state != AppState.LOADING:
            return

        self._show_robots("fixing")

        self.player.stop()
        media = self.vlc_instance.media_new(url)
        if audio_url:
            media.add_option(f":input-slave={audio_url}")
            # Each slave stream needs its own caching hint when using split
            # video+audio URLs, otherwise the audio can starve independently.
            media.add_option(":network-caching=10000")
        self.player.set_media(media)
        self.player.audio_set_mute(True)
        self.player.play()

        self._mute_gen = getattr(self, "_mute_gen", 0) + 1
        gen = self._mute_gen
        self.root.after(15_000, lambda: self._safety_unmute(gen))
        self.root.after(500, lambda: self._wait_vlc_playing(channel_index, seek_ms, 0))

    # ─────────────────────────────────────────
    #  VLC BUFFERING / SEEK HELPERS
    # ─────────────────────────────────────────

    def _safety_unmute(self, generation: int):
        if generation == self._mute_gen and self.player.audio_get_mute():
            print("[audio] safety unmute triggered")
            self.player.audio_set_mute(False)
            if self.state == AppState.LOADING:
                self._finish_loading()

    def _wait_vlc_playing(self, channel_index: int, seek_ms: int, attempts: int):
        """Wait for VLC to reach Playing state, then seek."""
        if self.current_channel != channel_index or self.state != AppState.LOADING:
            return

        vlc_state = self.player.get_state()

        if vlc_state in (vlc.State.Error, vlc.State.Ended):
            print(f"[vlc] Failed to start: {vlc_state}, retrying")
            # Increment retry counter for current video
            ch_state = self.states[channel_index]
            video_idx, _ = ch_state.get_position(self.elapsed())
            video_idx = min(video_idx, len(ch_state.videos) - 1)
            if video_idx < len(ch_state.videos):
                ch_state.videos[video_idx]["_play_retries"] = ch_state.videos[video_idx].get("_play_retries", 0) + 1
            self.root.after(2000, lambda: self._play_video_for_channel(channel_index))
            return

        if vlc_state == vlc.State.Playing:
            if seek_ms > 0:
                self.player.set_time(seek_ms)
                self.root.after(300, lambda: self._wait_seek_done(
                    channel_index, seek_ms, 0
                ))
            else:
                self._finish_loading()
            return

        # Still opening/buffering
        if attempts >= 50:  # 10 seconds
            print("[vlc] Timed out waiting for playback, retrying")
            self.root.after(1000, lambda: self._play_video_for_channel(channel_index))
            return

        self.root.after(200, lambda: self._wait_vlc_playing(
            channel_index, seek_ms, attempts + 1
        ))

    def _wait_seek_done(self, channel_index: int, target_ms: int, attempts: int):
        """Wait for VLC seek to land near the target."""
        if self.current_channel != channel_index or self.state != AppState.LOADING:
            return

        vlc_state = self.player.get_state()
        if vlc_state in (vlc.State.Error, vlc.State.Ended):
            print(f"[vlc] Error during seek: {vlc_state}")
            # Increment retry counter for current video
            ch_state = self.states[channel_index]
            video_idx, _ = ch_state.get_position(self.elapsed())
            video_idx = min(video_idx, len(ch_state.videos) - 1)
            if video_idx < len(ch_state.videos):
                ch_state.videos[video_idx]["_play_retries"] = ch_state.videos[video_idx].get("_play_retries", 0) + 1
            self.root.after(2000, lambda: self._play_video_for_channel(channel_index))
            return

        current_time = self.player.get_time()
        close_enough = current_time >= target_ms - 2000
        if close_enough or attempts >= 20:
            self._finish_loading()
        else:
            self.root.after(200, lambda: self._wait_seek_done(
                channel_index, target_ms, attempts + 1
            ))

    def _finish_loading(self):
        """VLC is playing and seeked. Unmute, show video, resume clock."""
        if self.state != AppState.LOADING:
            return
        self.player.audio_set_mute(False)
        self._show_video()
        self.clock.resume()
        self.state = AppState.PLAYING
        self._maybe_resolve_ahead(self.current_channel)

    # ─────────────────────────────────────────
    #  VLC POLL (video end / error recovery)
    # ─────────────────────────────────────────

    def _poll_vlc(self):
        if self.state == AppState.PLAYING:
            vlc_state = self.player.get_state()
            if vlc_state in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped):
                print(f"[poll] VLC {vlc_state}, restarting channel")
                index = self.current_channel
                ch_state = self.states[index]
                if ch_state.videos:
                    video_idx, _ = ch_state.get_position(self.elapsed())
                    video_idx = min(video_idx, len(ch_state.videos) - 1)
                    ch_state.videos[video_idx]["_resolved_at"] = 0
                    if vlc_state == vlc.State.Ended:
                        ch_state.advance_video(self.elapsed())
                self._start_channel(index)
            else:
                self._maybe_resolve_ahead(self.current_channel)

        # Advance inactive channels whose current video has ended by elapsed time
        elapsed = self.elapsed()
        for i, ch_state in enumerate(self.states):
            if i == self.current_channel or not ch_state.videos:
                continue
            if not ch_state._initialized:
                ch_state.advance_video(elapsed)
                self._maybe_resolve_ahead(i)
                continue
            video = ch_state.videos[ch_state._current_idx]
            if elapsed - ch_state._video_start_elapsed >= video["duration"]:
                ch_state.advance_video(elapsed)
                self._maybe_resolve_ahead(i)

        self.root.after(1000, self._poll_vlc)

    # ─────────────────────────────────────────
    #  VLC EMBEDDING
    # ─────────────────────────────────────────

    def _attach_vlc_to_window(self):
        self.root.update()
        wid = self.video_frame.winfo_id()
        if sys.platform == "win32":
            self.player.set_hwnd(wid)
        elif sys.platform == "darwin":
            self.player.set_nsobject(wid)
        else:
            self.player.set_xwindow(wid)

    # ─────────────────────────────────────────
    #  UI CONSTRUCTION
    # ─────────────────────────────────────────

    def _build_ui(self):
        p = BEZEL_PAD
        self.main_frame = tk.Frame(self.root, bg="#0d0721")
        self.main_frame.place(x=p, y=p, relwidth=1, relheight=1, width=-p*2, height=-p*2)

        VIDEO_RELH = 0.76
        BUTTON_RELY = 0.76
        BUTTON_RELH = 0.24

        # Video frame
        self.video_frame = tk.Frame(self.main_frame, bg="black")
        self.video_frame.place(relx=0, rely=0, relwidth=1, relheight=VIDEO_RELH)

        self.static_canvas = tk.Canvas(self.video_frame, bg="black", highlightthickness=0)
        self.static_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)

        # Robot canvas (hidden initially, placed over video frame when needed)
        self.robot_canvas = RobotCanvas(self.main_frame)

        # Top bar
        self.channel_bar = tk.Frame(self.main_frame, bg="#1a0a2e", height=80)
        self.channel_bar.place(relx=0, rely=0, relwidth=1)

        self.channel_info_label = tk.Label(
            self.channel_bar, text="",
            font=("Courier New", 25, "bold"), fg="#00ff88", bg="#1a0a2e", pady=8,
        )
        self.channel_info_label.pack(side="left", padx=20)

        self.clock_label = tk.Label(
            self.channel_bar, text="",
            font=("Courier New", 18, "bold"), fg="#888888", bg="#1a0a2e", pady=8,
        )
        self.clock_label.pack(side="right", padx=20)

        # Power button
        self.power_btn = tk.Label(
            self.channel_bar, text="\u23fb", cursor="hand2",
            font=("Courier New", 30, "bold"), fg="#00ff88", bg="#1a0a2e", pady=4, padx=12,
        )
        self.power_btn.pack(side="right")
        self.power_btn.bind("<Button-1>", lambda e: self._toggle_power())
        self.power_btn.bind("<Enter>", lambda e: self.power_btn.configure(fg="#ff6b6b"))
        self.power_btn.bind("<Leave>", lambda e: self.power_btn.configure(
            fg="#333344" if self.state == AppState.POWER_OFF else "#00ff88"
        ))

        # Bottom panel
        self.bottom_panel = tk.Frame(self.main_frame, bg="#0d0721", pady=2)
        self.bottom_panel.place(relx=0, rely=BUTTON_RELY, relwidth=1, relheight=BUTTON_RELH)

        tk.Label(
            self.bottom_panel, text="\U0001f4fa  MY TV",
            font=("Courier New", 11, "bold"), fg="#444466", bg="#0d0721",
        ).pack(side="top", pady=(2, 0))

        self.buttons_frame = tk.Frame(self.bottom_panel, bg="#0d0721")
        self.buttons_frame.pack(expand=True, fill="both", padx=12, pady=2)

        self.channel_buttons = [
            self._make_channel_button(i, ch)
            for i, ch in enumerate(CHANNELS)
        ]

        self._draw_scanlines()

    def _make_channel_button(self, index: int, channel: dict) -> tk.Canvas:
        color = channel.get("color", "#555555")
        dark = self._darken(color)

        outer = tk.Frame(self.buttons_frame, bg="#0d0721", padx=4)
        outer.pack(side="left", expand=True, fill="both")

        cv = tk.Canvas(outer, bg=dark, highlightthickness=2,
                       highlightbackground=dark, cursor="hand2")
        cv.pack(expand=True, fill="both")
        cv.bind("<Button-1>", lambda e, i=index: self.switch_channel(i))
        cv.bind("<Enter>", lambda e, c=cv, col=color: c.configure(bg=col))
        cv.bind("<Leave>", lambda e, c=cv, col=dark: c.configure(bg=col))

        cv._ch_index = index
        cv._ch_color = color
        cv._ch_dark = dark
        cv._thumb = None
        return cv

    def _draw_scanlines(self):
        self.root.update_idletasks()
        w = self.video_frame.winfo_width() or self.root.winfo_screenwidth()
        h = self.video_frame.winfo_height() or int(self.root.winfo_screenheight() * 0.76)
        for y in range(0, h, 4):
            self.static_canvas.create_line(0, y, w, y, fill="#000033", width=1)

    # ─────────────────────────────────────────
    #  UI UPDATES
    # ─────────────────────────────────────────

    def _highlight_active_button(self, index: int):
        for i, cv in enumerate(self.channel_buttons):
            is_active = (i == index)
            cv.configure(
                highlightthickness=3 if is_active else 1,
                highlightbackground=CHANNELS[i].get("color", "#555") if is_active else "#222244",
            )

    def _update_buttons(self):
        if self.state != AppState.POWER_OFF:
            for i, cv in enumerate(self.channel_buttons):
                self._draw_channel_button(i, cv)
        self.root.after(500, self._update_buttons)

    def _update_clock(self):
        self.clock_label.config(text=datetime.now().strftime("%H:%M") + "  ")
        self.root.after(30_000, self._update_clock)

    def _draw_channel_button(self, index: int, cv: tk.Canvas):
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 10:
            w = cv.winfo_reqwidth()
        if h < 10:
            h = cv.winfo_reqheight()
        if w < 10 or h < 10:
            self.root.after(300, lambda: self._draw_channel_button(index, cv))
            return

        is_active = (index == self.current_channel)
        color = cv._ch_color
        dark = cv._ch_dark
        ch_state = self.states[index]
        ch = CHANNELS[index]
        bar_h = 20
        pad = 6

        # Background
        cv.create_rectangle(0, 0, w, h, fill=dark, outline="")

        if not ch_state.ready or not ch_state.videos:
            cv.create_text(w // 2, h // 2 - 10, text=ch["emoji"],
                           font=("", 26), anchor="center")
            cv.create_text(w // 2, h - 14, text="loading...",
                           fill="#444466", font=("Courier New", 9), anchor="center")
            cv.create_rectangle(0, h - bar_h, w, h, fill=dark, outline="")
            return

        video_idx, offset = ch_state.get_position(self.elapsed())
        video_idx = min(video_idx, len(ch_state.videos) - 1)
        video = ch_state.videos[video_idx]
        duration = video["duration"] or 1
        progress = min(1.0, offset / duration)
        remaining = duration - offset
        next_idx = ch_state._unplayed[0] if ch_state._unplayed else video_idx
        next_video = ch_state.videos[next_idx]

        # Current avatar (centered)
        cur_channel_id = video.get("channel_id", "")
        av_size = min(h // 2, w // 3, 96)
        cur_avatar = self._get_avatar_image(cur_channel_id, cv, "_cur_av")
        av_cx = w // 2
        av_cy = (h - bar_h) // 2

        cv.create_rectangle(av_cx - av_size // 2 - 3, av_cy - av_size // 2 - 3,
                            av_cx + av_size // 2 + 3, av_cy + av_size // 2 + 3,
                            fill="#000000", outline="")
        if cur_avatar:
            cv.create_image(av_cx, av_cy, image=cur_avatar, anchor="center")
        else:
            cv.create_rectangle(av_cx - av_size // 2, av_cy - av_size // 2,
                                av_cx + av_size // 2, av_cy + av_size // 2,
                                fill=dark, outline="")
            cv.create_text(av_cx, av_cy,
                           text="\u25b6", font=("", max(14, av_size // 2)),
                           anchor="center")

        # Next-up avatar (bottom-right)
        next_channel_id = next_video.get("channel_id", "")
        small_av = min(h // 4, w // 7, 48)
        next_avatar = self._get_avatar_image(next_channel_id, cv, "_next_av")
        next_ax = w - pad - small_av
        next_ay = h - bar_h - pad - small_av
        cv.create_rectangle(next_ax - 2, next_ay - 2,
                            next_ax + small_av + 2, next_ay + small_av + 2,
                            fill="#000000", outline="")
        if next_avatar:
            cv.create_image(next_ax + small_av // 2, next_ay + small_av // 2,
                            image=next_avatar, anchor="center")
        else:
            cv.create_rectangle(next_ax, next_ay,
                                next_ax + small_av, next_ay + small_av,
                                fill=dark, outline="")
            cv.create_text(next_ax + small_av // 2, next_ay + small_av // 2,
                           text=ch["emoji"], font=("", max(8, small_av // 2)),
                           anchor="center")

        # Channel icon overlay (top-left)
        icon_size = max(28, int(min(w, h) * 0.38))
        icon_x = -icon_size // 6
        icon_y = -icon_size // 6
        icon_cx = icon_x + icon_size // 2
        icon_cy = icon_y + icon_size // 2

        if is_active:
            cv.create_oval(icon_x, icon_y,
                           icon_x + icon_size, icon_y + icon_size,
                           fill=color, outline="#ffffff", width=2)
            cv.create_text(icon_cx, icon_cy, text=ch["emoji"],
                           font=("", max(14, icon_size // 2)),
                           anchor="center")
        else:
            dim_color = self._darken(self._darken(color))
            cv.create_oval(icon_x, icon_y,
                           icon_x + icon_size, icon_y + icon_size,
                           fill=dim_color, outline="#222244", width=1)
            cv.create_text(icon_cx, icon_cy, text=ch["emoji"],
                           font=("", max(14, icon_size // 2)),
                           fill="#444466", anchor="center")

        # Time remaining
        mins = int(remaining) // 60
        secs = int(remaining) % 60
        cv.create_text(w - pad - 2, h - bar_h - 4,
                       text=f"{mins}:{secs:02d}",
                       fill="white", font=("Courier New", 9, "bold"),
                       anchor="se")

        # Progress bar
        cv.create_rectangle(0, h - bar_h, w, h, fill="#000000", outline="")
        fill_w = max(4, int(w * progress))
        cv.create_rectangle(0, h - bar_h, fill_w, h, fill=color, outline="")

        cv.update_idletasks()

    # ─────────────────────────────────────────
    #  AVATAR HELPERS
    # ─────────────────────────────────────────

    def _get_avatar_image(self, channel_id: str, cv: tk.Canvas, key: str = "_av"):
        """Get an avatar ImageTk.PhotoImage for a channel_id.
        Converts PIL->ImageTk on the main thread and stores on cv to prevent GC."""
        if not channel_id:
            return None

        stored_id = getattr(cv, f"{key}_cid", "")
        stored_img = getattr(cv, f"{key}_photo", None)
        if stored_id == channel_id and stored_img is not None:
            return stored_img

        cached = get_cached_avatar(channel_id)
        if cached and cached is not False:
            from PIL import ImageTk as _ImageTk, Image as _Image
            if isinstance(cached, _Image.Image):
                try:
                    photo = _ImageTk.PhotoImage(cached)
                    set_cached_avatar(channel_id, photo)
                    setattr(cv, f"{key}_cid", channel_id)
                    setattr(cv, f"{key}_photo", photo)
                    return photo
                except Exception:
                    return None
            setattr(cv, f"{key}_cid", channel_id)
            setattr(cv, f"{key}_photo", cached)
            return cached
        elif not is_avatar_fetched(channel_id):
            fetching_id = getattr(cv, f"{key}_fetching", "")
            if channel_id != fetching_id:
                setattr(cv, f"{key}_fetching", channel_id)

                def _fetch_av(cid=channel_id, canvas=cv, k=key):
                    pil_img = fetch_channel_avatar(cid, size=96)
                    if pil_img:
                        def _convert(img=pil_img, c=cid):
                            try:
                                from PIL import ImageTk as _ImageTk
                                photo = _ImageTk.PhotoImage(img)
                                set_cached_avatar(c, photo)
                                setattr(canvas, f"{k}_cid", c)
                                setattr(canvas, f"{k}_photo", photo)
                            except Exception:
                                pass
                        canvas.after(0, _convert)
                threading.Thread(target=_fetch_av, daemon=True).start()
        return None

    # ─────────────────────────────────────────
    #  COLOUR HELPERS
    # ─────────────────────────────────────────

    def _lighten(self, hex_color: str) -> str:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"#{min(255,r+40):02x}{min(255,g+40):02x}{min(255,b+40):02x}"

    def _darken(self, hex_color: str) -> str:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return f"#{max(0,r-60):02x}{max(0,g-60):02x}{max(0,b-60):02x}"

    def _on_escape(self, event):
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
        else:
            self.player.stop()
            self.root.destroy()


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = ToddlerTV(root)
    root.mainloop()
