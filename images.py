"""
Toddler TV - Images
Thumbnail and channel-avatar fetching with thread-safe caching.
"""

import io
import re
import threading
import urllib.request

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def fetch_thumbnail(url: str, size=(120, 90)):
    """Fetch a thumbnail image from URL. Returns PIL Image (not ImageTk) or None.
    The caller must convert to ImageTk.PhotoImage on the main Tk thread."""
    if not PIL_AVAILABLE or not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = r.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")

        # Crop to target aspect ratio then resize (no padding, no circles)
        target_ratio = size[0] / size[1]
        src_ratio    = img.width / img.height
        if src_ratio > target_ratio:
            new_w = int(img.height * target_ratio)
            x0 = (img.width - new_w) // 2
            img = img.crop((x0, 0, x0 + new_w, img.height))
        elif src_ratio < target_ratio:
            new_h = int(img.width / target_ratio)
            y0 = (img.height - new_h) // 2
            img = img.crop((0, y0, img.width, y0 + new_h))

        img = img.resize(size, Image.LANCZOS)
        return img  # Return PIL Image, NOT ImageTk.PhotoImage
    except Exception:
        return None


# Global avatar cache: channel_id -> ImageTk.PhotoImage | False | None
# None  = not yet fetched
# False = permanently failed
_avatar_cache: dict[str, object] = {}
_avatar_lock = threading.Lock()


def fetch_channel_avatar(channel_id: str, size: int = 48) -> object:
    """Fetch and cache a channel's profile picture as a square PIL Image.
    Scrapes the avatar URL from the YouTube channel page.
    The caller must convert to ImageTk.PhotoImage on the main Tk thread.
    Returns None if unavailable or Pillow not installed."""
    if not PIL_AVAILABLE or not channel_id:
        return None

    with _avatar_lock:
        if channel_id in _avatar_cache:
            return _avatar_cache[channel_id]
        _avatar_cache[channel_id] = None  # mark in-progress

    avatar_url = None
    try:
        req = urllib.request.Request(
            f"https://www.youtube.com/channel/{channel_id}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; toddlertv/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")

        # 1. og:image (most reliable)
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            avatar_url = m.group(1)

        # 2. Fallback: "avatar":{"thumbnails":[{"url":"..."}]}
        if not avatar_url:
            m = re.search(r'"avatar"\s*:\s*\{"thumbnails"\s*:\s*\[\{"url"\s*:\s*"([^"]+)"', html)
            if m:
                avatar_url = m.group(1).replace("\\u0026", "&")

        if not avatar_url or not avatar_url.startswith("http"):
            print(f"[avatar] Could not extract URL for channel {channel_id}")
            with _avatar_lock:
                _avatar_cache[channel_id] = False
            return None

        print(f"[avatar] Fetching image for channel {channel_id}")
        with urllib.request.urlopen(avatar_url, timeout=8) as r:
            data = r.read()
        img  = Image.open(io.BytesIO(data)).convert("RGB")
        side = min(img.width, img.height)
        x0   = (img.width  - side) // 2
        y0   = (img.height - side) // 2
        img  = img.crop((x0, y0, x0 + side, y0 + side))
        img  = img.resize((size, size), Image.LANCZOS)
        # Store as PIL Image, NOT ImageTk.PhotoImage (thread-unsafe on newer Tk)
        print(f"[avatar] Ready for channel {channel_id}")
        with _avatar_lock:
            _avatar_cache[channel_id] = img
        return img

    except Exception as e:
        print(f"[avatar] Error for {channel_id}: {e}")
        with _avatar_lock:
            _avatar_cache[channel_id] = False
        return None


def get_cached_avatar(channel_id: str) -> object:
    """Return the cached avatar for a channel: PIL Image, PhotoImage, False (failed), or None (not fetched)."""
    with _avatar_lock:
        return _avatar_cache.get(channel_id)


def set_cached_avatar(channel_id: str, photo) -> None:
    """Store a converted PhotoImage back into the cache (call on the main Tk thread)."""
    with _avatar_lock:
        _avatar_cache[channel_id] = photo


def is_avatar_fetched(channel_id: str) -> bool:
    """Return True if an avatar fetch has been initiated for this channel_id."""
    with _avatar_lock:
        return channel_id in _avatar_cache