"""
Toddler TV - Download Diagnostics
Checks whether each video in channels.json can be downloaded without
actually downloading anything.  Produces a detailed log file you can
share to diagnose failures.

Usage:
  py -3.12 diagnose_downloads.py                     (uses cookies.txt)
  py -3.12 diagnose_downloads.py --browser firefox    (live browser cookies)
  py -3.12 diagnose_downloads.py --channel "Songs"    (one channel only)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from media import FFMPEG_LOCATION, clean_url, get_video_id, _js_runtime_args

PYTHON       = sys.executable
COOKIES_FILE = "cookies.txt"
CHANNELS_FILE = "channels.json"
# Exact format string the downloader uses
DOWNLOAD_FORMAT = (
    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
    "/bestvideo[height<=1080]+bestaudio"
    "/best"
)

BROWSER = None  # set by CLI args

# ─────────────────────────────────────────────
#  LOGGING SETUP
# ─────────────────────────────────────────────

_ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = Path(f"diagnose_{_ts}.log")

_log = logging.getLogger("diagnose")
_log.setLevel(logging.DEBUG)
_log.propagate = False

# File handler — full detail, UTF-8
_fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                                   datefmt="%H:%M:%S"))
_log.addHandler(_fh)

# Console handler — INFO and above only
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(message)s"))
_log.addHandler(_ch)

def _section(title: str):
    _log.debug("")
    _log.debug("═" * 70)
    _log.debug("  %s", title)
    _log.debug("═" * 70)

def _separator():
    _log.debug("─" * 70)


# ─────────────────────────────────────────────
#  COOKIE / AUTH HELPERS  (mirrors downloader.py)
# ─────────────────────────────────────────────

def _cookie_args() -> list[str]:
    if BROWSER:
        return ["--cookies-from-browser", BROWSER]
    return ["--cookies", COOKIES_FILE]


def _auth_args() -> list[str]:
    return [*_cookie_args(), *_js_runtime_args()]


def _ffmpeg_args() -> list[str]:
    return ["--ffmpeg-location", FFMPEG_LOCATION] if FFMPEG_LOCATION else []


# ─────────────────────────────────────────────
#  SYSTEM CHECKS
# ─────────────────────────────────────────────

def check_system() -> bool:
    """Log environment details and return False if a fatal dependency is missing."""
    _section("SYSTEM CHECK")
    ok = True

    # Python
    _log.debug("Python  : %s  (%s)", sys.version.split()[0], sys.executable)

    # yt-dlp version
    try:
        r = subprocess.run(
            [PYTHON, "-m", "yt_dlp", "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, encoding="utf-8",
        )
        ytdlp_ver = r.stdout.strip() or r.stderr.strip() or "unknown"
        _log.debug("yt-dlp  : %s", ytdlp_ver)
        _log.info("  yt-dlp     %s", ytdlp_ver)
    except Exception as e:
        _log.error("yt-dlp not found: %s", e)
        _log.info("  yt-dlp     MISSING — install with: pip install yt-dlp")
        ok = False

    # ffmpeg
    if FFMPEG_LOCATION:
        exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        try:
            r = subprocess.run(
                [os.path.join(FFMPEG_LOCATION, exe), "-version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10,
            )
            first_line = (r.stdout or r.stderr or b"").decode("utf-8", errors="replace").split("\n")[0]
            _log.debug("ffmpeg  : %s  (dir: %s)", first_line.strip(), FFMPEG_LOCATION)
            _log.info("  ffmpeg     %s", first_line.strip()[:60])
        except Exception as e:
            _log.warning("ffmpeg check failed: %s", e)
            _log.info("  ffmpeg     check failed: %s", e)
    else:
        _log.warning("ffmpeg not found — video+audio merge will be unavailable")
        _log.info("  ffmpeg     NOT FOUND  (video+audio merge will fail)")

    # JS runtime (node / deno / bun)
    js_args = _js_runtime_args()
    if js_args:
        runtime = js_args[1] if len(js_args) > 1 else "unknown"
        _log.debug("JS runtime: %s (found)", runtime)
        _log.info("  JS runtime %s  (found)", runtime)
    else:
        _log.warning("No JS runtime found — yt-dlp may fail on YouTube since ~2026")
        _log.info("  JS runtime NOT FOUND  (install node/deno/bun if downloads fail)")

    # Cookies
    if BROWSER:
        _log.debug("Cookies : live from browser: %s", BROWSER)
        _log.info("  cookies    live from browser (%s)", BROWSER)
    elif os.path.exists(COOKIES_FILE):
        size = os.path.getsize(COOKIES_FILE)
        _log.debug("Cookies : %s  (%d bytes)", COOKIES_FILE, size)
        _log.info("  cookies    %s  (%d bytes)", COOKIES_FILE, size)
        if size < 100:
            _log.warning("cookies.txt is very small (%d bytes) — may be empty/invalid", size)
            _log.info("  WARNING: cookies.txt looks empty or truncated")
    else:
        _log.warning("No cookies file found and no --browser specified")
        _log.info("  cookies    NOT FOUND  (age-restricted/member videos will fail)")

    _log.debug("Format  : %s", DOWNLOAD_FORMAT)
    return ok


# ─────────────────────────────────────────────
#  PLAYLIST EXPANSION
# ─────────────────────────────────────────────

def expand_playlist(playlist_url: str) -> list[str]:
    _log.info("    expanding playlist ...")
    _log.debug("expand_playlist: %s", playlist_url)
    try:
        r = subprocess.run(
            [
                PYTHON, "-m", "yt_dlp",
                *_auth_args(),
                "--flat-playlist",
                "--print", "url",
                "--no-warnings",
                *_ffmpeg_args(),
                playlist_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120, encoding="utf-8", errors="replace",
        )
        for line in (r.stderr or "").splitlines():
            if line.strip():
                _log.debug("  [playlist stderr] %s", line)
        urls = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if not line.startswith("http"):
                line = f"https://www.youtube.com/watch?v={line}"
            urls.append(line)
        _log.debug("expand_playlist: found %d URLs", len(urls))
        return urls
    except Exception as e:
        _log.error("expand_playlist failed: %s", e)
        return []


# ─────────────────────────────────────────────
#  PER-VIDEO DIAGNOSIS
# ─────────────────────────────────────────────

# Phrases in yt-dlp stderr that indicate specific failure modes.
_KNOWN_ERRORS = [
    ("Sign in to confirm",          "AGE_RESTRICTED   — cookies needed or video requires sign-in"),
    ("members-only",                "MEMBERS_ONLY     — channel membership required"),
    ("Private video",               "PRIVATE          — video is private"),
    ("Video unavailable",           "UNAVAILABLE      — video deleted or region-blocked"),
    ("not available in your country","GEO_BLOCKED     — region restriction"),
    ("Requested format is not available", "FORMAT_MISSING   — selected format not offered"),
    ("Unable to extract",           "EXTRACTION_FAIL  — yt-dlp could not parse the page"),
    ("rate-limited",                "RATE_LIMITED     — YouTube is throttling requests"),
    ("HTTP Error 403",              "HTTP_403         — access denied (bad cookies?)"),
    ("HTTP Error 429",              "HTTP_429         — too many requests"),
    ("Unable to download",          "NETWORK_ERROR    — network or DNS failure"),
    ("ffmpeg",                      "FFMPEG_ISSUE     — ffmpeg warning or error"),
]

def _classify_stderr(stderr: str) -> list[str]:
    """Return list of matched error labels from stderr text."""
    found = []
    lower = stderr.lower()
    for phrase, label in _KNOWN_ERRORS:
        if phrase.lower() in lower:
            found.append(label)
    return found


def diagnose_video(channel_name: str, youtube_url: str, index: int, total: int) -> dict:
    """
    Run yt-dlp --simulate --verbose on one video.
    Returns a result dict: {url, status, format_selected, errors, duration_s}.
    """
    label = f"[{index}/{total}]"
    _log.info("%s  %s", label, youtube_url)
    _separator()
    _log.debug("URL     : %s", youtube_url)
    _log.debug("Channel : %s", channel_name)

    result = {
        "url":             youtube_url,
        "channel":         channel_name,
        "status":          "UNKNOWN",
        "format_selected": None,
        "video_title":     None,
        "duration_s":      None,
        "errors":          [],
        "warnings":        [],
    }

    cmd = [
        PYTHON, "-m", "yt_dlp",
        *_auth_args(),
        *_ffmpeg_args(),
        "--format", DOWNLOAD_FORMAT,
        "--simulate",            # resolve + select format, but don't download
        "--verbose",             # maximum diagnostic detail
        "--no-playlist",
        youtube_url,
    ]
    _log.debug("CMD: %s", " ".join(cmd))

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - t0

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Log the full raw output at DEBUG level (visible only in the log file)
        _log.debug("--- yt-dlp stdout ---")
        for line in stdout.splitlines():
            _log.debug("  %s", line)
        _log.debug("--- yt-dlp stderr ---")
        for line in stderr.splitlines():
            _log.debug("  %s", line)
        _log.debug("--- returncode: %d  elapsed: %.1fs ---", proc.returncode, elapsed)

        # Parse useful fields out of verbose stderr
        for line in stderr.splitlines():
            if "[info] " in line and "Downloading" in line:
                _log.debug("  >> %s", line.strip())
            if "Destination:" in line:
                _log.debug("  >> %s", line.strip())

        # Extract video title from stdout (yt-dlp prints it during simulate)
        for line in stdout.splitlines():
            line = line.strip()
            if line:
                result["video_title"] = line[:120]
                break

        # Extract selected format from verbose output
        for line in stderr.splitlines():
            if "[info]" in line and ("+" in line or "mp4" in line or "m4a" in line or "webm" in line):
                if "Downloading" in line or "format" in line.lower():
                    result["format_selected"] = line.strip()[-120:]

        # Check for known error patterns
        result["errors"]   = _classify_stderr(stderr)
        result["warnings"] = [
            line.strip() for line in stderr.splitlines()
            if "WARNING" in line and line.strip()
        ]

        if proc.returncode == 0:
            result["status"] = "OK"
            _log.info("        OK  (%.1fs)", elapsed)
        else:
            result["status"] = "FAIL"
            # Log the most informative error lines at INFO level so they show on console
            for line in stderr.splitlines():
                if "ERROR" in line:
                    _log.info("        ERROR: %s", line.strip()[:120])
            if result["errors"]:
                for e in result["errors"]:
                    _log.info("        -> %s", e)
            else:
                _log.info("        FAIL (returncode %d, see log for details)", proc.returncode)

    except subprocess.TimeoutExpired:
        result["status"] = "TIMEOUT"
        result["errors"] = ["TIMEOUT — yt-dlp took >60s to resolve"]
        _log.info("        TIMEOUT (>60s)")
        _log.debug("yt-dlp timed out after 60s for %s", youtube_url)

    except Exception as exc:
        result["status"] = "EXCEPTION"
        result["errors"] = [f"EXCEPTION: {exc}"]
        _log.info("        EXCEPTION: %s", exc)
        _log.debug("exception diagnosing %s: %s", youtube_url, exc, exc_info=True)

    _separator()
    return result


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    global BROWSER

    parser = argparse.ArgumentParser(description="Toddler TV Download Diagnostics")
    parser.add_argument("--browser", "-b",
                        help="Pull cookies live from browser (firefox/chrome/edge)")
    parser.add_argument("--channel", "-c",
                        help="Diagnose only this channel name (substring match)")
    args = parser.parse_args()
    BROWSER = args.browser

    _log.info("Toddler TV — Download Diagnostics")
    _log.info("Log file: %s", LOG_PATH)
    _log.info("")

    # ── System checks ─────────────────────────
    _log.info("── System ─────────────────────────────────────────────")
    sys_ok = check_system()
    _log.info("")
    if not sys_ok:
        _log.info("Fatal dependency missing — fix above before diagnosing videos.")
        sys.exit(1)

    # ── Load channels ─────────────────────────
    if not os.path.exists(CHANNELS_FILE):
        _log.error("%s not found", CHANNELS_FILE)
        sys.exit(1)

    with open(CHANNELS_FILE, encoding="utf-8") as f:
        channels = json.load(f)

    if args.channel:
        channels = [ch for ch in channels
                    if args.channel.lower() in ch["name"].lower()]
        if not channels:
            _log.info("No channel matching %r", args.channel)
            sys.exit(1)

    # ── Collect all URLs ───────────────────────
    _log.info("── Collecting URLs ────────────────────────────────────")
    all_items: list[tuple[str, str]] = []  # (channel_name, url)
    seen: set[str] = set()

    for ch in channels:
        ch_name = ch["name"]
        urls: list[str] = []

        playlist_url = ch.get("playlist_url", "")
        if playlist_url:
            _log.info("  %s  (playlist_url)", ch_name)
            _log.debug("  playlist_url: %s", playlist_url)
            urls = expand_playlist(playlist_url)

        if not urls:
            urls = [clean_url(u) for u in ch.get("playlist", []) if u.strip()]
            if urls:
                _log.info("  %s  (%d individual URLs)", ch_name, len(urls))

        for url in urls:
            clean = clean_url(url)
            if clean not in seen:
                seen.add(clean)
                all_items.append((ch_name, clean))

    if not all_items:
        _log.info("No URLs found in channels.json")
        sys.exit(0)

    _log.info("  Total: %d unique video(s)", len(all_items))
    _log.info("")

    # ── Diagnose each video ────────────────────
    _log.info("── Per-video diagnosis ────────────────────────────────")
    _log.info("  (full yt-dlp verbose output is in the log file)")
    _log.info("")

    results = []
    for i, (ch_name, url) in enumerate(all_items, 1):
        res = diagnose_video(ch_name, url, i, len(all_items))
        results.append(res)
        # Brief pause between checks to avoid rate-limiting
        if i < len(all_items):
            time.sleep(2)

    # ── Summary ───────────────────────────────
    _section("SUMMARY")
    ok      = [r for r in results if r["status"] == "OK"]
    failed  = [r for r in results if r["status"] != "OK"]

    _log.info("")
    _log.info("── Summary ────────────────────────────────────────────")
    _log.info("  OK      : %d / %d", len(ok), len(results))
    _log.info("  FAILED  : %d / %d", len(failed), len(results))
    _log.info("")

    if ok:
        _log.info("  WILL DOWNLOAD:")
        for r in ok:
            title = r["video_title"] or "(title unknown)"
            _log.info("    ✓  %s  —  %s", r["url"].split("v=")[-1], title[:60])

    if failed:
        _log.info("")
        _log.info("  WILL FAIL:")
        for r in failed:
            _log.info("    ✗  %s  [%s]", r["url"], r["status"])
            for e in r["errors"]:
                _log.info("         %s", e)
            for w in r["warnings"][:2]:
                _log.info("         WARN: %s", w[:100])

    _log.info("")
    _log.info("Full diagnostic log saved to:  %s", LOG_PATH.absolute())
    _log.debug("")
    _log.debug("END OF DIAGNOSTIC")

    # Exit non-zero if any video failed so CI/scripts can detect issues
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
