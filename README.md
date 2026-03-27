# 📺 Toddler TV

A simulated live-TV experience for toddlers, running on a media PC. YouTube playlists become themed channels. The TV goes dark during nap time, meal times, and bedtime — with animated robot characters standing guard. Parents control the schedule from their phone.

---

## How it works

- **Channels** stream curated YouTube playlists (or locally cached videos) through VLC, giving the illusion of live TV — no menus, no recommendations, no rabbit holes.
- **Scheduled off-periods** automatically stop playback and show an animated robot scene (sleeping, lunch break, or "engineers at work"). The toddler cannot override this.
- **Persistent position** — switching away from a channel and returning resumes exactly where it left off, just like real TV.
- **Phone remote** — a web interface at `http://<pc-ip>:8080` lets a parent view and edit the off-period schedule from any device on the same Wi-Fi.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.12+ | |
| [VLC media player](https://www.videolan.org/) | Must be installed system-wide |
| ffmpeg | Optional but recommended for better stream quality; auto-detected |
| Node.js | Optional; used by yt-dlp for some YouTube bot-check bypasses |

---

## Setup

**1. Clone the repo**

```bash
git clone https://github.com/your-username/toddlertv.git
cd toddlertv
```

**2. Create a virtual environment and install dependencies**

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

**3. Configure your channels**

Copy the example below into a file called `channels.json` in the project root. Each channel needs a `name`, an `emoji`, a `color`, and either a `playlist` (individual video URLs) or a `playlist_url` (a YouTube playlist URL that gets expanded automatically).

```json
[
  {
    "name": "Cartoons",
    "emoji": "🐭",
    "color": "#FF6B6B",
    "playlist_url": "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID"
  },
  {
    "name": "Songs",
    "emoji": "🎵",
    "color": "#4ECDC4",
    "playlist": [
      "https://www.youtube.com/watch?v=VIDEO_ID_1",
      "https://www.youtube.com/watch?v=VIDEO_ID_2"
    ]
  }
]
```

> `channels.json` is git-ignored so your personal playlist choices stay private. The app falls back to placeholder channels defined in `config.py` if the file is absent.

**4. (Optional) Export YouTube cookies**

Some videos require authentication. Export your browser cookies to `cookies.txt` using yt-dlp:

```bash
python -m yt_dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

> `cookies.txt` is git-ignored. The app works without it for publicly accessible videos.

---

## Running

```bash
python toddlertv.py
```

The app launches fullscreen. Press `Escape` to exit fullscreen (press again to quit), or `F11` to toggle.

---

## Configuring the schedule

Off-periods are defined in `config.py` as fallback defaults and can be overridden at runtime via the web remote or by editing `schedule.json` (auto-created on first save).

**Default schedule in `config.py`:**

```python
OFF_PERIODS = [
    (12, 0,  12, 3,  "lunch"),     # Lunch: 12:00–12:03
    (19, 30, 23, 59, "sleeping"),  # Evening/night: 19:30–midnight
    (0,  0,   6, 50, "sleeping"),  # Early morning: midnight–06:50
]
```

Each entry is `(start_hour, start_min, end_hour, end_min, scene)`.

**Available scenes:**

| Scene | Display |
|---|---|
| `sleeping` | Robots Sleeping |
| `lunch` | Lunch Break |
| `fixing` | Engineers at Work |

---

## Phone remote

Open `http://<your-pc-ip>:8080` from any device on the same Wi-Fi. The interface shows the current off-period schedule and lets you add, edit, or delete entries. Changes take effect within 30 seconds.

The PC's IP address is printed to the console on startup:

```
[remote] Web remote running at http://192.168.1.42:8080
```

---

## Keyboard shortcuts

Designed for a **MK424 mini keyboard** mounted somewhere toddler-inaccessible, but works with any keyboard.

| Key | Action |
|---|---|
| `A` | Toggle power |
| `B` | Channel 1 |
| `C` | Channel 2 |
| `D` | Channel 3 |
| `F11` | Toggle fullscreen |
| `Escape` | Exit fullscreen / quit |

---

## Project structure

```
toddlertv.py      Main application — UI, state machine, VLC integration
config.py         Channel definitions and off-period defaults (edit this)
schedule.py       Off-period logic and schedule.json I/O
media.py          yt-dlp wrappers, ffmpeg detection, ChannelState
images.py         Thumbnail and channel avatar fetching
robot_canvas.py   Animated robot scenes (Tkinter canvas)
robot_editor.py   Interactive animation designer for robot poses
web_remote.py     Phone-accessible HTTP schedule editor

tests/
  test_unit.py    Unit tests — pure logic, no network or display
  test_e2e.py     End-to-end tests — schedule lifecycle, web remote, state machine
  test_toddler.py Chaos/stress tests — simulates a toddler smashing buttons
```

---

## Running the tests

```bash
python -m unittest discover tests/ -v
```

---

## How playback works

1. On startup, all channels begin resolving their playlists in background threads (staggered to avoid YouTube rate-limiting).
2. Each channel maintains a virtual clock so that switching away and returning resumes at the same position — simulating a live broadcast.
3. When a streaming URL is older than 5 minutes (YouTube URLs expire), it is silently re-resolved in the background before playback continues.

---

## Troubleshooting

**Black screen / no video**
- Confirm VLC is installed at the default path (`C:\Program Files\VideoLAN\VLC` on Windows).
- Run `python -m yt_dlp <url>` to test if yt-dlp can access the video.
- Try exporting fresh cookies with `--cookies-from-browser`.

**"Rate-limited" messages in the console**
- Normal for large playlists. The app automatically waits and retries.
- For large playlists, consider using a smaller initial playlist.

**Web remote not reachable from phone**
- Confirm both devices are on the same Wi-Fi network.
- Check Windows Firewall isn't blocking port 8080.

**Avatar images not showing for downloaded videos**
- Requires an internet connection to fetch channel metadata from YouTube.
- Metadata is fetched separately from the local file using `--skip-download`.
