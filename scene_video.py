"""Toddler TV - Video-backed off-air scenes.

Plays a looping video for each off-air scene in place of the hand-drawn
`RobotCanvas` animation.  The scene label, message and "back at HH:MM" line are
still drawn by Tk, on a sibling overlay above the video surface — the same trick
the QR overlay uses, since VLC renders into the frame's own window handle and
covers any child widgets.

Scenes fall back to `RobotCanvas` whenever a clip is missing or VLC refuses it,
so a deleted file degrades the picture rather than blanking the screen.

Audio
-----
Scene clips are played through a dedicated VLC instance created with
``--no-audio``, so no audio output is ever initialised for them.  The generated
clips ship with a non-silent AAC track and these scenes run in a child's bedroom
at night; muting is a correctness requirement, not a preference.
"""

import os
import tkinter as tk

from config import SCENES
from schedule import next_on_time

# ─────────────────────────────────────────────
#  SCENE → CLIP MAPPING
# ─────────────────────────────────────────────

SCENE_VIDEO_DIR = "RobotVideos"

# Hardcoded whitelist: a scene name is never concatenated into a path.  Unknown
# scenes resolve to None and the caller falls back to the drawn canvas.
SCENE_VIDEOS = {
    "sleeping":     "sleep.mp4",
    "lunch":        "eat.mp4",
    "fixing":       "work.mp4",
    "kita_goodbye": "kita.mp4",
    "tubby":        "tubby.mp4",
}

_DIR_REAL = os.path.realpath(SCENE_VIDEO_DIR)

# Repeat count passed to VLC as a media option.  VLC has no "loop forever" for a
# single media item, so this is a large finite count: at ~10s per clip it is
# roughly seven days of continuous playback, far longer than any off period.
_REPEAT = 60000

# Native aspect of the generated clips (2206x946, i.e. 21:9).  Used to letterbox
# against the parent's background instead of VLC's black.  This is very close to
# the app's own video area (~2.38:1), so the margins are now only a few pixels.
_CLIP_ASPECT = 2206 / 946


def scene_video_path(scene: str) -> str | None:
    """Absolute path to the clip for `scene`, or None if there isn't a usable one."""
    filename = SCENE_VIDEOS.get(scene)
    if filename is None:
        return None
    candidate = os.path.join(SCENE_VIDEO_DIR, filename)
    real = os.path.realpath(candidate)
    # Boundary check at the sink even though the source is a hardcoded mapping.
    if real != _DIR_REAL and not real.startswith(_DIR_REAL + os.sep):
        print(f"[scene] Path escape refused: {candidate!r}")
        return None
    if not os.path.isfile(real):
        return None
    return real


def available_scenes() -> set:
    """Scene names that currently have a playable clip on disk."""
    return {s for s in SCENE_VIDEOS if scene_video_path(s) is not None}


# ─────────────────────────────────────────────
#  PLAYER
# ─────────────────────────────────────────────

class SceneVideoPlayer:
    """A looping, silent video surface plus the Tk text overlay that sits on it."""

    def __init__(self, parent: tk.Misc, vlc_module, relheight: float = 0.76,
                 text_parent: tk.Misc | None = None):
        """`text_parent` hosts the info banner.

        Given one, the banner fills that container (the app puts it in the
        bottom panel, in place of the channel buttons, during an off period).
        Left None, it overlays the lower band of the video itself.
        """
        self._vlc        = vlc_module
        self._relheight  = relheight
        self._text_below = text_parent is not None
        self.scene       = None
        self._playing    = False
        self._attached   = False
        self._text_job   = None

        # Dedicated instance: --no-audio means no audio output is ever opened
        # for scene playback, whatever the clip itself contains.
        self.instance = vlc_module.Instance(
            "--quiet",
            "--no-audio",
            "--no-video-title-show",
            "--file-caching=1000",
        )
        self.player = self.instance.media_player_new()

        self._parent = parent
        self.frame = tk.Frame(parent, bg="#0d0721")
        self.overlay = tk.Canvas(
            text_parent if text_parent is not None else parent,
            bg="#0a0820", highlightthickness=0 if self._text_below else 1,
            highlightbackground="#2a1a5e")
        self.overlay.bind("<Configure>", self._on_resize)
        # The app can toggle fullscreen (F11), which changes the video area.
        self._parent_size = (0, 0)
        parent.bind("<Configure>", self._on_parent_resize, add="+")

    # ── lifecycle ────────────────────────────────────────────────────────────

    def can_play(self, scene: str) -> bool:
        return scene_video_path(scene) is not None

    def start(self, scene: str) -> bool:
        """Show and loop the clip for `scene`.  Returns False if it can't be played."""
        path = scene_video_path(scene)
        if path is None:
            return False

        self._place_frame()
        if self._text_below:
            # Fills its own panel, so the whole clip stays unobscured.
            self.overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            # Sits in the bottom band of the video, clear of the action: the
            # clips put their subject mid-frame, where the canvas left a gap.
            self.overlay.place(relx=0.5, rely=self._relheight, anchor="s",
                               relwidth=0.56, relheight=self._relheight * 0.21)
        self.frame.update_idletasks()

        if not self._attach():
            self.stop()
            return False

        try:
            media = self.instance.media_new(path)
            media.add_option(f"input-repeat={_REPEAT}")
            self.player.set_media(media)
            self.player.audio_set_mute(True)      # belt and braces
            self.player.audio_set_volume(0)
            if self.player.play() == -1:
                print(f"[scene] VLC refused {os.path.basename(path)}")
                self.stop()
                return False
        except Exception as exc:                  # noqa: BLE001 - never kill the UI
            print(f"[scene] Playback failed for {scene}: {exc}")
            self.stop()
            return False

        self.scene    = scene
        self._playing = True
        self._draw_text()
        self._schedule_text_refresh()
        return True

    def stop(self):
        if self._text_job is not None:
            try:
                self.overlay.after_cancel(self._text_job)
            except Exception:                     # noqa: BLE001
                pass
            self._text_job = None
        try:
            self.player.stop()
        except Exception:                         # noqa: BLE001
            pass
        self._playing = False
        self.scene    = None
        self.frame.place_forget()
        self.overlay.place_forget()

    @property
    def playing(self) -> bool:
        return self._playing

    # ── geometry ─────────────────────────────────────────────────────────────

    def _place_frame(self):
        """Size the video surface to the clip's own aspect, centred.

        The app's video area is about 2.38:1 (full width by 0.76 of height) but
        the clips are 16:9.  Letting VLC letterbox inside a full-width frame
        paints its own black bars; sizing the frame to the clip's aspect instead
        lets the parent's indigo show through, so the margins read as part of the
        design rather than as a broken picture.
        """
        self._parent.update_idletasks()
        pw = self._parent.winfo_width()
        ph = self._parent.winfo_height()
        if pw < 10 or ph < 10:
            self.frame.place(relx=0, rely=0, relwidth=1, relheight=self._relheight)
            return
        avail_h = int(ph * self._relheight)
        want_w = int(avail_h * _CLIP_ASPECT)
        if want_w <= pw:
            self.frame.place(relx=0.5, rely=0, anchor="n",
                             width=want_w, height=avail_h)
        else:
            # Very tall/narrow window: fit to width instead and centre vertically.
            fit_h = int(pw / _CLIP_ASPECT)
            self.frame.place(relx=0.5, rely=avail_h / 2 / ph, anchor="center",
                             width=pw, height=fit_h)

    # ── VLC window embedding ─────────────────────────────────────────────────

    def _attach(self) -> bool:
        """Bind the VLC output to this frame's window handle."""
        if self._attached:
            return True
        import sys
        try:
            wid = self.frame.winfo_id()
            if sys.platform == "win32":
                self.player.set_hwnd(wid)
            elif sys.platform == "darwin":
                self.player.set_nsobject(wid)
            else:
                self.player.set_xwindow(wid)
        except Exception as exc:                  # noqa: BLE001
            print(f"[scene] Could not attach VLC output: {exc}")
            return False
        self._attached = True
        return True

    # ── text overlay ─────────────────────────────────────────────────────────

    def _schedule_text_refresh(self):
        """Redraw every 30s so the 'back at HH:MM' line tracks the schedule."""
        if not self._playing:
            return
        self._text_job = self.overlay.after(30_000, self._refresh_text)

    def _refresh_text(self):
        if not self._playing:
            return
        self._draw_text()
        self._schedule_text_refresh()

    def _on_resize(self, _event=None):
        """Re-lay the banner whenever its container changes size."""
        if self._playing:
            self._draw_text()

    def _on_parent_resize(self, event=None):
        """Re-fit the video surface when the video area changes size."""
        size = (event.width, event.height) if event is not None else (0, 0)
        if size == self._parent_size:
            return                      # spurious event; avoids a place() loop
        self._parent_size = size
        if self._playing:
            self._place_frame()

    def _draw_text(self):
        """Mirror of RobotCanvas._draw_text, laid out for the overlay bar."""
        cv = self.overlay
        cv.delete("all")
        # No update_idletasks() here: it can re-enter through <Configure>.
        # Geometry changes arrive via _on_resize instead.
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 10 or h < 10:
            # Not laid out yet — retry, tracking the job so stop() can cancel
            # it rather than leaving an orphan chain running forever.
            self._text_job = cv.after(200, self._draw_text)
            return

        scene   = SCENES.get(self.scene, SCENES["sleeping"])
        on_time = next_on_time()
        cx      = w // 2

        # Scale with the bar so the text stays inside it on any screen.
        msg_pt   = max(14, min(28, int(h * 0.30)))
        label_pt = max(8, min(12, int(h * 0.13)))
        sub_pt   = max(9, min(14, int(h * 0.15)))

        cv.create_text(cx, h * 0.17, text=scene["label"],
                       fill="#4a3a7e", font=("Courier New", label_pt, "bold"),
                       anchor="center")
        cv.create_text(cx + 2, h * 0.50 + 2, text=scene["msg"],
                       fill="#003322", font=("Courier New", msg_pt, "bold"),
                       anchor="center")
        cv.create_text(cx, h * 0.50, text=scene["msg"],
                       fill="#00ff88", font=("Courier New", msg_pt, "bold"),
                       anchor="center")
        cv.create_text(cx, h * 0.85,
                       text=f"{scene['sub']}  ·  back at {on_time}",
                       fill="#444466", font=("Courier New", sub_pt),
                       anchor="center")
