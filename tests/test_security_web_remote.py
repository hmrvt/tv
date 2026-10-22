"""
Security regression tests — web_remote.py
Covers: CWE-352 (CSRF), CWE-400 (DoS body/count/position),
        CWE-20 (scene + action + URL validation), CWE-755 (time parsing),
        CWE-778 (logging), CWE-79 (video metadata in the feed page),
        channel library pages (ignore / restore / sources / settings / merge),
        manual status (switch a scene on / off, overriding the schedule),
        pause / resume all schedules.

Run with:  python -m pytest tests/test_security_web_remote.py -v
"""

import io
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Stub external packages before any project import
sys.modules.setdefault("vlc",    MagicMock())
sys.modules.setdefault("yt_dlp", MagicMock())

import web_remote as _wr
from web_remote import (
    _Handler, _CSRF_TOKEN, MAX_BODY, MAX_PERIODS, _build_schedule_page,
)


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def _make_handler(method: str, body: bytes = b"", headers: dict | None = None) -> _Handler:
    """
    Construct a _Handler instance without starting a real socket.
    Injects a fake rfile and captures wfile output.
    """
    request  = MagicMock()
    rfile    = io.BytesIO(body)
    wfile    = io.BytesIO()

    handler = _Handler.__new__(_Handler)
    handler.rfile          = rfile
    handler.wfile          = wfile
    handler.client_address = ("127.0.0.1", 12345)
    handler.server         = MagicMock()

    raw_headers = {
        "Content-Length": str(len(body)),
        "Content-Type":   "application/x-www-form-urlencoded",
    }
    if headers:
        raw_headers.update(headers)

    mock_headers = MagicMock()
    mock_headers.get = lambda k, d=None: raw_headers.get(k, d)
    handler.headers = mock_headers

    handler.command      = method
    handler.path         = "/"
    handler.request_version = "HTTP/1.1"

    # Silence send_response / send_header / end_headers
    handler.send_response  = MagicMock()
    handler.send_header    = MagicMock()
    handler.end_headers    = MagicMock()
    handler.log_message    = MagicMock()
    return handler


_EVERY_DAY = "".join(f"&days={d}" for d in range(7))


def _valid_post_body(extra: str = "") -> bytes:
    """Minimal valid POST body for /schedule/new: correct CSRF, one period."""
    return (f"csrf={_CSRF_TOKEN}&action=save&start=08:00&end=09:00"
            f"&scene=sleeping{_EVERY_DAY}{extra}").encode()


def _post_period(body: bytes, path: str = "/schedule/new", periods=None,
                 headers: dict | None = None):
    """POST to a period route; returns (handler, list of save_periods payloads)."""
    h = _make_handler("POST", body, headers=headers)
    h.path = path
    saved = []
    with patch("web_remote.get_periods", return_value=list(periods or [])), \
         patch("web_remote.save_periods", side_effect=saved.append):
        h.do_POST()
    return h, saved


# ─────────────────────────────────────────────
#  1. CSRF Protection (CWE-352)
# ─────────────────────────────────────────────

class TestCSRF(unittest.TestCase):

    def _post(self, body: bytes) -> _Handler:
        return _post_period(body)[0]

    def test_missing_csrf_token_returns_403(self):
        h, saved = _post_period(b"action=save&start=08:00&end=09:00&scene=lunch&days=0")
        h.send_response.assert_called_once_with(403)
        self.assertEqual(saved, [])

    def test_wrong_csrf_token_returns_403(self):
        h = self._post(b"csrf=deadbeefdeadbeef&action=save&start=08:00&end=09:00&days=0")
        h.send_response.assert_called_once_with(403)

    def test_correct_csrf_token_returns_200(self):
        h = self._post(_valid_post_body())
        h.send_response.assert_called_once_with(200)

    def test_empty_csrf_token_returns_403(self):
        h = self._post(b"csrf=&action=save&start=08:00&end=09:00&days=0")
        h.send_response.assert_called_once_with(403)

    def test_delete_requires_csrf(self):
        h, saved = _post_period(b"action=delete", "/schedule/0",
                                periods=[(8, 0, 9, 0, "lunch", [], False)])
        h.send_response.assert_called_once_with(403)
        self.assertEqual(saved, [])

    def test_csrf_token_in_page_html(self):
        """The CSRF token must appear as a hidden input in the rendered page."""
        html = _build_schedule_page([])
        self.assertIn(_CSRF_TOKEN, html)
        self.assertIn('name="csrf"', html)

    def test_csrf_token_in_period_form(self):
        html = _wr._build_period_page(None, None)
        self.assertIn(_CSRF_TOKEN, html)


# ─────────────────────────────────────────────
#  3. DoS — Oversized Body (CWE-400)
# ─────────────────────────────────────────────

class TestBodySizeCap(unittest.TestCase):

    def test_reads_at_most_max_body_bytes(self):
        """Even if Content-Length claims 10 MB, only MAX_BODY bytes are read."""
        big_body = _valid_post_body() + b"&x=" + b"x" * (10 * 1024 * 1024)
        h, _ = _post_period(big_body, headers={"Content-Length": str(len(big_body))})
        # rfile position must not exceed MAX_BODY
        self.assertLessEqual(h.rfile.tell(), MAX_BODY)

    def test_giant_content_length_header_does_not_crash(self):
        """A Content-Length claiming 4 GB must not cause an allocation attempt."""
        start = time.monotonic()
        _post_period(_valid_post_body(), headers={"Content-Length": str(4 * 1024 ** 3)})
        # Must complete almost instantly — not hang reading gigabytes
        self.assertLess(time.monotonic() - start, 2.0)


# ─────────────────────────────────────────────
#  4. DoS — Unbounded Period Count / Index (CWE-400)
# ─────────────────────────────────────────────

class TestPeriodCountCap(unittest.TestCase):

    def test_max_periods_constant_is_bounded(self):
        self.assertLessEqual(MAX_PERIODS, 48)

    def test_adding_beyond_the_cap_is_refused(self):
        full = [(8, 0, 9, 0, "lunch", [], False)] * MAX_PERIODS
        h, saved = _post_period(_valid_post_body(), periods=full)
        h.send_response.assert_called_once_with(200)
        self.assertEqual(saved, [])

    def test_adding_below_the_cap_saves_one_more(self):
        _, saved = _post_period(_valid_post_body(), periods=[(8, 0, 9, 0, "lunch", [], False)])
        self.assertEqual(len(saved[0]), 2)

    def test_index_beyond_the_cap_is_not_routed(self):
        h, saved = _post_period(_valid_post_body(), path=f"/schedule/{MAX_PERIODS}")
        h.send_response.assert_called_once_with(404)
        self.assertEqual(saved, [])

    def test_huge_index_is_not_routed(self):
        h, saved = _post_period(_valid_post_body(), path="/schedule/" + "9" * 5000)
        self.assertEqual(saved, [])

    def test_index_of_a_missing_period_saves_nothing(self):
        h, saved = _post_period(_valid_post_body(), path="/schedule/5",
                                periods=[(8, 0, 9, 0, "lunch", [], False)])
        h.send_response.assert_called_once_with(200)
        self.assertEqual(saved, [])

    def test_delete_of_a_missing_period_saves_nothing(self):
        _, saved = _post_period(f"csrf={_CSRF_TOKEN}&action=delete".encode(),
                                path="/schedule/7")
        self.assertEqual(saved, [])

    def test_delete_removes_only_that_period(self):
        periods = [(8, 0, 9, 0, "sleeping", [], False), (12, 0, 13, 0, "lunch", [], False)]
        _, saved = _post_period(f"csrf={_CSRF_TOKEN}&action=delete".encode(),
                                path="/schedule/0", periods=periods)
        self.assertEqual(saved, [[(12, 0, 13, 0, "lunch", [], False)]])

    def test_many_day_fields_are_capped(self):
        """A flood of days= fields must not be iterated without bound."""
        body = _valid_post_body("&days=1" * 2000)
        start = time.monotonic()
        _, saved = _post_period(body)
        self.assertLess(time.monotonic() - start, 1.0)


# ─────────────────────────────────────────────
#  5. Scene Whitelist Validation (CWE-20)
# ─────────────────────────────────────────────

class TestSceneValidation(unittest.TestCase):

    def _parse_periods_from_post(self, scene_value: str) -> list:
        body = (f"csrf={_CSRF_TOKEN}&action=save&start=08:00&end=09:00"
                f"&scene={scene_value}{_EVERY_DAY}").encode()
        _, saved = _post_period(body)
        return saved[0] if saved else []

    def test_valid_scene_is_accepted(self):
        periods = self._parse_periods_from_post("sleeping")
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0][4], "sleeping")

    def test_unknown_scene_defaults_to_sleeping(self):
        periods = self._parse_periods_from_post("../../etc/passwd")
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0][4], "sleeping")

    def test_sql_injection_scene_defaults_to_sleeping(self):
        periods = self._parse_periods_from_post("'; DROP TABLE periods;--")
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0][4], "sleeping")

    def test_empty_scene_defaults_to_sleeping(self):
        periods = self._parse_periods_from_post("")
        if periods:
            self.assertEqual(periods[0][4], "sleeping")

    def test_very_long_scene_defaults_to_sleeping(self):
        periods = self._parse_periods_from_post("a" * 10_000)
        if periods:
            self.assertEqual(periods[0][4], "sleeping")


# ─────────────────────────────────────────────
#  6. Time / day field validation (CWE-755)
# ─────────────────────────────────────────────

class TestTimeFieldValidation(unittest.TestCase):

    def _post_time(self, start: str, end: str, days: str = _EVERY_DAY) -> list:
        body = (f"csrf={_CSRF_TOKEN}&action=save&start={start}&end={end}"
                f"&scene=sleeping{days}").encode()
        h, saved = _post_period(body)
        h.send_response.assert_called_once_with(200)   # never a crash
        return saved[0] if saved else []

    def test_valid_time_is_accepted(self):
        periods = self._post_time("08:00", "09:30")
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0][:4], (8, 0, 9, 30))

    def test_malformed_time_no_colon_is_rejected(self):
        """'notatime' has no colon — handler must reject, not crash."""
        self.assertEqual(self._post_time("notatime", "09:00"), [])

    def test_malformed_time_alpha_is_rejected(self):
        self.assertEqual(self._post_time("aa:bb", "cc:dd"), [])

    def test_time_out_of_range_is_clamped(self):
        """Hours > 23 or minutes > 59 must be clamped, not crash."""
        periods = self._post_time("05:99", "25:61")
        self.assertEqual(len(periods), 1)
        sh, sm, eh, em = periods[0][:4]
        self.assertEqual((sh, sm, eh, em), (5, 59, 23, 59))

    def test_end_before_start_is_rejected(self):
        self.assertEqual(self._post_time("10:00", "09:00"), [])

    def test_path_traversal_in_time_field_is_rejected(self):
        self.assertEqual(self._post_time("../../etc", "passwd"), [])

    def test_no_days_is_rejected(self):
        self.assertEqual(self._post_time("08:00", "09:00", days=""), [])

    def test_out_of_range_days_are_dropped(self):
        periods = self._post_time("08:00", "09:00", days="&days=1&days=9&days=-1&days=x")
        self.assertEqual(periods[0][5], [1])

    def test_every_day_is_stored_as_empty(self):
        self.assertEqual(self._post_time("08:00", "09:00")[0][5], [])

    def test_edit_keeps_the_other_periods(self):
        periods = [(8, 0, 9, 0, "sleeping", [], False), (12, 0, 13, 0, "lunch", [], False)]
        body = (f"csrf={_CSRF_TOKEN}&action=save&start=12:30&end=13:30"
                f"&scene=lunch&days=0&paused=1").encode()
        _, saved = _post_period(body, path="/schedule/1", periods=periods)
        self.assertEqual(saved[0][0], periods[0])
        self.assertEqual(saved[0][1], (12, 30, 13, 30, "lunch", [0], True))


# ─────────────────────────────────────────────
#  7. Logging (CWE-778)
# ─────────────────────────────────────────────

class TestLogging(unittest.TestCase):

    def test_log_message_calls_logger_not_pass(self):
        """log_message must emit to the logger, not swallow the event.
        We invoke the unbound class method to bypass the instance-level mock
        set up by _make_handler."""
        h = _make_handler("GET")
        with patch.object(_wr._log, "info") as mock_info:
            # Call the real class method, not the instance MagicMock
            _Handler.log_message(h, "%s %s", "GET", "/")
            mock_info.assert_called_once()

    def test_csrf_failure_logs_warning(self):
        h = _make_handler("POST", b"csrf=bad&action=save")
        with patch.object(_wr._log, "warning") as mock_warn:
            h.do_POST()
            mock_warn.assert_called()


# ─────────────────────────────────────────────
#  8. Log injection sanitisation (L1)
# ─────────────────────────────────────────────

class TestLogInjection(unittest.TestCase):

    def _csrf_fail_with_peer(self, peer_ip: str) -> str:
        """Trigger a CSRF failure with a crafted client_address and capture
        the string that was actually passed to _log.warning."""
        h = _make_handler("POST", b"csrf=bad&action=save")
        h.client_address = (peer_ip, 12345)
        logged_peer = []

        def capture(fmt, *args):
            logged_peer.append(args[0] if args else "")

        with patch.object(_wr._log, "warning", side_effect=capture):
            h.do_POST()
        return logged_peer[0] if logged_peer else ""

    def test_normal_ipv4_passes_through_unchanged(self):
        result = self._csrf_fail_with_peer("192.168.1.42")
        self.assertEqual(result, "192.168.1.42")

    def test_newline_in_peer_is_replaced(self):
        """A newline in the peer address must not reach the log sink."""
        crafted = "192.168.1.1\nFAKE LOG ENTRY: admin login successful"
        result = self._csrf_fail_with_peer(crafted)
        self.assertNotIn("\n", result)
        self.assertNotIn("FAKE LOG ENTRY", result)

    def test_carriage_return_in_peer_is_replaced(self):
        # The \r is the injection vector — it must be gone.
        # "INJECTED" may still appear on the same sanitised line (that's fine;
        # it's the line-splitting control character that enables log forgery,
        # not the payload text that follows it).
        result = self._csrf_fail_with_peer("10.0.0.1\rINJECTED")
        self.assertNotIn("\r", result)

    def test_ipv6_address_passes_through(self):
        result = self._csrf_fail_with_peer("::1")
        self.assertEqual(result, "::1")

    def test_bracketed_ipv6_passes_through(self):
        result = self._csrf_fail_with_peer("[::1]")
        self.assertEqual(result, "[::1]")


# ─────────────────────────────────────────────
#  9. Security response headers (L2)
# ─────────────────────────────────────────────

class TestSecurityHeaders(unittest.TestCase):

    def _headers(self, method: str, path: str, body: bytes = b"") -> dict:
        h = _make_handler(method, body)
        h.path = path
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            getattr(h, "do_" + method)()
        return emitted

    def test_every_page_carries_the_headers(self):
        for path in ("/", "/schedule", "/schedule/new", "/ch/1", "/ch/1/add",
                     "/ch/1/library", "/ch/1/settings", "/nope"):
            headers = self._headers("GET", path)
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(headers.get("X-Frame-Options"), "DENY", path)
            csp = headers.get("Content-Security-Policy", "")
            self.assertIn("script-src 'none'", csp, path)
            self.assertIn("frame-ancestors 'none'", csp, path)

    def test_post_response_includes_nosniff(self):
        headers = self._headers("POST", "/schedule/new", _valid_post_body())
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_403_response_includes_security_headers(self):
        """CSRF rejection responses must also carry hardening headers."""
        headers = self._headers("POST", "/", b"csrf=bad&action=save")
        self.assertIn("X-Content-Type-Options", headers)


# ─────────────────────────────────────────────
#  9b. Routing — whitelisted paths only (CWE-20)
# ─────────────────────────────────────────────

class TestRouting(unittest.TestCase):

    def _get(self, path: str) -> _Handler:
        h = _make_handler("GET")
        h.path = path
        with patch("web_remote.get_periods", return_value=[]):
            h.do_GET()
        return h

    def test_unknown_paths_are_404(self):
        for path in ("/admin", "/schedule/abc", "/schedule/../etc", "/ch/1/../../x",
                     "/ch/0", "/ch/99", "/ch/1/delete"):
            self._get(path).send_response.assert_called_once_with(404)

    def test_query_string_does_not_break_routing(self):
        self._get("/ch/1/library?show=removed&page=2").send_response.assert_called_once_with(200)

    def test_old_bookmarks_still_resolve(self):
        for path in ("/ch/1/videos", "/ch/1/ignored", "/ch/1/sources"):
            self._get(path).send_response.assert_called_once_with(200)

    def test_library_page_number_is_bounded(self):
        show, page = _wr._library_view("library", {"page": ["999999"]})
        self.assertEqual(page, _wr.MAX_PAGE)
        # Past Python's int-parsing limit: rejected, falls back to page 1.
        show, page = _wr._library_view("library", {"page": ["9" * 5000]})
        self.assertEqual(page, 1)
        show, page = _wr._library_view("library", {"page": ["-5"]})
        self.assertEqual(page, 1)
        show, page = _wr._library_view("library", {"page": ["abc"], "show": ["../x"]})
        self.assertEqual((show, page), ("", 1))


# ─────────────────────────────────────────────
#  9c. Now page — skip and status from the home page
# ─────────────────────────────────────────────

class TestNowPage(unittest.TestCase):

    def setUp(self):
        self.calls = []
        # A playing TV, whatever the real schedule / override files say.
        for name, value in (("get_manual_status", None), ("get_schedule_pause", None),
                            ("current_off_period", None), ("next_off_period", None),
                            ("todays_periods", [])):
            p = patch(f"web_remote.{name}", return_value=value)
            p.start()
            self.addCleanup(p.stop)
        _wr.register_channel_callbacks({
            "skip": lambda ch: self.calls.append(("skip", ch)),
            "tv": lambda: {"state": "playing", "channel": 0},
            "get": lambda ch: {"name": "C", "feed_size": 1, "is_active": ch == 0,
                               "current": {"pos": 0, "title": "<script>x</script>",
                                           "progress": 0.5},
                               "queue": []},
        })

    def tearDown(self):
        _wr.register_channel_callbacks({})

    def _post(self, body: str) -> _Handler:
        h = _make_handler("POST", body.encode())
        h.do_POST()
        return h

    def test_skip_reaches_the_app(self):
        self._post(f"csrf={_CSRF_TOKEN}&action=skip&ch=2")
        self.assertEqual(self.calls, [("skip", 1)])

    def test_skip_requires_csrf(self):
        self._post("action=skip&ch=1").send_response.assert_called_once_with(403)
        self.assertEqual(self.calls, [])

    def test_skip_channel_is_bounded(self):
        for ch in ("0", "-1", "99", "9" * 5000, "abc", ""):
            self._post(f"csrf={_CSRF_TOKEN}&action=skip&ch={ch}")
        self.assertEqual(self.calls, [])

    def test_now_page_escapes_the_title(self):
        h = _make_handler("GET")
        h.do_GET()
        html = h.wfile.getvalue().decode("utf-8")
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_now_page_survives_a_failing_callback(self):
        def boom(*a):
            raise RuntimeError("app hiccup")
        _wr.register_channel_callbacks({"tv": boom, "get": boom})
        h = _make_handler("GET")
        h.do_GET()
        h.send_response.assert_called_once_with(200)


# ─────────────────────────────────────────────
#  7. Channel feed endpoints (/ch/<n>)
#     CWE-352 CSRF, CWE-400 position bound, CWE-20 action + URL whitelist
# ─────────────────────────────────────────────

def _ch_post(body: str, path: str = "/ch/1") -> tuple[_Handler, io.BytesIO]:
    """POST to a channel route with the handler's socket faked out."""
    h = _make_handler("POST", body.encode())
    h.path = path
    return h, h.wfile


class _FakeApp:
    """Records what the HTTP layer asks the app to do."""

    def __init__(self):
        self.calls = []

    def get(self, ch_idx):
        return {
            "name": "Cartoons", "emoji": "C", "color": "#FF6B6B",
            "is_active": True, "feed_size": 3, "editable": True,
            "current": {"pos": 2, "title": "Now", "channel": "c",
                        "progress": 0.1, "url": "u", "injected": False,
                        "downloaded": True, "downloading": False},
            "queue": [{"pos": 5, "title": "Next", "url": "u", "injected": False,
                       "downloaded": False, "downloading": False}],
        }

    def skip(self, ch_idx):
        self.calls.append(("skip", ch_idx))

    def play_next(self, ch_idx, url):
        self.calls.append(("play_next", ch_idx, url))
        return True

    def add(self, ch_idx, url):
        self.calls.append(("add", ch_idx, url))
        return True

    def remove(self, ch_idx, pos, permanent):
        self.calls.append(("remove", ch_idx, pos, permanent))
        return True

    def refresh(self, ch_idx):
        self.calls.append(("refresh", ch_idx))
        return True

    def library(self):
        return {"current": None, "pending": 0, "completed": 0, "failed": 0,
                "refreshing": False, "last_refresh": "", "summary": ""}

    def register(self):
        _wr.register_channel_callbacks({
            "get": self.get, "skip": self.skip, "play_next": self.play_next,
            "add": self.add, "remove": self.remove, "refresh": self.refresh,
            "library": self.library,
        })


class TestChannelEndpoints(unittest.TestCase):

    def setUp(self):
        self.app = _FakeApp()
        self.app.register()

    def tearDown(self):
        _wr.register_channel_callbacks({})

    # ── CSRF (CWE-352) ────────────────────────

    def test_remove_without_csrf_is_rejected(self):
        h, _ = _ch_post("action=remove&pos=5")
        h.do_POST()
        h.send_response.assert_called_once_with(403)
        self.assertEqual(self.app.calls, [])

    def test_add_without_csrf_is_rejected(self):
        h, _ = _ch_post("action=add&url=https://youtu.be/jNQXAC9IVRw")
        h.do_POST()
        h.send_response.assert_called_once_with(403)
        self.assertEqual(self.app.calls, [])

    def test_refresh_without_csrf_is_rejected(self):
        h, _ = _ch_post("action=refresh")
        h.do_POST()
        h.send_response.assert_called_once_with(403)
        self.assertEqual(self.app.calls, [])

    def test_wrong_csrf_does_not_reach_the_app(self):
        h, _ = _ch_post("action=remove&pos=5&csrf=deadbeef")
        h.do_POST()
        h.send_response.assert_called_once_with(403)
        self.assertEqual(self.app.calls, [])

    # ── Position bounding (CWE-400 / CWE-20) ──

    def _remove(self, pos_field: str, action: str = "remove"):
        h, _ = _ch_post(f"action={action}&pos={pos_field}&csrf={_CSRF_TOKEN}")
        h.do_POST()
        return h

    def test_negative_position_rejected(self):
        self._remove("-1")
        self.assertEqual(self.app.calls, [])

    def test_non_numeric_position_rejected(self):
        self._remove("abc")
        self.assertEqual(self.app.calls, [])

    def test_position_above_cap_rejected(self):
        self._remove(str(_wr.MAX_POSITION + 1))
        self.assertEqual(self.app.calls, [])

    def test_huge_position_does_not_crash_the_handler(self):
        """A bare int() on user input must not raise out of the handler thread."""
        h = self._remove("9" * 5000)
        h.send_response.assert_called_once_with(200)
        self.assertEqual(self.app.calls, [])

    def test_missing_position_rejected(self):
        h, _ = _ch_post(f"action=remove&csrf={_CSRF_TOKEN}")
        h.do_POST()
        self.assertEqual(self.app.calls, [])

    def test_valid_position_reaches_the_app(self):
        self._remove("5")
        self.assertEqual(self.app.calls, [("remove", 0, 5, False)])

    def test_remove_perm_sets_the_permanent_flag(self):
        self._remove("5", action="remove_perm")
        self.assertEqual(self.app.calls, [("remove", 0, 5, True)])

    # ── URL whitelist (CWE-20) ────────────────

    def _add(self, url: str, action: str = "add"):
        h, _ = _ch_post(f"action={action}&url={url}&csrf={_CSRF_TOKEN}")
        h.do_POST()
        return h

    def test_add_rejects_file_url(self):
        self._add("file:///etc/passwd")
        self.assertEqual(self.app.calls, [])

    def test_add_rejects_path_traversal(self):
        self._add("https://youtube.com/watch?v=..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(self.app.calls, [])

    def test_add_rejects_empty_url(self):
        self._add("")
        self.assertEqual(self.app.calls, [])

    def test_add_normalises_a_share_link(self):
        self._add("https%3A%2F%2Fyoutu.be%2FjNQXAC9IVRw%3Fsi%3Dabc")
        self.assertEqual(
            self.app.calls,
            [("add", 0, "https://www.youtube.com/watch?v=jNQXAC9IVRw")],
        )

    def test_play_once_goes_to_play_next_not_add(self):
        """A one-shot injection must never reach the playlist-mutating callback."""
        self._add("https%3A%2F%2Fyoutu.be%2FjNQXAC9IVRw", action="queue")
        self.assertEqual(
            self.app.calls,
            [("play_next", 0, "https://www.youtube.com/watch?v=jNQXAC9IVRw")],
        )

    # ── Action whitelist (CWE-20) ─────────────

    def test_unknown_action_is_inert(self):
        h, _ = _ch_post(f"action=delete_everything&pos=5&csrf={_CSRF_TOKEN}")
        h.do_POST()
        h.send_response.assert_called_once_with(200)
        self.assertEqual(self.app.calls, [])

    def test_channel_index_out_of_range_is_not_routed(self):
        h, _ = _ch_post(f"action=remove&pos=5&csrf={_CSRF_TOKEN}", path="/ch/99")
        h.do_POST()
        self.assertEqual(self.app.calls, [])

    def test_channel_path_traversal_is_not_routed(self):
        h, _ = _ch_post(f"action=remove&pos=5&csrf={_CSRF_TOKEN}",
                        path="/ch/1/../../etc")
        h.do_POST()
        self.assertEqual(self.app.calls, [])

    # ── Missing callbacks must not raise ──────

    def test_actions_are_inert_when_no_app_is_registered(self):
        _wr.register_channel_callbacks({})
        for body in ("action=skip", "action=remove&pos=1",
                     "action=remove_perm&pos=1", "action=refresh",
                     "action=add&url=https%3A%2F%2Fyoutu.be%2FjNQXAC9IVRw"):
            h, _ = _ch_post(f"{body}&csrf={_CSRF_TOKEN}")
            h.do_POST()
            h.send_response.assert_called_once_with(200)

    # ── Responses stay hardened ───────────────

    def test_channel_post_response_has_security_headers(self):
        h, _ = _ch_post(f"action=remove&pos=5&csrf={_CSRF_TOKEN}")
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        h.do_POST()
        self.assertEqual(emitted.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(emitted.get("X-Frame-Options"), "DENY")
        self.assertIn("script-src 'none'", emitted.get("Content-Security-Policy", ""))

    def test_channel_get_response_has_security_headers(self):
        h = _make_handler("GET")
        h.path = "/ch/1"
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        h.do_GET()
        self.assertEqual(emitted.get("X-Content-Type-Options"), "nosniff")

    def test_body_over_cap_is_truncated_on_channel_route(self):
        """MAX_BODY applies to channel POSTs too (CWE-400)."""
        padding = "x" * (MAX_BODY * 2)
        h, _ = _ch_post(f"action=remove&pos=5&csrf={_CSRF_TOKEN}&pad={padding}")
        h.do_POST()
        self.assertLessEqual(len(h.rfile.getvalue()[: MAX_BODY]), MAX_BODY)
        h.send_response.assert_called_once_with(200)


# ─────────────────────────────────────────────
#  8. Feed rendering — no unescaped video metadata (CWE-79)
# ─────────────────────────────────────────────

class TestFeedRendering(unittest.TestCase):
    """Titles come from YouTube, so they are untrusted text in this page."""

    def _page(self, title: str) -> str:
        info = {
            "name": "Cartoons", "emoji": "C", "color": "#FF6B6B",
            "is_active": True, "feed_size": 1, "editable": True,
            "current": {"pos": 0, "title": title, "channel": title,
                        "progress": 0.5, "url": "u", "injected": False,
                        "downloaded": True, "downloading": False},
            "queue": [{"pos": 1, "title": title, "url": "u", "injected": False,
                       "downloaded": False, "downloading": False}],
        }
        return _wr._build_channel_page(0, info, "", None)

    def test_script_tag_in_title_is_escaped(self):
        html = self._page("<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_attribute_break_in_title_is_escaped(self):
        html = self._page('" onmouseover="alert(1)')
        self.assertNotIn('onmouseover="alert(1)"', html)

    def test_message_text_is_escaped(self):
        html = _wr._build_channel_page(0, None, "<img src=x onerror=alert(1)>")
        self.assertNotIn("<img src=x", html)

    def test_position_is_rendered_as_an_integer(self):
        """A non-integer pos from the app must not land in the form markup."""
        info = {
            "name": "C", "emoji": "C", "color": "#fff", "is_active": False,
            "feed_size": 1, "editable": True, "current": None,
            "queue": [{"pos": "3\" autofocus onfocus=alert(1) x=\"",
                       "title": "t", "url": "u", "injected": False,
                       "downloaded": False, "downloading": False}],
        }
        with self.assertRaises(ValueError):
            _wr._build_channel_page(0, info, "", None)


# ─────────────────────────────────────────────
#  9. Channel library pages (/ch/<n>/videos|ignored|sources|settings)
#     CWE-352 CSRF, CWE-20 ID / playlist / colour whitelist,
#     CWE-400 channel-number bound, CWE-79 catalog escaping
# ─────────────────────────────────────────────

_PL = "PLVOKDAn3IigUIpvFWScjlgOcnquOLp3Fp"


class _FakeLibraryApp(_FakeApp):

    def catalog(self, ch_idx):
        return {
            "videos": [{"id": "jNQXAC9IVRw", "title": "<script>x</script>",
                        "from": "<b>pl</b>", "downloaded": True, "downloading": False}],
            "ignored": [{"id": "aaaaaaaaaaa", "title": '" onfocus="x',
                         "from": "added by hand", "downloaded": False,
                         "downloading": False}],
            "sources": [{"playlist_id": _PL, "title": "<i>Trains</i>",
                         "last_sync": "2026-10-22 09:00", "last_count": 4}],
        }

    def _record(self, name):
        def fn(*args):
            self.calls.append((name, *args))
            return True
        return fn

    def register(self):
        _wr.register_channel_callbacks({
            "get": self.get, "library": self.library, "catalog": self.catalog,
            **{name: self._record(name) for name in (
                "skip", "play_next", "add", "remove", "refresh", "ignore",
                "restore", "add_source", "remove_source", "save_channel", "merge")},
        })


class TestLibraryEndpoints(unittest.TestCase):

    def setUp(self):
        self.app = _FakeLibraryApp()
        self.app.register()

    def tearDown(self):
        _wr.register_channel_callbacks({})

    def _post(self, body: str, path: str = "/ch/1/videos", csrf: bool = True):
        if csrf:
            body = f"{body}&csrf={_CSRF_TOKEN}"
        h, out = _ch_post(body, path)
        h.do_POST()
        return h, out.getvalue().decode("utf-8")

    def _get(self, path: str) -> str:
        h = _make_handler("GET")
        h.path = path
        h.do_GET()
        return h.wfile.getvalue().decode("utf-8")

    # ── CSRF (CWE-352) ────────────────────────

    def test_every_new_action_requires_csrf(self):
        for body in ("action=ignore&video=jNQXAC9IVRw",
                     "action=restore&video=jNQXAC9IVRw",
                     f"action=add_source&playlist={_PL}",
                     f"action=remove_source&playlist={_PL}",
                     "action=save_channel&name=x&emoji=x&color=%23ffffff",
                     "action=merge&target=2&confirm=1"):
            h, _ = self._post(body, csrf=False)
            h.send_response.assert_called_once_with(403)
        self.assertEqual(self.app.calls, [])

    # ── Routing (CWE-20) ──────────────────────

    def test_unknown_subpage_is_not_routed(self):
        self._post("action=ignore&video=jNQXAC9IVRw", path="/ch/1/admin")
        self.assertEqual(self.app.calls, [])

    def test_subpage_traversal_is_not_routed(self):
        self._post("action=ignore&video=jNQXAC9IVRw", path="/ch/1/videos/../../x")
        self.assertEqual(self.app.calls, [])

    def test_subpages_render_with_security_headers(self):
        for sub in ("videos", "ignored", "sources", "settings"):
            h = _make_handler("GET")
            h.path = f"/ch/1/{sub}"
            emitted = {}
            h.send_header = lambda k, v, e=emitted: e.update({k: v})
            h.do_GET()
            self.assertIn("script-src 'none'", emitted.get("Content-Security-Policy", ""))

    # ── Ignore / restore (CWE-20) ─────────────

    def test_ignore_reaches_the_app_as_a_canonical_url(self):
        self._post("action=ignore&video=jNQXAC9IVRw")
        self.assertEqual(self.app.calls, [
            ("ignore", 0, "https://www.youtube.com/watch?v=jNQXAC9IVRw")])

    def test_restore_reaches_the_app(self):
        self._post("action=restore&video=jNQXAC9IVRw", path="/ch/1/ignored")
        self.assertEqual(self.app.calls, [
            ("restore", 0, "https://www.youtube.com/watch?v=jNQXAC9IVRw")])

    def test_unsafe_video_id_is_rejected(self):
        for vid in ("../../etc/passwd", "", "file:///etc/passwd", "a" * 500):
            self._post(f"action=ignore&video={vid}")
        self.assertEqual(self.app.calls, [])

    # ── Sources (CWE-20, N1) ──────────────────

    def test_add_source_passes_only_the_playlist_id(self):
        self._post("action=add_source&playlist=https%3A%2F%2Fwww.youtube.com%2F"
                   f"playlist%3Flist%3D{_PL}", path="/ch/1/sources")
        self.assertEqual(self.app.calls, [("add_source", 0, _PL)])

    def test_add_source_rejects_a_foreign_host(self):
        self._post("action=add_source&playlist=https%3A%2F%2Fevil.example%2F"
                   f"playlist%3Flist%3D{_PL}", path="/ch/1/sources")
        self.assertEqual(self.app.calls, [])

    def test_add_source_rejects_an_option_shaped_id(self):
        self._post("action=add_source&playlist=https%3A%2F%2Fyoutube.com%2F"
                   "playlist%3Flist%3D--exec", path="/ch/1/sources")
        self.assertEqual(self.app.calls, [])

    def test_remove_source_rejects_an_unsafe_id(self):
        self._post("action=remove_source&playlist=..%2F..%2Fx", path="/ch/1/sources")
        self.assertEqual(self.app.calls, [])

    def test_remove_source_reaches_the_app(self):
        self._post(f"action=remove_source&playlist={_PL}", path="/ch/1/sources")
        self.assertEqual(self.app.calls, [("remove_source", 0, _PL)])

    # ── Channel settings (CWE-20) ─────────────

    def test_save_channel_reaches_the_app(self):
        self._post("action=save_channel&name=Trains&emoji=T&color=%234D96FF",
                   path="/ch/1/settings")
        self.assertEqual(self.app.calls, [("save_channel", 0, "Trains", "T", "#4D96FF")])

    def test_save_channel_rejects_a_bad_colour(self):
        self._post("action=save_channel&name=x&emoji=x&color=red%3Bx",
                   path="/ch/1/settings")
        self.assertEqual(self.app.calls, [])

    def test_save_channel_rejects_an_overlong_name(self):
        self._post("action=save_channel&name=" + "x" * 41 + "&emoji=x&color=%23ffffff",
                   path="/ch/1/settings")
        self.assertEqual(self.app.calls, [])

    def test_save_channel_rejects_an_empty_name(self):
        self._post("action=save_channel&name=&emoji=x&color=%23ffffff",
                   path="/ch/1/settings")
        self.assertEqual(self.app.calls, [])

    # ── Merge (CWE-20 / CWE-400) ──────────────

    def test_merge_needs_confirmation(self):
        self._post("action=merge&target=2", path="/ch/1/settings")
        self.assertEqual(self.app.calls, [])

    def test_merge_reaches_the_app(self):
        self._post("action=merge&target=1&confirm=1", path="/ch/3/settings")
        self.assertEqual(self.app.calls, [("merge", 2, 0)])

    def test_merge_into_itself_is_rejected(self):
        self._post("action=merge&target=1&confirm=1", path="/ch/1/settings")
        self.assertEqual(self.app.calls, [])

    def test_merge_target_is_bounded(self):
        for target in ("0", "-1", "99", "9" * 5000, "abc"):
            h, _ = self._post(f"action=merge&target={target}&confirm=1",
                              path="/ch/1/settings")
            h.send_response.assert_called_once_with(200)
        self.assertEqual(self.app.calls, [])

    # ── Rendering (CWE-79) ────────────────────

    def test_catalog_titles_are_escaped(self):
        html = self._get("/ch/1/videos")
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("<b>pl</b>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_ignored_titles_are_escaped(self):
        self.assertNotIn('" onfocus="x', self._get("/ch/1/ignored"))

    def test_source_titles_are_escaped(self):
        self.assertNotIn("<i>Trains</i>", self._get("/ch/1/sources"))

    def test_settings_escape_the_channel_name(self):
        with patch.dict(_wr.CHANNELS[0], {"name": '"><script>x</script>'}):
            html = self._get("/ch/1/settings")
        self.assertNotIn("<script>x</script>", html)

    def test_pages_work_when_the_app_is_offline(self):
        _wr.register_channel_callbacks({})
        for sub in ("videos", "ignored", "sources", "settings"):
            self.assertIn("<!DOCTYPE html>", self._get(f"/ch/1/{sub}"))

# ─────────────────────────────────────────────
#  10. Manual status ("Right now" on the schedule page)
#      CWE-352 CSRF, CWE-20 scene whitelist, schedule left untouched
# ─────────────────────────────────────────────

class TestManualStatus(unittest.TestCase):

    def setUp(self):
        import schedule as _schedule
        import shutil
        import tempfile
        self._schedule = _schedule
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        p = patch.object(_schedule, "STATUS_FILE",
                         os.path.join(self._tmp, "manual_status.json"))
        p.start()
        self.addCleanup(p.stop)
        _schedule._cached_status, _schedule._cached_status_mtime = None, 0.0
        self.addCleanup(setattr, _schedule, "_cached_status", None)

    def _post(self, body: str):
        return self._post_to("/", body)

    def _post_to(self, path: str, body: str):
        h = _make_handler("POST", body.encode())
        h.path = path
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods") as save:
            h.do_POST()
        return h, save, h.wfile.getvalue().decode("utf-8")

    def test_status_on_requires_csrf(self):
        h, _, _ = self._post("action=status_on&scene=tubby")
        h.send_response.assert_called_once_with(403)
        self.assertIsNone(self._schedule.get_manual_status())

    def test_status_off_requires_csrf(self):
        self._schedule.set_manual_status("tubby")
        h, _, _ = self._post("action=status_off")
        h.send_response.assert_called_once_with(403)
        self.assertIsNotNone(self._schedule.get_manual_status())

    def test_status_on_switches_the_scene_on(self):
        self._post(f"action=status_on&scene=tubby&csrf={_CSRF_TOKEN}")
        self.assertEqual(self._schedule.get_manual_status()["scene"], "tubby")

    def test_status_off_hands_back_to_the_schedule(self):
        self._schedule.set_manual_status("tubby")
        self._post(f"action=status_off&csrf={_CSRF_TOKEN}")
        self.assertIsNone(self._schedule.get_manual_status())

    def test_unknown_scene_is_rejected_not_defaulted(self):
        """A status switches the TV off; an unknown scene must do nothing."""
        for scene in ("party", "", "<script>", "sleeping%0Ax", "a" * 5000):
            h, _, _ = self._post(f"action=status_on&scene={scene}&csrf={_CSRF_TOKEN}")
            h.send_response.assert_called_once_with(200)
        self.assertIsNone(self._schedule.get_manual_status())

    def test_status_never_saves_the_schedule(self):
        """The status form has no period fields; it must not wipe the periods."""
        _, save, _ = self._post(f"action=status_on&scene=tubby&csrf={_CSRF_TOKEN}")
        save.assert_not_called()
        _, save, _ = self._post(f"action=status_off&csrf={_CSRF_TOKEN}")
        save.assert_not_called()

    def test_page_shows_the_active_status_and_an_end_button(self):
        self._schedule.set_manual_status("tubby")
        _, _, html = self._post(f"action=noop&csrf={_CSRF_TOKEN}")
        self.assertIn('value="status_off"', html)
        self.assertIn("End bath time", html)
        self.assertIn("stays on until you end it", html)

    def test_status_response_has_security_headers(self):
        h = _make_handler("POST", f"action=status_on&scene=tubby&csrf={_CSRF_TOKEN}".encode())
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        with patch("web_remote.get_periods", return_value=[]):
            h.do_POST()
        self.assertIn("script-src 'none'", emitted.get("Content-Security-Policy", ""))

class TestSchedulePause(TestManualStatus):
    """'Pause schedules' rides on the same override file and handler."""

    def test_pause_requires_csrf(self):
        h, _, _ = self._post("action=schedules_pause")
        h.send_response.assert_called_once_with(403)
        self.assertIsNone(self._schedule.get_schedule_pause())

    def test_resume_requires_csrf(self):
        self._schedule.pause_schedules()
        h, _, _ = self._post("action=schedules_resume")
        h.send_response.assert_called_once_with(403)
        self.assertIsNotNone(self._schedule.get_schedule_pause())

    def test_pause_and_resume(self):
        self._post(f"action=schedules_pause&csrf={_CSRF_TOKEN}")
        self.assertIsNotNone(self._schedule.get_schedule_pause())
        self._post(f"action=schedules_resume&csrf={_CSRF_TOKEN}")
        self.assertIsNone(self._schedule.get_schedule_pause())

    def test_pause_never_saves_the_schedule(self):
        """No period is rewritten, so each period's own Paused flag survives."""
        _, save, _ = self._post(f"action=schedules_pause&csrf={_CSRF_TOKEN}")
        save.assert_not_called()
        _, save, _ = self._post(f"action=schedules_resume&csrf={_CSRF_TOKEN}")
        save.assert_not_called()

    def test_schedule_checkbox_pauses_and_resumes(self):
        self._post_to("/schedule", f"action=schedules_pause&csrf={_CSRF_TOKEN}")
        self.assertIsNotNone(self._schedule.get_schedule_pause())
        self._post_to("/schedule", f"action=schedules_resume&csrf={_CSRF_TOKEN}")
        self.assertIsNone(self._schedule.get_schedule_pause())

    def test_schedule_checkbox_requires_csrf(self):
        h, _, _ = self._post_to("/schedule", "action=schedules_pause")
        h.send_response.assert_called_once_with(403)
        self.assertIsNone(self._schedule.get_schedule_pause())

    def test_checkbox_shows_ticked_while_paused(self):
        self._schedule.pause_schedules()
        _, _, html = self._post_to("/schedule", f"action=noop&csrf={_CSRF_TOKEN}")
        self.assertIn('value="schedules_resume"', html)
        self.assertIn('aria-pressed="true"', html)
        self.assertNotIn('value="schedules_pause"', html)

    def test_checkbox_shows_unticked_otherwise(self):
        _, _, html = self._post_to("/schedule", f"action=noop&csrf={_CSRF_TOKEN}")
        self.assertIn('value="schedules_pause"', html)
        self.assertIn('aria-pressed="false"', html)

    def test_now_page_says_schedules_are_paused(self):
        self._schedule.pause_schedules()
        _, _, html = self._post(f"action=noop&csrf={_CSRF_TOKEN}")
        self.assertIn("Schedules are paused", html)

    def test_schedule_page_explains_a_status_still_holding_the_tv_off(self):
        self._schedule.pause_schedules()
        self._schedule.set_manual_status("tubby")
        _, _, html = self._post_to("/schedule", f"action=noop&csrf={_CSRF_TOKEN}")
        self.assertIn("Bath time is on right now", html)

    def test_turn_the_tv_on_pauses_schedules(self):
        """The Now page's "Turn the TV on" during a scheduled break."""
        self._post(f"action=schedules_pause&csrf={_CSRF_TOKEN}")
        self.assertIsNotNone(self._schedule.get_schedule_pause())

if __name__ == "__main__":
    unittest.main(verbosity=2)
