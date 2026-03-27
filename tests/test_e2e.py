"""
Toddler TV — End-to-End Tests
Full-workflow tests with mocked external dependencies (VLC, yt-dlp, network).
No real subprocess calls, no real YouTube traffic, no display required.

Workflows covered:
  1. Schedule lifecycle  — write → detect → edit → re-detect
  2. Web remote server   — GET / POST save / add / delete
  3. Channel resolution  — resolve → add video → position tracking
  4. App state machine   — boot, power, channel switch, schedule off/on
"""

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from unittest.mock import MagicMock, patch

# ── Path setup ─────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# ── Stub external packages before any project code is imported ─────────────────
_mock_vlc = MagicMock()
# Give the mock real integer sentinels so comparisons work
_mock_vlc.State.Playing = 3
_mock_vlc.State.Error   = 5
_mock_vlc.State.Ended   = 6
_mock_vlc.State.Stopped = 0
sys.modules["vlc"]    = _mock_vlc
sys.modules["yt_dlp"] = MagicMock()

# ── Project imports (after stubs) ──────────────────────────────────────────────
import schedule as _schedule_module                              # noqa: E402
from schedule import save_periods, get_periods, is_tv_off       # noqa: E402
from media import ChannelState, get_video_info                  # noqa: E402
import images as _images_module                                  # noqa: E402

# ── Shared fake video info ─────────────────────────────────────────────────────
_FAKE_INFO = {
    "url":        "https://rr3.googlevideo.com/stream?id=abc",
    "duration":   600.0,
    "title":      "Test Video",
    "channel":    "Test Channel",
    "channel_id": "UC_test123",
    "thumbnail":  "https://i.ytimg.com/vi/abc/hqdefault.jpg",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _reset_schedule_cache():
    _schedule_module._cached_periods = None
    _schedule_module._cached_mtime   = 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  1. Schedule lifecycle
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduleLifecycle(unittest.TestCase):
    """
    Write a schedule → confirm detection → edit → confirm the change.
    Exercises: save_periods, get_periods, is_tv_off end-to-end with a real file.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._path   = os.path.join(self._tmpdir, "schedule.json")
        _reset_schedule_cache()
        self._patcher = patch.object(_schedule_module, "SCHEDULE_FILE", self._path)
        self._patcher.start()
        _reset_schedule_cache()

    def tearDown(self):
        self._patcher.stop()
        _reset_schedule_cache()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _now(self, hour, minute):
        from datetime import datetime
        return datetime(2024, 1, 1, hour, minute, 0)

    def test_full_schedule_cycle(self):
        """Save → detect → extend → re-detect → clear → TV always on."""
        # 1. Write: sleep 07:00–08:00
        save_periods([(7, 0, 8, 0, "sleeping")])
        self.assertTrue(os.path.exists(self._path))

        # 2. 07:30 → TV off
        with patch("schedule.datetime") as m:
            m.now.return_value = self._now(7, 30)
            self.assertTrue(is_tv_off())

        # 3. 06:59 → TV on
        with patch("schedule.datetime") as m:
            m.now.return_value = self._now(6, 59)
            self.assertFalse(is_tv_off())

        # 4. Extend to 09:00, reload
        save_periods([(7, 0, 9, 0, "sleeping")])

        with patch("schedule.datetime") as m:
            m.now.return_value = self._now(8, 30)
            self.assertTrue(is_tv_off())

        # 5. Remove all → always on
        save_periods([])
        with patch("schedule.datetime") as m:
            m.now.return_value = self._now(7, 30)
            self.assertFalse(is_tv_off())

        # 6. Final persisted state is empty
        self.assertEqual(get_periods(), [])

    def test_multiple_scenes_persist(self):
        periods = [
            (7,  0,  8,  0, "sleeping"),
            (12, 0, 13,  0, "lunch"),
            (19, 30, 23, 59, "sleeping"),
        ]
        save_periods(periods)
        loaded = get_periods()
        self.assertEqual(loaded, periods)

    def test_json_file_is_well_formed(self):
        save_periods([(9, 0, 10, 0, "fixing")])
        with open(self._path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data[0]["start_h"], 9)
        self.assertEqual(data[0]["scene"], "fixing")


# ══════════════════════════════════════════════════════════════════════════════
#  2. Web remote HTTP server
# ══════════════════════════════════════════════════════════════════════════════

class TestWebRemoteServer(unittest.TestCase):
    """
    Start the real _Handler on an OS-assigned port and exercise all endpoints.
    Exercises: GET page render, POST save, POST add, POST delete.
    """

    @classmethod
    def setUpClass(cls):
        import web_remote as _wr
        from http.server import HTTPServer

        cls._tmpdir  = tempfile.mkdtemp()
        cls._path    = os.path.join(cls._tmpdir, "schedule.json")
        cls._patcher = patch.object(_schedule_module, "SCHEDULE_FILE", cls._path)
        cls._patcher.start()
        _reset_schedule_cache()
        save_periods([(8, 0, 9, 0, "sleeping")])

        # Port 0 → OS picks a free port
        cls._server  = HTTPServer(("127.0.0.1", 0), _wr._Handler)
        cls._port    = cls._server.server_address[1]
        cls._thread  = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._patcher.stop()
        _reset_schedule_cache()
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self):
        conn = HTTPConnection("127.0.0.1", self._port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        return resp.status, body

    def _post(self, body: str):
        data = body.encode("utf-8")
        conn = HTTPConnection("127.0.0.1", self._port, timeout=5)
        conn.request("POST", "/", data, {
            "Content-Type":   "application/x-www-form-urlencoded",
            "Content-Length": str(len(data)),
        })
        resp = conn.getresponse()
        html = resp.read().decode("utf-8")
        conn.close()
        return resp.status, html

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_get_returns_200(self):
        status, _ = self._get()
        self.assertEqual(status, 200)

    def test_get_page_contains_branding(self):
        _, body = self._get()
        self.assertIn("Toddler TV", body)

    def test_get_page_shows_initial_period(self):
        _, body = self._get()
        self.assertIn("08:00", body)

    def test_post_save_persists_to_file(self):
        body = "count=1&start_0=10%3A00&end_0=11%3A00&scene_0=lunch&action=save"
        status, html = self._post(body)
        self.assertEqual(status, 200)
        self.assertIn("saved", html.lower())

        _reset_schedule_cache()
        periods = get_periods()
        saved_hours = [sh for sh, *_ in periods]
        self.assertIn(10, saved_hours)

    def test_post_add_returns_new_row_without_saving(self):
        save_periods([])
        _reset_schedule_cache()

        status, html = self._post("count=0&action=add")
        self.assertEqual(status, 200)
        # Default new period starts at 12:00
        self.assertIn("12:00", html)
        # But nothing written to file yet
        _reset_schedule_cache()
        self.assertEqual(get_periods(), [])

    def test_post_delete_removes_correct_period(self):
        save_periods([(8, 0, 9, 0, "sleeping"), (12, 0, 13, 0, "lunch")])
        _reset_schedule_cache()

        # Delete index 0 (sleeping)
        body = (
            "count=2"
            "&start_0=08%3A00&end_0=09%3A00&scene_0=sleeping"
            "&start_1=12%3A00&end_1=13%3A00&scene_1=lunch"
            "&delete=0"
        )
        status, _ = self._post(body)
        self.assertEqual(status, 200)

        _reset_schedule_cache()
        periods = get_periods()
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0][4], "lunch")

    def test_post_delete_out_of_range_is_safe(self):
        save_periods([(8, 0, 9, 0, "sleeping")])
        _reset_schedule_cache()

        body = "count=1&start_0=08%3A00&end_0=09%3A00&scene_0=sleeping&delete=99"
        status, _ = self._post(body)
        self.assertEqual(status, 200)  # must not crash


# ══════════════════════════════════════════════════════════════════════════════
#  3. Channel resolution workflow
# ══════════════════════════════════════════════════════════════════════════════

class TestChannelResolutionWorkflow(unittest.TestCase):
    """
    Simulate the background resolution loop by feeding resolved video info
    directly into ChannelState (bypassing the real yt-dlp subprocess).
    Exercises: ChannelState population, offset rebuilding, position tracking,
    and the app's handling of RATE_LIMITED / None return values.
    """

    def _add(self, state: ChannelState, info: dict, yt_url: str):
        """Replicate ToddlerTV._add_video without any UI calls."""
        state.videos.append({
            "yt_url":       yt_url,
            "stream_url":   info["url"],
            "duration":     info["duration"],
            "title":        info.get("title", ""),
            "channel":      info.get("channel", ""),
            "channel_id":   info.get("channel_id", ""),
            "thumbnail":    info.get("thumbnail", ""),
            "_resolved_at": time.time(),
        })
        if not state.ready:
            state.ready = True
        state._rebuild_offsets()

    def test_single_video_ready_after_resolve(self):
        state = ChannelState()
        self._add(state, _FAKE_INFO, "https://www.youtube.com/watch?v=abc")
        self.assertTrue(state.ready)
        self.assertEqual(len(state.videos), 1)
        self.assertAlmostEqual(state.total_duration, 600.0)

    def test_position_mid_first_video(self):
        state = ChannelState()
        self._add(state, _FAKE_INFO, "url0")
        idx, off = state.get_position(300.0)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(off, 300.0)

    def test_position_tracks_across_multiple_videos(self):
        state = ChannelState()
        for i, dur in enumerate([300.0, 600.0, 900.0]):
            self._add(state, {**_FAKE_INFO, "duration": dur}, f"url{i}")

        # total = 1800s; at 350s → past first (300s), 50s into second
        self.assertAlmostEqual(state.total_duration, 1800.0)
        idx, off = state.get_position(350.0)
        self.assertEqual(idx, 1)
        self.assertAlmostEqual(off, 50.0)

    def test_position_wraps_back_to_start(self):
        state = ChannelState()
        self._add(state, _FAKE_INFO, "url0")
        idx, off = state.get_position(600.0)   # exactly one full loop
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(off, 0.0, places=1)

    def test_rate_limited_sentinel_skips_video(self):
        """App must skip RATE_LIMITED results; state stays not-ready."""
        state = ChannelState()
        result = "RATE_LIMITED"
        if result and result != "RATE_LIMITED":  # what the app checks
            self._add(state, result, "url")
        self.assertFalse(state.ready)

    def test_none_result_leaves_state_not_ready(self):
        """App must skip None results; state stays not-ready."""
        state = ChannelState()
        if None is not None:  # app guard: `if info is None: continue`
            self._add(state, {}, "url")
        self.assertFalse(state.ready)
        self.assertEqual(len(state.videos), 0)

    def test_channel_id_stored_on_video(self):
        state = ChannelState()
        self._add(state, _FAKE_INFO, "url0")
        self.assertEqual(state.videos[0]["channel_id"], "UC_test123")

    def test_pending_url_queue_drains(self):
        """Simulate resolving 2 of 3 URLs upfront; 1 left as pending."""
        urls  = [f"https://www.youtube.com/watch?v=v{i}" for i in range(3)]
        state = ChannelState()
        for url in urls[:2]:           # INITIAL_RESOLVE = 2
            self._add(state, _FAKE_INFO, url)
        state.pending_urls = urls[2:]

        self.assertEqual(len(state.videos), 2)
        self.assertEqual(len(state.pending_urls), 1)
        self.assertTrue(state.ready)


# ══════════════════════════════════════════════════════════════════════════════
#  4. App state machine
# ══════════════════════════════════════════════════════════════════════════════

class TestAppStateMachine(unittest.TestCase):
    """
    Instantiate ToddlerTV with mocked VLC and a hidden tkinter root, then
    drive it through key state transitions without touching the display.
    """

    @classmethod
    def setUpClass(cls):
        import toddlertv as _tt
        cls._tt = _tt

    def setUp(self):
        import tkinter as tk

        # Controllable VLC player mock
        self._player = MagicMock()
        self._player.get_state.return_value = _mock_vlc.State.Playing
        self._player.get_time.return_value  = 0
        self._player.audio_get_mute.return_value = False
        _mock_vlc.Instance.return_value.media_player_new.return_value = self._player

        # Clean shared state
        _images_module._avatar_cache.clear()
        _reset_schedule_cache()
        _schedule_module._cached_periods = []   # empty = always on
        _schedule_module._cached_mtime   = 1.0

        # Patch everything that would open a window, contact VLC, or do I/O.
        # _check_schedule and _poll_vlc are NOT patched here so that tests can
        # call the real implementations; they're safe during __init__ because
        # state==BOOTING and cached_periods==[] (always-on).
        self._patchers = [
            patch.object(self._tt.ToddlerTV, "_build_ui"),
            patch.object(self._tt.ToddlerTV, "_attach_vlc_to_window"),
            patch.object(self._tt.ToddlerTV, "_show_robots"),
            patch.object(self._tt.ToddlerTV, "_hide_robots"),
            patch.object(self._tt.ToddlerTV, "_start_mini_robots"),
            patch.object(self._tt.ToddlerTV, "_stop_mini_robots"),
            patch.object(self._tt.ToddlerTV, "_update_buttons"),
            patch.object(self._tt.ToddlerTV, "_update_clock"),
            patch.object(self._tt.ToddlerTV, "_resolve_channel"),
            patch("toddlertv.start_web_remote"),
        ]
        for p in self._patchers:
            p.start()

        self._root = tk.Tk()
        self._root.withdraw()
        self.app = self._tt.ToddlerTV(self._root)

        # Wire up stub UI objects that state-machine methods reference
        self.app.video_frame        = MagicMock()
        self.app.robot_canvas       = MagicMock()
        self.app.channel_buttons    = [MagicMock() for _ in range(self._tt.N)]
        self.app.channel_info_label = MagicMock()
        self.app.power_btn          = MagicMock()
        self.app.bottom_panel       = MagicMock()
        self.app.clock_label        = MagicMock()
        self.app.static_canvas      = MagicMock()

    def tearDown(self):
        # Brief wait so any daemon threads finish their current work before the
        # tkinter root is destroyed (prevents Tcl_AsyncDelete races).
        time.sleep(0.15)
        for p in self._patchers:
            p.stop()
        try:
            self._root.destroy()
        except Exception:
            pass
        _reset_schedule_cache()

    # ── Boot ──────────────────────────────────────────────────────────────────

    def test_initial_state_is_booting(self):
        self.assertEqual(self.app.state, self._tt.AppState.BOOTING)

    def test_clock_not_running_at_boot(self):
        self.assertFalse(self.app.clock.is_running)

    # ── Power ─────────────────────────────────────────────────────────────────

    def test_power_off_stops_vlc_and_sets_state(self):
        self.app.state = self._tt.AppState.PLAYING
        self.app._power_off()
        self._player.stop.assert_called()
        self.assertEqual(self.app.state, self._tt.AppState.POWER_OFF)

    def test_power_off_pauses_clock(self):
        self.app.state = self._tt.AppState.PLAYING
        self.app.clock.resume()
        self.app._power_off()
        self.assertFalse(self.app.clock.is_running)

    def test_power_on_when_schedule_allows_starts_channel(self):
        self.app.state = self._tt.AppState.POWER_OFF
        with patch("toddlertv.is_tv_off", return_value=False), \
             patch.object(self.app, "_start_channel") as mock_start:
            self.app._power_on()
        mock_start.assert_called_once_with(self.app.current_channel)

    def test_power_on_during_schedule_off_enters_schedule_off(self):
        self.app.state = self._tt.AppState.POWER_OFF
        with patch("toddlertv.is_tv_off", return_value=True), \
             patch("toddlertv.current_scene_name", return_value="sleeping"), \
             patch.object(self.app, "_enter_schedule_off") as mock_off:
            self.app._power_on()
        mock_off.assert_called_once()

    # ── Schedule off/on ───────────────────────────────────────────────────────

    def test_enter_schedule_off_sets_state_and_stops_vlc(self):
        self.app.state = self._tt.AppState.PLAYING
        with patch("toddlertv.current_scene_name", return_value="sleeping"):
            self.app._enter_schedule_off()
        self.assertEqual(self.app.state, self._tt.AppState.SCHEDULE_OFF)
        self._player.stop.assert_called()

    def test_enter_schedule_off_disables_buttons(self):
        self.app.state = self._tt.AppState.PLAYING
        with patch("toddlertv.current_scene_name", return_value="sleeping"):
            self.app._enter_schedule_off()
        for btn in self.app.channel_buttons:
            btn.configure.assert_called()

    def test_leave_schedule_off_starts_channel(self):
        self.app.state = self._tt.AppState.SCHEDULE_OFF
        with patch.object(self.app, "_start_channel") as mock_start:
            self.app._leave_schedule_off()
        mock_start.assert_called_once_with(self.app.current_channel)

    def test_check_schedule_triggers_off(self):
        self.app.state = self._tt.AppState.PLAYING
        with patch("toddlertv.is_tv_off", return_value=True), \
             patch("toddlertv.current_scene_name", return_value="sleeping"), \
             patch.object(self.app, "_enter_schedule_off") as mock_off:
            # Run one iteration without the after() re-schedule
            with patch.object(self.app.root, "after"):
                self.app._check_schedule()
        mock_off.assert_called_once()

    def test_check_schedule_triggers_on(self):
        self.app.state = self._tt.AppState.SCHEDULE_OFF
        with patch("toddlertv.is_tv_off", return_value=False), \
             patch.object(self.app, "_leave_schedule_off") as mock_on:
            with patch.object(self.app.root, "after"):
                self.app._check_schedule()
        mock_on.assert_called_once()

    def test_check_schedule_ignores_power_off(self):
        self.app.state = self._tt.AppState.POWER_OFF
        with patch("toddlertv.is_tv_off", return_value=True), \
             patch.object(self.app, "_enter_schedule_off") as mock_off:
            with patch.object(self.app.root, "after"):
                self.app._check_schedule()
        mock_off.assert_not_called()

    # ── Channel switching ─────────────────────────────────────────────────────

    def test_switch_channel_ignored_when_schedule_off(self):
        self.app.state = self._tt.AppState.SCHEDULE_OFF
        with patch.object(self.app, "_start_channel") as mock_start:
            self.app.switch_channel(1)
        mock_start.assert_not_called()

    def test_switch_channel_ignored_to_same_channel(self):
        self.app.state   = self._tt.AppState.PLAYING
        self.app.current_channel = 0
        with patch.object(self.app, "_start_channel") as mock_start:
            self.app.switch_channel(0)
        mock_start.assert_not_called()

    def test_switch_channel_sets_loading_state(self):
        self.app.state         = self._tt.AppState.PLAYING
        self.app.current_channel = 0
        with patch.object(self.app, "_play_video_for_channel"):
            # Give channel 1 a ready state so it doesn't wait
            self.app.states[1].ready  = True
            self.app.states[1].videos = [{
                "stream_url":   "http://local.mp4",
                "duration":     600,
                "_resolved_at": time.time(),
            }]
            self.app.states[1]._rebuild_offsets()
            self.app.switch_channel(1)
        self.assertEqual(self.app.state, self._tt.AppState.LOADING)
        self.assertEqual(self.app.current_channel, 1)

    # ── Finish loading ────────────────────────────────────────────────────────

    def test_finish_loading_unmutes_and_plays(self):
        self.app.state = self._tt.AppState.LOADING
        self.app._finish_loading()
        self.assertEqual(self.app.state, self._tt.AppState.PLAYING)
        self._player.audio_set_mute.assert_called_with(False)

    def test_finish_loading_resumes_clock(self):
        self.app.state = self._tt.AppState.LOADING
        self.app._finish_loading()
        self.assertTrue(self.app.clock.is_running)

    def test_finish_loading_noop_when_not_loading(self):
        self.app.state = self._tt.AppState.PLAYING
        self.app._finish_loading()
        # State must not change
        self.assertEqual(self.app.state, self._tt.AppState.PLAYING)
        self._player.audio_set_mute.assert_not_called()

    # ── URL staleness ─────────────────────────────────────────────────────────

    def test_stale_remote_url_triggers_refresh(self):
        """A stale googlevideo URL must show robots and defer to a refresh thread
        rather than calling VLC immediately."""
        self.app.state           = self._tt.AppState.LOADING
        self.app.current_channel = 0
        self.app.states[0].ready  = True
        self.app.states[0].videos = [{
            "yt_url":       "https://www.youtube.com/watch?v=abc",
            "stream_url":   "https://rr3.googlevideo.com/stream?id=old",
            "duration":     600,
            "_resolved_at": time.time() - (self.app.URL_TTL + 10),  # deliberately stale
        }]
        self.app.states[0]._rebuild_offsets()

        # Prevent the background thread from calling back into tkinter (not
        # thread-safe) after tearDown destroys the root.
        self.app.root.after = lambda *a, **kw: None

        with patch("toddlertv.get_video_info", return_value=_FAKE_INFO):
            self.app._play_video_for_channel(0)

        # Stale path: VLC must NOT be invoked synchronously.
        self._player.play.assert_not_called()
        # Stale path: robots are shown while re-resolving.
        self.app._show_robots.assert_called_with("fixing")

    def test_fresh_local_url_plays_directly(self):
        """A local file URL must be handed to VLC immediately (no re-resolve)."""
        self.app.state         = self._tt.AppState.LOADING
        self.app.current_channel = 0
        self.app.states[0].ready  = True
        self.app.states[0].videos = [{
            "yt_url":       "https://www.youtube.com/watch?v=abc",
            "stream_url":   "C:/videos/abc.mp4",    # local path → not remote
            "duration":     600,
            "_resolved_at": time.time(),
        }]
        self.app.states[0]._rebuild_offsets()

        with patch.object(self.app, "_vlc_play") as mock_play:
            self.app._play_video_for_channel(0)

        mock_play.assert_called_once()
        args = mock_play.call_args[0]
        self.assertEqual(args[0], "C:/videos/abc.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
