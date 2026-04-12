"""
Toddler TV - Web Remote
A tiny HTTP server for controlling the off-period schedule from your phone.
Runs on port 8080 by default. Open http://<media-pc-ip>:8080 from any device on the same WiFi.
"""

import base64
import html as _html
import json
import logging
import re
import secrets
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

from schedule import get_periods, save_periods
from config import SCENES

# ─────────────────────────────────────────────
#  SECURITY CONSTANTS
# ─────────────────────────────────────────────

MAX_BODY    = 16_384   # 16 KB hard cap on POST body (CWE-400)
MAX_PERIODS = 48       # hard cap on period count per request (CWE-400)

# Both tokens are generated fresh on each process start.
_CSRF_TOKEN    = secrets.token_hex(16)
_BOOT_PASSWORD = secrets.token_urlsafe(12)
_BASIC_REALM   = "Toddler TV Remote"
_CREDENTIALS   = base64.b64encode(f"admin:{_BOOT_PASSWORD}".encode()).decode()

_log = logging.getLogger("web_remote")

PORT = 8080


def _get_local_ip() -> str:
    """Get the LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ─────────────────────────────────────────────
#  HTML PAGE
# ─────────────────────────────────────────────

def _build_page(periods: list, message: str = "") -> str:
    scene_options = list(SCENES.keys())

    rows_html = ""
    for i, (sh, sm, eh, em, scene) in enumerate(periods):
        scene_select = "".join(
            f'<option value="{s}" {"selected" if s == scene else ""}>'
            f'{SCENES[s]["label"]}</option>'
            for s in scene_options
        )
        rows_html += f"""
        <div class="period-card">
            <div class="period-times">
                <div class="time-group">
                    <label>From</label>
                    <input type="time" name="start_{i}" value="{sh:02d}:{sm:02d}">
                </div>
                <span class="arrow">\u2192</span>
                <div class="time-group">
                    <label>Until</label>
                    <input type="time" name="end_{i}" value="{eh:02d}:{em:02d}">
                </div>
            </div>
            <div class="period-scene">
                <select name="scene_{i}">{scene_select}</select>
                <button type="submit" name="delete" value="{i}" class="btn-delete">\u2715</button>
            </div>
        </div>
        """

    message_html = f'<div class="message">{_html.escape(message)}</div>' if message else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Toddler TV Remote</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap');

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: 'Space Mono', 'Courier New', monospace;
    background: #0d0721;
    color: #c0c0d0;
    min-height: 100vh;
    padding: 0 0 120px 0;
}}

.header {{
    background: linear-gradient(180deg, #1a0a2e 0%, #0d0721 100%);
    padding: 24px 20px 16px;
    text-align: center;
    border-bottom: 1px solid #1e0e3a;
    position: sticky;
    top: 0;
    z-index: 10;
}}

.header h1 {{
    font-size: 18px;
    font-weight: 700;
    color: #00ff88;
    letter-spacing: 3px;
    text-transform: uppercase;
    margin-bottom: 4px;
    text-shadow: 0 0 20px rgba(0, 255, 136, 0.3);
}}

.header .subtitle {{
    font-size: 11px;
    color: #444466;
    letter-spacing: 1px;
}}

.container {{
    max-width: 480px;
    margin: 0 auto;
    padding: 16px;
}}

.message {{
    background: #1a2a1a;
    border: 1px solid #00ff88;
    color: #00ff88;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 12px;
    margin-bottom: 16px;
    text-align: center;
    animation: fadeIn 0.3s ease;
}}

@keyframes fadeIn {{
    from {{ opacity: 0; transform: translateY(-8px); }}
    to {{ opacity: 1; transform: translateY(0); }}
}}

.section-label {{
    font-size: 11px;
    color: #555577;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 12px;
    padding-left: 4px;
}}

.period-card {{
    background: #1a0a2e;
    border: 1px solid #2a1a4e;
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 10px;
    transition: border-color 0.2s;
}}

.period-card:hover {{
    border-color: #3a2a5e;
}}

.period-times {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
}}

.time-group {{
    flex: 1;
}}

.time-group label {{
    display: block;
    font-size: 10px;
    color: #555577;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 4px;
}}

.arrow {{
    color: #00ff88;
    font-size: 18px;
    margin-top: 14px;
}}

input[type="time"] {{
    width: 100%;
    padding: 10px 8px;
    background: #0d0721;
    border: 1px solid #2a1a4e;
    border-radius: 6px;
    color: #00ff88;
    font-family: 'Space Mono', monospace;
    font-size: 16px;
    text-align: center;
    outline: none;
    -webkit-appearance: none;
}}

input[type="time"]:focus {{
    border-color: #00ff88;
    box-shadow: 0 0 8px rgba(0, 255, 136, 0.15);
}}

/* Fix time input color-scheme for dark backgrounds */
input[type="time"]::-webkit-calendar-picker-indicator {{
    filter: invert(0.7);
}}

.period-scene {{
    display: flex;
    gap: 8px;
    align-items: center;
}}

select {{
    flex: 1;
    padding: 8px 10px;
    background: #0d0721;
    border: 1px solid #2a1a4e;
    border-radius: 6px;
    color: #c0c0d0;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    outline: none;
    -webkit-appearance: none;
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23555577'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 10px center;
    padding-right: 28px;
}}

select:focus {{
    border-color: #4ECDC4;
}}

.btn-delete {{
    background: none;
    border: 1px solid #3a1a2e;
    color: #FF6B6B;
    width: 36px;
    height: 36px;
    border-radius: 6px;
    font-size: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
    flex-shrink: 0;
}}

.btn-delete:hover {{
    background: #3a1a2e;
    border-color: #FF6B6B;
}}

.actions {{
    display: flex;
    gap: 10px;
    margin-top: 20px;
}}

.btn {{
    flex: 1;
    padding: 14px;
    border: none;
    border-radius: 8px;
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
    letter-spacing: 1px;
    text-transform: uppercase;
    transition: all 0.2s;
}}

.btn-save {{
    background: #00ff88;
    color: #0d0721;
}}

.btn-save:hover {{
    background: #33ffaa;
    box-shadow: 0 0 20px rgba(0, 255, 136, 0.3);
}}

.btn-save:active {{
    transform: scale(0.97);
}}

.btn-add {{
    background: #1a0a2e;
    color: #4ECDC4;
    border: 1px solid #2a1a4e;
}}

.btn-add:hover {{
    border-color: #4ECDC4;
    background: #1a1a3e;
}}

.empty-state {{
    text-align: center;
    padding: 40px 20px;
    color: #333355;
}}

.empty-state .icon {{
    font-size: 48px;
    margin-bottom: 12px;
}}

.empty-state p {{
    font-size: 12px;
    line-height: 1.6;
}}

.status-bar {{
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: #1a0a2e;
    border-top: 1px solid #2a1a4e;
    padding: 12px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 10px;
    color: #444466;
    z-index: 10;
}}

.status-dot {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #00ff88;
    margin-right: 6px;
    animation: pulse 2s infinite;
}}

@keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.4; }}
}}
</style>
</head>
<body>

<div class="header">
    <h1>\U0001f4fa Toddler TV</h1>
    <div class="subtitle">SCHEDULE REMOTE</div>
</div>

<div class="container">
    {message_html}

    <form method="POST" action="/">
        <input type="hidden" name="count" value="{len(periods)}">
        <input type="hidden" name="csrf"  value="{_CSRF_TOKEN}">

        <div class="section-label">Off Periods</div>

        {rows_html if rows_html else '''
        <div class="empty-state">
            <div class="icon">\U0001f916</div>
            <p>No off-periods set.<br>The TV will play all day!</p>
        </div>
        '''}

        <div class="actions">
            <button type="submit" name="action" value="save" class="btn btn-save">Save</button>
            <button type="submit" name="action" value="add" class="btn btn-add">+ Add</button>
        </div>
    </form>
</div>

<div class="status-bar">
    <span><span class="status-dot"></span>Connected to Toddler TV</span>
    <span>{len(periods)} period(s)</span>
</div>

</body>
</html>"""


# ─────────────────────────────────────────────
#  HTTP HANDLER
# ─────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    # ── Auth helpers (CWE-306) ─────────────────────────────────────────────────

    def _check_auth(self) -> bool:
        """Return True only when the request carries valid Basic Auth."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        # secrets.compare_digest prevents timing-based credential leaks.
        return secrets.compare_digest(auth[6:], _CREDENTIALS)

    def _send_auth_challenge(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Basic realm="{_BASIC_REALM}"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(b"Unauthorized")

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        if not self._check_auth():
            self._send_auth_challenge()
            return
        periods = get_periods()
        html = _build_page(periods)
        self._respond(200, html)

    def do_POST(self):
        if not self._check_auth():
            self._send_auth_challenge()
            return

        # ── 1. Hard-cap body size before reading (CWE-400) ───────────────────
        try:
            raw_len = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raw_len = 0
        length = min(raw_len, MAX_BODY)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        params = parse_qs(body)

        def first(key, default=""):
            return params.get(key, [default])[0]

        # ── 2. CSRF token validation (CWE-352) ────────────────────────────────
        if not secrets.compare_digest(first("csrf"), _CSRF_TOKEN):
            # Strip anything that isn't a valid IP character before logging
            # to prevent log-injection via a crafted peer address (L1).
            safe_peer = re.sub(r"[^\w.:\[\]-]", "?", self.client_address[0])
            _log.warning("CSRF token mismatch from %s", safe_peer)
            self._respond(403, "<h1>403 Forbidden</h1>")
            return

        action = first("action")

        # ── 3. Hard-cap period count (CWE-400) ────────────────────────────────
        try:
            count = min(int(first("count", "0")), MAX_PERIODS)
        except ValueError:
            count = 0

        # ── 4. Parse periods with full validation ─────────────────────────────
        _valid_scenes = set(SCENES.keys())
        periods = []
        for i in range(count):
            start = first(f"start_{i}", "00:00")
            end   = first(f"end_{i}",   "00:00")
            scene = first(f"scene_{i}", "sleeping")

            # Validate and clamp time fields (CWE-755)
            try:
                parts_s = start.split(":")
                parts_e = end.split(":")
                sh, sm = int(parts_s[0]), int(parts_s[1])
                eh, em = int(parts_e[0]), int(parts_e[1])
                sh, sm = max(0, min(23, sh)), max(0, min(59, sm))
                eh, em = max(0, min(23, eh)), max(0, min(59, em))
            except (ValueError, IndexError):
                _log.warning("Malformed time in period %d — skipped", i)
                continue

            # Whitelist scene against known keys (CWE-20)
            if scene not in _valid_scenes:
                _log.warning("Unknown scene %r in period %d — defaulting", scene, i)
                scene = "sleeping"

            periods.append((sh, sm, eh, em, scene))

        # ── 5. Action dispatch ────────────────────────────────────────────────
        delete_idx = first("delete", "")
        if delete_idx != "":
            try:
                idx = int(delete_idx)
            except ValueError:
                idx = -1
            if 0 <= idx < len(periods):
                periods.pop(idx)
            save_periods(periods)
            html = _build_page(periods, "\u2705 Period deleted")
            self._respond(200, html)
            return

        if action == "add":
            periods.append((12, 0, 13, 0, "lunch"))
            # Don't save yet — let user edit first
            html = _build_page(periods)
            self._respond(200, html)

        elif action == "save":
            save_periods(periods)
            html = _build_page(periods, "\u2705 Schedule saved!")
            self._respond(200, html)

        else:
            html = _build_page(periods)
            self._respond(200, html)

    def _send_security_headers(self) -> None:
        """Emit hardening headers on every response (L2)."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # CSP matched to what the page actually loads:
        #   - inline <style> block            → style-src 'unsafe-inline'
        #   - Google Fonts CSS @import        → style-src fonts.googleapis.com
        #   - Google Fonts font files         → font-src fonts.gstatic.com
        #   - no scripts anywhere             → script-src 'none'
        #   - frame-ancestors 'none' mirrors X-Frame-Options for modern browsers
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            "style-src 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "script-src 'none'; "
            "frame-ancestors 'none'",
        )

    def _respond(self, code: int, html: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, fmt, *args):
        # Route access logs through the standard logging system (CWE-778)
        _log.info(fmt, *args)


# ─────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────

def start_web_remote(port: int = PORT) -> None:
    """Start the web remote server in a daemon thread."""
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")
    ip = _get_local_ip()

    def _run():
        server = HTTPServer(("0.0.0.0", port), _Handler)
        print(f"[remote] Web remote → http://{ip}:{port}")
        print(f"[remote] Login  →  username: admin   password: {_BOOT_PASSWORD}")
        print(f"[remote] (password is regenerated on every restart)")
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
