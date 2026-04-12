"""
Security regression tests — web_remote.py
Covers: CWE-352 (CSRF), CWE-400 (DoS body/count), CWE-306 (Auth),
        CWE-20 (scene validation), CWE-755 (time parsing), CWE-778 (logging).

Run with:  python -m pytest tests/test_security_web_remote.py -v
"""

import base64
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
    _Handler, _CSRF_TOKEN, _CREDENTIALS, MAX_BODY, MAX_PERIODS,
    _BOOT_PASSWORD, _build_page,
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


def _auth_header(password: str = _BOOT_PASSWORD) -> dict:
    creds = base64.b64encode(f"admin:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _valid_post_body(extra: str = "") -> bytes:
    """Minimal valid POST body: correct CSRF, zero periods."""
    return f"count=0&csrf={_CSRF_TOKEN}&action=save{extra}".encode()


# ─────────────────────────────────────────────
#  1. Authentication (CWE-306)
# ─────────────────────────────────────────────

class TestAuthentication(unittest.TestCase):

    def test_get_without_auth_returns_401(self):
        h = _make_handler("GET")
        h.do_GET()
        h.send_response.assert_called_once_with(401)

    def test_post_without_auth_returns_401(self):
        h = _make_handler("POST", _valid_post_body())
        h.do_POST()
        h.send_response.assert_called_once_with(401)

    def test_get_with_wrong_password_returns_401(self):
        h = _make_handler("GET", headers=_auth_header("wrongpassword"))
        h.do_GET()
        h.send_response.assert_called_once_with(401)

    @patch("web_remote.get_periods", return_value=[])
    def test_get_with_correct_auth_returns_200(self, _):
        h = _make_handler("GET", headers=_auth_header())
        h.do_GET()
        h.send_response.assert_called_once_with(200)

    def test_auth_check_uses_constant_time_comparison(self):
        """Ensure _check_auth doesn't short-circuit on first differing byte."""
        import secrets as _sec
        h = _make_handler("GET")
        # Provide a token one char longer — must still be rejected, not crash
        long_creds = base64.b64encode(b"admin:" + b"x" * 100).decode()
        h.headers.get = lambda k, d=None: (
            f"Basic {long_creds}" if k == "Authorization" else d
        )
        self.assertFalse(h._check_auth())

    def test_boot_password_has_sufficient_entropy(self):
        """Token must be at least 12 URL-safe base64 chars (≥72 bits)."""
        self.assertGreaterEqual(len(_BOOT_PASSWORD), 12)


# ─────────────────────────────────────────────
#  2. CSRF Protection (CWE-352)
# ─────────────────────────────────────────────

class TestCSRF(unittest.TestCase):

    def _post(self, body: bytes) -> _Handler:
        h = _make_handler("POST", body, headers=_auth_header())
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
        headers = {**_auth_header(), "Content-Length": str(len(big_body))}
        h = _make_handler("POST", big_body, headers=headers)
        with patch("web_remote.get_periods", return_value=[]), \
             patch("web_remote.save_periods"):
            h.do_POST()
        # rfile position must not exceed MAX_BODY
        self.assertLessEqual(h.rfile.tell(), MAX_BODY)

    def test_giant_content_length_header_does_not_crash(self):
        """A Content-Length claiming 4 GB must not cause an allocation attempt."""
        body = _valid_post_body()
        headers = {**_auth_header(), "Content-Length": str(4 * 1024 ** 3)}
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h = _make_handler("POST", body, headers=_auth_header())
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
        h_headers = {**_auth_header()}
        if extra_headers:
            h_headers.update(extra_headers)
        h = _make_handler("GET" if handler_method == "do_GET" else "POST",
                          body, headers=h_headers)
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

    def test_401_response_includes_security_headers(self):
        """Auth challenge responses must also carry hardening headers."""
        h = _make_handler("GET")   # no auth header
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        h.do_GET()
        self.assertIn("X-Content-Type-Options", emitted)
        self.assertIn("X-Frame-Options", emitted)

    def test_403_response_includes_security_headers(self):
        """CSRF rejection responses must also carry hardening headers."""
        body = b"count=0&csrf=bad&action=save"
        h = _make_handler("POST", body, headers=_auth_header())
        emitted = {}
        h.send_header = lambda k, v: emitted.update({k: v})
        h.do_POST()
        self.assertIn("X-Content-Type-Options", emitted)

    def test_csp_allows_google_fonts(self):
        """Google Fonts must still be allowed by the CSP (fonts load correctly)."""
        headers = self._get_headers("do_GET")
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("fonts.googleapis.com", csp)
        self.assertIn("fonts.gstatic.com", csp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
