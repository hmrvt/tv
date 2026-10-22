"""
Toddler TV - Background Downloads

A single-worker download queue so videos added to a playlist land in videos/
while the app keeps playing, instead of requiring a separate downloader.py run.

Design notes
------------
* One download at a time.  Bandwidth is shared with the stream the child is
  actually watching, so a burst of parallel downloads would stall playback.
* The worker asks a caller-supplied gate before each download, so the app can
  hold downloads off while VLC is buffering a new stream.
* Only video IDs are accepted, never raw URLs from the network.  The watch URL
  handed to yt-dlp is rebuilt from the validated ID, so a hostile playlist
  entry cannot smuggle an option (a "--"-prefixed URL) into the argv list.
"""

import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

from config import VIDEOS_DIR
from media import (
    FFMPEG_LOCATION, _subprocess_env, _cookie_args, _js_runtime_args,
    _validate_video_id, find_local_file, fetch_metadata,
)

# Seconds to wait between downloads — keeps YouTube from rate-limiting the
# whole app, which would also break stream resolution for playback.
DOWNLOAD_GAP = 5.0

# Hard ceiling per video.  A download that takes longer than this is almost
# certainly stuck; killing it lets the queue drain.
DOWNLOAD_TIMEOUT = 900

# Seconds the worker sleeps when the gate is closed or the queue is empty.
_IDLE_SLEEP = 2.0

# yt-dlp writes progress and warnings to stderr.  It is third-party output, so
# strip C0/C1 control characters before it reaches a terminal (CWE-117).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_MAX_FAILURES = 3   # give up on a video after this many attempts


class DownloadManager:
    """Serialised background downloader for playlist videos."""

    def __init__(self):
        self._queue: deque[str] = deque()      # video IDs, front = next
        self._queued: set[str] = set()         # membership index for _queue
        self._failures: dict[str, int] = {}    # video_id → attempts
        self._current: str | None = None
        self._completed = 0
        self._failed = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._gate = None                      # () -> bool, True = may download
        self._thread: threading.Thread | None = None
        self._stop = False

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self, gate=None) -> None:
        """Start the worker thread.  gate() is consulted before each download."""
        if self._thread is not None:
            return
        self._gate = gate
        os.makedirs(VIDEOS_DIR, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run, name="downloads", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    # ── Queue API ────────────────────────────────────────────────

    def enqueue(self, video_id: str, *, front: bool = False) -> bool:
        """Queue a video for download.  Returns False if it is already handled.

        front=True is for videos the child is about to see (a freshly added
        playlist entry queued up next), which should jump the backlog.
        """
        try:
            _validate_video_id(video_id)
        except ValueError:
            return False

        with self._lock:
            if video_id in self._queued or video_id == self._current:
                if front and video_id in self._queued:
                    # Already pending — just promote it.
                    self._queue.remove(video_id)
                    self._queue.appendleft(video_id)
                    self._wake.set()
                return False
            if self._failures.get(video_id, 0) >= _MAX_FAILURES:
                return False
            # Cheap pre-check; the worker re-checks before spending a subprocess.
            if find_local_file(video_id):
                return False
            if front:
                self._queue.appendleft(video_id)
            else:
                self._queue.append(video_id)
            self._queued.add(video_id)

        self._wake.set()
        return True

    def enqueue_many(self, video_ids, *, front: bool = False) -> int:
        """Queue several videos; returns how many were actually added."""
        # Reversed so the caller's order survives appendleft.
        ids = list(video_ids)
        if front:
            ids.reverse()
        return sum(1 for vid in ids if self.enqueue(vid, front=front))

    def cancel(self, video_id: str) -> bool:
        """Remove a pending video from the queue.  A running download continues."""
        with self._lock:
            if video_id not in self._queued:
                return False
            self._queue.remove(video_id)
            self._queued.discard(video_id)
            return True

    def status(self) -> dict:
        """Snapshot of queue state, for the web remote."""
        with self._lock:
            return {
                "current":   self._current,
                "pending":   len(self._queue),
                "completed": self._completed,
                "failed":    self._failed,
            }

    def is_pending(self, video_id: str) -> bool:
        with self._lock:
            return video_id in self._queued or video_id == self._current

    # ── Worker ───────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop:
            if self._gate is not None:
                try:
                    allowed = bool(self._gate())
                except Exception:
                    allowed = True
                if not allowed:
                    self._wake.wait(_IDLE_SLEEP)
                    self._wake.clear()
                    continue

            with self._lock:
                video_id = self._queue.popleft() if self._queue else None
                if video_id is not None:
                    self._queued.discard(video_id)
                    self._current = video_id

            if video_id is None:
                self._wake.wait(_IDLE_SLEEP)
                self._wake.clear()
                continue

            try:
                ok = self._download(video_id)
            except Exception as e:
                print(f"[download] {video_id}: unexpected error: {e}")
                ok = False

            with self._lock:
                self._current = None
                if ok:
                    self._completed += 1
                    self._failures.pop(video_id, None)
                else:
                    self._failed += 1
                    self._failures[video_id] = self._failures.get(video_id, 0) + 1

            time.sleep(DOWNLOAD_GAP)

    def _download(self, video_id: str) -> bool:
        """Fetch one video with yt-dlp.  Returns True if the file now exists."""
        # Re-validate at the sink; the ID has crossed a queue since the caller
        # checked it (CWE-22 defence in depth).
        _validate_video_id(video_id)

        if find_local_file(video_id):
            return True

        # Rebuilt from the validated ID, so the positional argument can never
        # be read by yt-dlp as an option.
        url = f"https://www.youtube.com/watch?v={video_id}"
        ffmpeg_args = ["--ffmpeg-location", FFMPEG_LOCATION] if FFMPEG_LOCATION else []

        print(f"[download] fetching {video_id}")
        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "yt_dlp",
                    *_cookie_args(),
                    *ffmpeg_args,
                    *_js_runtime_args(),
                    # Same format selection as downloader.py, so a background
                    # download is byte-for-byte what the offline script produces.
                    "--format",
                    "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/"
                    "bestvideo[height<=1080]+bestaudio/best",
                    "--merge-output-format", "mp4",
                    "--concurrent-fragments", "4",
                    "--output", os.path.join(VIDEOS_DIR, "%(id)s.%(ext)s"),
                    "--no-warnings",
                    "--no-playlist",
                    "--",           # end of options; url is positional
                    url,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=DOWNLOAD_TIMEOUT, encoding="utf-8", errors="replace",
                shell=False, env=_subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            print(f"[download] FAILED {video_id}: timed out after {DOWNLOAD_TIMEOUT}s")
            return False
        except Exception as e:
            print(f"[download] FAILED {video_id}: {e}")
            return False

        local = find_local_file(video_id)
        if result.returncode == 0 and local:
            size_mb = os.path.getsize(local) / 1_000_000
            print(f"[download] done {video_id} ({size_mb:.1f} MB)")
            # Populate title/channel/thumbnail so the remote and the channel
            # buttons can label the video without another YouTube round-trip.
            fetch_metadata(url)
            return True

        for line in (result.stderr or "").splitlines():
            if "ERROR" in line:
                print(f"[download] {video_id}: {_CONTROL_CHARS.sub('?', line.strip())}")
        print(f"[download] FAILED {video_id} (exit {result.returncode})")
        return False


# Process-wide instance used by the app.
manager = DownloadManager()
