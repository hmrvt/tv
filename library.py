"""
Toddler TV - Library
The channel catalog: which videos each channel plays, which the user has
ignored, and which YouTube playlists feed new videos in.  Stored in
channels.json, which this module owns:

    {
      "version": 2,
      "channels": [
        {
          "name": "Cartoons", "emoji": "\U0001f42d", "color": "#FF6B6B",
          "sources": [{"playlist_id": "PL...", "title": "...",
                       "last_sync": "2026-10-22 09:00", "last_count": 27}],
          "videos":  [{"id": "<video id>", "origin": "PL...", "added": "2026-10-22"}],
          "ignored": [{"id": "<video id>", "origin": "manual", "ignored": "2026-10-22"}]
        }
      ]
    }

The app, not YouTube, owns the playlist.  A source sync only ever *adds*:
a video that disappears from the YouTube playlist stays on the channel, and
a video the user removed (moved to "ignored") is not brought back by a sync.
Only the web remote takes videos off a channel.

Channels are addressed by slot index; the number of slots is fixed at start-up
(config.N).  Video and playlist IDs are validated on every load, because the
file is hand-editable.

One-shot injections ("Play once" on the web remote) are deliberately *not*
recorded here — they play once and are forgotten.
"""

import json
import os
import re
import shutil
import threading
from datetime import datetime

from media import (
    _validate_video_id, _SAFE_PLAYLIST_ID, validate_youtube_url,
    validate_youtube_playlist_url, get_video_id, get_playlist_urls, playlist_url,
)

CATALOG_FILE = "channels.json"
# Pre-version-2 per-channel edits; folded into the catalog by migration.
LEGACY_OVERLAY_FILE = "playlist_overlay.json"

CATALOG_VERSION = 2

MANUAL = "manual"   # origin of a video added by hand on the web remote

# Caps on hand-edited or web-supplied values (CWE-400)
MAX_SOURCES  = 20
MAX_VIDEOS   = 5_000
MAX_NAME_LEN = 40
MAX_EMOJI_LEN = 8
MAX_TITLE_LEN = 120

_HEX_COLOR = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")

# Guards every read-modify-write cycle; the web-handler thread and the
# playlist-refresh thread both write the catalog.
_lock = threading.RLock()


# ─────────────────────────────────────────────
#  VALIDATION
# ─────────────────────────────────────────────

def is_safe_playlist_id(value) -> bool:
    return isinstance(value, str) and bool(_SAFE_PLAYLIST_ID.match(value))


def _safe_video_id(value) -> str | None:
    try:
        return _validate_video_id(value) if isinstance(value, str) else None
    except ValueError:
        return None


def clean_name(value, fallback: str, max_len: int = MAX_NAME_LEN) -> str:
    """Strip control characters and cap the length of a name or title."""
    text = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()
    return text[:max_len] or fallback


def clean_emoji(value, fallback: str = "\U0001f4fa") -> str:
    text = re.sub(r"[\x00-\x20\x7f]", "", str(value or ""))
    return text[:MAX_EMOJI_LEN] or fallback


def clean_color(value, fallback: str = "#00ff88") -> str:
    text = str(value or "").strip()
    return text if _HEX_COLOR.match(text) else fallback


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────
#  NORMALISATION
# ─────────────────────────────────────────────

def _clean_entries(raw, date_key: str) -> list[dict]:
    """Validate a list of {"id", "origin", <date_key>} entries, dropping dupes."""
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        vid = _safe_video_id(item.get("id"))
        if vid is None or vid in seen:
            continue
        origin = item.get("origin")
        if origin != MANUAL and not is_safe_playlist_id(origin):
            origin = MANUAL
        seen.add(vid)
        out.append({"id": vid, "origin": origin,
                    date_key: str(item.get(date_key) or "")[:20]})
        if len(out) >= MAX_VIDEOS:
            break
    return out


def _clean_sources(raw) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = item.get("playlist_id")
        if not is_safe_playlist_id(pid) or pid in seen:
            continue
        seen.add(pid)
        try:
            count = max(0, int(item.get("last_count") or 0))
        except (TypeError, ValueError):
            count = 0
        out.append({
            "playlist_id": pid,
            "title":       clean_name(item.get("title"), pid, MAX_TITLE_LEN),
            "last_sync":   str(item.get("last_sync") or "")[:20],
            "last_count":  count,
        })
        if len(out) >= MAX_SOURCES:
            break
    return out


def _clean_channel(raw: dict, index: int) -> dict:
    videos = _clean_entries(raw.get("videos"), "added")
    in_feed = {v["id"] for v in videos}
    # A video can't be both on the channel and ignored; the feed wins.
    ignored = [v for v in _clean_entries(raw.get("ignored"), "ignored")
               if v["id"] not in in_feed]
    return {
        "name":    clean_name(raw.get("name"), f"Channel {index + 1}"),
        "emoji":   clean_emoji(raw.get("emoji")),
        "color":   clean_color(raw.get("color")),
        "sources": _clean_sources(raw.get("sources")),
        "videos":  videos,
        "ignored": ignored,
    }


# ─────────────────────────────────────────────
#  FILE I/O
# ─────────────────────────────────────────────

def _read_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[library] Could not read {path}: {e}")
        return None


def is_catalog(data) -> bool:
    return (isinstance(data, dict)
            and data.get("version") == CATALOG_VERSION
            and isinstance(data.get("channels"), list))


def load_catalog() -> list[dict]:
    """Read channels.json.  Returns [] if absent, unreadable or not version 2."""
    data = _read_json(CATALOG_FILE)
    if not is_catalog(data):
        return []
    return [_clean_channel(ch, i) for i, ch in enumerate(data["channels"])
            if isinstance(ch, dict)]


def save_catalog(channels: list[dict]) -> None:
    """Write the catalog atomically, keeping the previous file as a .bak."""
    tmp = CATALOG_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": CATALOG_VERSION, "channels": channels},
                      f, indent=2, ensure_ascii=False)
        if os.path.exists(CATALOG_FILE):
            shutil.copyfile(CATALOG_FILE, CATALOG_FILE + ".bak")
        os.replace(tmp, CATALOG_FILE)
    except OSError as e:
        print(f"[library] Could not write {CATALOG_FILE}: {e}")


def _edit(index: int, fn):
    """Run fn(channel, channels) under the lock and save if it returns truthy."""
    with _lock:
        channels = load_catalog()
        if not 0 <= index < len(channels):
            return None
        result = fn(channels[index], channels)
        if result:
            save_catalog(channels)
        return result


# ─────────────────────────────────────────────
#  MIGRATION FROM VERSION 1
# ─────────────────────────────────────────────

def fetch_source_ids(playlist_id: str) -> list[str]:
    """Expand a source playlist into validated video IDs ([] on any failure)."""
    try:
        url = playlist_url(playlist_id)
    except ValueError:
        return []
    return _ids_from_urls(get_playlist_urls(url))


def _ids_from_urls(urls) -> list[str]:
    out: list[str] = []
    for url in urls or []:
        canon = validate_youtube_url(url) if isinstance(url, str) else None
        if canon:
            out.append(get_video_id(canon))
    return out


def migrate_v1(fetch=None) -> bool:
    """Convert a version-1 channels.json (+ playlist_overlay.json) in place.

    fetch(playlist_id) -> list[str] | None, if given, seeds each channel from
    the live YouTube playlist rather than the saved "playlist" array, which may
    still hold videos that were since taken off YouTube.  The originals are
    kept as *.v1.bak.  Returns True if a migration ran.
    """
    with _lock:
        data = _read_json(CATALOG_FILE)
        if not isinstance(data, list):
            return False
        overlay = _read_json(LEGACY_OVERLAY_FILE)
        overlay = overlay if isinstance(overlay, dict) else {}
        today = _today()

        channels: list[dict] = []
        for i, old in enumerate(data):
            if not isinstance(old, dict):
                continue
            pid = validate_youtube_playlist_url(old.get("playlist_url") or "")
            name = clean_name(old.get("name"), f"Channel {i + 1}")
            edits = overlay.get(old.get("name")) or {}
            if not isinstance(edits, dict):
                edits = {}

            seed = fetch(pid) if (fetch and pid) else None
            if seed:
                print(f"[library] {name}: seeded {len(seed)} videos from YouTube")
            else:
                seed = _ids_from_urls(old.get("playlist"))
            origin = pid or MANUAL

            videos = [{"id": v, "origin": origin, "added": today} for v in seed]
            videos += [{"id": v, "origin": MANUAL, "added": today}
                       for v in _ids_from_urls(edits.get("added"))]
            removed = set(_ids_from_urls(edits.get("removed")))
            ignored = [{"id": v, "origin": origin, "ignored": today}
                       for v in _ids_from_urls(edits.get("removed"))]
            videos = [v for v in videos if v["id"] not in removed]

            channels.append(_clean_channel({
                "name": name, "emoji": old.get("emoji"), "color": old.get("color"),
                "sources": ([{"playlist_id": pid, "title": name}] if pid else []),
                "videos": videos, "ignored": ignored,
            }, i))

        shutil.copyfile(CATALOG_FILE, CATALOG_FILE + ".v1.bak")
        if os.path.exists(LEGACY_OVERLAY_FILE):
            os.replace(LEGACY_OVERLAY_FILE, LEGACY_OVERLAY_FILE + ".v1.bak")
        save_catalog(channels)
        print(f"[library] Migrated {CATALOG_FILE} to version {CATALOG_VERSION}")
        return True


# ─────────────────────────────────────────────
#  QUERIES
# ─────────────────────────────────────────────

def get_channel(index: int) -> dict | None:
    with _lock:
        channels = load_catalog()
    return channels[index] if 0 <= index < len(channels) else None


def video_ids(index: int) -> list[str]:
    ch = get_channel(index)
    return [v["id"] for v in ch["videos"]] if ch else []


def all_video_ids() -> set[str]:
    """Every video on any channel — the set of downloads worth keeping."""
    with _lock:
        return {v["id"] for ch in load_catalog() for v in ch["videos"]}


# ─────────────────────────────────────────────
#  EDITS
# ─────────────────────────────────────────────

def add_from_source(index: int, playlist_id: str, ids: list[str]) -> list[str]:
    """Append a source's videos that the channel has neither got nor ignored.

    Returns the IDs that were newly added.  Never removes anything.
    """
    def fn(ch, _channels):
        known = {v["id"] for v in ch["videos"]} | {v["id"] for v in ch["ignored"]}
        added: list[str] = []
        for raw in ids:
            vid = _safe_video_id(raw)
            if vid is None or vid in known:
                continue
            if len(ch["videos"]) >= MAX_VIDEOS:
                break
            known.add(vid)
            ch["videos"].append({"id": vid, "origin": playlist_id, "added": _today()})
            added.append(vid)
        for src in ch["sources"]:
            if src["playlist_id"] == playlist_id:
                src["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                src["last_count"] = len(ids)
        return added or True   # always save the sync time
    result = _edit(index, fn)
    return result if isinstance(result, list) else []


def add_manual(index: int, video_id: str) -> bool:
    """Put a video on the channel for good (un-ignoring it if need be)."""
    vid = _safe_video_id(video_id)
    if vid is None:
        return False

    return bool(_edit(index, lambda ch, _channels: _put_on_channel(ch, vid)))


def ignore(index: int, video_id: str) -> bool:
    """Take a video off the channel; a later sync won't bring it back."""
    vid = _safe_video_id(video_id)
    if vid is None:
        return False

    def fn(ch, _channels):
        entry = next((v for v in ch["videos"] if v["id"] == vid), None)
        if entry is None:
            return False
        ch["videos"].remove(entry)
        ch["ignored"].append({"id": vid, "origin": entry["origin"], "ignored": _today()})
        return True
    return bool(_edit(index, fn))


def restore(index: int, video_id: str) -> bool:
    """Move an ignored video back onto the channel."""
    vid = _safe_video_id(video_id)
    if vid is None:
        return False

    def fn(ch, _channels):
        if not any(v["id"] == vid for v in ch["ignored"]):
            return False
        return _put_on_channel(ch, vid)
    return bool(_edit(index, fn))


def _put_on_channel(ch: dict, vid: str) -> bool:
    """Make vid a channel member, keeping its origin if it was ignored."""
    if any(v["id"] == vid for v in ch["videos"]):
        return True
    if len(ch["videos"]) >= MAX_VIDEOS:
        return False
    origin = next((v["origin"] for v in ch["ignored"] if v["id"] == vid), MANUAL)
    ch["ignored"] = [v for v in ch["ignored"] if v["id"] != vid]
    ch["videos"].append({"id": vid, "origin": origin, "added": _today()})
    return True


def add_source(index: int, playlist_id: str, title: str = "") -> bool:
    if not is_safe_playlist_id(playlist_id):
        return False

    def fn(ch, _channels):
        if any(s["playlist_id"] == playlist_id for s in ch["sources"]):
            return True
        if len(ch["sources"]) >= MAX_SOURCES:
            return False
        ch["sources"].append({
            "playlist_id": playlist_id,
            "title":       clean_name(title, playlist_id, MAX_TITLE_LEN),
            "last_sync":   "", "last_count": 0,
        })
        return True
    return bool(_edit(index, fn))


def set_source_title(index: int, playlist_id: str, title: str) -> bool:
    def fn(ch, _channels):
        for src in ch["sources"]:
            if src["playlist_id"] == playlist_id:
                src["title"] = clean_name(title, playlist_id, MAX_TITLE_LEN)
                return True
        return False
    return bool(_edit(index, fn))


def remove_source(index: int, playlist_id: str) -> bool:
    """Stop syncing a playlist.  Its videos stay on the channel."""
    def fn(ch, _channels):
        before = len(ch["sources"])
        ch["sources"] = [s for s in ch["sources"] if s["playlist_id"] != playlist_id]
        return len(ch["sources"]) != before
    return bool(_edit(index, fn))


def update_channel(index: int, name, emoji, color) -> dict | None:
    """Change a channel's label.  Returns the cleaned values, or None."""
    def fn(ch, _channels):
        ch["name"] = clean_name(name, ch["name"])
        ch["emoji"] = clean_emoji(emoji, ch["emoji"])
        ch["color"] = clean_color(color, ch["color"])
        return {"name": ch["name"], "emoji": ch["emoji"], "color": ch["color"]}
    return _edit(index, fn)


def merge(src: int, dst: int) -> list[str] | None:
    """Move every video, ignored video and source from src into dst.

    src is left empty but keeps its name, ready to be repurposed.  Returns the
    video IDs that joined dst, or None if the indices are invalid.
    """
    if src == dst:
        return None
    with _lock:
        channels = load_catalog()
        if not (0 <= src < len(channels) and 0 <= dst < len(channels)):
            return None
        a, b = channels[src], channels[dst]
        have = {v["id"] for v in b["videos"]}
        moved = []
        for v in a["videos"]:
            if v["id"] not in have and len(b["videos"]) < MAX_VIDEOS:
                b["videos"].append(v)
                have.add(v["id"])
                moved.append(v["id"])
        ignored = {v["id"] for v in b["ignored"]}
        b["ignored"] += [v for v in a["ignored"]
                         if v["id"] not in have and v["id"] not in ignored]
        pids = {s["playlist_id"] for s in b["sources"]}
        b["sources"] += [s for s in a["sources"] if s["playlist_id"] not in pids]
        b["sources"] = b["sources"][:MAX_SOURCES]
        a["videos"], a["ignored"], a["sources"] = [], [], []
        save_catalog(channels)
        return moved
