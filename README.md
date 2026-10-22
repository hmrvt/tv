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

Copy the example below into a file called `channels.json` in the project root to get started. Each channel needs a `name`, an `emoji`, a `color`, and either a `playlist` (individual video URLs) or a `playlist_url` (a YouTube playlist URL).

On first start the app converts this file into its own catalog format (the original is kept as `channels.json.v1.bak`). From then on the app owns the playlists: manage videos, YouTube source playlists, channel names and colours from the phone remote rather than by editing the file.

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

Open `http://<your-pc-ip>:8080` from any device on the same Wi-Fi. The landing page shows the off-period schedule and lets you add, edit, or delete entries; changes take effect within 30 seconds. Tap a channel card to manage what that channel is playing.

**Per-channel pages** — each channel has five tabs:

| Tab | What you can do |
|---|---|
| **Now playing** (`/ch/<n>`) | **Skip** the current video; **Skip** an upcoming one for this round only; **Remove** a video (moves it to Ignored and deletes its download); **Add to this channel** (plays next, downloads, stays for good); **Play once, then forget** (never downloaded or saved). |
| **Videos** (`/ch/<n>/videos`) | Every video on the channel, where it came from, and whether it is downloaded. **Remove** moves it to Ignored. |
| **Ignored** (`/ch/<n>/ignored`) | Videos you removed. **Restore** puts one back, plays it next and downloads it again. |
| **YouTube playlists** (`/ch/<n>/sources`) | The playlists that feed the channel. **Add playlist** pulls its videos in straight away; **Stop using this playlist** keeps its videos but stops checking it; **Check YouTube playlists for new videos** syncs now instead of waiting. |
| **Settings** (`/ch/<n>/settings`) | Rename the channel, change its emoji and colour, or **merge** it into another channel (everything moves across; this channel is left empty, ready to reuse). |

Rows are tagged `SAVED` downloaded, `GETTING` downloading, `STREAM` plays from YouTube, `ONCE` a one-shot injection.

> **How YouTube playlists are used:** the app owns each channel's playlist, stored in `channels.json`. Every 30 minutes (and 90 s after start-up) it checks each channel's YouTube playlists and adds any new videos, apart from ones you have ignored. Taking a video out of a playlist on YouTube does **not** remove it from the TV — only the remote does that. The app never writes to YouTube.

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

## Keeping playlists up to date

The app re-reads every channel's YouTube playlist every 30 minutes while it runs (`ToddlerTV.PLAYLIST_REFRESH_MINS`), so a restart is no longer needed to pick up changes.

- **Videos added to the playlist** are queued to play right after whatever is on screen, shuffled so they don't arrive in upload order, and downloaded ahead of everything else in the queue.
- **Videos removed from the playlist** drop out of the rotation, and their downloaded file is deleted.
- **Missing downloads** are filled in by a background worker, one at a time, pausing while a channel is buffering a stream. Running `downloader.py` by hand is now optional — it is still there for a bulk first fetch before the app starts.
- **A failed playlist fetch changes nothing.** If yt-dlp returns an empty list, the feed is left alone and no files are deleted, so a network blip can't wipe the local library.

Local files live in `videos/`, named `<video-id>.mp4`. Files no longer referenced by any channel are swept after a successful sync of every channel.

---

## Troubleshooting

**Black screen / no video**
- Confirm VLC is installed at the default path (`C:\Program Files\VideoLAN\VLC` on Windows).
- Run `python -m yt_dlp <url>` to test if yt-dlp can access the video.
- Try exporting fresh cookies with `--cookies-from-browser`.

**A new video in the playlist hasn't appeared**
- The automatic sync runs every 30 minutes; tap **⟳ Sync Playlist Now** on the channel page to force it.
- Check the console for `[CH1] refresh: empty playlist response` — that means the fetch failed and the feed was deliberately left untouched.

**A downloaded file didn't get deleted**
- Windows won't delete a file VLC still has open. The next sweep picks it up.

**"Rate-limited" messages in the console**
- Normal for large playlists. The app automatically waits and retries.
- For large playlists, consider using a smaller initial playlist.

**Web remote not reachable from phone**
- Confirm both devices are on the same Wi-Fi network.
- Check Windows Firewall isn't blocking port 8080.

**Avatar images not showing for downloaded videos**
- Requires an internet connection to fetch channel metadata from YouTube.
- Metadata is fetched separately from the local file using `--skip-download`.
