"""
Toddler TV — Android Kiosk
A stripped-down, offline video kiosk for a tablet in airplane mode.

Videos live on device storage under BASE_DIR, one sub-folder per channel:

    /sdcard/ToddlerTV/Cartoons/*.mp4
    /sdcard/ToddlerTV/Songs/*.mp4
    /sdcard/ToddlerTV/Learning/*.mp4

Use export_videos.py (desktop side) + adb push to populate that layout.
No network, no scheduling, no remote control — just fullscreen looping
playback with a hidden long-press gesture to change channels.
"""
import json
import os

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.video import Video
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout
from kivy.properties import NumericProperty

BASE_DIR_CANDIDATES = [
    "/sdcard/ToddlerTV",
    "/storage/emulated/0/ToddlerTV",
]
VIDEO_EXTS = (".mp4", ".mkv", ".webm")
HOLD_SECONDS = 1.5          # how long an edge must be held to change channel
EDGE_FRACTION = 0.18        # width of the invisible edge zones, as a fraction of screen width
STATE_FILENAME = "kiosk_state.json"


def _is_android():
    try:
        from kivy.utils import platform
        return platform == "android"
    except Exception:
        return False


def _request_android_permissions():
    if not _is_android():
        return
    try:
        from android.permissions import request_permissions, Permission
        perms = [Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE]
        manage_all = getattr(Permission, "MANAGE_EXTERNAL_STORAGE", None)
        if manage_all:
            perms.append(manage_all)
        request_permissions(perms)
    except Exception as e:
        print(f"[permissions] request failed: {e}")


def _keep_screen_on_and_immersive():
    if not _is_android():
        return
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        WindowManager = autoclass("android.view.WindowManager$LayoutParams")
        View = autoclass("android.view.View")
        activity = PythonActivity.mActivity
        window = activity.getWindow()
        window.addFlags(WindowManager.FLAG_KEEP_SCREEN_ON)

        flags = (
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_FULLSCREEN
            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        )
        decor_view = window.getDecorView()
        decor_view.setSystemUiVisibility(flags)
    except Exception as e:
        print(f"[immersive] setup failed: {e}")


def _find_base_dir():
    for path in BASE_DIR_CANDIDATES:
        if os.path.isdir(path):
            return path
    return BASE_DIR_CANDIDATES[0]


def _discover_channels(base_dir):
    """Return [(channel_name, [sorted video paths]), ...] for sub-folders that contain videos."""
    channels = []
    if not os.path.isdir(base_dir):
        return channels
    for entry in sorted(os.listdir(base_dir)):
        folder = os.path.join(base_dir, entry)
        if not os.path.isdir(folder):
            continue
        videos = sorted(
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(VIDEO_EXTS)
        )
        if videos:
            channels.append((entry, videos))
    return channels


class EdgeZone(Widget):
    """Invisible touch zone. Fires on_hold_complete after being held for HOLD_SECONDS."""
    hold_progress = NumericProperty(0.0)

    def __init__(self, direction, on_hold_complete, **kwargs):
        super().__init__(**kwargs)
        self.direction = direction
        self._on_hold_complete = on_hold_complete
        self._event = None

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        self._event = Clock.schedule_once(self._fire, HOLD_SECONDS)
        return True

    def on_touch_up(self, touch):
        if self._event:
            self._event.cancel()
            self._event = None
        return False

    def _fire(self, _dt):
        self._event = None
        self._on_hold_complete(self.direction)


class KioskRoot(FloatLayout):
    pass


class KioskApp(App):
    def build(self):
        Window.clearcolor = (0, 0, 0, 1)
        _request_android_permissions()
        Clock.schedule_once(lambda _dt: _keep_screen_on_and_immersive(), 0.5)

        self.base_dir = _find_base_dir()
        self.channels = _discover_channels(self.base_dir)
        self.state_path = os.path.join(self.user_data_dir, STATE_FILENAME)
        self.channel_index, self.video_index = self._load_state()

        self.root_widget = KioskRoot()
        self.video = Video(state="stop", options={"eos": "stop"}, allow_stretch=True)
        self.video.bind(eos=self._on_video_end)
        self.root_widget.add_widget(self.video)

        left = EdgeZone(
            "prev", self._change_channel,
            size_hint=(EDGE_FRACTION, 1), pos_hint={"x": 0, "y": 0},
        )
        right = EdgeZone(
            "next", self._change_channel,
            size_hint=(EDGE_FRACTION, 1), pos_hint={"right": 1, "y": 0},
        )
        self.root_widget.add_widget(left)
        self.root_widget.add_widget(right)

        Window.bind(on_keyboard=self._on_keyboard)

        if self.channels:
            self._play_current()
        else:
            print(f"[kiosk] no channels found under {self.base_dir}")

        return self.root_widget

    # ── state persistence ────────────────────────────────────────────
    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("channel_index", 0)), int(data.get("video_index", 0))
        except Exception:
            return 0, 0

    def _save_state(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"channel_index": self.channel_index, "video_index": self.video_index},
                    f,
                )
        except Exception as e:
            print(f"[state] save failed: {e}")

    # ── playback ─────────────────────────────────────────────────────
    def _play_current(self):
        if not self.channels:
            return
        self.channel_index %= len(self.channels)
        _name, videos = self.channels[self.channel_index]
        self.video_index %= len(videos)
        self.video.source = videos[self.video_index]
        self.video.state = "play"
        self._save_state()

    def _on_video_end(self, _instance, eos):
        if not eos or not self.channels:
            return
        _name, videos = self.channels[self.channel_index]
        self.video_index = (self.video_index + 1) % len(videos)
        self._play_current()

    def _change_channel(self, direction):
        if not self.channels:
            return
        delta = 1 if direction == "next" else -1
        self.channel_index = (self.channel_index + delta) % len(self.channels)
        self.video_index = 0
        self._play_current()

    # ── input ────────────────────────────────────────────────────────
    def _on_keyboard(self, _window, key, *_args):
        if key == 27:  # Android back button / Escape
            return True  # consume it — stay in kiosk mode
        return False

    def on_pause(self):
        return True  # survive app-switch / screen-off without being killed

    def on_resume(self):
        _keep_screen_on_and_immersive()


if __name__ == "__main__":
    KioskApp().run()
