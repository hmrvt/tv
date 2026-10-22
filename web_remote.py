"""
Toddler TV - Web Remote
A tiny HTTP server for controlling the TV from your phone.  Runs on port 8080.
Open http://<media-pc-ip>:8080 from any device on the same WiFi.

Pages, by how often they are used:
  /                     Now — what the TV is doing, and the one thing to do about it
  /ch/<n>               a channel: now playing + up next
  /ch/<n>/add           add a video (once, or for good)
  /ch/<n>/library       every video on the channel, or the removed ones
  /ch/<n>/settings      YouTube playlists, name and colour, merge
  /schedule             pause switch, today's timeline, the off periods
  /schedule/<i>|new     edit or add one off period

No JavaScript: the CSP forbids it, so every control is a link or a form.
"""

import html as _html
import logging
import re
import secrets
import socket
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urlsplit

from schedule import (
    get_periods, save_periods, get_manual_status, set_manual_status,
    clear_manual_status, get_schedule_pause, pause_schedules, resume_schedules,
    current_off_period, next_off_period, todays_periods,
)
from config import SCENES, CHANNELS, N
from media import validate_youtube_url, validate_youtube_playlist_url, _SAFE_PLAYLIST_ID

# ─────────────────────────────────────────────
#  SECURITY CONSTANTS
# ─────────────────────────────────────────────

MAX_BODY     = 16_384   # 16 KB hard cap on POST body (CWE-400)
MAX_PERIODS  = 48       # hard cap on the number of off periods (CWE-400)
MAX_POSITION = 100_000  # hard cap on a feed position index (CWE-400)
MAX_PAGE     = 1_000    # hard cap on a library page number (CWE-400)
PAGE_SIZE    = 50       # library rows per page

_CSRF_TOKEN = secrets.token_hex(16)

_log = logging.getLogger("web_remote")

PORT = 8080

# ─────────────────────────────────────────────
#  CHANNEL CALLBACK REGISTRY
#  Populated by the app after start_web_remote()
# ─────────────────────────────────────────────

# Every entry is optional: an unregistered callback makes its UI control
# inert rather than raising out of the handler thread.
#
#   get       (ch_idx) -> dict | None            channel + feed snapshot
#   skip      (ch_idx) -> None                   advance to the next video
#   play_next (ch_idx, url) -> bool              one-shot injection
#   add       (ch_idx, url) -> bool              add to the channel for good
#   remove    (ch_idx, pos, permanent) -> bool   drop a video from the feed
#   refresh   (ch_idx) -> bool                   re-sync playlists now
#   library   () -> dict                         download / refresh status
#   catalog   (ch_idx) -> dict | None            videos / ignored / sources
#   ignore    (ch_idx, url) -> bool              move a video to Ignored
#   restore   (ch_idx, url) -> bool              bring an ignored video back
#   add_source    (ch_idx, playlist_id) -> bool  sync a YouTube playlist in
#   remove_source (ch_idx, playlist_id) -> bool  stop syncing it
#   save_channel  (ch_idx, name, emoji, color) -> bool
#   merge     (src_idx, dst_idx) -> bool         empty src into dst
#   tv        () -> {"state", "channel"}         what the TV is doing
_callbacks: dict = {}


def register_channel_callbacks(callbacks: dict) -> None:
    """Register app callbacks for channel playlist operations."""
    global _callbacks
    _callbacks = dict(callbacks or {})


def _cb(name):
    """Return a registered callback, or None if the app hasn't provided it."""
    return _callbacks.get(name)


def _call(name, *args):
    """Call a read-only callback; a failure renders as "unknown", never a 500."""
    fn = _cb(name)
    if not fn:
        return None
    try:
        return fn(*args)
    except Exception:                       # noqa: BLE001 - keep the page up
        _log.exception("callback %s failed", name)
        return None


def _channel_info(ch_idx: int) -> dict | None:
    return _call("get", ch_idx)


def _tv_state() -> dict | None:
    return _call("tv")


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

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


_esc = _html.escape

_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


def _safe_color(value, fallback: str = "#3DF2A0") -> str:
    """Whitelist a colour before it reaches a style attribute (CWE-20).

    Channel colours come from channels.json, which is hand-edited; anything
    that is not a plain hex literal is replaced rather than sanitised.
    """
    if isinstance(value, str) and _HEX_RE.match(value.strip()):
        return value.strip()
    return fallback


def _hm(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


_CURRENT = ' aria-current="page"'
_DOT = " · "


def _current(flag: bool) -> str:
    return _CURRENT if flag else ""


def _csrf_field() -> str:
    return f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'


def _channel_name(ch_idx: int) -> str:
    return CHANNELS[ch_idx].get("name") or f"Channel {ch_idx + 1}"


# ─────────────────────────────────────────────
#  ROUTING
# ─────────────────────────────────────────────

# Channel sub-pages, whitelisted in the route itself (CWE-20).  The old
# sub-page names still resolve, so bookmarks keep working.
_CH_SUBPAGES = {
    "": "", "add": "add", "library": "library", "settings": "settings",
    "videos": "library", "ignored": "removed", "sources": "settings",
}
_CH_PATH_RE = re.compile(
    r'^/ch/(\d{1,2})(?:/(add|library|settings|videos|ignored|sources))?$')
_SCHED_PATH_RE = re.compile(r'^/schedule(?:/(new|\d{1,2}))?$')


def _split(raw_path: str) -> tuple[str, dict]:
    """Split a request path into (path, query).  The query is size-capped."""
    try:
        parts = urlsplit(raw_path)
        query = parse_qs(parts.query[:512], max_num_fields=8)
    except ValueError:
        return raw_path, {}
    return parts.path, query


def _parse_channel_route(path: str) -> tuple[int, str] | None:
    """Return (0-indexed channel, sub-page) for /ch/N[/sub], or None."""
    m = _CH_PATH_RE.match(path)
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= N:
        return n - 1, _CH_SUBPAGES[m.group(2) or ""]
    return None


def _parse_schedule_route(path: str):
    """Return "list", "new" or a period index for /schedule[/…], or None."""
    m = _SCHED_PATH_RE.match(path)
    if not m:
        return None
    target = m.group(1)
    if target is None:
        return "list"
    if target == "new":
        return "new"
    i = int(target)
    return i if i < MAX_PERIODS else None


def _library_view(sub: str, query: dict) -> tuple[str, int]:
    """Whitelist the library filter and bound the page number (CWE-20/400)."""
    show = "removed" if sub == "removed" or query.get("show", [""])[0] == "removed" else ""
    try:
        page = int(query.get("page", ["1"])[0])
    except ValueError:
        page = 1
    return show, max(1, min(MAX_PAGE, page))


# ─────────────────────────────────────────────
#  SCENES — friendly names and line icons
# ─────────────────────────────────────────────

# (short name, accent colour, inline SVG paths).  SCENES' own labels are the
# shouty on-screen captions; the remote uses these instead.
_SCENE_UI = {
    "tubby":        ("Bath time", "#7FD6F5",
                     '<path d="M3 12h18v3a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z"/>'
                     '<path d="M6 12V6a2 2 0 0 1 4 0"/><path d="M7 20l-1 2M17 20l1 2"/>'),
    "lunch":        ("Lunch", "#FFD166",
                     '<path d="M4 3v8a3 3 0 0 0 6 0V3M7 3v18"/>'
                     '<path d="M17 3c-2 2-2 6 0 8v10"/>'),
    "sleeping":     ("Sleep", "#B9A6FF",
                     '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'),
    "kita_goodbye": ("Kita", "#7EE0A1",
                     '<rect x="5" y="7" width="14" height="14" rx="3"/>'
                     '<path d="M9 7V5a3 3 0 0 1 6 0v2M9 13h6"/>'),
    "fixing":       ("Engineers", "#FF9F7A",
                     '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 '
                     '5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>'),
}
_CLOCK_PATHS = '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'


def _scene_order() -> list[str]:
    known = [s for s in _SCENE_UI if s in SCENES]
    return known + [s for s in SCENES if s not in _SCENE_UI]


def _scene_name(scene: str) -> str:
    if scene in _SCENE_UI:
        return _SCENE_UI[scene][0]
    return SCENES.get(scene, {}).get("label", scene).title()


def _scene_color(scene: str) -> str:
    return _SCENE_UI.get(scene, ("", "#C9C0E8", ""))[1]


def _icon(paths: str, size: int = 22, color: str = "currentColor",
          width: float = 2) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{paths}</svg>')


def _scene_icon(scene: str, size: int = 22) -> str:
    paths = _SCENE_UI[scene][2] if scene in _SCENE_UI else _CLOCK_PATHS
    return _icon(paths, size, _scene_color(scene))


_I_SKIP    = '<path d="M5 4l10 8-10 8z"/><path d="M19 5v14"/>'
_I_PLAY    = '<path d="M6 4l14 8-14 8z"/>'
_I_POWER   = '<path d="M12 3v9"/><path d="M6.3 7.3a8 8 0 1 0 11.4 0"/>'
_I_PLUS    = '<path d="M12 5v14M5 12h14"/>'
_I_NEXT    = '<path d="M9 6l6 6-6 6"/>'
_I_BACK    = '<path d="M15 6l-6 6 6 6"/>'
_I_SYNC    = '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>'
_I_TV      = '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8"/>'
_I_LIST    = '<path d="M4 6h16M4 12h16M4 18h10"/>'
_I_CLOCK   = _CLOCK_PATHS
_I_CHECK   = '<path d="M5 12l5 5L20 7"/>'
_DOTS = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" '
         'aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" '
         'r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>')


# ─────────────────────────────────────────────
#  STYLE
# ─────────────────────────────────────────────

_CSS = (
    ":root{--bg:#0B0718;--surface:#160F2E;--surface2:#211846;--line:#2E2458;"
    "--line2:#3A2F6E;--text:#F1EDFF;--mid:#C9C0E8;--dim:#A79DD0;--green:#3DF2A0;"
    "--amber:#FFB547;--lilac:#C9B8FF;--red:#FF8FA0;--tap:48px;"
    "--ui:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "--mono:ui-monospace,'Cascadia Mono',Consolas,monospace;color-scheme:dark}\n"
    "*,*::before,*::after{box-sizing:border-box}\n"
    "html{-webkit-text-size-adjust:100%}\n"
    "body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 var(--ui);"
    "padding-bottom:calc(72px + env(safe-area-inset-bottom))}\n"
    "body.pinned{padding-bottom:calc(148px + env(safe-area-inset-bottom))}\n"
    "a{color:var(--green)}\n"
    "main{max-width:560px;margin:0 auto;padding:18px 16px 24px;display:flex;"
    "flex-direction:column;gap:22px}\n"
    "h1{margin:0;font-size:24px;line-height:1.25}\n"
    "h2{margin:0}\n"
    ".skip-link{position:absolute;left:-999px}.skip-link:focus{left:8px;top:8px;"
    "background:var(--surface);padding:8px;z-index:50}\n"
    ".lbl{font:11px var(--mono);letter-spacing:1.6px;text-transform:uppercase;"
    "color:var(--dim);margin:0}\n"
    ".note{margin:0;font-size:13px;color:var(--mid)}\n"
    ".dim{color:var(--dim)}\n"
    ".stack{display:flex;flex-direction:column;gap:10px}\n"
    ".brand{display:flex;justify-content:space-between;align-items:center}\n"
    ".flash{background:#0F2A22;border:1px solid #1E6B4E;color:#BFFBE0;padding:12px 14px;"
    "border-radius:12px;font-size:14px}\n"
    ".flash.err{background:#2A1220;border-color:#6B2A3A;color:#FFD0D8}\n"
    # cards / hero
    ".card{background:var(--surface);border:1px solid var(--line);border-radius:16px;"
    "padding:16px;display:flex;flex-direction:column;gap:12px}\n"
    ".hero{border-radius:18px;padding:20px 18px;gap:16px}\n"
    ".hero.on{border-color:#2B5C49}.hero.on .lbl{color:var(--green)}\n"
    ".hero.manual{background:#1E1528;border-color:#6B4A1F}.hero.manual .lbl{color:var(--amber)}\n"
    ".hero.sched{background:#19132E;border-color:#4A3D86}.hero.sched .lbl{color:var(--lilac)}\n"
    ".hero-row{display:flex;align-items:center;gap:14px}\n"
    ".badge{width:52px;height:52px;border-radius:14px;background:#211846;display:flex;"
    "align-items:center;justify-content:center;flex-shrink:0}\n"
    ".big{margin:0;font-size:26px;font-weight:700;line-height:1.2}\n"
    ".title{margin:0;font-size:20px;font-weight:700;line-height:1.3}\n"
    ".chan{display:flex;align-items:center;gap:8px;text-decoration:none;"
    "color:var(--mid);font-size:13px;font-weight:600}\n"
    ".sq{width:10px;height:10px;border-radius:3px;flex-shrink:0}\n"
    ".track{height:6px;border-radius:3px;background:#2A2150;overflow:hidden}\n"
    ".track span{display:block;height:100%}\n"
    # buttons
    ".btn{display:flex;align-items:center;justify-content:center;gap:8px;"
    "min-height:var(--tap);padding:0 16px;border-radius:12px;border:1px solid var(--line2);"
    "background:var(--surface2);color:var(--text);font:600 15px var(--ui);"
    "text-decoration:none;cursor:pointer;width:100%}\n"
    ".btn-main{min-height:54px;border:0;border-radius:14px;background:var(--green);"
    "color:#06140D;font-weight:700;font-size:16px}\n"
    ".btn-amber{background:var(--amber);color:#241503}\n"
    ".btn-ghost{background:transparent}\n"
    ".btn-danger{background:transparent;border-color:#5A2633;color:var(--red)}\n"
    ".btn-sm{min-height:40px;width:auto;padding:0 12px;font-size:13px;border-radius:10px}\n"
    ".btn-row{display:flex;gap:10px}.btn-row>*{flex:1}\n"
    "form{margin:0}\n"
    # scene tiles / chips
    ".tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}\n"
    ".tile{display:flex;flex-direction:column;align-items:flex-start;gap:10px;"
    "min-height:76px;padding:14px;border-radius:14px;background:#1A1236;"
    "border:1px solid var(--line);color:var(--text);font:600 15px var(--ui);"
    "text-align:left;cursor:pointer;width:100%}\n"
    ".chips{display:flex;flex-wrap:wrap;gap:8px}\n"
    ".chip{display:flex;align-items:center;gap:8px;min-height:44px;padding:0 14px;"
    "border-radius:12px;background:#1A1236;border:1px solid var(--line);"
    "color:var(--text);font:600 14px var(--ui);cursor:pointer;text-decoration:none}\n"
    # lists
    ".list{list-style:none;margin:0;padding:0;background:var(--surface);"
    "border:1px solid var(--line);border-radius:16px;overflow:hidden}\n"
    ".list>li+li{border-top:1px solid var(--line)}\n"
    ".row{display:flex;align-items:center;gap:12px;min-height:58px;padding:8px 8px 8px 14px;"
    "color:var(--text);text-decoration:none}\n"
    ".row .main{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}\n"
    ".row .t{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;"
    "text-overflow:ellipsis}\n"
    ".row .s{font-size:12px;color:var(--dim);white-space:nowrap;overflow:hidden;"
    "text-overflow:ellipsis}\n"
    ".num{width:34px;height:34px;border-radius:9px;font-weight:800;display:flex;"
    "align-items:center;justify-content:center;color:#0B0718;flex-shrink:0}\n"
    ".tag{font:600 11px var(--ui);padding:3px 8px;border-radius:6px;background:#2A2150;"
    "color:var(--mid);flex-shrink:0}\n"
    ".tag.on{background:#233D33;color:#7EF0B8}\n"
    ".once{background:#10273A}.once .s{color:#7FD6F5}\n"
    # up-next rows: the whole row is a <summary>, its actions the <details>
    # body — native show/hide, no JS and no :has() needed
    "details.more summary{list-style:none;cursor:pointer}\n"
    "details.more summary::-webkit-details-marker{display:none}\n"
    ".dots{width:44px;height:44px;border-radius:10px;display:flex;align-items:center;"
    "justify-content:center;color:var(--dim);flex-shrink:0}\n"
    "details.more[open]{background:#1B1338}\n"
    "details.more[open] .dots{color:var(--text);background:var(--surface2)}\n"
    ".menu{display:flex;gap:8px;padding:0 8px 12px 14px}\n"
    # channel header
    ".switch{display:flex;gap:8px}\n"
    ".sw{flex:1;min-width:0;display:flex;align-items:center;justify-content:center;gap:8px;"
    "min-height:44px;padding:0 10px;border-radius:12px;border:1px solid var(--line);"
    "background:#140E28;color:var(--mid);font:600 14px var(--ui);text-decoration:none}\n"
    ".sw span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n"
    ".sw[aria-current]{color:var(--text);background:#21173A}\n"
    ".seg{display:flex;gap:4px;padding:4px;background:#140E28;border:1px solid var(--line);"
    "border-radius:12px}\n"
    ".seg a{flex:1;display:flex;align-items:center;justify-content:center;min-height:40px;"
    "border-radius:9px;color:var(--dim);font:600 13px var(--ui);text-decoration:none}\n"
    ".seg a[aria-current]{background:#2A2052;color:var(--text)}\n"
    ".pills{display:flex;gap:8px;flex-wrap:wrap}\n"
    ".pill{display:flex;align-items:center;min-height:36px;padding:0 12px;border-radius:18px;"
    "border:1px solid var(--line2);color:var(--mid);font:600 13px var(--ui);text-decoration:none}\n"
    ".pill[aria-current]{background:var(--text);color:var(--bg);border-color:var(--text)}\n"
    ".linkcard{display:flex;align-items:center;gap:12px;padding:14px;border-radius:14px;"
    "border:1px dashed var(--line2);text-decoration:none;color:var(--text)}\n"
    # forms
    ".field{display:flex;flex-direction:column;gap:6px}\n"
    ".in{min-height:50px;width:100%;padding:0 14px;border-radius:12px;"
    "border:1px solid var(--line2);background:#1A1236;color:var(--text);font:15px var(--ui)}\n"
    ".in:focus,.btn:focus-visible,.tile:focus-visible,.chip:focus-visible{outline:2px solid "
    "var(--green);outline-offset:2px}\n"
    "fieldset{border:0;margin:0;padding:0;min-width:0}\n"
    "legend{padding:0;margin-bottom:10px}\n"
    ".opt{display:flex;gap:14px;align-items:flex-start;padding:16px;border-radius:14px;"
    "background:var(--surface);border:1px solid var(--line);cursor:pointer}\n"
    ".opt input{width:22px;height:22px;margin:2px 0 0;accent-color:var(--green);flex-shrink:0}\n"
    ".opt:has(input:checked){border-color:var(--green);background:#0F2A22}\n"
    ".opt b{display:block;font-size:16px}\n"
    ".pick{position:relative}\n"
    ".pick input{position:absolute;opacity:0;width:1px;height:1px}\n"
    ".pick span{display:flex;align-items:center;gap:10px;min-height:50px;padding:0 14px;"
    "border-radius:12px;border:1px solid var(--line);background:var(--surface);"
    "font:600 14px var(--ui);cursor:pointer}\n"
    ".pick input:checked+span{border-color:var(--text);background:#2A2150}\n"
    ".pick input:focus-visible+span{outline:2px solid var(--green);outline-offset:2px}\n"
    ".days{display:flex;gap:5px}.days .pick{flex:1}\n"
    ".days .pick span{justify-content:center;padding:0;min-height:44px;font-size:13px}\n"
    ".days .pick input:checked+span{border-color:var(--green);background:#233D33}\n"
    ".swatches{display:flex;gap:10px;flex-wrap:wrap}\n"
    ".swatches .pick span{width:40px;min-height:40px;border-radius:10px;padding:0;border:2px solid transparent}\n"
    ".swatches .pick input:checked+span{border-color:var(--text);box-shadow:0 0 0 2px var(--bg) inset}\n"
    ".checkbtn{display:flex;align-items:center;gap:14px;width:100%;padding:16px;"
    "border-radius:16px;background:var(--surface);border:1px solid var(--line);color:var(--text);"
    "text-align:left;font:15px var(--ui);cursor:pointer}\n"
    ".box{width:26px;height:26px;border-radius:7px;border:2px solid var(--dim);display:flex;"
    "align-items:center;justify-content:center;flex-shrink:0;color:#241503}\n"
    ".checkbtn[aria-pressed=true]{border-color:#6B4A1F;background:#1E1528}\n"
    ".checkbtn[aria-pressed=true] .box{background:var(--amber);border-color:var(--amber)}\n"
    "details.danger{border:1px solid #3A2633;border-radius:14px;background:#150D1E}\n"
    "details.danger summary{min-height:48px;display:flex;align-items:center;padding:0 14px;"
    "font-weight:600;color:var(--red);cursor:pointer}\n"
    "details.danger .body{padding:0 14px 14px;display:flex;flex-direction:column;gap:12px}\n"
    # timeline
    ".timeline{position:relative;height:40px;border-radius:10px;background:#183A2E;overflow:hidden}\n"
    ".timeline span{position:absolute;top:0;bottom:0;background:#4A3D86}\n"
    ".timeline.paused{background:#262040}.timeline.paused span{background:#3A3358}\n"
    ".timeline i{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--text)}\n"
    ".hours{display:flex;justify-content:space-between;font:11px var(--mono);color:var(--dim)}\n"
    ".empty{text-align:center;padding:28px 16px;border:1px dashed var(--line2);"
    "border-radius:16px;color:var(--mid);font-size:14px}\n"
    # bottom bars
    ".pin{position:fixed;left:0;right:0;bottom:calc(64px + env(safe-area-inset-bottom));"
    "padding:12px 16px;background:var(--bg);border-top:1px solid #1E1740}\n"
    ".pin>*{max-width:528px;margin:0 auto}\n"
    "nav.tabbar{position:fixed;left:0;right:0;bottom:0;display:flex;background:#120B26;"
    "border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom);z-index:20}\n"
    "nav.tabbar a{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;"
    "padding:10px 0 12px;text-decoration:none;color:var(--dim);font:600 11px var(--ui)}\n"
    "nav.tabbar a[aria-current]{color:var(--green)}\n"
    ".sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}\n"
)


# ─────────────────────────────────────────────
#  SHARED PAGE CHROME
# ─────────────────────────────────────────────

def _flash(message: str) -> str:
    """Status banner.  A leading cross marks the message as an error."""
    if not message:
        return ""
    err = message.startswith("❌")
    cls = "flash err" if err else "flash"
    role = "alert" if err else "status"
    return f'<div class="{cls}" role="{role}">{_esc(message)}</div>'


def _tabbar(active: str) -> str:
    tv = _tv_state() or {}
    try:
        on_screen = int(tv.get("channel", 0))
    except (TypeError, ValueError):
        on_screen = 0
    on_screen = on_screen if 0 <= on_screen < N else 0
    tabs = [("now", "/", _I_TV, "Now"),
            ("channels", f"/ch/{on_screen + 1}", _I_LIST, "Channels"),
            ("schedule", "/schedule", _I_CLOCK, "Schedule")]
    links = "".join(
        f'<a href="{href}"{_current(key == active)}>{_icon(icon)}{label}</a>'
        for key, href, icon, label in tabs
    )
    return f'<nav class="tabbar" aria-label="Sections">{links}</nav>'


def _shell(title: str, active: str, body: str, pinned: str = "") -> str:
    """Document chrome: head, main, an optional pinned action, the tab bar."""
    pin_html = f'<div class="pin">{pinned}</div>' if pinned else ""
    body_open = '<body class="pinned">' if pinned else "<body>"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="dark">\n'
        '<meta name="theme-color" content="#0B0718">\n'
        f"<title>{_esc(title)} — Toddler TV</title>\n"
        f"<style>\n{_CSS}</style>\n"
        "</head>\n"
        f"{body_open}\n"
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        f'<main id="main">{body}</main>\n'
        f"{pin_html}{_tabbar(active)}\n"
        "</body>\n</html>"
    )


def _offline() -> str:
    return ('<div class="empty">The TV app is not running.<br>'
            "Start it to see and control the channels.</div>")


# ─────────────────────────────────────────────
#  NOW PAGE
# ─────────────────────────────────────────────

def _now_minutes() -> int:
    now = datetime.now()
    return now.hour * 60 + now.minute


def _scheduled_now() -> tuple | None:
    """The scheduled period covering now (ignoring overrides), or None."""
    now = _now_minutes()
    for start, end, scene in todays_periods():
        if start <= now < end:
            return start, end, scene
    return None


def _after_status_text(pause: dict | None) -> str:
    """What happens once a manual status ends."""
    if pause:
        return "Afterwards the TV comes back on — schedules are paused."
    current = _scheduled_now()
    if current:
        start, end, scene = current
        return (f"Afterwards the schedule takes over: <b>{_esc(_scene_name(scene))} "
                f"until {_hm(end // 60, end % 60)}</b>.")
    nxt = next_off_period()
    if nxt:
        return (f"Afterwards the TV comes back on. Next on the schedule: "
                f"<b>{_esc(_scene_name(nxt['scene']))} at "
                f"{_hm(nxt['start_h'], nxt['start_m'])}</b>.")
    return "Afterwards the TV comes back on."


def _scene_buttons(exclude: str | None, style: str) -> str:
    """Scene tiles or chips; each one switches a manual status on."""
    cls = "tile" if style == "tiles" else "chip"
    buttons = "".join(
        f'<button type="submit" name="scene" value="{_esc(s)}" class="{cls}">'
        f"{_scene_icon(s, 22 if style == 'tiles' else 18)}{_esc(_scene_name(s))}</button>"
        for s in _scene_order() if s != exclude
    )
    wrap = "tiles" if style == "tiles" else "chips"
    return (f'<form method="POST" action="/">{_csrf_field()}'
            f'<input type="hidden" name="action" value="status_on">'
            f'<div class="{wrap}">{buttons}</div></form>')


def _hero_playing(tv: dict) -> str:
    ch_idx = tv.get("channel", 0)
    ch_idx = ch_idx if isinstance(ch_idx, int) and 0 <= ch_idx < N else 0
    info = _channel_info(ch_idx) or {}
    cur = info.get("current") or {}
    color = _safe_color(CHANNELS[ch_idx].get("color"))
    n = ch_idx + 1
    chan = (f'<a class="chan" href="/ch/{n}"><span class="sq" style="background:{color}">'
            f"</span>{_esc(_channel_name(ch_idx))}</a>")

    if tv.get("state") != "playing" or not cur:
        what = ("Starting up…" if tv.get("state") == "booting"
                else f"Loading {_channel_name(ch_idx)}…")
        return (f'<section class="card hero on" aria-label="On screen now">'
                f'<div class="brand"><p class="lbl">Getting ready</p>{chan}</div>'
                f'<p class="title">{_esc(what)}</p></section>')

    pct = min(100, max(0, int((cur.get("progress") or 0.0) * 100)))
    return (
        f'<section class="card hero on" aria-label="On screen now">'
        f'<div class="brand"><p class="lbl">Playing now</p>{chan}</div>'
        f'<h1 class="title">{_esc(cur.get("title") or "Unknown")}</h1>'
        f'<div class="track" role="img" aria-label="{pct}% watched">'
        f'<span style="width:{pct}%;background:var(--green)"></span></div>'
        f'<div class="btn-row">'
        f'<form method="POST" action="/">{_csrf_field()}'
        f'<input type="hidden" name="ch" value="{n}">'
        f'<button type="submit" name="action" value="skip" class="btn">'
        f"{_icon(_I_SKIP, 18)}Skip video</button></form>"
        f'<a class="btn btn-ghost" href="/ch/{n}">Up next</a></div>'
        f"</section>"
    )


def _hero_manual(status: dict, pause: dict | None) -> str:
    scene = status["scene"]
    since = _esc(status.get("since") or "")
    since_txt = f"Since {since}{_DOT}" if since else ""
    return (
        f'<section class="card hero manual" aria-label="TV is off">'
        f'<p class="lbl">TV is off · you switched it</p>'
        f'<div class="hero-row"><span class="badge">{_scene_icon(scene, 28)}</span>'
        f'<div><h1 class="big">{_esc(_scene_name(scene))}</h1>'
        f'<p class="note">{since_txt}stays on until you end it</p></div></div>'
        f'<form method="POST" action="/">{_csrf_field()}'
        f'<button type="submit" name="action" value="status_off" class="btn btn-main btn-amber">'
        f"{_icon(_I_POWER, 20, width=2.2)}End {_esc(_scene_name(scene).lower())}</button></form>"
        f'<p class="note">{_after_status_text(pause)}</p>'
        f"</section>"
    )


def _hero_scheduled(period: dict) -> str:
    scene = period["scene"]
    start = period["start_h"] * 60 + period["start_m"]
    end = period["end_h"] * 60 + period["end_m"]
    now = _now_minutes()
    left = max(0, end - now)
    pct = min(100, max(0, int((now - start) * 100 / max(1, end - start))))
    left_txt = f"{left} min left" if left < 60 else f"{left // 60} h {left % 60} min left"
    return (
        f'<section class="card hero sched" aria-label="TV is off">'
        f'<p class="lbl">TV is off · on the schedule</p>'
        f'<div class="hero-row"><span class="badge">{_scene_icon(scene, 28)}</span>'
        f'<div><h1 class="big">{_esc(_scene_name(scene))}</h1>'
        f'<p class="note">Back on at <b>{_hm(period["end_h"], period["end_m"])}</b> '
        f"· {left_txt}</p></div></div>"
        f'<div class="track" role="img" aria-label="{pct}% of this break done">'
        f'<span style="width:{pct}%;background:var(--lilac)"></span></div>'
        f'<form method="POST" action="/">{_csrf_field()}'
        f'<button type="submit" name="action" value="schedules_pause" class="btn btn-main">'
        f"{_icon(_I_PLAY, 20, width=2.2)}Turn the TV on</button></form>"
        f'<p class="note">This pauses all schedules until you resume them on the '
        f'<a href="/schedule">Schedule</a> tab.</p>'
        f"</section>"
    )


def _hero_power_off() -> str:
    return (
        '<section class="card hero" aria-label="TV is off">'
        '<p class="lbl">TV is off · power button</p>'
        '<h1 class="big">Switched off on the TV</h1>'
        '<p class="note">Press the power button on the TV to turn it back on.</p>'
        "</section>"
    )


def _channel_rows(tv: dict | None) -> str:
    """Compact channel list for the Now page."""
    on_screen = tv.get("channel") if tv and tv.get("state") == "playing" else None
    rows = []
    for i, ch in enumerate(CHANNELS):
        info = _channel_info(i) or {}
        cur = info.get("current") or {}
        if i == on_screen:
            sub = '<span class="s" style="color:var(--green)">On screen</span>'
        elif info and not info.get("feed_size"):
            sub = '<span class="s" style="color:var(--amber)">No videos yet — add a playlist</span>'
        elif cur.get("title"):
            sub = f'<span class="s">{_esc(cur["title"])}</span>'
        else:
            sub = '<span class="s">Loading…</span>'
        color = _safe_color(ch.get("color"))
        rows.append(
            f'<li><a class="row" href="/ch/{i + 1}">'
            f'<span class="num" style="background:{color}">{i + 1}</span>'
            f'<span class="main"><span class="t">{_esc(_channel_name(i))}</span>{sub}</span>'
            f"{_icon(_I_NEXT, 18, 'var(--dim)')}</a></li>"
        )
    return f'<ul class="list">{"".join(rows)}</ul>'


def _channel_chips() -> str:
    """While the TV is off: channels are still one tap away for editing."""
    chips = "".join(
        f'<a class="sw" href="/ch/{i + 1}"><span class="sq" '
        f'style="background:{_safe_color(ch.get("color"))}"></span><span>{_esc(_channel_name(i))}</span></a>'
        for i, ch in enumerate(CHANNELS)
    )
    return (f'<div class="card"><p class="note">Channels are paused while the TV is off. '
            f'You can still change what’s on them.</p><div class="switch">{chips}</div></div>')


def _build_now_page(message: str = "") -> str:
    tv = _tv_state()
    status = get_manual_status()
    pause = get_schedule_pause()
    period = current_off_period()      # manual status first, then the schedule

    brand = ('<div class="brand"><p class="lbl" style="color:var(--text);'
             'letter-spacing:2.4px">Toddler TV</p></div>')
    parts = [brand, _flash(message)]

    if status:
        parts += [_hero_manual(status, pause),
                  f'<section class="stack" aria-label="Switch scene"><p class="lbl">Or switch to…</p>'
                  f'{_scene_buttons(status["scene"], "chips")}</section>',
                  f'<section class="stack" aria-label="Channels"><p class="lbl">Channels</p>'
                  f"{_channel_chips()}</section>"]
    elif tv and tv.get("state") == "power_off":
        parts += [_hero_power_off(),
                  f'<section class="stack" aria-label="Channels"><p class="lbl">Channels</p>'
                  f"{_channel_rows(tv)}</section>"]
    elif period:
        later = [p for p in todays_periods() if p[0] > _now_minutes()]
        later_rows = "".join(
            f'<li><div class="row"><span class="main"><span class="t" style="font-family:var(--mono)">'
            f'{_hm(s // 60, s % 60)}–{_hm(e // 60, e % 60)}</span>'
            f'<span class="s">{_esc(_scene_name(sc))}</span></span></div></li>'
            for s, e, sc in later
        )
        parts += [_hero_scheduled(period),
                  f'<section class="stack" aria-label="Switch scene"><p class="lbl">'
                  f"Show a different scene</p>{_scene_buttons(period['scene'], 'chips')}</section>"]
        if later_rows:
            parts.append(f'<section class="stack" aria-label="Later today"><p class="lbl">Later today</p>'
                         f'<ul class="list">{later_rows}</ul></section>')
    else:
        if tv is None:
            parts.append(_offline())
        else:
            parts.append(_hero_playing(tv))
        if pause:
            next_line = ('<p class="note">Schedules are paused — the TV stays on. '
                         '<a href="/schedule">Resume</a></p>')
        else:
            nxt = next_off_period()
            next_line = (
                f'<p class="note">Next on the schedule: <b>{_esc(_scene_name(nxt["scene"]))}, '
                f'{_hm(nxt["start_h"], nxt["start_m"])}–{_hm(nxt["end_h"], nxt["end_m"])}</b>'
                f' · <a href="/schedule">Schedule</a></p>'
                if nxt else
                '<p class="note">No more breaks today · <a href="/schedule">Schedule</a></p>'
            )
        parts += [f'<section class="stack" aria-label="Turn the TV off"><p class="lbl">'
                  f"Turn the TV off for…</p>{_scene_buttons(None, 'tiles')}{next_line}</section>",
                  f'<section class="stack" aria-label="Channels"><p class="lbl">Channels</p>'
                  f"{_channel_rows(tv)}</section>"]

    return _shell("Now", "now", "".join(parts))


# ─────────────────────────────────────────────
#  CHANNEL PAGES
# ─────────────────────────────────────────────

def _channel_header(ch_idx: int, sub: str, count: int | None, is_active: bool | None) -> str:
    """Channel switcher, title line and the Playing / Library / Settings tabs."""
    target = {"": "", "add": "", "removed": "/library"}.get(sub, f"/{sub}")
    switch = ""
    for i, ch in enumerate(CHANNELS):
        color = _safe_color(ch.get("color"))
        border = f' style="border-color:{color}"' if i == ch_idx else ""
        switch += (f'<a class="sw" href="/ch/{i + 1}{target}"{_current(i == ch_idx)}{border}>'
                   f'<span class="sq" style="background:{color}"></span>'
                   f"<span>{_esc(_channel_name(i))}</span></a>")
    n = ch_idx + 1
    facts = [f"Channel {n}"]
    if count is not None:
        facts.append(f"{count} video{'' if count == 1 else 's'}")
    if is_active is True:
        facts.append('<span style="color:var(--green)">on screen now</span>')
    elif is_active is None:
        facts.append("TV app offline")
    tabs = [("", "Playing"), ("library", "Library"), ("settings", "Settings")]
    current = {"add": "", "removed": "library"}.get(sub, sub)
    seg = "".join(
        f'<a href="/ch/{n}{"/" + key if key else ""}"{_current(key == current)}>{label}</a>'
        for key, label in tabs
    )
    return (
        f'<nav class="switch" aria-label="Channels">{switch}</nav>'
        f'<div class="stack" style="gap:4px"><h1>{_esc(_channel_name(ch_idx))}</h1>'
        f'<p class="note dim">{_DOT.join(facts)}</p></div>'
        f'<nav class="seg" aria-label="Channel pages">{seg}</nav>'
    )


def _feed_status(item: dict) -> str:
    if item.get("injected"):
        return "Plays once · added from the remote"
    if item.get("downloaded"):
        return "Downloaded"
    if item.get("downloading"):
        return "Downloading"
    return "Streams from YouTube"


def _feed_row(n: int, item: dict, num: int) -> str:
    """One Up Next row; tapping it reveals its actions (details/summary, no JS)."""
    title = _esc(item.get("title") or "Unknown")
    pos = int(item.get("pos", -1))
    once = " once" if item.get("injected") else ""
    return (
        f'<li class="qitem{once}"><details class="more">'
        f'<summary class="row"><span class="main"><span class="t">{title}</span>'
        f'<span class="s">{_esc(_feed_status(item))}</span></span>'
        f'<span class="dots">{_DOTS}<span class="sr">Options</span></span></summary>'
        f'<form method="POST" action="/ch/{n}" class="menu">{_csrf_field()}'
        f'<input type="hidden" name="pos" value="{pos}">'
        f'<button type="submit" name="action" value="remove" class="btn btn-sm" style="flex:1">'
        f"Not this round</button>"
        f'<button type="submit" name="action" value="remove_perm" class="btn btn-sm btn-danger" '
        f'style="flex:1">Remove</button></form>'
        f"</details></li>"
    )


def _build_channel_page(ch_idx: int, info: dict | None, message: str = "",
                        lib: dict | None = None) -> str:
    """/ch/<n> — what's playing and what's next."""
    n = ch_idx + 1
    color = _safe_color(CHANNELS[ch_idx].get("color"))
    if not info:
        body = _channel_header(ch_idx, "", None, None) + _flash(message) + _offline()
        return _shell(_channel_name(ch_idx), "channels", body)

    parts = [_channel_header(ch_idx, "", int(info.get("feed_size") or 0),
                             bool(info.get("is_active"))), _flash(message)]

    cur = info.get("current")
    if cur:
        pct = min(100, max(0, int((cur.get("progress") or 0.0) * 100)))
        cur_pos = int(cur.get("pos", -1))
        parts.append(
            f'<section class="card" aria-label="Now playing"><p class="lbl">Now playing</p>'
            f'<p style="margin:0;font-size:17px;font-weight:700">{_esc(cur.get("title") or "Unknown")}</p>'
            f'<div class="track" role="img" aria-label="{pct}% watched">'
            f'<span style="width:{pct}%;background:{color}"></span></div>'
            f'<form method="POST" action="/ch/{n}" class="btn-row">{_csrf_field()}'
            f'<input type="hidden" name="pos" value="{cur_pos}">'
            f'<button type="submit" name="action" value="skip" class="btn">{_icon(_I_SKIP, 18)}Skip</button>'
            f'<button type="submit" name="action" value="remove_perm" class="btn btn-danger">'
            f"Remove</button></form></section>"
        )
    elif not info.get("feed_size"):
        parts.append(f'<div class="empty">No videos on this channel yet.<br>'
                     f'Add a YouTube playlist in <a href="/ch/{n}/settings">Settings</a>, '
                     f"or add a video below.</div>")
    else:
        parts.append('<div class="empty">Nothing playing yet — this channel is still loading…</div>')

    queue = info.get("queue") or []
    if queue:
        rows = "".join(_feed_row(n, item, i + 1) for i, item in enumerate(queue))
        parts.append(f'<section class="stack" aria-label="Up next"><div class="brand">'
                     f'<p class="lbl">Up next</p><p class="note dim" style="font-size:12px">shuffled</p></div>'
                     f'<ol class="list">{rows}</ol></section>')

    pinned = (f'<a class="btn btn-main" href="/ch/{n}/add">'
              f"{_icon(_I_PLUS, 20, width=2.4)}Add a video</a>")
    return _shell(_channel_name(ch_idx), "channels", "".join(parts), pinned)


def _build_add_page(ch_idx: int, message: str = "", url_value: str = "",
                    keep: bool = False) -> str:
    """/ch/<n>/add — one question: just this once, or keep it?"""
    n = ch_idx + 1
    body = (
        f'<a class="chan" href="/ch/{n}" style="font-size:15px;min-height:44px">'
        f"{_icon(_I_BACK, 20)}{_esc(_channel_name(ch_idx))}</a>"
        f"<h1>Add a video</h1>{_flash(message)}"
        f'<form method="POST" action="/ch/{n}/add" class="stack" style="gap:22px">{_csrf_field()}'
        f'<label class="field"><span class="lbl">YouTube link</span>'
        f'<input class="in" type="url" name="url" inputmode="url" autocomplete="off" spellcheck="false" '
        f'required placeholder="https://youtu.be/…" value="{_esc(url_value)}">'
        f'<span class="note dim">Share a video from the YouTube app and paste the link here.</span></label>'
        f'<fieldset class="stack"><legend class="lbl">How long should it stay?</legend>'
        f'<label class="opt"><input type="radio" name="keep" value="once"{"" if keep else " checked"}>'
        f'<span><b>Just this once</b><span class="note">Plays after the current video, then it’s '
        f"gone. Nothing is downloaded.</span></span></label>"
        f'<label class="opt"><input type="radio" name="keep" value="keep"{" checked" if keep else ""}>'
        f'<span><b>Keep it on this channel</b><span class="note">Plays next, downloads in the '
        f"background, and stays in the shuffle.</span></span></label></fieldset>"
        f'<button type="submit" name="action" value="add_video" class="btn btn-main">Play next</button>'
        f"</form>"
    )
    return _shell(f"Add a video — {_channel_name(ch_idx)}", "channels", body)


def _library_url(n: int, show: str, page: int) -> str:
    query = []
    if show:
        query.append(f"show={quote(show)}")
    if page > 1:
        query.append(f"page={page}")
    return f"/ch/{n}/library" + ("?" + "&".join(query) if query else "")


def _library_row(n: int, item: dict, action: str, label: str, form_url: str) -> str:
    vid = _esc(str(item.get("id") or ""))
    title = _esc(item.get("title") or vid)
    origin = item.get("from") or ""
    if origin == "added by hand":
        origin = "Added by you"
    elif origin and origin != "a removed playlist":
        origin = f"{origin} playlist"
    else:
        origin = "From a removed playlist"
    if item.get("downloaded"):
        state = "downloaded"
    elif item.get("downloading"):
        state = '<span style="color:var(--amber)">downloading</span>'
    else:
        state = "streams"
    btn_cls = "btn btn-sm btn-ghost" if action == "ignore" else "btn btn-sm"
    return (
        f'<li><form method="POST" action="{_esc(form_url)}" class="row">{_csrf_field()}'
        f'<input type="hidden" name="video" value="{vid}">'
        f'<span class="main"><span class="t">{title}</span>'
        f'<span class="s">{_esc(origin)} · {state}</span></span>'
        f'<button type="submit" name="action" value="{action}" class="{btn_cls}">{label}</button>'
        f"</form></li>"
    )


def _build_library_page(ch_idx: int, catalog: dict | None, message: str = "",
                        is_active: bool | None = None, show: str = "",
                        page: int = 1) -> str:
    """/ch/<n>/library — everything on the channel, or what was removed."""
    n = ch_idx + 1
    if catalog is None:
        body = _channel_header(ch_idx, "library", None, None) + _flash(message) + _offline()
        return _shell(f"Library — {_channel_name(ch_idx)}", "channels", body)

    videos = catalog.get("videos") or []
    removed = catalog.get("ignored") or []
    items = removed if show == "removed" else videos
    pages = max(1, -(-len(items) // PAGE_SIZE))
    page = min(page, pages)
    chunk = items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    form_url = _library_url(n, show, page)

    pills = (
        f'<nav class="pills" aria-label="Filter">'
        f'<a class="pill" href="{_library_url(n, "", 1)}"{_current(not show)}>'
        f"On the channel · {len(videos)}</a>"
        f'<a class="pill" href="{_library_url(n, "removed", 1)}"{_current(bool(show))}>'
        f"Removed · {len(removed)}</a></nav>"
    )
    if chunk:
        if show == "removed":
            rows = "".join(_library_row(n, v, "restore", "Restore", form_url) for v in chunk)
            hint = ('<p class="note dim">Removed videos stay off the channel, even if they are still '
                    "in a YouTube playlist. Restore puts one back and plays it next.</p>")
        else:
            rows = "".join(_library_row(n, v, "ignore", "Remove", form_url) for v in chunk)
            hint = ('<p class="note dim">Remove takes a video off this channel and deletes its '
                    "download. You can restore it from Removed.</p>")
        listing = f'<ul class="list">{rows}</ul>{hint}'
    elif show == "removed":
        listing = '<div class="empty">Nothing removed from this channel.</div>'
    else:
        listing = (f'<div class="empty">No videos yet — add a YouTube playlist in '
                   f'<a href="/ch/{n}/settings">Settings</a>.</div>')

    pager = ""
    if pages > 1:
        prev = (f'<a href="{_library_url(n, show, page - 1)}">Previous</a> · ' if page > 1 else "")
        nxt = (f' · <a href="{_library_url(n, show, page + 1)}">Next page</a>' if page < pages else "")
        pager = f'<p class="note dim">{prev}Page {page} of {pages}{nxt}</p>'

    sources = catalog.get("sources") or []
    if sources:
        feed_head = f"Fed by {len(sources)} YouTube playlist{'' if len(sources) == 1 else 's'}"
        feed_sub = "New videos are added automatically"
    else:
        feed_head = "No YouTube playlists yet"
        feed_sub = "Add one to fill this channel automatically"
    feed_card = (
        f'<a class="linkcard" href="/ch/{n}/settings">{_icon(_I_SYNC, 22, "var(--dim)")}'
        f'<span style="flex:1;display:flex;flex-direction:column">'
        f'<span style="font-weight:600;font-size:14px">{feed_head}</span>'
        f'<span class="note dim" style="font-size:12px">{feed_sub}</span></span>'
        f"{_icon(_I_NEXT, 18, 'var(--dim)')}</a>"
    )
    body = (_channel_header(ch_idx, "library", len(videos), is_active) + _flash(message)
            + pills + listing + pager + feed_card)
    return _shell(f"Library — {_channel_name(ch_idx)}", "channels", body)


# Swatches offered for a channel colour; the current colour is always kept.
_PALETTE = [("#FF6B6B", "Coral"), ("#4ECDC4", "Teal"), ("#4D96FF", "Blue"),
            ("#FFD93D", "Sun"), ("#6BCB77", "Green"), ("#B9A6FF", "Lilac"),
            ("#FF9F7A", "Peach"), ("#F78FD0", "Pink")]


def _build_settings_page(ch_idx: int, catalog: dict | None = None, message: str = "",
                         is_active: bool | None = None, lib: dict | None = None) -> str:
    """/ch/<n>/settings — playlists, the TV button, and merge (tucked away)."""
    n = ch_idx + 1
    ch = CHANNELS[ch_idx]
    name = _esc(_channel_name(ch_idx))
    emoji = _esc(ch.get("emoji", ""))
    color = _safe_color(ch.get("color"))

    parts = [_channel_header(ch_idx, "settings",
                             len(catalog.get("videos") or []) if catalog else None, is_active),
             _flash(message)]

    # ── YouTube playlists ────────────────────────────────────────────
    if catalog is None:
        parts.append(_offline())
    else:
        rows = ""
        for s in catalog.get("sources") or []:
            if s.get("last_sync"):
                checked = (f"{int(s.get('last_count') or 0)} videos{_DOT}"
                           f"checked {_esc(s['last_sync'][-5:])}")
            else:
                checked = "not checked yet"
            rows += (
                f'<li><form method="POST" action="/ch/{n}/settings" class="row">{_csrf_field()}'
                f'<input type="hidden" name="playlist" value="{_esc(str(s.get("playlist_id") or ""))}">'
                f'<span class="main"><span class="t">{_esc(s.get("title") or s.get("playlist_id") or "")}</span>'
                f'<span class="s">{checked}</span></span>'
                f'<button type="submit" name="action" value="remove_source" class="btn btn-sm btn-ghost">'
                f"Stop using</button></form></li>"
            )
        lib = lib or {}
        if lib.get("refreshing"):
            synced = "Checking right now…"
        elif lib.get("last_refresh"):
            synced = f"Last checked {lib['last_refresh']}" + (
                f" — {lib['summary']}" if lib.get("summary") else "")
        else:
            synced = "Not checked yet"
        parts.append(
            f'<section class="stack" aria-label="YouTube playlists"><p class="lbl">YouTube playlists</p>'
            f'<ul class="list">{rows}'
            f'<li><form method="POST" action="/ch/{n}/settings" class="row" style="gap:8px;padding:12px">'
            f'{_csrf_field()}<label style="flex:1"><span class="sr">Playlist link</span>'
            f'<input class="in" type="url" name="playlist" inputmode="url" autocomplete="off" '
            f'spellcheck="false" required placeholder="Paste a playlist link"></label>'
            f'<button type="submit" name="action" value="add_source" class="btn btn-main" '
            f'style="width:auto;min-height:50px;padding:0 18px">Add</button></form></li></ul>'
            f'<p class="note dim">New videos in these playlists join the channel automatically. '
            f"Removing a video on YouTube doesn’t remove it here.</p>"
            f'<form method="POST" action="/ch/{n}/settings" class="brand" style="gap:12px">{_csrf_field()}'
            f'<span class="note dim">{_esc(synced)}</span>'
            f'<button type="submit" name="action" value="refresh" class="btn btn-sm">'
            f"{_icon(_I_SYNC, 16)}Check now</button></form></section>"
        )

    # ── The button on the TV ─────────────────────────────────────────
    palette = list(_PALETTE)
    if color.upper() not in {c for c, _ in palette}:
        palette.insert(0, (color, "Current colour"))
    swatches = "".join(
        f'<label class="pick"><input type="radio" name="color" value="{c}"'
        f'{" checked" if c.upper() == color.upper() else ""}>'
        f'<span style="background:{c}"><span class="sr">{label}</span></span></label>'
        for c, label in palette
    )
    parts.append(
        f'<section class="stack" aria-label="Button on the TV"><p class="lbl">Button on the TV</p>'
        f'<form method="POST" action="/ch/{n}/settings" class="card">{_csrf_field()}'
        f'<div style="display:flex;gap:10px">'
        f'<label class="field" style="width:76px"><span class="note">Icon</span>'
        f'<input class="in" type="text" name="emoji" value="{emoji}" maxlength="8" '
        f'style="text-align:center"></label>'
        f'<label class="field" style="flex:1"><span class="note">Name</span>'
        f'<input class="in" type="text" name="name" value="{name}" maxlength="40" required></label></div>'
        f'<fieldset><legend class="note">Colour</legend><div class="swatches">{swatches}</div></fieldset>'
        f'<button type="submit" name="action" value="save_channel" class="btn">Save name and colour</button>'
        f"</form></section>"
    )

    # ── Merge — rare and drastic, so collapsed at the bottom ─────────
    options = "".join(
        f'<option value="{i + 1}">{_esc(_channel_name(i))}</option>'
        for i in range(N) if i != ch_idx
    )
    parts.append(
        f'<details class="danger"><summary>Merge into another channel…</summary>'
        f'<form method="POST" action="/ch/{n}/settings" class="body">{_csrf_field()}'
        f'<p class="note">Moves every video, removed video and YouTube playlist from <b>{name}</b> '
        f"into the channel you pick. Nothing is deleted. {name} is left empty, ready to reuse.</p>"
        f'<label class="field"><span class="note">Move everything into</span>'
        f'<select class="in" name="target">{options}</select></label>'
        f'<label style="display:flex;gap:10px;align-items:flex-start;font-size:14px">'
        f'<input type="checkbox" name="confirm" value="1" required '
        f'style="width:22px;height:22px;accent-color:var(--red)">'
        f"I understand {name} will be left empty</label>"
        f'<button type="submit" name="action" value="merge" class="btn btn-danger">Merge channel</button>'
        f"</form></details>"
    )
    return _shell(f"Settings — {_channel_name(ch_idx)}", "channels", "".join(parts))


# ─────────────────────────────────────────────
#  SCHEDULE PAGES
# ─────────────────────────────────────────────

_DAY_SHORT = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _days_text(days) -> str:
    s = sorted({d for d in days if 0 <= d <= 6})
    if not s or s == list(range(7)):
        return "every day"
    if s == [0, 1, 2, 3, 4]:
        return "Mon–Fri"
    if s == [5, 6]:
        return "weekends"
    return ", ".join(_DAY_NAMES[d] for d in s)


def _normalise(p) -> tuple:
    if len(p) >= 7:
        return tuple(p[:7])
    return (*p[:5], [], False)


def _timeline(paused: bool) -> str:
    segs = "".join(
        f'<span style="left:{s * 100 / 1440:.2f}%;width:{(e - s) * 100 / 1440:.2f}%"></span>'
        for s, e, _ in todays_periods()
    )
    now = _now_minutes()
    desc = ", ".join(f"{_hm(s // 60, s % 60)} to {_hm(e // 60, e % 60)}"
                     for s, e, _ in todays_periods()) or "none"
    label = f"TV off today: {desc}" + (" (schedules paused)" if paused else "")
    return (
        f'<div class="timeline{" paused" if paused else ""}" role="img" aria-label="{_esc(label)}">'
        f'{segs}<i style="left:{now * 100 / 1440:.2f}%"></i></div>'
        f'<div class="hours" aria-hidden="true"><span>00</span><span>06</span><span>12</span>'
        f"<span>18</span><span>24</span></div>"
    )


def _build_schedule_page(periods: list, message: str = "") -> str:
    """/schedule — pause switch, today at a glance, the period list."""
    pause = get_schedule_pause()
    status = get_manual_status()
    periods = [_normalise(p) for p in periods]

    since = _esc(pause.get("since") or "") if pause else ""
    sub = (f"Paused{f' since {since}' if since else ''} — the TV stays on. "
           "Untick to bring your periods back." if pause else
           "Keeps the TV on. Your periods stay exactly as they are.")
    check = (
        f'<form method="POST" action="/schedule">{_csrf_field()}'
        f'<button type="submit" name="action" '
        f'value="{"schedules_resume" if pause else "schedules_pause"}" class="checkbtn" '
        f'aria-pressed="{"true" if pause else "false"}">'
        f'<span class="box">{_icon(_I_CHECK, 18, width=3) if pause else ""}</span>'
        f'<span style="flex:1;display:flex;flex-direction:column;gap:3px">'
        f'<b>Pause all schedules</b><span class="note dim">{sub}</span></span></button></form>'
    )
    if status:
        check += (f'<p class="note">{_esc(_scene_name(status["scene"]))} is on right now '
                  f'— end it on the <a href="/">Now</a> tab.</p>')

    rows = []
    for i, (sh, sm, eh, em, scene, days, paused) in enumerate(periods):
        tag = '<span class="tag">Paused</span>' if paused else '<span class="tag on">On</span>'
        dim = ";color:var(--dim)" if paused else ""
        rows.append(
            f'<li><a class="row" href="/schedule/{i}">'
            f'<span class="main"><span class="t" style="font-family:var(--mono);font-size:15px{dim}">'
            f"{_hm(sh, sm)} – {_hm(eh, em)}</span>"
            f'<span class="s">{_esc(_scene_name(scene))} · {_esc(_days_text(days))}</span></span>'
            f"{tag}{_icon(_I_NEXT, 18, 'var(--dim)')}</a></li>"
        )
    listing = (f'<ul class="list">{"".join(rows)}</ul>' if rows else
               '<div class="empty">No off periods yet — the TV plays all day.</div>')
    legend = "Paused — nothing switches off" if pause else "TV off in purple"
    add = ("" if len(periods) >= MAX_PERIODS else
           f'<a class="btn" href="/schedule/new" style="border-style:dashed">'
           f"{_icon(_I_PLUS, 18, width=2.4)}Add an off period</a>")

    body = (
        f"<h1>Schedule</h1>{_flash(message)}{check}"
        f'<section class="stack" aria-label="Today"><div class="brand">'
        f'<p class="lbl">Today · {_WEEKDAYS[datetime.now().weekday()]}</p>'
        f'<p class="note dim" style="font-size:12px">{legend}</p></div>'
        f"{_timeline(bool(pause))}</section>"
        f'<section class="stack" aria-label="Off periods"><p class="lbl">Off periods</p>'
        f"{listing}{add}</section>"
    )
    return _shell("Schedule", "schedule", body)


def _build_period_page(index, period, message: str = "") -> str:
    """/schedule/<i> or /schedule/new — one off period."""
    if period is None:
        sh, sm, eh, em, scene, days, paused = 12, 0, 13, 0, "lunch", [], False
    else:
        sh, sm, eh, em, scene, days, paused = _normalise(period)
    target = "new" if index is None else str(index)
    days_set = set(days) if days else set(range(7))

    scenes = "".join(
        f'<label class="pick"><input type="radio" name="scene" value="{_esc(s)}"'
        f'{" checked" if s == scene else ""}><span>{_scene_icon(s, 20)}{_esc(_scene_name(s))}</span></label>'
        for s in _scene_order()
    )
    day_chips = "".join(
        f'<label class="pick"><input type="checkbox" name="days" value="{d}"'
        f'{" checked" if d in days_set else ""}><span>{lab}</span></label>'
        for d, lab in enumerate(_DAY_SHORT)
    )
    delete = ("" if index is None else
              '<button type="submit" name="action" value="delete" class="btn btn-danger" '
              'style="width:auto;padding:0 18px">Delete</button>')
    title = "New off period" if index is None else f"{_scene_name(scene)} period"
    body = (
        f'<a class="chan" href="/schedule" style="font-size:15px;min-height:44px">'
        f"{_icon(_I_BACK, 20)}Schedule</a><h1>{_esc(title)}</h1>{_flash(message)}"
        f'<form method="POST" action="/schedule/{target}" class="stack" style="gap:20px">{_csrf_field()}'
        f'<div style="display:flex;gap:10px;align-items:flex-end">'
        f'<label class="field" style="flex:1"><span class="lbl">TV off from</span>'
        f'<input class="in" type="time" name="start" value="{_hm(sh, sm)}" required></label>'
        f'<span aria-hidden="true" class="dim" style="padding-bottom:14px">to</span>'
        f'<label class="field" style="flex:1"><span class="lbl">Until</span>'
        f'<input class="in" type="time" name="end" value="{_hm(eh, em)}" required></label></div>'
        f'<fieldset><legend class="lbl">Show on the TV</legend><div class="tiles">{scenes}</div></fieldset>'
        f'<fieldset><legend class="lbl">Days</legend><div class="days">{day_chips}</div></fieldset>'
        f'<label style="display:flex;align-items:center;gap:12px;min-height:48px">'
        f'<input type="checkbox" name="paused" value="1"{" checked" if paused else ""} '
        f'style="width:22px;height:22px;accent-color:var(--green)">Pause just this period</label>'
        f'<div class="btn-row" style="flex:0">{delete}'
        f'<button type="submit" name="action" value="save" class="btn btn-main" style="flex:1">Save</button></div>'
        f"</form>"
    )
    return _shell(title, "schedule", body)


def _parse_period(first, params) -> tuple | str:
    """One period from the edit form, validated; a str is an error message."""
    try:
        sh, sm = (int(x) for x in first("start", "").split(":")[:2])
        eh, em = (int(x) for x in first("end", "").split(":")[:2])
        # Clamp out-of-range values rather than reject them (CWE-755)
        sh, sm = max(0, min(23, sh)), max(0, min(59, sm))
        eh, em = max(0, min(23, eh)), max(0, min(59, em))
    except (ValueError, TypeError):
        _log.warning("Malformed time in period form — rejected")
        return "❌ Enter a start and end time"
    if eh * 60 + em <= sh * 60 + sm:
        return ("❌ The end must be after the start. For an overnight break, "
                "add two periods (e.g. 19:30–23:59 and 00:00–06:50).")

    # Whitelist the scene against known keys (CWE-20)
    scene = first("scene", "sleeping")
    if scene not in SCENES:
        _log.warning("Unknown scene %r in period form — defaulting", scene[:40])
        scene = "sleeping"

    days_set = set()
    for raw in params.get("days", [])[:7]:
        try:
            d = int(raw)
        except (ValueError, TypeError):
            continue
        if 0 <= d <= 6:
            days_set.add(d)
    if not days_set:
        return "❌ Pick at least one day"
    # All 7 days = every day, stored as an empty list
    days = [] if days_set == set(range(7)) else sorted(days_set)
    paused = first("paused", "") == "1"
    return (sh, sm, eh, em, scene, days, paused)


# ─────────────────────────────────────────────
#  HTTP HANDLER
# ─────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        path, query = _split(self.path)
        if path == "/":
            self._respond(200, _build_now_page())
            return
        sched = _parse_schedule_route(path)
        if sched is not None:
            self._respond(200, self._schedule_page(sched))
            return
        route = _parse_channel_route(path)
        if route is not None:
            self._respond(200, self._channel_page(route[0], "", route[1], query))
            return
        self._respond(404, self._not_found())

    def do_POST(self):
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
            safe_peer = re.sub(r"[^\w.:\[\]-]", "?", self.client_address[0])
            _log.warning("CSRF token mismatch from %s", safe_peer)
            self._respond(403, "<h1>403 Forbidden</h1>")
            return

        action = first("action")
        path, query = _split(self.path)

        # ── 3. Route to the page the form came from ───────────────────────────
        if path == "/":
            self._respond(200, _build_now_page(self._handle_now(action, first)))
            return
        sched = _parse_schedule_route(path)
        if sched is not None:
            self._handle_schedule_post(sched, action, first, params)
            return
        route = _parse_channel_route(path)
        if route is not None:
            self._handle_channel_post(route[0], action, first, route[1], query)
            return
        self._respond(404, self._not_found())

    def _not_found(self) -> str:
        return _shell("Not found", "now",
                      '<h1>Page not found</h1><p class="note"><a href="/">Back to Now</a></p>')

    # ── Now ───────────────────────────────────────────────────────────────────

    def _handle_override(self, action: str, first) -> str | None:
        """Manual status and the schedule pause; None if not one of these."""
        if action == "schedules_pause":
            pause_schedules()
            return "⏸ Schedules paused — the TV stays on until you resume"
        if action == "schedules_resume":
            resume_schedules()
            return "▶ Schedules resumed"
        if action == "status_off":
            clear_manual_status()
            if get_schedule_pause():
                return "✅ Ended — schedules are paused, so the TV is back on"
            return "✅ Back to the schedule"
        if action == "status_on":
            scene = first("scene", "")
            # Reject unknowns at the gate — never fall back to some other scene
            # for a request that switches the TV off (CWE-20).
            if scene not in SCENES:
                _log.warning("Unknown manual status scene %r — ignored", scene[:40])
                return "❌ Unknown status"
            set_manual_status(scene)
            return f"✅ {_scene_name(scene)} is on — the schedule is paused until you end it"
        return None

    def _handle_now(self, action: str, first) -> str:
        message = self._handle_override(action, first)
        if message is not None:
            return message
        if action == "skip":
            try:
                n = int(first("ch", "0"))
            except ValueError:
                n = 0
            # Bound the channel number (CWE-400 / CWE-20)
            if not 1 <= n <= N:
                return "❌ Unknown channel"
            fn = _cb("skip")
            if fn:
                fn(n - 1)
            return "⏭ Skipped to the next video"
        return ""

    # ── Schedule ──────────────────────────────────────────────────────────────

    def _schedule_page(self, target, message: str = "") -> str:
        periods = get_periods()
        if target == "list":
            return _build_schedule_page(periods, message)
        if target == "new":
            return _build_period_page(None, None, message)
        if target >= len(periods):
            return _build_schedule_page(periods, "❌ That period no longer exists")
        return _build_period_page(target, periods[target], message)

    def _handle_schedule_post(self, target, action: str, first, params) -> None:
        def reply(html: str) -> None:
            self._respond(200, html)

        if target == "list":
            message = self._handle_override(action, first) or ""
            reply(_build_schedule_page(get_periods(), message))
            return

        periods = [_normalise(p) for p in get_periods()]
        if target != "new" and target >= len(periods):
            reply(_build_schedule_page(periods, "❌ That period no longer exists"))
            return

        if action == "delete" and target != "new":
            periods.pop(target)
            save_periods(periods)
            reply(_build_schedule_page(periods, "✅ Period deleted"))
            return

        if action != "save":
            reply(self._schedule_page(target))
            return

        parsed = _parse_period(first, params)
        if isinstance(parsed, str):
            reply(self._schedule_page(target, parsed))
            return
        if target == "new":
            # Hard cap on the number of periods (CWE-400)
            if len(periods) >= MAX_PERIODS:
                reply(_build_schedule_page(periods, f"❌ At most {MAX_PERIODS} periods"))
                return
            periods.append(parsed)
            message = "✅ Period added"
        else:
            periods[target] = parsed
            message = "✅ Period saved"
        save_periods(periods)
        reply(_build_schedule_page(periods, message))

    # ── Channels ──────────────────────────────────────────────────────────────

    def _channel_page(self, ch_idx: int, message: str = "", sub: str = "",
                      query: dict | None = None) -> str:
        """Render /ch/<n>[/sub] from a fresh snapshot of app state."""
        if sub == "add":
            return _build_add_page(ch_idx, message)
        lib = _call("library") if sub in ("", "settings") else None
        info = _channel_info(ch_idx)
        if sub == "":
            return _build_channel_page(ch_idx, info, message, lib)
        is_active = bool(info.get("is_active")) if info else None
        catalog = _call("catalog", ch_idx)
        if sub == "settings":
            return _build_settings_page(ch_idx, catalog, message, is_active, lib)
        show, page = _library_view(sub, query or {})
        return _build_library_page(ch_idx, catalog, message, is_active, show, page)

    def _handle_channel_post(self, ch_idx: int, action: str, first,
                             sub: str = "", query: dict | None = None) -> None:
        """Handle POST requests for /ch/<n>[/sub]; re-renders the same page."""
        def reply(message: str = "", page: str | None = None) -> None:
            self._respond(200, self._channel_page(ch_idx, message, page if page is not None else sub, query))

        # Enum-style dispatch: an unrecognised action falls through to a plain
        # re-render rather than being coerced into something (CWE-20).
        if action == "skip":
            fn = _cb("skip")
            if fn:
                fn(ch_idx)
            reply("⏭ Skipped to the next video")
            return

        if action == "add_video":
            # Whitelist: only an explicit "keep" persists; anything else plays once.
            mode = "add" if first("keep", "once") == "keep" else "queue"
            message, ok = self._handle_channel_add(ch_idx, mode, first)
            if ok:
                reply(message, "")
            else:
                self._respond(200, _build_add_page(ch_idx, message, first("url", "")[:300],
                                                   keep=mode == "add"))
            return

        if action in ("queue", "add"):
            reply(self._handle_channel_add(ch_idx, action, first)[0])
            return

        if action in ("remove", "remove_perm"):
            reply(self._handle_channel_remove(ch_idx, action, first))
            return

        if action in ("ignore", "restore"):
            reply(self._handle_catalog_video(ch_idx, action, first))
            return

        if action in ("add_source", "remove_source"):
            reply(self._handle_source(ch_idx, action, first))
            return

        if action == "save_channel":
            reply(self._handle_save_channel(ch_idx, first))
            return

        if action == "merge":
            reply(self._handle_merge(ch_idx, first))
            return

        if action == "refresh":
            fn = _cb("refresh")
            started = bool(fn and fn(ch_idx))
            reply("⟳ Checking YouTube playlists… reload in a moment"
                  if started else "⏳ A check is already running")
            return

        reply()

    def _handle_channel_add(self, ch_idx: int, action: str, first) -> tuple[str, bool]:
        """action=add keeps the video on the channel; action=queue plays it once."""
        # Whitelist at the gate: host and video ID must both check out (CWE-20)
        cleaned = validate_youtube_url(first("url", ""))
        if cleaned is None:
            return "❌ That isn’t a YouTube video link — paste a youtube.com or youtu.be link", False

        if action == "add":
            fn = _cb("add")
            ok_msg = "✅ Added to the channel — playing next, downloading now"
        else:
            fn = _cb("play_next")
            ok_msg = "✅ Playing next — just this once"

        success = bool(fn and fn(ch_idx, cleaned))
        return (ok_msg, True) if success else ("❌ Could not add that video", False)

    def _handle_channel_remove(self, ch_idx: int, action: str, first) -> str:
        """Drop one feed position.  remove_perm moves it to Removed."""
        # Bound the index before it reaches the app (CWE-400 / CWE-20)
        try:
            pos = int(first("pos", "-1"))
        except ValueError:
            pos = -1
        if pos < 0 or pos > MAX_POSITION:
            return "❌ Unknown video"

        permanent = action == "remove_perm"
        fn = _cb("remove")
        success = bool(fn and fn(ch_idx, pos, permanent))
        if success:
            return ("\U0001f5d1 Removed — you can restore it from the Library"
                    if permanent else "✕ Skipped for this round")
        return "❌ Could not remove that video"

    def _handle_catalog_video(self, ch_idx: int, action: str, first) -> str:
        """ignore / restore one video, named by its ID (validated, CWE-20)."""
        cleaned = validate_youtube_url(first("video", ""))
        if cleaned is None:
            return "❌ Unknown video"
        fn = _cb(action)
        success = bool(fn and fn(ch_idx, cleaned))
        if action == "ignore":
            return ("\U0001f5d1 Removed — you can restore it from Removed"
                    if success else "❌ Could not remove that video")
        return ("✅ Restored — playing next, downloading now"
                if success else "❌ Could not restore that video")

    def _handle_source(self, ch_idx: int, action: str, first) -> str:
        """add_source takes a pasted link; remove_source an ID from our own form."""
        raw = first("playlist", "")
        if action == "add_source":
            # Host whitelist + ID allowlist; the URL is rebuilt from the ID (CWE-20)
            playlist_id = validate_youtube_playlist_url(raw)
            if playlist_id is None:
                return ("❌ That isn’t a YouTube playlist link — it should "
                        "contain list=…")
            fn = _cb("add_source")
            if fn and fn(ch_idx, playlist_id):
                return "✅ Playlist added — fetching its videos now, reload in a moment"
            return "❌ Could not add that playlist"

        if not _SAFE_PLAYLIST_ID.match(raw):
            return "❌ Unknown playlist"
        fn = _cb("remove_source")
        if fn and fn(ch_idx, raw):
            return "✅ Playlist removed — its videos stay on the channel"
        return "❌ Could not remove that playlist"

    def _handle_save_channel(self, ch_idx: int, first) -> str:
        name = first("name", "").strip()
        emoji = first("emoji", "").strip()
        color = first("color", "").strip()
        # Length and format checks at the gate; library.py cleans again (CWE-20)
        if not name or len(name) > 40 or len(emoji) > 8:
            return "❌ Name must be 1–40 characters and the icon at most 8"
        if not _HEX_RE.match(color):
            return "❌ Pick a colour"
        fn = _cb("save_channel")
        if fn and fn(ch_idx, name, emoji, color):
            return "✅ Channel saved"
        return "❌ Could not save the channel"

    def _handle_merge(self, ch_idx: int, first) -> str:
        if first("confirm", "") != "1":
            return "❌ Tick the box to confirm the merge"
        try:
            target = int(first("target", "0"))
        except ValueError:
            target = 0
        # Bound the channel number (CWE-400 / CWE-20)
        if not 1 <= target <= N or target - 1 == ch_idx:
            return "❌ Pick another channel to merge into"
        fn = _cb("merge")
        if fn and fn(ch_idx, target - 1):
            return f"✅ Everything moved into {_channel_name(target - 1)} — this channel is now empty"
        return "❌ Could not merge the channels"

    # ── Response plumbing ─────────────────────────────────────────────────────

    def _send_security_headers(self) -> None:
        """Emit hardening headers on every response (L2)."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; "
            "style-src 'unsafe-inline'; "
            "font-src 'none'; "
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

def get_remote_url() -> str:
    """Return the URL shown on the QR code."""
    ip = _get_local_ip()
    return f"http://{ip}:{PORT}/"


def start_web_remote(port: int = PORT) -> None:
    """Start the web remote server in a daemon thread."""
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")
    ip = _get_local_ip()

    def _run():
        server = HTTPServer(("0.0.0.0", port), _Handler)
        print(f"[remote] Web remote: http://{ip}:{port}")
        server.serve_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
