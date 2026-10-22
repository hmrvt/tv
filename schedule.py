"""
Toddler TV - Schedule
Handles off-period merging and current schedule queries.
Loads from schedule.json if present, otherwise falls back to config.py defaults.
Auto-reloads schedule.json when it changes on disk.

Period tuple format: (start_h, start_m, end_h, end_m, scene, days, paused)
  days  : list of int 0–6 (Mon–Sun).  Empty list = every day.
  paused: bool.  True = period is saved but ignored.
"""

import json
import os
from datetime import datetime

from config import OFF_PERIODS as DEFAULT_OFF_PERIODS

SCHEDULE_FILE = "schedule.json"
_cached_periods = None
_cached_mtime = 0.0


def _normalise(p) -> tuple:
    """Accept a 5- or 7-element period and return a canonical 7-tuple."""
    if len(p) >= 7:
        sh, sm, eh, em, scene, days, paused = p[:7]
        return (sh, sm, eh, em, scene, list(days), bool(paused))
    sh, sm, eh, em, scene = p[:5]
    return (sh, sm, eh, em, scene, [], False)


def _load_periods() -> list:
    """Load off-periods from schedule.json, or fall back to config.py defaults.
    Caches the result and only re-reads the file when its mtime changes."""
    global _cached_periods, _cached_mtime

    if not os.path.exists(SCHEDULE_FILE):
        if _cached_periods is None:
            _cached_periods = [_normalise(p) for p in DEFAULT_OFF_PERIODS]
        return _cached_periods

    try:
        mtime = os.path.getmtime(SCHEDULE_FILE)
    except OSError:
        return _cached_periods or [_normalise(p) for p in DEFAULT_OFF_PERIODS]

    if mtime == _cached_mtime and _cached_periods is not None:
        return _cached_periods

    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        periods = []
        for entry in data:
            periods.append((
                int(entry["start_h"]), int(entry["start_m"]),
                int(entry["end_h"]),   int(entry["end_m"]),
                str(entry["scene"]),
                [int(d) for d in entry.get("days", [])],
                bool(entry.get("paused", False)),
            ))
        _cached_periods = periods
        _cached_mtime = mtime
        print(f"[schedule] Loaded {len(periods)} period(s) from {SCHEDULE_FILE}")
    except Exception as e:
        print(f"[schedule] Error reading {SCHEDULE_FILE}: {e}")
        if _cached_periods is None:
            _cached_periods = [_normalise(p) for p in DEFAULT_OFF_PERIODS]

    return _cached_periods


def get_periods() -> list:
    """Public accessor for the current off-period list."""
    return _load_periods()


def save_periods(periods: list) -> None:
    """Save off-periods to schedule.json.  Accepts 5- or 7-element tuples."""
    global _cached_periods, _cached_mtime
    normalised = [_normalise(p) for p in periods]
    data = [
        {
            "start_h": sh, "start_m": sm,
            "end_h":   eh, "end_m":   em,
            "scene":   scene,
            "days":    days,
            "paused":  paused,
        }
        for sh, sm, eh, em, scene, days, paused in normalised
    ]
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    _cached_periods = normalised
    _cached_mtime = os.path.getmtime(SCHEDULE_FILE)
    print(f"[schedule] Saved {len(normalised)} period(s) to {SCHEDULE_FILE}")


def merge_off_periods(periods):
    """Merge consecutive/adjacent off periods with the same scene.
    Expects 5-element tuples (sh, sm, eh, em, scene)."""
    if not periods:
        return []

    converted = sorted(
        [(sh * 60 + sm, eh * 60 + em, scene) for sh, sm, eh, em, scene in periods]
    )

    merged = [converted[0]]
    for start, end, scene in converted[1:]:
        prev_start, prev_end, prev_scene = merged[-1]
        if scene == prev_scene and start - prev_end <= 1:
            merged[-1] = (prev_start, max(prev_end, end), prev_scene)
        else:
            merged.append((start, end, scene))

    return merged


def current_off_period() -> dict | None:
    """Return the active off period dict, or None if TV should be on.
    Respects paused flag and days-of-week filter."""
    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    today = now.weekday()  # 0=Monday … 6=Sunday

    active = []
    for sh, sm, eh, em, scene, days, paused in _load_periods():
        if paused:
            continue
        if days and today not in days:
            continue
        active.append((sh, sm, eh, em, scene))

    for start, end, scene in merge_off_periods(active):
        if start <= now_mins < end:
            return {"end_h": end // 60, "end_m": end % 60, "scene": scene}
    return None


def is_tv_off() -> bool:
    return current_off_period() is not None


def next_on_time() -> str:
    period = current_off_period()
    return f"{period['end_h']:02d}:{period['end_m']:02d}" if period else "soon"


def current_scene_name() -> str:
    period = current_off_period()
    return period["scene"] if period else "sleeping"
