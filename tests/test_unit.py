"""
Toddler TV — Unit Tests
Pure-logic tests: no VLC, no display, no network, no subprocess.
"""

import io
import json
import os
import sys
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
        for i, d in enumerate(durations):
            s.videos.append({"stream_url": f"http://v{i}", "duration": d})
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

    def test_all_videos_in_initial_queue(self):
        s = self._state([100, 200, 300])
        # All 3 indices must be scheduled (current + 2 remaining)
        queued = {s._current_idx} | set(s._unplayed)
        self.assertEqual(queued, {0, 1, 2})

    def test_queue_new_video_inserted_into_unplayed(self):
        s = self._state([100, 200])
        s.videos.append({"stream_url": "http://v2", "duration": 300})
        s.queue_new_video(2)
        self.assertIn(2, s._unplayed)


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
                periods = [(7, 0, 8, 30, "sleeping"), (12, 0, 13, 0, "lunch")]
                save_periods(periods)
                result = get_periods()
        self.assertEqual(result, periods)

    def test_saved_file_is_valid_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with patch.object(_schedule_module, "SCHEDULE_FILE", path):
                _schedule_module._cached_periods = None
                _schedule_module._cached_mtime = 0.0
                save_periods([(9, 0, 10, 0, "lunch")])
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["start_h"], 9)
        self.assertEqual(data[0]["scene"], "lunch")

    def test_missing_file_falls_back_to_defaults(self):
        with patch.object(_schedule_module, "SCHEDULE_FILE", "/nonexistent/path.json"):
            _schedule_module._cached_periods = None
            result = get_periods()
        self.assertIsInstance(result, list)

    def test_mtime_cache_skips_reread(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "schedule.json")
            with patch.object(_schedule_module, "SCHEDULE_FILE", path):
                _schedule_module._cached_periods = None
                _schedule_module._cached_mtime = 0.0

                save_periods([(9, 0, 10, 0, "lunch")])
                get_periods()  # primes the cache

                # Tamper with the cache while mtime is unchanged
                _schedule_module._cached_periods = [(20, 0, 21, 0, "sleeping")]

                result = get_periods()
        # Mtime hasn't changed → cache should be returned, not the file
        self.assertEqual(result, [(20, 0, 21, 0, "sleeping")])


# ══════════════════════════════════════════════════════════════════════════════
#  6. Schedule queries (mocked datetime)
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduleQueries(unittest.TestCase):

    _PERIODS = [(7, 0, 8, 0, "sleeping"), (12, 0, 13, 0, "lunch")]

    def setUp(self):
        _schedule_module._cached_periods = list(self._PERIODS)
        _schedule_module._cached_mtime = 1.0  # non-zero → file won't be re-read

    def tearDown(self):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
