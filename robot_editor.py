"""
Robot Animation Editor - Design animated robot scenes for Toddler TV.

Run:  python robot_editor.py

Each body part has a POSE (center value) and a MOTION (amplitude + speed).
The animation cycles: value = pose + amplitude * sin(frame * speed)

For example, an eating robot:
  arm_r pose=-10, amplitude=8, speed=0.18
  → arm swings between -18 and -2, like raising food to mouth

The editor previews the animation live and exports ready-to-paste Python code.
"""

import math
import tkinter as tk
from tkinter import ttk

# ─────────────────────────────────────────────
#  EXPRESSION OPTIONS
# ─────────────────────────────────────────────

EYES = ["●", "◉", "^", "—", "·", "*", "○", "♥", "X", "?"]
MOUTHS = ["—", "~", ")", "(", "U", "D", "o", "/", "!", "O", "^", ".", ":"]

# ─────────────────────────────────────────────
#  ANIMATED PRESETS
# ─────────────────────────────────────────────

# Each preset has pose (static center) and motion (amplitude, speed) per param.
# Motion is optional — if missing, the part doesn't animate.

PRESETS = {
    "Idle": {
        "pose": {"tilt": 0, "arm_l": 0, "arm_r": 0, "leg_l": 0, "leg_r": 0,
                 "eye_l": "●", "eye_r": "●", "mouth": "—", "bob": 0},
        "motion": {"bob": (4, 0.06)},
    },
    "Walking": {
        "pose": {"tilt": 0, "arm_l": 0, "arm_r": 0, "leg_l": 0, "leg_r": 0,
                 "eye_l": "●", "eye_r": "●", "mouth": "—", "bob": 0},
        "motion": {"arm_l": (10, 0.15), "arm_r": (-10, 0.15),
                   "leg_l": (8, 0.15), "leg_r": (-8, 0.15), "bob": (2, 0.3)},
    },
    "Running": {
        "pose": {"tilt": 5, "arm_l": 0, "arm_r": 0, "leg_l": 0, "leg_r": 0,
                 "eye_l": "●", "eye_r": "●", "mouth": "D", "bob": 0},
        "motion": {"arm_l": (15, 0.25), "arm_r": (-15, 0.25),
                   "leg_l": (12, 0.25), "leg_r": (-12, 0.25),
                   "bob": (5, 0.5), "tilt": (3, 0.12)},
    },
    "Eating": {
        "pose": {"tilt": 8, "arm_l": 0, "arm_r": -10, "leg_l": 4, "leg_r": -4,
                 "eye_l": "^", "eye_r": "^", "mouth": "U", "bob": 0},
        "motion": {"arm_r": (8, 0.18), "bob": (3, 0.08), "tilt": (3, 0.09)},
    },
    "Sleeping": {
        "pose": {"tilt": 20, "arm_l": 14, "arm_r": 8, "leg_l": 8, "leg_r": -6,
                 "eye_l": "—", "eye_r": "—", "mouth": "~", "bob": 0},
        "motion": {"bob": (4, 0.05), "tilt": (2, 0.03)},
    },
    "Waving": {
        "pose": {"tilt": 5, "arm_l": -15, "arm_r": 0, "leg_l": 0, "leg_r": 0,
                 "eye_l": "^", "eye_r": "^", "mouth": ")", "bob": 0},
        "motion": {"arm_l": (12, 0.2), "bob": (2, 0.06)},
    },
    "Dancing": {
        "pose": {"tilt": 0, "arm_l": -10, "arm_r": 10, "leg_l": 0, "leg_r": 0,
                 "eye_l": "*", "eye_r": "*", "mouth": "D", "bob": 0},
        "motion": {"arm_l": (18, 0.18), "arm_r": (-18, 0.18),
                   "leg_l": (10, 0.18), "leg_r": (-10, 0.18),
                   "bob": (6, 0.36), "tilt": (8, 0.09)},
    },
    "Working": {
        "pose": {"tilt": 12, "arm_l": 0, "arm_r": -15, "leg_l": 6, "leg_r": -4,
                 "eye_l": "●", "eye_r": "●", "mouth": "/", "bob": 0},
        "motion": {"arm_r": (7, 0.22), "bob": (2, 0.1), "tilt": (2, 0.06)},
    },
    "Cheering": {
        "pose": {"tilt": 0, "arm_l": -22, "arm_r": -22, "leg_l": 0, "leg_r": 0,
                 "eye_l": "^", "eye_r": "^", "mouth": "D", "bob": 0},
        "motion": {"arm_l": (8, 0.3), "arm_r": (-8, 0.3),
                   "bob": (5, 0.3), "leg_l": (4, 0.3), "leg_r": (-4, 0.3)},
    },
    "Supervising": {
        "pose": {"tilt": -10, "arm_l": -20, "arm_r": 6, "leg_l": -5, "leg_r": 4,
                 "eye_l": "◉", "eye_r": "◉", "mouth": "!", "bob": 0},
        "motion": {"bob": (2, 0.06), "arm_l": (3, 0.08)},
    },
}

# Body part keys that have sliders
BODY_PARTS = ["tilt", "arm_l", "arm_r", "leg_l", "leg_r", "bob"]
PART_LABELS = {
    "tilt": "Head Tilt",
    "arm_l": "Left Arm",
    "arm_r": "Right Arm",
    "leg_l": "Left Leg",
    "leg_r": "Right Leg",
    "bob": "Body Bob",
}
PART_RANGES = {
    "tilt": (-30, 30),
    "arm_l": (-30, 30),
    "arm_r": (-30, 30),
    "leg_l": (-15, 15),
    "leg_r": (-15, 15),
    "bob": (-10, 10),
}


# ─────────────────────────────────────────────
#  ROBOT DRAWING
# ─────────────────────────────────────────────

def draw_robot(canvas, cx, cy, opts, scale=1.0):
    """Draw a robot. Matches RobotCanvas._robot() from robot_canvas.py."""
    s = scale
    tilt = opts.get("tilt", 0)
    arm_l = opts.get("arm_l", 0)
    arm_r = opts.get("arm_r", 0)
    leg_l = opts.get("leg_l", 0)
    leg_r = opts.get("leg_r", 0)
    eye_l = opts.get("eye_l", "●")
    eye_r = opts.get("eye_r", "●")
    mouth = opts.get("mouth", "—")
    color = opts.get("color", "#1a0a3e")
    glow = opts.get("glow", "#00ff88")

    c = canvas.create_oval
    r = canvas.create_rectangle
    l = canvas.create_line
    t = canvas.create_text

    # Legs
    l(cx-10*s, cy+50*s, cx-10*s+leg_l*s, cy+72*s, fill=glow, width=5*s, capstyle=tk.ROUND)
    l(cx+10*s, cy+50*s, cx+10*s+leg_r*s, cy+72*s, fill=glow, width=5*s, capstyle=tk.ROUND)
    r(cx-16*s+leg_l*s, cy+70*s, cx-2*s+leg_l*s, cy+76*s, fill=glow, outline="")
    r(cx+4*s+leg_r*s, cy+70*s, cx+18*s+leg_r*s, cy+76*s, fill=glow, outline="")

    # Body
    r(cx-22*s, cy+10*s, cx+22*s, cy+52*s, fill=color, outline=glow, width=2)
    c(cx-5*s, cy+22*s, cx+5*s, cy+32*s, fill=glow, outline="")

    # Arms
    l(cx-22*s, cy+22*s, cx-34*s, cy+22*s+arm_l*s, fill=glow, width=5*s, capstyle=tk.ROUND)
    c(cx-38*s, cy+18*s+arm_l*s, cx-30*s, cy+26*s+arm_l*s, fill=glow, outline="")
    l(cx+22*s, cy+22*s, cx+34*s, cy+22*s+arm_r*s, fill=glow, width=5*s, capstyle=tk.ROUND)
    c(cx+30*s, cy+18*s+arm_r*s, cx+38*s, cy+26*s+arm_r*s, fill=glow, outline="")

    # Neck
    r(cx-5*s, cy+2*s, cx+5*s, cy+12*s, fill=color, outline=glow, width=1)

    # Head
    tx = cx + tilt * 0.4
    ty = cy - 8*s
    r(tx-20*s, ty-28*s, tx+20*s, ty+6*s, fill=color, outline=glow, width=2)
    t(tx-8*s, ty-16*s, text=eye_l, fill=glow, font=("Courier New", int(10*s)))
    t(tx+8*s, ty-16*s, text=eye_r, fill=glow, font=("Courier New", int(10*s)))
    t(tx, ty-6*s, text=mouth, fill=glow, font=("Courier New", int(8*s)))

    # Antenna
    l(tx, ty-28*s, tx, ty-42*s, fill=glow, width=2)
    c(tx-5*s, ty-47*s, tx+5*s, ty-37*s, fill="#ff6b6b", outline="")


def compute_animated_opts(pose, motion, frame):
    """Compute current opts by applying sinusoidal motion to pose."""
    result = dict(pose)
    for key, (amplitude, speed) in motion.items():
        base = pose.get(key, 0)
        if isinstance(base, (int, float)):
            result[key] = base + amplitude * math.sin(frame * speed)
    return result


# ─────────────────────────────────────────────
#  EDITOR
# ─────────────────────────────────────────────

class RobotEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("\U0001f916 Robot Animation Editor")
        self.root.configure(bg="#1a0a2e")
        self.root.geometry("1300x800")

        self.active_robot = 0
        self.frame = 0

        # Each robot has a pose dict and a motion dict
        self.poses = [
            {"tilt": 0, "arm_l": 0, "arm_r": 0, "leg_l": 0, "leg_r": 0,
             "eye_l": "●", "eye_r": "●", "mouth": "—", "bob": 0},
            {"tilt": 0, "arm_l": 0, "arm_r": 0, "leg_l": 0, "leg_r": 0,
             "eye_l": "●", "eye_r": "●", "mouth": "—", "bob": 0},
        ]
        self.motions = [{}, {}]  # key -> (amplitude, speed)

        self._build_ui()
        self._animate()

    def _build_ui(self):
        # ── Preview (left 55%) ──
        self.preview = tk.Canvas(self.root, bg="#0d0721", highlightthickness=0)
        self.preview.place(x=0, y=0, relwidth=0.55, relheight=1)

        # ── Controls (right 45%) ──
        ctrl = tk.Frame(self.root, bg="#1a0a2e")
        ctrl.place(relx=0.55, y=0, relwidth=0.45, relheight=1)

        # Robot selector
        sel = tk.Frame(ctrl, bg="#1a0a2e")
        sel.pack(fill="x", padx=8, pady=(8, 4))
        self.robot_btns = []
        for i, label in enumerate(["Robot 1 (Left)", "Robot 2 (Right)"]):
            btn = tk.Button(sel, text=label, font=("Courier New", 9, "bold"),
                            bg="#00ff88" if i == 0 else "#2a1a4e",
                            fg="#0d0721" if i == 0 else "#888",
                            relief="flat", pady=3,
                            command=lambda idx=i: self._select_robot(idx))
            btn.pack(side="left", expand=True, fill="x", padx=2)
            self.robot_btns.append(btn)

        # Scrollable area
        sf = tk.Canvas(ctrl, bg="#1a0a2e", highlightthickness=0)
        sb = ttk.Scrollbar(ctrl, orient="vertical", command=sf.yview)
        self.inner = tk.Frame(sf, bg="#1a0a2e")
        self.inner.bind("<Configure>", lambda e: sf.configure(scrollregion=sf.bbox("all")))
        sf.create_window((0, 0), window=self.inner, anchor="nw",
                         tags="inner_window")

        # Make inner frame fill the canvas width
        def _resize_inner(event):
            sf.itemconfig("inner_window", width=event.width)
        sf.bind("<Configure>", _resize_inner)

        sf.configure(yscrollcommand=sb.set)
        sf.pack(side="left", fill="both", expand=True, padx=(8, 0))
        sb.pack(side="right", fill="y")
        sf.bind_all("<MouseWheel>", lambda e: sf.yview_scroll(int(-1*(e.delta/120)), "units"))

        # ── Presets ──
        self._label("PRESETS")
        pf = tk.Frame(self.inner, bg="#1a0a2e")
        pf.pack(fill="x", pady=(0, 8))
        for i, name in enumerate(PRESETS):
            tk.Button(pf, text=name, font=("Courier New", 8),
                      bg="#2a1a4e", fg="#c0c0d0", relief="flat", padx=3, pady=2,
                      command=lambda n=name: self._load_preset(n)
            ).grid(row=i//3, column=i%3, sticky="ew", padx=1, pady=1)
        for c in range(3):
            pf.columnconfigure(c, weight=1)

        # ── Body part controls (pose + motion) ──
        self._label("POSE & MOTION")
        self._label("Each part: center pose  |  swing amplitude  |  speed", size=7, color="#555577")

        self.pose_vars = {}
        self.amp_vars = {}
        self.speed_vars = {}

        for key in BODY_PARTS:
            lo, hi = PART_RANGES[key]
            lbl = PART_LABELS[key]
            frame = tk.Frame(self.inner, bg="#1a0a2e")
            frame.pack(fill="x", pady=2)

            tk.Label(frame, text=lbl, width=10, anchor="w",
                     font=("Courier New", 8), fg="#888", bg="#1a0a2e").grid(row=0, column=0, rowspan=2)

            # Pose slider
            pv = tk.IntVar(value=0)
            tk.Label(frame, text="pos", font=("Courier New", 7), fg="#555577",
                     bg="#1a0a2e").grid(row=0, column=1, sticky="w")
            tk.Scale(frame, from_=lo, to=hi, orient="horizontal", variable=pv,
                     font=("Courier New", 7), bg="#1a0a2e", fg="#00ff88",
                     troughcolor="#0d0721", highlightthickness=0, showvalue=True,
                     command=lambda v, k=key: self._on_pose(k, int(v))
            ).grid(row=0, column=2, sticky="ew")
            self.pose_vars[key] = pv

            # Amplitude slider
            av = tk.IntVar(value=0)
            tk.Label(frame, text="amp", font=("Courier New", 7), fg="#555577",
                     bg="#1a0a2e").grid(row=1, column=1, sticky="w")
            tk.Scale(frame, from_=0, to=abs(hi)*2, orient="horizontal", variable=av,
                     font=("Courier New", 7), bg="#1a0a2e", fg="#4ECDC4",
                     troughcolor="#0d0721", highlightthickness=0, showvalue=True,
                     command=lambda v, k=key: self._on_motion(k)
            ).grid(row=1, column=2, sticky="ew")
            self.amp_vars[key] = av

            # Speed slider (0.01 to 0.40, stored as int 1-40)
            sv = tk.IntVar(value=0)
            tk.Label(frame, text="spd", font=("Courier New", 7), fg="#555577",
                     bg="#1a0a2e").grid(row=1, column=3, sticky="w")
            tk.Scale(frame, from_=0, to=40, orient="horizontal", variable=sv,
                     font=("Courier New", 7), bg="#1a0a2e", fg="#FFD93D",
                     troughcolor="#0d0721", highlightthickness=0, showvalue=True, length=80,
                     command=lambda v, k=key: self._on_motion(k)
            ).grid(row=1, column=4, sticky="ew")
            self.speed_vars[key] = sv

            frame.columnconfigure(2, weight=3)
            frame.columnconfigure(4, weight=1)

        # ── Expressions ──
        self._label("EXPRESSION")
        ef = tk.Frame(self.inner, bg="#1a0a2e")
        ef.pack(fill="x", pady=(0, 3))
        tk.Label(ef, text="L eye", font=("Courier New", 8), fg="#888", bg="#1a0a2e").pack(side="left")
        for eye in EYES:
            tk.Button(ef, text=eye, font=("Courier New", 12), bg="#0d0721", fg="#00ff88",
                      relief="flat", width=2, command=lambda e=eye: self._set("eye_l", e)
            ).pack(side="left", padx=1)

        ef2 = tk.Frame(self.inner, bg="#1a0a2e")
        ef2.pack(fill="x", pady=(0, 3))
        tk.Label(ef2, text="R eye", font=("Courier New", 8), fg="#888", bg="#1a0a2e").pack(side="left")
        for eye in EYES:
            tk.Button(ef2, text=eye, font=("Courier New", 12), bg="#0d0721", fg="#00ff88",
                      relief="flat", width=2, command=lambda e=eye: self._set("eye_r", e)
            ).pack(side="left", padx=1)

        mf = tk.Frame(self.inner, bg="#1a0a2e")
        mf.pack(fill="x", pady=(0, 8))
        tk.Label(mf, text="Mouth", font=("Courier New", 8), fg="#888", bg="#1a0a2e").pack(side="left")
        for m in MOUTHS:
            tk.Button(mf, text=m, font=("Courier New", 12), bg="#0d0721", fg="#00ff88",
                      relief="flat", width=2, command=lambda mo=m: self._set("mouth", mo)
            ).pack(side="left", padx=1)

        # ── Code export ──
        self._label("CODE OUTPUT")
        bf = tk.Frame(self.inner, bg="#1a0a2e")
        bf.pack(fill="x", pady=(0, 4))
        tk.Button(bf, text="\U0001f4cb Copy Robot 1", font=("Courier New", 9, "bold"),
                  bg="#00ff88", fg="#0d0721", relief="flat", pady=4,
                  command=lambda: self._export(0)).pack(side="left", expand=True, fill="x", padx=2)
        tk.Button(bf, text="\U0001f4cb Copy Robot 2", font=("Courier New", 9, "bold"),
                  bg="#4ECDC4", fg="#0d0721", relief="flat", pady=4,
                  command=lambda: self._export(1)).pack(side="left", expand=True, fill="x", padx=2)
        tk.Button(bf, text="\U0001f4cb Copy Scene", font=("Courier New", 9, "bold"),
                  bg="#FFD93D", fg="#0d0721", relief="flat", pady=4,
                  command=self._export_scene).pack(side="left", expand=True, fill="x", padx=2)

        self.code_text = tk.Text(self.inner, height=12, wrap="word",
                                 font=("Courier New", 8), bg="#0d0721", fg="#00ff88",
                                 insertbackground="#00ff88", relief="flat")
        self.code_text.pack(fill="x", pady=(4, 10))

    def _label(self, text, size=9, color="#555577"):
        tk.Label(self.inner, text=text, font=("Courier New", size, "bold"),
                 fg=color, bg="#1a0a2e", anchor="w").pack(fill="x", pady=(6, 1))

    # ─────────────────────────────────────────
    #  Actions
    # ─────────────────────────────────────────

    def _select_robot(self, idx):
        self.active_robot = idx
        for i, btn in enumerate(self.robot_btns):
            btn.configure(bg="#00ff88" if i == idx else "#2a1a4e",
                          fg="#0d0721" if i == idx else "#888")
        self._load_sliders()

    def _load_sliders(self):
        """Load current robot's values into all sliders."""
        pose = self.poses[self.active_robot]
        motion = self.motions[self.active_robot]
        for key in BODY_PARTS:
            self.pose_vars[key].set(int(pose.get(key, 0)))
            amp, spd = motion.get(key, (0, 0))
            self.amp_vars[key].set(int(amp))
            self.speed_vars[key].set(int(spd * 100))

    def _on_pose(self, key, value):
        self.poses[self.active_robot][key] = value

    def _on_motion(self, key):
        amp = self.amp_vars[key].get()
        spd = self.speed_vars[key].get() / 100.0
        if amp > 0 and spd > 0:
            self.motions[self.active_robot][key] = (amp, spd)
        else:
            self.motions[self.active_robot].pop(key, None)

    def _set(self, key, value):
        self.poses[self.active_robot][key] = value

    def _load_preset(self, name):
        p = PRESETS[name]
        self.poses[self.active_robot] = dict(p["pose"])
        self.motions[self.active_robot] = dict(p.get("motion", {}))
        self._load_sliders()

    def _export(self, robot_idx):
        """Generate paste-ready code for one robot."""
        code = self._gen_robot_code(robot_idx, "cx", "cy")
        self.code_text.delete("1.0", "end")
        self.code_text.insert("1.0", code)
        self.root.clipboard_clear()
        self.root.clipboard_append(code)

    def _export_scene(self):
        """Generate a complete scene method with both robots."""
        lines = [
            "def _scene_custom(self, cx, cy, f):",
        ]

        for i, (offset, name) in enumerate([(("-100", "+bob1"), "Robot 1"),
                                              (("+100", "-bob1"), "Robot 2")]):
            pose = self.poses[i]
            motion = self.motions[i]

            # Generate motion variables
            if motion:
                lines.append(f"    # {name} motion")
                if "bob" in motion:
                    amp, spd = motion["bob"]
                    var = f"bob{i+1}"
                    lines.append(f"    {var} = math.sin(f * {spd}) * {amp}")

            # Build the opts dict with inline math.sin for animated parts
            opts_parts = []
            for key in ("tilt", "arm_l", "arm_r", "leg_l", "leg_r"):
                base = pose.get(key, 0)
                if key in motion:
                    amp, spd = motion[key]
                    if base == 0:
                        opts_parts.append(f'"{key}": math.sin(f * {spd}) * {amp}')
                    else:
                        opts_parts.append(f'"{key}": {base} + math.sin(f * {spd}) * {amp}')
                elif base != 0:
                    opts_parts.append(f'"{key}": {base}')

            for key in ("eye_l", "eye_r", "mouth"):
                val = pose.get(key, "●" if "eye" in key else "—")
                opts_parts.append(f'"{key}": "{val}"')

            opts_str = ", ".join(opts_parts)

            # Position
            x_off = "-100" if i == 0 else "+100"
            bob_var = f"bob{i+1}" if "bob" in motion else "0"
            lines.append(f"    self._robot(cx{x_off}, cy+{bob_var}, {{{opts_str}}})")
            lines.append("")

        code = "\n".join(lines)
        self.code_text.delete("1.0", "end")
        self.code_text.insert("1.0", code)
        self.root.clipboard_clear()
        self.root.clipboard_append(code)

    def _gen_robot_code(self, idx, cx_expr, cy_expr):
        pose = self.poses[idx]
        motion = self.motions[idx]

        lines = []
        # Bob variable
        if "bob" in motion:
            amp, spd = motion["bob"]
            lines.append(f"bob = math.sin(f * {spd}) * {amp}")

        opts_parts = []
        for key in ("tilt", "arm_l", "arm_r", "leg_l", "leg_r"):
            base = pose.get(key, 0)
            if key in motion:
                amp, spd = motion[key]
                if base == 0:
                    opts_parts.append(f'    "{key}": math.sin(f * {spd}) * {amp}')
                else:
                    opts_parts.append(f'    "{key}": {base} + math.sin(f * {spd}) * {amp}')
            elif base != 0:
                opts_parts.append(f'    "{key}": {base}')

        for key in ("eye_l", "eye_r", "mouth"):
            val = pose.get(key, "●" if "eye" in key else "—")
            opts_parts.append(f'    "{key}": "{val}"')

        bob_expr = "+bob" if "bob" in motion else ""
        lines.append(f"self._robot({cx_expr}, {cy_expr}{bob_expr}, {{")
        lines.append(",\n".join(opts_parts))
        lines.append("})")

        return "\n".join(lines)

    # ─────────────────────────────────────────
    #  Animation Loop
    # ─────────────────────────────────────────

    def _animate(self):
        self.frame += 1
        self._redraw()
        self.root.after(33, self._animate)

    def _redraw(self):
        cv = self.preview
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 10:
            return

        f = self.frame

        # Background
        cv.create_rectangle(0, 0, w, h, fill="#0d0721", outline="")

        # Stars (deterministic)
        import random as _rng
        r = _rng.Random(42)
        for _ in range(40):
            sx, sy = r.random()*w, r.random()*h*0.6
            sr = r.uniform(0.5, 1.8)
            br = r.randint(60, 160)
            cv.create_oval(sx-sr, sy-sr, sx+sr, sy+sr,
                          fill=f"#{br:02x}{min(255,br+20):02x}{min(255,br+60):02x}", outline="")

        # Ground
        gy = h * 0.72
        cv.create_rectangle(0, gy, w, h, fill="#1a0a2e", outline="")
        cv.create_line(0, gy, w, gy, fill="#2a1a4e", width=2)

        # Draw robots
        for i in range(2):
            cx = w * (0.3 if i == 0 else 0.7)
            cy_base = gy - 80

            pose = self.poses[i]
            motion = self.motions[i]

            # Compute animated values
            opts = compute_animated_opts(pose, motion, f)

            # Bob is applied to cy, not to the robot opts
            bob = opts.pop("bob", 0)

            # Dim inactive robot
            if i != self.active_robot:
                opts["glow"] = "#335544"

            draw_robot(cv, cx, cy_base + bob, opts, scale=1.0)

            # Label
            cv.create_text(cx, gy + 15, text=f"Robot {i+1}",
                           fill="#555577", font=("Courier New", 9))

            # Expression readout
            expr = f'{opts.get("eye_l","●")} {opts.get("mouth","—")} {opts.get("eye_r","●")}'
            cv.create_text(cx, gy + 30, text=expr,
                           fill="#888888", font=("Courier New", 12))

        # Selection indicator
        sel_x = w * (0.3 if self.active_robot == 0 else 0.7)
        cv.create_text(sel_x, gy - 170, text="\u25bc EDITING",
                       fill="#FFD93D", font=("Courier New", 9, "bold"))

        # Frame counter
        cv.create_text(10, h - 10, text=f"frame: {f}", anchor="sw",
                       fill="#333355", font=("Courier New", 8))


if __name__ == "__main__":
    root = tk.Tk()
    app = RobotEditor(root)
    root.mainloop()