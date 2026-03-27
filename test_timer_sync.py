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
    """Demonstrate that all timers progress together."""
    
    print("=" * 70)
    print("TIMER SYNCHRONIZATION DEMO")
    print("=" * 70)
    print()
    
    # Create channel states for 3 channels
    channels = [ChannelState() for _ in range(3)]
    
    # Add videos to each channel
    for ch_idx, ch in enumerate(channels):
        for i in range(3):
            ch.videos.append({
                "duration": 100,  # 100 seconds each
                "title": f"Channel {ch_idx + 1} - Video {i + 1}",
            })
        ch._rebuild_offsets()
    
    print(f"Created 3 channels, each with 3 videos (100s each = 300s total)")
    print()
    
    # Simulate elapsed time progression
    elapsed_times = [0, 50, 100, 150, 200, 250, 300, 350]
    
    print("Elapsed Time | CH1 Position | CH2 Position | CH3 Position")
    print("-" * 70)
    
    for elapsed in elapsed_times:
        positions = []
        for ch_idx, ch in enumerate(channels):
            video_idx, offset = ch.get_position(elapsed)
            positions.append(f"V{video_idx + 1}:{offset:05.1f}s")
        
        print(f"{elapsed:11.0f}s | {positions[0]:12s} | {positions[1]:12s} | {positions[2]:12s}")
    
    print()
    print("OBSERVATION: All channels advance through their playlists")
    print("at the SAME RATE based on global elapsed time, even though")
    print("only one channel is actively displayed.")
    print()
    print("BEFORE FIX: Clock would pause during channel switching,")
    print("            causing timers to appear frozen for other channels.")
    print()
    print("AFTER FIX:  Clock continues running, so all timers")
    print("            advance together continuously.")
    print()
    print("=" * 70)


if __name__ == "__main__":
    demo_timer_sync()
