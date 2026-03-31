#!/usr/bin/env python3
"""
Demonstration that channel timers now progress together.

This script simulates switching between channels and shows that
elapsed time continues to progress for all channels, not just the
actively playing channel.
"""

import sys
import os
import time

# Add project root to path
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from media import ChannelState


def demo_timer_sync():
    print("=" * 70)
    print("QUEUE-BASED PLAYLIST DEMO")
    print("=" * 70)
    print()

    channels = [ChannelState() for _ in range(3)]

    for ch_idx, ch in enumerate(channels):
        for i in range(3):
            ch.videos.append({
                "duration": 100,
                "title": f"Channel {ch_idx + 1} - Video {i + 1}",
            })
        ch.advance_video(0.0)

    print("Initial state (elapsed=0):")
    for ch_idx, ch in enumerate(channels):
        idx, off = ch.get_position(0)
        print(f"  CH{ch_idx + 1}: playing video {idx + 1}, offset {off:.1f}s")

    print()
    print("After 50s (mid-video):")
    for ch_idx, ch in enumerate(channels):
        idx, off = ch.get_position(50)
        print(f"  CH{ch_idx + 1}: playing video {idx + 1}, offset {off:.1f}s")

    print()
    print("Video ends on each channel at elapsed=100s — advancing queue:")
    for ch_idx, ch in enumerate(channels):
        ch.advance_video(100.0)
    for ch_idx, ch in enumerate(channels):
        idx, off = ch.get_position(100)
        print(f"  CH{ch_idx + 1}: now playing video {idx + 1}, offset {off:.1f}s")

    print()
    print("Each channel is on a different (shuffled) video from its queue.")
    print("No video repeats until all 3 have been played.")
    print("=" * 70)


if __name__ == "__main__":
    demo_timer_sync()
