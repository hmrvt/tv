"""
Toddler TV - RobotCanvas
Animated canvases displayed when the TV is off.

  RobotCanvas     — full-screen animated canvas (main video area)
  MiniRobotCanvas — small per-channel-button version with channel emoji prop
  RobotWorldCanvas — panoramic scene shown in the bottom panel during off periods
"""

import math
import random
import time
import tkinter as tk

from config import SCENES
from schedule import next_on_time

_MINI_SCENES = ["sleeping", "lunch", "working"]

# Frame-rate scale factor: at 60fps each frame is half the wall-clock time vs
# the original 30fps reference, so all phase coefficients must be halved.
_PHASE = 0.5

# ─────────────────────────────────────────────────────────────────────────────
#  PALETTE
# ─────────────────────────────────────────────────────────────────────────────

_SKY         = "#0d0721"
_GROUND      = "#1a0a3e"
_ROAD        = "#111122"
_GLOW        = "#00ff88"
_DIM         = "#2a1a4e"
_MOON        = "#FFD93D"
_RED         = "#FF6B6B"
_TEAL        = "#4ECDC4"
_DARK_BODY   = "#1a0a3e"
_PANEL       = "#04080f"
_STAR_COLS   = ["#ffffff", "#aaccff", "#ffeeaa", "#ccffee", "#ffccee"]

# Daytime / scene sky palettes
_DAY_SKY_TOP   = "#5ba3d9"
_DAY_SKY_BOT   = "#87CEEB"
_DAY_GROUND    = "#2a6b2a"
_DAY_GROUND_DK = "#1d5218"
_WARM_SKY_TOP  = "#e07a3a"   # kita_goodbye
_WARM_SKY_BOT  = "#f4c47a"
_EVE_SKY_TOP   = "#6b3fa0"   # tubby (evening bath)
_EVE_SKY_BOT   = "#c9a8e0"
_OVER_SKY_TOP  = "#546e7a"   # fixing (overcast)
_OVER_SKY_BOT  = "#90a4ae"

# Per-scene activity pools for RobotWorldCanvas agents
_SCENE_ACTS = {
    "sleeping":    ["sleep", "sleep", "sleep", "wave"],
    "lunch":       ["eat", "eat", "dance", "wave", "walk"],
    "fixing":      ["fix", "fix", "carry", "carry", "walk"],
    "kita_goodbye":["wave", "cheer", "dance", "run", "wave"],
    "tubby":       ["dance", "wave", "cheer", "walk", "dance"],
}

# Activity catalogue — (name, eye_l, eye_r, mouth, arm_l, arm_r, leg_l, leg_r, tilt)
_ACTIVITIES = {
    "walk":  ("walk",  "●", "●", "—",    0,    0,   0,   0,   0),
    "run":   ("run",   "●", "●", "D",   10,  -10,  12, -12,   0),
    "wave":  ("wave",  "^", "^", ")",  -25,    0,   0,   0,   5),
    "eat":   ("eat",   "^", "^", "U",    0,  -18,   4,  -4,   8),
    "sleep": ("sleep", "—", "—", "~",   14,    8,   6,  -5,  20),
    "dance": ("dance", "*", "*", "D",  -20,   20,  10, -10,   0),
    "fix":   ("fix",   "●", "●", "/",    0,  -22,   5,  -3,  14),
    "cheer": ("cheer", "^", "^", "D",  -28,  -28,   0,   0,   0),
    "skate": ("skate", "●", "^", ")",    6,  -12,   0,  18,  -8),
    "carry": ("carry", "●", "●", "—",  -20,  -20,   4,  -4,   0),
}
_ACT_KEYS = list(_ACTIVITIES.keys())

_CAR_COLORS = [
    ("#FF6B6B", "#ffaaaa"), ("#4ECDC4", "#aaffee"),
    ("#FFD93D", "#fff3aa"), ("#6BCB77", "#aaffaa"),
    ("#cc88ff", "#eebbff"), ("#FF8C42", "#ffc299"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  MODULE-LEVEL DRAWING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _eased_sin(phase: float) -> float:
    """Sin wave with smoothstep shaping — arms and legs linger at extremes."""
    raw  = math.sin(phase)
    sign = 1 if raw >= 0 else -1
    a    = abs(raw)
    return sign * a * a * (3 - 2 * a)


def _rounded_rect_points(x1, y1, x2, y2, radius):
    """Flat point list for create_polygon(smooth=True) rounded rectangle."""
    r = radius
    return [
        x1 + r, y1,   x2 - r, y1,
        x2,     y1,   x2,     y1 + r,
        x2,     y2 - r, x2,   y2,
        x2 - r, y2,   x1 + r, y2,
        x1,     y2,   x1,     y2 - r,
        x1,     y1 + r, x1,   y1,
    ]


def _draw_eye(canvas, cx, cy, r, style, color):
    """Draw a robot eye at (cx, cy) with radius r."""
    r = max(1.5, r)
    if style == "●":
        canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=color, outline="")
        # Specular highlight
        hr = r * 0.35
        canvas.create_oval(cx-r*0.55, cy-r*0.6, cx-r*0.55+hr, cy-r*0.6+hr,
                           fill="#ffffff", outline="")
    elif style == "◉":
        canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill=color, outline="")
        canvas.create_oval(cx-r*0.5, cy-r*0.5, cx+r*0.5, cy+r*0.5, fill="#000820", outline="")
        canvas.create_oval(cx-r*0.2, cy-r*0.2, cx+r*0.2, cy+r*0.2, fill=color, outline="")
    elif style == "—":
        canvas.create_line(cx-r*0.85, cy, cx+r*0.85, cy,
                          fill=color, width=max(1, int(r*0.5)))
    elif style in ("^", "*"):
        w = max(1, int(r * 0.45))
        canvas.create_line(cx-r, cy+r*0.3, cx, cy-r*0.6, fill=color, width=w)
        canvas.create_line(cx, cy-r*0.6, cx+r, cy+r*0.3, fill=color, width=w)
    elif style == "·":
        canvas.create_oval(cx-r*0.4, cy-r*0.4, cx+r*0.4, cy+r*0.4, fill=color, outline="")
    elif style == "X":
        w = max(1, int(r * 0.4))
        canvas.create_line(cx-r*0.7, cy-r*0.7, cx+r*0.7, cy+r*0.7, fill=color, width=w)
        canvas.create_line(cx+r*0.7, cy-r*0.7, cx-r*0.7, cy+r*0.7, fill=color, width=w)
    else:
        canvas.create_text(cx, cy, text=style, fill=color,
                          font=("Courier New", max(5, int(r * 1.7))))


def _draw_mouth(canvas, cx, cy, hw, style, color):
    """Draw a robot mouth. hw = half-width of the mouth area."""
    sw = max(1, int(hw * 0.22))
    if style == "—":
        canvas.create_line(cx-hw*0.7, cy, cx+hw*0.7, cy, fill=color, width=sw)
    elif style in ("D", ")"):
        pts = [(-0.65, 0.4), (-0.3, -0.15), (0.3, -0.15), (0.65, 0.4)]
        for i in range(len(pts)-1):
            canvas.create_line(cx+pts[i][0]*hw, cy+pts[i][1]*hw,
                              cx+pts[i+1][0]*hw, cy+pts[i+1][1]*hw,
                              fill=color, width=sw)
    elif style == "U":
        pts = [(-0.65, -0.25), (-0.45, 0.35), (0, 0.5), (0.45, 0.35), (0.65, -0.25)]
        for i in range(len(pts)-1):
            canvas.create_line(cx+pts[i][0]*hw, cy+pts[i][1]*hw,
                              cx+pts[i+1][0]*hw, cy+pts[i+1][1]*hw,
                              fill=color, width=sw)
    elif style == "/":
        canvas.create_line(cx-hw*0.6, cy+hw*0.4, cx+hw*0.6, cy-hw*0.3, fill=color, width=sw)
    elif style == "~":
        for i in range(3):
            x1 = cx - hw*0.65 + i*hw*0.45
            canvas.create_line(x1, cy+hw*0.25, x1+hw*0.22, cy-hw*0.2, fill=color, width=sw)
    elif style == "o":
        ro = hw * 0.42
        canvas.create_oval(cx-ro, cy-ro*0.8, cx+ro, cy+ro*0.8,
                          fill=_PANEL, outline=color, width=max(1, sw))
    elif style == "!":
        canvas.create_line(cx, cy-hw*0.3, cx, cy+hw*0.1, fill=color, width=sw)
        canvas.create_oval(cx-hw*0.15, cy+hw*0.28, cx+hw*0.15, cy+hw*0.52,
                          fill=color, outline="")
    else:
        canvas.create_text(cx, cy, text=style, fill=color,
                          font=("Courier New", max(5, int(hw * 0.85))))


def _draw_robot_full(canvas, cx, cy, s, tilt, arm_l, arm_r, leg_l, leg_r,
                     eye_l, eye_r, mouth, body_color, glow):
    """Draw a detailed robot. s = scale factor (1.0 ≈ 160px tall reference)."""
    c  = canvas.create_oval
    r  = canvas.create_rectangle
    l  = canvas.create_line

    # ── Ground shadow — soft ellipse tinted with body colour ────────────────
    _sr = int(body_color[1:3], 16) // 6
    _sg = int(body_color[3:5], 16) // 6
    _sb = int(body_color[5:7], 16) // 6
    canvas.create_oval(cx-28*s, cy+71*s, cx+28*s, cy+78*s,
                       fill=f"#{_sr:02x}{_sg:02x}{_sb:02x}", outline="")

    # ── Legs ─────────────────────────────────────────────────────────────────
    for lx, la in [(cx-10*s, leg_l), (cx+10*s, leg_r)]:
        l(lx, cy+50*s, lx + la*s, cy+72*s,
          fill=glow, width=max(2, int(5*s)), capstyle=tk.ROUND)
    # Boot soles (rounded ovals)
    c(cx-21*s+leg_l*s, cy+68*s, cx-1*s+leg_l*s,  cy+78*s, fill=glow, outline="")
    c(cx+1*s+leg_r*s,  cy+68*s, cx+21*s+leg_r*s, cy+78*s, fill=glow, outline="")

    # ── Body ─────────────────────────────────────────────────────────────────
    body_pts = _rounded_rect_points(cx-22*s, cy+7*s, cx+22*s, cy+55*s, 8*s)
    canvas.create_polygon(body_pts, fill=body_color, outline=glow,
                          width=max(1, int(2*s)), smooth=True)

    # Chest panel
    r(cx-13*s, cy+18*s, cx+13*s, cy+39*s, fill=_PANEL, outline=glow, width=1)
    # LED indicator row
    for i, dot_c in enumerate([glow, _MOON, _RED]):
        dx = cx + (-7 + i*7) * s
        c(dx-2*s, cy+21*s, dx+2*s, cy+25*s, fill=dot_c, outline="")
    # Chest scan lines
    for yi in range(3):
        yh = cy + (29 + yi*3)*s
        l(cx-9*s, yh, cx+9*s, yh, fill="#003322", width=1)
    # Vent slots at bottom of body
    for yi in range(2):
        yh = cy + (41 + yi*3)*s
        l(cx-10*s, yh, cx+10*s, yh, fill=glow, width=1)

    # ── Arms ─────────────────────────────────────────────────────────────────
    l(cx-22*s, cy+22*s, cx-34*s, cy+22*s+arm_l*s,
      fill=glow, width=max(2, int(5*s)), capstyle=tk.ROUND)
    c(cx-38*s, cy+18*s+arm_l*s, cx-30*s, cy+26*s+arm_l*s, fill=glow, outline="")
    l(cx+22*s, cy+22*s, cx+34*s, cy+22*s+arm_r*s,
      fill=glow, width=max(2, int(5*s)), capstyle=tk.ROUND)
    c(cx+30*s, cy+18*s+arm_r*s, cx+38*s, cy+26*s+arm_r*s, fill=glow, outline="")

    # ── Neck ─────────────────────────────────────────────────────────────────
    r(cx-5*s, cy+2*s, cx+5*s, cy+12*s, fill=body_color, outline=glow, width=1)
    l(cx-4*s, cy+5*s,  cx+4*s, cy+5*s,  fill=glow, width=1)
    l(cx-4*s, cy+9*s,  cx+4*s, cy+9*s,  fill=glow, width=1)

    # ── Head ─────────────────────────────────────────────────────────────────
    tx = cx + tilt * 0.4
    ty = cy - 8*s
    # Head — single rounded shape
    head_pts = _rounded_rect_points(tx-20*s, ty-31*s, tx+20*s, ty+6*s, 9*s)
    canvas.create_polygon(head_pts, fill=body_color, outline=glow,
                          width=max(1, int(2*s)), smooth=True)

    # Eye housings (recessed panels) — rounded rectangles
    eh_l = _rounded_rect_points(tx-17*s, ty-25*s, tx-3*s,  ty-11*s, 3*s)
    eh_r = _rounded_rect_points(tx+3*s,  ty-25*s, tx+17*s, ty-11*s, 3*s)
    canvas.create_polygon(eh_l, fill=_PANEL, outline=glow, width=1, smooth=True)
    canvas.create_polygon(eh_r, fill=_PANEL, outline=glow, width=1, smooth=True)
    # Eyes
    _draw_eye(canvas, tx-10*s, ty-18*s, 5*s, eye_l, glow)
    _draw_eye(canvas, tx+10*s, ty-18*s, 5*s, eye_r, glow)

    # Mouth housing
    r(tx-12*s, ty-9*s, tx+12*s, ty-1*s, fill=_PANEL, outline=glow, width=1)
    _draw_mouth(canvas, tx, ty-5*s, 10*s, mouth, glow)

    # Ear side panels
    r(tx-24*s, ty-22*s, tx-20*s, ty-10*s, fill=glow, outline="")
    r(tx+20*s, ty-22*s, tx+24*s, ty-10*s, fill=glow, outline="")

    # ── Antenna ──────────────────────────────────────────────────────────────
    l(tx, ty-28*s, tx, ty-47*s, fill=glow, width=max(1, int(2*s)))
    # Antenna tip — glow rings in the agent's own colour
    c(tx-6*s, ty-53*s, tx+6*s, ty-41*s, fill=body_color, outline="")
    c(tx-4*s, ty-51*s, tx+4*s, ty-43*s, fill=glow, outline="")
    c(tx-2*s, ty-49*s, tx+2*s, ty-45*s, fill="#ffffff", outline="")


# ─────────────────────────────────────────────────────────────────────────────
#  MINI ROBOT CANVAS
# ─────────────────────────────────────────────────────────────────────────────

class MiniRobotCanvas(tk.Canvas):
    """Small animated robot canvas used inside each channel button during off periods."""

    def __init__(self, parent, emoji: str, color: str, **kwargs):
        super().__init__(parent, bg="#0d0721", highlightthickness=0, **kwargs)
        self.emoji      = emoji
        self.color      = color
        self.frame      = 0
        self._animating = False
        self._scene         = random.choice(_MINI_SCENES)
        self._scene_frames  = 0
        self._scene_length  = random.randint(440, 560)
        self._stars         = []

    def start(self):
        self._animating = True
        self.frame      = 0
        self._init_stars()
        self._tick()

    def stop(self):
        self._animating = False

    def _init_stars(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w, h = 400, 200
        self._stars = [
            {"x": random.random()*w, "y": random.random()*h,
             "r": random.uniform(0.3, 1.4), "a": random.random(),
             "da": random.uniform(-0.03, 0.03),
             "col": random.choice(_STAR_COLS)}
            for _ in range(30)
        ]

    def _tick(self):
        if not self._animating:
            return
        self.frame         += 1
        self._scene_frames += 1
        if self._scene_frames >= self._scene_length:
            self._scene        = random.choice(_MINI_SCENES)
            self._scene_frames = 0
            self._scene_length = random.randint(440, 560)
        self._draw()
        self.after(16, self._tick)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return
        cx = w // 2
        cy = int(h * 0.46)
        s  = min(w, h) / 160
        self._draw_stars()
        self._draw_scene(cx, cy, s)
        self._draw_label(w, h)

    def _draw_stars(self):
        for star in self._stars:
            star["a"] = max(0.05, min(1.0, star["a"] + star["da"]))
            if star["a"] <= 0.05 or star["a"] >= 1.0:
                star["da"] *= -1
            b   = int(star["a"] * 180)
            col = star.get("col", f"#{b:02x}{b+15:02x}{min(255,b+50):02x}")
            x, y, r = star["x"], star["y"], star["r"]
            self.create_oval(x-r, y-r, x+r, y+r, fill=col, outline="")

    def _draw_label(self, w, h):
        self.create_text(w//2, h-10, text=self.emoji,
                        font=("", max(10, int(h*0.12))), anchor="center")

    def _draw_scene(self, cx, cy, s):
        f = self.frame
        if self._scene == "sleeping":
            self._mini_sleeping(cx, cy, s, f)
        elif self._scene == "lunch":
            self._mini_lunch(cx, cy, s, f)
        else:
            self._mini_working(cx, cy, s, f)

    def _robot(self, cx, cy, s, opts=None):
        if opts is None:
            opts = {}
        tilt  = opts.get("tilt", 0)
        arm_l = opts.get("arm_l", 0)
        arm_r = opts.get("arm_r", 0)
        leg_l = opts.get("leg_l", 0)
        leg_r = opts.get("leg_r", 0)
        eye_l = opts.get("eye_l", "●")
        eye_r = opts.get("eye_r", "●")
        mouth = opts.get("mouth", "—")
        bc    = opts.get("color", _DARK_BODY)
        glow  = opts.get("glow", _GLOW)
        _draw_robot_full(self, cx, cy, s, tilt, arm_l, arm_r, leg_l, leg_r,
                         eye_l, eye_r, mouth, bc, glow)

    def _mini_sleeping(self, cx, cy, s, f):
        bob = math.sin(f * 0.05 * _PHASE) * 3
        # Moon
        mx, my = cx + int(55*s), cy - int(55*s)
        mr = int(14*s)
        self.create_oval(mx-mr, my-mr, mx+mr, my+mr, fill=_MOON, outline="")
        self.create_oval(mx-mr+int(5*s), my-mr-int(3*s),
                         mx+mr+int(4*s), my+mr-int(2*s), fill=_SKY, outline="")
        # Stars around moon
        for i in range(3):
            ang = math.radians(i*120 + f*0.3*_PHASE)
            sx = mx + math.cos(ang)*int(22*s)
            sy = my + math.sin(ang)*int(16*s)
            sr = 1.0 + math.sin(f*0.07*_PHASE+i)*0.4
            self.create_oval(sx-sr, sy-sr, sx+sr, sy+sr, fill="#FFD93D", outline="")

        self._robot(cx, cy + bob, s, {
            "tilt": 18, "arm_l": 14, "arm_r": 6,
            "eye_l": "—", "eye_r": "—", "mouth": "~",
            "leg_l": 6, "leg_r": -5,
        })
        # Zzz bubbles
        zzz_phase = (f // 60) % 3
        for i in range(zzz_phase + 1):
            self.create_text(
                cx + int(28*s) + i*int(10*s),
                cy - int(38*s) - i*int(10*s) + math.sin(f*0.04*_PHASE)*2,
                text="z", fill=_TEAL,
                font=("Courier New", max(6, int((7+i*3)*s)), "bold"),
            )
        # Emoji pillow
        self.create_text(cx - int(38*s), cy + int(28*s), text=self.emoji,
                        font=("", max(8, int(18*s))), anchor="center")

    def _mini_lunch(self, cx, cy, s, f):
        bob = math.sin(f * 0.09 * _PHASE) * 2
        # Table
        self.create_rectangle(cx-int(50*s), cy+int(54*s), cx+int(50*s), cy+int(62*s),
                              fill=_DIM, outline=_GLOW, width=1)
        self.create_rectangle(cx-int(44*s), cy+int(61*s), cx-int(36*s), cy+int(80*s),
                              fill=_DIM, outline="")
        self.create_rectangle(cx+int(36*s), cy+int(61*s), cx+int(44*s), cy+int(80*s),
                              fill=_DIM, outline="")
        # Plate
        self.create_oval(cx-int(18*s), cy+int(34*s), cx+int(18*s), cy+int(56*s),
                        fill=_DARK_BODY, outline=_GLOW, width=1)
        self.create_text(cx, cy+int(44*s), text=self.emoji,
                        font=("", max(8, int(14*s))), anchor="center")
        # Steam
        steam_y = math.sin(f*0.12*_PHASE)*2
        for sx in [-5, 0, 5]:
            self.create_line(cx+int(sx*s), cy+int(30*s)+steam_y,
                           cx+int(sx*s)+1, cy+int(22*s)+steam_y,
                           fill="#445566", width=1)
        eat = _eased_sin(f*0.18*_PHASE)*int(8*s)
        self._robot(cx, cy+bob, s, {
            "tilt": 6, "arm_r": -int(14 - eat/s if s > 0 else 14),
            "eye_l": "^", "eye_r": "^", "mouth": "U",
            "leg_l": 4, "leg_r": -4,
        })

    def _mini_working(self, cx, cy, s, f):
        bob = math.sin(f*0.1*_PHASE)*2
        robot_cx = cx - int(22*s)
        tv_left  = cx + int(8*s)
        # Tiny TV
        tw, th = int(40*s), int(32*s)
        tv_x = tv_left
        tv_y = cy - int(22*s)
        # TV bezel
        self.create_rectangle(tv_x-int(2*s), tv_y-int(2*s),
                             tv_x+tw+int(2*s), tv_y+th+int(2*s),
                             fill=_DIM, outline=_RED, width=max(1, int(2*s)), dash=(3, 2))
        self.create_rectangle(tv_x, tv_y, tv_x+tw, tv_y+th, fill="#0a0010", outline="")
        # Scan lines on TV
        for yi in range(3):
            self.create_line(tv_x+2, tv_y+int((8+yi*8)*s),
                           tv_x+tw-2, tv_y+int((8+yi*8)*s),
                           fill="#1a0030", width=1)
        self.create_text(tv_x+tw//2, tv_y+th//2, text=self.emoji,
                        font=("", max(8, int(13*s))), anchor="center")
        # Sparks
        spark = (f//16)%4
        spark_colors = [_MOON, _RED, "#FFFFFF", _MOON]
        for i in range(spark):
            self.create_text(tv_x-int(2*s)+i*int(4*s), tv_y-int(6*s)+i*int(2*s),
                           text="✦", fill=spark_colors[i],
                           font=("Courier New", max(5, int((4+i)*s))))
        self._robot(robot_cx, cy+bob, s, {
            "tilt": 14, "arm_r": -20,
            "eye_l": "●", "eye_r": "●", "mouth": "/",
            "leg_l": 5, "leg_r": -3,
        })


# ─────────────────────────────────────────────────────────────────────────────
#  WORLD AGENTS
# ─────────────────────────────────────────────────────────────────────────────

class _RobotAgent:
    def __init__(self, x: float, ground_y: float, emoji: str, color: str, scale: float):
        self.x         = x
        self.ground_y  = ground_y
        self.emoji     = emoji
        self.color     = color
        self.scale     = scale
        self.direction = random.choice([-1, 1])
        # Fixed pixel/sec speed — independent of scale so agents always move visibly
        self.speed     = random.uniform(30, 80)
        self.activity  = random.choice(_ACT_KEYS)
        self.act_pool  = _ACT_KEYS   # overridden by world canvas after creation
        self._act_timer= random.randint(0, 180)
        self._act_dur  = random.randint(80, 220)
        self.phase     = random.uniform(0, math.pi * 2)

    def update(self, dt: float, world_w: float):
        self._act_timer += 1
        if self._act_timer >= self._act_dur:
            self._act_timer = 0
            self._act_dur   = random.randint(80, 220)
            self.activity   = random.choice(self.act_pool)
            if random.random() < 0.35:
                self.direction *= -1
        if self.activity in ("walk", "run", "skate"):
            mult = 2.0 if self.activity == "run" else (1.5 if self.activity == "skate" else 1.0)
            self.x += self.direction * self.speed * mult * dt
            margin = 60
            if self.x > world_w + margin:
                self.x = -margin
            elif self.x < -margin:
                self.x = world_w + margin


class _CarAgent:
    def __init__(self, x: float, road_y: float, scale: float):
        self.x       = x
        self.road_y  = road_y
        self.scale   = scale
        self.direction = random.choice([-1, 1])
        # Fixed pixel/sec speed — cars should zip across regardless of panel height
        self.speed   = random.uniform(80, 200)
        cols         = random.choice(_CAR_COLORS)
        self.body_col = cols[0]
        self.win_col  = cols[1]
        self.emoji   = random.choice(["🐭","🎵","🌟","🐘","🤖","👾","🚀","🎮"])

    def update(self, dt: float, world_w: float):
        self.x += self.direction * self.speed * dt
        margin = 80
        if self.x > world_w + margin:
            self.x = -margin
            self.direction = 1
        elif self.x < -margin:
            self.x = world_w + margin
            self.direction = -1


# ─────────────────────────────────────────────────────────────────────────────
#  ROBOT WORLD CANVAS
# ─────────────────────────────────────────────────────────────────────────────

class RobotWorldCanvas(tk.Canvas):
    """Panoramic robot world shown in the full bottom panel during off periods."""

    def __init__(self, parent, channels: list, scene: str = "sleeping", **kwargs):
        super().__init__(parent, bg=_SKY, highlightthickness=0, **kwargs)
        self._scene = scene
        self._animating = False
        self._frame     = 0
        self._channels  = channels
        self._agents: list[_RobotAgent] = []
        self._cars:   list[_CarAgent]   = []
        self._clouds  = []
        self._stars   = []
        self._buildings = []
        self._last_t  = 0.0

    def start(self):
        self._animating = True
        self._last_t    = time.time()
        self.after(50, self._boot)

    def stop(self):
        self._animating = False

    def _boot(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            self.after(50, self._boot)
            return
        self._setup_world(w, h)
        self._tick()

    def _setup_world(self, w: int, h: int):
        # Smaller scale so robots fit in the short bottom panel
        s = h / 240
        ground_y = h * 0.72
        road_y   = h * 0.8

        # Robot cy is the waist; feet are drawn at cy+78*s.
        # Subtract 78*s so feet land exactly on the visual ground line.
        agent_ground_cy = ground_y - 78 * s
        agent_road_cy   = road_y   - 78 * (s * 0.65)

        self._stars = [
            {"x": random.random()*w, "y": random.random()*h*0.55,
             "r": random.uniform(0.5, 2.0), "a": random.random(),
             "da": random.uniform(-0.025, 0.025),
             "col": random.choice(_STAR_COLS)}
            for _ in range(70)
        ]

        self._clouds = [
            {"x": random.random()*w, "y": random.uniform(h*0.04, h*0.28),
             "r": random.uniform(20, 45)*s, "speed": random.uniform(8, 25),
             "alpha": random.uniform(0.15, 0.40)}
            for _ in range(7)
        ]

        # Buildings with neon signs
        self._buildings = []
        bx = 0
        sign_words = ["CAFE", "TOYS", "PIZZA", "HOTEL", "OPEN", "DISCO", "CLUB", "DINER"]
        while bx < w:
            bw = random.randint(int(30*s), int(65*s))
            bh = random.randint(int(32*s), int(95*s))
            windows = []
            for wy in range(int(bh*0.14), int(bh*0.84), max(1, int(12*s))):
                for wx in range(int(bw*0.14), int(bw*0.84), max(1, int(13*s))):
                    windows.append((wx, wy, random.random() < 0.65))
            sign = random.random() < 0.35
            sign_col = random.choice([_RED, _TEAL, _MOON, "#cc88ff", "#ff88cc"])
            sign_text = random.choice(sign_words) if sign else ""
            self._buildings.append({
                "x": bx, "w": bw, "h": bh, "windows": windows,
                "sign": sign, "sign_col": sign_col, "sign_text": sign_text,
            })
            bx += bw + random.randint(0, int(6*s))

        act_pool = _SCENE_ACTS.get(self._scene, _ACT_KEYS)

        agents = []
        extras = max(0, 5 - len(self._channels))
        for i, ch in enumerate(self._channels):
            gx = (i + 0.5) * w / len(self._channels)
            a = _RobotAgent(gx, agent_ground_cy, ch["emoji"], ch.get("color", _GLOW), s)
            a.activity = random.choice(act_pool)
            a.act_pool = act_pool
            agents.append(a)
        extra_emojis = ["🤖","👾","🚀","🎮","💫","⚡","🌙","🔧"]
        for i in range(extras):
            a = _RobotAgent(random.uniform(0, w), agent_ground_cy,
                            random.choice(extra_emojis), _GLOW, s)
            a.activity = random.choice(act_pool)
            a.act_pool = act_pool
            agents.append(a)
        for i in range(2):
            a = _RobotAgent(random.uniform(0, w), agent_road_cy,
                            random.choice(extra_emojis), _TEAL, s*0.65)
            a.activity = random.choice(act_pool)
            a.act_pool = act_pool
            agents.append(a)
        self._agents = agents

        self._cars = [
            _CarAgent(random.uniform(0, w), road_y, s)
            for _ in range(random.randint(2, 5))
        ]
        self._ground_y  = ground_y
        self._road_y    = road_y
        self._scale     = s
        self._act_pool  = act_pool

    def _tick(self):
        if not self._animating:
            return
        now  = time.time()
        dt   = min(now - self._last_t, 0.05)
        self._last_t = now
        self._frame += 1
        self._update(dt)
        try:
            self._draw()
        except Exception:
            import traceback
            traceback.print_exc()
        self.after(16, self._tick)

    def _update(self, dt: float):
        w = self.winfo_width()
        for a in self._agents:
            a.update(dt, w)
        for c in self._cars:
            c.update(dt, w)
        for cl in self._clouds:
            cl["x"] += cl["speed"] * dt
            if cl["x"] - cl["r"] > self.winfo_width():
                cl["x"] = -cl["r"]

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return
        f = self._frame
        s = self._scale
        try:
            self._draw_sky(w, h)
        except Exception:
            import traceback; traceback.print_exc()
        try:
            self._draw_buildings(f)
        except Exception:
            import traceback; traceback.print_exc()
        try:
            self._draw_road(w, h, f, s)
        except Exception:
            import traceback; traceback.print_exc()
        try:
            self._draw_cars(f)
        except Exception:
            import traceback; traceback.print_exc()
        for a in sorted(self._agents, key=lambda a: a.ground_y):
            try:
                self._draw_agent(a, f)
            except Exception:
                import traceback; traceback.print_exc()
        try:
            self._draw_ground_details(w, h, s, f)
        except Exception:
            import traceback; traceback.print_exc()
        if self._scene == "tubby":
            try:
                self._draw_bubbles(w, f)
            except Exception:
                import traceback; traceback.print_exc()

    def _draw_sky(self, w, h):
        sky_h = int(h * 0.72)
        scene = self._scene

        if scene == "sleeping":
            self.create_rectangle(0, 0, w, sky_h, fill=_SKY, outline="")
            self.create_oval(w*0.3, h*0.0, w*0.75, h*0.35, fill="#0f0528", outline="")
            # Stars
            for st in self._stars:
                st["a"] = max(0.05, min(1.0, st["a"] + st["da"]))
                if st["a"] <= 0.05 or st["a"] >= 1.0:
                    st["da"] *= -1
                col = st["col"]
                x, y, r = st["x"], st["y"], st["r"] * (0.8 + 0.2*st["a"])
                self.create_oval(x-r, y-r, x+r, y+r, fill=col, outline="")
            # Moon with glow rings
            mx, my = w*0.88, h*0.17
            mr = self._scale * 18
            for ring in [mr*2.2, mr*1.6, mr*1.2]:
                alpha_ring = int(10 + (mr*2.2-ring)/mr*15)
                self.create_oval(mx-ring, my-ring, mx+ring, my+ring,
                               fill=f"#{alpha_ring:02x}{alpha_ring//2:02x}00", outline="")
            self.create_oval(mx-mr, my-mr, mx+mr, my+mr, fill=_MOON, outline="")
            self.create_oval(mx+mr*0.28, my-mr*1.12, mx+mr*1.28, my+mr*0.88,
                            fill=_SKY, outline="")
            # Dark clouds
            for cl in self._clouds:
                br = int(cl["alpha"] * 65)
                col = f"#{br+8:02x}{br:02x}{br+22:02x}"
                r = cl["r"]
                x, y = cl["x"], cl["y"]
                for dx, dy, dr in [(0,0,1.0),(-r*0.55,-r*0.25,0.65),(r*0.55,-r*0.2,0.65),(0,-r*0.4,0.72)]:
                    rr = r*dr
                    self.create_oval(x+dx-rr, y+dy-rr, x+dx+rr, y+dy+rr, fill=col, outline="")
        else:
            # Daytime sky — pick gradient by scene
            if scene == "lunch":
                top_col, bot_col = _DAY_SKY_TOP, _DAY_SKY_BOT
                sun_col = _MOON
            elif scene == "fixing":
                top_col, bot_col = _OVER_SKY_TOP, _OVER_SKY_BOT
                sun_col = "#c0c0a0"
            elif scene == "kita_goodbye":
                top_col, bot_col = _WARM_SKY_TOP, _WARM_SKY_BOT
                sun_col = _MOON
            else:  # tubby — warm evening
                top_col, bot_col = _EVE_SKY_TOP, _EVE_SKY_BOT
                sun_col = "#f4a261"

            # Sky gradient (two bands)
            self.create_rectangle(0, 0, w, sky_h//2, fill=top_col, outline="")
            self.create_rectangle(0, sky_h//2, w, sky_h, fill=bot_col, outline="")

            # Sun
            sr = max(6, int(self._scale * 16))
            sx, sy = int(w * 0.82), int(h * 0.15)
            for ring in [sr*2.4, sr*1.7, sr*1.25]:
                _r = int(ring)
                ri = int(ring*255//(sr*2.4))
                self.create_oval(sx-_r, sy-_r, sx+_r, sy+_r,
                               fill=f"#{ri:02x}{ri*3//4:02x}00", outline="")
            self.create_oval(sx-sr, sy-sr, sx+sr, sy+sr, fill=sun_col, outline="")

            # White/light clouds
            for cl in self._clouds:
                r = cl["r"]
                x, y = cl["x"], cl["y"]
                for dx, dy, dr in [(0,0,1.0),(-r*0.55,-r*0.25,0.65),(r*0.55,-r*0.2,0.65),(0,-r*0.4,0.72)]:
                    rr = r*dr
                    self.create_oval(x+dx-rr, y+dy-rr, x+dx+rr, y+dy+rr,
                                   fill="#deeeff", outline="")

    def _draw_buildings(self, f):
        ground_y = self._ground_y
        s        = self._scale
        for b in self._buildings:
            bx = b["x"]
            bw = b["w"]
            bh = b["h"]
            by = ground_y - bh
            # Building body with slight gradient via two rects
            self.create_rectangle(bx, by, bx+bw, ground_y, fill=_DIM, outline="#1a0e3a")
            self.create_rectangle(bx, by, bx+bw, by+int(bh*0.3), fill="#1e1040", outline="")
            # Windows
            for (wx, wy, lit) in b["windows"]:
                col = _MOON if lit else "#0c0416"
                ws  = max(3, int(bw*0.12))
                self.create_rectangle(bx+wx, by+wy, bx+wx+ws, by+wy+ws, fill=col, outline="")
            # Neon sign
            if b.get("sign") and b.get("sign_text"):
                sc  = b["sign_col"]
                sw  = min(bw-8, int(bw*0.85))
                sh  = int(12*s)
                sx  = bx + (bw-sw)//2
                sy  = by + int(bh*0.12)
                # Sign glow halo — derive a very dark tint from the sign colour
                _r = int(sc[1:3], 16) // 8
                _g = int(sc[3:5], 16) // 8
                _b = int(sc[5:7], 16) // 8
                halo_col = f"#{_r:02x}{_g:02x}{_b:02x}"
                self.create_rectangle(sx-2, sy-2, sx+sw+2, sy+sh+2, fill=halo_col, outline="")
                self.create_rectangle(sx, sy, sx+sw, sy+sh, fill="#08020e",
                                    outline=sc, width=1)
                pulse = 0.7 + 0.3*math.sin(f*0.06*_PHASE + bx)
                fill_c = sc if pulse > 0.8 else "#333333"
                self.create_text(sx+sw//2, sy+sh//2, text=b["sign_text"],
                               font=("Courier New", max(5, int(7*s)), "bold"),
                               fill=fill_c, anchor="center")
            # Rooftop antenna
            mid = bx + bw//2
            self.create_line(mid, by, mid, by-int(8*s), fill=_GLOW, width=1)
            self.create_oval(mid-2, by-int(11*s), mid+2, by-int(7*s), fill=_RED, outline="")

    def _draw_road(self, w, h, f, s):
        road_y = self._road_y
        road_h = max(4, int(h*0.07))
        # Road surface
        self.create_rectangle(0, road_y, w, road_y+road_h, fill=_ROAD, outline="")
        # Lane markings
        dash_w  = max(4, int(28*s))
        dash_gap = max(2, int(20*s))
        offset = int(f*1.5) % (dash_w+dash_gap)
        ly = road_y + road_h//2
        x = -dash_gap + offset
        while x < w + dash_w:
            self.create_line(x, ly, x+dash_w, ly, fill="#2a2a44",
                           width=max(1, int(2*s)), dash=(4,4))
            x += dash_w + dash_gap
        # Kerb lines
        self.create_line(0, road_y,          w, road_y,          fill="#2a1a5e", width=max(1,int(2*s)))
        self.create_line(0, road_y+road_h,   w, road_y+road_h,   fill="#2a1a5e", width=max(1,int(2*s)))

    def _draw_cars(self, f):
        s = self._scale
        for car in self._cars:
            self._draw_car(car, f, s)

    def _draw_car(self, car: _CarAgent, f: int, s: float):
        x  = car.x
        y  = car.road_y
        d  = car.direction
        cw = int(52*s)
        ch = int(24*s)
        wr = int(8*s)
        x0, x1 = x-cw//2, x+cw//2
        # Shadow
        self.create_oval(x0+int(4*s), y+int(1*s), x1-int(4*s), y+int(5*s),
                        fill="#060812", outline="")
        # Body
        self.create_rectangle(x0, y-ch, x1, y, fill=car.body_col, outline="")
        # Cab
        cab = int(8*s)
        self.create_rectangle(x0+cab, y-ch-int(14*s), x1-cab, y-ch+int(4*s),
                             fill=car.body_col, outline="")
        self.create_oval(x0+cab, y-ch-int(14*s), x1-cab, y-ch+int(4*s),
                        fill=car.body_col, outline="")
        # Windows
        self.create_rectangle(x0+cab+int(3*s), y-ch-int(11*s),
                             x1-cab-int(3*s), y-ch+int(2*s),
                             fill=car.win_col, outline="")
        # Wheels
        roll = int(x/3) % 360
        for wx in [x0+int(10*s), x1-int(10*s)]:
            self.create_oval(wx-wr, y-wr, wx+wr, y+wr, fill="#111133", outline=_GLOW, width=1)
            ang = math.radians(roll)
            self.create_line(wx, y, wx+int(wr*0.8*math.cos(ang)), y+int(wr*0.8*math.sin(ang)),
                           fill=_GLOW, width=1)
        # Emoji passenger
        self.create_text((x0+x1)//2, y-ch-int(4*s), text=car.emoji,
                        font=("", max(7, int(10*s))), anchor="center")
        # Lights with glow
        light_col = _MOON if d > 0 else _RED
        self.create_oval(x1-int(5*s), y-int(11*s), x1+int(3*s), y-int(3*s),
                        fill=light_col, outline="")
        # Glow halo for headlight — dim version of headlight colour (6 hex digits)
        gl = int(8*s)
        _lr = int(light_col[1:3], 16) // 5
        _lg = int(light_col[3:5], 16) // 5
        _lb = int(light_col[5:7], 16) // 5
        self.create_oval(x1-gl, y-gl, x1+gl, y,
                        fill=f"#{_lr:02x}{_lg:02x}{_lb:02x}", outline="")
        back_col = _RED if d > 0 else _MOON
        self.create_oval(x0-int(3*s), y-int(11*s), x0+int(5*s), y-int(3*s),
                        fill=back_col, outline="")

    def _draw_ground_details(self, w, h, s, f):
        gy = self._ground_y
        is_night = (self._scene == "sleeping")
        ground_col = _GROUND if is_night else _DAY_GROUND
        edge_col   = "#200d44" if is_night else _DAY_GROUND_DK
        self.create_rectangle(0, gy, w, h, fill=ground_col, outline="")
        self.create_rectangle(0, gy, w, gy+int(4*s), fill=edge_col, outline="")
        # Grass tufts
        grass_step = max(1, int(20*s))
        jitter_mod = max(1, int(8*s))
        ht_mod     = max(1, int(4*s))
        for gx in range(0, w, grass_step):
            jitter = (gx*7919) % jitter_mod
            ht = max(1, int(5*s)) + jitter % ht_mod
            self.create_line(gx, gy, gx, gy-ht, fill="#1e3a1e", width=max(1,int(2*s)))
            self.create_line(gx+int(3*s), gy, gx+int(3*s), gy-ht+int(2*s),
                           fill="#1e3a1e", width=max(1,int(2*s)))
        # Lamp posts with glow halos
        for lx in range(int(w*0.15), w, int(w//5)):
            pole_h = int(50*s)
            self.create_line(lx, gy, lx, gy-pole_h, fill="#2a1a5e", width=max(2,int(3*s)))
            self.create_rectangle(lx-int(6*s), gy-pole_h-int(4*s),
                                 lx+int(6*s), gy-pole_h,
                                 fill="#2a1a5e", outline=_GLOW, width=1)
            pulse = 0.4 + 0.2*math.sin(f*0.04*_PHASE+lx)
            gb = int(pulse*80)
            glow_col = f"#{gb:02x}{min(255,gb*2+50):02x}{gb:02x}"
            # Layered glow halos
            for ring_r, alpha in [(int(18*s), glow_col), (int(12*s), glow_col)]:
                self.create_oval(lx-ring_r, gy-pole_h-ring_r,
                               lx+ring_r, gy-pole_h+int(8*s),
                               fill=alpha, outline="")
            self.create_text(lx, gy-pole_h-int(2*s), text="💡",
                           font=("", max(6, int(8*s))), anchor="center")

    def _draw_bubbles(self, w: int, f: int):
        """Floating soap bubbles for the tubby scene."""
        gy = self._ground_y
        for i in range(22):
            seed = i * 137
            bx  = (seed * 19 + f * 1) % w
            # Bubbles rise from ground upward; wrap at top
            by  = gy - ((seed * 11 + f + i * 17) % int(gy + 10))
            br  = 4 + (seed % 6)
            # Translucent bubble ring
            self.create_oval(bx-br, by-br, bx+br, by+br,
                           fill="", outline=_EVE_SKY_BOT, width=1)
            # Small specular highlight
            self.create_oval(bx-br//3, by-br*0.7, bx, by-br*0.3,
                           fill="#ffffff", outline="")

    def _draw_agent(self, agent: _RobotAgent, f: int):
        act_name = agent.activity
        act      = _ACTIVITIES.get(act_name, _ACTIVITIES["walk"])
        _, eye_l, eye_r, mouth, arm_l, arm_r, leg_l, leg_r, tilt = act

        s  = agent.scale
        cx = agent.x
        cy = agent.ground_y

        phase = f*0.18*_PHASE + agent.phase
        if act_name in ("walk", "run"):
            swing = 14 if act_name == "run" else 9
            leg_l =  swing * _eased_sin(phase)
            leg_r = -swing * _eased_sin(phase)
            arm_l = -swing * 0.6 * _eased_sin(phase)
            arm_r =  swing * 0.6 * _eased_sin(phase)
        elif act_name == "dance":
            arm_l = -22 + 8*_eased_sin(phase*1.3)
            arm_r =  22 - 8*_eased_sin(phase*1.3)
            leg_l =  10*_eased_sin(phase)
            leg_r = -10*_eased_sin(phase)
        elif act_name == "wave":
            arm_l = -28 + 10*_eased_sin(phase*2)
        elif act_name == "cheer":
            arm_l = -28 + 6*_eased_sin(phase*2.5)
            arm_r = -28 + 6*_eased_sin(phase*2.5+0.5)
        elif act_name == "skate":
            leg_r = 18*_eased_sin(phase*0.7)

        bob = math.sin(phase*0.5)*2*s

        d = agent.direction
        if d < 0:
            tilt  = -tilt
            arm_l, arm_r = arm_r, arm_l
            leg_l, leg_r = leg_r, leg_l

        # Body: 50 % brightness of the agent's glow colour with a per-channel
        # floor so robots are readable against both night and daytime skies.
        try:
            _br = max(30, int(agent.color[1:3], 16) // 2)
            _bg = max(20, int(agent.color[3:5], 16) // 2)
            _bb = max(25, int(agent.color[5:7], 16) // 2)
            body_col = f"#{_br:02x}{_bg:02x}{_bb:02x}"
        except Exception:
            body_col = "#1e2a40"
        _draw_robot_full(self, cx, cy+bob, s, tilt, arm_l, arm_r, leg_l, leg_r,
                         eye_l, eye_r, mouth, body_col, agent.color)

        # Emoji prop with glow halo
        prop_y = cy + bob - s*54
        if act_name == "carry":
            prop_y = cy + bob - s*32
        elif act_name == "sleep":
            prop_y = cy + bob - s*20
        hr = int(10*s)
        self.create_oval(cx-hr, prop_y-hr, cx+hr, prop_y+hr,
                        fill=agent.color, outline="")
        self.create_text(cx, prop_y, text=agent.emoji,
                        font=("", max(7, int(13*s))), anchor="center")

        # Skateboard
        if act_name == "skate":
            bw = int(28*s)
            self.create_rectangle(cx-bw//2, cy+int(68*s), cx+bw//2, cy+int(72*s),
                                 fill=_TEAL, outline="")
            for wx in [-int(8*s), int(8*s)]:
                self.create_oval(cx+wx-int(3*s), cy+int(70*s),
                                cx+wx+int(3*s), cy+int(76*s),
                                fill="#111133", outline=_GLOW, width=1)

        # Sleep Zzz
        if act_name == "sleep":
            zp = (f//56)%3
            for i in range(zp+1):
                self.create_text(cx+int((18+i*10)*s), cy+bob-int((30+i*10)*s),
                               text="z", fill=_TEAL,
                               font=("Courier New", max(6, int((6+i*3)*s)), "bold"))

        # Food prop when eating
        if act_name == "eat":
            self.create_text(cx+int(30*s), cy+bob-int(10*s),
                           text="🍕", font=("", max(7, int(11*s))), anchor="center")

    def _draw_robot(self, cx, cy, s, opts):
        """Legacy wrapper — kept for compatibility."""
        tilt  = opts.get("tilt", 0)
        arm_l = opts.get("arm_l", 0)
        arm_r = opts.get("arm_r", 0)
        leg_l = opts.get("leg_l", 0)
        leg_r = opts.get("leg_r", 0)
        eye_l = opts.get("eye_l", "●")
        eye_r = opts.get("eye_r", "●")
        mouth = opts.get("mouth", "—")
        bc    = opts.get("color", _DARK_BODY)
        glow  = opts.get("glow", _GLOW)
        _draw_robot_full(self, cx, cy, s, tilt, arm_l, arm_r, leg_l, leg_r,
                         eye_l, eye_r, mouth, bc, glow)


# ─────────────────────────────────────────────────────────────────────────────
#  FULL-SCREEN ROBOT CANVAS (shown while TV is off / loading)
# ─────────────────────────────────────────────────────────────────────────────

class RobotCanvas(tk.Canvas):
    """Animated robot canvas shown when the TV is off."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#0d0721", highlightthickness=0, **kwargs)
        self.frame      = 0
        self.scene      = "sleeping"
        self._stars     = []
        self._animating = False

    def start(self, scene: str = "sleeping"):
        self.scene      = scene
        self.frame      = 0
        self._animating = True
        self._init_stars()
        self._tick()

    def stop(self):
        self._animating = False

    def _init_stars(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w, h = 1920, 1080
        self._stars = [
            {"x": random.random()*w, "y": random.random()*h,
             "r": random.uniform(0.5, 2.2), "a": random.random(),
             "da": random.uniform(-0.02, 0.02),
             "col": random.choice(_STAR_COLS)}
            for _ in range(90)
        ]

    def _tick(self):
        if not self._animating:
            return
        self.frame += 1
        self._draw()
        self.after(16, self._tick)

    def _draw(self):
        self.delete("all")
        w  = self.winfo_width()
        h  = self.winfo_height()
        cx = w // 2
        cy = int(h * 0.42)
        if self.scene == "sleeping":
            self._draw_stars()
            self.create_oval(cx-w*0.4, cy-h*0.5, cx+w*0.4, cy+h*0.3,
                            fill="#0e0825", outline="")
        else:
            self._draw_day_sky(w, h, cx)
        self._draw_scene(cx, cy)
        self._draw_text(cx, h)

    def _draw_stars(self):
        for s in self._stars:
            s["a"] = max(0.05, min(1.0, s["a"] + s["da"]))
            if s["a"] <= 0.05 or s["a"] >= 1.0:
                s["da"] *= -1
            brightness = int(s["a"] * 200)
            col = s.get("col", f"#{brightness:02x}{brightness+20:02x}{min(255,brightness+60):02x}")
            x, y, r = s["x"], s["y"], s["r"]
            self.create_oval(x-r, y-r, x+r, y+r, fill=col, outline="")

    def _draw_day_sky(self, w: int, h: int, cx: int):
        """Gradient daytime sky for non-sleeping scenes."""
        if self.scene == "lunch":
            top, bot, sun = _DAY_SKY_TOP, _DAY_SKY_BOT, _MOON
        elif self.scene == "fixing":
            top, bot, sun = _OVER_SKY_TOP, _OVER_SKY_BOT, "#b0bec5"
        elif self.scene == "kita_goodbye":
            top, bot, sun = _WARM_SKY_TOP, _WARM_SKY_BOT, _MOON
        else:  # tubby
            top, bot, sun = _EVE_SKY_TOP, _EVE_SKY_BOT, "#f4a261"

        # Three-band gradient approximation
        third = h // 3
        self.create_rectangle(0, 0,      w, third,   fill=top, outline="")
        self.create_rectangle(0, third,  w, third*2, fill=bot, outline="")
        self.create_rectangle(0, third*2, w, h,       fill=bot, outline="")

        # Sun / glow
        sr = 48
        sx, sy = int(w * 0.80), int(h * 0.18)
        for ring in [sr*2.2, sr*1.6, sr*1.2]:
            ri = int(ring * 60 // (sr * 2.2))
            self.create_oval(sx-int(ring), sy-int(ring), sx+int(ring), sy+int(ring),
                           fill=f"#{ri:02x}{ri*3//4:02x}00", outline="")
        self.create_oval(sx-sr, sy-sr, sx+sr, sy+sr, fill=sun, outline="")

        # White clouds (3 puffs near top)
        for clx, cly, clr in [(cx*0.4, h*0.12, 55),
                               (cx*1.2, h*0.08, 40),
                               (cx*0.75, h*0.18, 35)]:
            for dx, dy, dr in [(0,0,1.0),(-clr*.55,-clr*.25,.65),(clr*.55,-clr*.2,.65)]:
                rr = clr*dr
                self.create_oval(clx+dx-rr, cly+dy-rr, clx+dx+rr, cly+dy+rr,
                               fill="#deeeff", outline="")

    def _draw_text(self, cx, h):
        scene   = SCENES.get(self.scene, SCENES["sleeping"])
        on_time = next_on_time()
        is_day  = self.scene != "sleeping"
        # Dark backing panel so text is readable on bright day sky
        if is_day:
            self.create_rectangle(cx-320, h*0.68, cx+320, h*0.93,
                                 fill="#0a0820", outline="#2a1a5e", width=1)
        # Scene label
        self.create_text(cx, h*0.72, text=scene["label"],
                        fill="#2a1a5e" if not is_day else "#4a3a7e",
                        font=("Courier New", 12, "bold"), anchor="center")
        # Main message with drop shadow
        self.create_text(cx+2, h*0.80+2, text=scene["msg"],
                        fill="#003322", font=("Courier New", 28, "bold"), anchor="center")
        self.create_text(cx, h*0.80, text=scene["msg"],
                        fill="#00ff88", font=("Courier New", 28, "bold"), anchor="center")
        # Subtext
        self.create_text(cx, h*0.87,
                        text=f"{scene['sub']}  ·  back at {on_time}",
                        fill="#444466", font=("Courier New", 14), anchor="center")

    def _draw_scene(self, cx, cy):
        f = self.frame
        s = self.scene
        if s == "sleeping":
            self._scene_sleeping(cx, cy, f)
        elif s == "lunch":
            self._scene_lunch(cx, cy, f)
        elif s == "kita_goodbye":
            self._scene_kita_goodbye(cx, cy, f)
        elif s == "tubby":
            self._scene_tubby(cx, cy, f)
        else:
            self._scene_fixing(cx, cy, f)

    # ── Robot primitive ────────────────────────────────────────────────────────

    def _robot(self, cx, cy, opts={}):
        """Draw a detailed robot at (cx, cy)."""
        tilt  = opts.get("tilt", 0)
        arm_l = opts.get("arm_l", 0)
        arm_r = opts.get("arm_r", 0)
        leg_l = opts.get("leg_l", 0)
        leg_r = opts.get("leg_r", 0)
        eye_l = opts.get("eye_l", "●")
        eye_r = opts.get("eye_r", "●")
        mouth = opts.get("mouth", "—")
        color = opts.get("color", _DARK_BODY)
        glow  = opts.get("glow", "#00ff88")
        scale = opts.get("scale", 1.0)
        _draw_robot_full(self, cx, cy, scale, tilt, arm_l, arm_r, leg_l, leg_r,
                         eye_l, eye_r, mouth, color, glow)

    # ── Scene: sleeping ────────────────────────────────────────────────────────

    def _scene_sleeping(self, cx, cy, f):
        bob = math.sin(f * 0.05 * _PHASE) * 4

        # Crescent moon with glow rings
        mx, my = cx+162, cy-128
        for ring in [32, 26, 22]:
            br = max(0, int((32-ring)*3))
            self.create_oval(mx-ring, my-ring, mx+ring, my+ring,
                           fill=f"#{br//2:02x}{br//4:02x}00", outline="")
        self.create_oval(mx-22, my-22, mx+22, my+22, fill=_MOON, outline="")
        self.create_oval(mx-22+6, my-22-4, mx+22+5, my+22-3, fill="#0d0721", outline="")
        # Stars near moon
        for i in range(5):
            ang = math.radians(i*72 + f*0.4*_PHASE)
            sr  = 1.0 + 0.5*math.sin(f*0.09*_PHASE+i*1.5)
            sx  = mx + math.cos(ang)*38
            sy  = my + math.sin(ang)*28
            self.create_oval(sx-sr, sy-sr, sx+sr, sy+sr, fill=_MOON, outline="")

        # Robot 1 — slumped, sleeping
        self._robot(cx-100, cy+bob, {
            "tilt": 20, "arm_l": 18, "arm_r": 8,
            "eye_l": "—", "eye_r": "—", "mouth": "~",
            "leg_l": 8, "leg_r": -6,
        })
        # Zzz floating up
        zzz_phase = (f//50) % 3
        for i in range(zzz_phase+1):
            self.create_text(cx-55+i*14, cy-30-i*14+math.sin(f*0.04*_PHASE)*3,
                           text="z", fill=_TEAL,
                           font=("Courier New", 9+i*4, "bold"))

        # Robot 2 — leaning, half-awake
        self._robot(cx+100, cy-bob, {
            "tilt": -18, "arm_l": -12, "arm_r": -8,
            "eye_l": "·", "eye_r": "·", "mouth": "o",
            "leg_l": -6, "leg_r": 6,
        })
        # Sparkles
        for i in range(4):
            sx = cx+148+i*18
            sy = cy-54-i*8+math.sin(f*0.07*_PHASE+i)*5
            sr = max(1, 4-i)
            self.create_oval(sx-sr, sy-sr, sx+sr, sy+sr, fill=_MOON, outline="")

        # Pillow under robot 1
        self.create_oval(cx-140, cy+68, cx-60, cy+82, fill=_DIM, outline=_GLOW, width=1)

    # ── Scene: lunch ───────────────────────────────────────────────────────────

    def _scene_lunch(self, cx, cy, f):
        bob = math.sin(f*0.08*_PHASE)*3

        # Table
        self.create_rectangle(cx-95, cy+55, cx+95, cy+66, fill=_DIM, outline=_GLOW, width=1)
        self.create_rectangle(cx-87, cy+65, cx-74, cy+95, fill=_DIM, outline="")
        self.create_rectangle(cx+74, cy+65, cx+87, cy+95, fill=_DIM, outline="")
        # Table top shine
        self.create_line(cx-93, cy+57, cx+93, cy+57, fill="#3a2a5e", width=1)

        # Plate with food
        self.create_oval(cx-26, cy+34, cx+26, cy+60, fill=_DARK_BODY, outline=_GLOW, width=1)
        # Food items on plate
        for dx, dy, w2, h2, col in [(-9,42,16,9,_RED),(3,44,11,7,_MOON),(-5,37,9,6,_TEAL)]:
            self.create_rectangle(cx+dx, cy+dy, cx+dx+w2, cy+dy+h2, fill=col, outline="")
        # Steam wisps
        steam_y = math.sin(f*0.12*_PHASE)*3
        for i, sx in enumerate([-7, 0, 7]):
            for j in range(3):
                jitter = math.sin(f*0.08*_PHASE+i+j)*2
                self.create_line(cx+sx+jitter, cy+31-j*8+steam_y,
                               cx+sx+jitter+2, cy+23-j*8+steam_y,
                               fill="#334455", width=1)

        # Mug
        mug_x, mug_y = cx+46, cy+38
        self.create_rectangle(mug_x, mug_y, mug_x+20, mug_y+22,
                            fill=_DARK_BODY, outline=_GLOW, width=1)
        self.create_arc(mug_x+18, mug_y+4, mug_x+30, mug_y+18,
                      start=-90, extent=180, style="arc", outline=_GLOW, width=1)
        self.create_rectangle(mug_x+2, mug_y+2, mug_x+18, mug_y+9, fill=_TEAL, outline="")
        cup_steam = math.sin(f*0.15*_PHASE)*2
        for si in range(2):
            self.create_line(mug_x+5+si*7, mug_y-5+cup_steam,
                           mug_x+7+si*7, mug_y-12+cup_steam, fill="#334455", width=1)

        # Robot 1 eating
        eat = _eased_sin(f*0.18*_PHASE)*10
        self._robot(cx-120, cy+bob, {
            "tilt": 8, "arm_r": eat-16,
            "eye_l": "^", "eye_r": "^", "mouth": "U",
            "leg_l": 4, "leg_r": -4,
        })
        # Fork animation
        fork_x = cx-84
        fork_y = cy+22+eat-16+bob
        self.create_line(fork_x, fork_y, fork_x, fork_y-16, fill=_GLOW, width=2)
        for tine in range(3):
            self.create_line(fork_x-3+tine*3, fork_y-16, fork_x-3+tine*3, fork_y-22,
                           fill=_GLOW, width=1)

        # Robot 2 relaxing
        self._robot(cx+130, cy-bob, {
            "tilt": -8, "arm_l": -8, "arm_r": -18,
            "eye_l": "●", "eye_r": "^", "mouth": ")",
            "leg_l": -5, "leg_r": 3,
        })

    # ── Scene: kita_goodbye ────────────────────────────────────────────────────

    def _scene_kita_goodbye(self, cx, cy, f):
        bob = math.sin(f*0.07*_PHASE)*4

        # Sunrise disc with rays
        sr = 72
        sy = cy - 5
        # Glow rings
        for ring in [sr*1.8, sr*1.4, sr*1.15]:
            ri = int(ring)
            self.create_oval(cx-ri, sy-ri, cx+ri, sy+ri, fill="#1a0804", outline="")
        self.create_oval(cx-sr, sy-sr, cx+sr, sy+sr, fill="#FF8C42", outline=_MOON, width=3)
        for i in range(12):
            ang = math.radians(i*30 + f*0.35*_PHASE)
            inn = sr + 6
            out = sr + 22 + 8*math.sin(f*0.07*_PHASE+i*0.8)
            self.create_line(cx+math.cos(ang)*inn, sy+math.sin(ang)*inn,
                           cx+math.cos(ang)*out, sy+math.sin(ang)*out,
                           fill=_MOON, width=2)
        self.create_text(cx, sy+4, text="☀", fill="#fff3aa",
                        font=("Courier New", 20, "bold"), anchor="center")

        # Floating school bag
        bag_y = cy - 110 + math.sin(f*0.05*_PHASE)*10
        self.create_text(cx, bag_y, text="🎒", font=("", 30), anchor="center")

        # Orbiting sparkle emojis
        sparkles = ["⭐","💛","🌟","✨","💚","🎈"]
        for i, em in enumerate(sparkles):
            ang   = math.radians(i*60 + f*1.0)
            orb_r = 98 + 10*math.sin(f*0.04*_PHASE+i)
            ex = cx + math.cos(ang)*orb_r
            ey = cy - 30 + math.sin(ang)*40
            self.create_text(ex, ey, text=em, font=("", 12), anchor="center")

        # Banner with drop shadow and pennants
        bw = 162
        by = cy - 40
        banner_bob = math.sin(f*0.09*_PHASE)*3
        self.create_rectangle(cx-bw+3, by-17+3, cx+bw+3, by+17+3, fill="#000033", outline="")
        self.create_rectangle(cx-bw, by-17, cx+bw, by+17,
                            fill="#1a004e", outline=_MOON, width=2)
        self.create_text(cx, by+banner_bob, text="HAVE FUN AT KITA!",
                        fill=_MOON, font=("Courier New", 13, "bold"), anchor="center")
        for px in range(-bw+8, bw, 22):
            py_bob = math.sin(px*0.05+f*0.08*_PHASE)*4
            col_idx = int((px+bw)/22) % 3
            pcol = [_RED, _TEAL, _MOON][col_idx]
            self.create_text(cx+px, by-26+py_bob, text="▼",
                           fill=pcol, font=("Courier New", 9), anchor="center")

        # Confetti rain
        confetti_cols = [_RED, _MOON, _TEAL, "#6BCB77", "#cc88ff", "#ff88cc"]
        for i in range(24):
            seed  = i*137
            sx = cx-290+(seed*19+f*2)%580
            sy = (seed*13+int(f*1.5)+i*31) % int(cy*1.9+120)
            col = confetti_cols[i % len(confetti_cols)]
            cr  = 3+(seed%3)
            if (f+seed)%2 == 0:
                self.create_oval(sx-cr, sy-cr, sx+cr, sy+cr, fill=col, outline="")
            else:
                self.create_rectangle(sx-cr, sy-cr//2, sx+cr, sy+cr//2, fill=col, outline="")

        # Robot 1 — cheering
        wave_l = -30 + 9*_eased_sin(f*0.20*_PHASE)
        wave_r = -30 + 9*_eased_sin(f*0.20*_PHASE+0.4)
        self._robot(cx-140, cy+bob, {
            "tilt": 8, "arm_l": wave_l, "arm_r": wave_r,
            "eye_l": "^", "eye_r": "^", "mouth": "D",
            "leg_l": 6, "leg_r": -6, "glow": _MOON,
        })

        # Robot 2 — waving with lunchbox
        wave_l2 = -30 + 9*_eased_sin(f*0.17*_PHASE+1.2)
        self._robot(cx+140, cy-bob, {
            "tilt": -10, "arm_l": wave_l2, "arm_r": -12,
            "eye_l": "^", "eye_r": "^", "mouth": ")",
            "leg_l": -5, "leg_r": 5, "glow": _TEAL,
        })
        lx = cx+100
        ly = cy - bob + 12
        self.create_rectangle(lx-13, ly-10, lx+13, ly+10,
                            fill=_DIM, outline=_TEAL, width=1)
        self.create_text(lx, ly, text="🍱", font=("", 12), anchor="center")

    # ── Scene: fixing ──────────────────────────────────────────────────────────

    def _scene_fixing(self, cx, cy, f):
        bob = math.sin(f*0.1*_PHASE)*2

        # TV screen being fixed (with detailed bezel)
        # Outer bezel
        self.create_rectangle(cx-58, cy-38, cx+58, cy+48, fill="#221133", outline=_RED,
                            width=2)
        # Inner screen
        self.create_rectangle(cx-50, cy-30, cx+50, cy+38, fill="#0a0010", outline="#333333", width=1)
        # Scan line effect on TV screen
        for yi in range(6):
            self.create_line(cx-48, cy-28+yi*10, cx+48, cy-28+yi*10, fill="#110022", width=1)
        # Glitchy static blocks
        for i in range(10):
            sx = cx-44+(i*14+f*2)%82
            sy = cy-26+(i*9+int(f*1.5))%56
            self.create_rectangle(sx, sy, sx+10, sy+4, fill=_GLOW, outline="")
        # "NO SIGNAL" text on TV
        self.create_text(cx, cy+44, text="⚠  NO SIGNAL",
                        fill=_RED, font=("Courier New", 9, "bold"), anchor="center")
        # TV stand
        self.create_rectangle(cx-15, cy+48, cx+15, cy+58, fill=_DIM, outline="")
        self.create_rectangle(cx-25, cy+56, cx+25, cy+62, fill=_DIM, outline="")

        # Sparks flying from TV
        spark = (f//10) % 6
        spark_colors = [_MOON, _RED, "#FFFFFF", _MOON, _RED, "#ff88ff"]
        for i in range(min(spark, 5)):
            angle = math.radians(120 + i*15)
            sx = cx-58 + math.cos(angle)*6
            sy = cy-38 + math.sin(angle)*6
            self.create_text(sx+i*5, sy-i*5, text="✦",
                           fill=spark_colors[i], font=("Courier New", 7+i*2))

        # Robot 1 — working with tool
        self._robot(cx-130, cy+bob, {
            "tilt": 12, "arm_r": -22,
            "eye_l": "●", "eye_r": "●", "mouth": "/",
            "leg_l": 6, "leg_r": -4,
        })
        # Wrench tool
        self.create_oval(cx-88, cy-5+bob, cx-78, cy+5+bob, fill=_GLOW, outline="")
        self.create_line(cx-83, cy+5+bob, cx-76, cy+22+bob, fill=_GLOW, width=3)

        # Robot 2 — supervising with clipboard
        self._robot(cx+130, cy-bob, {
            "tilt": -10, "arm_l": -28, "arm_r": 6,
            "eye_l": "◉", "eye_r": "◉", "mouth": "!",
            "leg_l": -5, "leg_r": 4,
        })
        # Clipboard
        self.create_rectangle(cx+148, cy-22, cx+170, cy+8, fill=_DIM, outline=_GLOW, width=1)
        self.create_rectangle(cx+150, cy-20, cx+168, cy-16, fill="#2a1a6e", outline="")
        for row in range(3):
            self.create_line(cx+152, cy-13+row*7, cx+166, cy-13+row*7, fill=_GLOW, width=1)

    # ── Scene: tubby ───────────────────────────────────────────────────────────

    def _scene_tubby(self, cx, cy, f):
        bob = math.sin(f * 0.07 * _PHASE) * 4
        
        # Layout — all derived from cy so scene scales with window height
        tub_hw   = 230          # half-width of the tub opening
        rim_y    = cy + 32      # top centre of the rolled rim
        water_y  = rim_y + 16   # actual water surface inside the tub
        tub_bot  = cy + 115     # exterior bottom of the tub body
        leg_top  = tub_bot - 8  # where claw legs attach

        tub_col   = "#1a3850"
        rim_col   = "#253e52"
        water_col = "#0a3a55"
        foam_col  = "#c8e8f8"
        brass_col = "#c8a030"
        brass_dk  = "#8b6014"

        # ── Claw feet (4, drawn first so tub body sits on top) ────────────
        for fx in [cx - tub_hw + 28, cx - tub_hw + 82,
                   cx + tub_hw - 82, cx + tub_hw - 28]:
            # Leg shaft
            self.create_rectangle(fx-6, leg_top, fx+6, leg_top+22,
                                 fill=brass_col, outline=brass_dk, width=1)
            # Ball foot
            self.create_oval(fx-14, leg_top+16, fx+14, leg_top+36,
                            fill=brass_col, outline=brass_dk, width=1)
            # Three claw toes
            for toe_dx in [-8, 0, 8]:
                self.create_oval(fx+toe_dx-5, leg_top+30,
                                fx+toe_dx+5, leg_top+46,
                                fill=brass_col, outline=brass_dk, width=1)

        # ── Tub exterior body ─────────────────────────────────────────────
        self.create_oval(cx-tub_hw, rim_y+8, cx+tub_hw, tub_bot+8,
                        fill=tub_col, outline=_TEAL, width=2)
        self.create_rectangle(cx-tub_hw+4, rim_y+8,
                              cx+tub_hw-4, (rim_y+8+tub_bot+8)//2,
                              fill=tub_col, outline="")

        # ── Water body inside tub ─────────────────────────────────────────
        self.create_rectangle(cx-tub_hw+20, water_y,
                              cx+tub_hw-20, tub_bot-8,
                              fill=water_col, outline="")
        self.create_oval(cx-tub_hw+20, tub_bot-30, cx+tub_hw-20, tub_bot,
                        fill=water_col, outline="")

        # ── Robots inside the tub ─────────────────────────────────────────
        self._robot(cx - 90, cy + bob, {
            "tilt": 8, "arm_l": -30, "arm_r": -26,
            "eye_l": "^", "eye_r": "^", "mouth": "D",
            "leg_l": 0, "leg_r": 0, "glow": _TEAL,
        })
        self._robot(cx + 75, cy - bob, {
            "tilt": -12, "arm_l": -16, "arm_r": 10,
            "eye_l": "^", "eye_r": "●", "mouth": ")",
            "leg_l": 0, "leg_r": 0, "glow": _MOON,
        })

        # ── Splash drops around robot 1 ───────────────────────────────────
        splash_r = 20 + (f // 8) % 10
        for i in range(7):
            ang = math.radians(i * 51 + f * 2)
            sx = cx - 90 + math.cos(ang) * splash_r
            sy = water_y - 4 + math.sin(ang) * 7
            self.create_oval(sx-3, sy-4, sx+3, sy+4, fill=_TEAL, outline="")

        # ── Rubber duck — large, prominent, floating centre-right ─────────
        duck_bob = math.sin(f * 0.09 * _PHASE + 1.5) * 5
        self.create_text(cx + 155, water_y - 26 + duck_bob,
                        text="🦆", font=("", 44), anchor="center")

        # ── Foam / suds row — covers robot legs below rim ─────────────────
        for i in range(20):
            fw  = cx - tub_hw + 22 + i * ((tub_hw * 2 - 44) // 20)
            fhy = water_y + math.sin(f * 0.08 * _PHASE + i * 0.45) * 5
            fr  = 13 + (i % 4) * 4
            self.create_oval(fw-fr, fhy-fr//2, fw+fr, fhy+fr//2,
                            fill=foam_col, outline="")

        # ── Rolled rim — drawn over robots to mask the submerged half ─────
        self.create_oval(cx-tub_hw-8, rim_y-18, cx+tub_hw+8, rim_y+24,
                         outline=rim_col, width=2)
        # Shine stripe on rim
        self.create_oval(cx-tub_hw+22, rim_y-14, cx-tub_hw+130, rim_y-6,
                        fill="#3a607e", outline="")

        # ── Faucet on right end ───────────────────────────────────────────
        fax = cx + tub_hw - 26
        fay = rim_y - 5
        self.create_rectangle(fax-7, fay-34, fax+7, fay+8,
                             fill=brass_col, outline=brass_dk, width=1)
        self.create_oval(fax-11, fay+4, fax+11, fay+16,
                        fill=brass_col, outline=brass_dk, width=1)
        for i in range(4):
            drip_y = (int(f * 1.5) + i * 10) % 26
            self.create_oval(fax-3, fay+14+drip_y, fax+3, fay+20+drip_y,
                            fill=_TEAL, outline="")

        # ── Hot/cold valve knobs on left end ─────────────────────────────
        for vx, vcol in [(cx - tub_hw + 20, _RED), (cx - tub_hw + 48, _TEAL)]:
            self.create_oval(vx-9, rim_y-14, vx+9, rim_y+2,
                            fill=brass_col, outline=brass_dk, width=1)
            self.create_line(vx-7, rim_y-6, vx+7, rim_y-6, fill=vcol, width=2)

        # ── Bubbles rising from the water surface ─────────────────────────
        for i in range(28):
            seed  = i * 137
            bx    = cx - tub_hw + 28 + (seed * 23 + f // 2) % (tub_hw * 2 - 56)
            depth = (seed * 11 + f + i * 19) % 200
            by    = water_y - 10 - depth
            br    = 5 + (seed % 8)
            self.create_oval(bx-br, by-br, bx+br, by+br,
                            fill="", outline=_EVE_SKY_BOT, width=1)
            self.create_oval(bx-br//3, by-int(br*0.7), bx, by-int(br*0.35),
                            fill="#ffffff", outline="")

        # ── Steam wisps floating upward ───────────────────────────────────
        for i in range(4):
            wph = f * 0.04 * _PHASE + i * 1.6
            wx  = cx - 150 + i * 100 + int(math.sin(wph) * 14)
            wy  = rim_y - 35 - (f // 2 + i * 55) % 95
            wr  = 11 + (i % 3) * 4
            self.create_oval(wx-wr, wy-wr//2, wx+wr, wy+wr//2,
                            fill="#c8e8f8", outline="")
