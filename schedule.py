"""
Toddler TV - Schedule
Handles off-period merging and current schedule queries.
Loads from schedule.json if present, otherwise falls back to config.py defaults.
Auto-reloads schedule.json when it changes on disk.

Period tuple format: (start_h, start_m, end_h, end_m, scene, days, paused)
  days  : list of int 0–6 (Mon–Sun).  Empty list = every day.
  paused: bool.  True = period is saved but ignored.

Manual overrides from the web remote live in manual_status.json:
  * a manual status (e.g. bath time) switches the TV to that scene now and
    overrides every period until it is switched off;
  * "Pause schedules" ignores every period, keeping the TV on, until resumed.
Either way the periods themselves are untouched, so the schedule picks up
exactly where it was once the override ends.
"""

import json
import os
from datetime import datetime

from config import OFF_PERIODS as DEFAULT_OFF_PERIODS, SCENES

SCHEDULE_FILE = "schedule.json"
STATUS_FILE = "manual_status.json"
_cached_periods = None
_cached_mtime = 0.0
_cached_status = None
_cached_status_mtime = 0.0


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


_NO_OVERRIDES = {"scene": None, "since": "", "paused": False, "paused_since": ""}


def _load_overrides() -> dict:
    """Read the manual overrides from STATUS_FILE (absent file = none).

    Cached on mtime like the periods, since the app polls every few seconds.
    The file is hand-editable, so the scene is whitelisted on every load.
    """
    global _cached_status, _cached_status_mtime

    try:
        mtime = os.path.getmtime(STATUS_FILE)
    except OSError:
        _cached_status, _cached_status_mtime = None, 0.0
        return dict(_NO_OVERRIDES)
    if mtime == _cached_status_mtime and _cached_status is not None:
        return dict(_cached_status)

    overrides = dict(_NO_OVERRIDES)
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        scene = data.get("scene")
        if scene in SCENES:
            overrides["scene"] = scene
            overrides["since"] = str(data.get("since") or "")[:5]
        elif scene is not None:
            print(f"[schedule] Ignoring unknown manual scene {scene!r} in {STATUS_FILE}")
        if data.get("schedules_paused") is True:
            overrides["paused"] = True
            overrides["paused_since"] = str(data.get("paused_since") or "")[:5]
    except Exception as e:
        print(f"[schedule] Error reading {STATUS_FILE}: {e}")
    _cached_status, _cached_status_mtime = overrides, mtime
    return dict(overrides)


def _save_overrides(overrides: dict) -> None:
    """Write the overrides, removing the file once nothing is overridden."""
    global _cached_status, _cached_status_mtime
    if not overrides["scene"] and not overrides["paused"]:
        try:
            os.remove(STATUS_FILE)
        except FileNotFoundError:
            pass
        _cached_status, _cached_status_mtime = None, 0.0
        return
    data = {}
    if overrides["scene"]:
        data.update(scene=overrides["scene"], since=overrides["since"])
    if overrides["paused"]:
        data.update(schedules_paused=True, paused_since=overrides["paused_since"])
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    _cached_status, _cached_status_mtime = dict(overrides), os.path.getmtime(STATUS_FILE)


def _now_hm() -> str:
    return datetime.now().strftime("%H:%M")


def get_manual_status() -> dict | None:
    """Return {"scene", "since"} while a manual status is on, else None."""
    o = _load_overrides()
    return {"scene": o["scene"], "since": o["since"]} if o["scene"] else None


def set_manual_status(scene: str) -> bool:
    """Switch the TV to `scene` now, overriding the schedule until cleared."""
    if scene not in SCENES:
        return False
    o = _load_overrides()
    o.update(scene=scene, since=_now_hm())
    _save_overrides(o)
    print(f"[schedule] Manual status on: {scene}")
    return True


def clear_manual_status() -> None:
    """End the manual status; the schedule takes over again."""
    o = _load_overrides()
    if o["scene"]:
        print("[schedule] Manual status off — back to the schedule")
    o.update(scene=None, since="")
    _save_overrides(o)


def get_schedule_pause() -> dict | None:
    """Return {"since"} while every period is paused, else None."""
    o = _load_overrides()
    return {"since": o["paused_since"]} if o["paused"] else None


def pause_schedules() -> None:
    """Ignore every period until resumed, so the TV stays on.

    The periods themselves (including their own "paused" flags) are not
    touched, so resuming brings back exactly the ones that were enabled.
    """
    o = _load_overrides()
    if not o["paused"]:
        o.update(paused=True, paused_since=_now_hm())
        _save_overrides(o)
        print("[schedule] All schedules paused")


def resume_schedules() -> None:
    o = _load_overrides()
    if o["paused"]:
        o.update(paused=False, paused_since="")
        _save_overrides(o)
        print("[schedule] Schedules resumed")


def current_off_period() -> dict | None:
    """Return the active off period dict, or None if TV should be on.
    A manual status wins over everything (end_h/end_m are then None); while
    schedules are paused no period applies.
    Respects paused flag and days-of-week filter."""
    o = _load_overrides()
    if o["scene"]:
        return {"end_h": None, "end_m": None, "scene": o["scene"], "manual": True}
    if o["paused"]:
        return None

    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    for start, end, scene in todays_periods(now):
        if start <= now_mins < end:
            return {"start_h": start // 60, "start_m": start % 60,
                    "end_h": end // 60, "end_m": end % 60, "scene": scene}
    return None


def todays_periods(now: datetime | None = None) -> list:
    """Merged (start_min, end_min, scene) off periods that apply today.

    Skips each period's own paused flag and the days filter; the global
    "Pause schedules" override is the caller's concern.
    """
    today = (now or datetime.now()).weekday()   # 0=Monday … 6=Sunday
    active = []
    for sh, sm, eh, em, scene, days, paused in _load_periods():
        if paused:
            continue
        if days and today not in days:
            continue
        active.append((sh, sm, eh, em, scene))
    return merge_off_periods(active)


def next_off_period() -> dict | None:
    """The next scheduled break later today, or None (also while paused)."""
    if _load_overrides()["paused"]:
        return None
    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    for start, end, scene in todays_periods(now):
        if start > now_mins:
            return {"start_h": start // 60, "start_m": start % 60,
                    "end_h": end // 60, "end_m": end % 60, "scene": scene}
    return None


def is_tv_off() -> bool:
    return current_off_period() is not None


def next_on_time() -> str:
    period = current_off_period()
    if not period or period["end_h"] is None:
        return "soon"   # a manual status has no end time
    return f"{period['end_h']:02d}:{period['end_m']:02d}"


def back_text() -> str:
    """On-screen hint: "back at 12:30", or "back soon" with no known end."""
    on_time = next_on_time()
    return "back soon" if on_time == "soon" else f"back at {on_time}"


def current_scene_name() -> str:
    period = current_off_period()
    return period["scene"] if period else "sleeping"
