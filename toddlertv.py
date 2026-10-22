"""
Toddler TV - A simulated live TV experience for kids

Setup
-----
1. Install VLC media player: https://www.videolan.org/
2. Install Python dependencies:
     pip install -r requirements.txt
3. (Windows only) Allow inbound connections on port 8080 so phones on the
   same network can reach the web remote. Run once in an elevated PowerShell:
     netsh advfirewall firewall add rule name="Toddler TV Remote" ^
       dir=in action=allow protocol=TCP localport=8080 profile=private
   Without this rule Windows Firewall will silently block the web remote.

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
import traceback
from datetime import datetime
from enum import Enum, auto

# Console output must never be able to raise.  Several status lines contain
# non-ASCII (em dashes, "▶"), and on Windows stdout defaults to cp1252 whenever
# it is redirected — encoding those raises UnicodeEncodeError.  Raised from
# inside a Tk `after` callback that ends the whole periodic chain, which froze
# the UI when an off period ended (_leave_schedule_off -> _start_channel).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # not a real stream (pytest capture, pythonw.exe) — nothing to do

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
from media import (
    ChannelState, get_video_info, get_cached_title, get_playlist_title,
    get_video_id, find_local_file, delete_local_file, forget_metadata,
    list_local_video_ids, playlist_url, _fail_log,
)
import library
import downloads
from images import fetch_channel_avatar, get_cached_avatar, set_cached_avatar, is_avatar_fetched
from robot_canvas import RobotCanvas
from scene_video import SceneVideoPlayer, available_scenes
from web_remote import start_web_remote, get_remote_url, register_channel_callbacks

import random


# How often the schedule is re-checked.  Also how quickly a scene edited on the
# web remote appears on screen, so it is seconds rather than the old 30s.
# schedule._load_periods() caches on mtime, so a poll is just a stat() call.
_SCHEDULE_POLL_MS = 3_000


def _watch_url(video_id: str) -> str:
    """Watch URL for a catalog video ID (already validated by library.py)."""
    return f"https://www.youtube.com/watch?v={video_id}"


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

    # Seconds before current video ends to pre-fetch the next stream URL.
    # Close enough to playback that the URL never expires; far enough for
    # yt-dlp to finish before the video ends.
    PREFETCH_WINDOW = 180
    # Seconds before a streaming URL is considered stale (YouTube DASH ~6 h).
    URL_TTL = 21600
    # How long an inactive channel sits on a position whose duration isn't known
    # yet.  The channel button scales its progress bar by the same number, so the
    # bar fills exactly as the channel gives up and moves on.
    UNRESOLVED_VIDEO_ESTIMATE = 900.0
    # Minutes between automatic syncs of each channel's YouTube source
    # playlists.  New videos are queued up next; a video taken off YouTube
    # stays on the channel (only the web remote removes videos).
    PLAYLIST_REFRESH_MINS = 30
    # Seconds after start-up before the first source sync, so it doesn't
    # compete with the first channel's stream.
    FIRST_SYNC_DELAY_S = 90
    # Duration of end-of-stream audio fade in ms.  Masks the hard cut caused
    # by VLC input-slave terminating the M4A audio stream when the MP4 video
    # master reaches EOS — the two DASH segment boundaries don't align exactly.
    _FADE_MS = 4000

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
        # Ensure the boot-start sequence is only scheduled once
        self._boot_start_scheduled = False
        # Replaced in _build_ui; None means "fall back to the drawn canvas".
        self.scene_video = None
        # Scene currently shown during an off period, so a change made on the
        # web remote can be detected and applied without restarting.
        self._off_scene = None

        # Per-channel locks so each channel resolves independently without
        # blocking the others (channels share no yt-dlp state at runtime).
        self._resolve_locks = [threading.Lock() for _ in range(N)]

        # Playlist-refresh bookkeeping.  _refresh_lock is held for the whole
        # refresh pass so the periodic timer and the web remote can't overlap.
        self._refresh_lock = threading.Lock()
        self._refresh_running = False
        self._last_refresh = 0.0
        self._last_refresh_summary = ""

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

        # Background downloads: new playlist videos land in videos/ while the
        # app runs, so downloader.py is no longer a prerequisite for updates.
        downloads.manager.start(gate=self._downloads_allowed)

        # Start periodic checks.  Channels play from the saved catalog, so the
        # first sync for new YouTube additions can wait until boot settles.
        self.root.after(self.FIRST_SYNC_DELAY_S * 1000, self._start_playlist_refresh)
        self._schedule_playlist_refresh()
        self._check_schedule()
        self._poll_vlc()
        self._update_buttons()
        self._update_clock()

        # Start web remote for phone-based schedule control
        start_web_remote()
        register_channel_callbacks({
            "get":       self._web_get_channel_info,
            "skip":      self._web_skip_video,
            "play_next": self._web_queue_video,
            "remove":    self._web_remove_video,
            "add":       self._web_add_to_playlist,
            "refresh":   self._web_refresh_playlist,
            "library":   self._web_library_status,
            "catalog":   self._web_catalog,
            "ignore":    self._web_ignore_video,
            "restore":   self._web_restore_video,
            "add_source":    self._web_add_source,
            "remove_source": self._web_remove_source,
            "save_channel":  self._web_save_channel,
            "merge":     self._web_merge_channel,
            "tv":        self._web_tv_state,
        })

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
        # The body is wrapped so that an unexpected error can never stop the
        # chain: the reschedule lives in `finally`.  Losing this timer used to
        # freeze the whole UI, since it also drives entering/leaving off mode.
        try:
            if self.state == AppState.POWER_OFF:
                pass  # user override, don't touch
            elif is_tv_off():
                if self.state != AppState.SCHEDULE_OFF:
                    self._enter_schedule_off()
                else:
                    # Already off — pick up a scene edited from the web remote.
                    scene = current_scene_name()
                    if scene != self._off_scene:
                        print(f"[schedule] Scene changed to '{scene}'")
                        self._off_scene = scene
                        self._show_robots(scene)
            else:
                if self.state == AppState.SCHEDULE_OFF:
                    self._leave_schedule_off()
        except Exception:                       # noqa: BLE001 - never stop the chain
            print("[schedule] check failed:")
            traceback.print_exc()
        finally:
            self.root.after(_SCHEDULE_POLL_MS, self._check_schedule)

    def _enter_schedule_off(self):
        print("[schedule] TV turning off")
        self.clock.pause()
        self.player.stop()
        self.state = AppState.SCHEDULE_OFF
        scene = current_scene_name()
        self._off_scene = scene
        self._hide_channel_buttons()
        self._show_robots(scene)

    def _leave_schedule_off(self):
        print("[schedule] TV turning on")
        self._off_scene = None
        self._hide_robots()
        self._show_channel_buttons()
        self._start_channel(self.current_channel)

    # ─────────────────────────────────────────
    #  BOTTOM PANEL VISIBILITY
    # ─────────────────────────────────────────

    def _hide_channel_buttons(self):
        """Clear the bottom panel so the scene's info banner can own it."""
        self.panel_header.pack_forget()
        self.buttons_frame.pack_forget()
        for btn in self.channel_buttons:
            btn.configure(state="disabled", cursor="")

    def _show_channel_buttons(self):
        self.panel_header.pack(side="top", pady=(3, 0))
        self.buttons_frame.pack(expand=True, fill="both", padx=10, pady=(2, 6))
        for btn in self.channel_buttons:
            btn.configure(state="normal", cursor="hand2")

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
        self.video_frame.place_forget()

        # Dim buttons
        for cv in self.channel_buttons:
            cv.delete("all")
            w = cv.winfo_width() or 200
            h = cv.winfo_height() or 100
            cv.create_rectangle(0, 0, w, h, fill=cv._ch_dark, outline="")
            cv.configure(state="disabled", cursor="")

        self.channel_info_label.config(text="")
        self.ch_badge.config(text="", bg="#100828")
        self.live_label.config(text="")
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
    #  ROBOT / VIDEO FRAME VISIBILITY
    # ─────────────────────────────────────────

    def _show_robots(self, scene: str):
        self.video_frame.place_forget()

        # Prefer the rendered scene clip; fall back to the drawn canvas when the
        # file is missing or VLC won't take it, so a deleted clip degrades the
        # picture instead of blanking the screen.
        if self.scene_video is not None:
            if self.scene_video.playing and self.scene_video.scene == scene:
                return
            self.scene_video.stop()
            self.robot_canvas.stop()
            self.robot_canvas.place_forget()
            if self.scene_video.start(scene):
                return
            print(f"[scene] No clip for '{scene}' - using drawn canvas")

        self.robot_canvas.place(relx=0, rely=0, relwidth=1, relheight=0.76)
        if not self.robot_canvas._animating or self.robot_canvas.scene != scene:
            self.robot_canvas.start(scene)

    def _hide_robots(self):
        if self.scene_video is not None:
            self.scene_video.stop()
        self.robot_canvas.stop()
        self.robot_canvas.place_forget()

    def _show_video(self):
        self._hide_robots()
        self.video_frame.place(relx=0, rely=0, relwidth=1, relheight=0.76)

    # ─────────────────────────────────────────
    #  CHANNEL RESOLUTION (background threads)
    # ─────────────────────────────────────────

    def _resolve_channel(self, index: int):
        """Fetch playlist and resolve the first video. Runs in background thread."""
        state = self.states[index]
        state.resolving = True
        # The saved catalog is the playlist; no network needed to start.
        state.set_playlist([_watch_url(v) for v in library.video_ids(index)])

        # Build shuffled play queue of playlist position integers
        indices = state.active_positions()
        random.shuffle(indices)
        state._play_queue = indices
        # Debug: log initial shuffled play queue for this channel
        try:
            print(f"[CH{index + 1}] initial shuffled queue (first 10): {state._play_queue[:10]}")
        except Exception:
            pass

        if not indices:
            state.resolving = False
            if self.state == AppState.BOOTING:
                all_done = all(not s.resolving for s in self.states)
                any_ready = any(s.ready for s in self.states)
                if all_done and any_ready and not getattr(self, "_boot_start_scheduled", False):
                    self._boot_start_scheduled = True
                    self.root.after(0, lambda: self._start_channel(self.current_channel))
            return

        # Try videos from the front of the play queue until one resolves.
        # A geo-blocked or unavailable first video would otherwise leave
        # state.ready=False and freeze the boot sequence indefinitely.
        info = None
        first_pl_idx = None
        first_url = None

        for _ in range(min(5, len(state._play_queue))):
            candidate_pl_idx = state._play_queue[0]
            candidate_url = state.url_at(candidate_pl_idx)

            if self.state == AppState.POWER_OFF:
                while self.state == AppState.POWER_OFF:
                    time.sleep(1)

            with self._resolve_locks[index]:
                candidate_info = get_video_info(candidate_url)

            if candidate_info == "RATE_LIMITED":
                print(f"[channel {index}] Rate-limited, waiting 60s...")
                time.sleep(60)
                with self._resolve_locks[index]:
                    candidate_info = get_video_info(candidate_url)
                if not candidate_info or candidate_info == "RATE_LIMITED":
                    break  # back off; remaining videos tried at playback time

            if candidate_info:
                info = candidate_info
                first_pl_idx = candidate_pl_idx
                first_url = candidate_url
                break

            # Permanently unavailable — mark failed and try the next one.
            state._failed_positions.add(candidate_pl_idx)
            state._play_queue.pop(0)
            _fail_log.warning("CH%d | pl_idx=%d | %s", index + 1, candidate_pl_idx, candidate_url)
            print(f"[CH{index + 1}] startup: skip pl_idx={candidate_pl_idx} — unavailable")

        if info:
            self._add_video(state, index, info, first_url, first_pl_idx)

        state.resolving = False
        print(f"[CH{index + 1}] ready — {len(indices)} videos in playlist")

        # Fill in anything this channel is missing from videos/, lowest priority.
        self._enqueue_backlog(index)

        if self.state == AppState.BOOTING:
            all_done = all(not s.resolving for s in self.states)
            if all_done and not getattr(self, "_boot_start_scheduled", False):
                self._boot_start_scheduled = True
                self.root.after(0, lambda: self._start_channel(self.current_channel))

    def _add_video(self, state: ChannelState, index: int, info: dict, yt_url: str, playlist_idx: int):
        """Add a resolved video to channel state and register it in _resolved_map."""
        channel_id = info.get("channel_id", "")
        video_list_idx = len(state.videos)
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
            "_play_retries": 0,
            "_playlist_idx": playlist_idx,
        })
        state._resolved_map[playlist_idx] = video_list_idx

        # If this is the video currently queued to play, update the pointer now.
        if state._current_playlist_idx == playlist_idx:
            state._current_idx = video_list_idx

        if channel_id and not is_avatar_fetched(channel_id):
            threading.Thread(
                target=fetch_channel_avatar, args=(channel_id,), daemon=True
            ).start()

        title = info.get("title", yt_url)
        duration = info["duration"]
        print(f"[CH{index + 1}] + \"{title}\" ({duration:.0f}s)")

        if not state.ready:
            state.ready = True

        if self.state == AppState.BOOTING:
            all_ready = all(s.ready for s in self.states)
            if all_ready:
                self.root.after(0, lambda: self._start_channel(self.current_channel))

    def _resolve_for_playback(self, index: int, pl_idx: int, yt_url: str):
        """Resolve stream URL for a playlist position. Used for prefetch and inline resolution."""
        state = self.states[index]

        if self.state == AppState.POWER_OFF:
            while self.state == AppState.POWER_OFF:
                time.sleep(1)

        with self._resolve_locks[index]:
            info = get_video_info(yt_url)

        if info == "RATE_LIMITED":
            print(f"[channel {index}] Rate-limited, waiting 60s...")
            time.sleep(60)
            with self._resolve_locks[index]:
                info = get_video_info(yt_url)
            if not info or info == "RATE_LIMITED":
                info = None

        if info and info != "RATE_LIMITED":
            self._add_video(state, index, info, yt_url, pl_idx)
        else:
            # Permanent failure (unavailable, geo/copyright block, or repeated rate-limit).
            # Mark so _play_video_for_channel skips past it instead of retrying forever.
            state._failed_positions.add(pl_idx)
            print(f"[CH{index + 1}] skip pl_idx={pl_idx} — marked unavailable")
            _fail_log.warning("CH%d | pl_idx=%d | %s", index + 1, pl_idx, yt_url)

        state.resolving = False

    # ─────────────────────────────────────────
    #  SOURCE PLAYLIST SYNC
    # ─────────────────────────────────────────

    def _join_feed(self, index: int, video_ids: list[str]) -> None:
        """Bring videos that just joined the catalog into the live feed.

        They play right after whatever is on screen, shuffled so they don't
        arrive in playlist order, and are downloaded ahead of the backlog.
        """
        state = self.states[index]
        if self._reload_if_empty(index):
            return
        positions = state.add_members([_watch_url(v) for v in video_ids])
        if not positions:
            return
        order = list(positions)
        random.shuffle(order)
        for pos in reversed(order):
            state.insert_next(pos)
        downloads.manager.enqueue_many(self._video_ids_at(state, order), front=True)

    def _reload_if_empty(self, index: int) -> bool:
        """Start a channel that had nothing to play now that its catalog has videos.

        Returns True if a reload was started; _resolve_channel then rebuilds the
        feed from the catalog, so the caller has nothing more to do.
        """
        state = self.states[index]
        if state.active_positions() or state.resolving or not library.video_ids(index):
            return False
        state.resolving = True
        threading.Thread(target=self._resolve_channel, args=(index,), daemon=True).start()
        return True

    def _schedule_playlist_refresh(self) -> None:
        self.root.after(
            self.PLAYLIST_REFRESH_MINS * 60_000, self._periodic_playlist_refresh
        )

    def _periodic_playlist_refresh(self) -> None:
        # Rearm unconditionally: a refresh that is skipped because one is already
        # running must not break the timer chain.
        self._start_playlist_refresh()
        self._schedule_playlist_refresh()

    def _start_playlist_refresh(self) -> bool:
        """Kick off a background sync of every channel. False if one is running."""
        if not self._refresh_lock.acquire(blocking=False):
            return False
        self._refresh_running = True
        threading.Thread(
            target=self._refresh_worker, name="playlist-refresh", daemon=True
        ).start()
        return True

    def _refresh_worker(self) -> None:
        try:
            added = 0
            all_ok = True
            for i in range(N):
                ch_added, ok = self._refresh_channel(i)
                added += ch_added
                all_ok = all_ok and ok
                self._enqueue_backlog(i)

            pruned = self._prune_orphan_downloads()

            self._last_refresh = time.time()
            parts = []
            if added:
                parts.append(f"+{added} new")
            if pruned:
                parts.append(f"{pruned} file(s) cleaned")
            if not all_ok:
                parts.append("a playlist could not be fetched")
            self._last_refresh_summary = ", ".join(parts) or "no changes"
            print(f"[refresh] {self._last_refresh_summary}")
        finally:
            self._refresh_running = False
            self._refresh_lock.release()

    def _refresh_channel(self, index: int) -> tuple[int, bool]:
        """Pull new videos from one channel's YouTube source playlists.

        Returns (added, ok).  ok is False if any source fetch failed.  This only
        ever adds: a video taken off YouTube stays on the channel, and a video
        the user ignored on the web remote is not brought back.
        """
        ch = library.get_channel(index)
        if not ch:
            return 0, True

        new_ids: list[str] = []
        ok = True
        for src in ch["sources"]:
            pid = src["playlist_id"]
            with self._resolve_locks[index]:
                ids = library.fetch_source_ids(pid)
            if not ids:
                print(f"[CH{index + 1}] sync: {pid} came back empty, skipped")
                ok = False
                continue
            new_ids += library.add_from_source(index, pid, ids)

        if new_ids:
            print(f"[CH{index + 1}] sync: +{len(new_ids)} new")
            self._join_feed(index, new_ids)
        return len(new_ids), ok

    # ─────────────────────────────────────────
    #  LOCAL LIBRARY UPKEEP
    # ─────────────────────────────────────────

    def _downloads_allowed(self) -> bool:
        """Gate for the background downloader.

        Downloading competes with the stream VLC is buffering, so hold off
        while a channel is starting up.
        """
        return self.state not in (AppState.BOOTING, AppState.LOADING)

    @staticmethod
    def _video_ids_at(state, positions) -> list[str]:
        """Map playlist positions to validated video IDs, skipping unusable ones."""
        ids: list[str] = []
        for pos in positions:
            try:
                ids.append(get_video_id(state.url_at(pos)))
            except ValueError:
                continue
        return ids

    def _on_screen_video_ids(self) -> set[str]:
        """Video IDs any channel currently points at — never delete these."""
        ids: set[str] = set()
        for state in self.states:
            try:
                ids.add(get_video_id(state.url_at(state._current_playlist_idx)))
            except ValueError:
                continue
        return ids

    def _enqueue_backlog(self, index: int) -> None:
        """Queue every feed video that has no local file yet, behind new arrivals."""
        state = self.states[index]
        missing = []
        for video_id in sorted(state.feed_video_ids()):
            try:
                if not find_local_file(video_id):
                    missing.append(video_id)
            except ValueError:
                continue
        queued = downloads.manager.enqueue_many(missing)
        if queued:
            print(f"[CH{index + 1}] {queued} video(s) queued for background download")

    def _discard_local_copy(self, url: str) -> None:
        """Delete the download and cached metadata for a video that left the feed."""
        if not url:
            return
        try:
            video_id = get_video_id(url)
        except ValueError:
            return
        if video_id in self._on_screen_video_ids():
            return  # still on screen; the next sweep will collect it
        if video_id in library.all_video_ids():
            return  # another channel still plays it
        downloads.manager.cancel(video_id)
        try:
            if delete_local_file(video_id):
                forget_metadata(video_id)
        except ValueError as e:
            print(f"[library] {e}")

    def _prune_orphan_downloads(self) -> int:
        """Delete downloaded files that no channel's catalog refers to any more."""
        keep = library.all_video_ids()
        if not keep:
            # An empty or unreadable catalog says nothing about what to keep;
            # deleting on that basis would wipe a healthy library.
            print("[library] Prune skipped — channel catalog is empty")
            return 0
        keep |= self._on_screen_video_ids()

        deleted = 0
        for video_id in sorted(list_local_video_ids() - keep):
            try:
                if delete_local_file(video_id):
                    forget_metadata(video_id)
                    deleted += 1
            except ValueError as e:
                print(f"[library] {e}")
        if deleted:
            print(f"[library] Pruned {deleted} orphaned download(s)")
        return deleted

    # ─────────────────────────────────────────
    #  WEB REMOTE CHANNEL CALLBACKS
    # ─────────────────────────────────────────

    def _web_get_channel_info(self, ch_idx: int) -> dict | None:
        """Return a dict describing ch_idx's current video and upcoming queue.
        Called from the web-handler thread; reads state without blocking tkinter."""
        if ch_idx < 0 or ch_idx >= N:
            return None
        ch    = CHANNELS[ch_idx]
        state = self.states[ch_idx]

        result: dict = {
            "name":      ch.get("name", f"Channel {ch_idx + 1}"),
            "emoji":     ch.get("emoji", "\U0001f4fa"),
            "color":     ch.get("color", "#00ff88"),
            "is_active": ch_idx == self.current_channel,
            "current":   None,
            "queue":     [],
            "feed_size": len(state.active_positions()),
            "editable":  True,
        }

        if not state.videos or not state._initialized:
            return result

        # ── Current video ────────────────────────────────────────────
        cur_pl = state._current_playlist_idx
        if cur_pl in state._resolved_map:
            vid_idx = state._resolved_map[cur_pl]
            vid     = state.videos[min(vid_idx, len(state.videos) - 1)]

            if ch_idx == self.current_channel and self.state == AppState.PLAYING:
                vlc_ms  = self.player.get_time()
                vlc_len = self.player.get_length()
                progress = min(1.0, vlc_ms / vlc_len) if vlc_len > 0 and vlc_ms >= 0 else 0.0
            else:
                offset   = max(0.0, self.elapsed() - state._video_start_elapsed)
                duration = max(1.0, vid.get("duration") or 1.0)
                progress = min(1.0, offset / duration)

            result["current"] = dict(
                self._web_entry(state, cur_pl),
                channel=vid.get("channel") or "",
                progress=progress,
            )

        # ── Upcoming queue (next 10 positions) ───────────────────────
        for pl_idx in list(state._play_queue)[:10]:
            if not state.is_playable(pl_idx):
                continue
            result["queue"].append(self._web_entry(state, pl_idx))

        return result

    def _web_entry(self, state: ChannelState, pl_idx: int) -> dict:
        """Describe one feed position for the web remote."""
        url = state.url_at(pl_idx)
        if pl_idx in state._resolved_map:
            title = state.videos[state._resolved_map[pl_idx]].get("title") or ""
        else:
            title = get_cached_title(url) if url else ""
        if not title:
            title = f"Video {pl_idx + 1}"

        downloaded = False
        downloading = False
        try:
            video_id = get_video_id(url)
            downloaded = bool(find_local_file(video_id))
            downloading = downloads.manager.is_pending(video_id)
        except ValueError:
            pass

        return {
            "pos":         pl_idx,
            "title":       title,
            "url":         url,
            "injected":    state.is_injected(pl_idx),
            "downloaded":  downloaded,
            "downloading": downloading,
        }

    def _web_skip_video(self, ch_idx: int) -> None:
        """Advance ch_idx to its next video. State mutation is immediate (GIL-safe);
        VLC restart (if this is the active channel) is scheduled on the tkinter thread."""
        if ch_idx < 0 or ch_idx >= N:
            return
        self.states[ch_idx].advance_video(self.elapsed())
        if ch_idx == self.current_channel and self.state == AppState.PLAYING:
            self.root.after(0, lambda: self._start_channel(ch_idx))

    def _web_queue_video(self, ch_idx: int, url: str) -> bool:
        """Play a video next, once.

        An injected video is deliberately *not* a playlist member: it is never
        written back to YouTube, never downloaded, and never reshuffled into a
        later cycle.  Called from the web-handler thread.
        """
        if ch_idx < 0 or ch_idx >= N:
            return False
        state = self.states[ch_idx]
        pos = state.add_url(url, injected=True)
        state.insert_next(pos)
        self._resolve_soon(ch_idx, pos)
        return True

    def _web_add_to_playlist(self, ch_idx: int, url: str) -> bool:
        """Add a video to the channel for good.

        It is recorded in the channel catalog (un-ignoring it if need be), plays
        next, and is downloaded in the background.
        """
        if ch_idx < 0 or ch_idx >= N:
            return False
        try:
            video_id = get_video_id(url)
        except ValueError:
            return False
        if not library.add_manual(ch_idx, video_id):
            return False
        self._play_member_next(ch_idx, url)
        return True

    def _play_member_next(self, ch_idx: int, url: str) -> None:
        """Make a catalog video live: play it next and download it."""
        if self._reload_if_empty(ch_idx):
            return
        state = self.states[ch_idx]
        pos = state.add_url(url, user_added=True)
        state.insert_next(pos)
        downloads.manager.enqueue_many(self._video_ids_at(state, [pos]), front=True)
        self._resolve_soon(ch_idx, pos)

    def _web_remove_video(self, ch_idx: int, pos: int, permanent: bool) -> bool:
        """Drop a video from the feed.

        permanent=True moves it to the channel's ignored list and deletes the
        local file, so a source sync won't bring it back.  A one-shot injection
        has no catalog entry; it is simply dropped.
        """
        if ch_idx < 0 or ch_idx >= N:
            return False
        state = self.states[ch_idx]
        if pos < 0 or pos >= len(state._playlist_urls):
            return False

        url = state.url_at(pos)
        if not self._drop_position(ch_idx, pos, permanent):
            return False
        if permanent:
            try:
                library.ignore(ch_idx, get_video_id(url))
            except ValueError:
                pass
            self._discard_local_copy(url)
        return True

    def _drop_position(self, ch_idx: int, pos: int, permanent: bool) -> bool:
        state = self.states[ch_idx]
        was_current = pos == state._current_playlist_idx
        if not state.remove_position(pos, permanent=permanent):
            return False
        if was_current:
            # Move on before touching the file — VLC still has it open.
            self._web_skip_video(ch_idx)
        return True

    def _web_ignore_video(self, ch_idx: int, url: str) -> bool:
        """Move a catalog video to the ignored list (from the Videos page)."""
        if ch_idx < 0 or ch_idx >= N:
            return False
        try:
            video_id = get_video_id(url)
        except ValueError:
            return False
        if not library.ignore(ch_idx, video_id):
            return False
        pos = self.states[ch_idx].position_of(url)
        if pos is not None:
            self._drop_position(ch_idx, pos, permanent=True)
        self._discard_local_copy(url)
        return True

    def _web_restore_video(self, ch_idx: int, url: str) -> bool:
        """Bring an ignored video back onto the channel."""
        if ch_idx < 0 or ch_idx >= N:
            return False
        try:
            video_id = get_video_id(url)
        except ValueError:
            return False
        if not library.restore(ch_idx, video_id):
            return False
        self._play_member_next(ch_idx, url)
        return True

    def _web_add_source(self, ch_idx: int, playlist_id: str) -> bool:
        """Attach a YouTube playlist to a channel and pull its videos in.

        The playlist title and the first sync are fetched in the background so
        the web request returns straight away.
        """
        if ch_idx < 0 or ch_idx >= N:
            return False
        if not library.add_source(ch_idx, playlist_id):
            return False
        threading.Thread(
            target=self._sync_new_source, args=(ch_idx, playlist_id),
            name="source-sync", daemon=True,
        ).start()
        return True

    def _sync_new_source(self, ch_idx: int, playlist_id: str) -> None:
        title = get_playlist_title(playlist_url(playlist_id))
        if title:
            library.set_source_title(ch_idx, playlist_id, title)
        # Wait out any full sync rather than racing it.
        with self._refresh_lock:
            self._refresh_running = True
            try:
                added, _ok = self._refresh_channel(ch_idx)
                self._enqueue_backlog(ch_idx)
                self._last_refresh = time.time()
                self._last_refresh_summary = f"+{added} new from {title or playlist_id}"
            finally:
                self._refresh_running = False

    def _web_remove_source(self, ch_idx: int, playlist_id: str) -> bool:
        """Stop syncing a playlist.  Its videos stay on the channel."""
        if ch_idx < 0 or ch_idx >= N:
            return False
        return library.remove_source(ch_idx, playlist_id)

    def _web_save_channel(self, ch_idx: int, name: str, emoji: str, color: str) -> bool:
        """Rename / recolour a channel.  The buttons pick it up on their next redraw."""
        if ch_idx < 0 or ch_idx >= N:
            return False
        saved = library.update_channel(ch_idx, name, emoji, color)
        if not saved:
            return False
        CHANNELS[ch_idx].update(saved)
        self.root.after(0, lambda: self._apply_channel_style(ch_idx))
        return True

    def _apply_channel_style(self, ch_idx: int) -> None:
        cv = self.channel_buttons[ch_idx]
        cv._ch_color = CHANNELS[ch_idx]["color"]
        cv._ch_dark = self._darken(cv._ch_color)
        self._draw_channel_button(ch_idx, cv)

    def _web_merge_channel(self, src_idx: int, dst_idx: int) -> bool:
        """Move every video, ignored video and source from src into dst.

        src is left empty (it shows the robots until it gets videos again), so
        the slot can be renamed and given new sources.
        """
        if not (0 <= src_idx < N and 0 <= dst_idx < N) or src_idx == dst_idx:
            return False
        moved = library.merge(src_idx, dst_idx)
        if moved is None:
            return False

        # Merged videos join the shuffle at random spots rather than all
        # playing next.
        dst = self.states[dst_idx]
        if not self._reload_if_empty(dst_idx):
            for pos in dst.add_members([_watch_url(v) for v in moved]):
                dst._play_queue.insert(random.randint(0, len(dst._play_queue)), pos)

        self.states[src_idx] = ChannelState()
        if src_idx == self.current_channel and self.state in (
                AppState.PLAYING, AppState.LOADING):
            self.root.after(0, lambda: self._start_channel(src_idx))
        return True

    def _web_tv_state(self) -> dict:
        """What the TV is doing, for the remote's Now page."""
        return {
            "state":   self.state.name.lower(),   # booting/loading/playing/schedule_off/power_off
            "channel": self.current_channel,
        }

    def _web_catalog(self, ch_idx: int) -> dict | None:
        """Full video / ignored / source lists for the remote's library pages."""
        ch = library.get_channel(ch_idx) if 0 <= ch_idx < N else None
        if ch is None:
            return None
        titles = {s["playlist_id"]: s["title"] for s in ch["sources"]}

        def describe(entry: dict) -> dict:
            url = _watch_url(entry["id"])
            origin = entry["origin"]
            return {
                "id":          entry["id"],
                "title":       get_cached_title(url) or entry["id"],
                "from":        ("added by hand" if origin == library.MANUAL
                                else titles.get(origin, "a removed playlist")),
                "downloaded":  bool(find_local_file(entry["id"])),
                "downloading": downloads.manager.is_pending(entry["id"]),
            }

        return {
            "videos":  [describe(v) for v in ch["videos"]],
            "ignored": [describe(v) for v in ch["ignored"]],
            "sources": [dict(s) for s in ch["sources"]],
        }

    def _web_refresh_playlist(self, ch_idx: int) -> bool:
        """Sync every channel's sources now.  False if a sync is already running."""
        return self._start_playlist_refresh()

    def _web_library_status(self) -> dict:
        """Download-queue and refresh state for the remote's status strip."""
        status = downloads.manager.status()
        status["refreshing"] = self._refresh_running
        status["last_refresh"] = (
            datetime.fromtimestamp(self._last_refresh).strftime("%H:%M")
            if self._last_refresh else ""
        )
        status["summary"] = self._last_refresh_summary
        return status

    def _resolve_soon(self, ch_idx: int, pos: int) -> None:
        """Start resolving a position's stream URL if nothing else is resolving."""
        state = self.states[ch_idx]
        if pos in state._resolved_map or state.resolving:
            return
        state.resolving = True
        threading.Thread(
            target=self._resolve_for_playback,
            args=(ch_idx, pos, state.url_at(pos)),
            daemon=True,
        ).start()

    def _prefetch_for_inactive(self, index: int, ch_state) -> None:
        """Kick off background resolution for an inactive channel's current position.
        Called right after advance_video so the new video is ready when the user
        switches to the channel."""
        if ch_state.resolving:
            return
        pl_idx = ch_state._current_playlist_idx
        if pl_idx in ch_state._resolved_map or pl_idx in ch_state._failed_positions:
            return
        if pl_idx >= len(ch_state._playlist_urls):
            return
        yt_url = ch_state._playlist_urls[pl_idx]
        ch_state.resolving = True
        threading.Thread(
            target=self._resolve_for_playback,
            args=(index, pl_idx, yt_url),
            daemon=True,
        ).start()

    def _prefetch_next(self, index: int):
        """Resolve the next video's stream URL while the current video is still playing."""
        state = self.states[index]
        if state.resolving:
            return
        next_pl_idx = state.next_playlist_idx()
        if next_pl_idx is None:
            return
        # Already resolved and still fresh — nothing to do.
        if next_pl_idx in state._failed_positions:
            return  # known-bad position; _play_video_for_channel will skip it when reached
        if next_pl_idx in state._resolved_map:
            resolved_at = state.videos[state._resolved_map[next_pl_idx]].get("_resolved_at", 0)
            if (time.time() - resolved_at) < self.URL_TTL:
                return
        if next_pl_idx >= len(state._playlist_urls):
            return
        yt_url = state._playlist_urls[next_pl_idx]
        state.resolving = True
        threading.Thread(
            target=self._resolve_for_playback,
            args=(index, next_pl_idx, yt_url),
            daemon=True,
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
        self.ch_badge.config(text=f" {ch['emoji']} ", bg=ch.get("color", "#00ff88"))
        self.channel_info_label.config(
            text=f"CH {index + 1}  ·  {ch['name'].upper()}"
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
            return
        # If all startup attempts failed (e.g. first 5 videos geo-blocked),
        # kick off inline resolution for the next queued position so we don't spin forever.
        if not state.resolving and state._play_queue:
            next_pl_idx = state._play_queue[0]
            if (next_pl_idx not in state._resolved_map
                    and next_pl_idx not in state._failed_positions
                    and next_pl_idx < len(state._playlist_urls)):
                state.resolving = True
                yt_url = state._playlist_urls[next_pl_idx]
                threading.Thread(
                    target=self._resolve_for_playback,
                    args=(index, next_pl_idx, yt_url),
                    daemon=True,
                ).start()
        self.root.after(500, lambda: self._wait_for_channel_ready(index))

    def _play_video_for_channel(self, index: int):
        """Start VLC playback for the current video. Resolves stream URL inline if needed."""
        if self.current_channel != index or self.state != AppState.LOADING:
            return

        state = self.states[index]
        if not state._initialized:
            state.advance_video(self.elapsed())

        pl_idx = state._current_playlist_idx

        # The feed was emptied under us (every video deleted from the web
        # remote).  Sit on the robots until something is added back.
        if pl_idx < 0 or not state.is_playable(pl_idx):
            self._show_robots("fixing")
            self.root.after(2000, lambda i=index: self._play_video_for_channel(i))
            return

        # Permanently unavailable — advance immediately without retrying.
        if pl_idx in state._failed_positions:
            state.advance_video(self.elapsed())
            self.root.after(0, lambda i=index: self._play_video_for_channel(i))
            return

        # Stream URL not resolved yet — start resolution and poll until ready.
        if pl_idx not in state._resolved_map:
            if not state.resolving and 0 <= pl_idx < len(state._playlist_urls):
                state.resolving = True
                yt_url = state._playlist_urls[pl_idx]
                threading.Thread(
                    target=self._resolve_for_playback,
                    args=(index, pl_idx, yt_url),
                    daemon=True,
                ).start()
            self.root.after(500, lambda i=index: self._play_video_for_channel(i))
            return

        video_idx = state._resolved_map[pl_idx]
        state._current_idx = video_idx
        video = state.videos[video_idx]
        duration = video.get("duration") or 0
        offset_secs = max(0.0, self.elapsed() - state._video_start_elapsed)

        # If the virtual clock says this video has already ended (e.g. the
        # inactive-channel clock used the 900s estimate and overshot), treat it
        # as done and advance rather than seeking past the end and confusing VLC.
        if duration and offset_secs >= duration:
            state.advance_video(self.elapsed())
            self.root.after(0, lambda i=index: self._play_video_for_channel(i))
            return

        seek_ms = int(offset_secs * 1000)

        stream_url = video["stream_url"]
        resolved_at = video.get("_resolved_at", 0)
        is_stale = (time.time() - resolved_at) > self.URL_TTL
        retry_count = video.get("_play_retries", 0)
        max_retries = 3

        title = video.get("title") or video["yt_url"]
        stale_tag = " [stale]" if is_stale else ""
        retry_tag = f" retry {retry_count}" if retry_count else ""
        print(f"[CH{index + 1}] ▶ \"{title}\" +{offset_secs:.0f}s{stale_tag}{retry_tag}")

        if retry_count >= max_retries:
            print(f"[CH{index + 1}] skip \"{title}\" (failed {retry_count}x)")
            video["_failed"] = True
            state.advance_video(self.elapsed())
            self.root.after(0, lambda i=index: self._play_video_for_channel(i))
            return

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
            ch_state = self.states[channel_index]
            video_idx, _ = ch_state.get_position(self.elapsed())
            video_idx = min(video_idx, len(ch_state.videos) - 1)
            if video_idx < len(ch_state.videos):
                ch_state.videos[video_idx]["_play_retries"] = ch_state.videos[video_idx].get("_play_retries", 0) + 1
                ch_state.videos[video_idx]["_resolved_at"] = 0
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
            ch_state = self.states[channel_index]
            video_idx, _ = ch_state.get_position(self.elapsed())
            video_idx = min(video_idx, len(ch_state.videos) - 1)
            if video_idx < len(ch_state.videos):
                ch_state.videos[video_idx]["_play_retries"] = ch_state.videos[video_idx].get("_play_retries", 0) + 1
                ch_state.videos[video_idx]["_resolved_at"] = 0
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
        self.player.audio_set_volume(100)  # undo any fade from the previous video
        self.player.audio_set_mute(False)
        self._show_video()
        self.clock.resume()

        # Re-anchor the virtual clock to VLC's actual playback position.
        # Between advance_video() and here, VLC buffered for a few seconds;
        # that buffering time was counted by elapsed() but not by VLC.
        # Without this correction the virtual offset drifts ahead and can
        # cause inactive channels to advance their video prematurely.
        vlc_ms = self.player.get_time()
        if vlc_ms >= 0:
            ch_state = self.states[self.current_channel]
            ch_state._video_start_elapsed = self.elapsed() - vlc_ms / 1000.0

        self.state = AppState.PLAYING

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
                # Keep the virtual clock anchored to VLC's actual position so that
                # inactive-channel advancement uses an accurate baseline.  Without
                # this, network stalls / seek latency accumulate as drift and can
                # cause the virtual offset to overshoot the video duration, advancing
                # inactive channels to the next video prematurely.
                if vlc_state == vlc.State.Playing:
                    vlc_ms = self.player.get_time()
                    if vlc_ms >= 0:
                        ch_state = self.states[self.current_channel]
                        ch_state._video_start_elapsed = self.elapsed() - vlc_ms / 1000.0

                # Pre-fetch next video's stream URL when close to end of current.
                index = self.current_channel
                ch_state = self.states[index]
                if ch_state._initialized and ch_state.videos:
                    video_idx, offset = ch_state.get_position(self.elapsed())
                    video_idx = min(video_idx, len(ch_state.videos) - 1)
                    remaining = ch_state.videos[video_idx]["duration"] - offset
                    if 0 < remaining <= self.PREFETCH_WINDOW:
                        self._prefetch_next(index)

                # Fade volume to zero in the last _FADE_MS milliseconds.
                # VLC's input-slave EOS handling cuts the M4A audio abruptly
                # when the MP4 video master exhausts; fading preempts the hard cut.
                if vlc_state == vlc.State.Playing and not self.player.audio_get_mute():
                    length_ms = self.player.get_length()
                    pos_ms = self.player.get_time()
                    if length_ms > 0 and pos_ms > 0:
                        remaining_ms = length_ms - pos_ms
                        if 0 < remaining_ms <= self._FADE_MS:
                            vol = max(0, int(100 * remaining_ms / self._FADE_MS))
                            self.player.audio_set_volume(vol)
                        elif self.player.audio_get_volume() < 100:
                            self.player.audio_set_volume(100)

        # Advance inactive channels whose current video has ended by elapsed time.
        elapsed = self.elapsed()
        for i, ch_state in enumerate(self.states):
            if i == self.current_channel or not ch_state._playlist_urls:
                continue
            if not ch_state._initialized:
                if ch_state.ready:
                    ch_state.advance_video(elapsed)
                    self._prefetch_for_inactive(i, ch_state)
                continue
            cur_pl_idx = ch_state._current_playlist_idx
            if cur_pl_idx in ch_state._failed_positions:
                # Already known unplayable — move on now instead of burning the
                # full estimate on a video that will never resolve.
                ch_state.advance_video(elapsed)
                self._prefetch_for_inactive(i, ch_state)
                continue
            if cur_pl_idx not in ch_state._resolved_map:
                # Keep asking.  The attempt made right after advance_video is
                # often skipped because another resolve for this channel was
                # still in flight, and nothing else would ever retry.
                self._prefetch_for_inactive(i, ch_state)
                # Duration unknown for this position; use a conservative estimate so
                # _video_start_elapsed doesn't go stale and cause a huge seek offset
                # if the user switches to this channel later.
                if elapsed - ch_state._video_start_elapsed >= self.UNRESOLVED_VIDEO_ESTIMATE:
                    ch_state.advance_video(elapsed)
                    self._prefetch_for_inactive(i, ch_state)
                continue
            video_idx = min(ch_state._resolved_map[cur_pl_idx], len(ch_state.videos) - 1)
            if elapsed - ch_state._video_start_elapsed >= ch_state.videos[video_idx]["duration"]:
                ch_state.advance_video(elapsed)
                self._prefetch_for_inactive(i, ch_state)

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
        _BAR_BG = "#100828"
        self.channel_bar = tk.Frame(self.main_frame, bg=_BAR_BG, height=72)
        self.channel_bar.place(relx=0, rely=0, relwidth=1)
        # Neon accent line at the bottom of the bar
        tk.Frame(self.channel_bar, bg="#00ff88", height=2).place(
            relx=0, rely=1.0, relwidth=1, y=-2
        )

        # Emoji badge (solid colour block, updated per channel)
        self.ch_badge = tk.Label(
            self.channel_bar, text="",
            font=("Courier New", 15, "bold"), fg="#0d0721", bg="#00ff88",
            padx=8, pady=3,
        )
        self.ch_badge.pack(side="left", padx=(18, 0), pady=16)

        self.channel_info_label = tk.Label(
            self.channel_bar, text="",
            font=("Courier New", 18, "bold"), fg="#00ff88", bg=_BAR_BG, pady=8,
        )
        self.channel_info_label.pack(side="left", padx=(10, 4))

        # Pulsing live indicator
        self.live_label = tk.Label(
            self.channel_bar, text="",
            font=("Courier New", 10, "bold"), fg="#FF6B6B", bg=_BAR_BG, pady=8,
        )
        self.live_label.pack(side="left", padx=2)

        self.clock_label = tk.Label(
            self.channel_bar, text="",
            font=("Courier New", 14, "bold"), fg="#444466", bg=_BAR_BG, pady=8,
        )
        self.clock_label.pack(side="right", padx=18)

        # Power button
        self.power_btn = tk.Label(
            self.channel_bar, text="\u23fb", cursor="hand2",
            font=("Courier New", 22, "bold"), fg="#00ff88", bg=_BAR_BG, pady=4, padx=14,
        )
        self.power_btn.pack(side="right")
        self.power_btn.bind("<Button-1>", lambda e: self._toggle_power())
        self.power_btn.bind("<Enter>", lambda e: self.power_btn.configure(fg="#ff6b6b"))
        self.power_btn.bind("<Leave>", lambda e: self.power_btn.configure(
            fg="#333344" if self.state == AppState.POWER_OFF else "#00ff88"
        ))

        # Bottom panel
        self.bottom_panel = tk.Frame(self.main_frame, bg="#080416")
        self.bottom_panel.place(relx=0, rely=BUTTON_RELY, relwidth=1, relheight=BUTTON_RELH)
        # Neon accent line at the top of the panel
        tk.Frame(self.bottom_panel, bg="#1a0a4e", height=2).pack(side="top", fill="x")

        # Header
        self.panel_header = tk.Label(
            self.bottom_panel, text="\U0001f4fa  MY TV",
            font=("Courier New", 9, "bold"), fg="#2a1a5e", bg="#080416",
        )
        self.panel_header.pack(side="top", pady=(3, 0))

        self.buttons_frame = tk.Frame(self.bottom_panel, bg="#080416")
        self.buttons_frame.pack(expand=True, fill="both", padx=10, pady=(2, 6))

        self.channel_buttons = [
            self._make_channel_button(i, ch)
            for i, ch in enumerate(CHANNELS)
        ]

        # Video-backed scenes.  Built after the bottom panel so the info banner
        # can live inside it: during an off period the buttons are hidden and
        # the banner takes the panel, leaving the clip itself unobscured.  The
        # video frame is a sibling on main_frame because VLC covers any child
        # of its own frame.
        try:
            self.scene_video = SceneVideoPlayer(
                self.main_frame, vlc, text_parent=self.bottom_panel)
            # Built last, so without this it would stack above the channel bar
            # and the bottom panel and hide both.  Lower it to the back; the
            # banner is a child of the panel and so is unaffected.
            self.scene_video.frame.lower()
            have = available_scenes()
            print(f"[scene] Clips found for: {', '.join(sorted(have)) or 'none'}")
        except Exception as exc:  # noqa: BLE001 - never block UI construction
            print(f"[scene] Video scenes unavailable: {exc}")
            self.scene_video = None

        self._draw_scanlines()
        self._build_qr_overlay()

    def _make_channel_button(self, index: int, channel: dict) -> tk.Canvas:
        color = channel.get("color", "#555555")
        dark = self._darken(color)

        outer = tk.Frame(self.buttons_frame, bg="#03010d", padx=3, pady=3)
        outer.pack(side="left", expand=True, fill="both")

        cv = tk.Canvas(outer, bg=dark, highlightthickness=0, cursor="hand2")
        cv.pack(expand=True, fill="both")
        cv.bind("<Button-1>", lambda e, i=index: self.switch_channel(i))

        cv._ch_index = index
        cv._ch_color = color
        cv._ch_dark = dark
        cv._thumb = None
        return cv

    def _draw_scanlines(self):
        self.root.update_idletasks()
        w = self.video_frame.winfo_width() or self.root.winfo_screenwidth()
        h = self.video_frame.winfo_height() or int(self.root.winfo_screenheight() * 0.76)
        # Subtle interlaced scanline pattern — every 3 rows, very dark
        for y in range(0, h, 3):
            self.static_canvas.create_line(0, y, w, y, fill="#000022", width=1)

    def _build_qr_overlay(self):
        try:
            import qrcode
            from PIL import ImageTk as _ImageTk, Image as _Image
        except ImportError:
            return

        screen_h = self.root.winfo_screenheight()
        qr_size = max(90, min(150, int(screen_h * 0.76) // 6))
        label_h = 18
        pad = 8
        canvas_w = qr_size + pad * 2
        canvas_h = qr_size + pad * 2 + label_h + 6

        # Canvas is a sibling of video_frame on main_frame, so it renders above VLC
        self.qr_overlay = tk.Canvas(
            self.main_frame,
            bg="#080416",
            highlightthickness=2,
            highlightbackground="#00ff88",
            width=canvas_w,
            height=canvas_h,
        )
        self.qr_overlay.place(relx=1.0, rely=0.76, anchor="se", x=-16, y=-16)

        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=2,
        )
        qr.add_data(get_remote_url())
        qr.make(fit=True)
        img = qr.make_image(fill_color="#00ff88", back_color="#080416")
        img = img.resize((qr_size, qr_size), _Image.LANCZOS)
        self._qr_photo = _ImageTk.PhotoImage(img)

        cx = canvas_w // 2
        # Corner marks (CRT-style)
        cm = 5
        for ox, oy in [(0, 0), (canvas_w, 0), (0, canvas_h), (canvas_w, canvas_h)]:
            sx = 1 if ox == 0 else -1
            sy = 1 if oy == 0 else -1
            self.qr_overlay.create_line(ox, oy, ox + sx*cm, oy, fill="#00ff88", width=1)
            self.qr_overlay.create_line(ox, oy, ox, oy + sy*cm, fill="#00ff88", width=1)

        self.qr_overlay.create_image(cx, pad + qr_size // 2, image=self._qr_photo, anchor="center")

        sep_y = pad + qr_size + 4
        self.qr_overlay.create_line(pad, sep_y, canvas_w - pad, sep_y, fill="#1a0a4e", width=1)

        self.qr_overlay.create_text(
            cx, sep_y + 3 + label_h // 2,
            text="SCAN · REMOTE",
            font=("Courier New", 8, "bold"),
            fill="#00ff88",
            anchor="center",
        )


    # ─────────────────────────────────────────
    #  UI UPDATES
    # ─────────────────────────────────────────

    def _highlight_active_button(self, _: int):
        # Active border is drawn entirely inside _draw_channel_button — no widget-level
        # highlightthickness changes, which would cause layout shifts and flicker.
        pass

    def _update_buttons(self):
        # Skipped while off: the buttons are unmapped during a scheduled off
        # period and hidden when powered down, so redrawing them is pure waste
        # that competes with scene-video decoding for the UI thread.
        if self.state not in (AppState.POWER_OFF, AppState.SCHEDULE_OFF):
            for i, cv in enumerate(self.channel_buttons):
                self._draw_channel_button(i, cv)
        self.root.after(500, self._update_buttons)

    def _update_clock(self):
        self.clock_label.config(text=datetime.now().strftime("%H:%M") + "  ")
        # Pulse LIVE indicator every 30s (driven by _update_buttons at 500ms for blink)
        if self.state == AppState.PLAYING:
            self.live_label.config(text="● LIVE")
        else:
            self.live_label.config(text="")
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
        color  = cv._ch_color
        dark   = cv._ch_dark
        ch_state = self.states[index]
        ch = CHANNELS[index]
        bar_h  = 10   # thick progress strip at the very bottom
        top_h  = 24   # reserved header row height
        pad    = 8

        # ── Background ────────────────────────────────────────────────────
        cv.create_rectangle(0, 0, w, h, fill=dark, outline="")

        # ── Border: multi-layer glow for active, ghost line for inactive ──
        if is_active:
            cv.create_rectangle(0, 0, w, h,
                                fill="", outline=self._darken(color), width=5)
            cv.create_rectangle(2, 2, w - 2, h - 2,
                                fill="", outline=color, width=2)
            cv.create_rectangle(4, 4, w - 4, h - 4,
                                fill="", outline=self._lighten(color), width=1)
        else:
            cv.create_rectangle(0, 0, w, h,
                                fill="", outline="#16103a", width=1)

        # ── CRT corner marks ──────────────────────────────────────────────
        cm  = max(12, min(22, w // 8))
        col_cm = color if is_active else "#252548"
        lw  = 2 if is_active else 1
        for ox, oy in [(0, 0), (w, 0), (0, h), (w, h)]:
            sx = 1 if ox == 0 else -1
            sy = 1 if oy == 0 else -1
            cv.create_line(ox, oy, ox + sx * cm, oy, fill=col_cm, width=lw)
            cv.create_line(ox, oy, ox, oy + sy * cm, fill=col_cm, width=lw)

        # ── Channel number badge (top-left) ───────────────────────────────
        bw, bh = 26, 18
        bx, by = pad - 2, pad - 2
        badge_bg = color if is_active else "#0e0a28"
        badge_fg = "#050314" if is_active else "#2e2b55"
        cv.create_rectangle(bx, by, bx + bw, by + bh,
                            fill=badge_bg, outline="")
        cv.create_text(bx + bw // 2, by + bh // 2,
                       text=str(index + 1),
                       fill=badge_fg,
                       font=("Courier New", max(9, bh - 4), "bold"),
                       anchor="center")

        # ── Loading state ─────────────────────────────────────────────────
        if not ch_state.ready or not ch_state.videos:
            font_sz = max(28, min(h * 2 // 5, w // 3))
            cv.create_text(w // 2, top_h + (h - bar_h - top_h) // 2,
                           text=ch["emoji"],
                           font=("", font_sz), anchor="center",
                           fill="#252548")
            cv.create_rectangle(0, h - bar_h, w, h, fill="#04020e", outline="")
            return

        # ── Compute video position ─────────────────────────────────────────
        video_idx, offset = ch_state.get_position(self.elapsed())
        video_idx  = min(video_idx, len(ch_state.videos) - 1)
        video      = ch_state.videos[video_idx]

        # video is a *placeholder* while the current position is still resolving —
        # get_position falls back to the previously resolved entry so the avatar
        # doesn't blank out.  Scaling the bar by that video's duration is what made
        # inactive channels race to 100% and then sit there, so ask for the current
        # video explicitly and fall back to the same estimate _poll_vlc advances on.
        current_video = ch_state.current_video()
        duration = (current_video["duration"] or 1) if current_video             else self.UNRESOLVED_VIDEO_ESTIMATE

        # For the active channel use VLC's real playback position so the
        # progress bar reflects what's actually on screen.  For inactive
        # channels the virtual clock is accurate enough.
        if is_active and self.state == AppState.PLAYING:
            vlc_pos_ms  = self.player.get_time()
            vlc_len_ms  = self.player.get_length()
            if vlc_pos_ms >= 0 and vlc_len_ms > 0:
                progress = min(1.0, vlc_pos_ms / vlc_len_ms)
            else:
                progress = min(1.0, offset / duration)
        else:
            progress   = min(1.0, offset / duration)
        next_pl_idx = ch_state.next_playlist_idx()
        if next_pl_idx is not None and next_pl_idx in ch_state._resolved_map:
            next_vid_idx = min(ch_state._resolved_map[next_pl_idx], len(ch_state.videos) - 1)
            next_video = ch_state.videos[next_vid_idx]
        else:
            next_video = {}  # not yet resolved — button will show emoji placeholder

        # ── Main avatar (large, centred in the content zone) ─────────────
        name_h   = max(16, h // 9)    # row for the channel name label
        content_t = top_h
        content_b = h - bar_h - name_h - 4
        content_cy = (content_t + content_b) // 2

        # Small next-up thumbnail lives in the top-right corner, so give the
        # main avatar a slight left offset only when there's room for it.
        small_av  = max(20, min(h // 6, w // 9, 36))
        next_zone  = small_av + pad * 2   # width reserved for the next-up
        av_max_w  = w - next_zone - pad * 2
        av_max_h  = content_b - content_t - 6
        av_size   = max(32, min(av_max_h, av_max_w, 110))
        av_cx     = (w - next_zone) // 2 + pad // 2

        cur_channel_id = video.get("channel_id", "")
        cur_avatar = self._get_avatar_image(cur_channel_id, cv, "_cur_av")

        # Drop shadow
        cv.create_rectangle(
            av_cx - av_size // 2 + 3, content_cy - av_size // 2 + 3,
            av_cx + av_size // 2 + 3, content_cy + av_size // 2 + 3,
            fill="#000000", outline="")
        # Frame border
        fp = 3
        cv.create_rectangle(
            av_cx - av_size // 2 - fp, content_cy - av_size // 2 - fp,
            av_cx + av_size // 2 + fp, content_cy + av_size // 2 + fp,
            fill="#000000",
            outline=color if is_active else "#1c1840",
            width=2 if is_active else 1)

        if cur_avatar:
            cv.create_image(av_cx, content_cy, image=cur_avatar, anchor="center")
        else:
            cv.create_rectangle(
                av_cx - av_size // 2, content_cy - av_size // 2,
                av_cx + av_size // 2, content_cy + av_size // 2,
                fill=dark, outline="")
            cv.create_text(av_cx, content_cy, text=ch["emoji"],
                           font=("", max(22, av_size // 2)), anchor="center")

        # ── Next-up thumbnail (top-right) ─────────────────────────────────
        next_ax = w - pad - small_av
        next_ay = top_h + 2

        next_channel_id = next_video.get("channel_id", "")
        next_avatar = self._get_avatar_image(next_channel_id, cv, "_next_av")

        cv.create_rectangle(next_ax - 1, next_ay - 1,
                            next_ax + small_av + 1, next_ay + small_av + 1,
                            fill="#000000", outline="#16103a", width=1)
        if next_avatar:
            cv.create_image(next_ax + small_av // 2, next_ay + small_av // 2,
                            image=next_avatar, anchor="center")
        else:
            cv.create_rectangle(next_ax, next_ay,
                                next_ax + small_av, next_ay + small_av,
                                fill=dark, outline="")
            cv.create_text(next_ax + small_av // 2, next_ay + small_av // 2,
                           text=ch["emoji"],
                           font=("", max(8, small_av // 2)),
                           anchor="center", fill="#252548")
        # Tiny "NEXT" label under the thumbnail
        cv.create_text(next_ax + small_av // 2, next_ay + small_av + 3,
                       text="▶▶", fill="#252548",
                       font=("Courier New", 6), anchor="n")

        # ── LIVE dot (top-right of badge row) ────────────────────────────
        if is_active and self.state == AppState.PLAYING:
            blink_on = int(time.time()) % 2 == 0
            dot_col  = "#FF6B6B" if blink_on else "#4a1515"
            dot_r    = 5
            dot_x    = bx + bw + 8
            dot_y    = by + bh // 2 - dot_r
            cv.create_oval(dot_x, dot_y,
                           dot_x + dot_r * 2, dot_y + dot_r * 2,
                           fill=dot_col, outline="")

        # ── Channel name label (above progress bar) ───────────────────────
        name_y = h - bar_h - name_h // 2 - 1
        cv.create_text(w // 2, name_y,
                       text=ch["name"].upper(),
                       fill=color if is_active else "#252548",
                       font=("Courier New", max(8, name_h - 5), "bold"),
                       anchor="center")

        # ── Progress bar (full-bleed at the very bottom) ──────────────────
        cv.create_rectangle(0, h - bar_h, w, h, fill="#04020e", outline="")
        fill_w = max(2, int(w * progress))
        cv.create_rectangle(0, h - bar_h, fill_w, h,
                            fill=color if is_active else self._darken(dark),
                            outline="")
        # Leading-edge highlight on active channel
        if is_active and fill_w > 5:
            cv.create_rectangle(fill_w - 3, h - bar_h, fill_w, h,
                                fill=self._lighten(color), outline="")

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
    # One-off: turn an old channels.json + playlist_overlay.json into the
    # app-owned catalog, seeding from the live playlists where possible.
    library.migrate_v1(fetch=library.fetch_source_ids)
    root = tk.Tk()
    app = ToddlerTV(root)
    root.mainloop()
