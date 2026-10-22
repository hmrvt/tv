[app]
title = Toddler TV Kiosk
package.name = toddlertvkiosk
package.domain = org.toddlertv

source.dir = .
source.include_exts = py,kv,png,jpg,atlas

version = 1.0

requirements = python3,kivy,ffpyplayer,pyjnius

orientation = landscape
fullscreen = 1

icon.filename = %(source.dir)s/icon.png

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE

# Kiosk apps are side-loaded, not Play Store distributed, so we can pin an
# older target API to avoid Android 10+ scoped storage entirely. Bump this
# (and switch the storage access approach in main.py) if that changes.
android.api = 28
android.minapi = 24
android.ndk_api = 24

android.archs = arm64-v8a, armeabi-v7a

# Prevent the tablet's launcher/recents/back-button chrome from being usable
# to escape the app. Full "device owner" kiosk mode requires an MDM profile
# or `adb shell dpm set-device-owner`, which is out of scope for buildozer —
# see README.md for the manual lock-task-mode setup.

[buildozer]
log_level = 2
warn_on_root = 1
