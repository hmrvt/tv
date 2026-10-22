"""
Toddler TV - Web Remote
A tiny HTTP server for controlling the off-period schedule and channel playlists
from your phone.  Runs on port 8080 by default.
Open http://<media-pc-ip>:8080 from any device on the same WiFi.
"""

import html as _html
import logging
import re
import secrets
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

from schedule import get_periods, save_periods
from config import SCENES, CHANNELS, N
from media import validate_youtube_url, validate_youtube_playlist_url, _SAFE_PLAYLIST_ID

# ─────────────────────────────────────────────
#  SECURITY CONSTANTS
# ─────────────────────────────────────────────

MAX_BODY     = 16_384   # 16 KB hard cap on POST body (CWE-400)
MAX_PERIODS  = 48       # hard cap on period count per request (CWE-400)
MAX_POSITION = 100_000  # hard cap on a feed position index (CWE-400)

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
_callbacks: dict = {}


def register_channel_callbacks(callbacks: dict) -> None:
    """Register app callbacks for channel playlist operations."""
    global _callbacks
    _callbacks = dict(callbacks or {})


def _cb(name):
    """Return a registered callback, or None if the app hasn't provided it."""
    return _callbacks.get(name)


def _channel_info(ch_idx: int) -> dict | None:
    fn = _cb("get")
    return fn(ch_idx) if fn else None


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


# Channel sub-pages, whitelisted in the route itself (CWE-20).
_SUBPAGES = ("videos", "ignored", "sources", "settings")
_CH_PATH_RE = re.compile(r'^/ch/(\d{1,2})(?:/(' + "|".join(_SUBPAGES) + r'))?$')


def _parse_channel_route(path: str) -> tuple[int, str] | None:
    """Return (0-indexed channel, sub-page) for /ch/N[/sub], or None."""
    m = _CH_PATH_RE.match(path)
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= N:
        return n - 1, m.group(2) or ""
    return None


# ─────────────────────────────────────────────
#  SCHEDULE PAGE
# ─────────────────────────────────────────────

_DAY_LABELS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

_SCENE_META = {
    "sleeping":     ("\U0001f319", "#4ECDC4"),
    "lunch":        ("\U0001f37d", "#FFD93D"),
    "fixing":       ("\U0001f527", "#FF6B6B"),
    "kita_goodbye": ("\U0001f392", "#6BCB77"),
    "tubby":        ("\U0001f6c1", "#87CEEB"),
}

_esc = _html.escape

_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


def _safe_color(value, fallback: str = "#00ff88") -> str:
    """Whitelist a colour before it reaches a style attribute (CWE-20).

    Channel colours come from channels.json, which is hand-edited; anything
    that is not a plain hex literal is replaced rather than sanitised.
    """
    if isinstance(value, str) and _HEX_RE.match(value.strip()):
        return value.strip()
    return fallback


_COMMON_CSS = (
    "*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}\n"
    ":root{"
    "--bg:#080416;--surface:#120a2c;--surface2:#1c0f44;"
    "--border:#2e1c55;--border2:#43306e;"
    "--green:#00ff88;--green-dim:#00301c;--red:#ff6b6b;--red-dim:#3a1520;"
    "--teal:#4ECDC4;--yellow:#FFD93D;"
    "--text:#e6e6f4;--text-mid:#a0a0c0;--text-dim:#7a7a9c;"
    "--mono:'Courier New',Courier,monospace;"
    "--ui:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    "--tap:46px}\n"
    "html{height:100%;-webkit-text-size-adjust:100%;overflow-x:hidden}\n"
    "body{font-family:var(--ui);font-size:15px;line-height:1.5;"
    "background:var(--bg);color:var(--text);min-height:100vh;"
    "padding-bottom:calc(var(--tap) + 34px)}\n"
    ".skip{position:absolute;left:-9999px;top:0;background:var(--green);"
    "color:#04101a;padding:10px 16px;font-weight:700;z-index:99}\n"
    ".skip:focus{left:8px;top:8px}\n"
    "header.top{background:var(--surface);border-bottom:1px solid var(--border);"
    "position:sticky;top:0;z-index:20}\n"
    ".brand{display:flex;align-items:center;gap:10px;padding:12px 16px 10px;"
    "max-width:600px;margin:0 auto}\n"
    ".brand h1{flex:0 1 auto;min-width:0;font-family:var(--mono);font-size:15px;letter-spacing:2px;"
    "text-transform:uppercase;color:var(--green);font-weight:700}\n"
    ".brand .where{margin-left:auto;flex:0 0 auto;font-size:11px;color:var(--text-dim);"
    "font-family:var(--mono);letter-spacing:1px;text-align:right}\n"
    "nav.tabs{display:flex;gap:2px;overflow-x:auto;padding:0 10px;"
    "max-width:600px;margin:0 auto;scrollbar-width:none}\n"
    "nav.tabs::-webkit-scrollbar{display:none}\n"
    "nav.tabs a{flex:0 0 auto;display:flex;flex-direction:column;"
    "align-items:center;gap:2px;min-width:74px;padding:7px 10px 9px;"
    "text-decoration:none;color:var(--text-dim);font-size:11px;"
    "border-bottom:3px solid transparent;transition:color .15s,border-color .15s}\n"
    "nav.tabs a .ico{font-size:19px;line-height:1}\n"
    "nav.tabs a .lbl{max-width:80px;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap;letter-spacing:.5px}\n"
    "nav.tabs a:hover{color:var(--text-mid)}\n"
    "nav.tabs a[aria-current='page']{color:var(--text);"
    "border-bottom-color:var(--tabcol,var(--green))}\n"
    "main{max-width:600px;margin:0 auto;padding:16px}\n"
    "h2.section{font-family:var(--mono);font-size:11px;letter-spacing:2.5px;"
    "text-transform:uppercase;color:var(--text-dim);margin:22px 0 10px;"
    "display:flex;align-items:center;gap:10px}\n"
    "h2.section:first-child{margin-top:4px}\n"
    "h2.section::after{content:'';flex:1;height:1px;background:var(--border)}\n"
    ".note{font-size:12px;color:var(--text-dim);line-height:1.6;margin:8px 2px 0}\n"
    ".flash{display:block;background:var(--green-dim);border:1px solid var(--green);"
    "color:var(--green);padding:12px 14px;border-radius:10px;font-size:13px;"
    "margin-bottom:16px}\n"
    ".flash.err{background:var(--red-dim);border-color:var(--red);color:var(--red)}\n"
    ".card{background:var(--surface);border:1px solid var(--border);"
    "border-left:4px solid var(--accent,var(--border2));"
    "border-radius:12px;padding:14px;margin-bottom:12px}\n"
    ".card.muted{opacity:.55}\n"
    ".card-head{display:flex;align-items:center;gap:10px;margin-bottom:12px}\n"
    ".card-head .ico{font-size:20px;line-height:1}\n"
    ".card-head h3{flex:0 1 auto;min-width:0;font-family:var(--mono);font-size:13px;letter-spacing:1px;"
    "color:var(--text);font-weight:700}\n"
    ".field{display:block;margin-bottom:12px}\n"
    ".field>.lab,legend.lab{display:block;font-size:11px;letter-spacing:1.5px;"
    "text-transform:uppercase;color:var(--text-dim);margin-bottom:6px;"
    "font-family:var(--mono)}\n"
    "input[type=time],input[type=text],select{width:100%;min-height:var(--tap);"
    "padding:10px 12px;background:var(--surface2);color:var(--text);"
    "border:1px solid var(--border2);border-radius:8px;font-size:15px;"
    "font-family:var(--ui);outline:none;min-width:0}\n"
    "input[type=time]{font-family:var(--mono)}\n"
    "input:focus-visible,select:focus-visible,button:focus-visible,"
    "a:focus-visible,summary:focus-visible{outline:2px solid var(--teal);"
    "outline-offset:2px}\n"
    ".times{display:flex;gap:10px;align-items:flex-end}\n"
    ".times .field{flex:1;min-width:0;margin-bottom:0}\n"
    ".times .sep{padding-bottom:13px;color:var(--text-dim);font-size:18px}\n"
    "fieldset{border:0;margin:12px 0}\n"
    ".chips{display:flex;gap:6px;flex-wrap:wrap}\n"
    ".chips input{position:absolute;opacity:0;width:0;height:0}\n"
    ".chips label{display:flex;align-items:center;justify-content:center;"
    "min-width:42px;min-height:40px;padding:0 4px;border-radius:8px;"
    "background:var(--surface2);border:1px solid var(--border2);"
    "color:var(--text-dim);font-size:12px;font-family:var(--mono);"
    "cursor:pointer;letter-spacing:.5px}\n"
    ".chips input:checked+label{background:var(--green-dim);"
    "border-color:var(--green);color:var(--green)}\n"
    ".chips input:focus-visible+label{outline:2px solid var(--teal);outline-offset:2px}\n"
    ".toggle{margin-left:auto;display:flex;align-items:center;gap:8px;"
    "font-size:12px;color:var(--text-dim);cursor:pointer;min-height:40px}\n"
    ".toggle input{width:20px;height:20px;accent-color:var(--yellow)}\n"
    ".btn{display:flex;align-items:center;justify-content:center;gap:8px;"
    "width:100%;min-height:var(--tap);padding:12px 16px;border-radius:9px;"
    "border:1px solid var(--border2);background:var(--surface2);color:var(--text);"
    "font-family:var(--ui);font-size:14px;font-weight:600;cursor:pointer;"
    "text-decoration:none;transition:background .15s,border-color .15s,color .15s}\n"
    ".btn:hover{border-color:var(--text-dim)}\n"
    ".btn-primary{background:var(--green);border-color:var(--green);color:#04101a}\n"
    ".btn-primary:hover{background:#4dffab;border-color:#4dffab}\n"
    ".btn-accent{background:var(--accent,var(--teal));"
    "border-color:var(--accent,var(--teal));color:#04101a}\n"
    ".btn-danger{color:var(--red);border-color:#5a2633;background:transparent}\n"
    ".btn-danger:hover{background:var(--red-dim);border-color:var(--red)}\n"
    ".btn-sm{min-height:38px;padding:8px 12px;font-size:13px;width:auto;"
    "flex:0 0 auto}\n"
    ".btn-row{display:flex;gap:10px}\n"
    ".btn-row .btn{flex:1}\n"
    ".bar{position:fixed;left:0;right:0;bottom:0;z-index:20;"
    "background:var(--surface);border-top:1px solid var(--border);"
    "padding:10px 16px calc(10px + env(safe-area-inset-bottom,0px))}\n"
    ".bar-inner{max-width:600px;margin:0 auto;display:flex;gap:10px;"
    "align-items:center}\n"
    ".bar .btn{flex:1}\n"
    ".bar .info{font-size:12px;color:var(--text-dim);font-family:var(--mono);"
    "letter-spacing:1px;display:flex;align-items:center;gap:8px}\n"
    ".dot{width:8px;height:8px;border-radius:50%;background:var(--green);"
    "box-shadow:0 0 8px var(--green);flex-shrink:0}\n"
    ".dot.idle{background:var(--text-dim);box-shadow:none}\n"
    ".empty{text-align:center;padding:30px 20px;border:1px dashed var(--border2);"
    "border-radius:12px;color:var(--text-dim);font-size:13px;margin-bottom:12px}\n"
    ".empty .ico{font-size:40px;display:block;margin-bottom:10px}\n"
    "details{background:var(--surface);border:1px solid var(--border);"
    "border-radius:12px;margin-bottom:12px;overflow:hidden}\n"
    "summary{min-height:var(--tap);display:flex;align-items:center;gap:8px;"
    "padding:12px 14px;cursor:pointer;font-size:13px;color:var(--text-mid);"
    "list-style:none}\n"
    "summary::-webkit-details-marker{display:none}\n"
    "summary::after{content:'\\25be';margin-left:auto;color:var(--text-dim)}\n"
    "details[open] summary::after{content:'\\25b4'}\n"
    "details .body{padding:0 14px 14px}\n"
)

_PAGE_CSS = (
    ".np{background:var(--surface);border:1px solid var(--border);"
    "border-left:4px solid var(--accent);border-radius:12px;padding:16px;"
    "margin-bottom:12px}\n"
    ".np-title{font-size:16px;font-weight:700;line-height:1.35;margin-bottom:4px}\n"
    ".np-sub{font-size:12px;color:var(--text-dim);margin-bottom:12px}\n"
    ".track{height:8px;background:var(--surface2);border-radius:4px;"
    "overflow:hidden;margin-bottom:6px}\n"
    ".track>span{display:block;height:100%;background:var(--accent);border-radius:4px}\n"
    ".pct{font-size:11px;color:var(--text-dim);font-family:var(--mono);"
    "margin-bottom:14px}\n"
    ".queue{list-style:none;background:var(--surface);border:1px solid var(--border);"
    "border-radius:12px;overflow:hidden;margin-bottom:8px}\n"
    ".queue li{border-bottom:1px solid var(--border)}\n"
    ".queue li:last-child{border-bottom:none}\n"
    ".qrow{display:flex;align-items:center;gap:10px;padding:10px 12px}\n"
    ".qnum{font-family:var(--mono);font-size:12px;color:var(--text-dim);"
    "min-width:20px;text-align:right;flex-shrink:0}\n"
    ".qtitle{flex:1;min-width:0;font-size:14px;overflow:hidden;text-overflow:ellipsis;"
    "white-space:nowrap}\n"
    ".tag{font-size:10px;font-family:var(--mono);letter-spacing:.5px;"
    "padding:3px 6px;border-radius:5px;flex-shrink:0;"
    "border:1px solid var(--border2);color:var(--text-dim)}\n"
    ".tag.ok{color:var(--green);border-color:#0a5c38}\n"
    ".tag.dl{color:var(--yellow);border-color:#6b5a14}\n"
    ".tag.once{color:var(--teal);border-color:#1f6b66}\n"
    ".legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:11px;"
    "color:var(--text-dim);margin:0 2px 16px}\n"
    ".legend b{color:var(--text-mid);font-weight:600}\n"
    ".choice{background:var(--surface);border:1px solid var(--border);"
    "border-radius:12px;padding:14px;margin-bottom:12px}\n"
    ".choice+.choice{margin-top:-4px}\n"
    ".lib{font-size:13px;color:var(--text-mid);line-height:1.8;"
    "margin-bottom:12px}\n"
    ".lib b{color:var(--text);font-weight:600}\n"
    ".subtabs{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;"
    "margin:0 0 16px}\n"
    ".subtabs a{flex:0 0 auto;padding:8px 12px;border-radius:8px;"
    "border:1px solid var(--border2);color:var(--text-dim);text-decoration:none;"
    "font-size:13px;white-space:nowrap}\n"
    ".subtabs a[aria-current]{color:var(--text);border-color:var(--accent);"
    "background:var(--surface2)}\n"
    ".qmain{flex:1;min-width:0;display:flex;flex-direction:column}\n"
    ".qfrom{font-size:11px;color:var(--text-dim);overflow:hidden;"
    "text-overflow:ellipsis;white-space:nowrap}\n"
    "input[type=color]{height:var(--tap);padding:4px}\n"
    ".confirm{display:flex;gap:10px;align-items:flex-start;font-size:13px;"
    "color:var(--text-mid);margin:12px 0}\n"
    ".confirm input{width:20px;height:20px;flex-shrink:0;accent-color:var(--red)}\n"
)


# ─────────────────────────────────────────────
#  SHARED PAGE CHROME
# ─────────────────────────────────────────────

def _nav(active: str) -> str:
    """Primary tab bar, on every page, so nav is always one tap away."""
    items = [("/", "\U0001f4c5", "Schedule", "schedule", "#00ff88")]
    for i, ch in enumerate(CHANNELS):
        items.append((
            f"/ch/{i + 1}",
            ch.get("emoji", "\U0001f4fa"),
            ch.get("name", f"CH {i + 1}"),
            f"ch{i + 1}",
            _safe_color(ch.get("color")),
        ))

    out = []
    for href, icon, label, key, color in items:
        current = ' aria-current="page"' if key == active else ""
        out.append(
            f'<a href="{href}"{current} style="--tabcol:{color}">'
            f'<span class="ico" aria-hidden="true">{icon}</span>'
            f'<span class="lbl">{_esc(label)}</span></a>'
        )
    return f'<nav class="tabs" aria-label="Sections">{"".join(out)}</nav>'


def _flash(message: str) -> str:
    """Status banner.  A leading cross marks the message as an error."""
    if not message:
        return ""
    err = message.startswith("\u274c")
    cls = "flash err" if err else "flash"
    role = "alert" if err else "status"
    return f'<div class="{cls}" role="{role}">{_esc(message)}</div>'


def _shell(title: str, active: str, body: str,
           where: str = "", bar: str = "", css: str = "") -> str:
    """Document chrome: head, header + tabs, main, optional sticky bottom bar.

    `bar` is left empty by pages that render their own bar inside a form, so
    that its buttons submit that form.
    """
    bar_html = (f'<div class="bar"><div class="bar-inner">{bar}</div></div>\n'
                if bar else "")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="dark">\n'
        f"<title>{_esc(title)}</title>\n"
        "<style>\n" + _COMMON_CSS + css + "</style>\n"
        "</head>\n"
        "<body>\n"
        '<a class="skip" href="#main">Skip to content</a>\n'
        '<header class="top">\n'
        '<div class="brand"><span aria-hidden="true">\U0001f4fa</span>'
        "<h1>Toddler TV</h1>"
        f'<span class="where">{_esc(where)}</span></div>\n'
        f"{_nav(active)}\n"
        "</header>\n"
        f'<main id="main">{body}</main>\n'
        f"{bar_html}"
        "</body>\n"
        "</html>"
    )


# ─────────────────────────────────────────────
#  SCHEDULE PAGE
# ─────────────────────────────────────────────

def _period_card(i: int, period) -> str:
    """One off-period.  Every control carries a visible label."""
    if len(period) >= 7:
        sh, sm, eh, em, scene, days, paused = period[:7]
    else:
        sh, sm, eh, em, scene = period[:5]
        days, paused = [], False

    emoji, accent = _SCENE_META.get(scene, ("\u23f0", "#00ff88"))
    accent = _safe_color(accent)

    options = "".join(
        f'<option value="{s}"{" selected" if s == scene else ""}>'
        f'{_esc(SCENES[s]["label"])}</option>'
        for s in SCENES
    )

    days_set = set(days) if days else set(range(7))
    chips = "".join(
        f'<input type="checkbox" id="d{d}_{i}" name="days_{i}" value="{d}"'
        f'{" checked" if d in days_set else ""}>'
        f'<label for="d{d}_{i}">{lab}</label>'
        for d, lab in enumerate(_DAY_LABELS)
    )

    label_txt = _esc(SCENES.get(scene, {}).get("label", scene))
    return (
        f'<section class="card{" muted" if paused else ""}" style="--accent:{accent}">'
        f'<div class="card-head">'
        f'<span class="ico" aria-hidden="true">{emoji}</span>'
        f"<h3>Period {i + 1}</h3>"
        f'<label class="toggle" for="paused_{i}">'
        f'<input type="checkbox" id="paused_{i}" name="paused_{i}" value="1"'
        f'{" checked" if paused else ""}>Paused</label>'
        f"</div>"
        f'<div class="times">'
        f'<label class="field"><span class="lab">TV off from</span>'
        f'<input type="time" name="start_{i}" value="{sh:02d}:{sm:02d}"></label>'
        f'<span class="sep" aria-hidden="true">\u2192</span>'
        f'<label class="field"><span class="lab">until</span>'
        f'<input type="time" name="end_{i}" value="{eh:02d}:{em:02d}"></label>'
        f"</div>"
        f'<fieldset><legend class="lab">Repeat on</legend>'
        f'<div class="chips">{chips}</div></fieldset>'
        f'<label class="field"><span class="lab">Show on screen</span>'
        f'<select name="scene_{i}">{options}</select></label>'
        f'<button type="submit" name="delete" value="{i}" class="btn btn-danger">'
        f'<span aria-hidden="true">\U0001f5d1</span> Delete this period</button>'
        f'<p class="note">While this period runs the TV shows '
        f"&ldquo;{label_txt}&rdquo; instead of videos.</p>"
        f"</section>"
    )


def _build_page(periods: list, message: str = "") -> str:
    cards = "".join(_period_card(i, p) for i, p in enumerate(periods))
    if not cards:
        cards = (
            '<div class="empty">'
            '<span class="ico" aria-hidden="true">\U0001f916</span>'
            "No off-periods yet \u2014 the TV will play all day.<br>"
            "Tap <strong>Add period</strong> below to set quiet time."
            "</div>"
        )

    active = sum(1 for p in periods if not (p[6] if len(p) >= 7 else False))

    # The sticky bar is inside the form so its buttons submit it, so the
    # shell's own bar slot stays empty on this page.
    body = (
        f"{_flash(message)}"
        f'<form method="POST" action="/">'
        f'<input type="hidden" name="count" value="{len(periods)}">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<h2 class="section">Off periods</h2>'
        f'<p class="note" style="margin:0 2px 14px">Times when the TV shows a '
        f"robot scene instead of videos.</p>"
        f"{cards}"
        f'<div class="bar"><div class="bar-inner">'
        f'<button type="submit" name="action" value="add" class="btn">'
        f'<span aria-hidden="true">+</span> Add period</button>'
        f'<button type="submit" name="action" value="save" class="btn btn-primary">'
        f"Save changes</button>"
        f"</div></div>"
        f"</form>"
    )
    return _shell("Schedule \u2014 Toddler TV Remote", "schedule", body,
                  where=f"{active} of {len(periods)} on")


# ─────────────────────────────────────────────
#  CHANNEL PLAYLIST PAGE
# ─────────────────────────────────────────────

def _feed_row(n: int, item: dict, num: int) -> str:
    """One Up Next row.  Actions are spelled out rather than bare icons."""
    title = _esc(item.get("title") or "Unknown")
    pos = int(item.get("pos", -1))

    if item.get("injected"):
        tag = '<span class="tag once">ONCE</span>'
    elif item.get("downloaded"):
        tag = '<span class="tag ok">SAVED</span>'
    elif item.get("downloading"):
        tag = '<span class="tag dl">GETTING</span>'
    else:
        tag = '<span class="tag">STREAM</span>'

    return (
        f"<li>"
        f'<form method="POST" action="/ch/{n}" class="qrow">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<input type="hidden" name="pos" value="{pos}">'
        f'<span class="qnum">{num}</span>'
        f'<span class="qtitle">{title}</span>'
        f"{tag}"
        f'<button type="submit" name="action" value="remove" class="btn btn-sm">'
        f"Skip</button>"
        f'<button type="submit" name="action" value="remove_perm" '
        f'class="btn btn-sm btn-danger">Remove</button>'
        f"</form></li>"
    )


def _library_strip(lib: dict | None, n: int, feed_size: int,
                   action: str = "") -> str:
    """Download / sync status, plus the manual sync button."""
    action = action or f"/ch/{n}"
    lib = lib or {}
    pending = int(lib.get("pending") or 0)
    current = lib.get("current")

    if current:
        downloads = f"downloading, {pending + 1} left"
    elif pending:
        downloads = f"{pending} waiting to download"
    else:
        downloads = "all downloaded"

    last = lib.get("last_refresh") or ""
    summary = lib.get("summary") or ""
    if lib.get("refreshing"):
        synced = "syncing right now\u2026"
    elif last:
        synced = f"last synced {last}" + (f" ({summary})" if summary else "")
    else:
        synced = "not synced yet"

    return (
        '<div class="card" style="--accent:var(--border2)">'
        f'<p class="lib"><b>{feed_size}</b> videos on this channel<br>'
        f"{_esc(downloads)}<br>{_esc(synced)}</p>"
        f'<form method="POST" action="{action}">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        '<button type="submit" name="action" value="refresh" class="btn">'
        '<span aria-hidden="true">\u27f3</span> Check YouTube playlists for new videos'
        "</button></form></div>"
    )


def _subtabs(n: int, active: str) -> str:
    """Second-level tabs for one channel's pages."""
    items = [("", "Now playing"), ("videos", "Videos"), ("ignored", "Ignored"),
             ("sources", "YouTube playlists"), ("settings", "Settings")]
    links = []
    for sub, label in items:
        href = f"/ch/{n}/{sub}" if sub else f"/ch/{n}"
        current = ' aria-current="page"' if sub == active else ""
        links.append(f'<a href="{href}"{current}>{label}</a>')
    return f'<nav class="subtabs" aria-label="Channel pages">{"".join(links)}</nav>'


def _channel_shell(ch_idx: int, sub: str, body: str, is_active: bool | None) -> str:
    """Chrome shared by every /ch/N page.  is_active=None means app offline."""
    ch = CHANNELS[ch_idx]
    name = _esc(ch.get("name", f"Channel {ch_idx + 1}"))
    color = _safe_color(ch.get("color"))
    n = ch_idx + 1
    body = (f'<div style="--accent:{color}">{_subtabs(n, sub)}</div>' + body)
    if is_active is None:
        return _shell(f"{name} \u2014 Toddler TV Remote", f"ch{n}", body,
                      where="offline", css=_PAGE_CSS)
    bar = (
        f'<span class="info"><span class="dot{"" if is_active else " idle"}">'
        f"</span>{name} \u00b7 "
        f'{"on screen now" if is_active else "in the background"}</span>'
    )
    return _shell(f"{name} \u2014 Toddler TV Remote", f"ch{n}", body,
                  where=f"CH {n}", bar=bar, css=_PAGE_CSS)


def _offline_body(message: str) -> str:
    return (
        _flash(message)
        + '<div class="empty">'
        '<span class="ico" aria-hidden="true">\U0001f4f4</span>'
        "The TV app is not running.<br>"
        "Start it to manage this channel.</div>"
    )


def _status_tag(item: dict) -> str:
    if item.get("downloaded"):
        return '<span class="tag ok">SAVED</span>'
    if item.get("downloading"):
        return '<span class="tag dl">GETTING</span>'
    return '<span class="tag">STREAM</span>'


def _catalog_row(n: int, sub: str, item: dict, action: str, label: str,
                 btn_class: str) -> str:
    """One video in the Videos / Ignored lists, keyed by video ID."""
    vid = _esc(str(item.get("id") or ""))
    title = _esc(item.get("title") or vid)
    origin = _esc(item.get("from") or "")
    return (
        "<li>"
        f'<form method="POST" action="/ch/{n}/{sub}" class="qrow">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<input type="hidden" name="video" value="{vid}">'
        f'<span class="qmain"><span class="qtitle">{title}</span>'
        f'<span class="qfrom">from {origin}</span></span>'
        f"{_status_tag(item)}"
        f'<button type="submit" name="action" value="{action}" '
        f'class="btn btn-sm {btn_class}">{label}</button>'
        "</form></li>"
    )


def _build_videos_page(ch_idx: int, catalog: dict | None, message: str = "",
                       is_active: bool | None = None) -> str:
    n = ch_idx + 1
    if catalog is None:
        return _channel_shell(ch_idx, "videos", _offline_body(message), None)
    videos = catalog.get("videos") or []
    if videos:
        rows = "".join(_catalog_row(n, "videos", v, "ignore", "Remove", "btn-danger")
                       for v in videos)
        listing = f'<ul class="queue">{rows}</ul>'
    else:
        listing = ('<div class="empty">No videos yet \u2014 add a YouTube playlist '
                   f'under <a href="/ch/{n}/sources">YouTube playlists</a>, or add a '
                   f'video on <a href="/ch/{n}">Now playing</a>.</div>')
    body = (
        f"{_flash(message)}"
        f'<h2 class="section">Videos on this channel ({len(videos)})</h2>'
        f"{listing}"
        '<p class="note"><b>Remove</b> takes a video off this channel and deletes '
        "its download. It moves to <b>Ignored</b>, so a YouTube playlist won't "
        "add it back, and you can restore it from there.</p>"
    )
    return _channel_shell(ch_idx, "videos", body, is_active)


def _build_ignored_page(ch_idx: int, catalog: dict | None, message: str = "",
                        is_active: bool | None = None) -> str:
    n = ch_idx + 1
    if catalog is None:
        return _channel_shell(ch_idx, "ignored", _offline_body(message), None)
    ignored = catalog.get("ignored") or []
    if ignored:
        rows = "".join(_catalog_row(n, "ignored", v, "restore", "Restore", "")
                       for v in ignored)
        listing = f'<ul class="queue">{rows}</ul>'
    else:
        listing = '<div class="empty">Nothing ignored on this channel.</div>'
    body = (
        f"{_flash(message)}"
        f'<h2 class="section">Ignored videos ({len(ignored)})</h2>'
        f"{listing}"
        '<p class="note">Videos removed from this channel. They stay off it even '
        "if they are still in a YouTube playlist. <b>Restore</b> puts one back "
        "on the channel, plays it next and downloads it again.</p>"
    )
    return _channel_shell(ch_idx, "ignored", body, is_active)


def _source_card(n: int, src: dict) -> str:
    pid = _esc(str(src.get("playlist_id") or ""))
    title = _esc(src.get("title") or pid)
    synced = src.get("last_sync") or ""
    detail = (f"last checked {_esc(synced)} \u00b7 {int(src.get('last_count') or 0)} "
              "videos in the playlist" if synced else "not checked yet")
    return (
        '<div class="card" style="--accent:var(--border2)">'
        f'<p class="lib"><b>{title}</b><br>{detail}</p>'
        f'<form method="POST" action="/ch/{n}/sources">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<input type="hidden" name="playlist" value="{pid}">'
        '<button type="submit" name="action" value="remove_source" '
        'class="btn btn-danger">Stop using this playlist</button>'
        "</form></div>"
    )


def _build_sources_page(ch_idx: int, catalog: dict | None, message: str = "",
                        is_active: bool | None = None,
                        lib: dict | None = None) -> str:
    n = ch_idx + 1
    if catalog is None:
        return _channel_shell(ch_idx, "sources", _offline_body(message), None)
    sources = catalog.get("sources") or []
    cards = "".join(_source_card(n, s) for s in sources) or (
        '<div class="empty">No YouTube playlists yet.</div>')
    color = _safe_color(CHANNELS[ch_idx].get("color"))
    body = (
        f"{_flash(message)}"
        f'<h2 class="section">YouTube playlists ({len(sources)})</h2>'
        '<p class="note" style="margin:0 2px 14px">New videos added to these '
        "playlists on YouTube are added to this channel automatically. Taking a "
        "video out of a playlist on YouTube does not remove it here.</p>"
        f"{cards}"
        f'<h2 class="section">Add a YouTube playlist</h2>'
        f'<form method="POST" action="/ch/{n}/sources">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<label class="field"><span class="lab">Playlist link</span>'
        f'<input type="text" name="playlist" inputmode="url" autocomplete="off" '
        f'spellcheck="false" placeholder="https://youtube.com/playlist?list=\u2026">'
        f"</label>"
        f'<button type="submit" name="action" value="add_source" '
        f'class="btn btn-accent" style="--accent:{color}">'
        f'<span aria-hidden="true">+</span> Add playlist</button>'
        '<p class="note">Its videos are added to the channel straight away, '
        "except any you have ignored.</p></form>"
        f'<h2 class="section">Sync</h2>'
        f'{_library_strip(lib, n, len(catalog.get("videos") or []), f"/ch/{n}/sources")}'
    )
    return _channel_shell(ch_idx, "sources", body, is_active)


def _build_settings_page(ch_idx: int, message: str = "",
                         is_active: bool | None = None) -> str:
    n = ch_idx + 1
    ch = CHANNELS[ch_idx]
    name = _esc(ch.get("name", f"Channel {n}"))
    emoji = _esc(ch.get("emoji", "\U0001f4fa"))
    color = _safe_color(ch.get("color"))
    # <input type=color> only takes #rrggbb.
    picker = color if len(color) == 7 else "#00ff88"

    options = "".join(
        f'<option value="{i + 1}">{_esc(other.get("emoji", ""))} '
        f'{_esc(other.get("name", f"Channel {i + 1}"))}</option>'
        for i, other in enumerate(CHANNELS) if i != ch_idx
    )
    body = (
        f"{_flash(message)}"
        f'<h2 class="section">Channel button</h2>'
        f'<form method="POST" action="/ch/{n}/settings" class="card" '
        f'style="--accent:{color}">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<label class="field"><span class="lab">Name</span>'
        f'<input type="text" name="name" value="{name}" maxlength="40" required>'
        f"</label>"
        f'<label class="field"><span class="lab">Emoji</span>'
        f'<input type="text" name="emoji" value="{emoji}" maxlength="8"></label>'
        f'<label class="field"><span class="lab">Colour</span>'
        f'<input type="color" name="color" value="{picker}"></label>'
        f'<button type="submit" name="action" value="save_channel" '
        f'class="btn btn-primary">Save</button>'
        f"</form>"
        f'<h2 class="section">Merge into another channel</h2>'
        f'<form method="POST" action="/ch/{n}/settings" class="card" '
        f'style="--accent:var(--red)">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<p class="note" style="margin:0 0 12px">Moves every video, ignored '
        f"video and YouTube playlist from <b>{name}</b> into the channel you pick. "
        f"Nothing is deleted. {name} is left empty, ready to rename and fill "
        f"with new playlists.</p>"
        f'<label class="field"><span class="lab">Move everything into</span>'
        f'<select name="target">{options}</select></label>'
        f'<label class="confirm"><input type="checkbox" name="confirm" value="1" '
        f"required>I understand {name} will be left empty</label>"
        f'<button type="submit" name="action" value="merge" class="btn btn-danger">'
        f"Merge channel</button>"
        f"</form>"
    )
    return _channel_shell(ch_idx, "settings", body, is_active)


def _build_channel_page(ch_idx: int, info: dict | None, message: str = "",
                        lib: dict | None = None) -> str:
    ch = CHANNELS[ch_idx]
    color = _safe_color(ch.get("color"))
    n = ch_idx + 1

    if not info:
        return _channel_shell(ch_idx, "", _offline_body(message), None)

    # ── Now playing ──────────────────────────────────────────────────
    cur = info.get("current")
    if cur:
        title = _esc(cur.get("title") or "Unknown")
        uploader = _esc(cur.get("channel") or "")
        pct = min(100, max(0, int((cur.get("progress") or 0.0) * 100)))
        cur_pos = int(cur.get("pos", -1))
        now_html = (
            f'<form method="POST" action="/ch/{n}">'
            f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
            f'<input type="hidden" name="pos" value="{cur_pos}">'
            f'<div class="np" style="--accent:{color}">'
            f'<p class="np-title">{title}</p>'
            + (f'<p class="np-sub">{uploader}</p>' if uploader else "")
            + f'<div class="track"><span style="width:{pct}%"></span></div>'
            f'<p class="pct">{pct}% watched</p>'
            f'<div class="btn-row">'
            f'<button type="submit" name="action" value="skip" class="btn">'
            f'<span aria-hidden="true">\u23ed</span> Skip</button>'
            f'<button type="submit" name="action" value="remove_perm" '
            f'class="btn btn-danger">'
            f'<span aria-hidden="true">\U0001f5d1</span> Remove</button>'
            f"</div></div></form>"
        )
    else:
        now_html = ('<div class="empty">Nothing playing yet \u2014 '
                    "this channel is still loading\u2026</div>")

    # ── Up next ──────────────────────────────────────────────────────
    queue = info.get("queue", [])
    if queue:
        rows = "".join(_feed_row(n, item, i + 1) for i, item in enumerate(queue))
        queue_html = (
            f'<ul class="queue">{rows}</ul>'
            '<p class="legend">'
            "<span><b>Skip</b> \u2014 just this time round</span>"
            "<span><b>Remove</b> \u2014 move to Ignored and delete the download</span>"
            "</p>"
            '<p class="legend">'
            "<span><b>SAVED</b> downloaded</span>"
            "<span><b>GETTING</b> downloading</span>"
            "<span><b>STREAM</b> plays from YouTube</span>"
            "<span><b>ONCE</b> plays once only</span>"
            "</p>"
        )
    else:
        queue_html = '<div class="empty">Nothing queued up yet.</div>'

    # ── Add a video ──────────────────────────────────────────────────
    add_html = (
        f'<form method="POST" action="/ch/{n}">'
        f'<input type="hidden" name="csrf" value="{_CSRF_TOKEN}">'
        f'<label class="field"><span class="lab">YouTube link</span>'
        f'<input type="text" name="url" inputmode="url" autocomplete="off" '
        f'spellcheck="false" placeholder="https://youtube.com/watch?v=\u2026">'
        f"</label>"
        f'<div class="choice">'
        f'<button type="submit" name="action" value="add" class="btn btn-accent" '
        f'style="--accent:{color}">'
        f'<span aria-hidden="true">+</span> Add to this channel</button>'
        f'<p class="note">Downloads it and keeps it in the rotation for good.</p>'
        f"</div>"
        f'<div class="choice">'
        f'<button type="submit" name="action" value="queue" class="btn">'
        f'<span aria-hidden="true">\u25b6</span> Play once, then forget</button>'
        f'<p class="note">Plays straight after the current video. Nothing is '
        f"downloaded or saved.</p>"
        f"</div></form>"
    )

    body = (
        f"{_flash(message)}"
        f'<h2 class="section">Now playing</h2>{now_html}'
        f'<h2 class="section">Up next ({len(queue)})</h2>{queue_html}'
        f'<h2 class="section">Add a video</h2>{add_html}'
        f'<h2 class="section">Library</h2>'
        f'{_library_strip(lib, n, int(info.get("feed_size") or 0))}'
    )
    return _channel_shell(ch_idx, "", body, bool(info.get("is_active")))


# ─────────────────────────────────────────────
#  HTTP HANDLER
# ─────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        route = _parse_channel_route(self.path)
        if route is not None:
            ch_idx, sub = route
            html = self._channel_page(ch_idx, "", sub)
        else:
            periods = get_periods()
            html = _build_page(periods)
        self._respond(200, html)

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

        # ── 3. Route channel POST ─────────────────────────────────────────────
        route = _parse_channel_route(self.path)
        if route is not None:
            self._handle_channel_post(route[0], action, first, route[1])
            return

        # ── 4. Hard-cap period count (CWE-400) ────────────────────────────────
        try:
            count = min(int(first("count", "0")), MAX_PERIODS)
        except ValueError:
            count = 0

        # ── 5. Parse periods with full validation ─────────────────────────────
        _valid_scenes = set(SCENES.keys())
        _all_days = {0, 1, 2, 3, 4, 5, 6}
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

            # Days: collect checked day values, validate each is 0–6
            raw_days = params.get(f"days_{i}", [])
            days_set = set()
            for raw in raw_days:
                try:
                    d = int(raw)
                    if 0 <= d <= 6:
                        days_set.add(d)
                except (ValueError, TypeError):
                    pass
            # All 7 days checked = every day → store as empty list
            days = [] if days_set == _all_days else sorted(days_set)

            # Paused: checkbox present with value "1" means paused
            paused = first(f"paused_{i}", "") == "1"

            periods.append((sh, sm, eh, em, scene, days, paused))

        # ── 6. Action dispatch ────────────────────────────────────────────────
        delete_idx = first("delete", "")
        if delete_idx != "":
            try:
                idx = int(delete_idx)
            except ValueError:
                idx = -1
            if 0 <= idx < len(periods):
                periods.pop(idx)
            save_periods(periods)
            html = _build_page(periods, "✅ Period deleted")
            self._respond(200, html)
            return

        if action == "add":
            periods.append((12, 0, 13, 0, "lunch", [], False))
            html = _build_page(periods)
            self._respond(200, html)

        elif action == "save":
            save_periods(periods)
            html = _build_page(periods, "✅ Schedule saved!")
            self._respond(200, html)

        else:
            html = _build_page(periods)
            self._respond(200, html)

    def _channel_page(self, ch_idx: int, message: str = "", sub: str = "") -> str:
        """Render /ch/<n>[/sub] from a fresh snapshot of app state."""
        lib = None
        if sub in ("", "sources"):
            lib_fn = _cb("library")
            if lib_fn:
                try:
                    lib = lib_fn()
                except Exception:
                    lib = None
        if sub == "":
            return _build_channel_page(ch_idx, _channel_info(ch_idx), message, lib)

        info = _channel_info(ch_idx)
        is_active = bool(info.get("is_active")) if info else None
        if sub == "settings":
            return _build_settings_page(ch_idx, message, is_active)

        catalog_fn = _cb("catalog")
        catalog = catalog_fn(ch_idx) if catalog_fn else None
        if sub == "videos":
            return _build_videos_page(ch_idx, catalog, message, is_active)
        if sub == "ignored":
            return _build_ignored_page(ch_idx, catalog, message, is_active)
        return _build_sources_page(ch_idx, catalog, message, is_active, lib)

    def _handle_channel_post(self, ch_idx: int, action: str, first,
                             sub: str = "") -> None:
        """Handle POST requests for /ch/<n>[/sub]; re-renders the same page."""
        def reply(message: str = "") -> None:
            self._respond(200, self._channel_page(ch_idx, message, sub))

        # Enum-style dispatch: an unrecognised action falls through to a plain
        # re-render rather than being coerced into something (CWE-20).
        if action == "skip":
            fn = _cb("skip")
            if fn:
                fn(ch_idx)
            reply("⏭ Skipped to next video")
            return

        if action in ("queue", "add"):
            reply(self._handle_channel_add(ch_idx, action, first))
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
                  if started else "⏳ A sync is already running")
            return

        reply()

    def _handle_channel_add(self, ch_idx: int, action: str, first) -> str:
        """action=add keeps the video on the channel; action=queue plays it once."""
        # Whitelist at the gate: host and video ID must both check out (CWE-20)
        cleaned = validate_youtube_url(first("url", ""))
        if cleaned is None:
            return "❌ Invalid YouTube URL — paste a youtube.com or youtu.be link"

        if action == "add":
            fn = _cb("add")
            ok_msg = "✅ Added to the channel — playing next, downloading now"
        else:
            fn = _cb("play_next")
            ok_msg = "✅ Playing next — this once only"

        success = bool(fn and fn(ch_idx, cleaned))
        return ok_msg if success else "❌ Could not add that video"

    def _handle_channel_remove(self, ch_idx: int, action: str, first) -> str:
        """Drop one feed position.  remove_perm moves it to Ignored."""
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
            return ("\U0001f5d1 Removed — moved to Ignored, download deleted"
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
            return ("\U0001f5d1 Removed — moved to Ignored, download deleted"
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
                return ("❌ That isn't a YouTube playlist link — it should "
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
            return "❌ Name must be 1–40 characters and the emoji at most 8"
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
            dst = CHANNELS[target - 1].get("name", f"Channel {target}")
            return f"✅ Everything moved into {dst} — this channel is now empty"
        return "❌ Could not merge the channels"

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
