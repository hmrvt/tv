"""
Security regression tests — media.py
Covers: CWE-22 (path traversal via video_id), Finding 9 (removed yt-dlp flags).

Run with:  python -m unittest tests.test_security_media -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

sys.modules.setdefault("vlc",    MagicMock())
sys.modules.setdefault("yt_dlp", MagicMock())

from media import (
    clean_url, get_video_id, find_local_file,
    _validate_video_id, _SAFE_VIDEO_ID, _VIDEOS_DIR_REAL,
)
import media as _media_module


# ─────────────────────────────────────────────
#  1. _validate_video_id — allowlist regex
# ─────────────────────────────────────────────

class TestValidateVideoId(unittest.TestCase):

    # ── Accept ────────────────────────────────

    def test_standard_youtube_id_accepted(self):
        self.assertEqual(_validate_video_id("jNQXAC9IVRw"), "jNQXAC9IVRw")

    def test_id_with_hyphen_accepted(self):
        self.assertEqual(_validate_video_id("abc-123_XYZ"), "abc-123_XYZ")

    def test_single_char_accepted(self):
        self.assertEqual(_validate_video_id("a"), "a")

    def test_20_char_id_accepted(self):
        self.assertEqual(_validate_video_id("A" * 20), "A" * 20)

    # ── Reject ────────────────────────────────

    def test_empty_string_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("")

    def test_path_traversal_dotdot_slash_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("../../etc/passwd")

    def test_path_traversal_dotdot_only_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("..")

    def test_forward_slash_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("valid/../../etc")

    def test_backslash_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("valid\\..\\secret")

    def test_null_byte_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("abc\x00def")

    def test_newline_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("abc\ndef")

    def test_space_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("abc def")

    def test_21_chars_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("A" * 21)

    def test_angle_bracket_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("<script>")

    def test_semicolon_rejected(self):
        with self.assertRaises(ValueError):
            _validate_video_id("abc;rm -rf /")


# ─────────────────────────────────────────────
#  2. get_video_id — source-level sanitisation
# ─────────────────────────────────────────────

class TestGetVideoId(unittest.TestCase):

    def test_standard_url_returns_id(self):
        self.assertEqual(
            get_video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw"),
            "jNQXAC9IVRw",
        )

    def test_url_with_extra_params_returns_id(self):
        self.assertEqual(
            get_video_id("https://www.youtube.com/watch?v=abc123&list=PLxyz"),
            "abc123",
        )

    def test_short_url_returns_id(self):
        self.assertEqual(
            get_video_id("https://youtu.be/abc123"),
            "abc123",
        )

    def test_path_traversal_in_watch_param_raises(self):
        with self.assertRaises(ValueError):
            get_video_id("https://www.youtube.com/watch?v=../../etc/passwd")

    def test_dot_in_short_url_segment_raises(self):
        # split("/")[-1] on "youtu.be/../../../etc/shadow" gives "shadow" — safe.
        # The real short-URL risk is a dot inside the last path segment itself,
        # e.g. "abc.def", which is not a valid YouTube ID character.
        with self.assertRaises(ValueError):
            get_video_id("https://youtu.be/abc.def")

    def test_null_byte_in_url_raises(self):
        with self.assertRaises(ValueError):
            get_video_id("https://www.youtube.com/watch?v=abc\x00evil")

    def test_slash_in_video_param_raises(self):
        with self.assertRaises(ValueError):
            get_video_id("https://www.youtube.com/watch?v=abc/def")


# ─────────────────────────────────────────────
#  3. find_local_file — sink-level boundary check
# ─────────────────────────────────────────────

class TestFindLocalFile(unittest.TestCase):

    def test_invalid_id_raises_before_filesystem_access(self):
        """_validate_video_id must fire before any os.path call."""
        with patch("os.path.exists") as mock_exists:
            with self.assertRaises(ValueError):
                find_local_file("../../etc/passwd")
            mock_exists.assert_not_called()

    def test_valid_id_missing_file_returns_none(self):
        with patch("os.path.exists", return_value=False):
            result = find_local_file("jNQXAC9IVRw")
        self.assertIsNone(result)

    def test_valid_id_present_file_returns_path_inside_videos_dir(self):
        with patch("os.path.exists", return_value=True):
            result = find_local_file("jNQXAC9IVRw")
        self.assertIsNotNone(result)
        real = os.path.realpath(result)
        self.assertTrue(
            real.startswith(_VIDEOS_DIR_REAL),
            f"Returned path {real!r} escapes VIDEOS_DIR {_VIDEOS_DIR_REAL!r}",
        )

    def test_symlink_escape_is_blocked(self):
        """Even if a symlink inside videos/ points outside the directory,
        the realpath check must catch it."""
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmp:
            videos_dir = os.path.join(tmp, "videos")
            os.makedirs(videos_dir)
            secret_dir = os.path.join(tmp, "secret")
            os.makedirs(secret_dir)
            secret_file = os.path.join(secret_dir, "data.mp4")
            open(secret_file, "w").close()

            # Create a symlink inside videos/ that points outside
            link_path = os.path.join(videos_dir, "escape.mp4")
            os.symlink(secret_file, link_path)

            with patch.object(_media_module, "VIDEOS_DIR", videos_dir), \
                 patch.object(_media_module, "_VIDEOS_DIR_REAL",
                              os.path.realpath(videos_dir)):
                with self.assertRaises(ValueError):
                    find_local_file("escape")


# ─────────────────────────────────────────────
#  4. Removed yt-dlp flags (Finding 9)
# ─────────────────────────────────────────────

class TestRemovedYtDlpFlags(unittest.TestCase):
    """Ensure the undocumented --js-runtimes and --remote-components flags
    are never passed to yt-dlp subprocess calls."""

    def _capture_subprocess_calls(self, func, *args, **kwargs):
        """Run func with subprocess.run mocked; return all command lists used."""
        calls = []
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return mock_result

        with patch("media.subprocess.run", side_effect=fake_run):
            try:
                func(*args, **kwargs)
            except Exception:
                pass
        return calls

    def test_get_video_info_does_not_use_js_runtimes(self):
        calls = self._capture_subprocess_calls(
            _media_module.get_video_info,
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        )
        for cmd in calls:
            self.assertNotIn("--js-runtimes", cmd,
                             f"--js-runtimes found in: {cmd}")

    def test_get_video_info_does_not_use_remote_components(self):
        calls = self._capture_subprocess_calls(
            _media_module.get_video_info,
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        )
        for cmd in calls:
            self.assertNotIn("--remote-components", cmd,
                             f"--remote-components found in: {cmd}")

    def test_get_playlist_urls_does_not_use_js_runtimes(self):
        calls = self._capture_subprocess_calls(
            _media_module.get_playlist_urls,
            "https://www.youtube.com/playlist?list=PLtest",
        )
        for cmd in calls:
            self.assertNotIn("--js-runtimes", cmd)

    def test_no_ejs_github_reference_anywhere(self):
        """'ejs:github' must not appear in any subprocess command."""
        calls = self._capture_subprocess_calls(
            _media_module.get_video_info,
            "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        )
        for cmd in calls:
            for arg in cmd:
                self.assertNotIn("ejs:github", arg,
                                 f"'ejs:github' found in arg {arg!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
