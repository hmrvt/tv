"""
Toddler TV - Media
ffmpeg detection, yt-dlp wrappers, and ChannelState.
"""

import os
import re
import sys
import json
import random
import logging
import subprocess

from config import VIDEOS_DIR


# ─────────────────────────────────────────────
#  FAILED-VIDEO LOG
# ─────────────────────────────────────────────

_fail_log = logging.getLogger("failed_videos")
_fail_log.setLevel(logging.WARNING)
_fail_log.propagate = False  # keep out of the root logger / console

_fh = logging.FileHandler("failed_videos.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter(
    "%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
_fail_log.addHandler(_fh)
_fail_log.warning("=== app started ===")


# ─────────────────────────────────────────────
#  FFMPEG LOCATION
# ─────────────────────────────────────────────

def _find_ffmpeg() -> str | None:
    """Return the directory containing ffmpeg/ffmpeg.exe, or None."""
    import shutil

    def _can_run(directory: str) -> bool:
        """Return True if ffmpeg in the given directory actually executes."""
        exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        path = os.path.join(directory, exe)
        try:
            result = subprocess.run(
                [path, "-version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    app_dir = os.getcwd()
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    # 1. Prefer project-local ffmpeg (has DLLs alongside it)
    local_candidates = [
        os.path.join(app_dir, "ffmpeg", "bin"),    # ./ffmpeg/bin/
        os.path.join(app_dir, "ffmpeg"),            # ./ffmpeg/
        app_dir,                                    # ./
    ]

    for d in local_candidates:
        if os.path.isfile(os.path.join(d, exe)) and _can_run(d):
            return d

    # 2. Check PATH, but verify it actually works (DLLs may be missing)
    which_result = shutil.which("ffmpeg")
    if which_result:
        found = os.path.dirname(os.path.abspath(which_result))
        if _can_run(found):
            return found
        print(f"[ffmpeg] Found on PATH: {found}")
        print(f"[ffmpeg] Note: ffmpeg.exe exits with code 3221225781 (may be missing DLLs — yt-dlp will fall back to HLS streams)")

    # 3. Platform-specific system paths
    system_candidates: list[str] = []
    if sys.platform == "win32":
        system_candidates = [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin"),
        ]
    elif sys.platform == "darwin":
        system_candidates = ["/usr/local/bin", "/opt/homebrew/bin"]
    else:
        system_candidates = ["/usr/bin", "/usr/local/bin", "/snap/bin"]

    for d in system_candidates:
        if os.path.isfile(os.path.join(d, exe)) and _can_run(d):
            return d

    return None


FFMPEG_LOCATION = _find_ffmpeg()

if FFMPEG_LOCATION and FFMPEG_LOCATION not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FFMPEG_LOCATION + os.pathsep + os.environ.get("PATH", "")

if FFMPEG_LOCATION:
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffmpeg_bin = os.path.join(FFMPEG_LOCATION, exe)
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        if result.returncode == 0:
            output = (result.stdout or result.stderr or b"").decode("utf-8", errors="replace")
            version_line = output.strip().split("\n")[0] if output.strip() else ""
            print(f"[ffmpeg] {version_line or ffmpeg_bin}")
        else:
            print(f"[ffmpeg] WARNING: exits with code {result.returncode} — will fall back to HLS")
    except Exception as e:
        print(f"[ffmpeg] WARNING: cannot run: {e}")
else:
    print("[ffmpeg] Not found — will use HLS streams")


# ─────────────────────────────────────────────
#  URL HELPERS
# ─────────────────────────────────────────────

# YouTube video IDs are exactly 11 characters from this alphabet.
# We allow up to 20 to be tolerant of non-YouTube sources while still
# blocking any path-traversal sequence (which requires '/' or '.').
_SAFE_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{1,20}$')

# Resolved once at import time so find_local_file can use it as a boundary.
_VIDEOS_DIR_REAL = os.path.realpath(VIDEOS_DIR)

# Persistent cache for YouTube metadata (title, channel, channel_id, thumbnail)
# keyed by video_id.  Avoids re-fetching from YouTube on every restart for
# videos that are already downloaded locally.
_METADATA_CACHE_PATH = os.path.join(VIDEOS_DIR, ".metadata_cache.json")
_metadata_cache: dict = {}

def _load_metadata_cache() -> None:
    global _metadata_cache
    try:
        with open(_METADATA_CACHE_PATH, encoding="utf-8") as _f:
            _metadata_cache = json.load(_f)
    except Exception:
        _metadata_cache = {}

def _save_metadata_cache() -> None:
    try:
        with open(_METADATA_CACHE_PATH, "w", encoding="utf-8") as _f:
            json.dump(_metadata_cache, _f, indent=2, ensure_ascii=False)
    except Exception as _e:
        print(f"[metadata cache] save failed: {_e}")

_load_metadata_cache()


def _validate_video_id(raw: str) -> str:
    """Return the video ID unchanged if it is safe, otherwise raise ValueError."""
    if not _SAFE_VIDEO_ID.match(raw):
        raise ValueError(f"Unsafe video_id rejected: {raw!r}")
    return raw


def _extract_video_id_raw(url: str) -> str:
    """Extract the raw video-ID string from any recognised YouTube URL variant.

    Handles (non-exhaustively):
      https://www.youtube.com/watch?v=ID
      https://youtu.be/ID?si=...          ← YouTube app share link
      https://youtube.com/shorts/ID       ← YouTube Shorts
      https://m.youtube.com/watch?v=ID    ← mobile web
      youtube://watch?v=ID               ← iOS/Android app scheme
      vnd.youtube://ID                   ← legacy app scheme
    """
    # v=ID query-param covers watch?v=, youtube://watch?v=, ?v=, &v=
    if "v=" in url:
        raw = url.split("v=")[1].split("&")[0].split("?")[0].split("#")[0]
        if raw:
            return raw
    # Path-segment keywords: /shorts/, /embed/, /v/, /e/
    for seg in ("/shorts/", "/embed/", "/v/", "/e/"):
        if seg in url:
            raw = url.split(seg, 1)[1].split("?")[0].split("&")[0].split("/")[0]
            if raw:
                return raw
    # Last path component (handles youtu.be/ID?si=... and vnd.youtube://ID)
    raw = url.rstrip("/").split("/")[-1].split("?")[0].split("&")[0].split("#")[0]
    return raw


def clean_url(youtube_url: str) -> str:
    """Normalise any YouTube URL variant to the canonical watch?v= form."""
    raw = _extract_video_id_raw(youtube_url)
    if raw and re.match(r'^[A-Za-z0-9_-]{1,20}$', raw):
        return f"https://www.youtube.com/watch?v={raw}"
    return youtube_url


def get_video_id(youtube_url: str) -> str:
    """Extract and validate a video ID from any YouTube URL (CWE-22 Layer 1)."""
    raw = _extract_video_id_raw(youtube_url)
    return _validate_video_id(raw)


# Hosts a link may legitimately come from.  clean_url is deliberately permissive
# — it will read "passwd" out of "file:///etc/passwd" — so anything arriving
# from outside the app has to clear a host check first (CWE-20).
_YT_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
})

# The YouTube apps share links with their own scheme and no recognisable host.
_YT_APP_SCHEMES = frozenset({"youtube", "vnd.youtube"})

# A bare 11-character video ID, as pasted from a share sheet.
_BARE_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')

_WATCH_PREFIX = "https://www.youtube.com/watch?v="


def validate_youtube_url(raw: str) -> str | None:
    """Return a canonical watch URL, or None if this isn't a YouTube video link.

    Use this for anything that arrives from outside the app — a web-remote form
    field, a hand-edited playlist_overlay.json — where clean_url alone would
    coerce arbitrary text into a plausible-looking watch URL.
    """
    from urllib.parse import urlparse

    raw = (raw or "").strip()
    if not raw:
        return None

    if _BARE_VIDEO_ID.match(raw):
        return f"{_WATCH_PREFIX}{raw}"

    try:
        parsed = urlparse(raw)
    except ValueError:
        return None

    scheme = (parsed.scheme or "").lower()
    if scheme in ("http", "https"):
        if (parsed.hostname or "").lower() not in _YT_HOSTS:
            return None
    elif scheme not in _YT_APP_SCHEMES:
        return None

    try:
        cleaned = clean_url(raw)
        get_video_id(cleaned)   # raises ValueError if the video ID is unsafe
    except Exception:
        return None

    # clean_url returns its input unchanged when it finds no usable ID.
    return cleaned if cleaned.startswith(_WATCH_PREFIX) else None


# Real playlist IDs start with a letter or digit (PL…, UU…, OLAK5uy_…), so a
# leading "-" is refused outright.
_SAFE_PLAYLIST_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$')
# A bare ID is accepted only with a real playlist prefix, so a typo'd video ID
# isn't mistaken for one.
_BARE_PLAYLIST_ID = re.compile(r'^(?:PL|UU|OL|FL|LL)[A-Za-z0-9_-]{10,62}$')


def validate_youtube_playlist_url(raw: str) -> str | None:
    """Return the playlist ID from a YouTube playlist link, or None.

    Same rules as validate_youtube_url: host whitelist, then the ID itself is
    allowlisted.  Callers rebuild the URL with playlist_url(), so nothing from
    the original string reaches yt-dlp (CWE-20, finding N1).
    """
    from urllib.parse import urlparse, parse_qs

    raw = (raw or "").strip()
    if not raw:
        return None
    if _BARE_PLAYLIST_ID.match(raw):
        return raw

    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if (parsed.scheme or "").lower() not in ("http", "https"):
        return None
    if (parsed.hostname or "").lower() not in _YT_HOSTS:
        return None

    values = parse_qs(parsed.query).get("list") or []
    if not values or not _SAFE_PLAYLIST_ID.match(values[0]):
        return None
    return values[0]


def playlist_url(playlist_id: str) -> str:
    """Canonical URL for a playlist ID that has already been validated."""
    if not _SAFE_PLAYLIST_ID.match(playlist_id or ""):
        raise ValueError(f"Unsafe playlist ID: {playlist_id!r}")
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def get_cached_title(youtube_url: str) -> str:
    """Return the cached title for a YouTube URL, or empty string if not in cache."""
    try:
        vid_id = get_video_id(clean_url(youtube_url))
        return _metadata_cache.get(vid_id, {}).get("title", "")
    except Exception:
        return ""


def find_local_file(video_id: str) -> str | None:
    """Return a local file path only when it resolves inside VIDEOS_DIR (CWE-22 Layer 2)."""
    _validate_video_id(video_id)  # re-validate at the sink (defence in depth)
    for ext in ("mp4", "mkv", "webm"):
        candidate = os.path.join(VIDEOS_DIR, f"{video_id}.{ext}")
        # Resolve symlinks and '..' before the boundary check.
        real = os.path.realpath(candidate)
        if not real.startswith(_VIDEOS_DIR_REAL + os.sep):
            raise ValueError(f"Path escape detected: {candidate!r} → {real!r}")
        if os.path.exists(real):
            return real
    return None


# ─────────────────────────────────────────────
#  YT-DLP WRAPPERS
# ─────────────────────────────────────────────

def _ffmpeg_args() -> list[str]:
    return ["--ffmpeg-location", FFMPEG_LOCATION] if FFMPEG_LOCATION else []


def _cookie_args() -> list[str]:
    return ["--cookies", "cookies.txt"] if os.path.exists("cookies.txt") else []


def _js_runtime_args() -> list[str]:
    """Pass a JS runtime to yt-dlp so YouTube extraction works (required since ~2026)."""
    import shutil
    for runtime in ("node", "deno", "bun"):
        if shutil.which(runtime):
            return ["--js-runtimes", runtime]
    return []


def _subprocess_env() -> dict:
    """Return an environment dict with FFMPEG_LOCATION added to PATH."""
    env = os.environ.copy()
    if FFMPEG_LOCATION:
        env["PATH"] = FFMPEG_LOCATION + os.pathsep + env.get("PATH", "")
    return env


# Used only when a local file's real duration can't be read.  Everything that
# consumes a duration (the channel-button progress bar, the inactive-channel
# advance in _poll_vlc, the resume-seek on channel switch) is wrong for as long
# as this estimate is in play, so it is a last resort, not a default.
LOCAL_DURATION_FALLBACK = 1800.0


def ffprobe_duration(path: str) -> float | None:
    """Return a local file's duration in seconds, or None if it can't be read.

    yt-dlp cannot do this: handed a filesystem path it answers
    "'<path>' is not a valid URL" and exits 1, which silently sent every
    downloaded video to LOCAL_DURATION_FALLBACK.  ffprobe ships alongside the
    ffmpeg this app already locates, so use it directly.
    """
    if not FFMPEG_LOCATION:
        return None
    exe = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    probe = os.path.join(FFMPEG_LOCATION, exe)
    if not os.path.isfile(probe):
        return None
    try:
        result = subprocess.run(
            [
                probe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nokey=1",
                # -i marks the path as input, so a filename starting with '-'
                # can never be read as an option.
                "-i", path,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15, shell=False, env=_subprocess_env(),
        )
    except Exception as e:
        print(f"[ffprobe] {os.path.basename(path)}: {e}")
        return None

    text = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    try:
        duration = float(text)
    except ValueError:
        return None
    return duration if duration > 0 else None


def get_playlist_urls(playlist_url: str) -> list[str]:
    """Expand a YouTube playlist URL into individual video URLs in original order."""
    print(f"[playlist] Fetching: {playlist_url}")
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "--flat-playlist",
                "--print", "url",
                "--no-warnings",
                *_ffmpeg_args(),
                *_js_runtime_args(),
                "--",
                playlist_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, encoding="utf-8", errors="replace",
            env=_subprocess_env(),
        )
        for line in (result.stderr or "").splitlines():
            if "ERROR" in line:
                print(f"[playlist stderr] {line}")
        urls = [
            f"https://www.youtube.com/watch?v={line.strip()}"
            if not line.strip().startswith("http") else line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        print(f"[playlist] Found {len(urls)} videos")
        return urls
    except Exception as e:
        print(f"[playlist] Error fetching playlist: {e}")
        return []


def get_playlist_title(playlist_url: str) -> str:
    """Return a playlist's title, or "" if it can't be read."""
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "--flat-playlist",
                "--playlist-items", "1",
                "--print", "playlist_title",
                "--no-warnings",
                *_js_runtime_args(),
                "--",
                playlist_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60, encoding="utf-8", errors="replace", shell=False,
            env=_subprocess_env(),
        )
    except Exception as e:
        print(f"[playlist] Error fetching title: {e}")
        return ""
    lines = (result.stdout or "").strip().splitlines()
    # Third-party text: keep printable characters only before it is stored.
    title = lines[0] if lines else ""
    return "".join(c for c in title if c.isprintable()).strip()


def get_video_info(youtube_url: str) -> dict | None:
    youtube_url = clean_url(youtube_url)
    video_id    = get_video_id(youtube_url)
    local       = find_local_file(video_id)

    if local:
        print(f"[local] Using: {local}")
        duration = ffprobe_duration(local)
        if duration is None:
            duration = LOCAL_DURATION_FALLBACK
            print(f"[local] WARNING: duration unreadable for {video_id} — "
                  f"assuming {duration:.0f}s (progress bar and auto-advance "
                  f"will be wrong for this video)")

        # Fetch metadata (title, channel, channel_id, thumbnail) from cache
        # first; only call YouTube when the cache has no entry for this video.
        title, channel, thumbnail, channel_id = "Unknown", "", "", ""
        cached = _metadata_cache.get(video_id)
        if cached:
            title      = cached.get("title", "Unknown")
            channel    = cached.get("channel", "")
            thumbnail  = cached.get("thumbnail", "")
            channel_id = cached.get("channel_id", "")
            print(f"[local metadata] cache hit for {video_id}")
        else:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "yt_dlp",
                     *_cookie_args(),
                     *_ffmpeg_args(),
                     *_js_runtime_args(),
                     "--skip-download",
                     "--print", "%(title)s|||%(channel)s|||%(thumbnail)s|||%(channel_id)s",
                     "--no-warnings",
                     youtube_url],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                    encoding="utf-8", errors="replace",
                    env=_subprocess_env(),
                )
                for line in (result.stderr or "").splitlines():
                    if "ERROR" in line:
                        print(f"[local metadata stderr] {line}")
                parts = result.stdout.strip().split("|||")
                if len(parts) >= 1 and parts[0].strip():
                    title = parts[0].strip()
                if len(parts) >= 2:
                    channel = parts[1].strip()
                if len(parts) >= 3:
                    thumbnail = parts[2].strip()
                if len(parts) >= 4:
                    channel_id = parts[3].strip()
            except Exception as e:
                print(f"[local metadata] Error fetching metadata for {youtube_url}: {e}")

            if channel_id:
                _metadata_cache[video_id] = {
                    "title": title, "channel": channel,
                    "channel_id": channel_id, "thumbnail": thumbnail,
                }
                _save_metadata_cache()

        return {"url": local, "duration": duration, "title": title,
                "channel": channel, "thumbnail": thumbnail, "channel_id": channel_id}

    print(f"[resolve] {youtube_url}")
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                *_cookie_args(),
                *_ffmpeg_args(),
                *_js_runtime_args(),
                # Prefer 1080p video-only + separate audio so VLC can play via
                # input-slave. Falls back to best pre-muxed if unavailable.
                # H.264 first: [ext=mp4] alone admits YouTube's AV1 tracks
                # (399/721), which VLC fails to stream ("Error parsing OBU data").
                "--format", "bestvideo[vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best",
                "--dump-single-json",
                "--no-warnings",
                youtube_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
            encoding="utf-8", errors="replace",
            env=_subprocess_env(),
        )
        stderr_text = result.stderr or ""
        yt_error_lines: list[str] = []
        for line in stderr_text.splitlines():
            if "ffmpeg not found" in line or "best pre-merged format" in line:
                continue
            if "ERROR" in line or "WARNING" in line:
                print(f"[yt-dlp stderr] {line}")
            if "ERROR" in line:
                yt_error_lines.append(line.strip())

        # Detect YouTube rate-limiting (temporary — don't log as a failed video)
        if "rate-limited" in stderr_text or "try again later" in stderr_text.lower():
            return "RATE_LIMITED"

        # Permanent error (geo-block, copyright, private, etc.) — stdout will be
        # empty; log and bail out before json.loads raises on an empty string.
        if yt_error_lines and not result.stdout.strip():
            for err in yt_error_lines:
                _fail_log.warning("%s | %s", youtube_url, err)
            return None

        info = json.loads(result.stdout.strip())
        if not isinstance(info, dict):
            for err in yt_error_lines:
                _fail_log.warning("%s | %s", youtube_url, err)
            return None
        requested = info.get("requested_formats", [])
        if len(requested) >= 2:
            video_url = requested[0].get("url", "")
            audio_url = requested[1].get("url")
            height    = requested[0].get("height", "?")
            print(f"[resolve] {height}p video + separate audio")
        else:
            video_url = info.get("url", "")
            audio_url = None

        if not video_url.startswith("http"):
            for err in yt_error_lines:
                _fail_log.warning("%s | %s", youtube_url, err)
            return None
        return {
            "url":        video_url,
            "audio_url":  audio_url,
            "duration":   float(info.get("duration") or 0),
            "title":      info.get("title", "Unknown"),
            "channel":    info.get("channel", ""),
            "thumbnail":  info.get("thumbnail", ""),
            "channel_id": info.get("channel_id", ""),
        }
    except Exception as e:
        print(f"[yt-dlp] Error: {e}")
        return None


# ─────────────────────────────────────────────
#  LOCAL LIBRARY HOUSEKEEPING
# ─────────────────────────────────────────────

# Extensions a downloaded video (or a yt-dlp scratch file) can carry.
_VIDEO_EXTS   = ("mp4", "mkv", "webm")
_PARTIAL_EXTS = ("part", "ytdl", "temp")


def _videos_dir_path(video_id: str, ext: str) -> str:
    """Build VIDEOS_DIR/<video_id>.<ext> and verify it stays inside VIDEOS_DIR.

    CWE-22 Layer 2 — the boundary check runs at the sink even though the caller
    is expected to have passed the ID through _validate_video_id first.
    """
    _validate_video_id(video_id)
    candidate = os.path.join(VIDEOS_DIR, f"{video_id}.{ext}")
    real = os.path.realpath(candidate)
    if not real.startswith(_VIDEOS_DIR_REAL + os.sep):
        raise ValueError(f"Path escape detected: {candidate!r} → {real!r}")
    return real


def delete_local_file(video_id: str) -> bool:
    """Delete every local copy of video_id (including yt-dlp partials).

    Returns True if at least one file was removed.  Raises ValueError if the
    ID is unsafe or would resolve outside VIDEOS_DIR (CWE-22).
    """
    _validate_video_id(video_id)
    removed = False
    for ext in _VIDEO_EXTS + _PARTIAL_EXTS:
        real = _videos_dir_path(video_id, ext)
        if not os.path.exists(real):
            continue
        try:
            os.remove(real)
            removed = True
            print(f"[library] Deleted {os.path.basename(real)}")
        except OSError as e:
            # A file VLC still has open (Windows) stays put; the next sweep retries.
            print(f"[library] Could not delete {os.path.basename(real)}: {e}")
    return removed


def list_local_video_ids() -> set[str]:
    """Return the set of video IDs that have a playable file in VIDEOS_DIR."""
    ids: set[str] = set()
    try:
        names = os.listdir(_VIDEOS_DIR_REAL)
    except OSError:
        return ids
    for name in names:
        stem, dot, ext = name.rpartition(".")
        if not dot or ext.lower() not in _VIDEO_EXTS:
            continue
        if _SAFE_VIDEO_ID.match(stem):
            ids.add(stem)
    return ids


def forget_metadata(video_id: str) -> None:
    """Drop a video's cached title/channel/thumbnail entry."""
    if _metadata_cache.pop(video_id, None) is not None:
        _save_metadata_cache()


def fetch_metadata(youtube_url: str) -> dict | None:
    """Fetch title/channel/thumbnail for a video and persist it to the cache.

    Returns the cached entry (existing or freshly fetched), or None on failure.
    """
    youtube_url = clean_url(youtube_url)
    try:
        video_id = get_video_id(youtube_url)
    except ValueError:
        return None

    cached = _metadata_cache.get(video_id)
    if cached:
        return cached

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                *_cookie_args(),
                *_ffmpeg_args(),
                *_js_runtime_args(),
                "--skip-download",
                "--print", "%(title)s|||%(channel)s|||%(thumbnail)s|||%(channel_id)s",
                "--no-warnings",
                youtube_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
            encoding="utf-8", errors="replace", shell=False,
            env=_subprocess_env(),
        )
    except Exception as e:
        print(f"[metadata] fetch failed for {video_id}: {e}")
        return None

    parts      = (result.stdout or "").strip().split("|||")
    title      = parts[0].strip() if len(parts) >= 1 and parts[0].strip() else ""
    channel    = parts[1].strip() if len(parts) >= 2 else ""
    thumbnail  = parts[2].strip() if len(parts) >= 3 else ""
    channel_id = parts[3].strip() if len(parts) >= 4 else ""

    if not title:
        return None

    entry = {"title": title, "channel": channel,
             "channel_id": channel_id, "thumbnail": thumbnail}
    _metadata_cache[video_id] = entry
    _save_metadata_cache()
    return entry


# ─────────────────────────────────────────────
#  CHANNEL STATE
#
#  Positions, not list indices, are the stable handle for a video.
#  _playlist_urls is append-only: a video that leaves the playlist is marked
#  in _removed_positions rather than spliced out, so every position already
#  held by _play_queue / _resolved_map / _failed_positions stays valid.
# ─────────────────────────────────────────────

class ChannelState:
    def __init__(self):
        self.videos: list[dict] = []
        self.resolving = False
        self.ready = False
        self._current_idx: int = 0
        self._current_playlist_idx: int = -1
        self._video_start_elapsed: float = 0.0
        self._initialized: bool = False
        # Ordered canonical watch URLs (never shuffled, never spliced).
        self._playlist_urls: list[str] = []
        # Shuffled integer positions into _playlist_urls — the playback order.
        self._play_queue: list[int] = []
        # Maps playlist position → index in self.videos (resolved stream info cache).
        self._resolved_map: dict[int, int] = {}
        # Playlist positions that permanently failed resolution this cycle (e.g. geo/copyright block).
        self._failed_positions: set[int] = set()
        # Positions no longer part of the channel — removed (ignored) on the
        # web remote, or merged into another channel.
        self._removed_positions: set[int] = set()
        # Positions the user skipped for the rest of this shuffle cycle.  They
        # are still channel members: the file stays, and _refill_queue clears
        # the set so they come back on the next round.
        self._skipped_positions: set[int] = set()
        # One-shot web-remote injections: play once, never reshuffled back in,
        # never downloaded, never recorded in the channel catalog.
        self._injected_positions: set[int] = set()
        # Canonical URL → position, so a known video keeps its position.
        self._url_to_pos: dict[str, int] = {}
        # Bumped on every feed mutation so callers can cheaply detect change.
        self.revision: int = 0

    # ── Playlist membership ──────────────────────────────────────

    def url_at(self, pos: int) -> str:
        """Return the canonical URL at a playlist position, or "" if out of range."""
        if 0 <= pos < len(self._playlist_urls):
            return self._playlist_urls[pos]
        return ""

    def position_of(self, url: str) -> int | None:
        return self._url_to_pos.get(clean_url(url.strip()))

    def is_injected(self, pos: int) -> bool:
        return pos in self._injected_positions

    def is_removed(self, pos: int) -> bool:
        """True once a position has left the channel for good."""
        return pos in self._removed_positions

    def is_playable(self, pos: int) -> bool:
        """False if a position is gone, or skipped for the rest of this cycle."""
        return (pos not in self._removed_positions
                and pos not in self._skipped_positions)

    def add_url(self, url: str, *, injected: bool = False,
                user_added: bool = False) -> int:
        """Append a URL to the playlist and return its position.

        A URL that is already known keeps its original position instead of
        being duplicated — re-adding something previously removed revives it.
        """
        canon = clean_url(url.strip())
        pos = self._url_to_pos.get(canon)

        if pos is None:
            pos = len(self._playlist_urls)
            self._playlist_urls.append(canon)
            self._url_to_pos[canon] = pos
            if injected:
                self._injected_positions.add(pos)
        elif not injected:
            # Promoted from a one-shot injection to a real playlist entry.
            self._injected_positions.discard(pos)

        if user_added:
            # An explicit add (or restore) revives an earlier removal.
            self._removed_positions.discard(pos)

        self.revision += 1
        return pos

    def remove_position(self, pos: int, *, permanent: bool = False) -> bool:
        """Drop a position from playback.

        permanent=False skips it for the rest of this shuffle cycle only: the
        video stays a channel member, keeps its downloaded file, and returns on
        the next reshuffle.
        permanent=True removes it from the channel.  Recording that in the
        catalog (so it survives a restart) is the caller's job — see library.py.
        """
        if pos < 0 or pos >= len(self._playlist_urls):
            return False
        self._play_queue = [p for p in self._play_queue if p != pos]
        if permanent:
            self._removed_positions.add(pos)
        else:
            self._skipped_positions.add(pos)
        self.revision += 1
        return True

    def insert_next(self, pos: int) -> None:
        """Queue a position to play immediately after the current video."""
        if pos < 0 or pos >= len(self._playlist_urls):
            return
        self._play_queue = [p for p in self._play_queue if p != pos]
        self._play_queue.insert(0, pos)
        self._removed_positions.discard(pos)
        self._skipped_positions.discard(pos)
        self._failed_positions.discard(pos)
        self.revision += 1

    def active_positions(self) -> list[int]:
        """Positions eligible for a fresh shuffle cycle.

        Injections are excluded on purpose — they are a one-shot request from
        the web remote, not a playlist member.
        """
        return [
            p for p in range(len(self._playlist_urls))
            if p not in self._removed_positions and p not in self._injected_positions
        ]

    def feed_video_ids(self) -> set[str]:
        """Video IDs that are still part of the feed (download-worthy)."""
        ids: set[str] = set()
        for pos in self.active_positions():
            try:
                ids.add(get_video_id(self._playlist_urls[pos]))
            except ValueError:
                continue
        return ids

    def set_playlist(self, urls: list[str]) -> None:
        """Install the initial playlist (startup).  Replaces any prior contents."""
        self._playlist_urls = []
        self._url_to_pos = {}
        self._removed_positions = set()
        self._skipped_positions = set()
        self._injected_positions = set()
        for url in urls:
            if url.strip():
                self.add_url(url)

    def add_members(self, urls: list[str]) -> list[int]:
        """Make each URL a channel member; return the positions that are new.

        Used for videos newly added to the catalog (a source sync, a merge).
        This never removes anything: the catalog, not the YouTube playlist,
        decides what leaves a channel.  A one-shot injection of the same video
        is promoted to a real member.
        """
        added: list[int] = []
        for url in urls:
            if not url.strip():
                continue
            known = self.position_of(url)
            if known is not None and known not in self._removed_positions \
                    and known not in self._injected_positions:
                continue
            added.append(self.add_url(url, user_added=True))
        return added

    # ── Playback order ───────────────────────────────────────────

    def _refill_queue(self):
        """Reshuffle every active playlist position into the play queue."""
        for v in self.videos:
            v["_play_retries"] = 0
            v.pop("_failed", None)
        self._failed_positions.clear()
        # A new cycle forgives every "skip this round".
        self._skipped_positions.clear()
        indices = self.active_positions()
        random.shuffle(indices)
        self._play_queue = indices
        # Debug: show the first few playlist positions after shuffling
        try:
            print(f"[playlist] new order (first 10): {self._play_queue[:10]}")
        except Exception:
            pass
        if self._initialized:
            print(f"[playlist] Cycle complete — reshuffled {len(indices)} videos")

    def advance_video(self, elapsed: float):
        """Pop the next playable playlist position and make it current."""
        if not self._playlist_urls:
            return
        # Bounded: each iteration either returns or discards one queued position.
        for _ in range(len(self._playlist_urls) + 1):
            if not self._play_queue:
                self._refill_queue()
            if not self._play_queue:
                return  # every position removed — nothing left to play
            nxt = self._play_queue.pop(0)
            if not self.is_playable(nxt):
                continue  # removed or skipped while it sat in the queue
            self._current_playlist_idx = nxt
            if nxt in self._resolved_map:
                self._current_idx = self._resolved_map[nxt]
            self._video_start_elapsed = elapsed
            self._initialized = True
            return

    def current_video(self) -> dict | None:
        """The resolved video for the current position, or None while it resolves.

        get_position() deliberately falls back to the *previous* video's index so
        the channel button keeps showing something during resolution.  Anything
        that needs the current video's own duration must use this instead.
        """
        idx = self._resolved_map.get(self._current_playlist_idx)
        if idx is None or idx >= len(self.videos):
            return None
        return self.videos[idx]

    def next_playlist_idx(self) -> int | None:
        """Peek at the position that will be played next (does not consume it)."""
        for pos in self._play_queue:
            if self.is_playable(pos):
                return pos
        return None

    def get_position(self, elapsed: float) -> tuple[int, float]:
        if not self.videos or not self._initialized:
            return 0, 0.0
        offset = max(0.0, elapsed - self._video_start_elapsed)
        # Prefer the index from _resolved_map so that after advance_video
        # moves _current_playlist_idx to the next video, the button shows
        # the new video's avatar as soon as it's resolved.
        cur_pl = self._current_playlist_idx
        if cur_pl in self._resolved_map:
            video_idx = min(self._resolved_map[cur_pl], len(self.videos) - 1)
        else:
            video_idx = self._current_idx
        return video_idx, offset
