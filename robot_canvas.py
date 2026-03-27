"""
Toddler TV - RobotCanvas
Animated canvases displayed when the TV is off.

  RobotCanvas     — full-screen animated canvas (main video area)
  MiniRobotCanvas — small per-channel-button version with channel emoji prop
"""

import math
import random
import time
import tkinter as tk

from config import SCENES
from schedule import next_on_time

# Scenes available to mini robots (excludes "fixing" — saved for loading state)
_MINI_SCENES = ["sleeping", "lunch", "working"]


class MiniRobotCanvas(tk.Canvas):
    """Small animated robot canvas used inside each channel button during off periods.
    Draws a single robot doing a random scene and incorporates the channel emoji
    as a prop (a sign it holds, a plate of food, a TV screen being fixed, etc.)."""

    def __init__(self, parent, emoji: str, color: str, **kwargs):
        super().__init__(parent, bg="#0d0721", highlightthickness=0, **kwargs)
        self.emoji      = emoji
        self.color      = color
        self.frame      = 0
        self._animating = False
        # Pick a random scene and rotate every ~8 seconds
        self._scene         = random.choice(_MINI_SCENES)
        self._scene_frames  = 0
        self._scene_length  = random.randint(220, 280)  # frames at ~30fps ≈ 7-9 s
        self._stars         = []

    # ── Public API ────────────────────────────

    def start(self):
        self._animating = True
        self.frame      = 0
        self._init_stars()
        self._tick()

    def stop(self):
        self._animating = False

    # ── Internal loop ─────────────────────────

    def _init_stars(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w, h = 400, 200
        self._stars = [
            {
                "x":  random.random() * w,
                "y":  random.random() * h,
                "r":  random.uniform(0.3, 1.2),
                "a":  random.random(),
                "da": random.uniform(-0.03, 0.03),
            }
            for _ in range(25)
        ]

    def _tick(self):
        if not self._animating:
            return
        self.frame         += 1
        self._scene_frames += 1
        if self._scene_frames >= self._scene_length:
            self._scene        = random.choice(_MINI_SCENES)
            self._scene_frames = 0
            self._scene_length = random.randint(220, 280)
        self._draw()
        self.after(33, self._tick)

    def _draw(self):
        self.delete("all")
        w  = self.winfo_width()
        h  = self.winfo_height()
        if w < 10 or h < 10:
            return

        cx = w // 2
        cy = int(h * 0.48)
        s  = min(w, h) / 160   # scale factor: robot fits in ~160px reference square

        self._draw_stars(w, h)
        self._draw_scene(cx, cy, s)
        self._draw_label(w, h)

    def _draw_stars(self, w, h):
        for star in self._stars:
            star["a"] = max(0.05, min(1.0, star["a"] + star["da"]))
            if star["a"] <= 0.05 or star["a"] >= 1.0:
                star["da"] *= -1
            b     = int(star["a"] * 160)
            color = f"#{b:02x}{b+15:02x}{min(255,b+50):02x}"
            x, y, r = star["x"], star["y"], star["r"]
            self.create_oval(x-r, y-r, x+r, y+r, fill=color, outline="")

    def _draw_label(self, w, h):
        """Small channel-colour dot + emoji label at the bottom."""
        self.create_text(
            w // 2, h - 10,
            text=self.emoji,
            font=("", max(10, int(h * 0.12))),
            anchor="center",
        )

    # ── Scene dispatcher ──────────────────────

    def _draw_scene(self, cx, cy, s):
        f = self.frame
        if self._scene == "sleeping":
            self._mini_sleeping(cx, cy, s, f)
        elif self._scene == "lunch":
            self._mini_lunch(cx, cy, s, f)
        else:
            self._mini_working(cx, cy, s, f)

    # ── Robot primitive (scaled) ──────────────

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
        body_color = opts.get("color", "#1a0a3e")
        glow       = opts.get("glow", "#00ff88")

        c = self.create_oval
        r = self.create_rectangle
        l = self.create_line
        t = self.create_text

        # Legs
        l(cx-10*s, cy+50*s, cx-10*s+leg_l*s, cy+70*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        l(cx+10*s, cy+50*s, cx+10*s+leg_r*s, cy+70*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        r(cx-15*s+leg_l*s, cy+68*s, cx-3*s+leg_l*s,  cy+74*s, fill=glow, outline="")
        r(cx+3*s +leg_r*s, cy+68*s, cx+15*s+leg_r*s, cy+74*s, fill=glow, outline="")

        # Body
        r(cx-20*s, cy+10*s, cx+20*s, cy+50*s, fill=body_color, outline=glow, width=max(1,int(1.5*s)))
        c(cx-4*s,  cy+22*s, cx+4*s,  cy+30*s, fill=glow, outline="")

        # Arms
        l(cx-20*s, cy+20*s, cx-32*s, cy+20*s+arm_l*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        c(cx-36*s, cy+16*s+arm_l*s, cx-28*s, cy+24*s+arm_l*s, fill=glow, outline="")
        l(cx+20*s, cy+20*s, cx+32*s, cy+20*s+arm_r*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        c(cx+28*s, cy+16*s+arm_r*s, cx+36*s, cy+24*s+arm_r*s, fill=glow, outline="")

        # Neck
        r(cx-4*s, cy+2*s, cx+4*s, cy+12*s, fill=body_color, outline=glow, width=1)

        # Head
        tx = cx + tilt * 0.4
        ty = cy - 8*s
        r(tx-18*s, ty-26*s, tx+18*s, ty+4*s, fill=body_color, outline=glow, width=max(1,int(1.5*s)))
        font_sz = max(6, int(9*s))
        t(tx-7*s, ty-14*s, text=eye_l, fill=glow, font=("Courier New", font_sz))
        t(tx+7*s, ty-14*s, text=eye_r, fill=glow, font=("Courier New", font_sz))
        t(tx,     ty-5*s,  text=mouth, fill=glow, font=("Courier New", max(5, int(7*s))))

        # Antenna
        l(tx, ty-26*s, tx, ty-40*s, fill=glow, width=max(1,int(1.5*s)))
        c(tx-4*s, ty-45*s, tx+4*s, ty-36*s, fill="#ff6b6b", outline="")

    # ── Scene: sleeping ────────────────────────

    def _mini_sleeping(self, cx, cy, s, f):
        bob = math.sin(f * 0.05) * 3

        # Small moon top-right
        mx, my = cx + int(55*s), cy - int(55*s)
        mr = int(14*s)
        self.create_oval(mx-mr, my-mr, mx+mr, my+mr, fill="#FFD93D", outline="")
        self.create_oval(mx-mr+int(5*s), my-mr-int(3*s),
                         mx+mr+int(4*s), my+mr-int(2*s),
                         fill="#0d0721", outline="")

        self._robot(cx, cy + bob, s, {
            "tilt": 18, "arm_l": 14, "arm_r": 6,
            "eye_l": "—", "eye_r": "—", "mouth": "~",
            "leg_l": 6, "leg_r": -5,
        })

        # Zzz bubbles
        zzz_phase = (f // 30) % 3
        for i in range(zzz_phase + 1):
            self.create_text(
                cx + int(28*s) + i * int(10*s),
                cy - int(40*s) - i * int(10*s) + math.sin(f * 0.04) * 2,
                text="z", fill="#4ECDC4",
                font=("Courier New", max(6, int((7 + i*3)*s)), "bold"),
            )

        # Emoji as a pillow / sign the robot is hugging
        self.create_text(
            cx - int(38*s), cy + int(28*s),
            text=self.emoji,
            font=("", max(8, int(18*s))),
            anchor="center",
        )

    # ── Scene: lunch ───────────────────────────

    def _mini_lunch(self, cx, cy, s, f):
        bob = math.sin(f * 0.09) * 2

        # Tiny table
        self.create_rectangle(cx-int(50*s), cy+int(54*s), cx+int(50*s), cy+int(62*s),
                               fill="#2a1a4e", outline="#00ff88", width=1)
        self.create_rectangle(cx-int(44*s), cy+int(61*s), cx-int(36*s), cy+int(80*s),
                               fill="#2a1a4e", outline="")
        self.create_rectangle(cx+int(36*s), cy+int(61*s), cx+int(44*s), cy+int(80*s),
                               fill="#2a1a4e", outline="")

        # Plate — emoji IS the food
        self.create_oval(cx-int(18*s), cy+int(34*s), cx+int(18*s), cy+int(56*s),
                         fill="#1a0a3e", outline="#00ff88", width=1)
        self.create_text(
            cx, cy + int(44*s),
            text=self.emoji,
            font=("", max(8, int(14*s))),
            anchor="center",
        )

        # Steam
        steam_y = math.sin(f * 0.12) * 2
        for i, sx in enumerate([-5, 0, 5]):
            self.create_line(
                cx + int(sx*s), cy + int(30*s) + steam_y,
                cx + int(sx*s) + 1, cy + int(22*s) + steam_y,
                fill="#445566", width=1,
            )

        eat = math.sin(f * 0.18) * int(8*s)
        self._robot(cx, cy + bob, s, {
            "tilt": 6, "arm_r": -int(14 - eat/s if s > 0 else 14),
            "eye_l": "^", "eye_r": "^", "mouth": "U",
            "leg_l": 4, "leg_r": -4,
        })

    # ── Scene: working ─────────────────────────

    def _mini_working(self, cx, cy, s, f):
        bob = math.sin(f * 0.1) * 2

        # Robot on the left, TV on the right — split the centre between them
        robot_cx = cx - int(22*s)
        tv_left  = cx + int(8*s)

        # Tiny TV / screen — emoji shown on it
        tw, th = int(38*s), int(30*s)
        tv_x = tv_left
        tv_y = cy - int(22*s)
        self.create_rectangle(tv_x, tv_y, tv_x+tw, tv_y+th,
                               fill="#1a0a3e", outline="#ff6b6b",
                               width=max(1, int(1.5*s)), dash=(3, 2))
        self.create_rectangle(tv_x+int(3*s), tv_y+int(3*s),
                               tv_x+tw-int(3*s), tv_y+th-int(3*s),
                               fill="#0d0721", outline="")

        # Emoji on the glitchy screen
        self.create_text(
            tv_x + tw//2, tv_y + th//2,
            text=self.emoji,
            font=("", max(8, int(13*s))),
            anchor="center",
        )

        # Sparks at the top-left corner of the TV
        spark = (f // 8) % 4
        spark_colors = ["#FFD93D", "#FF6B6B", "#FFFFFF", "#FFD93D"]
        for i in range(spark):
            self.create_text(
                tv_x - int(2*s) + i * int(3*s),
                tv_y - int(5*s) + i * int(2*s),
                text="✦", fill=spark_colors[i],
                font=("Courier New", max(5, int((4 + i)*s))),
            )

        # Robot reaching toward the TV (arm_r is in robot-units, NOT pixels)
        self._robot(robot_cx, cy + bob, s, {
            "tilt": 14, "arm_r": -20,
            "eye_l": "●", "eye_r": "●", "mouth": "/",
            "leg_l": 5, "leg_r": -3,
        })


# ─────────────────────────────────────────────────────────────────────────────
#  ROBOT WORLD CANVAS
#  A wide panoramic scene that replaces the whole button area during off periods.
#  Each channel gets its own robot agent that wanders, interacts, and shows its
#  emoji.  Background layers: sky, ground, road, buildings, clouds.
# ─────────────────────────────────────────────────────────────────────────────

# Palette
_SKY   = "#0d0721"
_GROUND= "#1a0a3e"
_ROAD  = "#111122"
_GLOW  = "#00ff88"
_DIM   = "#2a1a4e"
_MOON  = "#FFD93D"
_RED   = "#FF6B6B"
_TEAL  = "#4ECDC4"
_STAR_COLS = ["#ffffff", "#aaccff", "#ffeeaa", "#ccffee"]

# Activity catalogue — (name, eye_l, eye_r, mouth, arm_l, arm_r, leg_l, leg_r, tilt)
_ACTIVITIES = {
    "walk":    ("walk",    "●", "●", "—",  0,   0,   0,   0,   0),
    "run":     ("run",     "●", "●", "D",  10, -10,  12, -12,  0),
    "wave":    ("wave",    "^", "^", ")",  -25,  0,   0,   0,   5),
    "eat":     ("eat",     "^", "^", "U",   0, -18,   4,  -4,  8),
    "sleep":   ("sleep",   "—", "—", "~",  14,   8,   6,  -5, 20),
    "dance":   ("dance",   "*", "*", "D",  -20, 20,  10, -10,  0),
    "fix":     ("fix",     "●", "●", "/",   0, -22,   5,  -3, 14),
    "cheer":   ("cheer",   "^", "^", "D",  -28,-28,   0,   0,  0),
    "skate":   ("skate",   "●", "^", ")",   6, -12,   0,  18,  -8),
    "carry":   ("carry",   "●", "●", "—", -20, -20,   4,  -4,  0),
}
_ACT_KEYS = list(_ACTIVITIES.keys())

# Car colour pairs (body, window)
_CAR_COLORS = [
    ("#FF6B6B", "#ffaaaa"), ("#4ECDC4", "#aaffee"),
    ("#FFD93D", "#fff3aa"), ("#6BCB77", "#aaffaa"),
    ("#cc88ff", "#eebbff"),
]


class _RobotAgent:
    """One robot character in the world scene."""

    def __init__(self, x: float, ground_y: float, emoji: str, color: str, scale: float):
        self.x         = x
        self.ground_y  = ground_y   # pixel y of the ground surface this robot stands on
        self.emoji     = emoji
        self.color     = color      # glow colour (channel colour)
        self.scale     = scale
        self.direction = random.choice([-1, 1])
        self.speed     = random.uniform(0.4, 1.1) * scale * 60
        self.activity  = random.choice(_ACT_KEYS)
        self._act_timer= random.randint(0, 180)
        self._act_dur  = random.randint(80, 220)
        self.phase     = random.uniform(0, math.pi * 2)  # personal phase offset

    def update(self, dt: float, world_w: float):
        self._act_timer += 1
        if self._act_timer >= self._act_dur:
            self._act_timer = 0
            self._act_dur   = random.randint(80, 220)
            self.activity   = random.choice(_ACT_KEYS)
            if random.random() < 0.35:
                self.direction *= -1

        moving = self.activity in ("walk", "run", "skate")
        if moving:
            mult = 2.0 if self.activity == "run" else (1.5 if self.activity == "skate" else 1.0)
            self.x += self.direction * self.speed * mult * dt
            # Wrap with a margin so robots re-enter from the opposite edge
            margin = self.scale * 60
            if self.x > world_w + margin:
                self.x = -margin
            elif self.x < -margin:
                self.x = world_w + margin


class _CarAgent:
    """A little car driving along the road."""

    def __init__(self, x: float, road_y: float, scale: float):
        self.x       = x
        self.road_y  = road_y
        self.scale   = scale
        self.direction = random.choice([-1, 1])
        self.speed   = random.uniform(1.5, 3.5) * scale * 60
        cols         = random.choice(_CAR_COLORS)
        self.body_col= cols[0]
        self.win_col = cols[1]
        # Emoji passenger
        self.emoji   = random.choice(["🐭","🎵","🌟","🐘","🤖","👾","🚀","🎮"])

    def update(self, dt: float, world_w: float):
        self.x += self.direction * self.speed * dt
        margin = self.scale * 80
        if self.x > world_w + margin:
            self.x = -margin
            self.direction = 1
        elif self.x < -margin:
            self.x = world_w + margin
            self.direction = -1


class RobotWorldCanvas(tk.Canvas):
    """Panoramic robot world shown in the full bottom panel during off periods."""

    def __init__(self, parent, channels: list, **kwargs):
        super().__init__(parent, bg=_SKY, highlightthickness=0, **kwargs)
        self._animating = False
        self._frame     = 0
        self._channels  = channels   # list of {emoji, color, name}
        self._agents: list[_RobotAgent] = []
        self._cars:   list[_CarAgent]   = []
        self._clouds  = []
        self._stars   = []
        self._buildings = []
        self._last_t  = 0.0

    # ── Public ────────────────────────────────

    def start(self):
        self._animating = True
        self._last_t    = time.time()
        self.after(50, self._boot)   # wait one tick so winfo_width is valid

    def stop(self):
        self._animating = False

    # ── Bootstrap (deferred so geometry is known) ──

    def _boot(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10:
            self.after(50, self._boot)
            return
        self._setup_world(w, h)
        self._tick()

    def _setup_world(self, w: int, h: int):
        s = h / 160          # scale: treat canvas height as 160 reference units

        ground_y = h * 0.72  # where robots stand
        road_y   = h * 0.58  # road surface (robots on road walk in front of cars)

        # Stars
        self._stars = [
            {"x": random.random()*w, "y": random.random()*h*0.55,
             "r": random.uniform(0.5,1.8), "a": random.random(),
             "da": random.uniform(-0.025, 0.025),
             "col": random.choice(_STAR_COLS)}
            for _ in range(60)
        ]

        # Clouds (decorative blobs drifting slowly)
        self._clouds = [
            {"x": random.random()*w, "y": random.uniform(h*0.05, h*0.30),
             "r": random.uniform(18, 40)*s, "speed": random.uniform(0.2,0.6)*s*60,
             "alpha": random.uniform(0.2, 0.45)}
            for _ in range(6)
        ]

        # Buildings silhouette
        self._buildings = []
        bx = 0
        while bx < w:
            bw = random.randint(int(28*s), int(60*s))
            bh = random.randint(int(30*s), int(90*s))
            windows = []
            for wy in range(int(bh*0.15), int(bh*0.85), int(12*s)):
                for wx in range(int(bw*0.15), int(bw*0.85), int(12*s)):
                    windows.append((wx, wy, random.random() < 0.6))
            self._buildings.append({"x": bx, "w": bw, "h": bh, "windows": windows})
            bx += bw + random.randint(0, int(8*s))

        # Robot agents — one per channel, rest are extras
        extras = max(0, 5 - len(self._channels))
        agents = []
        for i, ch in enumerate(self._channels):
            gx = (i + 0.5) * w / len(self._channels)
            agents.append(_RobotAgent(gx, ground_y, ch["emoji"],
                                      ch.get("color", _GLOW), s))
        # Extra unnamed robots
        extra_emojis = ["🤖","👾","🚀","🎮","💫","⚡","🌙","🔧"]
        for i in range(extras):
            agents.append(_RobotAgent(
                random.uniform(0, w), ground_y,
                random.choice(extra_emojis), _GLOW, s
            ))
        # A couple of robots on the road (smaller, z-depth feel)
        for _ in range(2):
            agents.append(_RobotAgent(
                random.uniform(0, w), road_y,
                random.choice(extra_emojis), _TEAL, s * 0.65
            ))
        self._agents = agents

        # Cars
        self._cars = [
            _CarAgent(random.uniform(0, w), road_y, s)
            for _ in range(random.randint(2, 4))
        ]

        self._ground_y  = ground_y
        self._road_y    = road_y
        self._scale     = s

    # ── Tick ─────────────────────────────────

    def _tick(self):
        if not self._animating:
            return
        now   = time.time()
        dt    = min(now - self._last_t, 0.05)   # cap at 50ms
        self._last_t = now
        self._frame += 1
        self._update(dt)
        self._draw()
        self.after(33, self._tick)

    def _update(self, dt: float):
        w = self.winfo_width()
        for a in self._agents:
            a.update(dt, w)
        for c in self._cars:
            c.update(dt, w)
        # Drift clouds
        for cl in self._clouds:
            cl["x"] += cl["speed"] * dt
            if cl["x"] - cl["r"] > self.winfo_width():
                cl["x"] = -cl["r"]

    # ── Draw ──────────────────────────────────

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        f = self._frame
        s = self._scale

        self._draw_sky(w, h, f)
        self._draw_buildings(w, h)
        self._draw_road(w, h, f, s)
        self._draw_cars(h, f)
        # Draw ground-level agents behind road robots
        for a in sorted(self._agents, key=lambda a: a.ground_y):
            self._draw_agent(a, f)
        self._draw_ground_details(w, h, s, f)

    def _draw_sky(self, w, h, f):
        # Gradient sky — three horizontal bands
        sky_h = int(h * 0.60)
        self.create_rectangle(0, 0, w, sky_h, fill=_SKY, outline="")
        # Twinkling stars
        for st in self._stars:
            st["a"] = max(0.05, min(1.0, st["a"] + st["da"]))
            if st["a"] <= 0.05 or st["a"] >= 1.0:
                st["da"] *= -1
            br = int(st["a"] * 210)
            col = st["col"]
            x, y, r = st["x"], st["y"], st["r"] * (0.8 + 0.2 * st["a"])
            self.create_oval(x-r, y-r, x+r, y+r, fill=col, outline="")
        # Moon
        mx, my = w * 0.88, h * 0.18
        mr = self._scale * 18
        self.create_oval(mx-mr, my-mr, mx+mr, my+mr, fill=_MOON, outline="")
        self.create_oval(mx+mr*0.3, my-mr*1.1, mx+mr*1.3, my+mr*0.9,
                         fill=_SKY, outline="")
        # Clouds
        for cl in self._clouds:
            br = int(cl["alpha"] * 60)
            col = f"#{br+10:02x}{br:02x}{br+25:02x}"
            r = cl["r"]
            x, y = cl["x"], cl["y"]
            for dx, dy, dr in [(0,0,1.0),(-r*0.55,-r*0.25,0.65),(r*0.55,-r*0.2,0.65),(0,-r*0.4,0.7)]:
                rr = r * dr
                self.create_oval(x+dx-rr, y+dy-rr, x+dx+rr, y+dy+rr, fill=col, outline="")

    def _draw_buildings(self, w, h):
        ground_y = self._ground_y
        for b in self._buildings:
            bx = b["x"]
            bw = b["w"]
            bh = b["h"]
            by = ground_y - bh
            # Building body
            self.create_rectangle(bx, by, bx+bw, ground_y, fill=_DIM, outline="#1e0e3a")
            # Windows
            for (wx, wy, lit) in b["windows"]:
                col = "#FFD93D" if lit else "#0d0518"
                ws  = max(3, int(bw * 0.12))
                self.create_rectangle(bx+wx, by+wy, bx+wx+ws, by+wy+ws, fill=col, outline="")
            # Rooftop antenna
            mid = bx + bw//2
            self.create_line(mid, by, mid, by-int(8*self._scale), fill=_GLOW, width=1)
            self.create_oval(mid-2, by-int(10*self._scale), mid+2, by-int(6*self._scale),
                             fill=_RED, outline="")

    def _draw_road(self, w, h, f, s):
        road_y = self._road_y
        road_h = int(h * 0.14)
        # Road surface
        self.create_rectangle(0, road_y, w, road_y+road_h, fill=_ROAD, outline="")
        # Lane markings — dashes scroll with frame to suggest motion
        dash_w, dash_gap = int(28*s), int(20*s)
        offset = (f * 3) % (dash_w + dash_gap)
        ly = road_y + road_h // 2
        x = -dash_gap + offset
        while x < w + dash_w:
            self.create_line(x, ly, x+dash_w, ly, fill="#333355", width=max(1,int(2*s)), dash=(4,4))
            x += dash_w + dash_gap
        # Kerb lines
        self.create_line(0, road_y, w, road_y, fill="#2a1a5e", width=max(1,int(2*s)))
        self.create_line(0, road_y+road_h, w, road_y+road_h, fill="#2a1a5e", width=max(1,int(2*s)))

    def _draw_cars(self, h, f):
        s = self._scale
        for car in self._cars:
            self._draw_car(car, f, s)

    def _draw_car(self, car: _CarAgent, f: int, s: float):
        x   = car.x
        y   = car.road_y
        d   = car.direction
        cw  = int(52*s)
        ch  = int(24*s)
        wr  = int(8*s)    # wheel radius
        # Flip if going left
        if d < 0:
            x0, x1 = x - cw//2, x + cw//2
        else:
            x0, x1 = x - cw//2, x + cw//2

        # Shadow
        self.create_oval(x0+int(4*s), y+int(1*s), x1-int(4*s), y+int(5*s),
                         fill="#080412", outline="")
        # Body
        self.create_rectangle(x0, y-ch, x1, y, fill=car.body_col, outline="")
        # Cab (rounded roof shape via two overlapping rects + oval)
        cab_inset = int(8*s)
        self.create_rectangle(x0+cab_inset, y-ch-int(14*s),
                               x1-cab_inset, y-ch+int(4*s),
                               fill=car.body_col, outline="")
        self.create_oval(x0+cab_inset, y-ch-int(14*s),
                         x1-cab_inset, y-ch+int(4*s),
                         fill=car.body_col, outline="")
        # Windows
        self.create_rectangle(x0+cab_inset+int(3*s), y-ch-int(11*s),
                               x1-cab_inset-int(3*s), y-ch+int(2*s),
                               fill=car.win_col, outline="")
        # Wheels (animate rolling)
        roll = int(x / 3) % 360
        for wx in [x0+int(10*s), x1-int(10*s)]:
            self.create_oval(wx-wr, y-wr, wx+wr, y+wr, fill="#111133", outline=_GLOW, width=1)
            # Spoke
            ang = math.radians(roll)
            self.create_line(wx, y, wx+int(wr*0.8*math.cos(ang)), y+int(wr*0.8*math.sin(ang)),
                             fill=_GLOW, width=1)
        # Emoji passenger visible through window
        self.create_text((x0+x1)//2, y-ch-int(4*s), text=car.emoji,
                         font=("", max(7, int(10*s))), anchor="center")
        # Headlights / tail lights
        light_col = "#FFD93D" if d > 0 else _RED
        self.create_oval(x1-int(4*s), y-int(10*s), x1+int(2*s), y-int(4*s),
                         fill=light_col, outline="")
        back_col  = _RED if d > 0 else "#FFD93D"
        self.create_oval(x0-int(2*s), y-int(10*s), x0+int(4*s), y-int(4*s),
                         fill=back_col, outline="")

    def _draw_ground_details(self, w, h, s, f):
        # Ground strip
        gy = self._ground_y
        self.create_rectangle(0, gy, w, h, fill=_GROUND, outline="")
        # Pixel grass tufts
        for gx in range(0, w, int(22*s)):
            jitter = (gx * 7919) % int(8*s)
            ht = int(5*s) + jitter % int(4*s)
            self.create_line(gx, gy, gx, gy-ht, fill="#1e3a1e", width=max(1, int(2*s)))
            self.create_line(gx+int(3*s), gy, gx+int(3*s), gy-ht+int(2*s),
                             fill="#1e3a1e", width=max(1, int(2*s)))
        # Lamp posts
        for lx in range(int(w*0.15), w, int(w//5)):
            pole_h = int(50*s)
            self.create_line(lx, gy, lx, gy-pole_h, fill="#2a1a5e", width=max(2, int(3*s)))
            self.create_rectangle(lx-int(6*s), gy-pole_h-int(4*s),
                                  lx+int(6*s), gy-pole_h,
                                  fill="#2a1a5e", outline=_GLOW, width=1)
            # Glow halo
            glow_alpha = 0.4 + 0.2 * math.sin(f * 0.04 + lx)
            gb = int(glow_alpha * 80)
            self.create_oval(lx-int(12*s), gy-pole_h-int(12*s),
                             lx+int(12*s), gy-pole_h+int(8*s),
                             fill=f"#{gb:02x}{min(255,gb*2+40):02x}{gb:02x}", outline="")
            self.create_text(lx, gy-pole_h-int(2*s), text="💡",
                             font=("", max(6, int(8*s))), anchor="center")

    def _draw_agent(self, agent: _RobotAgent, f: int):
        act_name = agent.activity
        act      = _ACTIVITIES.get(act_name, _ACTIVITIES["walk"])
        _, eye_l, eye_r, mouth, arm_l, arm_r, leg_l, leg_r, tilt = act

        s  = agent.scale
        cx = agent.x
        cy = agent.ground_y

        # Animate limbs based on activity
        phase = f * 0.18 + agent.phase
        if act_name in ("walk", "run"):
            swing  = 14 if act_name == "run" else 9
            leg_l  =  swing * math.sin(phase)
            leg_r  = -swing * math.sin(phase)
            arm_l  = -swing * 0.6 * math.sin(phase)
            arm_r  =  swing * 0.6 * math.sin(phase)
        elif act_name == "dance":
            arm_l  = -22 + 8 * math.sin(phase * 1.3)
            arm_r  =  22 - 8 * math.sin(phase * 1.3)
            leg_l  =  10 * math.sin(phase)
            leg_r  = -10 * math.sin(phase)
        elif act_name == "wave":
            arm_l  = -28 + 10 * math.sin(phase * 2)
        elif act_name == "cheer":
            arm_l  = -28 + 6 * math.sin(phase * 2.5)
            arm_r  = -28 + 6 * math.sin(phase * 2.5 + 0.5)
        elif act_name == "skate":
            leg_r  = 18 * math.sin(phase * 0.7)

        bob = math.sin(phase * 0.5) * 2 * s

        # Flip direction (mirror tilt & arm_l/r swap for left-facing robots)
        d = agent.direction
        if d < 0:
            tilt  = -tilt
            arm_l, arm_r = arm_r, arm_l
            leg_l, leg_r = leg_r, leg_l

        glow = agent.color

        self._draw_robot(cx, cy + bob, s, {
            "tilt":  tilt,
            "arm_l": arm_l, "arm_r": arm_r,
            "leg_l": leg_l, "leg_r": leg_r,
            "eye_l": eye_l, "eye_r": eye_r,
            "mouth": mouth,
            "color": "#1a0a3e",
            "glow":  glow,
        })

        # Emoji prop — floats above the robot's head
        prop_y = cy + bob - s * 54
        if act_name == "carry":
            prop_y = cy + bob - s * 32   # held in arms
        elif act_name == "sleep":
            prop_y = cy + bob - s * 20   # resting near face
        # Coloured halo behind the emoji so it reads against any background
        hr = int(10*s)
        self.create_oval(cx-hr, prop_y-hr, cx+hr, prop_y+hr,
                         fill=glow, outline="")
        self.create_text(cx, prop_y, text=agent.emoji,
                         font=("", max(7, int(13*s))), anchor="center")

        # Skate board
        if act_name == "skate":
            bw = int(28*s)
            self.create_rectangle(cx-bw//2, cy+int(68*s), cx+bw//2, cy+int(72*s),
                                   fill="#4ECDC4", outline="")
            for wx in [-int(8*s), int(8*s)]:
                self.create_oval(cx+wx-int(3*s), cy+int(70*s),
                                 cx+wx+int(3*s), cy+int(76*s),
                                 fill="#111133", outline=_GLOW, width=1)

        # Sleep Zzz
        if act_name == "sleep":
            zp = (f // 28) % 3
            for i in range(zp+1):
                self.create_text(
                    cx + int((18+i*10)*s),
                    cy + bob - int((30+i*10)*s),
                    text="z", fill=_TEAL,
                    font=("Courier New", max(6, int((6+i*3)*s)), "bold"),
                )

        # Food item when eating
        if act_name == "eat":
            self.create_text(cx + int(30*s), cy + bob - int(10*s),
                             text="🍕", font=("", max(7, int(11*s))), anchor="center")

    def _draw_robot(self, cx, cy, s, opts):
        """Shared robot-drawing primitive (same as MiniRobotCanvas._robot)."""
        tilt  = opts.get("tilt", 0)
        arm_l = opts.get("arm_l", 0)
        arm_r = opts.get("arm_r", 0)
        leg_l = opts.get("leg_l", 0)
        leg_r = opts.get("leg_r", 0)
        eye_l = opts.get("eye_l", "●")
        eye_r = opts.get("eye_r", "●")
        mouth = opts.get("mouth", "—")
        bc    = opts.get("color", "#1a0a3e")
        glow  = opts.get("glow",  _GLOW)

        c = self.create_oval
        r = self.create_rectangle
        l = self.create_line
        t = self.create_text

        l(cx-10*s, cy+50*s, cx-10*s+leg_l*s, cy+70*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        l(cx+10*s, cy+50*s, cx+10*s+leg_r*s, cy+70*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        r(cx-15*s+leg_l*s, cy+68*s, cx-3*s+leg_l*s,  cy+74*s, fill=glow, outline="")
        r(cx+3*s +leg_r*s, cy+68*s, cx+15*s+leg_r*s, cy+74*s, fill=glow, outline="")
        r(cx-20*s, cy+10*s, cx+20*s, cy+50*s, fill=bc, outline=glow, width=max(1,int(1.5*s)))
        c(cx-4*s,  cy+22*s, cx+4*s,  cy+30*s, fill=glow, outline="")
        l(cx-20*s, cy+20*s, cx-32*s, cy+20*s+arm_l*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        c(cx-36*s, cy+16*s+arm_l*s, cx-28*s, cy+24*s+arm_l*s, fill=glow, outline="")
        l(cx+20*s, cy+20*s, cx+32*s, cy+20*s+arm_r*s, fill=glow, width=max(1,int(4*s)), capstyle=tk.ROUND)
        c(cx+28*s, cy+16*s+arm_r*s, cx+36*s, cy+24*s+arm_r*s, fill=glow, outline="")
        r(cx-4*s, cy+2*s, cx+4*s, cy+12*s, fill=bc, outline=glow, width=1)
        tx = cx + tilt * 0.4
        ty = cy - 8*s
        r(tx-18*s, ty-26*s, tx+18*s, ty+4*s, fill=bc, outline=glow, width=max(1,int(1.5*s)))
        fs = max(6, int(9*s))
        t(tx-7*s, ty-14*s, text=eye_l, fill=glow, font=("Courier New", fs))
        t(tx+7*s, ty-14*s, text=eye_r, fill=glow, font=("Courier New", fs))
        t(tx,     ty-5*s,  text=mouth, fill=glow, font=("Courier New", max(5, int(7*s))))
        l(tx, ty-26*s, tx, ty-40*s, fill=glow, width=max(1,int(1.5*s)))
        c(tx-4*s, ty-45*s, tx+4*s, ty-36*s, fill=_RED, outline="")


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

    # ── Internal loop ─────────────────────────

    def _init_stars(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w, h = 1920, 1080
        self._stars = [
            {
                "x":  random.random() * w,
                "y":  random.random() * h,
                "r":  random.uniform(0.5, 2),
                "a":  random.random(),
                "da": random.uniform(-0.02, 0.02),
            }
            for _ in range(80)
        ]

    def _tick(self):
        if not self._animating:
            return
        self.frame += 1
        self._draw()
        self.after(33, self._tick)  # ~30fps

    def _draw(self):
        self.delete("all")
        w  = self.winfo_width()
        h  = self.winfo_height()
        cx = w // 2
        cy = int(h * 0.42)

        self._draw_stars(w, h)
        self._draw_scene(cx, cy)
        self._draw_text(cx, h)

    def _draw_stars(self, w, h):
        for s in self._stars:
            s["a"] = max(0.05, min(1.0, s["a"] + s["da"]))
            if s["a"] <= 0.05 or s["a"] >= 1.0:
                s["da"] *= -1
            brightness = int(s["a"] * 180)
            color = f"#{brightness:02x}{brightness+20:02x}{min(255,brightness+60):02x}"
            x, y, r = s["x"], s["y"], s["r"]
            self.create_oval(x-r, y-r, x+r, y+r, fill=color, outline="")

    def _draw_text(self, cx, h):
        scene  = SCENES.get(self.scene, SCENES["sleeping"])
        on_time = next_on_time()
        self.create_text(cx, h * 0.72, text=scene["label"],
                         fill="#2a1a5e", font=("Courier New", 12, "bold"),
                         anchor="center")
        self.create_text(cx, h * 0.80, text=scene["msg"],
                         fill="#00ff88", font=("Courier New", 28, "bold"),
                         anchor="center")
        self.create_text(cx, h * 0.87, text=f"{scene['sub']}  ·  back at {on_time}",
                         fill="#444466", font=("Courier New", 14),
                         anchor="center")

    # ── Scene dispatcher ───────────────────────

    def _draw_scene(self, cx, cy):
        f = self.frame
        s = self.scene
        if s == "sleeping":
            self._scene_sleeping(cx, cy, f)
        elif s == "lunch":
            self._scene_lunch(cx, cy, f)
        else:
            self._scene_fixing(cx, cy, f)

    # ── Robot primitive ────────────────────────

    def _robot(self, cx, cy, opts={}):
        """Draw a simple robot at (cx, cy). opts controls pose and expression."""
        tilt  = opts.get("tilt", 0)
        arm_l = opts.get("arm_l", 0)
        arm_r = opts.get("arm_r", 0)
        leg_l = opts.get("leg_l", 0)
        leg_r = opts.get("leg_r", 0)
        eye_l = opts.get("eye_l", "●")
        eye_r = opts.get("eye_r", "●")
        mouth = opts.get("mouth", "—")
        color = opts.get("color", "#1a0a3e")
        glow  = opts.get("glow", "#00ff88")
        scale = opts.get("scale", 1.0)

        s = scale
        c = self.create_oval
        r = self.create_rectangle
        l = self.create_line
        t = self.create_text

        # Legs
        l(cx-10*s, cy+50*s, cx-10*s+leg_l*s, cy+72*s, fill=glow, width=5*s, capstyle=tk.ROUND)
        l(cx+10*s, cy+50*s, cx+10*s+leg_r*s, cy+72*s, fill=glow, width=5*s, capstyle=tk.ROUND)
        r(cx-16*s+leg_l*s, cy+70*s, cx-2*s+leg_l*s,  cy+76*s, fill=glow, outline="")
        r(cx+4*s +leg_r*s, cy+70*s, cx+18*s+leg_r*s, cy+76*s, fill=glow, outline="")

        # Body
        r(cx-22*s, cy+10*s, cx+22*s, cy+52*s, fill=color, outline=glow, width=2)
        c(cx-5*s,  cy+22*s, cx+5*s,  cy+32*s, fill=glow, outline="")

        # Arms
        l(cx-22*s, cy+22*s, cx-34*s, cy+22*s+arm_l*s, fill=glow, width=5*s, capstyle=tk.ROUND)
        c(cx-38*s, cy+18*s+arm_l*s, cx-30*s, cy+26*s+arm_l*s, fill=glow, outline="")
        l(cx+22*s, cy+22*s, cx+34*s, cy+22*s+arm_r*s, fill=glow, width=5*s, capstyle=tk.ROUND)
        c(cx+30*s, cy+18*s+arm_r*s, cx+38*s, cy+26*s+arm_r*s, fill=glow, outline="")

        # Neck
        r(cx-5*s, cy+2*s, cx+5*s, cy+12*s, fill=color, outline=glow, width=1)

        # Head (with tilt offset)
        tx = cx + tilt * 0.4
        ty = cy - 8*s
        r(tx-20*s, ty-28*s, tx+20*s, ty+6*s, fill=color, outline=glow, width=2)
        t(tx-8*s, ty-16*s, text=eye_l, fill=glow, font=("Courier New", int(10*s)))
        t(tx+8*s, ty-16*s, text=eye_r, fill=glow, font=("Courier New", int(10*s)))
        t(tx,     ty-6*s,  text=mouth, fill=glow, font=("Courier New", int(8*s)))

        # Antenna
        l(tx, ty-28*s, tx, ty-42*s, fill=glow, width=2)
        c(tx-5*s, ty-47*s, tx+5*s, ty-37*s, fill="#ff6b6b", outline="")

    # ── Scene: sleeping ────────────────────────

    def _scene_sleeping(self, cx, cy, f):
        bob = math.sin(f * 0.05) * 4

        # Moon (crescent)
        self.create_oval(cx+158, cy-125, cx+200, cy-83,  fill="#FFD93D", outline="")
        self.create_oval(cx+168, cy-130, cx+208, cy-88,  fill="#0d0721", outline="")

        # Robot 1 slumped
        self._robot(cx-100, cy+bob, {
            "tilt": 20, "arm_l": 18, "arm_r": 8,
            "eye_l": "—", "eye_r": "—", "mouth": "~",
            "leg_l": 8, "leg_r": -6,
        })

        # Zzz floating up
        zzz_phase = (f // 25) % 3
        for i in range(zzz_phase + 1):
            self.create_text(
                cx-55+i*14,
                cy-30-i*14+math.sin(f*0.04)*3,
                text="z", fill="#4ECDC4",
                font=("Courier New", 9+i*4, "bold"),
            )

        # Robot 2 leaning
        self._robot(cx+100, cy-bob, {
            "tilt": -18, "arm_l": -12, "arm_r": -8,
            "eye_l": "·", "eye_r": "·", "mouth": "o",
            "leg_l": -6, "leg_r": 6,
        })

        # Sparkles near robot 2
        for i in range(3):
            sx = cx+145+i*18
            sy = cy-52-i*8+math.sin(f*0.07+i)*4
            r  = 4-i
            self.create_oval(sx-r, sy-r, sx+r, sy+r, fill="#FFD93D", outline="")

    # ── Scene: lunch ───────────────────────────

    def _scene_lunch(self, cx, cy, f):
        bob = math.sin(f * 0.08) * 3

        # Table
        self.create_rectangle(cx-90, cy+55, cx+90, cy+65, fill="#2a1a4e", outline="#00ff88", width=1)
        self.create_rectangle(cx-82, cy+64, cx-70, cy+92, fill="#2a1a4e", outline="")
        self.create_rectangle(cx+70, cy+64, cx+82, cy+92, fill="#2a1a4e", outline="")

        # Plate
        self.create_oval(cx-22, cy+36, cx+22, cy+58, fill="#1a0a3e", outline="#00ff88", width=1)
        for dx, dy, w, h, col in [(-8,42,14,8,"#FF6B6B"),(2,44,10,6,"#FFD93D"),(-4,38,8,5,"#4ECDC4")]:
            self.create_rectangle(cx+dx, cy+dy, cx+dx+w, cy+dy+h, fill=col, outline="")

        # Steam above plate
        steam_y = math.sin(f * 0.12) * 3
        for i, sx in enumerate([-6, 0, 6]):
            for j in range(3):
                jitter = math.sin(f*0.08+i+j)*2
                self.create_line(
                    cx+sx+jitter, cy+32-j*7+steam_y,
                    cx+sx+jitter+2, cy+26-j*7+steam_y,
                    fill="#445566", width=1,
                )

        # Cup
        mug_x, mug_y = cx+42, cy+40
        self.create_rectangle(mug_x, mug_y, mug_x+18, mug_y+20, fill="#1a0a3e", outline="#00ff88", width=1)
        self.create_arc(mug_x+16, mug_y+4, mug_x+28, mug_y+16, start=-90, extent=180, style="arc", outline="#00ff88", width=1)
        self.create_rectangle(mug_x+2, mug_y+2, mug_x+16, mug_y+8, fill="#4ECDC4", outline="")
        cup_steam = math.sin(f * 0.15) * 2
        for si in range(2):
            self.create_line(mug_x+5+si*6, mug_y-4+cup_steam, mug_x+7+si*6, mug_y-10+cup_steam, fill="#445566", width=1)

        # Robot 1 eating
        eat = math.sin(f * 0.18) * 10
        self._robot(cx-120, cy+bob, {
            "tilt": 8, "arm_r": eat-16,
            "eye_l": "^", "eye_r": "^", "mouth": "U",
            "leg_l": 4, "leg_r": -4,
        })
        fork_x = cx-84
        fork_y = cy+22+eat-16+bob
        self.create_line(fork_x, fork_y, fork_x, fork_y-14, fill="#00ff88", width=2)
        for tine in range(3):
            self.create_line(fork_x-3+tine*3, fork_y-14, fork_x-3+tine*3, fork_y-20, fill="#00ff88", width=1)

        # Robot 2 relaxing
        self._robot(cx+130, cy-bob, {
            "tilt": -8, "arm_l": -8, "arm_r": -18,
            "eye_l": "●", "eye_r": "^", "mouth": ")",
            "leg_l": -5, "leg_r": 3,
        })

    # ── Scene: fixing ──────────────────────────

    def _scene_fixing(self, cx, cy, f):
        bob = math.sin(f * 0.1) * 2

        # TV being repaired
        self.create_rectangle(cx-50, cy-30, cx+50, cy+40, fill="#1a0a3e", outline="#ff6b6b", width=2, dash=(4,2))
        self.create_rectangle(cx-38, cy-20, cx+38, cy+28, fill="#0d0721", outline="#333333", width=1)

        # Static
        for i in range(8):
            sx = cx-34+(i*10+f*3)%68
            sy = cy-16+(i*7+f*2)%40
            self.create_rectangle(sx, sy, sx+7, sy+3, fill="#00ff88", outline="")

        self.create_text(cx, cy+35, text="⚠ NO SIGNAL", fill="#ff6b6b", font=("Courier New", 8, "bold"), anchor="center")

        # Sparks
        spark = (f // 6) % 5
        spark_colors = ["#FFD93D", "#FF6B6B", "#FFFFFF", "#FFD93D", "#FF6B6B"]
        for i in range(min(spark, 4)):
            self.create_text(cx-52+i*6, cy-28+i*5, text="✦", fill=spark_colors[i], font=("Courier New", 8+i*2))

        # Robot 1 working
        self._robot(cx-120, cy+bob, {
            "tilt": 12, "arm_r": -22,
            "eye_l": "●", "eye_r": "●", "mouth": "/",
            "leg_l": 6, "leg_r": -4,
        })

        # Robot 2 supervising
        self._robot(cx+120, cy-bob, {
            "tilt": -10, "arm_l": -28, "arm_r": 6,
            "eye_l": "◉", "eye_r": "◉", "mouth": "!",
            "leg_l": -5, "leg_r": 4,
        })
        # Clipboard
        self.create_rectangle(cx+140, cy-20, cx+160, cy+6, fill="#2a1a4e", outline="#00ff88", width=1)
        for row in range(3):
            self.create_line(cx+143, cy-14+row*7, cx+157, cy-14+row*7, fill="#00ff88", width=1)