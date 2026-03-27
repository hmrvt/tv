"""
Toddler TV - Video Downloader
Downloads all videos from channels.json to a local 'videos' folder.
Run this once before starting the app to reduce loading times.

Usage:
  py -3.12 downloader.py                     (uses cookies.txt)
  py -3.12 downloader.py --browser firefox    (pulls cookies live from browser)
"""

import subprocess
import json
import os
import sys
import time
import argparse
from pathlib import Path

from media import FFMPEG_LOCATION, clean_url, get_video_id

PYTHON = sys.executable

# ── Config ────────────────────────────────────
VIDEOS_DIR = Path("videos")
COOKIES_FILE = "cookies.txt"
CHANNELS_FILE = "channels.json"

# Set by CLI args in main()
BROWSER = None


# ── Cookie / auth helpers ────────────────────
def _cookie_args() -> list[str]:
    """Return the yt-dlp cookie arguments based on CLI config."""
    if BROWSER:
        return ["--cookies-from-browser", BROWSER]
    return ["--cookies", COOKIES_FILE]


def _auth_args() -> list[str]:
    """Return the full set of yt-dlp auth + runtime arguments."""
    return [
        *_cookie_args(),
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]


# ── Helpers ───────────────────────────────────
def video_path(video_id: str) -> Path | None:
    """Return path to downloaded file if it exists, else None."""
    for ext in ("mp4", "mkv", "webm"):
        p = VIDEOS_DIR / f"{video_id}.{ext}"
        if p.exists():
            return p
    return None


def download_video(youtube_url: str) -> bool:
    """Download a video. Returns True on success."""
    youtube_url = clean_url(youtube_url)
    video_id = get_video_id(youtube_url)

    # Skip if already downloaded
    existing = video_path(video_id)
    if existing:
        print(f"  ✅ Already downloaded: {existing.name}")
        return True

    print(f"  ⬇️  Downloading: {youtube_url}")
    try:
        ffmpeg_args = ["--ffmpeg-location", FFMPEG_LOCATION] if FFMPEG_LOCATION else []
        result = subprocess.run(
            [
                PYTHON, "-m", "yt_dlp",
                *_auth_args(),
                *ffmpeg_args,
                "--format", "bestvideo[height<=1080]+bestaudio/best",
                "--merge-output-format", "mp4",
                "--output", str(VIDEOS_DIR / "%(id)s.%(ext)s"),
                "--no-warnings",
                "--no-playlist",
                youtube_url,
            ],
            timeout=600,  # 10 min max per video
        )
        if result.returncode == 0:
            existing = video_path(video_id)
            if existing:
                size_mb = existing.stat().st_size / 1_000_000
                print(f"  ✅ Saved: {existing.name} ({size_mb:.1f} MB)")
                return True
        print(f"  ❌ Failed (return code {result.returncode})")
        return False
    except subprocess.TimeoutExpired:
        print(f"  ❌ Timed out after 10 minutes")
        return False
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def expand_playlist(playlist_url: str) -> list[str]:
    """Use yt-dlp --flat-playlist to get all video URLs from a playlist."""
    print(f"  📋 Expanding playlist: {playlist_url}")
    try:
        ffmpeg_args = ["--ffmpeg-location", FFMPEG_LOCATION] if FFMPEG_LOCATION else []
        result = subprocess.run(
            [
                PYTHON, "-m", "yt_dlp",
                *_auth_args(),
                "--flat-playlist",
                "--print", "url",
                "--no-warnings",
                *ffmpeg_args,
                playlist_url,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120, encoding="utf-8", errors="replace",
        )
        urls = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if not line.startswith("http"):
                line = f"https://www.youtube.com/watch?v={line}"
            urls.append(line)
        print(f"  📋 Found {len(urls)} video(s) in playlist")
        return urls
    except Exception as e:
        print(f"  ⚠️  Failed to expand playlist: {e}")
        return []


# ── Main ──────────────────────────────────────
def main():
    global BROWSER

    parser = argparse.ArgumentParser(description="Toddler TV Video Downloader")
    parser.add_argument(
        "--browser", "-b",
        help="Pull cookies live from browser (e.g. firefox, chrome, edge) instead of cookies.txt",
    )
    args = parser.parse_args()
    BROWSER = args.browser

    # Check cookies source exists
    if BROWSER:
        print(f"🍪 Using live cookies from: {BROWSER}")
    elif os.path.exists(COOKIES_FILE):
        print(f"🍪 Using cookies file: {COOKIES_FILE}")
    else:
        print(f"❌ {COOKIES_FILE} not found.")
        print("Either generate it:")
        print(f"  py -3.12 -m yt_dlp --cookies-from-browser firefox "
              f"--cookies {COOKIES_FILE} --skip-download "
              f"\"https://www.youtube.com/watch?v=jNQXAC9IVRw\"")
        print("Or use --browser to pull cookies directly:")
        print(f"  py -3.12 downloader.py --browser firefox")
        sys.exit(1)

    # Load channels
    if not os.path.exists(CHANNELS_FILE):
        print(f"❌ {CHANNELS_FILE} not found.")
        sys.exit(1)

    with open(CHANNELS_FILE, encoding="utf-8") as f:
        channels = json.load(f)

    # Collect all unique URLs — prefer playlist_url, fall back to playlist array
    all_urls = []
    seen = set()
    for ch in channels:
        ch_name = ch["name"]
        urls = []

        playlist_url = ch.get("playlist_url", "")
        if playlist_url:
            print(f"\n🔍 {ch_name}: expanding playlist_url...")
            urls = expand_playlist(playlist_url)

        if not urls:
            # Fall back to individual video URLs
            urls = [clean_url(u) for u in ch.get("playlist", []) if u.strip()]
            if urls:
                print(f"\n🔍 {ch_name}: using {len(urls)} individual URL(s)")

        for url in urls:
            clean = clean_url(url)
            if clean not in seen:
                seen.add(clean)
                all_urls.append((ch_name, clean))

    if not all_urls:
        print("No videos found in channels.json")
        sys.exit(0)

    print(f"📺 Toddler TV Downloader")
    print(f"{'─' * 40}")
    print(f"Found {len(all_urls)} unique video(s) across {len(channels)} channel(s)")
    print(f"Saving to: {VIDEOS_DIR.absolute()}")
    print()

    # Create videos directory
    VIDEOS_DIR.mkdir(exist_ok=True)

    # Download each video (with delay between to avoid rate-limiting)
    success = 0
    failed = 0
    for i, (channel_name, url) in enumerate(all_urls, 1):
        print(f"[{i}/{len(all_urls)}] {channel_name}")
        if download_video(url):
            success += 1
        else:
            failed += 1
        print()
        # Small delay between downloads to avoid YouTube rate-limiting
        if i < len(all_urls):
            time.sleep(3)

    # Summary
    print(f"{'─' * 40}")
    print(f"✅ Downloaded: {success}")
    if failed:
        print(f"❌ Failed:     {failed}")
    print()
    print("Done! The Toddler TV app will now use local files automatically.")
    print("Run: py -3.12 toddlertv.py")


if __name__ == "__main__":
    main()