# Toddler TV — Android Kiosk

A separate, minimal project that packages a subset of already-downloaded
videos into an offline Android APK. No YouTube, no scheduling, no phone
remote — just fullscreen looping playback of local files, meant for a
tablet left in airplane mode.

This is **not** a port of the desktop app (`toddlertv.py`). The desktop app
uses Tkinter + VLC, neither of which runs on Android. This is a from-scratch
Kivy app that reuses only the *channel definitions* and *already-downloaded
video files* from the main project.

---

## How it works

- The app scans `/sdcard/ToddlerTV/<ChannelName>/` for video files at
  startup. Each sub-folder is a channel; files play in sorted order and
  loop back to the start.
- Last channel/video position is saved to the app's private storage so a
  restart doesn't always jump back to the first video.
- There is no visible UI. Holding a finger on the far left or right edge
  of the screen for ~1.5 seconds switches to the previous/next channel.
  A plain tap does nothing, so it's much harder for a toddler to trigger
  by accident than a normal button.
- The back button is swallowed so the app can't be backed out of. Screen
  stays on and the status/nav bars are hidden (immersive mode).

---

## 1. Stage videos for a channel folder layout

From this directory, on the desktop machine that already has `videos/`
populated and `channels.json` configured:

```bash
python export_videos.py
```

This copies already-downloaded videos (it does **not** download anything)
into `android_kiosk/export/<ChannelName>/<id>.mp4`, matching each
channel's `channels.json` playlist. Useful flags:

```bash
python export_videos.py --max-per-channel 15        # cap size per channel
python export_videos.py --channel Cartoons --channel Songs   # only some channels
python export_videos.py --out D:\staging             # custom output location
```

## 2. Push videos to the tablet

With the tablet connected via USB (developer options + USB debugging
enabled):

```bash
adb shell mkdir -p /sdcard/ToddlerTV
adb push "export/." /sdcard/ToddlerTV/
```

Re-run `export_videos.py` + `adb push` any time you want to swap the video
set — no APK rebuild needed, since videos live on device storage rather
than inside the app package.

## 3. Build the APK

Buildozer only runs on Linux (or WSL on Windows). From this directory,
inside a Linux/WSL shell with buildozer installed:

```bash
pip install buildozer cython
buildozer android debug
```

The first run downloads the Android SDK/NDK (several GB) and can take a
while. Output APK lands in `bin/toddlertvkiosk-1.0-arm64-v8a_armeabi-v7a-debug.apk`.

Install it:

```bash
adb install -r bin/toddlertvkiosk-*-debug.apk
```

## 4. Grant storage access

`buildozer.spec` pins `android.api = 28`, which predates Android's scoped
storage restrictions, so a plain storage permission prompt on first launch
is enough — no need to visit Settings for "All files access." If you raise
`android.api` above 28 later, you'll need to add the
`MANAGE_EXTERNAL_STORAGE` "All files access" flow (Settings →
Apps → Toddler TV Kiosk → Permissions) instead, since Android blocks that
permission from a runtime dialog on API 29+.

## 5. Lock the tablet into the app (optional but recommended)

Buildozer/Kivy can't fully replace the launcher or block the recents/home
buttons on a stock tablet — that needs either:

- **Screen pinning** (built into Android, no extra tools): open Recents,
  tap the app icon, choose "Pin". Exits require holding Back+Recents
  together, which a toddler won't stumble into.
- **Device owner kiosk mode** (stronger, more setup): on a freshly
  factory-reset tablet with no Google account added yet,
  `adb shell dpm set-device-owner org.toddlertv.toddlertvkiosk/.KioskDeviceAdminReceiver`
  gives the app permission to lock itself into full kiosk mode
  permanently. This requires adding a `DeviceAdminReceiver` to the
  project, which isn't included here — screen pinning is enough for most
  use cases.

---

## Project structure

```
main.py            Kivy app — video playback, channel switching, kiosk lockdown
buildozer.spec      Android build configuration
export_videos.py    Desktop helper — stages already-downloaded videos into per-channel folders
```

---

## Known limitations

- `ffpyplayer` (Kivy's video backend) supports fewer codecs than VLC.
  Videos downloaded via yt-dlp as `mp4`/H.264 play fine; anything
  transcoded to an exotic codec may not.
- No thumbnails, channel icons, or any visual chrome by design — this is
  the "greatly reduced UI" kiosk variant, not a feature port of the
  desktop app's channel grid.
- Screen pinning must be re-armed manually after a device reboot (Android
  doesn't persist it across restarts).
