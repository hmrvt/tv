"""
Desktop-side helper: stage already-downloaded videos into the
per-channel folder layout the Android kiosk app expects, ready for
`adb push`.

    python export_videos.py [--out DIR] [--max-per-channel N] [--channel NAME ...]

Reads channels.json (falling back to config.CHANNELS) and videos/ from the
main Toddler TV project one directory up, and only copies videos that were
already downloaded there — it does not fetch anything from the network.
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import CHANNELS, VIDEOS_DIR as _VIDEOS_DIR_REL  # noqa: E402
from media import get_video_id  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEOS_DIR = os.path.join(PROJECT_ROOT, _VIDEOS_DIR_REL)
_SAFE_FOLDER = re.compile(r"[^A-Za-z0-9_-]+")


def _channel_folder_name(channel_name):
    cleaned = _SAFE_FOLDER.sub("_", channel_name).strip("_")
    return cleaned or "Channel"


def _find_local_file(video_id):
    for ext in ("mp4", "mkv", "webm"):
        candidate = os.path.join(VIDEOS_DIR, f"{video_id}.{ext}")
        if os.path.isfile(candidate):
            return candidate
    return None


def export(out_dir, max_per_channel=None, only_channels=None):
    os.makedirs(out_dir, exist_ok=True)
    total_copied = 0
    total_missing = 0

    for channel in CHANNELS:
        name = channel.get("name", "Channel")
        if only_channels and name not in only_channels:
            continue

        # Version-2 channels.json (library.py) lists video IDs; an old file
        # still has a "playlist" array of URLs.
        urls = [f"https://www.youtube.com/watch?v={v['id']}"
                for v in channel.get("videos") or [] if isinstance(v, dict)]
        urls += list(channel.get("playlist") or [])

        dest_folder = os.path.join(out_dir, _channel_folder_name(name))
        copied_this_channel = 0
        seen_ids = set()

        for url in urls:
            if max_per_channel is not None and copied_this_channel >= max_per_channel:
                break
            try:
                video_id = get_video_id(url)
            except ValueError:
                continue
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            src = _find_local_file(video_id)
            if src is None:
                total_missing += 1
                continue

            os.makedirs(dest_folder, exist_ok=True)
            dest = os.path.join(dest_folder, os.path.basename(src))
            if not os.path.exists(dest):
                shutil.copy2(src, dest)
            copied_this_channel += 1
            total_copied += 1

        if copied_this_channel:
            print(f"[{name}] {copied_this_channel} video(s) -> {dest_folder}")

    print(f"\nDone: {total_copied} copied, {total_missing} not yet downloaded (skipped).")
    print(f"Staged at: {out_dir}\n")
    print("Push to the tablet with (the trailing '/.' copies contents, not the folder itself):")
    print("  adb shell mkdir -p /sdcard/ToddlerTV")
    print(f'  adb push "{out_dir}/." /sdcard/ToddlerTV/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "export"))
    parser.add_argument("--max-per-channel", type=int, default=None)
    parser.add_argument("--channel", action="append", dest="channels", default=None)
    args = parser.parse_args()

    export(args.out, max_per_channel=args.max_per_channel, only_channels=args.channels)


if __name__ == "__main__":
    main()
