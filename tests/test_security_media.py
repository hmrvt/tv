"""
Security regression tests — media.py and downloads.py
Covers: CWE-22 (path traversal via video_id, and at the delete sink),
        CWE-78/CWE-88 (subprocess argv hygiene in the background downloader),
        CWE-117 (control characters in yt-dlp stderr),
        Finding 9 (removed yt-dlp flags),
        CWE-20 / N1 (playlist source links and their argv).

Run with:  python -m unittest tests.test_security_media -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

sys.modules.setdefault("vlc",    MagicMock())
sys.modules.setdefault("yt_dlp", MagicMock())

from media import (
    clean_url, get_video_id, find_local_file, validate_youtube_url,
    validate_youtube_playlist_url, playlist_url,
    ffprobe_duration,
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




# ══════════════════════════════════════════════════════════════════════════════
#  5. delete_local_file — CWE-22 boundary at the delete sink
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteLocalFile(unittest.TestCase):
    """Deleting is a stronger sink than reading: an escape here destroys data."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._videos = os.path.join(self._tmp, "videos")
        os.makedirs(self._videos)
        self._patch = patch.multiple(
            _media_module,
            VIDEOS_DIR=self._videos,
            _VIDEOS_DIR_REAL=os.path.realpath(self._videos),
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _touch(self, name: str) -> str:
        path = os.path.join(self._videos, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        return path

    # ── Reject ────────────────────────────────

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            _media_module.delete_local_file("../../etc/passwd")

    def test_absolute_path_rejected(self):
        with self.assertRaises(ValueError):
            _media_module.delete_local_file("C:/Windows/System32/config")

    def test_backslash_rejected(self):
        with self.assertRaises(ValueError):
            _media_module.delete_local_file("..\\..\\secrets")

    def test_empty_id_rejected(self):
        with self.assertRaises(ValueError):
            _media_module.delete_local_file("")

    def test_traversal_does_not_delete_outside_videos_dir(self):
        outside = os.path.join(self._tmp, "important.mp4")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("keep me")
        with self.assertRaises(ValueError):
            _media_module.delete_local_file("../important")
        self.assertTrue(os.path.exists(outside))

    # ── Accept ────────────────────────────────

    def test_deletes_every_extension(self):
        for ext in ("mp4", "mkv", "webm", "part"):
            self._touch(f"vidA.{ext}")
        self.assertTrue(_media_module.delete_local_file("vidA"))
        self.assertEqual(os.listdir(self._videos), [])

    def test_missing_file_returns_false(self):
        self.assertFalse(_media_module.delete_local_file("nothingHere"))

    def test_leaves_other_videos_alone(self):
        self._touch("keepThis.mp4")
        self._touch("dropThis.mp4")
        _media_module.delete_local_file("dropThis")
        self.assertEqual(os.listdir(self._videos), ["keepThis.mp4"])


# ══════════════════════════════════════════════════════════════════════════════
#  6. list_local_video_ids — only well-formed IDs escape the directory listing
# ══════════════════════════════════════════════════════════════════════════════

class TestListLocalVideoIds(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._videos = os.path.join(self._tmp, "videos")
        os.makedirs(self._videos)
        self._patch = patch.object(
            _media_module, "_VIDEOS_DIR_REAL", os.path.realpath(self._videos)
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _touch(self, name: str) -> None:
        with open(os.path.join(self._videos, name), "w", encoding="utf-8") as f:
            f.write("x")

    def test_returns_video_ids(self):
        self._touch("jNQXAC9IVRw.mp4")
        self._touch("abc-123_XYZ.webm")
        self.assertEqual(
            _media_module.list_local_video_ids(),
            {"jNQXAC9IVRw", "abc-123_XYZ"},
        )

    def test_ignores_non_video_extensions(self):
        self._touch(".metadata_cache.json")
        self._touch("notes.txt")
        self.assertEqual(_media_module.list_local_video_ids(), set())

    def test_ignores_partial_downloads(self):
        self._touch("halfDone.mp4.part")
        self.assertEqual(_media_module.list_local_video_ids(), set())

    def test_ignores_names_that_are_not_safe_ids(self):
        """A stem that fails the allowlist must never reach a delete call."""
        self._touch("what a name.mp4")
        self._touch(("x" * 40) + ".mp4")
        self.assertEqual(_media_module.list_local_video_ids(), set())

    def test_missing_directory_returns_empty(self):
        shutil.rmtree(self._videos)
        self.assertEqual(_media_module.list_local_video_ids(), set())


# ══════════════════════════════════════════════════════════════════════════════
#  7. downloads.py — subprocess hygiene (CWE-78, CWE-88, CWE-22)
# ══════════════════════════════════════════════════════════════════════════════

class TestDownloadSubprocess(unittest.TestCase):
    """The background downloader is the only place that shells out at runtime
    with an ID that arrived over HTTP, so its argv is worth pinning down."""

    def setUp(self):
        import downloads
        self._downloads = downloads
        self._mgr = downloads.DownloadManager()

    def _capture(self, video_id: str) -> list:
        calls = []

        def _fake_run(cmd, *a, **kw):
            calls.append((cmd, kw))
            result = MagicMock()
            result.returncode = 1
            result.stderr = ""
            return result

        with patch.object(self._downloads.subprocess, "run", _fake_run), \
             patch.object(self._downloads, "find_local_file", return_value=None):
            self._mgr._download(video_id)
        return calls

    def test_command_is_a_list_not_a_string(self):
        cmd, _ = self._capture("jNQXAC9IVRw")[0]
        self.assertIsInstance(cmd, list)

    def test_shell_is_disabled(self):
        _, kwargs = self._capture("jNQXAC9IVRw")[0]
        self.assertIs(kwargs.get("shell"), False)

    def test_url_is_rebuilt_from_the_validated_id(self):
        """The positional URL must be derived from the ID, never passed through."""
        cmd, _ = self._capture("jNQXAC9IVRw")[0]
        self.assertEqual(cmd[-1], "https://www.youtube.com/watch?v=jNQXAC9IVRw")

    def test_url_is_guarded_by_an_end_of_options_separator(self):
        """'--' stops yt-dlp reading the URL as an option (option injection)."""
        cmd, _ = self._capture("jNQXAC9IVRw")[0]
        self.assertEqual(cmd[-2], "--")

    def test_download_has_a_timeout(self):
        _, kwargs = self._capture("jNQXAC9IVRw")[0]
        self.assertTrue(kwargs.get("timeout"))

    def test_output_template_stays_inside_videos_dir(self):
        cmd, _ = self._capture("jNQXAC9IVRw")[0]
        template = cmd[cmd.index("--output") + 1]
        self.assertTrue(template.startswith(_media_module.VIDEOS_DIR))

    # ── ID validation at the sink ─────────────

    def test_traversal_id_raises_before_any_subprocess(self):
        calls = []
        with patch.object(self._downloads.subprocess, "run",
                          lambda *a, **k: calls.append(a)):
            with self.assertRaises(ValueError):
                self._mgr._download("../../etc/passwd")
        self.assertEqual(calls, [])

    def test_enqueue_rejects_unsafe_id(self):
        self.assertFalse(self._mgr.enqueue("../evil"))
        self.assertEqual(self._mgr.status()["pending"], 0)

    def test_enqueue_skips_already_downloaded(self):
        with patch.object(self._downloads, "find_local_file", return_value="x.mp4"):
            self.assertFalse(self._mgr.enqueue("jNQXAC9IVRw"))

    def test_enqueue_is_idempotent(self):
        with patch.object(self._downloads, "find_local_file", return_value=None):
            self.assertTrue(self._mgr.enqueue("jNQXAC9IVRw"))
            self.assertFalse(self._mgr.enqueue("jNQXAC9IVRw"))
        self.assertEqual(self._mgr.status()["pending"], 1)

    def test_front_enqueue_jumps_the_queue(self):
        with patch.object(self._downloads, "find_local_file", return_value=None):
            self._mgr.enqueue("aaaaaaaaaaa")
            self._mgr.enqueue("bbbbbbbbbbb", front=True)
        self.assertEqual(list(self._mgr._queue)[0], "bbbbbbbbbbb")

    def test_stderr_control_characters_are_stripped(self):
        """yt-dlp stderr is third-party output — no raw escapes to the terminal."""
        printed = []

        def _fake_run(cmd, *a, **kw):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "ERROR: \x1b[31mboom\x1b[0m\x07"
            return result

        with patch.object(self._downloads.subprocess, "run", _fake_run), \
             patch.object(self._downloads, "find_local_file", return_value=None), \
             patch("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            self._mgr._download("jNQXAC9IVRw")

        joined = "\n".join(printed)
        self.assertNotIn("\x1b", joined)
        self.assertNotIn("\x07", joined)


# ══════════════════════════════════════════════════════════════════════════════
#  8. validate_youtube_url — host whitelist for links arriving from outside
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateYoutubeUrl(unittest.TestCase):
    """clean_url alone will turn almost any string into a watch URL, so the
    entry points that accept pasted text go through this instead."""

    WATCH = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

    # ── Accept ────────────────────────────────

    def test_canonical_watch_url(self):
        self.assertEqual(validate_youtube_url(self.WATCH), self.WATCH)

    def test_share_link_with_tracking_param(self):
        self.assertEqual(
            validate_youtube_url("https://youtu.be/jNQXAC9IVRw?si=AbC"), self.WATCH)

    def test_mobile_url(self):
        self.assertEqual(
            validate_youtube_url("https://m.youtube.com/watch?v=jNQXAC9IVRw"), self.WATCH)

    def test_shorts_url(self):
        self.assertEqual(
            validate_youtube_url("https://www.youtube.com/shorts/jNQXAC9IVRw"), self.WATCH)

    def test_app_scheme(self):
        self.assertEqual(
            validate_youtube_url("vnd.youtube://jNQXAC9IVRw"), self.WATCH)

    def test_bare_video_id(self):
        self.assertEqual(validate_youtube_url("jNQXAC9IVRw"), self.WATCH)

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(
            validate_youtube_url(" 	" + self.WATCH + " "), self.WATCH)

    # ── Reject ────────────────────────────────

    def test_file_url_rejected(self):
        self.assertIsNone(validate_youtube_url("file:///etc/passwd"))

    def test_foreign_host_rejected(self):
        self.assertIsNone(validate_youtube_url("https://evil.test/watch?v=jNQXAC9IVRw"))

    def test_lookalike_host_rejected(self):
        self.assertIsNone(
            validate_youtube_url("https://youtube.com.evil.test/watch?v=jNQXAC9IVRw"))

    def test_subdomain_lookalike_rejected(self):
        self.assertIsNone(
            validate_youtube_url("https://notyoutube.com/watch?v=jNQXAC9IVRw"))

    def test_userinfo_host_spoof_rejected(self):
        self.assertIsNone(
            validate_youtube_url("https://www.youtube.com@evil.test/watch?v=jNQXAC9IVRw"))

    def test_javascript_scheme_rejected(self):
        self.assertIsNone(validate_youtube_url("javascript:alert(1)"))

    def test_data_scheme_rejected(self):
        self.assertIsNone(validate_youtube_url("data:text/html,<script>x</script>"))

    def test_path_traversal_rejected(self):
        self.assertIsNone(validate_youtube_url("../../etc/passwd"))

    def test_bare_path_rejected(self):
        self.assertIsNone(validate_youtube_url("/etc/passwd"))

    def test_empty_rejected(self):
        self.assertIsNone(validate_youtube_url(""))

    def test_none_rejected(self):
        self.assertIsNone(validate_youtube_url(None))

    def test_oversized_id_rejected(self):
        self.assertIsNone(
            validate_youtube_url("https://www.youtube.com/watch?v=" + "A" * 200))


# ══════════════════════════════════════════════════════════════════════════════
#  9. ffprobe_duration — local file duration, and its argv
# ══════════════════════════════════════════════════════════════════════════════

class TestFfprobeDuration(unittest.TestCase):
    """yt-dlp cannot read a local path ("is not a valid URL"), which silently
    sent every downloaded video to the 1800s fallback."""

    def _run_with(self, stdout=b"", returncode=0, exc=None):
        calls = []

        def _fake_run(cmd, *a, **kw):
            calls.append((cmd, kw))
            if exc:
                raise exc
            result = MagicMock()
            result.returncode = returncode
            result.stdout = stdout
            return result

        with patch.object(_media_module, "FFMPEG_LOCATION", "/ff"),              patch.object(_media_module.os.path, "isfile", return_value=True),              patch.object(_media_module.subprocess, "run", _fake_run):
            value = ffprobe_duration("/videos/abc.mp4")
        return value, calls

    def test_parses_a_duration(self):
        value, _ = self._run_with(b"487.874467\n")
        self.assertAlmostEqual(value, 487.874467)

    def test_unreadable_output_returns_none(self):
        self.assertIsNone(self._run_with(b"N/A\n")[0])

    def test_empty_output_returns_none(self):
        self.assertIsNone(self._run_with(b"")[0])

    def test_zero_duration_returns_none(self):
        self.assertIsNone(self._run_with(b"0.0")[0])

    def test_subprocess_failure_returns_none(self):
        self.assertIsNone(self._run_with(exc=OSError("boom"))[0])

    def test_returns_none_without_ffmpeg(self):
        with patch.object(_media_module, "FFMPEG_LOCATION", None):
            self.assertIsNone(ffprobe_duration("/videos/abc.mp4"))

    # ── argv hygiene ──────────────────────────

    def test_command_is_a_list(self):
        _, calls = self._run_with(b"1.0")
        self.assertIsInstance(calls[0][0], list)

    def test_shell_is_disabled(self):
        _, calls = self._run_with(b"1.0")
        self.assertIs(calls[0][1].get("shell"), False)

    def test_has_a_timeout(self):
        _, calls = self._run_with(b"1.0")
        self.assertTrue(calls[0][1].get("timeout"))

    def test_path_is_passed_as_an_input_argument(self):
        """-i keeps a filename starting with '-' from being read as an option;
        YouTube IDs routinely start with a hyphen."""
        _, calls = self._run_with(b"1.0")
        cmd = calls[0][0]
        self.assertEqual(cmd[-2], "-i")
        self.assertEqual(cmd[-1], "/videos/abc.mp4")

    def test_hyphen_leading_filename_stays_an_input(self):
        calls = []

        def _fake_run(cmd, *a, **kw):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = b"12.0"
            return result

        with patch.object(_media_module, "FFMPEG_LOCATION", "/ff"),              patch.object(_media_module.os.path, "isfile", return_value=True),              patch.object(_media_module.subprocess, "run", _fake_run):
            ffprobe_duration("/videos/-0zZ9O3KrOk.mp4")
        self.assertEqual(calls[0][-2], "-i")
        self.assertEqual(calls[0][-1], "/videos/-0zZ9O3KrOk.mp4")


# ══════════════════════════════════════════════════════════════════════════════
#  10. Playlist sources — validate_youtube_playlist_url and playlist argv
#      CWE-20 (host + ID whitelist), finding N1 (option injection)
# ══════════════════════════════════════════════════════════════════════════════

_PL = "PLVOKDAn3IigUIpvFWScjlgOcnquOLp3Fp"


class TestValidatePlaylistUrl(unittest.TestCase):

    def test_playlist_link_accepted(self):
        self.assertEqual(
            validate_youtube_playlist_url(f"https://www.youtube.com/playlist?list={_PL}"),
            _PL)

    def test_watch_link_with_list_accepted(self):
        self.assertEqual(validate_youtube_playlist_url(
            f"https://youtube.com/watch?v=P0rfy_inw3U&list={_PL}&index=2"), _PL)

    def test_bare_playlist_id_accepted(self):
        self.assertEqual(validate_youtube_playlist_url(_PL), _PL)

    def test_foreign_host_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(
            f"https://evil.example/playlist?list={_PL}"))

    def test_lookalike_host_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(
            f"https://youtube.com.evil.example/playlist?list={_PL}"))

    def test_file_scheme_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(f"file:///etc/passwd?list={_PL}"))

    def test_link_without_list_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(
            "https://www.youtube.com/watch?v=P0rfy_inw3U"))

    def test_option_shaped_id_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(
            "https://www.youtube.com/playlist?list=--exec%20calc"))

    def test_traversal_id_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(
            "https://www.youtube.com/playlist?list=../../etc"))

    def test_bare_video_id_is_not_a_playlist(self):
        self.assertIsNone(validate_youtube_playlist_url("jNQXAC9IVRw"))

    def test_empty_and_none_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(""))
        self.assertIsNone(validate_youtube_playlist_url(None))

    def test_oversized_id_rejected(self):
        self.assertIsNone(validate_youtube_playlist_url(
            "https://www.youtube.com/playlist?list=PL" + "A" * 200))

    def test_playlist_url_rebuilds_from_the_id(self):
        self.assertEqual(playlist_url(_PL),
                         f"https://www.youtube.com/playlist?list={_PL}")

    def test_playlist_url_refuses_an_unsafe_id(self):
        with self.assertRaises(ValueError):
            playlist_url("x&exec=1")


class TestPlaylistSubprocess(unittest.TestCase):

    def _capture(self, func, *args):
        calls = []
        result = MagicMock(stdout="", stderr="", returncode=0)

        def fake_run(cmd, **kw):
            calls.append((list(cmd), kw))
            return result

        with patch("media.subprocess.run", side_effect=fake_run):
            func(*args)
        return calls

    def test_playlist_fetch_guards_the_url_with_a_separator(self):
        cmd, _ = self._capture(_media_module.get_playlist_urls, playlist_url(_PL))[0]
        self.assertEqual(cmd[-2:], ["--", playlist_url(_PL)])

    def test_playlist_title_guards_the_url_with_a_separator(self):
        cmd, kw = self._capture(_media_module.get_playlist_title, playlist_url(_PL))[0]
        self.assertEqual(cmd[-2:], ["--", playlist_url(_PL)])
        self.assertIs(kw.get("shell"), False)

    def test_playlist_title_strips_control_characters(self):
        """yt-dlp output is third-party text bound for the catalog file."""
        result = MagicMock(stdout="Trains\x1b[31m\x07 & Planes\n", returncode=0)
        with patch("media.subprocess.run", return_value=result):
            title = _media_module.get_playlist_title(playlist_url(_PL))
        self.assertNotIn("\x1b", title)
        self.assertNotIn("\x07", title)

if __name__ == "__main__":
    unittest.main(verbosity=2)
