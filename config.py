"""
Toddler TV - Configuration
Edit this file to change channels, off periods, and layout constants.
"""

import json
import os
import sys

# ─────────────────────────────────────────────
#  OFF SCHEDULE  ← edit this!
#
#  Each entry: (start_hour, start_min, end_hour, end_min, scene)
#  scene options: "sleeping" | "lunch" | "fixing"
# ─────────────────────────────────────────────
OFF_PERIODS = [
    (11, 0,  12, 30,  "lunch"),
    (19, 30, 23, 59, "sleeping"),
    (0,  0,   6, 50, "sleeping"),
]

# ─────────────────────────────────────────────
#  SCENE LABELS (shown on the off-screen animation)
# ─────────────────────────────────────────────
SCENES = {
    "sleeping": {"label": "ROBOTS SLEEPING",  "msg": "TV is resting...", "sub": "come back later!"},
    "lunch":    {"label": "LUNCH BREAK",       "msg": "Taking a break!",  "sub": "back soon..."},
    "fixing":   {"label": "ENGINEERS AT WORK", "msg": "Fixing things up!","sub": "almost ready..."},
}

# ─────────────────────────────────────────────
#  CHANNELS  (overridden by channels.json if present)
# ─────────────────────────────────────────────
CHANNELS = [
    {
        "name": "Cartoons",
        "emoji": "🐭",
        "color": "#FF6B6B",
        "playlist": ["https://www.youtube.com/watch?v=jNQXAC9IVRw"],
    },
    {
        "name": "Songs",
        "emoji": "🎵",
        "color": "#4ECDC4",
        "playlist": ["https://www.youtube.com/watch?v=jNQXAC9IVRw"],
    },
    {
        "name": "Learning",
        "emoji": "🌟",
        "color": "#FFD93D",
        "playlist": ["https://www.youtube.com/watch?v=jNQXAC9IVRw"],
    },
    {
        "name": "Animals",
        "emoji": "🐘",
        "color": "#6BCB77",
        "playlist": ["https://www.youtube.com/watch?v=jNQXAC9IVRw"],
    },
]

if os.path.exists("channels.json"):
    with open("channels.json", encoding="utf-8") as f:
        CHANNELS = json.load(f)

N = len(CHANNELS)

# ─────────────────────────────────────────────
#  LAYOUT CONSTANTS
# ─────────────────────────────────────────────

# Pixels of padding on every edge to keep content clear of the TV bezel
BEZEL_PAD = 18

# Pixels of padding around the video frame to avoid bezel clipping
VIDEO_INSET = 12

VIDEOS_DIR = "videos"

# ─────────────────────────────────────────────
#  VLC / FFMPEG SETUP
# ─────────────────────────────────────────────

# Add VLC to DLL search path (Windows)
if sys.platform == "win32":
    vlc_path = r"C:\Program Files\VideoLAN\VLC"
    if os.path.exists(vlc_path):
        os.add_dll_directory(vlc_path)
