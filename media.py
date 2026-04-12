"""
Toddler TV - Media
ffmpeg detection, yt-dlp wrappers, and ChannelState.
"""

import os
import sys
import json
import random
import subprocess

from config import VIDEOS_DIR


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

def clean_url(youtube_url: str) -> str:
    if "watch?v=" in youtube_url:
        video_id = youtube_url.split("watch?v=")[1].split("&")[0]
        return f"https://www.youtube.com/watch?v={video_id}"
    return youtube_url


def get_video_id(youtube_url: str) -> str:
    if "watch?v=" in youtube_url:
        return youtube_url.split("watch?v=")[1].split("&")[0]
    return youtube_url.split("/")[-1]


def find_local_file(video_id: str) -> str | None:
    for ext in ("mp4", "mkv", "webm"):
        path = os.path.join(VIDEOS_DIR, f"{video_id}.{ext}")
        if os.path.exists(path):
            return path
    return None


# ─────────────────────────────────────────────
#  YT-DLP WRAPPERS
# ─────────────────────────────────────────────

def _ffmpeg_args() -> list[str]:
    return ["--ffmpeg-location", FFMPEG_LOCATION] if FFMPEG_LOCATION else []


def _cookie_args() -> list[str]:
    return ["--cookies", "cookies.txt"] if os.path.exists("cookies.txt") else []


def _subprocess_env() -> dict:
    """Return an environment dict with FFMPEG_LOCATION added to PATH."""
    env = os.environ.copy()
    if FFMPEG_LOCATION:
        env["PATH"] = FFMPEG_LOCATION + os.pathsep + env.get("PATH", "")
    return env


def get_playlist_urls(playlist_url: str) -> list[str]:
    """Expand a YouTube playlist URL into individual video URLs (shuffled)."""
    print(f"[playlist] Fetching: {playlist_url}")
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "--flat-playlist",
                "--print", "url",
                "--no-warnings",
                *_ffmpeg_args(),
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
        random.shuffle(urls)
        print(f"[playlist] Found {len(urls)} videos")
        return urls
    except Exception as e:
        print(f"[playlist] Error fetching playlist: {e}")
        return []


def get_video_info(youtube_url: str) -> dict | None:
    youtube_url = clean_url(youtube_url)
    video_id    = get_video_id(youtube_url)
    local       = find_local_file(video_id)

    if local:
        print(f"[local] Using: {local}")
        # Get duration from local file via ffprobe/yt-dlp
        duration = 1800
        try:
            result = subprocess.run(
                [sys.executable, "-m", "yt_dlp",
                 *_ffmpeg_args(),
                 "--print", "%(duration)s",
                 local],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                env=_subprocess_env(),
            )
            dur_str = result.stdout.decode("utf-8", errors="replace").strip()
            if dur_str:
                duration = float(dur_str)
        except Exception:
            pass

        # Fetch metadata (title, channel, channel_id, thumbnail) from YouTube
        # using the original URL — this is fast since we're not downloading
        title, channel, thumbnail, channel_id = "Unknown", "", "", ""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "yt_dlp",
                 *_cookie_args(),
                 "--js-runtimes", "node",
                 "--remote-components", "ejs:github",
                 *_ffmpeg_args(),
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

        return {"url": local, "duration": duration, "title": title,
                "channel": channel, "thumbnail": thumbnail, "channel_id": channel_id}

    print(f"[resolve] {youtube_url}")
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                *_cookie_args(),
                "--js-runtimes", "node",
                "--remote-components", "ejs:github",
                *_ffmpeg_args(),
                # Prefer 1080p video-only + separate audio so VLC can play via
                # input-slave. Falls back to best pre-muxed if unavailable.
                "--format", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best",
                "--dump-single-json",
                "--no-warnings",
                youtube_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
            encoding="utf-8", errors="replace",
            env=_subprocess_env(),
        )
        stderr_text = result.stderr or ""
        for line in stderr_text.splitlines():
            if "ffmpeg not found" in line or "best pre-merged format" in line:
                continue
            if "ERROR" in line or "WARNING" in line:
                print(f"[yt-dlp stderr] {line}")

        # Detect YouTube rate-limiting
        if "rate-limited" in stderr_text or "try again later" in stderr_text.lower():
            return "RATE_LIMITED"

        info = json.loads(result.stdout.strip())
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
#  CHANNEL STATE
# ─────────────────────────────────────────────

class ChannelState:
    def __init__(self):
        self.videos: list[dict] = []
        self.resolving = False
        self.ready = False
        self.pending_urls: list[str] = []
        self._current_idx: int = 0
        self._video_start_elapsed: float = 0.0
        self._unplayed: list[int] = []
        self._initialized: bool = False

    def _refill_queue(self):
        """Shuffle all video indices into the unplayed queue."""
        indices = list(range(len(self.videos)))
        random.shuffle(indices)
        self._unplayed = indices

    def advance_video(self, elapsed: float):
        """Move to the next video in the queue. Refills and reshuffles when exhausted."""
        if not self.videos:
            return
        if not self._unplayed:
            self._refill_queue()
            if self._initialized:
                print(f"[playlist] Cycle complete — reshuffled {len(self.videos)} videos")
        self._current_idx = self._unplayed.pop(0)
        self._video_start_elapsed = elapsed
        self._initialized = True

    def queue_new_video(self, index: int):
        """Insert a newly-resolved video into the current cycle's unplayed queue."""
        if self._initialized:
            insert_at = random.randint(0, len(self._unplayed))
            self._unplayed.insert(insert_at, index)

    def get_position(self, elapsed: float) -> tuple[int, float]:
        if not self.videos or not self._initialized:
            return 0, 0.0
        offset = max(0.0, elapsed - self._video_start_elapsed)
        return self._current_idx, offset