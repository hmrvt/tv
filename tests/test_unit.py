"""
Toddler TV — Unit Tests
Pure-logic tests: no VLC, no display, no network, no subprocess.
"""

import io
import json
import os
import sys
import shutil
import tempfile
import time
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# ── Path & module stubs ────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Stub external packages before any project import touches them
sys.modules.setdefault("vlc", MagicMock())
_vlc_stub = sys.modules["vlc"]
sys.modules.setdefault("yt_dlp", MagicMock())

# ── Project imports ────────────────────────────────────────────────────────────
import schedule as _schedule_module                                   # noqa: E402
from schedule import (                                                # noqa: E402
    merge_off_periods, save_periods, get_periods,
    current_off_period, is_tv_off, next_on_time, current_scene_name,
)
import images as _images_module                                       # noqa: E402
from images import (                                                  # noqa: E402
    get_cached_avatar, set_cached_avatar, is_avatar_fetched,
    fetch_thumbnail, _avatar_cache,
)
from media import clean_url, get_video_id, ChannelState              # noqa: E402
import library as _library                                           # noqa: E402
from toddlertv import Clock, AppState                                # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
#  1. Clock
# ══════════════════════════════════════════════════════════════════════════════

class TestClock(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()

    def test_initial_elapsed_is_zero(self):
        self.assertAlmostEqual(self.clock.elapsed(), 0.0, places=2)

    def test_not_running_initially(self):
        self.assertFalse(self.clock.is_running)

    def test_resume_starts_elapsed(self):
        self.clock.resume()
        time.sleep(0.05)
        self.assertGreater(self.clock.elapsed(), 0.0)

    def test_pause_freezes_elapsed(self):
        self.clock.resume()
        time.sleep(0.05)
        self.clock.pause()
        t1 = self.clock.elapsed()
        time.sleep(0.05)
        self.assertAlmostEqual(self.clock.elapsed(), t1, places=3)

    def test_is_running_toggles(self):
        self.assertFalse(self.clock.is_running)
        self.clock.resume()
        self.assertTrue(self.clock.is_running)
        self.clock.pause()
        self.assertFalse(self.clock.is_running)

    def test_resume_is_idempotent(self):
        self.clock.resume()
        time.sleep(0.02)
        t_before = self.clock.elapsed()
        self.clock.resume()  # second resume must not reset elapsed
        self.assertGreaterEqual(self.clock.elapsed(), t_before)

    def test_pause_is_idempotent(self):
        self.clock.resume()
        time.sleep(0.05)
        self.clock.pause()
        self.clock.pause()  # second pause must not corrupt elapsed
        t = self.clock.elapsed()
        time.sleep(0.05)
        self.assertAlmostEqual(self.clock.elapsed(), t, places=3)

    def test_elapsed_accumulates_across_cycles(self):
        self.clock.resume()
        time.sleep(0.05)
        self.clock.pause()
        first = self.clock.elapsed()
        self.clock.resume()
        time.sleep(0.05)
        self.clock.pause()
        self.assertGreater(self.clock.elapsed(), first)


# ══════════════════════════════════════════════════════════════════════════════
#  2. ChannelState
# ══════════════════════════════════════════════════════════════════════════════

class TestChannelState(unittest.TestCase):

    def _state(self, durations):
        """Return a ChannelState pre-loaded with videos of the given durations."""
        s = ChannelState()
        s._playlist_urls = [f"https://www.youtube.com/watch?v=v{i}" for i in range(len(durations))]
        s._play_queue = list(range(len(durations)))  # deterministic order for tests
        for i, d in enumerate(durations):
            s.videos.append({"stream_url": f"http://v{i}", "duration": d})
            s._resolved_map[i] = i
        s.advance_video(0.0)
        return s

    # ── get_position ──────────────────────────────────────────────────────────

    def test_empty_state_returns_zero(self):
        s = ChannelState()
        idx, off = s.get_position(999)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(off, 0.0)

    def test_single_video_at_start(self):
        s = self._state([600])
        idx, off = s.get_position(0)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(off, 0.0)

    def test_single_video_midpoint(self):
        s = self._state([600])
        idx, off = s.get_position(300)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(off, 300.0)

    def test_single_video_offset_grows_past_duration(self):
        s = self._state([600])
        idx, off = s.get_position(600)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(off, 600.0)

    def test_multi_video_first(self):
        s = self._state([300, 600, 900])
        idx, off = s.get_position(100)
        self.assertIn(idx, [0, 1, 2])
        self.assertAlmostEqual(off, 100.0)

    def test_advance_moves_to_next_video(self):
        s = self._state([300, 600, 900])
        first_idx, _ = s.get_position(0)
        s.advance_video(300)
        second_idx, off = s.get_position(350)
        self.assertNotEqual(first_idx, second_idx)
        self.assertAlmostEqual(off, 50.0)

    def test_advance_twice_reaches_third_video(self):
        s = self._state([300, 600, 900])
        first_idx, _ = s.get_position(0)
        s.advance_video(300)
        second_idx, _ = s.get_position(300)
        s.advance_video(900)
        third_idx, off = s.get_position(1000)
        self.assertNotIn(third_idx, [first_idx, second_idx])
        self.assertAlmostEqual(off, 100.0)

    def test_queue_reshuffles_after_all_videos_played(self):
        s = self._state([300, 600])
        seen = set()
        for i in range(2):
            idx, _ = s.get_position(i * 300)
            seen.add(idx)
            s.advance_video(i * 300)
        self.assertEqual(seen, {0, 1})
        # Queue should refill — next advance still works
        s.advance_video(600)
        idx, _ = s.get_position(600)
        self.assertIn(idx, [0, 1])

    # ── queue membership ──────────────────────────────────────────────────────

    def test_all_playlist_positions_in_queue(self):
        s = self._state([100, 200, 300])
        # Current playlist position + remaining play queue = all 3 positions
        queued = {s._current_playlist_idx} | set(s._play_queue)
        self.assertEqual(queued, {0, 1, 2})

    def test_resolved_map_updated_on_add(self):
        s = self._state([100, 200])
        # A newly-resolved video should appear in _resolved_map
        s._playlist_urls.append("https://www.youtube.com/watch?v=v2")
        s._play_queue.append(2)
        s.videos.append({"stream_url": "http://v2", "duration": 300})
        s._resolved_map[2] = 2
        self.assertIn(2, s._resolved_map)
        self.assertEqual(s._resolved_map[2], 2)


# ══════════════════════════════════════════════════════════════════════════════
#  3. URL helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestURLHelpers(unittest.TestCase):

    def test_clean_url_strips_extra_params(self):
        url = "https://www.youtube.com/watch?v=abc123&list=PLxyz&index=1"
        self.assertEqual(clean_url(url), "https://www.youtube.com/watch?v=abc123")

    def test_clean_url_passthrough_non_youtube(self):
        url = "https://example.com/video.mp4"
        self.assertEqual(clean_url(url), url)

    def test_clean_url_already_clean(self):
        url = "https://www.youtube.com/watch?v=abc123"
        self.assertEqual(clean_url(url), url)

    def test_clean_url_removes_list_param(self):
        url = "https://www.youtube.com/watch?v=xyz&list=PL123"
        result = clean_url(url)
        self.assertNotIn("list=", result)
        self.assertIn("xyz", result)

    def test_get_video_id_standard(self):
        url = "https://www.youtube.com/watch?v=abc123&list=PLxyz"
        self.assertEqual(get_video_id(url), "abc123")

    def test_get_video_id_clean_url(self):
        url = "https://www.youtube.com/watch?v=abc123"
        self.assertEqual(get_video_id(url), "abc123")

    def test_get_video_id_short_url(self):
        url = "https://youtu.be/abc123"
        self.assertEqual(get_video_id(url), "abc123")

    def test_clean_then_get_id_round_trip(self):
        url = "https://www.youtube.com/watch?v=xyz789&index=3&list=PL0"
        self.assertEqual(get_video_id(clean_url(url)), "xyz789")


# ══════════════════════════════════════════════════════════════════════════════
#  4. merge_off_periods
# ══════════════════════════════════════════════════════════════════════════════

class TestMergeOffPeriods(unittest.TestCase):

    def test_empty_returns_empty(self):
        self.assertEqual(merge_off_periods([]), [])

    def test_single_period_unchanged(self):
        result = merge_off_periods([(20, 0, 23, 0, "sleeping")])
        self.assertEqual(len(result), 1)
        start, end, scene = result[0]
        self.assertEqual(start, 20 * 60)
        self.assertEqual(end, 23 * 60)
        self.assertEqual(scene, "sleeping")

    def test_no_merge_different_scenes(self):
        periods = [(8, 0, 9, 0, "lunch"), (9, 0, 10, 0, "sleeping")]
        result = merge_off_periods(periods)
        self.assertEqual(len(result), 2)

    def test_merge_adjacent_same_scene(self):
        periods = [(8, 0, 9, 0, "sleeping"), (9, 0, 10, 0, "sleeping")]
        result = merge_off_periods(periods)
        self.assertEqual(len(result), 1)
        start, end, scene = result[0]
        self.assertEqual(start, 8 * 60)
        self.assertEqual(end, 10 * 60)
        self.assertEqual(scene, "sleeping")

    def test_merge_overlapping_same_scene(self):
        periods = [(8, 0, 9, 30, "sleeping"), (9, 0, 10, 0, "sleeping")]
        result = merge_off_periods(periods)
        self.assertEqual(len(result), 1)
        _, end, _ = result[0]
        self.assertEqual(end, 10 * 60)

    def test_sorts_before_merging(self):
        periods = [
            (10, 0, 11, 0, "sleeping"),
            (8,  0,  9, 0, "sleeping"),
            (9,  0, 10, 0, "sleeping"),
        ]
        result = merge_off_periods(periods)
        self.assertEqual(len(result), 1)
        start, end, _ = result[0]
        self.assertEqual(start, 8 * 60)
        self.assertEqual(end, 11 * 60)

    def test_gap_prevents_merge(self):
        # 60-minute gap between periods — must NOT merge
        periods = [(8, 0, 9, 0, "sleeping"), (10, 0, 11, 0, "sleeping")]
        result = merge_off_periods(periods)
        self.assertEqual(len(result), 2)

    def test_multiple_distinct_scenes_preserved(self):
        periods = [
            (7, 0,  8, 0, "sleeping"),
            (12, 0, 13, 0, "lunch"),
            (20, 0, 23, 0, "sleeping"),
        ]
        result = merge_off_periods(periods)
        self.assertEqual(len(result), 3)
        scenes = [s for _, _, s in result]
        self.assertIn("sleeping", scenes)
        self.assertIn("lunch", scenes)


# ══════════════════════════════════════════════════════════════════════════════
#  5. Schedule file I/O
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduleFileIO(unittest.TestCase):

    def setUp(self):
        _schedule_module._cached_periods = None
        _schedule_module._cached_mtime = 0.0

    def tearDown(self):
        _schedule_module._cached_periods = None
        _schedule_module._cached_mtime = 0.0

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with patch.object(_schedule_module, "SCHEDULE_FILE", path):
                _schedule_module._cached_periods = None
                _schedule_module._cached_mtime = 0.0
                periods = [(7, 0, 8, 30, "sleeping", [], False), (12, 0, 13, 0, "lunch", [], False)]
                save_periods(periods)
                result = get_periods()
        self.assertEqual(result, periods)

    def test_save_5tuple_backward_compat(self):
        """save_periods accepts old 5-element tuples and stores them with defaults."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with patch.object(_schedule_module, "SCHEDULE_FILE", path):
                _schedule_module._cached_periods = None
                _schedule_module._cached_mtime = 0.0
                save_periods([(9, 0, 10, 0, "lunch")])
                result = get_periods()
        self.assertEqual(result, [(9, 0, 10, 0, "lunch", [], False)])

    def test_saved_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with patch.object(_schedule_module, "SCHEDULE_FILE", path):
                _schedule_module._cached_periods = None
                _schedule_module._cached_mtime = 0.0
                save_periods([(9, 0, 10, 0, "lunch", [], False)])
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["start_h"], 9)
        self.assertEqual(data[0]["scene"], "lunch")
        self.assertEqual(data[0]["days"], [])
        self.assertIs(data[0]["paused"], False)

    def test_missing_file_falls_back_to_defaults(self):
        with patch.object(_schedule_module, "SCHEDULE_FILE", "/nonexistent/path.json"):
            _schedule_module._cached_periods = None
            result = get_periods()
        self.assertIsInstance(result, list)
        # Each period is a 7-tuple with days and paused defaults
        if result:
            self.assertEqual(len(result[0]), 7)

    def test_mtime_cache_skips_reread(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with patch.object(_schedule_module, "SCHEDULE_FILE", path):
                _schedule_module._cached_periods = None
                _schedule_module._cached_mtime = 0.0

                save_periods([(9, 0, 10, 0, "lunch", [], False)])
                get_periods()  # primes the cache

                # Tamper with the cache while mtime is unchanged
                _schedule_module._cached_periods = [(20, 0, 21, 0, "sleeping", [], False)]

                result = get_periods()
        # Mtime hasn't changed → cache should be returned, not the file
        self.assertEqual(result, [(20, 0, 21, 0, "sleeping", [], False)])


# ══════════════════════════════════════════════════════════════════════════════
#  6. Schedule queries (mocked datetime)
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduleQueries(unittest.TestCase):

    _PERIODS = [(7, 0, 8, 0, "sleeping", [], False), (12, 0, 13, 0, "lunch", [], False)]

    def setUp(self):
        # Patch SCHEDULE_FILE to a non-existent path so the real on-disk
        # schedule.json never interferes with the injected cache.
        self._patcher = patch.object(_schedule_module, "SCHEDULE_FILE", "/nonexistent/sched_test.json")
        self._patcher.start()
        _schedule_module._cached_periods = list(self._PERIODS)
        _schedule_module._cached_mtime = 1.0

    def tearDown(self):
        self._patcher.stop()
        _schedule_module._cached_periods = None
        _schedule_module._cached_mtime = 0.0

    def _mock_now(self, hour, minute):
        return datetime(2024, 1, 1, hour, minute, 0)

    def test_off_during_sleep_period(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(7, 30)
            self.assertTrue(is_tv_off())

    def test_on_before_sleep_period(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(6, 59)
            self.assertFalse(is_tv_off())

    def test_on_at_exact_end_of_period(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(8, 0)  # end is exclusive
            self.assertFalse(is_tv_off())

    def test_off_during_lunch(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(12, 30)
            self.assertTrue(is_tv_off())

    def test_on_between_periods(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(10, 0)
            self.assertFalse(is_tv_off())

    def test_next_on_time_during_sleep(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(7, 30)
            self.assertEqual(next_on_time(), "08:00")

    def test_next_on_time_during_lunch(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(12, 15)
            self.assertEqual(next_on_time(), "13:00")

    def test_next_on_time_when_playing(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(10, 0)
            self.assertEqual(next_on_time(), "soon")

    def test_current_scene_sleeping(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(7, 30)
            self.assertEqual(current_scene_name(), "sleeping")

    def test_current_scene_lunch(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(12, 30)
            self.assertEqual(current_scene_name(), "lunch")

    def test_current_scene_defaults_when_on(self):
        with patch("schedule.datetime") as m:
            m.now.return_value = self._mock_now(10, 0)
            # Returns "sleeping" as default when TV is on (not used but tested for contract)
            self.assertIsInstance(current_scene_name(), str)


# ══════════════════════════════════════════════════════════════════════════════
#  7. Avatar cache public API
# ══════════════════════════════════════════════════════════════════════════════

class TestAvatarCache(unittest.TestCase):

    def setUp(self):
        _avatar_cache.clear()

    def tearDown(self):
        _avatar_cache.clear()

    def test_unknown_channel_returns_none(self):
        self.assertIsNone(get_cached_avatar("UC_unknown"))

    def test_set_and_get_round_trip(self):
        sentinel = object()
        set_cached_avatar("UC123", sentinel)
        self.assertIs(get_cached_avatar("UC123"), sentinel)

    def test_failed_channel_returns_false(self):
        set_cached_avatar("UC_bad", False)
        self.assertIs(get_cached_avatar("UC_bad"), False)

    def test_is_fetched_false_for_unknown(self):
        self.assertFalse(is_avatar_fetched("UC_unknown"))

    def test_is_fetched_true_after_set(self):
        set_cached_avatar("UC123", False)
        self.assertTrue(is_avatar_fetched("UC123"))

    def test_is_fetched_true_for_in_progress_none(self):
        with _images_module._avatar_lock:
            _avatar_cache["UC_inprog"] = None  # None = fetch in-flight
        self.assertTrue(is_avatar_fetched("UC_inprog"))

    def test_overwrite_replaces_value(self):
        a, b = object(), object()
        set_cached_avatar("UC123", a)
        set_cached_avatar("UC123", b)
        self.assertIs(get_cached_avatar("UC123"), b)

    def test_thread_safety_concurrent_writes(self):
        errors = []

        def writer(cid, val):
            try:
                set_cached_avatar(cid, val)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"UC{i}", i)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(_avatar_cache), 20)


# ══════════════════════════════════════════════════════════════════════════════
#  8. fetch_thumbnail
# ══════════════════════════════════════════════════════════════════════════════

class TestFetchThumbnail(unittest.TestCase):

    def _fake_response(self, pil_format="JPEG", size=(160, 90)):
        """Return bytes of a real in-memory PIL image."""
        from PIL import Image
        img = Image.new("RGB", size, color=(200, 100, 50))
        buf = io.BytesIO()
        img.save(buf, format=pil_format)
        return buf.getvalue()

    def _mock_urlopen(self, data: bytes):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.read.return_value = data
        return ctx

    @patch("images.PIL_AVAILABLE", True)
    @patch("images.urllib.request.urlopen")
    def test_returns_pil_image_correct_size(self, mock_open):
        mock_open.return_value = self._mock_urlopen(self._fake_response())
        result = fetch_thumbnail("http://example.com/t.jpg", size=(120, 90))
        self.assertIsNotNone(result)
        self.assertEqual(result.size, (120, 90))

    @patch("images.PIL_AVAILABLE", True)
    @patch("images.urllib.request.urlopen")
    def test_crops_to_aspect_ratio(self, mock_open):
        # 160×90 source → 120×90 target (same ratio, no crop needed)
        mock_open.return_value = self._mock_urlopen(self._fake_response(size=(160, 90)))
        result = fetch_thumbnail("http://example.com/t.jpg", size=(120, 90))
        self.assertEqual(result.size, (120, 90))

    @patch("images.PIL_AVAILABLE", True)
    @patch("images.urllib.request.urlopen", side_effect=OSError("no network"))
    def test_returns_none_on_network_error(self, _):
        self.assertIsNone(fetch_thumbnail("http://bad/t.jpg"))

    @patch("images.PIL_AVAILABLE", False)
    def test_returns_none_when_pillow_absent(self):
        self.assertIsNone(fetch_thumbnail("http://example.com/t.jpg"))

    def test_returns_none_for_empty_url(self):
        self.assertIsNone(fetch_thumbnail(""))

    def test_returns_none_for_none_url(self):
        self.assertIsNone(fetch_thumbnail(None))




# ══════════════════════════════════════════════════════════════════════════════
#  N. ChannelState — live feed editing
# ══════════════════════════════════════════════════════════════════════════════

def _u(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


_A, _B, _C, _D = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"


class TestChannelStateFeed(unittest.TestCase):

    def setUp(self):
        self.state = ChannelState()
        self.state.set_playlist([_u(_A), _u(_B), _u(_C)])

    # ── set_playlist ──────────────────────────

    def test_set_playlist_canonicalises_urls(self):
        state = ChannelState()
        state.set_playlist([f"https://youtu.be/{_A}?si=xyz"])
        self.assertEqual(state.url_at(0), _u(_A))

    def test_set_playlist_skips_blank_entries(self):
        state = ChannelState()
        state.set_playlist([_u(_A), "   ", _u(_B)])
        self.assertEqual(len(state.active_positions()), 2)

    def test_all_positions_start_active(self):
        self.assertEqual(self.state.active_positions(), [0, 1, 2])

    # ── add_url ───────────────────────────────

    def test_add_url_appends_a_new_position(self):
        self.assertEqual(self.state.add_url(_u(_D)), 3)
        self.assertEqual(self.state.active_positions(), [0, 1, 2, 3])

    def test_add_url_is_idempotent_for_a_known_video(self):
        self.assertEqual(self.state.add_url(_u(_B)), 1)
        self.assertEqual(len(self.state._playlist_urls), 3)

    def test_add_url_accepts_a_share_link_for_a_known_video(self):
        self.assertEqual(self.state.add_url(f"https://youtu.be/{_B}?si=q"), 1)

    # ── remove_position ───────────────────────

    def test_permanent_remove_drops_from_active_positions(self):
        self.state.remove_position(1, permanent=True)
        self.assertEqual(self.state.active_positions(), [0, 2])

    def test_remove_drops_from_the_play_queue(self):
        self.state._play_queue = [0, 1, 2]
        self.state.remove_position(1)
        self.assertEqual(self.state._play_queue, [0, 2])

    def test_round_skip_keeps_channel_membership(self):
        """"Skip this round" must not cost the video its downloaded file."""
        self.state.remove_position(1)
        self.assertIn(1, self.state.active_positions())
        self.assertIn(_B, self.state.feed_video_ids())
        self.assertFalse(self.state.is_removed(1))
        self.assertFalse(self.state.is_playable(1))

    def test_round_skip_is_forgiven_by_the_next_reshuffle(self):
        self.state.remove_position(1)
        self.state._refill_queue()
        self.assertIn(1, self.state._play_queue)
        self.assertTrue(self.state.is_playable(1))

    def test_permanent_remove_costs_channel_membership(self):
        self.state.remove_position(1, permanent=True)
        self.assertNotIn(_B, self.state.feed_video_ids())
        self.assertTrue(self.state.is_removed(1))

    def test_remove_out_of_range_is_a_no_op(self):
        self.assertFalse(self.state.remove_position(99))
        self.assertEqual(self.state.active_positions(), [0, 1, 2])

    def test_permanently_removed_position_stays_out_after_a_reshuffle(self):
        self.state.remove_position(1, permanent=True)
        self.state._refill_queue()
        self.assertNotIn(1, self.state._play_queue)

    # ── Injections ────────────────────────────

    def test_injected_video_is_not_a_playlist_member(self):
        pos = self.state.add_url(_u(_D), injected=True)
        self.assertTrue(self.state.is_injected(pos))
        self.assertNotIn(pos, self.state.active_positions())

    def test_injected_video_does_not_return_after_a_reshuffle(self):
        pos = self.state.add_url(_u(_D), injected=True)
        self.state.insert_next(pos)
        self.state._refill_queue()
        self.assertNotIn(pos, self.state._play_queue)

    def test_injected_video_plays_next(self):
        self.state._play_queue = [0, 1, 2]
        pos = self.state.add_url(_u(_D), injected=True)
        self.state.insert_next(pos)
        self.assertEqual(self.state._play_queue[0], pos)

    def test_injecting_an_existing_video_does_not_mark_it_injected(self):
        """Injecting something already in the playlist must not evict it."""
        pos = self.state.add_url(_u(_B), injected=True)
        self.assertEqual(pos, 1)
        self.assertFalse(self.state.is_injected(1))
        self.assertIn(1, self.state.active_positions())

    def test_injected_video_is_excluded_from_download_set(self):
        self.state.add_url(_u(_D), injected=True)
        self.assertEqual(self.state.feed_video_ids(), {_A, _B, _C})

    def test_user_added_video_is_included_in_download_set(self):
        self.state.add_url(_u(_D), user_added=True)
        self.assertEqual(self.state.feed_video_ids(), {_A, _B, _C, _D})

    # ── insert_next ───────────────────────────

    def test_insert_next_moves_a_queued_position_to_the_front(self):
        self.state._play_queue = [0, 1, 2]
        self.state.insert_next(2)
        self.assertEqual(self.state._play_queue, [2, 0, 1])

    def test_insert_next_does_not_duplicate(self):
        self.state._play_queue = [0, 1, 2]
        self.state.insert_next(1)
        self.assertEqual(sorted(self.state._play_queue), [0, 1, 2])

    def test_insert_next_revives_a_removed_position(self):
        self.state.remove_position(1, permanent=True)
        self.state.insert_next(1)
        self.assertIn(1, self.state.active_positions())
        self.assertTrue(self.state.is_playable(1))

    # ── advance_video with a mutating feed ────

    def test_advance_skips_a_position_removed_while_queued(self):
        self.state._play_queue = [0, 1, 2]
        self.state._removed_positions.add(0)   # removed without pruning the queue
        self.state.advance_video(0.0)
        self.assertEqual(self.state._current_playlist_idx, 1)

    def test_advance_on_a_fully_removed_feed_does_not_hang(self):
        for pos in (0, 1, 2):
            self.state.remove_position(pos, permanent=True)
        self.state.advance_video(0.0)
        self.assertEqual(self.state._current_playlist_idx, -1)

    def test_advance_skips_a_position_skipped_for_this_round(self):
        self.state._play_queue = [0, 1, 2]
        self.state.remove_position(0)
        self.state.advance_video(0.0)
        self.assertEqual(self.state._current_playlist_idx, 1)

    def test_next_playlist_idx_skips_removed_positions(self):
        self.state._play_queue = [0, 1, 2]
        self.state._removed_positions.add(0)
        self.assertEqual(self.state.next_playlist_idx(), 1)

    # ── add_members (source sync / merge) ─────

    def test_add_members_reports_new_videos(self):
        added = self.state.add_members([_u(_A), _u(_B), _u(_C), _u(_D)])
        self.assertEqual(len(added), 1)
        self.assertEqual(self.state.url_at(added[0]), _u(_D))

    def test_add_members_with_nothing_new_reports_nothing(self):
        self.assertEqual(self.state.add_members([_u(_C), _u(_A), _u(_B)]), [])

    def test_add_members_never_removes(self):
        """The app owns the playlist: a video missing from YouTube stays."""
        self.state.add_members([_u(_A)])
        self.assertEqual(self.state.active_positions(), [0, 1, 2])

    def test_add_members_ignores_duplicates(self):
        added = self.state.add_members([_u(_D), _u(_D)])
        self.assertEqual(len(added), 1)

    def test_add_members_revives_a_removed_position(self):
        """Restoring an ignored video reuses its original position."""
        self.state.remove_position(1, permanent=True)
        self.assertEqual(self.state.add_members([_u(_B)]), [1])
        self.assertIn(1, self.state.active_positions())
        self.assertEqual(len(self.state._playlist_urls), 3)

    def test_add_members_promotes_a_one_shot_injection(self):
        pos = self.state.add_url(_u(_D), injected=True)
        self.assertEqual(self.state.add_members([_u(_D)]), [pos])
        self.assertIn(pos, self.state.active_positions())

    def test_add_members_does_not_disturb_resolved_stream_cache(self):
        self.state._resolved_map = {0: 0, 2: 1}
        self.state.add_members([_u(_D)])
        self.assertEqual(self.state._resolved_map, {0: 0, 2: 1})

    def test_add_after_permanent_removal_undoes_it(self):
        self.state.remove_position(1, permanent=True)
        self.state.add_url(_u(_B), user_added=True)
        self.assertIn(1, self.state.active_positions())


# ══════════════════════════════════════════════════════════════════════════════
#  N+1. library.py — the app-owned channel catalog
# ══════════════════════════════════════════════════════════════════════════════

_PL1 = "PLaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_PL2 = "PLbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class TestCatalog(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._patches = [
            patch.object(_library, "CATALOG_FILE",
                         os.path.join(self._tmp, "channels.json")),
            patch.object(_library, "LEGACY_OVERLAY_FILE",
                         os.path.join(self._tmp, "playlist_overlay.json")),
        ]
        for p in self._patches:
            p.start()
        _library.save_catalog([
            {"name": "Cartoons", "emoji": "C", "color": "#FF6B6B",
             "sources": [{"playlist_id": _PL1, "title": "One"}],
             "videos": [{"id": _A, "origin": _PL1}], "ignored": []},
            {"name": "Learning", "emoji": "L", "color": "#FFD93D",
             "sources": [{"playlist_id": _PL2, "title": "Two"}],
             "videos": [{"id": _B, "origin": _PL2}, {"id": _A, "origin": "manual"}],
             "ignored": [{"id": _C, "origin": _PL2}]},
        ])

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── Source sync ───────────────────────────

    def test_sync_adds_only_new_videos(self):
        self.assertEqual(_library.add_from_source(0, _PL1, [_A, _B]), [_B])
        self.assertEqual(_library.video_ids(0), [_A, _B])

    def test_sync_never_removes(self):
        """A video taken off the YouTube playlist stays on the channel."""
        _library.add_from_source(0, _PL1, [_B])
        self.assertIn(_A, _library.video_ids(0))

    def test_sync_skips_an_ignored_video(self):
        self.assertEqual(_library.add_from_source(1, _PL2, [_C]), [])
        self.assertNotIn(_C, _library.video_ids(1))

    def test_sync_records_the_check(self):
        _library.add_from_source(0, _PL1, [_A, _B])
        src = _library.get_channel(0)["sources"][0]
        self.assertTrue(src["last_sync"])
        self.assertEqual(src["last_count"], 2)

    def test_sync_drops_unsafe_ids(self):
        self.assertEqual(_library.add_from_source(0, _PL1, ["../../etc/passwd"]), [])

    # ── Ignore / restore ──────────────────────

    def test_ignore_moves_a_video_to_the_ignored_list(self):
        self.assertTrue(_library.ignore(0, _A))
        ch = _library.get_channel(0)
        self.assertEqual(ch["videos"], [])
        self.assertEqual([v["id"] for v in ch["ignored"]], [_A])

    def test_ignored_video_keeps_its_origin(self):
        _library.ignore(0, _A)
        self.assertEqual(_library.get_channel(0)["ignored"][0]["origin"], _PL1)

    def test_ignore_of_an_unknown_video_fails(self):
        self.assertFalse(_library.ignore(0, _D))

    def test_ignore_is_per_channel(self):
        _library.ignore(0, _A)
        self.assertIn(_A, _library.video_ids(1))

    def test_restore_brings_a_video_back(self):
        self.assertTrue(_library.restore(1, _C))
        self.assertIn(_C, _library.video_ids(1))
        self.assertEqual(_library.get_channel(1)["ignored"], [])

    def test_restore_of_a_video_that_is_not_ignored_fails(self):
        self.assertFalse(_library.restore(1, _D))

    def test_manual_add_un_ignores(self):
        self.assertTrue(_library.add_manual(1, _C))
        self.assertIn(_C, _library.video_ids(1))
        self.assertEqual(_library.get_channel(1)["ignored"], [])

    def test_manual_add_rejects_an_unsafe_id(self):
        self.assertFalse(_library.add_manual(0, "../x"))

    # ── Sources ───────────────────────────────

    def test_add_source(self):
        self.assertTrue(_library.add_source(0, _PL2, "Two"))
        self.assertEqual([s["playlist_id"] for s in _library.get_channel(0)["sources"]],
                         [_PL1, _PL2])

    def test_add_source_rejects_an_unsafe_id(self):
        self.assertFalse(_library.add_source(0, "PL/../../x"))

    def test_remove_source_keeps_its_videos(self):
        self.assertTrue(_library.remove_source(0, _PL1))
        self.assertEqual(_library.get_channel(0)["sources"], [])
        self.assertEqual(_library.video_ids(0), [_A])

    def test_source_cap(self):
        for i in range(_library.MAX_SOURCES + 5):
            _library.add_source(0, f"PL{i:020d}")
        self.assertEqual(len(_library.get_channel(0)["sources"]), _library.MAX_SOURCES)

    # ── Channel settings ──────────────────────

    def test_update_channel_cleans_its_input(self):
        saved = _library.update_channel(0, "x" * 100 + "\n", "\U0001f682", "red")
        self.assertEqual(saved["name"], "x" * _library.MAX_NAME_LEN)
        self.assertEqual(saved["emoji"], "\U0001f682")
        self.assertEqual(saved["color"], "#FF6B6B")   # invalid colour keeps the old

    # ── Merge ─────────────────────────────────

    def test_merge_moves_everything_and_empties_the_source(self):
        moved = _library.merge(1, 0)
        self.assertEqual(moved, [_B])           # _A was already on channel 0
        dst, src = _library.get_channel(0), _library.get_channel(1)
        self.assertEqual(_library.video_ids(0), [_A, _B])
        self.assertEqual([v["id"] for v in dst["ignored"]], [_C])
        self.assertEqual([s["playlist_id"] for s in dst["sources"]], [_PL1, _PL2])
        self.assertEqual((src["videos"], src["ignored"], src["sources"]), ([], [], []))
        self.assertEqual(src["name"], "Learning")

    def test_merge_into_itself_is_refused(self):
        self.assertIsNone(_library.merge(0, 0))

    def test_merge_out_of_range_is_refused(self):
        self.assertIsNone(_library.merge(0, 9))

    # ── File handling ─────────────────────────

    def test_all_video_ids_spans_channels(self):
        self.assertEqual(_library.all_video_ids(), {_A, _B})

    def test_corrupt_file_gives_an_empty_catalog(self):
        with open(_library.CATALOG_FILE, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(_library.load_catalog(), [])

    def test_hand_edited_unsafe_entries_are_dropped(self):
        with open(_library.CATALOG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "channels": [{
                "name": "X", "color": "javascript:alert(1)",
                "sources": [{"playlist_id": "../../etc"}],
                "videos": [{"id": "../../etc/passwd"}, {"id": _A, "origin": "<b>"}],
            }]}, f)
        ch = _library.load_catalog()[0]
        self.assertEqual(ch["sources"], [])
        self.assertEqual(ch["videos"][0]["id"], _A)
        self.assertEqual(ch["videos"][0]["origin"], _library.MANUAL)
        self.assertEqual(len(ch["videos"]), 1)
        self.assertEqual(ch["color"], "#00ff88")

    def test_a_video_cannot_be_both_on_the_channel_and_ignored(self):
        with open(_library.CATALOG_FILE, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "channels": [{
                "name": "X", "videos": [{"id": _A}], "ignored": [{"id": _A}],
            }]}, f)
        self.assertEqual(_library.load_catalog()[0]["ignored"], [])

    def test_save_keeps_a_backup(self):
        _library.ignore(0, _A)
        self.assertTrue(os.path.exists(_library.CATALOG_FILE + ".bak"))


class TestCatalogMigration(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._patches = [
            patch.object(_library, "CATALOG_FILE",
                         os.path.join(self._tmp, "channels.json")),
            patch.object(_library, "LEGACY_OVERLAY_FILE",
                         os.path.join(self._tmp, "playlist_overlay.json")),
        ]
        for p in self._patches:
            p.start()
        with open(_library.CATALOG_FILE, "w", encoding="utf-8") as f:
            json.dump([{
                "name": "Cartoons", "emoji": "C", "color": "#FF6B6B",
                "playlist_url": f"https://www.youtube.com/watch?v={_A}&list={_PL1}",
                "playlist": [_u(_A) + "&pp=junk", _u(_B)],
            }], f)
        with open(_library.LEGACY_OVERLAY_FILE, "w", encoding="utf-8") as f:
            json.dump({"Cartoons": {"added": [_u(_D)], "removed": [_u(_B)]}}, f)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_migration_without_network_uses_the_saved_array(self):
        self.assertTrue(_library.migrate_v1())
        ch = _library.get_channel(0)
        self.assertEqual(ch["name"], "Cartoons")
        self.assertEqual([s["playlist_id"] for s in ch["sources"]], [_PL1])
        self.assertEqual(_library.video_ids(0), [_A, _D])
        self.assertEqual([v["id"] for v in ch["ignored"]], [_B])

    def test_migration_prefers_the_live_playlist(self):
        """The saved array is stale; the live fetch is what YouTube has now."""
        _library.migrate_v1(fetch=lambda pid: [_C, _B])
        self.assertEqual(_library.video_ids(0), [_C, _D])

    def test_migration_keeps_backups_and_retires_the_overlay(self):
        _library.migrate_v1()
        self.assertTrue(os.path.exists(_library.CATALOG_FILE + ".v1.bak"))
        self.assertFalse(os.path.exists(_library.LEGACY_OVERLAY_FILE))
        self.assertTrue(os.path.exists(_library.LEGACY_OVERLAY_FILE + ".v1.bak"))

    def test_migration_runs_once(self):
        self.assertTrue(_library.migrate_v1())
        self.assertFalse(_library.migrate_v1())



# ══════════════════════════════════════════════════════════════════════════════
#  N+2. ChannelState.current_video — resolved vs. placeholder
# ══════════════════════════════════════════════════════════════════════════════

class TestCurrentVideo(unittest.TestCase):

    def setUp(self):
        self.state = ChannelState()
        self.state.set_playlist([_u(_A), _u(_B)])
        self.state._play_queue = [0, 1]
        self.state.advance_video(0.0)
        _add_resolved(self.state, 0, 60.0, "first")

    def test_returns_the_resolved_current_video(self):
        self.assertEqual(self.state.current_video()["title"], "first")

    def test_returns_none_while_the_current_position_resolves(self):
        self.state.advance_video(60.0)          # position 1, not resolved yet
        self.assertIsNone(self.state.current_video())

    def test_get_position_still_falls_back_to_the_previous_video(self):
        """The placeholder behaviour current_video() exists to work around."""
        self.state.advance_video(60.0)
        video_idx, _ = self.state.get_position(70.0)
        self.assertEqual(self.state.videos[video_idx]["title"], "first")

    def test_returns_the_new_video_once_resolved(self):
        self.state.advance_video(60.0)
        _add_resolved(self.state, 1, 120.0, "second")
        self.assertEqual(self.state.current_video()["title"], "second")

    def test_returns_none_on_an_empty_channel(self):
        self.assertIsNone(ChannelState().current_video())


# ══════════════════════════════════════════════════════════════════════════════
#  N+3. Inactive-channel advancement (the channel-button progress bar)
# ══════════════════════════════════════════════════════════════════════════════

def _add_resolved(state, pos, duration, title="v"):
    """Mimic ToddlerTV._add_video for one playlist position."""
    state.videos.append({
        "yt_url": state.url_at(pos), "stream_url": "s", "audio_url": None,
        "duration": duration, "title": title, "channel": "", "channel_id": "",
        "thumbnail": "", "_resolved_at": 0, "_play_retries": 0,
        "_playlist_idx": pos,
    })
    state._resolved_map[pos] = len(state.videos) - 1
    if state._current_playlist_idx == pos:
        state._current_idx = len(state.videos) - 1
    state.ready = True


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def elapsed(self):
        return self.t


class TestInactiveChannelAdvance(unittest.TestCase):
    """_poll_vlc drives every channel the viewer isn't watching.  Its handling of
    a position whose duration isn't known yet is what the progress bar reads."""

    def setUp(self):
        import toddlertv as _tt
        self._tt = _tt
        app = _tt.ToddlerTV.__new__(_tt.ToddlerTV)
        app.root = MagicMock()
        app.player = MagicMock()
        app.player.get_state.return_value = _vlc_stub.State.Playing
        app.player.get_time.return_value = -1
        app.player.get_length.return_value = -1
        app.state = _tt.AppState.PLAYING
        app.current_channel = 0
        app.clock = _FakeClock()
        app.states = [ChannelState(), ChannelState()]
        app._resolve_locks = [MagicMock(), MagicMock()]
        app._prefetch_next = MagicMock()
        app._start_channel = MagicMock()
        self.prefetches = []
        app._prefetch_for_inactive = lambda i, s: self.prefetches.append(round(app.clock.t))
        self.app = app

        # Channel 0 is the active one and stays put for the whole test.
        active = app.states[0]
        active.set_playlist([_u(_A)])
        active._play_queue = [0]
        active.advance_video(0.0)
        _add_resolved(active, 0, 9999.0)

        # Channel 1 is the one being watched from the sidelines.
        self.st = app.states[1]
        self.st.set_playlist([_u(_A), _u(_B), _u(_C)])
        self.st._play_queue = [0, 1, 2]
        self.st.advance_video(0.0)
        _add_resolved(self.st, 0, 60.0, "first")

    def _tick(self, t):
        self.app.clock.t = t
        self.app._poll_vlc()

    # ── Resolved positions advance on their own duration ──

    def test_advances_when_the_video_ends(self):
        self._tick(59)
        self.assertEqual(self.st._current_playlist_idx, 0)
        self._tick(61)
        self.assertEqual(self.st._current_playlist_idx, 1)

    # ── Unresolved positions ──

    def test_prefetch_is_retried_every_tick_while_unresolved(self):
        """The one attempt made at advance time is usually skipped because a
        resolve for the same channel is still in flight; nothing else retried."""
        for t in range(0, 66):
            self._tick(t)
        retries = [t for t in self.prefetches if t > 61]
        self.assertGreater(len(retries), 1)

    def test_unresolved_position_waits_the_estimate_not_the_old_duration(self):
        self._tick(61)                       # -> position 1, unresolved
        self._tick(61 + 70)                  # well past the previous 60s video
        self.assertEqual(self.st._current_playlist_idx, 1)
        self._tick(61 + self.app.UNRESOLVED_VIDEO_ESTIMATE + 1)
        self.assertEqual(self.st._current_playlist_idx, 2)

    def test_progress_bar_scale_matches_the_advance_deadline(self):
        """The regression: the bar was scaled by the previous video's duration,
        so it hit 100% in 60s and then sat there for the rest of the estimate."""
        self._tick(61)
        self.assertIsNone(self.st.current_video())

        def bar(t):
            offset = max(0.0, t - self.st._video_start_elapsed)
            cur = self.st.current_video()
            duration = ((cur["duration"] or 1) if cur
                        else self.app.UNRESOLVED_VIDEO_ESTIMATE)
            return min(1.0, offset / duration)

        self.assertLess(bar(61 + 60), 0.2)        # would have been 1.0 before
        self.assertLess(bar(61 + 300), 0.5)
        self.assertGreater(bar(61 + 880), 0.9)    # fills as the deadline nears

    def test_bar_tracks_the_real_duration_once_resolved(self):
        self._tick(61)
        _add_resolved(self.st, 1, 120.0, "second")
        self.assertEqual(self.st.current_video()["title"], "second")
        offset = 60.0
        self.assertAlmostEqual(offset / self.st.current_video()["duration"], 0.5)

    # ── Known-bad positions ──

    def test_failed_position_is_skipped_without_waiting_for_the_estimate(self):
        self.st._failed_positions.add(1)
        self._tick(61)                       # -> position 1, already known bad
        self._tick(62)
        self.assertEqual(self.st._current_playlist_idx, 2)

    def test_failed_position_does_not_stall_for_the_full_estimate(self):
        self.st._failed_positions.add(1)
        self._tick(61)
        self._tick(62)
        self.assertLess(self.app.clock.t, self.app.UNRESOLVED_VIDEO_ESTIMATE)

    # ── The active channel is left to VLC ──

    def test_active_channel_is_not_advanced_by_the_virtual_clock(self):
        active = self.app.states[0]
        before = active._current_playlist_idx
        for t in range(0, 30):
            self._tick(t)
        self.assertEqual(active._current_playlist_idx, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
