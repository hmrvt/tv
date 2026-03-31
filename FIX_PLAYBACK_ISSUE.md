# Fix: Infinite Retry Loop During Playback Errors

## Problem

Videos stopped playing entirely, with repeated error messages:
```
[channel 0] video 1, seek 1305.8s, stale URL
[vlc] Error during seek: State.Ended
```

This occurred because:
1. When VLC encountered an error or ended prematurely during seek, the error handlers would call `_play_video_for_channel()` to retry
2. If the URL was invalid or refresh failed, VLC would immediately fail again
3. This created an **infinite retry loop** on the same video, preventing the stream from ever advancing
4. No mechanism existed to skip bad videos or limit retries

## Solution

Implemented retry tracking and intelligent skipping:

### Changes Made

1. **Video State Tracking** (`_add_video`)
   - Added `_play_retries` counter initialized to 0 for each video
   - Added `_failed` flag to mark videos that can't be played

2. **Retry Limit Enforcement** (`_play_video_for_channel`)
   - Max retries per video: 3 attempts
   - After 3 failures, mark video as `_failed` and skip to next video
   - Reset `_play_retries` counter on successful URL refresh
   - Safety: if all videos are marked failed, reset and try again

3. **Increment on Error** (`_wait_vlc_playing` and `_wait_seek_done`)
   - Increment `_play_retries` whenever VLC enters Error or Ended state
   - This ensures we don't waste time retrying permanently broken videos

### Behavior

**Before Fix:**
- Bad video causes infinite retry loop
- Stream hangs indefinitely
- Application becomes unresponsive

**After Fix:**
- Bad video retries 3 times
- After 3 failures, automatically skips to next video
- Stream continues to next available content
- Graceful degradation even with problematic videos

## Testing

Manual test scenarios:
1. ✅ Normal video playback works unchanged
2. ✅ Stale URL refresh works as before
3. ✅ Video that fails to play is skipped after 3 retries
4. ✅ Stream continues with next available video
5. ✅ If entire queue is bad, attempts reset and cycle again

## Files Modified

- `toddlertv.py`:
  - `_add_video()`: Initialize `_play_retries` counter
  - `_play_video_for_channel()`: Add retry logic and video skipping
  - `_wait_vlc_playing()`: Increment retry counter on error
  - `_wait_seek_done()`: Increment retry counter on error
