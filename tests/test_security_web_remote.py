"""
Security regression tests — web_remote.py
Covers: CWE-352 (CSRF), CWE-400 (DoS body/count/position),
        CWE-20 (scene + action + URL validation), CWE-755 (time parsing),
        CWE-778 (logging), CWE-79 (video metadata in the feed page),
        channel library pages (ignore / restore / sources / settings / merge).

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
    _Handler, _CSRF_TOKEN, MAX_BODY, MAX_PERIODS, _build_page,
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


def _valid_post_body(extra: str = "") -> bytes:
    """Minimal valid POST body: correct CSRF, zero periods."""
    return f"count=0&csrf={_CSRF_TOKEN}&action=save{extra}".encode()


# ─────────────────────────────────────────────
#  1. CSRF Protection (CWE-352)
# ─────────────────────────────────────────────

class TestCSRF(unittest.TestCase):

    def _post(self, body: bytes) -> _Handler:
        h = _make_handler("POST", body)
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            h.do_POST()
        return h

    def test_missing_csrf_token_returns_403(self):
        body = b"count=0&action=save"   # no csrf field
        h = self._post(body)
        h.send_response.assert_called_once_with(403)

    def test_wrong_csrf_token_returns_403(self):
        body = b"count=0&csrf=deadbeefdeadbeef&action=save"
        h = self._post(body)
        h.send_response.assert_called_once_with(403)

    def test_correct_csrf_token_returns_200(self):
        h = self._post(_valid_post_body())
        h.send_response.assert_called_once_with(200)

    def test_empty_csrf_token_returns_403(self):
        body = b"count=0&csrf=&action=save"
        h = self._post(body)
        h.send_response.assert_called_once_with(403)

    def test_csrf_token_in_page_html(self):
        """The CSRF token must appear as a hidden input in the rendered page."""
        html = _build_page([])
        self.assertIn(_CSRF_TOKEN, html)
        self.assertIn('name="csrf"', html)


# ─────────────────────────────────────────────
#  3. DoS — Oversized Body (CWE-400)
# ─────────────────────────────────────────────

class TestBodySizeCap(unittest.TestCase):

    def test_reads_at_most_max_body_bytes(self):
        """Even if Content-Length claims 10 MB, only MAX_BODY bytes are read."""
        big_body = b"count=0&csrf=" + _CSRF_TOKEN.encode() + b"&action=save" + b"x" * (10 * 1024 * 1024)
        headers = {"Content-Length": str(len(big_body))}
        h = _make_handler("POST", big_body, headers=headers)
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            h.do_POST()
        # rfile position must not exceed MAX_BODY
        self.assertLessEqual(h.rfile.tell(), MAX_BODY)

    def test_giant_content_length_header_does_not_crash(self):
        """A Content-Length claiming 4 GB must not cause an allocation attempt."""
        body = _valid_post_body()
        headers = {"Content-Length": str(4 * 1024 ** 3)}
        h = _make_handler("POST", body, headers=headers)
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            start = time.monotonic()
            h.do_POST()
            elapsed = time.monotonic() - start
        # Must complete almost instantly — not hang reading gigabytes
        self.assertLess(elapsed, 2.0)


# ─────────────────────────────────────────────
#  4. DoS — Unbounded Period Count (CWE-400)
# ─────────────────────────────────────────────

class TestPeriodCountCap(unittest.TestCase):

    def _post_with_count(self, count: int) -> float:
        body = f"count={count}&csrf={_CSRF_TOKEN}&action=save".encode()
        h = _make_handler("POST", body)
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            start = time.monotonic()
            h.do_POST()
            return time.monotonic() - start

    def test_max_periods_constant_is_bounded(self):
        self.assertLessEqual(MAX_PERIODS, 48)

    def test_count_of_999999_completes_quickly(self):
        elapsed = self._post_with_count(999_999)
        self.assertLess(elapsed, 1.0, "count DoS: handler took too long")

    def test_count_exceeding_max_is_clamped(self):
        """Handler must not create more than MAX_PERIODS parsed periods
        even when count claims a higher number with real field data."""
        # Provide MAX_PERIODS + 10 fully filled period fields
        n = MAX_PERIODS + 10
        fields = "".join(
            f"&start_{i}=08:00&end_{i}=09:00&scene_{i}=lunch"
            for i in range(n)
        )
        body = f"count={n}&csrf={_CSRF_TOKEN}&action=save{fields}".encode()
        h = _make_handler("POST", body)
        saved_calls = []
        with patch("web_remote.save_periods", side_effect=saved_calls.append):
            h.do_POST()
        if saved_calls:
            self.assertLessEqual(len(saved_calls[0]), MAX_PERIODS)


# ─────────────────────────────────────────────
#  5. Scene Whitelist Validation (CWE-20)
# ─────────────────────────────────────────────

class TestSceneValidation(unittest.TestCase):

    def _parse_periods_from_post(self, scene_value: str) -> list:
        body = (
            f"count=1&csrf={_CSRF_TOKEN}&action=save"
            f"&start_0=08:00&end_0=09:00&scene_0={scene_value}"
        ).encode()
        h = _make_handler("POST", body)
        captured = []
        with patch("web_remote.save_periods", side_effect=captured.append):
            h.do_POST()
        return captured[0] if captured else []

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
#  6. Time Field Validation (CWE-755)
# ─────────────────────────────────────────────

class TestTimeFieldValidation(unittest.TestCase):

    def _post_time(self, start: str, end: str) -> list:
        body = (
            f"count=1&csrf={_CSRF_TOKEN}&action=save"
            f"&start_0={start}&end_0={end}&scene_0=sleeping"
        ).encode()
        h = _make_handler("POST", body)
        captured = []
        with patch("web_remote.save_periods", side_effect=captured.append):
            h.do_POST()
        return captured[0] if captured else []

    def test_valid_time_is_accepted(self):
        periods = self._post_time("08:00", "09:30")
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0][:4], (8, 0, 9, 30))

    def test_malformed_time_no_colon_skips_period(self):
        """'notatime' has no colon — handler must skip, not crash."""
        periods = self._post_time("notatime", "09:00")
        self.assertEqual(len(periods), 0)

    def test_malformed_time_alpha_skips_period(self):
        periods = self._post_time("aa:bb", "cc:dd")
        self.assertEqual(len(periods), 0)

    def test_time_out_of_range_is_clamped(self):
        """Hours > 23 or minutes > 59 must be clamped, not crash."""
        periods = self._post_time("99:99", "25:61")
        self.assertEqual(len(periods), 1)
        sh, sm, eh, em = periods[0][:4]
        self.assertLessEqual(sh, 23)
        self.assertLessEqual(sm, 59)
        self.assertLessEqual(eh, 23)
        self.assertLessEqual(em, 59)

    def test_path_traversal_in_time_field_skips_period(self):
        periods = self._post_time("../../etc", "passwd")
        self.assertEqual(len(periods), 0)

    def test_mixed_valid_and_invalid_periods(self):
        """Valid periods survive; malformed ones are dropped silently."""
        body = (
            f"count=2&csrf={_CSRF_TOKEN}&action=save"
            f"&start_0=08:00&end_0=09:00&scene_0=sleeping"
            f"&start_1=INVALID&end_1=INVALID&scene_1=sleeping"
        ).encode()
        h = _make_handler("POST", body)
        captured = []
        with patch("web_remote.save_periods", side_effect=captured.append):
            h.do_POST()
        self.assertEqual(len(captured[0]), 1)
        self.assertEqual(captured[0][0][:4], (8, 0, 9, 0))


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
        body = b"count=0&csrf=bad&action=save"
        h = _make_handler("POST", body)
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
        body = b"count=0&csrf=bad&action=save"
        h = _make_handler("POST", body)
        h.client_address = (peer_ip, 12345)
        logged_peer = []
        real_warning = _wr._log.warning

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

    def _get_headers(self, handler_method="do_GET",
                     body=b"", extra_headers=None) -> dict:
        """Run a handler method and collect all send_header calls."""
        h = _make_handler("GET" if handler_method == "do_GET" else "POST",
                          body, headers=extra_headers)
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            getattr(h, handler_method)()
        return emitted

    def test_get_response_includes_nosniff(self):
        headers = self._get_headers("do_GET")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_get_response_denies_framing(self):
        headers = self._get_headers("do_GET")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

    def test_get_response_includes_csp(self):
        headers = self._get_headers("do_GET")
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_post_response_includes_nosniff(self):
        body = _valid_post_body()
        headers = self._get_headers("do_POST", body=body)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_403_response_includes_security_headers(self):
        """CSRF rejection responses must also carry hardening headers."""
        body = b"count=0&csrf=bad&action=save"
        h = _make_handler("POST", body)
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        h.do_POST()
        self.assertIn("X-Content-Type-Options", emitted)




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

if __name__ == "__main__":
    unittest.main(verbosity=2)
